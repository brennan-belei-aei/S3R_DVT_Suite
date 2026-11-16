"""Arc Management collection, first cut.

Based on capture_arc_management.py. Preserves its CH1-CH4 Tektronix MSO44
capture workflow and adds DUT control through AECommandsUniversal plus embedded
Tektronix and Siglent control for a manually configured clacker waveform.

IMPORTANT: Until the clacker waveform is confirmed, this script does not program
waveform shape, voltage, offset, frequency, duty cycle, or burst. It only toggles
the selected, preconfigured function-generator output.
"""
from __future__ import annotations

import csv
import importlib
import json
import os
import re
import signal
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyvisa
import tkinter as tk
from contextlib import contextmanager
from tkinter import filedialog, messagebox, ttk

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.config import plugin_settings_path  # noqa: E402
import suite_common.bootstrap  # noqa: E402,F401  (AE command modules on sys.path)
SETTINGS_FILE = plugin_settings_path("arc_management", "collection")
STOP = threading.Event()

# Preserved capture defaults from capture_arc_management.py.
DEFAULT_SAMPLES = 10
DEFAULT_SCOPE_IP = "172.24.242.29"
TRIGGER_CHANNEL = 3
DEFAULT_TRIGGER_LEVEL_V = 1.0
TRIGGER_SLOPE = "RISE"
TRIGGER_COUPLING = "DC"
TRIGGER_MODE = "NORMAL"
DEFAULT_TRIGGER_TIMEOUT_S = 120.0
TRIGGER_POLL_S = 0.02
ARM_READY_TIMEOUT_S = 5.0
DEFAULT_INTER_SAMPLE_DELAY_S = 0.25

# Conservative first-cut DUT defaults. 500 W is a hard ceiling, not a forced test point.
DEFAULT_POWER_W = 250
MAX_POWER_W = 500
DEFAULT_FIXED_FREQUENCY_KHZ = 13_560
DEFAULT_REGULATION_MODE = 0
DEFAULT_RF_SETTLE_S = 1.0
DEFAULT_POST_ARC_RECOVERY_S = 1.0
DEFAULT_CLACKER_CHANNEL = 2
DEFAULT_CLACKER_ON_S = 0.10



# Internal VISA manager. This replaces local scope.py, siglent_sdg6022x.py, and
# visa_manager.py dependencies while retaining pyvisa/NI-VISA as runtime requirements.
_VISA_MANAGER = None
_VISA_LOCK = threading.RLock()


def get_resource_manager():
    global _VISA_MANAGER
    with _VISA_LOCK:
        if _VISA_MANAGER is None:
            _VISA_MANAGER = pyvisa.ResourceManager()
        return _VISA_MANAGER


@contextmanager
def visa_session(resource: str, timeout_ms: int = 120_000):
    instrument = None
    try:
        instrument = get_resource_manager().open_resource(resource)
        instrument.timeout = timeout_ms
        instrument.read_termination = "\n"
        instrument.write_termination = "\n"
        instrument.chunk_size = 25 * 1024 * 1024
        yield instrument
    finally:
        if instrument is not None:
            instrument.close()


def scope_write(resource: str, command: str) -> None:
    with visa_session(resource) as instrument:
        instrument.write(command)
        instrument.write("*WAI")


def scope_enable_channel(resource: str, channel: int) -> None:
    if channel not in (1, 2, 3, 4):
        raise ValueError("Scope channel must be 1 through 4")
    scope_write(resource, f"DISPLAY:WAVEVIEW1:CH{channel}:STATE ON")


def scope_setup_edge_trigger(resource: str, channel: int, level: float) -> dict:
    with visa_session(resource) as instrument:
        instrument.write("TRIGGER:A:TYPE EDGE")
        instrument.write(f"TRIGGER:A:EDGE:SOURCE CH{channel}")
        instrument.write(f"TRIGGER:A:EDGE:SLOPE {TRIGGER_SLOPE}")
        instrument.write(f"TRIGGER:A:EDGE:COUPLING {TRIGGER_COUPLING}")
        instrument.write(f"TRIGGER:A:LEVEL:CH{channel} {float(level):.12g}")
        instrument.write(f"TRIGGER:A:MODE {TRIGGER_MODE}")
        instrument.write("*WAI")
        return {
            "source": instrument.query("TRIGGER:A:EDGE:SOURCE?").strip(),
            "level": float(instrument.query(f"TRIGGER:A:LEVEL:CH{channel}?")),
            "slope": instrument.query("TRIGGER:A:EDGE:SLOPE?").strip(),
            "mode": instrument.query("TRIGGER:A:MODE?").strip(),
            "coupling": instrument.query("TRIGGER:A:EDGE:COUPLING?").strip(),
        }


def scope_arm(resource: str) -> None:
    deadline = time.monotonic() + ARM_READY_TIMEOUT_S
    last_state = "unknown"
    with visa_session(resource) as instrument:
        instrument.write("ACQUIRE:STOPAFTER SEQUENCE")
        instrument.write("ACQUIRE:STATE RUN")
        while time.monotonic() < deadline and not STOP.is_set():
            last_state = instrument.query("ACQUIRE:STATE?").strip().upper()
            if last_state in {"1", "ON", "RUN"}:
                return
            time.sleep(0.05)
    if STOP.is_set():
        raise InterruptedError("Stop requested while arming the oscilloscope")
    raise TimeoutError(f"Scope did not arm within {ARM_READY_TIMEOUT_S:g} s; state={last_state}")


def scope_wait_for_acquisition(resource: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    with visa_session(resource) as instrument:
        while time.monotonic() < deadline and not STOP.is_set():
            state = instrument.query("ACQUIRE:STATE?").strip().upper()
            if state in {"0", "OFF", "STOP"}:
                return
            time.sleep(TRIGGER_POLL_S)
    if STOP.is_set():
        raise InterruptedError("Stop requested while waiting for the scope trigger")
    raise TimeoutError(f"Scope acquisition did not complete within {timeout_s:g} s")


def scope_stop(resource: str) -> None:
    scope_write(resource, "ACQUIRE:STATE STOP")


def scope_recover(resource: str) -> str:
    with visa_session(resource, timeout_ms=5000) as instrument:
        instrument.write("ACQUIRE:STATE STOP")
        instrument.write("*CLS")
        instrument.write("*WAI")
        identity = instrument.query("*IDN?").strip()
        state = instrument.query("ACQUIRE:STATE?").strip().upper()
        if state not in {"0", "OFF", "STOP"}:
            raise RuntimeError(f"Scope recovery failed; acquisition state is {state}")
        return identity


def capture_scope_channel(instrument, channel: int):
    name = f"CH{channel}"
    instrument.write(f"DATA:SOURCE {name}")
    instrument.write("DATA:ENCDG RIBINARY")
    instrument.write("DATA:WIDTH 2")
    record_length = int(float(instrument.query("HORIZONTAL:RECORDLENGTH?")))
    instrument.write("DATA:START 1")
    instrument.write(f"DATA:STOP {record_length}")
    xincr = float(instrument.query("WFMOUTPRE:XINCR?"))
    xzero = float(instrument.query("WFMOUTPRE:XZERO?"))
    pt_off = float(instrument.query("WFMOUTPRE:PT_OFF?"))
    ymult = float(instrument.query("WFMOUTPRE:YMULT?"))
    yzero = float(instrument.query("WFMOUTPRE:YZERO?"))
    yoff = float(instrument.query("WFMOUTPRE:YOFF?"))
    xunit = instrument.query("WFMOUTPRE:XUNIT?").strip().strip('"')
    yunit = instrument.query("WFMOUTPRE:YUNIT?").strip().strip('"')
    raw = np.asarray(instrument.query_binary_values(
        "CURVE?", datatype="h", is_big_endian=True,
        container=np.array, expect_termination=False), dtype=np.float64)
    if not raw.size:
        raise RuntimeError(f"No waveform points returned for {name}")
    index = np.arange(raw.size, dtype=np.float64)
    times = xzero + (index - pt_off) * xincr
    values = (raw - yoff) * ymult + yzero
    metadata = dict(channel=name, points=int(raw.size), xincr=xincr, xzero=xzero,
                    pt_off=pt_off, ymult=ymult, yzero=yzero, yoff=yoff,
                    xunit=xunit, yunit=yunit)
    return times, values, metadata


class SDG6022X:
    """Embedded, output-state-only Siglent SDG6022X controller."""
    def __init__(self, ip_address: str, timeout_ms: int = 5000):
        self.resource = f"TCPIP0::{ip_address}::INSTR"
        self.instrument = get_resource_manager().open_resource(self.resource)
        self.instrument.timeout = timeout_ms
        self.instrument.write_termination = "\n"
        self.instrument.read_termination = None
        self.closed = False
        identity = self.query("*IDN?")
        if "SDG6022X" not in identity.upper():
            self.close()
            raise RuntimeError(f"Connected instrument is not an SDG6022X: {identity!r}")

    @classmethod
    def from_ip(cls, ip_address: str):
        visa_from_ip(ip_address)  # validation
        return cls(ip_address)

    def write(self, command: str) -> None:
        if self.closed:
            raise RuntimeError("Siglent connection is closed")
        self.instrument.write(command)

    def query(self, command: str) -> str:
        if self.closed:
            raise RuntimeError("Siglent connection is closed")
        return self.instrument.query(command).strip()

    @staticmethod
    def validate_channel(channel: int) -> int:
        if channel not in (1, 2) or isinstance(channel, bool):
            raise ValueError("Siglent channel must be 1 or 2")
        return channel

    def output_state(self, channel: int) -> str:
        channel = self.validate_channel(channel)
        response = self.query(f"C{channel}:OUTP?")
        match = re.search(r"\b(ON|OFF)\b", response.upper())
        if not match:
            raise RuntimeError(f"Could not parse Siglent output response: {response!r}")
        return match.group(1)

    def output_on(self, channel: int = 2) -> None:
        channel = self.validate_channel(channel)
        self.write(f"C{channel}:OUTP ON")
        if self.output_state(channel) != "ON":
            raise RuntimeError(f"Siglent channel {channel} did not report ON")

    def output_off(self, channel: int = 2) -> None:
        channel = self.validate_channel(channel)
        self.write(f"C{channel}:OUTP OFF")
        if self.output_state(channel) != "OFF":
            raise RuntimeError(f"Siglent channel {channel} did not report OFF")

    def close(self) -> None:
        if not self.closed:
            self.instrument.close()
            self.closed = True

def env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def visa_from_ip(ip: str) -> str:
    value = ip.strip()
    old = re.fullmatch(r"TCPIP(?:0)?::([^:]+)::(?:inst0::)?INSTR", value, re.I)
    if old:
        value = old.group(1)
    parts = value.split(".")
    if len(parts) != 4:
        raise ValueError("Enter an IPv4 address such as 172.24.242.29.")
    try:
        numbers = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError("IP address must contain numbers only.") from exc
    if any(number < 0 or number > 255 for number in numbers):
        raise ValueError("Each IP address number must be from 0 through 255.")
    return f"TCPIP0::{value}::inst0::INSTR"


def safe_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip().rstrip(".") or "Unknown"


def create_run_directory(parent: Path, generator_name: str) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    base = f"{stamp} - Arc Management - {safe_name(generator_name)}"
    candidate = parent / base
    number = 2
    while candidate.exists():
        candidate = parent / f"{base} - Run {number}"
        number += 1
    candidate.mkdir(parents=False)
    return candidate


def request_stop(*_args) -> None:
    if not STOP.is_set():
        print("Stop requested; performing safe cleanup...", flush=True)
    STOP.set()


def load_universal_module():
    errors = []
    for name in ("AECommandsUniversal",):
        try:
            return importlib.import_module(name)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("AECommandsUniversal could not be imported. " + " | ".join(errors))


def create_dut(address: str):
    module = load_universal_module()
    for class_name in ("generator", "Generator"):
        factory = getattr(module, class_name, None)
        if factory:
            try:
                return factory(address)
            except Exception as exc:
                raise RuntimeError(f"Could not connect using {class_name}({address!r}): {exc}") from exc
    raise RuntimeError("AECommandsUniversal does not expose generator or Generator.")


def call_required(device: Any, names: tuple[str, ...], *args):
    for name in names:
        method = getattr(device, name, None)
        if callable(method):
            return method(*args)
    raise RuntimeError(f"AECommandsUniversal generator lacks required method: {' or '.join(names)}")


def call_optional(device: Any, names: tuple[str, ...], *args) -> bool:
    for name in names:
        method = getattr(device, name, None)
        if callable(method):
            method(*args)
            return True
    return False


def safe_rf_off(dut: Any | None) -> None:
    if dut is None:
        return
    try:
        call_required(dut, ("rfOFF", "rf_off", "setRFOff"))
    except Exception as exc:
        print(f"Warning: DUT RF OFF failed: {exc}", flush=True)


def close_device(device: Any | None, label: str) -> None:
    if device is None:
        return
    try:
        closer = getattr(device, "close", None)
        if callable(closer):
            closer()
    except Exception as exc:
        print(f"Warning: {label} close failed: {exc}", flush=True)


def configure_dut(dut: Any, regulation_mode: int, frequency_khz: int, power_w: int) -> None:
    safe_rf_off(dut)
    # Fixed-frequency mode is 0 in the coworker generator API used elsewhere.
    call_required(dut, ("setFrequencyMode", "set_frequency_mode"), 0)
    call_required(dut, ("setFixedFrequency", "set_fixed_frequency"), frequency_khz)
    call_required(dut, ("setRegulationMode", "set_regulation_mode"), regulation_mode)
    if not call_optional(dut, ("setPowerSetpoint", "set_power_setpoint", "setSetpoint"), power_w):
        # Some universal revisions separate setpoint by regulation mode.
        call_required(dut, ("setPower", "set_power"), power_w)


def configure_scope(resource: str, trigger_level: float) -> None:
    for channel in (1, 2, 3, 4):
        scope_enable_channel(resource, channel)
    scope_setup_edge_trigger(resource, TRIGGER_CHANNEL, trigger_level)


def capture_all_channels(resource: str):
    waveforms: dict[str, np.ndarray] = {}
    metadata: dict[str, dict] = {}
    with visa_session(resource) as instrument:
        identity = instrument.query("*IDN?").strip()
        for channel_number in (1, 2, 3, 4):
            name = f"CH{channel_number}"
            times, values, channel_metadata = capture_scope_channel(instrument, channel_number)
            if channel_number == 1:
                common_times = times
            elif len(times) != len(common_times) or not np.allclose(times, common_times, rtol=0.0, atol=1e-12):
                raise RuntimeError(f"{name} time coordinates do not match CH1")
            waveforms[name] = np.asarray(values, dtype=float)
            metadata[name] = channel_metadata
    return common_times, waveforms, {"identity": identity, "channels": metadata}

def save_combined_csv(path: Path, times: np.ndarray, waveforms: dict[str, np.ndarray]) -> None:
    lengths = {len(times), *(len(waveforms[f"CH{i}"]) for i in range(1, 5))}
    if len(lengths) != 1:
        raise RuntimeError(f"Channel lengths do not match: {sorted(lengths)}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time_s", "ch1_v", "ch2_v", "ch3_v", "ch4_v"])
        writer.writerows(zip(times, waveforms["CH1"], waveforms["CH2"], waveforms["CH3"], waveforms["CH4"]))


@dataclass
class Settings:
    generator_address: str
    scope_ip: str
    function_generator_ip: str
    parent_folder: str
    sample_count: int
    power_w: int
    fixed_frequency_khz: int
    regulation_mode: int
    trigger_level_v: float
    trigger_timeout_s: float
    rf_settle_s: float
    clacker_channel: int
    clacker_on_s: float
    post_arc_recovery_s: float
    inter_sample_delay_s: float
    arc_management_confirmed: bool
    clacker_preconfigured_confirmed: bool


def write_manifest(folder: Path, settings: Settings, status: str, completed: int, error: str = "") -> None:
    data = {
        "schema_version": 1, "test_id": "arc_management", "test_name": "Arc Management",
        "status": status, "updated_at": datetime.now().astimezone().isoformat(),
        "settings": asdict(settings), "completed_samples": completed,
        "clacker_configuration": "Manually configured on function generator; script toggles output only.",
        "error": error,
    }
    (folder / "run_manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def execute(settings: Settings, status=lambda _text: None) -> Path:
    if settings.power_w > MAX_POWER_W:
        raise ValueError(f"Power setpoint cannot exceed {MAX_POWER_W} W.")
    if not settings.arc_management_confirmed:
        raise ValueError("Confirm that DUT Arc Management is manually enabled with default settings.")
    if not settings.clacker_preconfigured_confirmed:
        raise ValueError("Confirm that the clacker waveform is safely preconfigured on the function generator.")
    parent = Path(settings.parent_folder).expanduser().resolve()
    if not parent.is_dir():
        raise ValueError(f"Parent folder does not exist: {parent}")
    scope_resource = visa_from_ip(settings.scope_ip)
    run = create_run_directory(parent, settings.generator_address)
    STOP.clear(); completed = 0; dut = clacker = None
    write_manifest(run, settings, "starting", completed)
    try:
        status("Connecting to DUT...")
        dut = create_dut(settings.generator_address)
        configure_dut(dut, settings.regulation_mode, settings.fixed_frequency_khz, settings.power_w)
        status("Connecting to clacker function generator...")
        clacker = SDG6022X.from_ip(settings.function_generator_ip)
        clacker.output_off(settings.clacker_channel)
        status("Configuring oscilloscope...")
        configure_scope(scope_resource, settings.trigger_level_v)
        write_manifest(run, settings, "running", completed)

        for sample in range(1, settings.sample_count + 1):
            if STOP.is_set():
                break
            status(f"Sample {sample}/{settings.sample_count}: preparing DUT")
            safe_rf_off(dut)
            clacker.output_off(settings.clacker_channel)
            scope_arm(scope_resource)
            if STOP.is_set():
                break
            call_required(dut, ("rfON", "rf_on", "setRFOn"))
            if STOP.wait(settings.rf_settle_s):
                break
            status(f"Sample {sample}/{settings.sample_count}: actuating clacker")
            clacker.output_on(settings.clacker_channel)
            # Keep the output active only for the operator-confirmed interval.
            STOP.wait(settings.clacker_on_s)
            clacker.output_off(settings.clacker_channel)
            scope_wait_for_acquisition(scope_resource, settings.trigger_timeout_s)
            safe_rf_off(dut)
            status(f"Sample {sample}/{settings.sample_count}: downloading CH1-CH4")
            times, waveforms, metadata = capture_all_channels(scope_resource)
            stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            stem = f"arc_sample_{sample:04d}_{stamp}"
            csv_path = run / f"{stem}.csv"
            save_combined_csv(csv_path, times, waveforms)
            (run / f"{stem}_info.json").write_text(json.dumps({
                "sample": sample, "scope": metadata,
                "power_w": settings.power_w, "fixed_frequency_khz": settings.fixed_frequency_khz,
                "regulation_mode": settings.regulation_mode,
                "clacker_channel": settings.clacker_channel,
                "clacker_on_s": settings.clacker_on_s,
            }, indent=2, default=str), encoding="utf-8")
            completed += 1
            write_manifest(run, settings, "running", completed)
            print(f"Saved sample {sample}: {csv_path.name} ({len(times):,} points/channel)", flush=True)
            STOP.wait(settings.post_arc_recovery_s)
            if sample < settings.sample_count:
                STOP.wait(settings.inter_sample_delay_s)

        final = "cancelled" if STOP.is_set() else "completed"
        write_manifest(run, settings, final, completed)
        return run
    except BaseException as exc:
        write_manifest(run, settings, "failed", completed, str(exc))
        try:
            scope_recover(scope_resource)
        except Exception as recovery_error:
            print(f"Scope recovery failed: {recovery_error}", flush=True)
        raise
    finally:
        try:
            if clacker is not None:
                clacker.output_off(settings.clacker_channel)
        except Exception as exc:
            print(f"Warning: clacker output OFF failed: {exc}", flush=True)
        safe_rf_off(dut)
        try:
            scope_stop(scope_resource)
        except Exception as exc:
            print(f"Warning: scope stop failed: {exc}", flush=True)
        close_device(clacker, "function generator")
        close_device(dut, "DUT")


def load_saved() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root; self.worker = None; saved = load_saved()
        defaults = {
            "generator_address": env("PARAMOUNT_GENERATOR_ADDRESS", default=saved.get("generator_address", "PMNT075224")),
            "scope_ip": env("PARAMOUNT_OSCILLOSCOPE_ADDRESS", default=saved.get("scope_ip", DEFAULT_SCOPE_IP)),
            "function_generator_ip": env("PARAMOUNT_WAVEFORM_GENERATOR_ADDRESS", "PARAMOUNT_FUNCTION_GENERATOR_ADDRESS", default=saved.get("function_generator_ip", "172.24.242.22")),
            "parent_folder": os.environ.get("PARAMOUNT_COLLECTION_PARENT_FOLDER", saved.get("parent_folder", str(Path.home()))),
            "sample_count": saved.get("sample_count", DEFAULT_SAMPLES), "power_w": saved.get("power_w", DEFAULT_POWER_W),
            "fixed_frequency_khz": saved.get("fixed_frequency_khz", DEFAULT_FIXED_FREQUENCY_KHZ),
            "regulation_mode": saved.get("regulation_mode", DEFAULT_REGULATION_MODE),
            "trigger_level_v": saved.get("trigger_level_v", DEFAULT_TRIGGER_LEVEL_V),
            "trigger_timeout_s": saved.get("trigger_timeout_s", DEFAULT_TRIGGER_TIMEOUT_S),
            "rf_settle_s": saved.get("rf_settle_s", DEFAULT_RF_SETTLE_S),
            "clacker_channel": saved.get("clacker_channel", DEFAULT_CLACKER_CHANNEL),
            "clacker_on_s": saved.get("clacker_on_s", DEFAULT_CLACKER_ON_S),
            "post_arc_recovery_s": saved.get("post_arc_recovery_s", DEFAULT_POST_ARC_RECOVERY_S),
            "inter_sample_delay_s": saved.get("inter_sample_delay_s", DEFAULT_INTER_SAMPLE_DELAY_S),
        }
        self.v = {key: tk.StringVar(value=str(value)) for key, value in defaults.items()}
        self.arc_ok = tk.BooleanVar(value=False); self.clacker_ok = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready")
        self.build()

    def build(self):
        self.root.title("Arc Management Data Collection - First Cut"); self.root.geometry("900x820")
        frame = ttk.Frame(self.root, padding=16); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Arc Management Data Collection", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Preserves the coworker CH1-CH4 scope capture and adds DUT plus clacker output control. The clacker waveform must be configured manually until its exact settings are confirmed.", wraplength=850).pack(anchor="w", pady=(2,10))
        box = ttk.LabelFrame(frame, text="Bench and output", padding=10); box.pack(fill="x"); box.columnconfigure(1, weight=1)
        fields = [("DUT address / hostname","generator_address"),("Tektronix MSO44 IP address","scope_ip"),("Siglent SDG6022X IP address","function_generator_ip"),("Collection parent folder","parent_folder")]
        for i,(label,key) in enumerate(fields):
            ttk.Label(box,text=label).grid(row=i,column=0,sticky="w",pady=4); ttk.Entry(box,textvariable=self.v[key]).grid(row=i,column=1,sticky="ew",padx=8,pady=4)
        ttk.Button(box,text="Browse...",command=self.browse).grid(row=3,column=2)
        test = ttk.LabelFrame(frame,text="Test settings",padding=10); test.pack(fill="x",pady=10)
        settings = [("Successful captures","sample_count"),("Power setpoint (W, maximum 500)","power_w"),("Fixed frequency (kHz)","fixed_frequency_khz"),("Regulation mode value","regulation_mode"),("CH3 trigger level (V)","trigger_level_v"),("Trigger timeout (s)","trigger_timeout_s"),("RF settle before clacker (s)","rf_settle_s"),("Clacker output channel (1 or 2)","clacker_channel"),("Clacker output ON time (s)","clacker_on_s"),("Post-arc recovery (s)","post_arc_recovery_s"),("Inter-sample delay (s)","inter_sample_delay_s")]
        for i,(label,key) in enumerate(settings):
            row=i//2; col=(i%2)*2; ttk.Label(test,text=label).grid(row=row,column=col,sticky="w",pady=3,padx=(0,6)); ttk.Entry(test,textvariable=self.v[key],width=12).grid(row=row,column=col+1,sticky="w",pady=3,padx=(0,18))
        confirms=ttk.LabelFrame(frame,text="Required confirmations",padding=10); confirms.pack(fill="x")
        ttk.Checkbutton(confirms,text="DUT Arc Management is manually enabled using default settings.",variable=self.arc_ok).pack(anchor="w",pady=3)
        ttk.Checkbutton(confirms,text="The function-generator waveform is manually configured and safe for the clacker. This script will only toggle output ON/OFF.",variable=self.clacker_ok).pack(anchor="w",pady=3)
        ttk.Label(frame,textvariable=self.status,wraplength=850).pack(anchor="w",pady=(12,5)); self.progress=ttk.Progressbar(frame,mode="indeterminate"); self.progress.pack(fill="x")
        actions=ttk.Frame(frame); actions.pack(fill="x",pady=12); self.stop=ttk.Button(actions,text="Stop Safely",command=request_stop,state="disabled"); self.stop.pack(side="left"); self.start=ttk.Button(actions,text="Start Collection",command=self.start_run); self.start.pack(side="right")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def browse(self):
        selected=filedialog.askdirectory(parent=self.root,initialdir=self.v["parent_folder"].get() or str(Path.home()))
        if selected: self.v["parent_folder"].set(selected)

    def settings(self) -> Settings:
        s=Settings(self.v["generator_address"].get().strip(),self.v["scope_ip"].get().strip(),self.v["function_generator_ip"].get().strip(),self.v["parent_folder"].get().strip(),int(self.v["sample_count"].get()),int(self.v["power_w"].get()),int(self.v["fixed_frequency_khz"].get()),int(self.v["regulation_mode"].get()),float(self.v["trigger_level_v"].get()),float(self.v["trigger_timeout_s"].get()),float(self.v["rf_settle_s"].get()),int(self.v["clacker_channel"].get()),float(self.v["clacker_on_s"].get()),float(self.v["post_arc_recovery_s"].get()),float(self.v["inter_sample_delay_s"].get()),self.arc_ok.get(),self.clacker_ok.get())
        if not all((s.generator_address,s.scope_ip,s.function_generator_ip,s.parent_folder)): raise ValueError("Enter all addresses and a parent folder.")
        if not 1 <= s.power_w <= MAX_POWER_W: raise ValueError("Power must be from 1 through 500 W.")
        if s.sample_count < 1: raise ValueError("Sample count must be at least 1.")
        if s.clacker_channel not in (1,2): raise ValueError("Clacker channel must be 1 or 2.")
        if min(s.trigger_timeout_s,s.rf_settle_s,s.clacker_on_s,s.post_arc_recovery_s,s.inter_sample_delay_s) < 0: raise ValueError("Timing values cannot be negative.")
        visa_from_ip(s.scope_ip); visa_from_ip(s.function_generator_ip)
        return s

    def start_run(self):
        try: settings=self.settings()
        except Exception as exc: messagebox.showerror("Invalid settings",str(exc),parent=self.root); return
        SETTINGS_FILE.write_text(json.dumps(asdict(settings),indent=2),encoding="utf-8")
        STOP.clear(); self.start.configure(state="disabled"); self.stop.configure(state="normal"); self.progress.start(10)
        self.worker=threading.Thread(target=self.work,args=(settings,),daemon=True); self.worker.start()

    def work(self, settings):
        try:
            path=execute(settings,lambda text:self.root.after(0,self.status.set,text)); self.root.after(0,self.finish,True,f"Collection finished.\n\n{path}")
        except Exception as exc:
            traceback.print_exc(); self.root.after(0,self.finish,False,str(exc))

    def finish(self, ok, text):
        self.progress.stop(); self.start.configure(state="normal"); self.stop.configure(state="disabled"); self.status.set(text)
        (messagebox.showinfo if ok else messagebox.showerror)("Arc Management" if ok else "Collection failed",text,parent=self.root)

    def close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Collection running","Request safe stop and close after cleanup?",parent=self.root): return
            request_stop(); self.root.after(250,self.wait_close)
        else: self.root.destroy()
    def wait_close(self):
        if self.worker and self.worker.is_alive(): self.root.after(250,self.wait_close)
        else: self.root.destroy()


def self_test() -> int:
    assert visa_from_ip("172.24.242.29") == "TCPIP0::172.24.242.29::inst0::INSTR"
    assert safe_name("A:B/C") == "A_B_C"
    assert MAX_POWER_W == 500 and TRIGGER_CHANNEL == 3 and DEFAULT_SAMPLES == 10
    assert "scope.py" not in globals() and SDG6022X.validate_channel(2) == 2
    print("Arc Management first-cut collection self-test passed.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv: return self_test()
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal,"SIGTERM"): signal.signal(signal.SIGTERM, request_stop)
    root=tk.Tk(); App(root); root.mainloop(); return 0

if __name__ == "__main__": raise SystemExit(main())
