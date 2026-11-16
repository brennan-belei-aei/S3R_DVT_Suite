"""
Analog Setpoint Transfer Function - Data Collection
======================================================

Standalone collection script extracted from CollectionBeta.py.

Purpose
-------
Sweep the User Port analog setpoint input, read delivered power from the DUT,
and save raw measurements under a versioned output contract. Final regression,
linearity calculations, and plots intentionally belong in the separate transfer-function
analysis script.

Bench connections
-----------------
Waveform generator CH1 HI -> User Port pin 5  (Setpoint Input)
Waveform generator CH1 LO -> User Port pin 18 (Setpoint Return)
Waveform generator CH2 HI -> User Port pin 4  (RF PWR ON)
Waveform generator CH2 LO -> User Port pin 17 (RF PWR ON Return)
A suitable 50-ohm RF load, cooling, interlocks, and emergency shutdown access
must be available before running the test.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pyvisa

# Keep compatibility with the existing suite layout.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
import suite_common.bootstrap  # noqa: F401,E402
from suite_common.bench import bench_default, visa_socket_resource  # noqa: E402
import AECommandsUniversal  # noqa: E402

# =============================================================================
# TEST IDENTITY AND OUTPUT CONTRACT
# =============================================================================
TEST_ID = "SETPOINT_TRANSFER_FUNCTION"
TEST_NAME = "Setpoint Transfer Function"
CONTRACT_NAME = "Paramount.SetpointTransferFunction"
CONTRACT_VERSION = "1.0.0"

# =============================================================================
# BENCH AND DUT SETTINGS
# =============================================================================
WAVEGEN_ADDRESS = bench_default("waveform_generator")
WAVEGEN_SOCKET_FALLBACK_ADDRESS = visa_socket_resource(WAVEGEN_ADDRESS)
DUT_ADDRESS = bench_default("generator")

OPERATING_FREQUENCY_KHZ = 13_560
RATED_POWER_W = 3_000.0
REGULATION_FORWARD = 6
HOST_MODE = 2
USER_PORT_MODE = 4
HALO_SETPOINT_RESOLUTION = 10  # setPowerSetpoint uses tenths of watts

# =============================================================================
# TRANSFER-FUNCTION TEST SETTINGS
# =============================================================================
TRANSFER_VOLTAGES_V = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 4.0,
                   5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
TRANSFER_SAMPLES_PER_POINT = 10
TRANSFER_DEFAULT_CYCLES = 3
TRANSFER_MAX_CYCLES = 100
TRANSFER_BETWEEN_CYCLES_S = 1.0
TRANSFER_SAMPLE_INTERVAL_S = 0.10
TRANSFER_SETTLE_S = 1.0
TRANSFER_FULL_SCALE_INPUT_V = 10.0
TRANSFER_SETPOINT_WG_CHANNEL = 1
TRANSFER_RF_ENABLE_WG_CHANNEL = 2
RF_ENABLE_LOW_V = 0.0
RF_ENABLE_HIGH_V = 6.0

INSTRUMENT_TIMEOUT_MS = 30_000
COMMAND_SETTLE_S = 0.25
RF_STABILIZE_S = 1.0

# =============================================================================
# OUTPUT FILE NAMES
# =============================================================================
CONFIGURATION_FILENAME = "Run_Configuration.csv"
RAW_SAMPLES_FILENAME = "Raw_Samples.csv"
POINTS_FILENAME = "Collection_Points.csv"
EVENT_LOG_FILENAME = "Event_Log.csv"
CONTRACT_FILENAME = "Output_Contract.json"
RUN_MANIFEST_FILENAME = "Run_Manifest.json"


def _safe_component(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1F]", "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def choose_parent_folder() -> Optional[Path]:
    """Prompt for the parent output folder. Cancellation creates no test folder."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            title="Select parent folder for transfer-function collection results"
        )
        root.destroy()
        return Path(selected) if selected else None
    except Exception as exc:
        print(f"Folder-selection dialog unavailable: {exc}")
        entered = input(
            "Enter a parent output folder, or press Enter to cancel: "
        ).strip().strip('"')
        return Path(entered).expanduser() if entered else None


def create_unique_run_folder(parent: Path, dut_address: str = "") -> Path:
    """Create the established Windows-safe timestamped test folder."""
    parent = parent.expanduser().resolve()
    if not parent.exists() or not parent.is_dir():
        raise ValueError(f"Selected parent folder is not a directory: {parent}")

    generator_name = _safe_component(dut_address or DUT_ADDRESS, "Generator")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_name = _safe_component(
        f"{timestamp} - {TEST_NAME} - {generator_name}",
        f"{timestamp} - Setpoint Transfer Function",
    )
    candidate = parent / base_name
    run_number = 1
    while candidate.exists():
        run_number += 1
        candidate = parent / f"{base_name} - Run {run_number}"
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Rewrite a small run CSV atomically enough for interruption recovery."""
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


@dataclass
class RunContext:
    run_dir: Path
    run_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    configuration_rows: list[dict[str, Any]] = field(default_factory=list)
    raw_sample_rows: list[dict[str, Any]] = field(default_factory=list)
    point_rows: list[dict[str, Any]] = field(default_factory=list)
    event_rows: list[dict[str, Any]] = field(default_factory=list)
    total_cycles: int = 1

    def _stamp(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "Timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "Contract_Name": CONTRACT_NAME,
            "Contract_Version": CONTRACT_VERSION,
            "Test_ID": TEST_ID,
            "Run_ID": self.run_id,
            **row,
        }

    def add_configuration(self, **row: Any) -> None:
        self.configuration_rows.append(self._stamp(row))
        write_csv(self.run_dir / CONFIGURATION_FILENAME, self.configuration_rows)

    def add_raw_sample(self, **row: Any) -> None:
        self.raw_sample_rows.append(self._stamp(row))
        write_csv(self.run_dir / RAW_SAMPLES_FILENAME, self.raw_sample_rows)

    def add_point(self, **row: Any) -> None:
        self.point_rows.append(self._stamp(row))
        write_csv(self.run_dir / POINTS_FILENAME, self.point_rows)

    def add_event(self, severity: str, event: str, **row: Any) -> None:
        self.event_rows.append(self._stamp({"Severity": severity, "Event": event, **row}))
        write_csv(self.run_dir / EVENT_LOG_FILENAME, self.event_rows)

    def write_contract(self) -> None:
        contract = {
            "contract_name": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "test_id": TEST_ID,
            "test_name": TEST_NAME,
            "collection_script": Path(__file__).name,
            "files": {
                CONFIGURATION_FILENAME: {
                    "role": "Run settings and per-point commanded configuration",
                    "required": True,
                },
                RAW_SAMPLES_FILENAME: {
                    "role": "One row per DUT delivered-power reading",
                    "required": True,
                    "primary_key": ["Run_ID", "Cycle", "Point", "Sample"],
                    "units": {
                        "Input_Voltage_V": "V",
                        "Expected_Power_W": "W",
                        "Measured_Delivered_Power_W": "W",
                    },
                },
                POINTS_FILENAME: {
                    "role": "Collection-side completeness and descriptive statistics",
                    "required": True,
                    "primary_key": ["Run_ID", "Cycle", "Point"],
                    "note": "Final regression, error, and linearity metrics belong to analysis.",
                },
                EVENT_LOG_FILENAME: {
                    "role": "Run lifecycle, warnings, and errors",
                    "required": True,
                },
                RUN_MANIFEST_FILENAME: {
                    "role": "Machine-readable run status and file inventory",
                    "required": True,
                },
            },
            "analysis_rules": {
                "expected_power_w": "RATED_POWER_W * Input_Voltage_V / Full_Scale_Input_V",
                "use_raw_samples": True,
                "zero_setpoint_percent_error": "undefined",
                "collection_points_are_not_final_analysis": True,
                "average_hierarchy": "samples within each cycle/point, then corresponding points across cycles",
                "cycles_are_independent_repeats": True,
            },
        }
        (self.run_dir / CONTRACT_FILENAME).write_text(
            json.dumps(contract, indent=2), encoding="utf-8"
        )

    def write_manifest(self, status: str, **extra: Any) -> None:
        manifest = {
            "contract_name": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "test_id": TEST_ID,
            "test_name": TEST_NAME,
            "run_id": self.run_id,
            "status": status,
            "updated": datetime.now().isoformat(timespec="seconds"),
            "run_directory": str(self.run_dir),
            "counts": {
                "configured_cycles": self.total_cycles,
                "configured_points_per_cycle": len(TRANSFER_VOLTAGES_V),
                "expected_completed_points": self.total_cycles * len(TRANSFER_VOLTAGES_V),
                "completed_points": len(self.point_rows),
                "raw_samples": len(self.raw_sample_rows),
                "expected_raw_samples": self.total_cycles * len(TRANSFER_VOLTAGES_V) * TRANSFER_SAMPLES_PER_POINT,
            },
            "files": sorted(path.name for path in self.run_dir.iterdir() if path.is_file()),
            **extra,
        }
        (self.run_dir / RUN_MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )


class TransferFunctionInstruments:
    """Transfer-function-only connection and cleanup layer for the DUT and waveform generator."""

    def __init__(self, dut_address: str, wavegen_address: str) -> None:
        self.dut_address = dut_address.strip()
        self.wavegen_address = wavegen_address.strip()
        self.wavegen_socket_fallback_address = visa_socket_resource(self.wavegen_address)
        self.rm = None
        self.wavegen = None
        self.dut = None

    def connect(self) -> None:
        if not self.wavegen_address:
            raise RuntimeError(
                "Waveform generator address is not configured. Select a bench "
                "profile or set PARAMOUNT_WAVEFORM_GENERATOR_ADDRESS."
            )
        if not self.dut_address:
            raise RuntimeError(
                "DUT generator address is not configured. Select a bench profile "
                "or set PARAMOUNT_GENERATOR_ADDRESS."
            )

        self.rm = pyvisa.ResourceManager()
        errors: list[str] = []
        candidates = []
        for address in (self.wavegen_address, self.wavegen_socket_fallback_address):
            if address and address not in candidates:
                candidates.append(address)

        for address in candidates:
            candidate = None
            try:
                print(f"Opening waveform generator at {address}...")
                candidate = self.rm.open_resource(address)
                candidate.timeout = INSTRUMENT_TIMEOUT_MS
                if str(address).endswith("::SOCKET"):
                    candidate.read_termination = "\n"
                    candidate.write_termination = "\n"
                identity = candidate.query("*IDN?").strip()
                self.wavegen = candidate
                print(f"Waveform generator connected: {identity}")
                break
            except Exception as exc:
                errors.append(f"{address}: {type(exc).__name__}: {exc}")
                try:
                    if candidate is not None:
                        candidate.close()
                except Exception:
                    pass

        if self.wavegen is None:
            raise RuntimeError(
                "Unable to connect to the waveform generator using configured "
                "VISA resources. " + " | ".join(errors)
            )

        print(f"Connecting to DUT at {self.dut_address}...")
        self.dut = AECommandsUniversal.generator(self.dut_address)
        print("DUT connected.")

    def safe_state(self) -> None:
        print("Placing transfer-function equipment in a best-effort safe state...")
        if self.wavegen is not None:
            try:
                set_dc(self.wavegen, TRANSFER_RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, True)
            except Exception as exc:
                print(f"RF Enable cleanup warning: {exc}")
            try:
                set_dc(self.wavegen, TRANSFER_SETPOINT_WG_CHANNEL, 0.0, True)
            except Exception as exc:
                print(f"Setpoint cleanup warning: {exc}")
            for channel in (TRANSFER_SETPOINT_WG_CHANNEL, TRANSFER_RF_ENABLE_WG_CHANNEL):
                try:
                    self.wavegen.write(f"C{channel}:OUTP OFF")
                except Exception as exc:
                    print(f"Wavegen CH{channel} output-off warning: {exc}")

        if self.dut is not None:
            try:
                self.dut.setActiveControlMode(HOST_MODE)
                time.sleep(COMMAND_SETTLE_S)
            except Exception:
                pass
            try:
                self.dut.rfOFF()
            except Exception as exc:
                print(f"DUT RF-off cleanup warning: {exc}")

    def close(self) -> None:
        self.safe_state()
        try:
            if self.wavegen is not None:
                self.wavegen.close()
        except Exception:
            pass
        try:
            if self.dut is not None:
                self.dut.close()
        except Exception:
            pass
        try:
            if self.rm is not None:
                self.rm.close()
        except Exception:
            pass


def set_dc(wavegen: Any, channel: int, voltage: float, output_on: bool) -> None:
    voltage = float(voltage)
    if not math.isfinite(voltage):
        raise ValueError("Waveform-generator DC voltage must be finite.")
    wavegen.write(f"C{channel}:BSWV WVTP,DC")
    wavegen.write(f"C{channel}:BSWV OFST,{voltage:.6f}")
    wavegen.write(f"C{channel}:OUTP {'ON' if output_on else 'OFF'}")


def set_host_configuration(dut: Any) -> None:
    """Apply only settings that are legal and deterministic in Host mode."""
    dut.setActiveControlMode(HOST_MODE)
    time.sleep(COMMAND_SETTLE_S)
    dut.rfOFF()
    dut.setFixedFrequency(int(OPERATING_FREQUENCY_KHZ))
    dut.setRegulationMode(REGULATION_FORWARD)
    dut.setFrequencyMode(0)
    dut.setPulsingFrequency(0)
    dut.setPowerSetpoint(int(round(RATED_POWER_W * HALO_SETPOINT_RESOLUTION)))


def enter_user_port(dut: Any) -> None:
    dut.setActiveControlMode(USER_PORT_MODE)
    time.sleep(COMMAND_SETTLE_S)
    reported = int(dut.reportActiveControlMode())
    if reported != USER_PORT_MODE:
        raise RuntimeError(
            f"Expected User Port control mode {USER_PORT_MODE}, but DUT reported {reported}."
        )


def validate_settings() -> None:
    if RATED_POWER_W <= 0:
        raise ValueError("RATED_POWER_W must be greater than zero.")
    if TRANSFER_FULL_SCALE_INPUT_V <= 0:
        raise ValueError("TRANSFER_FULL_SCALE_INPUT_V must be greater than zero.")
    if TRANSFER_SAMPLES_PER_POINT < 1:
        raise ValueError("TRANSFER_SAMPLES_PER_POINT must be at least 1.")
    if not 1 <= TRANSFER_DEFAULT_CYCLES <= TRANSFER_MAX_CYCLES:
        raise ValueError("TRANSFER_DEFAULT_CYCLES must be within the allowed cycle range.")
    if TRANSFER_SAMPLE_INTERVAL_S < 0 or TRANSFER_SETTLE_S < 0:
        raise ValueError("Transfer-function timing values cannot be negative.")
    if len(TRANSFER_VOLTAGES_V) < 2:
        raise ValueError("The transfer-function test requires at least two voltage points.")
    if len(set(float(v) for v in TRANSFER_VOLTAGES_V)) != len(TRANSFER_VOLTAGES_V):
        raise ValueError("TRANSFER_VOLTAGES_V contains duplicate points.")
    for voltage in TRANSFER_VOLTAGES_V:
        value = float(voltage)
        if not math.isfinite(value) or not 0.0 <= value <= TRANSFER_FULL_SCALE_INPUT_V:
            raise ValueError(
                f"Transfer-function voltage {voltage!r} is outside 0 to {TRANSFER_FULL_SCALE_INPUT_V:g} V."
            )


def require_operator_acknowledgement() -> None:
    print("\n" + "=" * 78)
    print("TRANSFER-FUNCTION PRE-RUN CHECK")
    print(__doc__.split("Bench connections", 1)[1].split("must be available", 1)[0])
    print("Verify DB25 pin numbering from the mating-side view.")
    print("Verify source returns, load, cooling, interlocks, voltage limits,")
    print("and emergency shutdown access before enabling RF.")
    answer = input("Type RUN to continue: ").strip().upper()
    if answer != "RUN":
        raise RuntimeError("Operator cancelled the test.")



def prompt_cycle_count() -> int:
    """Ask how many complete transfer-function sweeps to run, using a safe bounded integer."""
    while True:
        entered = input(
            f"Number of complete cycles [{TRANSFER_DEFAULT_CYCLES}]: "
        ).strip()
        if not entered:
            return TRANSFER_DEFAULT_CYCLES
        try:
            cycles = int(entered)
        except ValueError:
            print("Enter a whole number.")
            continue
        if 1 <= cycles <= TRANSFER_MAX_CYCLES:
            return cycles
        print(f"Enter a value from 1 through {TRANSFER_MAX_CYCLES}.")


def collect_transfer_function(inst: TransferFunctionInstruments, ctx: RunContext, total_cycles: int) -> None:
    ctx.total_cycles = total_cycles
    ctx.add_configuration(
        Configuration="Run",
        DUT_Address=inst.dut_address,
        Wavegen_Address=inst.wavegen_address,
        Operating_Frequency_kHz=OPERATING_FREQUENCY_KHZ,
        Rated_Power_W=RATED_POWER_W,
        Full_Scale_Input_V=TRANSFER_FULL_SCALE_INPUT_V,
        Voltage_Points=str(TRANSFER_VOLTAGES_V),
        Total_Cycles=total_cycles,
        Samples_Per_Point=TRANSFER_SAMPLES_PER_POINT,
        Sample_Interval_s=TRANSFER_SAMPLE_INTERVAL_S,
        Settle_Time_s=TRANSFER_SETTLE_S,
        Between_Cycles_s=TRANSFER_BETWEEN_CYCLES_S,
        Setpoint_Wavegen_Channel=TRANSFER_SETPOINT_WG_CHANNEL,
        RF_Enable_Wavegen_Channel=TRANSFER_RF_ENABLE_WG_CHANNEL,
        RF_Enable_Low_V=RF_ENABLE_LOW_V,
        RF_Enable_High_V=RF_ENABLE_HIGH_V,
    )

    set_dc(inst.wavegen, TRANSFER_SETPOINT_WG_CHANNEL, 0.0, False)
    set_dc(inst.wavegen, TRANSFER_RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, False)
    set_host_configuration(inst.dut)
    enter_user_port(inst.dut)
    set_dc(inst.wavegen, TRANSFER_SETPOINT_WG_CHANNEL, 0.0, True)
    set_dc(inst.wavegen, TRANSFER_RF_ENABLE_WG_CHANNEL, RF_ENABLE_HIGH_V, True)
    time.sleep(RF_STABILIZE_S)

    for cycle in range(1, total_cycles + 1):
        print(f"\n{'=' * 78}\nTRANSFER-FUNCTION CYCLE {cycle}/{total_cycles}\n{'=' * 78}")
        ctx.add_event("INFO", "Cycle started", Cycle=cycle, Total_Cycles=total_cycles)

        for point, voltage in enumerate(TRANSFER_VOLTAGES_V, start=1):
            voltage = float(voltage)
            expected_power = RATED_POWER_W * voltage / TRANSFER_FULL_SCALE_INPUT_V
            capture_id = f"TRANSFER_C{cycle:03d}_P{point:03d}_{voltage:g}V"
            print(
                f"\nCycle {cycle}/{total_cycles}, point {point}/{len(TRANSFER_VOLTAGES_V)}: "
                f"{voltage:.3f} V, expected {expected_power:.3f} W"
            )
            ctx.add_configuration(
                Configuration="Point", Capture_ID=capture_id, Cycle=cycle,
                Total_Cycles=total_cycles, Point=point, Input_Voltage_V=voltage,
                Expected_Power_W=expected_power,
            )

            set_dc(inst.wavegen, TRANSFER_SETPOINT_WG_CHANNEL, voltage, True)
            time.sleep(TRANSFER_SETTLE_S)

            samples: list[float] = []
            for sample in range(1, TRANSFER_SAMPLES_PER_POINT + 1):
                sample_time = datetime.now().isoformat(timespec="milliseconds")
                measured_power = float(inst.dut.reportDeliveredPower())
                if not math.isfinite(measured_power):
                    raise RuntimeError(
                        f"Non-finite reading at cycle {cycle}, point {point}, sample {sample}."
                    )
                samples.append(measured_power)
                ctx.add_raw_sample(
                    Capture_ID=capture_id, Cycle=cycle, Total_Cycles=total_cycles,
                    Point=point, Sample=sample, Sample_Timestamp=sample_time,
                    Input_Voltage_V=voltage, Expected_Power_W=expected_power,
                    Measured_Delivered_Power_W=measured_power, Valid=True,
                )
                print(f"  Sample {sample}/{TRANSFER_SAMPLES_PER_POINT}: {measured_power:.3f} W")
                if sample < TRANSFER_SAMPLES_PER_POINT:
                    time.sleep(TRANSFER_SAMPLE_INTERVAL_S)

            mean_power = sum(samples) / len(samples)
            ctx.add_point(
                Capture_ID=capture_id, Cycle=cycle, Total_Cycles=total_cycles,
                Point=point, Input_Voltage_V=voltage, Expected_Power_W=expected_power,
                Sample_Count=len(samples), Collection_Mean_W=mean_power,
                Collection_Minimum_W=min(samples), Collection_Maximum_W=max(samples),
                Collection_Complete=len(samples) == TRANSFER_SAMPLES_PER_POINT,
            )
            print(
                f"  Collected mean {mean_power:.3f} W "
                f"(min {min(samples):.3f}, max {max(samples):.3f})"
            )
            ctx.write_manifest(
                "running", current_cycle=cycle, total_cycles=total_cycles,
                current_point=point,
            )

        ctx.add_event("INFO", "Cycle completed", Cycle=cycle, Total_Cycles=total_cycles)
        # Return to zero between complete sweeps so each repeat starts identically.
        set_dc(inst.wavegen, TRANSFER_SETPOINT_WG_CHANNEL, 0.0, True)
        if cycle < total_cycles:
            time.sleep(TRANSFER_BETWEEN_CYCLES_S)

    set_dc(inst.wavegen, TRANSFER_RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, True)
    set_dc(inst.wavegen, TRANSFER_SETPOINT_WG_CHANNEL, 0.0, True)


def run_collection(total_cycles: int, parent: Path, dut_address: str, wavegen_address: str) -> tuple[int, Optional[Path]]:
    """Run the transfer-function test after the GUI has validated settings and received confirmation."""
    run_dir = create_unique_run_folder(parent, dut_address)
    ctx = RunContext(run_dir, total_cycles=total_cycles)
    ctx.write_contract()
    ctx.add_event("INFO", "Run folder created")
    ctx.add_event("INFO", "Operator pre-run acknowledgement received")
    ctx.write_manifest("initialized")
    print(f"Results directory: {run_dir}")

    inst = TransferFunctionInstruments(dut_address, wavegen_address)
    status = "aborted"
    exit_code = 1
    try:
        inst.connect()
        ctx.add_event("INFO", "Instrument connection completed")
        collect_transfer_function(inst, ctx, total_cycles)
        status = "completed"
        exit_code = 0
        ctx.add_event("INFO", "transfer-function collection completed")
        print("\ntransfer-function collection completed successfully.")
    except KeyboardInterrupt:
        status = "interrupted"
        exit_code = 130
        ctx.add_event("WARNING", "Operator interrupted the test")
    except Exception as exc:
        ctx.add_event("ERROR", "transfer-function collection aborted",
                      Error_Type=type(exc).__name__, Error=str(exc))
        print(f"\nTRANSFER-FUNCTION COLLECTION ABORTED: {exc}")
        traceback.print_exc()
    finally:
        inst.close()
        try:
            ctx.add_event("INFO", "Best-effort equipment cleanup completed")
            ctx.write_manifest(status)
        except Exception as exc:
            print(f"Final log/manifest warning: {exc}")
    return exit_code, run_dir


class TransferFunctionCollectionGUI:
    """Independent child GUI compatible with the Paramount unified launcher."""

    def __init__(self, root: Any) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.exit_code = 2
        self.running = False
        self.result_folder: Optional[Path] = None
        self.cycle_var = tk.StringVar(value=str(TRANSFER_DEFAULT_CYCLES))
        self.dut_address_var = tk.StringVar(value=DUT_ADDRESS or "")
        self.wavegen_address_var = tk.StringVar(value=WAVEGEN_ADDRESS or "")
        default_parent = os.environ.get("PARAMOUNT_COLLECTION_PARENT_FOLDER", "").strip()
        self.parent_var = tk.StringVar(value=default_parent or str(Path.home()))
        self.status_var = tk.StringVar(value="Ready")
        self.progress_text = tk.StringVar(value="Configure the run, then select Start Collection.")
        self._build()

    def _build(self) -> None:
        from tkinter import ttk
        self.root.title("Setpoint Transfer Function Collection")
        self.root.geometry("860x720")
        self.root.minsize(760, 620)
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Setpoint Transfer Function",
                  font=("Segoe UI", 16, "bold")).pack(anchor="w")
        launcher = os.environ.get("PARAMOUNT_UNIFIED_LAUNCHER") == "1"
        ttk.Label(
            outer,
            text=("Launched by Paramount Unified Data Collection. " if launcher else "")
                 + "Configure complete voltage-sweep cycles and collect raw delivered-power samples.",
            wraplength=780,
        ).pack(anchor="w", pady=(2, 12))

        settings = ttk.LabelFrame(outer, text="Run Settings", padding=12)
        settings.pack(fill="x")
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="Complete cycles").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Spinbox(settings, from_=1, to=TRANSFER_MAX_CYCLES, textvariable=self.cycle_var,
                    width=10).grid(row=0, column=1, sticky="w", padx=8, pady=5)
        ttk.Label(settings, text="Collection parent folder").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(settings, textvariable=self.parent_var).grid(row=1, column=1, sticky="ew", padx=8, pady=5)
        self.browse_button = ttk.Button(settings, text="Browse...", command=self._browse)
        self.browse_button.grid(row=1, column=2, pady=5)
        ttk.Label(settings, text=f"Voltage points: {len(TRANSFER_VOLTAGES_V)}    Samples per point: {TRANSFER_SAMPLES_PER_POINT}").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Label(settings, text="DUT IP / hostname").grid(row=3, column=0, sticky="w", pady=5)
        self.dut_address_entry = ttk.Entry(settings, textvariable=self.dut_address_var)
        self.dut_address_entry.grid(row=3, column=1, columnspan=2, sticky="ew", padx=8, pady=5)
        ttk.Label(settings, text="Waveform generator VISA address / IP").grid(row=4, column=0, sticky="w", pady=5)
        self.wavegen_address_entry = ttk.Entry(settings, textvariable=self.wavegen_address_var)
        self.wavegen_address_entry.grid(row=4, column=1, columnspan=2, sticky="ew", padx=8, pady=5)
        ttk.Label(settings, text="Defaults are inherited from the active unified collection bench profile; edit for this run if needed.",
                  foreground="#555555", wraplength=720).grid(row=5, column=0, columnspan=3, sticky="w", pady=(3, 0))

        action = ttk.Frame(outer, padding=(0, 12, 0, 0))
        action.pack(fill="x")
        self.start_button = ttk.Button(
            action,
            text="START TRANSFER FUNCTION COLLECTION",
            command=self._start,
        )
        self.start_button.pack(fill="x", ipady=10)
        ttk.Label(
            action,
            text="This starts the complete 0 to 10 V transfer-function sweep for the selected number of cycles.",
            foreground="#555555",
        ).pack(anchor="center", pady=(5, 0))

        safety = ttk.LabelFrame(outer, text="Required Bench Check", padding=12)
        safety.pack(fill="x", pady=12)
        ttk.Label(safety, justify="left", wraplength=760, text=(
            "Waveform generator CH1: User Port setpoint input.\n"
            "Waveform generator CH2: User Port RF Enable.\n"
            "Verify DB25 pin numbering, returns, RF load, cooling, interlocks, voltage limits, "
            "and emergency shutdown access before starting."
        )).pack(anchor="w")

        progress = ttk.LabelFrame(outer, text="Run Status", padding=10)
        progress.pack(fill="both", expand=True)
        ttk.Label(progress, textvariable=self.progress_text, wraplength=750).pack(anchor="w")
        self.progress = ttk.Progressbar(progress, mode="indeterminate")
        self.progress.pack(fill="x", pady=10)
        self.log = self.tk.Text(progress, height=10, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Label(footer, textvariable=self.status_var).pack(side="left")
        self.close_button = ttk.Button(footer, text="Close", command=self._close)
        self.close_button.pack(side="right", padx=8)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _browse(self) -> None:
        from tkinter import filedialog
        selected = filedialog.askdirectory(parent=self.root, title="Select transfer-function collection parent folder",
            initialdir=self.parent_var.get().strip() or str(Path.home()), mustexist=True)
        if selected:
            self.parent_var.set(selected)

    def _append(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self) -> None:
        from tkinter import messagebox
        try:
            cycles = int(self.cycle_var.get().strip())
            if not 1 <= cycles <= TRANSFER_MAX_CYCLES:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid cycles", f"Enter a whole number from 1 through {TRANSFER_MAX_CYCLES}.", parent=self.root)
            return
        parent = Path(self.parent_var.get().strip()).expanduser()
        if not parent.is_dir():
            messagebox.showerror("Invalid folder", "Select an existing collection parent folder.", parent=self.root)
            return
        dut_address = self.dut_address_var.get().strip()
        wavegen_address = self.wavegen_address_var.get().strip()
        if not dut_address or not wavegen_address:
            messagebox.showerror("Missing bench address",
                "Enter both the DUT IP/hostname and waveform-generator VISA address/IP.", parent=self.root)
            return
        expected = cycles * len(TRANSFER_VOLTAGES_V) * TRANSFER_SAMPLES_PER_POINT
        if not messagebox.askyesno("Start transfer-function collection",
            f"Run {cycles} complete cycle(s), producing {expected} raw readings?\n\n"
            "RF will be enabled through the User Port during collection.", parent=self.root):
            return
        self.running = True
        self.start_button.configure(state="disabled")
        self.browse_button.configure(state="disabled")
        self.dut_address_entry.configure(state="disabled")
        self.wavegen_address_entry.configure(state="disabled")
        self.close_button.configure(state="disabled")
        self.progress.start(10)
        self.status_var.set("Running")
        self.progress_text.set(f"Collecting {cycles} complete transfer-function cycle(s). Do not disconnect equipment.")
        self._append(f"Starting transfer-function collection with {cycles} cycle(s).")

        import threading
        def worker() -> None:
            code, folder = run_collection(cycles, parent, dut_address, wavegen_address)
            self.root.after(0, lambda: self._finished(code, folder))
        threading.Thread(target=worker, daemon=True).start()

    def _finished(self, code: int, folder: Optional[Path]) -> None:
        from tkinter import messagebox
        self.running = False
        self.exit_code = code
        self.result_folder = folder
        self.progress.stop()
        self.start_button.configure(state="normal")
        self.browse_button.configure(state="normal")
        self.dut_address_entry.configure(state="normal")
        self.wavegen_address_entry.configure(state="normal")
        self.close_button.configure(state="normal")
        if code == 0:
            self.progress["value"] = 100
            self.status_var.set("Completed successfully")
            self.progress_text.set(f"Completed. Results: {folder}")
            self._append(f"Completed successfully. Results: {folder}")
            messagebox.showinfo("Transfer-function collection complete", f"transfer-function collection completed successfully.\n\n{folder}", parent=self.root)
        else:
            self.status_var.set(f"Stopped with exit code {code}")
            self.progress_text.set("Collection did not complete. Review Event_Log.csv and the child console.")
            self._append(f"Collection stopped with exit code {code}.")
            messagebox.showerror("Transfer-function collection did not complete",
                f"Collection stopped with exit code {code}.\n\nPartial results: {folder or 'None'}", parent=self.root)

    def _close(self) -> None:
        from tkinter import messagebox
        if self.running:
            messagebox.showwarning("Collection running",
                "The collection is still running. Use the equipment emergency shutdown if required; "
                "the GUI will close only after collection cleanup completes.", parent=self.root)
            return
        self.root.destroy()


def main() -> int:
    validate_settings()
    import tkinter as tk
    root = tk.Tk()
    app = TransferFunctionCollectionGUI(root)
    root.mainloop()
    return app.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
