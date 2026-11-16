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

    def record(self, *entry: Any) -> None:
        self.log.append(repr(entry))

    # -- instrument state -----------------------------------------------------------------
    def wrote(self, resource: str, command: str) -> None:
        text = command.strip()
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
        if upper == "TRIG:STATE?":
            return "SAVE"
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
    def ae_reply(self, cmd: int, recv_sizes: str, args: tuple) -> list:
        from tests.tools.golden_frames import _bench_reply

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
        reply = self._bench.ae_reply(cmd, recv_sizes, tuple(args))
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


@dataclass(frozen=True)
class Scenario:
    name: str
    script: str
    drive: Callable[[types.ModuleType, Bench, Path], Any]
    refuse: frozenset[str] = frozenset()


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
    Scenario("rf_enable_timing_wavegen_fallback",
             "collection_plugins/analog_port_timing/RF_enable_timing_collection.py", _rf_enable,
             frozenset({WAVEGEN})),
)}


def run_scenario(name: str, parent: Path) -> dict:
    scenario = SCENARIOS[name]
    bench = Bench(refuse=set(scenario.refuse))
    clock = Clock()
    with scripted_bench(bench):
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
