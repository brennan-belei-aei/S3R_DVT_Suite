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
    def __init__(self) -> None:
        self.now = 1_000.0

    def sleep(self, seconds) -> None:
        self.now += max(0.0, float(seconds))

    def monotonic(self) -> float:
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
            return f"FAKE,{resource},0,1"
        if upper == "*OPC?":
            return "1"
        if self.acquisition_model and upper in ("ACQ:STATE?", "ACQUIRE:STATE?"):
            key = upper[:-1]
            state = self.setting(resource, key, "0")
            if state.upper() in {"ON", "1", "RUN"} and self.rf_on():
                self.settings[(resource, key)] = state = "0"
            return state
        if upper == "TRIG:STATE?":
            return self.setting(resource, "TRIG:STATE", "SAVE") if self.trigger_model else "SAVE"
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
            return self.setting(resource, upper[:-1].strip(), "0")
        return "0"

    # -- AE bus (frame-level DUT model) ---------------------------------------------------
    def ae_reply(self, cmd: int, recv_sizes: str, args: tuple, target: Any = None) -> list:
        from tests.tools.golden_frames import _bench_reply

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
            return [0, 4, {2: 14_240_000, 3: 12_880_000}.get(int(args[0]), 13_560_000)]
        if cmd == 154:  # regulation mode: forward power
            return [0, 2, 6]
        if cmd == 169:  # user power limit (W)
            return [0, 2, 6000]
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
        self._bench.record("write", self._resource, command)
        self._bench.wrote(self._resource, command)
        return len(command)

    def query(self, command: str, *args, **kwargs) -> str:
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
    start: str
    curves: int                                    # waveform transfers a complete run makes
    choices: dict[str, Any] = field(default_factory=dict)


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

    operator = fake_tk.Operator(gui.entries, gui.choices, gui.start, str(parent), bench.record)
    fakes = fake_tk.modules(operator)
    path = REPO_ROOT / relative
    spec = importlib.util.spec_from_file_location("__main__", path)
    module = importlib.util.module_from_spec(spec)
    clocks = ("sleep", "time", "monotonic", "perf_counter")
    saved_modules = {name: sys.modules.get(name) for name in (*fakes, "__main__")}
    saved_clocks = {name: getattr(real_time, name) for name in clocks}
    saved_env = {k: v for k, v in os.environ.items() if k.startswith("PARAMOUNT_")}
    root_logger = logging.getLogger()
    saved_handlers, saved_level = list(root_logger.handlers), root_logger.level
    try:
        for key in saved_env:
            del os.environ[key]
        os.environ["PARAMOUNT_COLLECTION_PARENT_FOLDER"] = str(parent)
        sys.modules.update(fakes)
        sys.modules["__main__"] = module
        for name in clocks:
            setattr(real_time, name, getattr(clock, name))
        try:
            spec.loader.exec_module(module)
        except SystemExit as stop:
            if stop.code not in (None, 0):
                raise
    finally:
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
    curves = sum(1 for entry in bench.log if entry.startswith("('query_binary_values'") and "'CURVE?'" in entry)
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
    Scenario("rf_enable_timing_wavegen_fallback",
             "collection_plugins/analog_port_timing/RF_enable_timing_collection.py", _rf_enable,
             frozenset({WAVEGEN})),
)}


def run_scenario(name: str, parent: Path) -> dict:
    scenario = SCENARIOS[name]
    bench = Bench(refuse=set(scenario.refuse), **scenario.bench)
    clock = Clock()
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
