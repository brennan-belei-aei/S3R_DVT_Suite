"""Golden AE-bus frame logs for the DUT command adapter migration.

The retired libraries (AE_Commands_PMNT_HF_1XX, AECommandsDLPUnits) were recorded once,
before deletion, into tests/fixtures/golden_frames/. tests/test_golden_frames.py replays
the same calls against AECommandsUniversal and the current DLP script, and requires
identical frames (cmd, send_sizes, recv_sizes, args), ordering, and outcome.

Recording needs the retired modules, so it only works on a checkout from before Phase 1b:

    .venv\\Scripts\\python -m tests.tools.golden_frames record
"""
from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.util
import inspect
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "golden_frames"
FRAME_LIMIT = 200

ADAPTER_SCRIPTS = [
    "Analog Port Timing/Analog Processing Timing/analog_processing_timing_collection.py",
    "Analog Port Timing/CEX Lock Acquisition Timing/CEX_Lock_Acquisition_Collection.py",
    "Analog Port Timing/Interlock Response Timing/Interlock_Response_Timing_Collection.py",
    "Analog Port Timing/RF Enable Timing/RF_enable_timing_collection.py",
    "Analog Port Timing/Setpoint Transfer Function/setpoint_transfer_function_collection.py",
    "DC Operating Point/DC_Operating_Point_Collection_GUI.py",
    "Harmonics and Spurious/Harmonics_Spurious_Collection.py",
    "Power Linearity and Accuracy/Power_Accuracy_Collection_GUI.py",
    "Power Linearity and Accuracy/Power_Linearity_Collection_GUI.py",
    "Power Profile/Power_Profile_Collection_GUI.py",
    "Rise and Fall/SLPDataCollection_GUI.py",
    "RF Ramping/RF_Ramp_Collection.py",
]
DLP_SCRIPT = "Rise and Fall/DLPDataCollection_GUI.py"

# AECommandsDLPUnits name -> AECommandsUniversal name. Names that already existed in
# Universal with different frames got a DLP_ prefix.
DLP_METHOD_MAP = {
    "RF_OFF": "DLP_RF_OFF",
    "RF_ON": "DLP_RF_ON",
    "setRegulationMode": "DLP_setRegulationMode",
    "DLP_setpoint": "DLP_setpoint_csr",
    "pulseEnable": "pulseEnable",
    "setPulseMode": "setPulseMode",
    "setDLPfreqMode": "setDLPfreqMode",
    "setDLPFreq_s1": "setDLPFreq_s1",
    "setDLPFreq_s0": "setDLPFreq_s0",
    "setPulseFreq_DutyCycle": "setPulseFreq_DutyCycle",
}

GENERIC_VALUES = (0, 1, 3, 6, 13560)


_FILL = {"1": 0x35, "2": 0x3535, "4": 0x35353535, "8": 0x3535353535353535}


def _bench_reply(frame) -> list:
    """Reply shaped by recv_sizes like a healthy unit: set commands CSR 0, host control mode."""
    if frame.cmd < 128:
        return [0, 0, 0]
    if frame.cmd == 155:  # Report Active Control Mode -> 2 (host)
        return [0, 1, 2]
    vals = [0, 8]
    for part in str(frame.recv_sizes).replace(" ", "").split(","):
        count, _, size = part.partition(":")
        if not count.isdigit() or count == "0":
            continue
        if "s" in size:
            vals.append("5" * int(count))
        else:
            vals.extend([_FILL.get(size.rstrip("dD")[:1], 0x35)] * int(count))
    return vals


def _profile(name: str):
    if name == "bench":
        return _bench_reply
    if name == "csr_ok":
        return lambda frame: [0, 0, 0]
    if name == "data":
        return lambda frame: [0, 8] + [2] * 16
    if name == "csr12_on_cmd8":
        return lambda frame: [0, 0, 12] if frame.cmd == 8 else [0, 0, 0]
    if name == "csr12_all":
        return lambda frame: [0, 0, 12]
    raise KeyError(name)


ADAPTER_PROFILES = ("bench", "csr_ok", "data", "csr12_all")
DLP_PROFILES = ("bench", "csr_ok", "data", "csr12_on_cmd8", "csr12_all")


def _ensure_paths() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    importlib.import_module("suite_common.bootstrap")  # puts AE dependency modules on sys.path


@contextlib.contextmanager
def fake_bus(profile: str):
    """Patch AE_Com_Module transports with a recording FakeAEBus for the duration."""
    _ensure_paths()
    import AE_Com_Module
    from tests.fakes.ae_bus import FakeAEBus

    bus = FakeAEBus(default=_profile(profile), limit=FRAME_LIMIT)
    saved = AE_Com_Module.AETCP, AE_Com_Module.AEB32
    AE_Com_Module.AETCP, AE_Com_Module.AEB32 = bus.tcp_factory, bus.serial_factory
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield bus
    finally:
        AE_Com_Module.AETCP, AE_Com_Module.AEB32 = saved


def _outcome(call) -> str:
    try:
        return "return " + repr(call())
    except Exception as error:  # the outcome, including the exception type, is part of the contract
        return f"raise {type(error).__name__}: {error}"


def _wire(bus) -> list[str]:
    return [repr((f.cmd, f.send_sizes, f.recv_sizes, f.args)) for f in bus.frames]


def run_method(module_name: str, method: str, args: list, profile: str) -> dict:
    _ensure_paths()
    module = importlib.import_module(module_name)
    with fake_bus(profile) as bus:
        gen = module.generator("GOLDEN")
        outcome = _outcome(lambda: getattr(gen, method)(*args))
    return {"frames": _wire(bus), "outcome": outcome}


def _literal_args(paths: list[str], methods: set[str]) -> dict[str, set[tuple]]:
    found: dict[str, set[tuple]] = {}
    for rel in paths:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in methods and not node.keywords
                    and all(isinstance(a, ast.Constant) for a in node.args)):
                found.setdefault(node.func.attr, set()).add(tuple(a.value for a in node.args))
    return found


def _arg_sets(func, literals: set[tuple]) -> list[list]:
    params = list(inspect.signature(func).parameters.values())[1:]
    required = [p for p in params if p.default is inspect.Parameter.empty]
    sets = set(literals)
    for value in GENERIC_VALUES:
        sets.add((value,) * len(required))
        if len(params) > len(required):
            sets.add((value,) * len(params))
    return [list(s) for s in sorted(sets, key=repr)]


def _used_methods(module, paths: list[str]) -> set[str]:
    methods = {n for n, _ in inspect.getmembers(module.generator, inspect.isfunction) if not n.startswith("_")}
    used = set()
    for rel in paths:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in methods}
    return used - {"close"}


def record_adapter() -> list[dict]:
    _ensure_paths()
    old = importlib.import_module("AE_Commands_PMNT_HF_1XX")
    methods = _used_methods(old, ADAPTER_SCRIPTS)
    literals = _literal_args(ADAPTER_SCRIPTS, methods)
    cases = []
    for method in sorted(methods):
        for args in _arg_sets(getattr(old.generator, method), literals.get(method, set())):
            for profile in ADAPTER_PROFILES:
                cases.append({"method": method, "args": args, "profile": profile,
                              **run_method("AE_Commands_PMNT_HF_1XX", method, args, profile)})
    return cases


def record_dlp_methods() -> list[dict]:
    _ensure_paths()
    old = importlib.import_module("AECommandsDLPUnits")
    literals = _literal_args([DLP_SCRIPT], set(DLP_METHOD_MAP))
    cases = []
    for method in sorted(DLP_METHOD_MAP):
        for args in _arg_sets(getattr(old.generator, method), literals.get(method, set())):
            for profile in DLP_PROFILES:
                cases.append({"method": method, "new_method": DLP_METHOD_MAP[method], "args": args,
                              "profile": profile, **run_method("AECommandsDLPUnits", method, args, profile)})
    return cases


DLP_SEQUENCE = [
    ("connect_generator", []),
    ("configure_generator_dlp_mode", [13560, 100, 500, 1000, 50]),
    ("turn_rf_off_between_conditions", []),
    ("configure_generator_dlp_mode", [2000, 1500, 0, 5000, 10]),
    ("disable_generator_dlp_mode", []),
    ("close_connections", []),
]


def run_dlp_script_sequence(profile: str) -> dict:
    """Drive the DLP script's own generator functions in collection order."""
    _ensure_paths()
    spec = importlib.util.spec_from_file_location(f"dlp_script_{profile}", REPO_ROOT / DLP_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)
    module.sleep = lambda _seconds: None
    module.GEN_IP = "GOLDEN"
    module.gen = module.scope = module.scope_resource_manager = None
    steps = []
    with fake_bus(profile) as bus:
        for name, args in DLP_SEQUENCE:
            start = len(bus.frames)
            outcome = _outcome(lambda: getattr(module, name)(*args))
            steps.append({"step": name, "args": args, "frames": _wire(bus)[start:], "outcome": outcome})
        closed = bool(bus.transports) and all(t.closed for t in bus.transports)
    return {"profile": profile, "steps": steps, "transport_closed": closed}


def record_dlp_sequence() -> list[dict]:
    return [run_dlp_script_sequence(profile) for profile in DLP_PROFILES]


FIXTURES = {
    "dut_adapter_pmnt_hf_1xx.json": record_adapter,
    "dlp_units_methods.json": record_dlp_methods,
    "dlp_script_sequence.json": record_dlp_sequence,
}


def load_fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def main() -> int:
    if sys.argv[1:] != ["record"]:
        print(__doc__)
        return 2
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, recorder in FIXTURES.items():
        data = recorder()
        (FIXTURE_DIR / name).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"{name}: {len(data)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
