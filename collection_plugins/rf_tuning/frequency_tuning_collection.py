"""RF Tuning data collection GUI for the Paramount unified test suite.

Preserves the validated coworker workflow:
- Six directed transitions among 12.882, 13.560, and 14.238 MHz.
- Navigator match positions selected from the target frequency.
- Verified Navigator movement with retries and position tolerance.
- Keysight 53220A buffered frequency logging (time,frequency_hz).
- RF ON in fixed mode, fixed dwell, live switch to tuning mode, tuning dwell.
- Live generator-fault polling and retry of unable-to-tune attempts only.
- Safe RF OFF and instrument cleanup on completion, cancellation, or error.

The script runs standalone or as a child of suite_apps/collection_gui.py.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "RF Tuning Data Collection"
APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.config import plugin_settings_path  # noqa: E402
import suite_common.bootstrap  # noqa: E402,F401  (AE command modules on sys.path)
SETTINGS_PATH = plugin_settings_path("rf_tuning", "collection")

# Unified-launcher environment values. Multiple counter names are accepted so the
# script remains compatible if the bench-profile field is named differently.
ENV_GENERATOR = ("PARAMOUNT_GENERATOR_ADDRESS", "PARAMOUNT_GENERATOR_IP")
ENV_NAVIGATOR = ("PARAMOUNT_NAVIGATOR_ADDRESS", "PARAMOUNT_NAVIGATOR_PORT")
ENV_COUNTER = (
    "PARAMOUNT_FREQUENCY_COUNTER_ADDRESS",
    "PARAMOUNT_COUNTER_ADDRESS",
    "PARAMOUNT_FREQUENCY_COUNTER_RESOURCE",
)
ENV_PARENT = "PARAMOUNT_COLLECTION_PARENT_FOLDER"
ENV_BENCH_PROFILE = "PARAMOUNT_BENCH_PROFILE"

DEFAULT_GENERATOR = "RFGN07C807"
DEFAULT_NAVIGATOR = "COM11"
DEFAULT_COUNTER = "172.24.242.12"
DEFAULT_SAMPLES = 10
DEFAULT_MAX_RETRIES = 10

FIXED_MODE = 0
TUNING_MODE = 1
FIXED_DWELL_SECONDS = 1.0
TUNING_DWELL_SECONDS = 1.0
INTER_SAMPLE_DELAY_SECONDS = 1.0
COUNTER_ARM_LEAD_SECONDS = 0.05
NAVIGATOR_COMMAND_DWELL_SECONDS = 0.020
NAVIGATOR_POLL_SECONDS = 0.050
NAVIGATOR_MOVE_TIMEOUT_SECONDS = 15.0
NAVIGATOR_SETTLE_SECONDS = 1.0
NAVIGATOR_POSITION_TOLERANCE = 5
NAVIGATOR_MOVE_RETRIES = 2
BLOCK_SIZE = 10
GATE_TIME_SECONDS = 0.001
INPUT_IMPEDANCE_OHMS = 50
INPUT_COUPLING = "AC"
AUTO_TRIGGER_LEVEL = False
MANUAL_TRIGGER_LEVEL_VOLTS = 0.0
NO_SIGNAL_TIMEOUT_SECONDS = 0.50
COUNTER_OPEN_TIMEOUT_MS = 3000
COUNTER_CONNECT_ATTEMPTS = 3
FAULT_CLEAR_TIMEOUT_SECONDS = 5.0
FAULT_CLEAR_POLL_SECONDS = 0.100
FAULT_POLL_INTERVAL_SECONDS = 0.050
UNABLE_TO_TUNE_PHRASES = ("unable to tune", "unable-to-tune")

MIN_FREQUENCY_KHZ = 12_882
CENTER_FREQUENCY_KHZ = 13_560
MAX_FREQUENCY_KHZ = 14_238
FREQUENCY_LABELS = {
    MIN_FREQUENCY_KHZ: "min",
    CENTER_FREQUENCY_KHZ: "center",
    MAX_FREQUENCY_KHZ: "max",
}
MATCH_BY_TARGET_KHZ = {
    MIN_FREQUENCY_KHZ: (5795, 6277),
    CENTER_FREQUENCY_KHZ: (5183, 5629),
    MAX_FREQUENCY_KHZ: (4657, 5070),
}
CONDITION_PAIRS = (
    (CENTER_FREQUENCY_KHZ, MIN_FREQUENCY_KHZ),
    (MAX_FREQUENCY_KHZ, MIN_FREQUENCY_KHZ),
    (MIN_FREQUENCY_KHZ, CENTER_FREQUENCY_KHZ),
    (MAX_FREQUENCY_KHZ, CENTER_FREQUENCY_KHZ),
    (MIN_FREQUENCY_KHZ, MAX_FREQUENCY_KHZ),
    (CENTER_FREQUENCY_KHZ, MAX_FREQUENCY_KHZ),
)
STOP = threading.Event()


def env_first(names: Iterable[str], default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def normalize_counter_ip(value: str) -> str:
    """Accept a plain IPv4 address, including conversion of saved legacy VISA strings."""
    text = value.strip()
    legacy = re.fullmatch(r"TCPIP(?:0)?::([^:]+)::inst0::INSTR", text, flags=re.IGNORECASE)
    return legacy.group(1) if legacy else text


def counter_visa_resource(ip_address: str) -> str:
    ip = normalize_counter_ip(ip_address)
    octets = ip.split(".")
    if len(octets) != 4:
        raise ValueError("Keysight 53220A IP address must contain four numbers, for example 172.24.242.12.")
    try:
        values = [int(octet) for octet in octets]
    except ValueError as exc:
        raise ValueError("Keysight 53220A IP address must contain numbers only.") from exc
    if any(value < 0 or value > 255 for value in values) or any(str(value) != octet for value, octet in zip(values, octets)):
        raise ValueError("Enter a valid Keysight 53220A IPv4 address, for example 172.24.242.12.")
    return f"TCPIP0::{ip}::inst0::INSTR"


def safe_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", str(value)).strip().rstrip(".")
    return cleaned or "Unknown"


def create_run_folder(parent: Path, generator_name: str) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    base = f"{stamp} - RF Tuning - {safe_component(generator_name)}"
    candidate = parent / base
    run_number = 2
    while candidate.exists():
        candidate = parent / f"{base} - Run {run_number}"
        run_number += 1
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


@dataclass(frozen=True)
class TestCondition:
    start_frequency_khz: int
    target_frequency_khz: int
    load_position: int
    tune_position: int
    sample_count: int

    @property
    def start_label(self) -> str:
        return FREQUENCY_LABELS[self.start_frequency_khz]

    @property
    def target_label(self) -> str:
        return FREQUENCY_LABELS[self.target_frequency_khz]

    @property
    def direction_name(self) -> str:
        return f"{self.start_label}_to_{self.target_label}"


def make_condition(start_khz: int, target_khz: int, sample_count: int) -> TestCondition:
    if start_khz == target_khz:
        raise ValueError("Start and target frequencies must differ.")
    if start_khz not in FREQUENCY_LABELS or target_khz not in MATCH_BY_TARGET_KHZ:
        raise ValueError("Unsupported RF tuning frequency.")
    if sample_count < 1:
        raise ValueError("Samples per transition must be at least 1.")
    load, tune = MATCH_BY_TARGET_KHZ[target_khz]
    return TestCondition(start_khz, target_khz, load, tune, sample_count)


def request_stop(*_args) -> None:
    if not STOP.is_set():
        print("Stop requested; shutting down safely...", flush=True)
    STOP.set()


def import_counter_driver():
    try:
        module = importlib.import_module("keysight_53220a_fast")
    except ImportError as exc:
        raise RuntimeError(
            "Could not import keysight_53220a_fast.py. Place it beside this script "
            "or on the Python import path."
        ) from exc
    return module.Counter53220A, module.NoSignalError


def create_generator(address: str):
    """Prefer the suite universal dependency, with legacy fallback for transition."""
    attempts = []
    candidates = (
        ("AECommandsUniversal", ("generator", "Generator")),
        ("AE_Commands_7434753", ("generator", "Generator")),
    )
    for module_name, class_names in candidates:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            attempts.append(f"{module_name}: {exc}")
            continue
        for class_name in class_names:
            factory = getattr(module, class_name, None)
            if factory is None:
                continue
            try:
                return factory(address)
            except Exception as exc:
                attempts.append(f"{module_name}.{class_name}: {exc}")
    raise RuntimeError("Unable to create generator connection. " + " | ".join(attempts))


def create_navigator(port: str):
    attempts = []
    candidates = (
        ("AECommandsUniversal", ("navigator", "Navigator")),
        ("AECommands", ("navigator", "Navigator")),
    )
    for module_name, class_names in candidates:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            attempts.append(f"{module_name}: {exc}")
            continue
        for class_name in class_names:
            factory = getattr(module, class_name, None)
            if factory is None:
                continue
            try:
                return factory(port)
            except Exception as exc:
                attempts.append(f"{module_name}.{class_name}: {exc}")
    raise RuntimeError("Unable to create Navigator connection. " + " | ".join(attempts))


def safe_rf_off(generator) -> None:
    if generator is None:
        return
    try:
        generator.rfOFF()
    except Exception as exc:
        print(f"Warning: RF OFF failed: {exc}", flush=True)


def close_device(device, label: str) -> None:
    if device is None:
        return
    try:
        closer = getattr(device, "close", None)
        if callable(closer):
            closer()
    except Exception as exc:
        print(f"Warning: {label} close failed: {exc}", flush=True)


def command_dwell() -> None:
    STOP.wait(NAVIGATOR_COMMAND_DWELL_SECONDS)


def motors_are_moving(navigator) -> bool:
    response = navigator.nav.create_buffer_cmd(135, "0:0", "1:8")
    command_dwell()
    if not isinstance(response, (list, tuple)) or len(response) < 3:
        raise RuntimeError(f"Unexpected Navigator movement response: {response!r}")
    return bool(int(response[2]))


def wait_for_motors(navigator) -> None:
    deadline = time.monotonic() + NAVIGATOR_MOVE_TIMEOUT_SECONDS
    while not STOP.is_set():
        if not motors_are_moving(navigator):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Navigator motors did not stop within {NAVIGATOR_MOVE_TIMEOUT_SECONDS:.1f} seconds")
        STOP.wait(NAVIGATOR_POLL_SECONDS)
    raise InterruptedError("Stop requested while Navigator motors were moving")


def integer_values(value: Any) -> list[int]:
    if isinstance(value, dict):
        return [number for item in value.values() for number in integer_values(item)]
    if isinstance(value, (list, tuple)):
        return [number for item in value for number in integer_values(item)]
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, float) and value.is_integer():
        return [int(value)]
    return [int(match) for match in re.findall(r"-?\d+", str(value))]


def read_cap_positions(navigator) -> tuple[int, int]:
    response = navigator.reportCapPositions()
    command_dwell()
    values = integer_values(response)
    if len(values) < 2:
        raise RuntimeError(f"Could not parse Navigator capacitor positions: {response!r}")
    return values[-2], values[-1]


def move_and_verify_navigator(navigator, load: int, tune: int) -> tuple[int, int]:
    target = (load, tune)
    last_actual = None
    for attempt in range(1, NAVIGATOR_MOVE_RETRIES + 2):
        if STOP.is_set():
            raise InterruptedError("Stop requested before Navigator movement")
        print(f"Moving Navigator to load={load}, tune={tune} (attempt {attempt})...", flush=True)
        navigator.moveLoadMotor(load)
        command_dwell()
        navigator.moveTuneMotor(tune)
        command_dwell()
        wait_for_motors(navigator)
        if STOP.wait(NAVIGATOR_SETTLE_SECONDS):
            raise InterruptedError("Stop requested during Navigator settle")
        last_actual = read_cap_positions(navigator)
        print(f"Navigator reported load={last_actual[0]}, tune={last_actual[1]}", flush=True)
        if abs(last_actual[0] - load) <= NAVIGATOR_POSITION_TOLERANCE and abs(last_actual[1] - tune) <= NAVIGATOR_POSITION_TOLERANCE:
            return last_actual
    raise RuntimeError(f"Navigator failed to reach target {target}; last reported {last_actual}")


def normalize_fault_codes(response) -> list[int]:
    if response is None:
        return []
    if isinstance(response, str):
        if "no active fault" in response.lower():
            return []
        raise RuntimeError(f"Unexpected generator fault response: {response!r}")
    if isinstance(response, int) and not isinstance(response, bool):
        return [] if response == 0 else [response]
    if isinstance(response, Iterable):
        codes = []
        for item in response:
            try:
                code = int(item)
            except (TypeError, ValueError):
                continue
            if code:
                codes.append(code)
        return codes
    raise RuntimeError(f"Unexpected generator fault response: {response!r}")


def active_faults(generator) -> list[tuple[int, str]]:
    codes = normalize_fault_codes(generator.reportFaultOrWarningCodeList(1))
    faults = []
    for code in codes:
        try:
            description = str(generator.reportSystemControl(92, 1, code)).strip()
        except Exception as exc:
            description = f"Description lookup failed: {exc}"
        faults.append((code, description))
    return faults


def classify_faults(faults: list[tuple[int, str]]):
    retryable, other = [], []
    for fault in faults:
        normalized = " ".join(fault[1].lower().replace("_", " ").split())
        (retryable if any(p in normalized for p in UNABLE_TO_TUNE_PHRASES) else other).append(fault)
    return retryable, other


def format_faults(faults: list[tuple[int, str]]) -> str:
    return "; ".join(f"{code}: {description}" for code, description in faults)


def wait_for_faults_to_clear(generator) -> None:
    safe_rf_off(generator)
    deadline = time.monotonic() + FAULT_CLEAR_TIMEOUT_SECONDS
    while not STOP.is_set():
        faults = active_faults(generator)
        if not faults:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Generator fault did not clear after RF OFF: " + format_faults(faults))
        STOP.wait(FAULT_CLEAR_POLL_SECONDS)
    raise InterruptedError("Stop requested while waiting for generator fault to clear")


def verify_no_preexisting_faults(generator) -> None:
    faults = active_faults(generator)
    if not faults:
        return
    retryable, other = classify_faults(faults)
    if other:
        raise RuntimeError("Unrelated active fault before sample: " + format_faults(other))
    print("Stale unable-to-tune fault; waiting for RF OFF reset: " + format_faults(retryable), flush=True)
    wait_for_faults_to_clear(generator)


def mhz_filename(frequency_khz: int) -> str:
    return f"{frequency_khz / 1000:.3f}".replace(".", "p")


def condition_directory(root: Path, condition: TestCondition) -> Path:
    return root / f"target_{mhz_filename(condition.target_frequency_khz)}MHz" / condition.direction_name


def csv_path_for(root: Path, condition: TestCondition, sample: int, attempt: int, actual: tuple[int, int]) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    name = (
        f"sample_{sample:04d}_attempt_{attempt:02d}_"
        f"{mhz_filename(condition.start_frequency_khz)}_to_{mhz_filename(condition.target_frequency_khz)}MHz_"
        f"match_L{actual[0]}_T{actual[1]}_{stamp}.csv"
    )
    return condition_directory(root, condition) / name


def logger_worker(counter, no_signal_error, csv_path: Path, sample_stop: threading.Event,
                  ready: threading.Event, result: dict) -> None:
    rows = 0
    try:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time", "frequency_hz"])
            handle.flush()
            ready.set()
            while not STOP.is_set() and not sample_stop.is_set():
                timeout = max(NO_SIGNAL_TIMEOUT_SECONDS, BLOCK_SIZE * GATE_TIME_SECONDS * 2.5 + 0.25)
                try:
                    block = counter.acquire_block(BLOCK_SIZE, timeout)
                except no_signal_error:
                    continue
                completed = datetime.now(timezone.utc)
                first = completed - timedelta(seconds=(len(block.values_hz) - 1) * GATE_TIME_SECONDS)
                for index, frequency_hz in enumerate(block.values_hz):
                    when = first + timedelta(seconds=index * GATE_TIME_SECONDS)
                    writer.writerow([when.astimezone().isoformat(timespec="milliseconds"), f"{frequency_hz:.12g}"])
                rows += len(block.values_hz)
                handle.flush()
    except BaseException as exc:
        result["error"] = exc
        STOP.set()
    finally:
        result["rows"] = rows
        ready.set()


def run_attempt(root: Path, condition: TestCondition, sample: int, attempt: int,
                generator, counter, no_signal_error, actual_positions: tuple[int, int]):
    folder = condition_directory(root, condition)
    folder.mkdir(parents=True, exist_ok=True)
    csv_path = csv_path_for(root, condition, sample, attempt, actual_positions)
    sample_stop = threading.Event()
    ready = threading.Event()
    result = {}
    logger = threading.Thread(
        target=logger_worker,
        args=(counter, no_signal_error, csv_path, sample_stop, ready, result),
        name=f"counter-{condition.direction_name}-{sample}-{attempt}", daemon=True,
    )
    detected_faults = []
    safe_rf_off(generator)
    generator.setFrequencyMode(FIXED_MODE)
    generator.setFixedFrequency(condition.start_frequency_khz)
    generator.setTuningStartFrequency(condition.start_frequency_khz)
    logger.start()
    if not ready.wait(COUNTER_OPEN_TIMEOUT_MS / 1000.0):
        raise RuntimeError("Counter logger did not become ready")
    try:
        if STOP.wait(COUNTER_ARM_LEAD_SECONDS):
            return csv_path, detected_faults, result.get("rows", 0)
        generator.rfON()
        print(f"RF ON at fixed {condition.start_frequency_khz / 1000:.3f} MHz", flush=True)
        if STOP.wait(FIXED_DWELL_SECONDS):
            return csv_path, detected_faults, result.get("rows", 0)
        generator.setFrequencyMode(TUNING_MODE)
        deadline = time.monotonic() + TUNING_DWELL_SECONDS
        while not STOP.is_set() and time.monotonic() < deadline:
            faults = active_faults(generator)
            if faults:
                detected_faults = faults
                break
            STOP.wait(min(FAULT_POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
    finally:
        if detected_faults or STOP.is_set():
            safe_rf_off(generator)
        sample_stop.set()
        logger.join(NO_SIGNAL_TIMEOUT_SECONDS + 2.0)
        if logger.is_alive():
            counter.recover()
            logger.join(2.0)
        safe_rf_off(generator)
    if "error" in result:
        raise RuntimeError(f"Counter logger failed: {result['error']}") from result["error"]
    post_faults = active_faults(generator)
    known = {code for code, _ in detected_faults}
    detected_faults.extend(fault for fault in post_faults if fault[0] not in known)
    return csv_path, detected_faults, result.get("rows", 0)


def append_attempt_log(path: Path, row: dict) -> None:
    fields = [
        "timestamp", "condition", "start_frequency_khz", "target_frequency_khz",
        "logical_sample", "attempt", "status", "csv_path", "rows",
        "requested_load", "requested_tune", "actual_load", "actual_tune",
        "fault_codes", "fault_descriptions", "use_in_analysis", "exclusion_reason",
    ]
    exists = path.is_file()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def run_logical_sample(root: Path, condition: TestCondition, sample: int, generator,
                       counter, no_signal_error, actual: tuple[int, int], max_retries: int) -> Path:
    attempts = max_retries + 1
    log_path = root / "attempt_log.csv"
    for attempt in range(1, attempts + 1):
        if STOP.is_set():
            raise InterruptedError("Stop requested before sample attempt")
        verify_no_preexisting_faults(generator)
        print(f"{condition.direction_name}, logical sample {sample}/{condition.sample_count}, attempt {attempt}/{attempts}", flush=True)
        csv_path, faults, rows = run_attempt(root, condition, sample, attempt, generator, counter, no_signal_error, actual)
        if STOP.is_set():
            raise InterruptedError("Stop requested during sample attempt")
        retryable, other = classify_faults(faults)
        status = "Passed" if not faults else "Failed"
        reason = "" if not faults else ("Unable to tune fault" if retryable and not other else "Non-retryable generator fault")
        append_attempt_log(log_path, {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "condition": condition.direction_name,
            "start_frequency_khz": condition.start_frequency_khz,
            "target_frequency_khz": condition.target_frequency_khz,
            "logical_sample": sample, "attempt": attempt, "status": status,
            "csv_path": str(csv_path.relative_to(root)), "rows": rows,
            "requested_load": condition.load_position, "requested_tune": condition.tune_position,
            "actual_load": actual[0], "actual_tune": actual[1],
            "fault_codes": ";".join(str(code) for code, _ in faults),
            "fault_descriptions": format_faults(faults),
            "use_in_analysis": not faults, "exclusion_reason": reason,
        })
        if not faults:
            return csv_path
        failed_dir = root / "Failed Attempts" / condition.direction_name
        failed_dir.mkdir(parents=True, exist_ok=True)
        retained_path = failed_dir / csv_path.name
        csv_path.replace(retained_path)
        if other:
            raise RuntimeError(f"Sample {sample} produced non-retryable fault(s): {format_faults(other)}")
        if not retryable:
            raise RuntimeError(f"Sample {sample} produced unclassified fault(s): {format_faults(faults)}")
        wait_for_faults_to_clear(generator)
        if attempt == attempts:
            raise RuntimeError(f"Sample {sample} failed with unable-to-tune after {attempts} attempts")
    raise AssertionError("Unreachable retry loop")


@dataclass
class RunSettings:
    generator: str
    navigator: str
    counter: str
    parent_folder: str
    samples_per_transition: int
    max_retries: int
    selected_pairs: list[tuple[int, int]]
    bench_profile: str = ""


def write_manifest(root: Path, settings: RunSettings, conditions: list[TestCondition], status: str,
                   completed_samples: int, error: str = "") -> None:
    payload = {
        "schema_version": 1,
        "test_id": "rf_tuning",
        "test_name": "RF Tuning",
        "status": status,
        "updated_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "run_folder": str(root),
        "settings": {
            **asdict(settings),
            "selected_pairs": [list(pair) for pair in settings.selected_pairs],
            "fixed_dwell_seconds": FIXED_DWELL_SECONDS,
            "tuning_dwell_seconds": TUNING_DWELL_SECONDS,
            "counter_gate_time_seconds": GATE_TIME_SECONDS,
            "counter_block_size": BLOCK_SIZE,
            "navigator_position_tolerance": NAVIGATOR_POSITION_TOLERANCE,
        },
        "conditions": [asdict(condition) | {"direction_name": condition.direction_name} for condition in conditions],
        "completed_samples": completed_samples,
        "total_samples": sum(condition.sample_count for condition in conditions),
        "error": error,
    }
    (root / "run_manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def execute_collection(settings: RunSettings, status_callback=lambda _text: None) -> Path:
    STOP.clear()
    parent = Path(settings.parent_folder).expanduser().resolve()
    if not parent.is_dir():
        raise ValueError(f"Collection parent folder does not exist: {parent}")
    conditions = [make_condition(a, b, settings.samples_per_transition) for a, b in settings.selected_pairs]
    if not conditions:
        raise ValueError("Select at least one frequency transition.")

    root = create_run_folder(parent, settings.generator)
    write_manifest(root, settings, conditions, "starting", 0)
    generator = navigator = None
    completed = 0
    Counter53220A, NoSignalError = import_counter_driver()
    try:
        status_callback("Connecting to generator...")
        generator = create_generator(settings.generator)
        safe_rf_off(generator)
        status_callback("Connecting to Navigator...")
        navigator = create_navigator(settings.navigator)
        status_callback("Connecting to Keysight 53220A...")
        counter_resource = counter_visa_resource(settings.counter)
        with Counter53220A(counter_resource, open_timeout_ms=COUNTER_OPEN_TIMEOUT_MS,
                           connect_attempts=COUNTER_CONNECT_ATTEMPTS) as counter:
            counter.configure_frequency(
                GATE_TIME_SECONDS, CENTER_FREQUENCY_KHZ * 1000,
                INPUT_IMPEDANCE_OHMS, INPUT_COUPLING,
                AUTO_TRIGGER_LEVEL, MANUAL_TRIGGER_LEVEL_VOLTS,
            )
            print(f"Counter: {counter.identify()}", flush=True)
            print(f"Output root: {root}", flush=True)
            write_manifest(root, settings, conditions, "running", completed)
            for index, condition in enumerate(conditions, 1):
                if STOP.is_set():
                    break
                status_callback(f"Condition {index}/{len(conditions)}: {condition.direction_name}")
                safe_rf_off(generator)
                actual = move_and_verify_navigator(navigator, condition.load_position, condition.tune_position)
                for sample in range(1, condition.sample_count + 1):
                    if STOP.is_set():
                        break
                    status_callback(f"{condition.direction_name}: sample {sample}/{condition.sample_count}")
                    run_logical_sample(root, condition, sample, generator, counter, NoSignalError,
                                       actual, settings.max_retries)
                    completed += 1
                    write_manifest(root, settings, conditions, "running", completed)
                    if sample < condition.sample_count and not STOP.is_set():
                        STOP.wait(INTER_SAMPLE_DELAY_SECONDS)
        final_status = "cancelled" if STOP.is_set() else "completed"
        write_manifest(root, settings, conditions, final_status, completed)
        return root
    except BaseException as exc:
        write_manifest(root, settings, conditions, "failed", completed, str(exc))
        raise
    finally:
        safe_rf_off(generator)
        close_device(navigator, "Navigator")
        close_device(generator, "generator")


def load_saved_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


class CollectionApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.worker: threading.Thread | None = None
        saved = load_saved_settings()
        inherited_parent = os.environ.get(ENV_PARENT, "").strip()
        self.generator_var = tk.StringVar(value=env_first(ENV_GENERATOR, saved.get("generator", DEFAULT_GENERATOR)))
        self.navigator_var = tk.StringVar(value=env_first(ENV_NAVIGATOR, saved.get("navigator", DEFAULT_NAVIGATOR)))
        self.counter_var = tk.StringVar(value=normalize_counter_ip(env_first(ENV_COUNTER, saved.get("counter", DEFAULT_COUNTER))))
        self.parent_var = tk.StringVar(value=inherited_parent or saved.get("parent_folder", str(Path.home())))
        self.samples_var = tk.StringVar(value=str(saved.get("samples_per_transition", DEFAULT_SAMPLES)))
        self.retries_var = tk.StringVar(value=str(saved.get("max_retries", DEFAULT_MAX_RETRIES)))
        selected = {tuple(pair) for pair in saved.get("selected_pairs", CONDITION_PAIRS)}
        self.condition_vars = {pair: tk.BooleanVar(value=pair in selected) for pair in CONDITION_PAIRS}
        self.status_var = tk.StringVar(value="Ready")
        self._build()

    def _build(self):
        self.root.title(APP_TITLE)
        self.root.geometry("850x720")
        self.root.minsize(760, 650)
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=APP_TITLE, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Validated fixed-to-tuning frequency collection with Navigator matching and Keysight 53220A logging.", wraplength=790).pack(anchor="w", pady=(2, 12))

        connection = ttk.LabelFrame(frame, text="Bench connections", padding=10)
        connection.pack(fill="x")
        connection.columnconfigure(1, weight=1)
        rows = [
            ("Generator address / hostname", self.generator_var),
            ("Navigator COM port", self.navigator_var),
            ("Keysight 53220A IP address", self.counter_var),
            ("Collection parent folder", self.parent_var),
        ]
        for row, (label, variable) in enumerate(rows):
            ttk.Label(connection, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(connection, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8, pady=5)
        ttk.Button(connection, text="Browse...", command=self._browse_parent).grid(row=3, column=2, pady=5)

        options = ttk.LabelFrame(frame, text="Test settings", padding=10)
        options.pack(fill="x", pady=10)
        ttk.Label(options, text="Successful samples per transition").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(options, textvariable=self.samples_var, width=10).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(options, text="Maximum unable-to-tune retries").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(options, textvariable=self.retries_var, width=10).grid(row=1, column=1, sticky="w", padx=8)

        conditions = ttk.LabelFrame(frame, text="Frequency transitions", padding=10)
        conditions.pack(fill="both", expand=True)
        for row, pair in enumerate(CONDITION_PAIRS):
            start, target = pair
            load, tune = MATCH_BY_TARGET_KHZ[target]
            label = f"{start/1000:.3f} MHz -> {target/1000:.3f} MHz   (Navigator L{load} / T{tune})"
            ttk.Checkbutton(conditions, text=label, variable=self.condition_vars[pair]).grid(row=row, column=0, sticky="w", pady=4)

        ttk.Label(frame, textvariable=self.status_var, wraplength=790).pack(anchor="w", pady=(10, 6))
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        self.stop_button = ttk.Button(buttons, text="Stop Safely", command=request_stop, state="disabled")
        self.stop_button.pack(side="left")
        self.start_button = ttk.Button(buttons, text="Start Collection", command=self._start)
        self.start_button.pack(side="right")
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _browse_parent(self):
        selected = filedialog.askdirectory(parent=self.root, title="Select RF Tuning output parent folder",
                                           initialdir=self.parent_var.get() or str(Path.home()))
        if selected:
            self.parent_var.set(selected)

    def _settings(self) -> RunSettings:
        try:
            samples = int(self.samples_var.get())
            retries = int(self.retries_var.get())
        except ValueError as exc:
            raise ValueError("Samples and retries must be whole numbers.") from exc
        if samples < 1 or retries < 0:
            raise ValueError("Samples must be at least 1 and retries cannot be negative.")
        generator = self.generator_var.get().strip()
        navigator = self.navigator_var.get().strip()
        counter = self.counter_var.get().strip()
        parent = self.parent_var.get().strip()
        if not all((generator, navigator, counter, parent)):
            raise ValueError("Enter all bench addresses and an output parent folder.")
        counter_visa_resource(counter)
        pairs = [pair for pair, variable in self.condition_vars.items() if variable.get()]
        if not pairs:
            raise ValueError("Select at least one frequency transition.")
        return RunSettings(generator, navigator, counter, parent, samples, retries, pairs,
                           os.environ.get(ENV_BENCH_PROFILE, ""))

    def _start(self):
        try:
            settings = self._settings()
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self.root)
            return
        SETTINGS_PATH.write_text(json.dumps({**asdict(settings), "selected_pairs": [list(p) for p in settings.selected_pairs]}, indent=2), encoding="utf-8")
        STOP.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.start(10)
        self.status_var.set("Starting RF Tuning collection...")
        self.worker = threading.Thread(target=self._worker, args=(settings,), daemon=True)
        self.worker.start()

    def _worker(self, settings: RunSettings):
        try:
            output = execute_collection(settings, lambda text: self.root.after(0, self.status_var.set, text))
            self.root.after(0, self._finish, True, f"Collection finished.\n\n{output}")
        except InterruptedError:
            self.root.after(0, self._finish, True, "Collection stopped safely by the user.")
        except BaseException as exc:
            traceback.print_exc()
            self.root.after(0, self._finish, False, str(exc))

    def _finish(self, success: bool, detail: str):
        self.progress.stop()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_var.set(detail)
        if success:
            messagebox.showinfo("RF Tuning collection", detail, parent=self.root)
        else:
            messagebox.showerror("RF Tuning collection failed", detail, parent=self.root)

    def _close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Collection is running", "Request a safe stop and close after the collection thread exits?", parent=self.root):
                return
            request_stop()
            self.status_var.set("Stop requested. Waiting for RF OFF and cleanup...")
            self.root.after(250, self._wait_to_close)
        else:
            self.root.destroy()

    def _wait_to_close(self):
        if self.worker and self.worker.is_alive():
            self.root.after(250, self._wait_to_close)
        else:
            self.root.destroy()


def self_test() -> int:
    assert len(CONDITION_PAIRS) == 6
    for start, target in CONDITION_PAIRS:
        condition = make_condition(start, target, 10)
        assert (condition.load_position, condition.tune_position) == MATCH_BY_TARGET_KHZ[target]
    assert safe_component('A:B/C') == 'A_B_C'
    assert normalize_fault_codes([0, 53, "101"]) == [53, 101]
    assert normalize_counter_ip("TCPIP0::172.24.242.12::inst0::INSTR") == "172.24.242.12"
    assert counter_visa_resource("172.24.242.12") == "TCPIP0::172.24.242.12::inst0::INSTR"
    retryable, other = classify_faults([(1, "Unable to tune")])
    assert retryable and not other
    print("RF Tuning collection self-test passed.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    root = tk.Tk()
    CollectionApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
