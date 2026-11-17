"""Golden SCPI/AE-bus logs for migrating collection scripts onto suite_instruments.

A script's real collection routine runs against a scripted bench: fake VISA instruments, a
frame-level DUT model, and a virtual clock (sleep() advances it, so polling loops are
deterministic and instant). Everything the script does to an instrument lands in one ordered
log: opens, attribute sets (timeout, terminations, chunk size), writes, queries, binary reads,
closes, and every AE-bus frame. A migration is correct when the log is unchanged.

    .venv\\Scripts\\python -m tests.tools.golden_scpi record <name>   # before migrating <name>
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import logging
import os
import re
import sys
import time as real_time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "golden_scpi"
TRACKED_ATTRIBUTES = {"timeout", "read_termination", "write_termination", "chunk_size"}
WAVEFORM_POINTS = np.array([0.0, 100.0, 200.0, 300.0, 200.0, 100.0, 0.0, -100.0])


class Clock:
    def __init__(self, tick: float = 0.0) -> None:
        self.now = 1_000.0
        self.tick = tick  # advance per reading, for loops that poll the clock without sleeping

    def sleep(self, seconds) -> None:
        self.now += max(0.0, float(seconds))

    def monotonic(self) -> float:
        self.now += self.tick
        return self.now

    time = perf_counter = monotonic


def clock_module(clock: Clock) -> types.ModuleType:
    """Stand-in for a script's `time` module: virtual sleep/clocks, everything else real."""
    module = types.ModuleType("time")
    for name in dir(real_time):
        if not name.startswith("__"):
            setattr(module, name, getattr(real_time, name))
    for name in ("sleep", "monotonic", "time", "perf_counter"):
        setattr(module, name, getattr(clock, name))
    return module


@dataclass
class Bench:
    """Shared log and the state the fake instruments and DUT model expose."""
    log: list[str] = field(default_factory=list)
    settings: dict[tuple[str, str], str] = field(default_factory=dict)
    refuse: set[str] = field(default_factory=set)
    rf_enable: tuple[str, int] | None = None        # (wavegen resource, channel) driving RF Enable in User Port mode
    interlock_supply: str | None = None              # resource whose OUTP:STAT gates RF (interlock)
    cex_reference: tuple[str, int] | None = None     # (wavegen resource, channel) of the CEX reference
    mode: int = 2
    host_rf: bool = False
    trigger_model: bool = False                      # scope READY after arming, SAVE after the next stimulus
    armed: set[str] = field(default_factory=set)
    acquisition_model: bool = False                  # an armed single sequence completes (ACQ:STATE 0) while RF is on
    pulse_model: bool = False                        # DUT remembers CMD 26/93/96 per connection and reports them back
    pulse: dict[Any, dict] = field(default_factory=dict)
    ae_trigger_model: bool = False                   # an armed scope triggers on the next AE-bus command
    identities: dict[str, str] = field(default_factory=dict)   # *IDN? reply per resource
    navigator_port: str | None = None                # serial port answered by the Navigator model
    nav: dict[int, int] = field(default_factory=lambda: {112: 0, 122: 0})
    visa_faults: dict[str, int] = field(default_factory=dict)  # command -> times it times out (VisaIOError)
    fixed_frequency_hz: tuple[int, int] = (12_880_000, 14_240_000)  # DUT fixed-frequency range (min, max)
    power_limits_w: tuple[int, int] | None = None    # (low power limit, supply size) reported by CMD 129
    ae_values: dict[int, int] = field(default_factory=dict)  # report cmd -> value; set cmd (report - 100) updates it

    def fault(self, resource: str, command: str) -> None:
        key = command.strip().upper()
        if self.visa_faults.get(key, 0) > 0:
            import pyvisa
            self.visa_faults[key] -= 1
            self.record("visa-timeout", resource, command)
            raise pyvisa.errors.VisaIOError(pyvisa.constants.StatusCode.error_timeout)

    def record(self, *entry: Any) -> None:
        self.log.append(repr(entry))

    # -- instrument state -----------------------------------------------------------------
    def wrote(self, resource: str, command: str) -> None:
        text = command.strip()
        if self.trigger_model:
            if text.upper() == "ACQ:STATE ON":
                self.armed.add(resource)
                self.settings[(resource, "TRIG:STATE")] = "READY"
            else:
                for scope in [s for s in self.armed if s != resource]:
                    self.settings[(scope, "TRIG:STATE")] = "SAVE"
                    self.armed.discard(scope)
        if self.ae_trigger_model and text.upper() in {"ACQ:STATE ON", "ACQUIRE:STATE ON", "ACQUIRE:STATE RUN", "ACQ:STATE RUN"}:
            self.armed.add(resource)
            self.settings[(resource, "TRIG:STATE")] = "READY"
        siglent = re.fullmatch(r"(C\d):OUTP\s+(LOAD,\w+|ON|OFF)", text, re.I)
        if siglent:
            part = siglent.group(2).upper()
            self.settings[(resource, f"{siglent.group(1).upper()}:OUTP:{'LOAD' if part.startswith('LOAD') else 'STATE'}")] = part.split(",")[-1]
            return
        bswv = re.fullmatch(r"(C\d):BSWV\s+(\w+),(.+)", text, re.I)
        if bswv:
            self.settings[(resource, f"{bswv.group(1).upper()}:BSWV:{bswv.group(2).upper()}")] = bswv.group(3)
            return
        if " " in text and not text.endswith("?"):
            key, value = text.split(" ", 1)
            self.settings[(resource, key.upper())] = value.strip()

    def setting(self, resource: str, key: str, default: str = "") -> str:
        return self.settings.get((resource, key.upper()), default)

    def channel_high(self, source: tuple[str, int] | None) -> bool:
        if source is None:
            return False
        resource, channel = source
        on = self.setting(resource, f"C{channel}:OUTP:STATE", "OFF") == "ON"
        try:
            level = float(self.setting(resource, f"C{channel}:BSWV:OFST", "0"))
        except ValueError:
            level = 0.0
        return on and level > 3.0

    def interlock_ok(self) -> bool:
        if self.interlock_supply is None:
            return True
        return self.setting(self.interlock_supply, "OUTP:STAT", "OFF").upper() in {"ON", "1"}

    def rf_on(self) -> bool:
        requested = self.host_rf if self.mode == 2 else self.channel_high(self.rf_enable)
        return requested and self.interlock_ok()

    def cex_locked(self) -> bool:
        if self.cex_reference is None:
            return False
        resource, channel = self.cex_reference
        return self.rf_on() and self.setting(resource, f"C{channel}:OUTP:STATE", "OFF") == "ON"

    def reply(self, resource: str, command: str) -> str:
        text = command.strip()
        upper = text.upper()
        if upper == "*IDN?":
            return self.identities.get(resource, f"FAKE,{resource},0,1")
        if upper == "*OPC?":
            return "1"
        if self.acquisition_model and upper in ("ACQ:STATE?", "ACQUIRE:STATE?"):
            key = upper[:-1]
            state = self.setting(resource, key, "0")
            if state.upper() in {"ON", "1", "RUN"} and self.rf_on():
                self.settings[(resource, key)] = state = "0"
            return state
        if self.ae_trigger_model and upper == "TRIGGER:STATE?":
            upper = "TRIG:STATE?"
        if upper == "TRIG:STATE?":
            return self.setting(resource, "TRIG:STATE", "SAVE") if self.trigger_model or self.ae_trigger_model else "SAVE"
        siglent = re.fullmatch(r"(C\d):OUTP\?", upper)
        if siglent:
            ch = siglent.group(1)
            return f"{ch}:OUTP {self.setting(resource, ch + ':OUTP:STATE', 'OFF')},LOAD,{self.setting(resource, ch + ':OUTP:LOAD', 'HZ')},PLRT,NOR"
        if upper.startswith("WFMOUTPRE:"):
            return "1.0"
        if upper == "MEAS:VOLT?":
            if self.setting(resource, "OUTP:STAT", "OFF").upper() in {"ON", "1"}:
                return self.setting(resource, "APPL", "0,0").split(",")[0]
            return "0.0"
        if upper == "MEAS:CURR?":
            return "0.010"
        if upper.endswith("?"):
            value = self.setting(resource, upper[:-1].strip(), "0")
            unit = re.fullmatch(r"(-?[\d.]+(?:E[-+]?\d+)?)\s*(?:HZ|DB)", value, re.I)  # instruments report bare numbers
            return unit.group(1) if unit else value
        return "0"

    # -- AE bus (frame-level DUT model) ---------------------------------------------------
    def ae_reply(self, cmd: int, recv_sizes: str, args: tuple, target: Any = None) -> list:
        from tests.tools.golden_frames import _bench_reply

        if self.ae_trigger_model and self.armed:
            for scope in self.armed:
                self.settings[(scope, "TRIG:STATE")] = "SAVE"
                self.settings[(scope, "ACQ:STATE")] = self.settings[(scope, "ACQUIRE:STATE")] = "0"
            self.armed.clear()
        if self.navigator_port is not None and target == self.navigator_port:
            if cmd in (112, 122):  # move load / tune motor
                self.nav[cmd] = int(args[0])
                return [0, 0, 0]
            if cmd == 135:  # motors moving?
                return [0, 1, 0]
            if cmd == 180:  # capacitor positions
                return [0, 4, self.nav[112], self.nav[122]]
        if self.pulse_model and cmd in (26, 172, 93, 193, 96, 196):
            state = self.pulse.setdefault(target, {"config": {}, 93: 0, 96: 0})
            if cmd == 26:
                state["config"][int(args[0])] = int(args[1])
                return [0, 0, 0]
            if cmd == 172:  # 16/17: minimum/maximum duty cycle (%)
                selection = int(args[0])
                return [0, 4, state["config"].get(selection, {16: 1, 17: 99}.get(selection, 0))]
            if cmd in (93, 96):  # pulse frequency (Hz) / duty cycle (%); raw-payload callers send bytes
                value = args[0] if len(args) == 1 else bytes(int(a) for a in args)
                state[cmd] = int.from_bytes(value, "little") if isinstance(value, (bytes, bytearray)) else int(value)
                return [0, 0, 0]
            return [0, 4 if cmd == 193 else 2, state[cmd - 100]]
        if cmd == 14:
            self.mode = int(args[0])
            return [0, 0, 0]
        if cmd == 155:
            return [0, 1, self.mode]
        if cmd == 2:
            self.host_rf = True
            return [0, 0, 0]
        if cmd == 1:
            self.host_rf = False
            return [0, 0, 0]
        if cmd == 161:  # fixed frequency (Hz): 0/1 current, 2 maximum, 3 minimum
            low, high = self.fixed_frequency_hz
            return [0, 4, {2: high, 3: low}.get(int(args[0]) if args else 0, 13_560_000)]
        if cmd == 154:  # regulation mode: forward power
            return [0, 2, 6]
        if cmd == 169:  # user power limit (W)
            return [0, 2, 6000]
        if cmd < 128 and cmd + 100 in self.ae_values and args:
            self.ae_values[cmd + 100] = int(args[0])
            return [0, 0, 0]
        if cmd in self.ae_values:
            return [0, 4, self.ae_values[cmd]]
        if cmd == 129 and self.power_limits_w is not None:  # 6 ASCII digits: 0 = supply size, 1 = low limit
            value = self.power_limits_w[1] if int(args[0]) == 0 else self.power_limits_w[0]
            return [0, 6, *f"{value:06d}".encode("ascii")]
        if cmd == 223:  # fault/warning code list: none active
            return [0, 2, 0]
        if cmd == 162:  # process status: byte0 bit5 = RF on, byte3 bit7 = CEX locked
            return [0, 4, 0b00100000 if self.rf_on() else 0, 0, 0, 0x80 if self.cex_locked() else 0]
        return _bench_reply(types.SimpleNamespace(cmd=cmd, recv_sizes=recv_sizes))


class ScriptedInstrument:
    def __init__(self, bench: Bench, resource: str):
        object.__setattr__(self, "_bench", bench)
        object.__setattr__(self, "_resource", resource)
        object.__setattr__(self, "timeout", 2000)
        bench.record("open", resource)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in TRACKED_ATTRIBUTES:
            self._bench.record("set", self._resource, name, value)
        object.__setattr__(self, name, value)

    def write(self, command: str, *args, **kwargs) -> int:
        self._bench.fault(self._resource, command)
        self._bench.record("write", self._resource, command)
        self._bench.wrote(self._resource, command)
        return len(command)

    def query(self, command: str, *args, **kwargs) -> str:
        self._bench.fault(self._resource, command)
        reply = self._bench.reply(self._resource, command)
        self._bench.record("query", self._resource, command, reply)
        return reply

    def read(self, *args, **kwargs) -> str:
        self._bench.record("read", self._resource)
        return ""

    def query_ascii_values(self, command: str, *args, **kwargs):
        self._bench.record("query_ascii_values", self._resource, command)
        return [float(v) for v in self._bench.reply(self._resource, command).split(",") if v.strip()]

    def query_binary_values(self, command: str, *args, **kwargs):
        self._bench.fault(self._resource, command)
        options = {k: kwargs[k] for k in sorted(kwargs) if k != "container"}
        self._bench.record("query_binary_values", self._resource, command, options)
        return WAVEFORM_POINTS.copy()

    def write_raw(self, data: bytes, *args, **kwargs) -> int:
        self._bench.record("write_raw", self._resource, data)
        return len(data)

    def read_raw(self, *args, **kwargs) -> bytes:
        self._bench.record("read_raw", self._resource)
        return b""

    def clear(self) -> None:
        self._bench.record("clear", self._resource)

    def close(self) -> None:
        self._bench.record("close", self._resource)


class ScriptedResourceManager:
    visalib = "scripted-visa"

    def __init__(self, bench: Bench):
        self._bench = bench
        bench.record("rm-open")

    def open_resource(self, resource: str, *args, **kwargs) -> ScriptedInstrument:
        if resource in self._bench.refuse:
            self._bench.record("open-refused", resource)
            raise OSError(f"VI_ERROR_RSRC_NFOUND: {resource}")
        return ScriptedInstrument(self._bench, resource)

    def list_resources(self, *args, **kwargs) -> tuple:
        self._bench.record("list_resources")
        return ()

    def close(self) -> None:
        self._bench.record("rm-close")


class ScriptedAeConnection:
    latency_ms = 0.5  # AE_Com_Module's per-frame latency (CommandProcessing reports it)

    def __init__(self, bench: Bench, kind: str, target: Any = None):
        self._bench = bench
        self._target = target
        bench.record("ae-open", kind, target)

    def connect(self, address, *args, **kwargs) -> None:
        self._target = address
        self._bench.record("ae-connect", address)

    def create_buffer_cmd(self, cmd, send_sizes, recv_sizes, *args, **kwargs) -> list:
        reply = self._bench.ae_reply(cmd, recv_sizes, tuple(args), self._target)
        self._bench.record("ae", cmd, send_sizes, recv_sizes, tuple(args), reply)
        return list(reply)

    create_buffer_cmd_fastDAQ = create_buffer_cmd

    def close(self) -> None:
        self._bench.record("ae-close", self._target)


@contextlib.contextmanager
def scripted_bench(bench: Bench):
    """Patch pyvisa and AE_Com_Module so every instrument the script opens is scripted."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    importlib.import_module("suite_common.bootstrap")
    import AE_Com_Module
    import pyvisa

    saved = (pyvisa.ResourceManager, AE_Com_Module.AETCP, AE_Com_Module.AEB32)
    pyvisa.ResourceManager = lambda *args, **kwargs: ScriptedResourceManager(bench)
    AE_Com_Module.AETCP = lambda *args, **kwargs: ScriptedAeConnection(bench, "tcp")
    AE_Com_Module.AEB32 = lambda port=None, *args, **kwargs: ScriptedAeConnection(bench, "serial", port)
    try:
        yield bench
    finally:
        pyvisa.ResourceManager, AE_Com_Module.AETCP, AE_Com_Module.AEB32 = saved


def load_script(relative: str, clock: Clock) -> types.ModuleType:
    path = REPO_ROOT / relative
    name = "golden_" + re.sub(r"\W", "_", path.stem)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)
    module.time = clock_module(clock)
    return module


@dataclass(frozen=True)
class GuiRun:
    """A script run end to end as __main__, set up through its own setup window (see fake_tk)."""
    entries: dict[str, str]
    start: str | None                              # None: no setup window (argv-driven script)
    curves: int                                    # waveform transfers a complete run makes
    choices: dict[str, Any] = field(default_factory=dict)
    argv: tuple[str, ...] = ()
    stdin: str = ""                                # what the operator types at console prompts
    finished: str = ""                             # stdout text a complete run prints (scripts that sys.exit() on error)
    open_files: tuple[str, ...] = ()               # file contents for filedialog.askopenfile, in order


def run_gui_script(relative: str, gui: GuiRun, bench: Bench, clock: Clock, parent: Path) -> int:
    """Run a module-level GUI script exactly as the operator would (python script.py).

    tkinter is replaced by fake_tk, and the time module's clocks/sleep by the virtual clock
    (the scripts bind them with `from time import ...` at import). Environment variables that
    select bench addresses or launcher modes are cleared so only the scenario's entries apply.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot  # noqa: F401  (backend chosen before the fake tkinter is installed)
    for name in ("suite_common.bench", "suite_common.inputs", "suite_common.output_folders",
                 "suite_instruments.factory", "suite_instruments.transport.visa"):
        importlib.import_module(name)
    from tests.tools import fake_tk

    operator = fake_tk.Operator(gui.entries, gui.choices, gui.start, str(parent), bench.record, gui.open_files)
    fakes = fake_tk.modules(operator)
    path = REPO_ROOT / relative
    spec = importlib.util.spec_from_file_location("__main__", path)
    module = importlib.util.module_from_spec(spec)
    clocks = ("sleep", "time", "monotonic", "perf_counter")
    saved_modules = {name: sys.modules.get(name) for name in (*fakes, "__main__")}
    saved_clocks = {name: getattr(real_time, name) for name in clocks}
    saved_env = {k: v for k, v in os.environ.items() if k.startswith("PARAMOUNT_")}
    saved_argv, saved_cwd = sys.argv, os.getcwd()
    saved_stdin, saved_hook = sys.stdin, sys.excepthook
    saved_use = matplotlib.use
    output = io.StringIO()
    root_logger = logging.getLogger()
    saved_handlers, saved_level = list(root_logger.handlers), root_logger.level
    try:
        for key in saved_env:
            del os.environ[key]
        os.environ["PARAMOUNT_COLLECTION_PARENT_FOLDER"] = str(parent)
        sys.modules.update(fakes)
        sys.modules["__main__"] = module
        sys.argv = [str(path), *gui.argv]
        sys.path.insert(0, str(path.parent))  # as `python script.py` does (sibling helper modules)
        sys.stdin = io.StringIO(gui.stdin)
        matplotlib.use = lambda *args, **kwargs: None  # stay on the headless Agg backend (scripts ask for TkAgg)
        os.chdir(parent)  # scripts that write relative paths (logs/) write into the run folder
        for name in clocks:
            setattr(real_time, name, getattr(clock, name))
        try:
            with contextlib.redirect_stdout(output):
                spec.loader.exec_module(module)
        except SystemExit as stop:
            if stop.code not in (None, 0):
                raise
    finally:
        sys.argv, sys.stdin, sys.excepthook = saved_argv, saved_stdin, saved_hook
        matplotlib.use = saved_use
        if str(path.parent) in sys.path:
            sys.path.remove(str(path.parent))
        os.chdir(saved_cwd)
        for name in clocks:
            setattr(real_time, name, saved_clocks[name])
        for name, saved in saved_modules.items():
            if saved is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved
        for key in [k for k in os.environ if k.startswith("PARAMOUNT_")]:
            del os.environ[key]
        os.environ.update(saved_env)
        for handler in [h for h in root_logger.handlers if h not in saved_handlers]:
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.setLevel(saved_level)
        fake_tk.Widget.operator = None
    errors = [title for kind, title, _ in operator.messages if kind == "showerror"]
    if errors:
        raise RuntimeError(f"error dialog: {errors[0]}")
    if gui.finished and gui.finished not in output.getvalue():
        raise RuntimeError(f"run did not finish (no {gui.finished!r} in its output)")
    curves = sum(1 for entry in bench.log if entry.startswith("('query_binary_values'") and "'CURVE?'" in entry.upper())
    if curves != gui.curves:
        raise RuntimeError(f"{curves} waveform transfers; a complete run makes {gui.curves}")
    return 0


# -- drivers: one per migrated script -------------------------------------------------------
SCOPE = "TCPIP0::192.0.2.10::INSTR"
WAVEGEN = "TCPIP0::192.0.2.11::INSTR"
SUPPLY = "TCPIP0::192.0.2.13::INSTR"
DUT = "PMNTGOLDEN"


def _in_use(module, bench: Bench, resource: str) -> str:
    """The address the script ends up using: its ::SOCKET fallback when the VISA address is refused."""
    return module.visa_socket_resource(resource) if resource in bench.refuse else resource


def _cex(module, bench: Bench, parent: Path):
    wavegen = _in_use(module, bench, WAVEGEN)
    bench.rf_enable = (wavegen, module.RF_ENABLE_WG_CHANNEL)
    bench.cex_reference = (wavegen, module.CEX_REFERENCE_WG_CHANNEL)
    return module.run_collection(parent, DUT, SCOPE, WAVEGEN, 3000.0, [1500.0, 3000.0], 2,
                                 lambda power, setpoint: True, lambda message: None)[0]


def _interlock(module, bench: Bench, parent: Path):
    bench.rf_enable = (_in_use(module, bench, WAVEGEN), module.RF_ENABLE_WG_CHANNEL)
    bench.interlock_supply = _in_use(module, bench, SUPPLY)
    return module.run_collection(parent, DUT, SCOPE, WAVEGEN, SUPPLY, 3000.0, [1500.0, 3000.0], 2, lambda message: None)[0]


def _rf_enable(module, bench: Bench, parent: Path):
    bench.rf_enable = (_in_use(module, bench, WAVEGEN), module.RF_ENABLE_WG_CHANNEL)
    return module.run_collection(parent, DUT, SCOPE, WAVEGEN, 6000.0, [4000.0, 6000.0], 2, lambda message: None)[0]


def _setpoint_transfer(module, bench: Bench, parent: Path):
    bench.rf_enable = (_in_use(module, bench, WAVEGEN), module.TRANSFER_RF_ENABLE_WG_CHANNEL)
    module.TRANSFER_VOLTAGES_V = [0.0, 5.0, 10.0]
    module.TRANSFER_SAMPLES_PER_POINT = 3
    return module.run_collection(2, parent, DUT, WAVEGEN)[0]


def _analog_processing(module, bench: Bench, parent: Path):
    bench.rf_enable = (_in_use(module, bench, WAVEGEN), module.ANALOG_TIMING_RF_ENABLE_WG_CHANNEL)
    bench.trigger_model = True
    module.input = lambda prompt="": "RUN"  # operator pre-run acknowledgement
    module.ANALOG_TIMING_STEPS_V = list(module.ANALOG_TIMING_STEPS_V)[:2]
    return module._run_collection(parent, 1, True, DUT, SCOPE, WAVEGEN)


def _analog_processing_bk9141_connect(module, bench: Bench, parent: Path):
    """The BK 9141 branch of Instruments.connect (not reachable from the GUI today)."""
    bench.interlock_supply = _in_use(module, bench, SUPPLY)
    module.DUT_ADDRESS, module.SCOPE_ADDRESS, module.WAVEGEN_ADDRESS = DUT, SCOPE, WAVEGEN
    module.WAVEGEN_SOCKET_FALLBACK_ADDRESS = module.visa_socket_resource(WAVEGEN)
    module.BK9141_ADDRESS = SUPPLY
    module.BK9141_SOCKET_FALLBACK_ADDRESS = module.visa_socket_resource(SUPPLY)
    inst = module.Instruments(connect_bk9141=True)
    try:
        inst.connect()
        return 0
    finally:
        inst.close()


def _arc(module, bench: Bench, parent: Path):
    module.execute(module.Settings(
        generator_address=DUT, scope_ip="192.0.2.10", function_generator_ip="192.0.2.11",
        parent_folder=str(parent), sample_count=2, power_w=250, fixed_frequency_khz=13_560,
        regulation_mode=6, trigger_level_v=1.0, trigger_timeout_s=5.0, rf_settle_s=0.0, clacker_channel=2,
        clacker_on_s=0.0, post_arc_recovery_s=0.0, inter_sample_delay_s=0.0,
        arc_management_confirmed=True, clacker_preconfigured_confirmed=True))
    return 0


RF_RAMP_FIELDS = {
    "generator": DUT, "scope": "192.0.2.10", "parent": None, "product": "Golden", "power_levels": "50, 1000",
    "ramps": "200", "frequency_khz": "13560", "channel": "CH1", "volts_div": "AUTO", "trigger_low_v": "0.0",
    "trigger_high_v": "0.075", "record_length": "100000", "pretrigger_pct": "20", "repetitions": "1",
    "settle_s": "0", "capture_pre_delay_pct": 100.0, "capture_stop_delay_pct": 100.0,
    "inter_condition_delay_s": 0.0, "raw_per_ms": "1", "ramp_mode": "2", "reg_mode": "6",
    "measure_definition": "10_TO_90", "rise": True, "fall": True, "rf_off_between": False,
}


def _rf_ramp(module, bench: Bench, parent: Path):
    """App.collect() (the worker behind Start Collection) on a headless App with every field set."""
    from tests.tools import fake_tk

    operator = fake_tk.Operator({}, {}, None, str(parent), bench.record)
    fakes = fake_tk.modules(operator)
    module.tk, module.ttk = fakes["tkinter"], fakes["tkinter.ttk"]
    module.filedialog, module.messagebox = fakes["tkinter.filedialog"], fakes["tkinter.messagebox"]
    try:
        app = module.App(fakes["tkinter"].Tk())
        assert set(app.v) == set(RF_RAMP_FIELDS), sorted(set(app.v) ^ set(RF_RAMP_FIELDS))
        for key, value in RF_RAMP_FIELDS.items():
            app.v[key].set(str(parent) if key == "parent" else value)
        app.collect()
    finally:
        fake_tk.Widget.operator = None
    errors = [title for kind, title, _ in operator.messages if kind == "showerror"]
    if errors:
        raise RuntimeError(f"error dialog: {errors[0]}")
    return 0


def _rf_tuning(module, bench: Bench, parent: Path):
    """Generator and Navigator connection, a fault check, and a verified Navigator move."""
    module.STOP.clear()
    module.NAVIGATOR_COMMAND_DWELL_SECONDS = module.NAVIGATOR_SETTLE_SECONDS = 0.0
    generator = navigator = None
    try:
        generator = module.create_generator(DUT)
        module.safe_rf_off(generator)
        module.verify_no_preexisting_faults(generator)
        navigator = module.create_navigator("COM11")
        load, tune = module.MATCH_BY_TARGET_KHZ[module.CENTER_FREQUENCY_KHZ]
        module.move_and_verify_navigator(navigator, load, tune)
    finally:
        module.safe_rf_off(generator)
        module.close_device(navigator, "Navigator")
        module.close_device(generator, "generator")
    return 0


RF_ONOFF_ARGS = ("--no-gui", "--unit", "refreshed 3013", "--scope-ip", "192.0.2.10", "--com-port", "COM5",
                 "--dut-name", DUT, "--trial", "--setpoints", "1000")
SDG = "TCPIP0::192.0.2.11::INSTR"
ARC_BENCH = {"ae_trigger_model": True, "identities": {SDG: "Siglent Technologies,SDG6022X,SDG6XGOLDEN,1.0"}}

# A 5 kW, 2 MHz unit (CommandProcessing's command values) with tuning limits it reports back.
COMMAND_PROCESSING_BENCH = {"fixed_frequency_hz": (1_700_000, 2_300_000), "power_limits_w": (10, 5000),
                            "ae_values": {144: 1700, 145: 2300, 146: 1985}}

LYNX = "LYNXGOLDEN"
NAVIGATOR_PORT = "COM11"
DC_OPERATING_POINT_GUI = {"DUT IP / hostname": DUT, "Lynx hostname / port": LYNX, "RF frequencies (kHz)": "13560",
                          "Power sweep setpoints (W)": "50, 100"}
POWER_ACCURACY_GUI = {"DUT IP / hostname": DUT, "Lynx hostname / port": LYNX, "Number of frequency points": "3",
                      "Warm-up time (s)": "1"}
POWER_LINEARITY_GUI = {"Generator hostname / IP address": DUT, "Lynx hostname / IP address": LYNX,
                       "Number of collection cycles": "1"}
POWER_PROFILE_GUI = {"DUT IP / hostname": DUT, "Lynx hostname / port": LYNX, "Navigator COM port": NAVIGATOR_PORT}
# Tuner-loss maps (shunt, series, R, X, efficiency), one per frequency: two 50-ohm-ish loads each.
POWER_PROFILE_MAPS = ("5000,5100,50,0,0.9\n5200,5300,55,5,0.9\n",) * 3
HARMONICS_GUI = {"DUT IP / hostname": DUT, "Spectrum analyzer IP / hostname": "192.0.2.12",
                 "Generator frequencies (Hz)": "13560000, 14238000", "Power setpoints (W)": "50, 100"}

PSYNC_SLAVE = "PSYNCSLAVE"
PULSED = {"acquisition_model": True, "pulse_model": True}

CW_GUI = {"Oscilloscope IP address": "192.0.2.10", "DUT IP / hostname": DUT, "Acquisitions per setpoint": "1",
          "RF frequencies (kHz)": "13000, 13560", "Power setpoints (script units)": "10, 50"}
SLP_GUI = {"Scope VISA address": SCOPE, "DUT IP / hostname": DUT, "Acquisitions per condition": "1",
           "RF frequencies (kHz)": "13560", "Base power setpoints": "10, 50", "Pulse frequencies (Hz)": "4000",
           "Duty cycles (%)": "20"}
DLP_GUI = {"Scope VISA address": SCOPE, "DUT IP / hostname": DUT, "Acquisitions per condition": "1",
           "RF frequencies (kHz)": "13560", "Pulse frequencies (Hz)": "4000", "Duty cycles (%)": "50",
           "Ordered DLP transitions (W)": "50 -> 100\n100 -> 50"}
PSYNC_GUI = {"Master generator hostname / IP": DUT, "Slave generator hostname / IP": PSYNC_SLAVE,
             "Oscilloscope hostname or IP address": "192.0.2.10", "RF frequencies [MHz]": "13.56",
             "Power setpoints [W]": "100", "Pulse frequencies [Hz]": "1000", "Requested duty cycles [%]": "50",
             "Samples per condition": "1", "Capture views": "RISE, FULL_PULSE"}
PSYNC_LIVE = {"Dry run: do not contact instruments": False, "Enable RF during captures": True}


@dataclass(frozen=True)
class Scenario:
    name: str
    script: str
    drive: Callable[[types.ModuleType, Bench, Path], Any] | None
    refuse: frozenset[str] = frozenset()
    gui: GuiRun | None = None
    bench: dict[str, Any] = field(default_factory=dict)     # Bench model switches
    expect: str = "return 0"                                # golden outcome (error-path scenarios differ)
    clock_tick: float = 0.0                                 # see Clock.tick


SCENARIOS = {s.name: s for s in (
    Scenario("cex_lock_acquisition", "collection_plugins/analog_port_timing/CEX_Lock_Acquisition_Collection.py", _cex),
    Scenario("interlock_response_timing", "collection_plugins/analog_port_timing/Interlock_Response_Timing_Collection.py", _interlock),
    # The supply's VISA INSTR address is refused, so its ::SOCKET fallback path is exercised.
    Scenario("interlock_response_timing_socket_fallback",
             "collection_plugins/analog_port_timing/Interlock_Response_Timing_Collection.py", _interlock,
             frozenset({SUPPLY})),
    Scenario("cex_lock_acquisition_wavegen_fallback",
             "collection_plugins/analog_port_timing/CEX_Lock_Acquisition_Collection.py", _cex, frozenset({WAVEGEN})),
    Scenario("rf_enable_timing", "collection_plugins/analog_port_timing/RF_enable_timing_collection.py", _rf_enable),
    Scenario("setpoint_transfer_function", "collection_plugins/analog_port_timing/setpoint_transfer_function_collection.py",
             _setpoint_transfer),
    Scenario("setpoint_transfer_function_wavegen_fallback",
             "collection_plugins/analog_port_timing/setpoint_transfer_function_collection.py", _setpoint_transfer,
             frozenset({WAVEGEN})),
    Scenario("analog_processing_timing", "collection_plugins/analog_port_timing/analog_processing_timing_collection.py",
             _analog_processing),
    Scenario("analog_processing_timing_wavegen_fallback",
             "collection_plugins/analog_port_timing/analog_processing_timing_collection.py", _analog_processing,
             frozenset({WAVEGEN})),
    Scenario("analog_processing_timing_bk9141_socket_fallback",
             "collection_plugins/analog_port_timing/analog_processing_timing_collection.py",
             _analog_processing_bk9141_connect, frozenset({SUPPLY})),
    # Rise/Fall and Pulse Sync run end to end as __main__ through their setup windows.
    Scenario("rise_fall_cw_rise", "collection_plugins/rise_fall_time/CWDataCollection_GUI.py", None,
             gui=GuiRun(CW_GUI, "Start Collection", 4, {"Rising Edge": True}), bench={"pulse_model": True}),
    Scenario("rise_fall_cw_fall", "collection_plugins/rise_fall_time/CWDataCollection_GUI.py", None,
             gui=GuiRun(CW_GUI, "Start Collection", 4, {"Falling Edge": True}), bench={"pulse_model": True}),
    Scenario("rise_fall_slp", "collection_plugins/rise_fall_time/SLPDataCollection_GUI.py", None,
             gui=GuiRun(SLP_GUI, "Start Collection", 4, {"Both Edges": True}), bench=PULSED),
    Scenario("rise_fall_dlp", "collection_plugins/rise_fall_time/DLPDataCollection_GUI.py", None,
             gui=GuiRun(DLP_GUI, "Start Test", 2, {"All configured transitions": True}), bench=PULSED),
    Scenario("pulse_sync", "collection_plugins/pulse_sync/Psync_Data_Collection.py", None,
             gui=GuiRun(PSYNC_GUI, "Start Test", 8, PSYNC_LIVE), bench=PULSED),
    Scenario("arc_management", "collection_plugins/arc_management/arc_management_collection.py", _arc,
             bench=ARC_BENCH),
    # Wrong instrument at the clacker address: the failure path (scope recovery, cleanup) is unchanged.
    Scenario("arc_management_wrong_function_generator",
             "collection_plugins/arc_management/arc_management_collection.py", _arc, bench={"ae_trigger_model": True},
             expect="raise RuntimeError: Connected instrument is not an SDG6022X: 'FAKE,TCPIP0::192.0.2.11::INSTR,0,1'"),
    Scenario("rf_ramping", "collection_plugins/rf_ramping/RF_Ramp_Collection.py", _rf_ramp),
    Scenario("rf_tuning_connect_and_match", "collection_plugins/rf_tuning/frequency_tuning_collection.py", _rf_tuning,
             bench={"navigator_port": "COM11"}),
    Scenario("rf_onoff_scope_capture", "collection_plugins/rf_on_off_timing/rf_onoff_scope_capture_GUI.py", None,
             gui=GuiRun({}, None, 6, argv=RF_ONOFF_ARGS), bench={"ae_trigger_model": True}),
    # A VISA timeout mid-capture takes the scope-recovery branch, not the trigger-timeout branch.
    Scenario("rf_onoff_scope_capture_visa_timeout",
             "collection_plugins/rf_on_off_timing/rf_onoff_scope_capture_GUI.py", None,
             gui=GuiRun({}, None, 3, argv=RF_ONOFF_ARGS), bench={"ae_trigger_model": True, "visa_faults": {"CURVE?": 1}}),
    # A VISA timeout while configuring the trigger: device clear, then the script exits with an error.
    Scenario("rf_onoff_scope_capture_trigger_setup_timeout",
             "collection_plugins/rf_on_off_timing/rf_onoff_scope_capture_GUI.py", None,
             gui=GuiRun({}, None, 0, argv=RF_ONOFF_ARGS),
             bench={"ae_trigger_model": True, "visa_faults": {"TRIGGER:A:EDGE:SOURCE?": 1}}, expect="raise SystemExit: 1"),
    Scenario("command_processing", "collection_plugins/rf_on_off_timing/CommandProcessing.py", None,
             gui=GuiRun({}, None, 0), bench=COMMAND_PROCESSING_BENCH,
             # Bench-specific benchmark: runs until a firmware-feature check the scripted DUT does not model.
             expect="raise Exception: Cannot do this if Tuning algorithm III is not enabled!"),
    # Lynx/Navigator/spectrum-analyzer collectors, run end to end as __main__.
    Scenario("dc_operating_point", "collection_plugins/dc_operating_point/DC_Operating_Point_Collection_GUI.py", None,
             gui=GuiRun(DC_OPERATING_POINT_GUI, "Start Test", 0, stdin="\n", finished="DONE"), clock_tick=0.05),
    Scenario("power_accuracy", "collection_plugins/power_linearity_accuracy/Power_Accuracy_Collection_GUI.py", None,
             gui=GuiRun(POWER_ACCURACY_GUI, "Start Test", 0, finished="Test Complete")),
    Scenario("power_linearity", "collection_plugins/power_linearity_accuracy/Power_Linearity_Collection_GUI.py", None,
             gui=GuiRun(POWER_LINEARITY_GUI, "Start Test", 0, finished="Test Complete"),
             bench={"power_limits_w": (50, 3000)}),  # its warm-up commands the reported supply size
    Scenario("power_profile", "collection_plugins/power_profile/Power_Profile_Collection_GUI.py", None,
             gui=GuiRun({**POWER_PROFILE_GUI, "Tuner map parent folder": "."}, "Start Test", 0, stdin="\n",
                        finished="DONE", open_files=POWER_PROFILE_MAPS),
             bench={"navigator_port": NAVIGATOR_PORT}, clock_tick=0.05),
    Scenario("harmonics_spurious", "collection_plugins/harmonics_spurious/Harmonics_Spurious_Collection.py", None,
             gui=GuiRun(HARMONICS_GUI, "Start Test", 0, {"Run Harmonics test": True, "Run Spurious test": True},
                        finished="Spectrum collection and cycle averaging complete.")),
    Scenario("rf_enable_timing_wavegen_fallback",
             "collection_plugins/analog_port_timing/RF_enable_timing_collection.py", _rf_enable,
             frozenset({WAVEGEN})),
)}


def run_scenario(name: str, parent: Path) -> dict:
    scenario = SCENARIOS[name]
    bench = Bench(refuse=set(scenario.refuse), **{k: (dict(v) if isinstance(v, dict) else v) for k, v in scenario.bench.items()})
    clock = Clock(scenario.clock_tick)
    with scripted_bench(bench):
        if scenario.gui is not None:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    outcome = "return " + repr(run_gui_script(scenario.script, scenario.gui, bench, clock, parent))
            except (Exception, SystemExit) as error:
                outcome = f"raise {type(error).__name__}: {error}"
            return {"scenario": name, "outcome": outcome, "log": bench.log}
        module = load_script(scenario.script, clock)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                outcome = "return " + repr(scenario.drive(module, bench, parent))
        except Exception as error:
            outcome = f"raise {type(error).__name__}: {error}"
    return {"scenario": name, "outcome": outcome, "log": bench.log}


def fixture_path(name: str) -> Path:
    return FIXTURE_DIR / f"{name}.json"


def load_golden(name: str) -> dict:
    return json.loads(fixture_path(name).read_text(encoding="utf-8"))


def main() -> int:
    import tempfile

    if len(sys.argv) < 3 or sys.argv[1] != "record":
        print(__doc__)
        return 2
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    names = [n for n in SCENARIOS if n == sys.argv[2] or n.startswith(sys.argv[2] + "_")] if sys.argv[2] != "all" else list(SCENARIOS)
    for name in names:
        with tempfile.TemporaryDirectory() as folder:
            data = run_scenario(name, Path(folder))
        fixture_path(name).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"{name}: {len(data['log'])} entries, {data['outcome']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
