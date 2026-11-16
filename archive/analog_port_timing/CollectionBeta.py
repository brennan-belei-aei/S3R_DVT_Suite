"""
Paramount User Analog Interface Transparency Test Automation - CH2 50 Ohm and Noise Reject
===============================================================================================

Implements test-plan objectives T1 through T9 using:
  * AE_Commands_PMNT_HF_1XX.generator for DUT Ethernet/AEBus access
  * PyVISA SCPI control of the oscilloscope and two-channel waveform generator

IMPORTANT
---------
1. This script is a first hardware-integration version. Review USER SETTINGS,
   wiring, signal polarities, voltage limits, and scope scaling before enabling RF.
2. The oscilloscope and waveform-generator SCPI dialects follow the verified
   examples in CWDataCollection.py (Tektronix-like scope, Siglent-like wavegen).
3. T1 uses a fixed analog setpoint on wavegen Channel A while sweeping RF
   Enable on Channel B. The threshold state is firmware process-status d0 bit 6
   (RF On Requested); RF Output On is retained as a slower functional cross-check.
   T1 waits for RF On Requested to release at 0 V before each new cycle.
   T1 intentionally does not call the snapshot wrapper in User Port mode.
4. T2/T3 enter User Port once, verify the default Forward mode, and use the
   active regulation-mode field returned by
   reportGeneratorSnapshotData(). T2 detects mode 8 (External/DC Bias), and T3
   detects mode 7 (Delivered/Real, called Load regulation in the test plan).
5. T5-T9 save only the scope channels configured as physically connected.
   Automatic timing extraction is intentionally not guessed in this acquisition
   script; use the saved waveforms with explicit,
   reviewed threshold definitions.
5. Run one test at a time from the menu. Every test creates a timestamped local
   results directory with short Windows-safe names. Configuration, measurement
   results, and events/errors are written to separate CSV files to avoid sparse
   rows, misleading blank cells, and long-path failures in nested OneDrive folders.

Generated for engineering evaluation. Not hardware-validated by the author.
"""

from __future__ import annotations

import csv
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

import pyvisa
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default, visa_socket_resource

import AE_Commands_PMNT_HF_1XX


# =============================================================================
# USER SETTINGS
# =============================================================================

SCOPE_ADDRESS = bench_default("oscilloscope")
WAVEGEN_ADDRESS = bench_default("waveform_generator")
WAVEGEN_SOCKET_FALLBACK_ADDRESS = visa_socket_resource(WAVEGEN_ADDRESS)

# T7-only BK Precision 9141 LAN control.
BK9141_ADDRESS = bench_default("dc_supply")
BK9141_SOCKET_FALLBACK_ADDRESS = visa_socket_resource(BK9141_ADDRESS)
BK9141_CHANNEL = 1
BK9141_INTERLOCK_VOLTAGE_V = 25.0
BK9141_CURRENT_LIMIT_A = 0.500
BK9141_OUTPUT_SETTLE_S = 0.25
BK9141_VERIFY_TOLERANCE_V = 1.0
BK9141_VOLTAGE_READY_TIMEOUT_S = 5.0
BK9141_VOLTAGE_POLL_INTERVAL_S = 0.05
BK9141_CURRENT_LIMIT_MARGIN_A = 0.010

DUT_ADDRESS = bench_default("generator")

OUTPUT_ROOT = Path(__file__).resolve().parent / "Results"
SCOPE_DRIVE = os.environ.get("PARAMOUNT_SCOPE_DRIVE", "E:/")

# DUT defaults
OPERATING_FREQUENCY_KHZ = 13560
# RATED_POWER_W = 6000.0
RATED_POWER_W = 3000.0
REGULATION_FORWARD = 6
REGULATION_DELIVERED = 7           # Delivered/Real, called Load regulation in the test plan
REGULATION_EXTERNAL = 8            # External/DC Bias
REGULATION_VA_LIMIT = 9
DEFAULT_REGULATION_MODE = REGULATION_FORWARD
GENERATOR_SNAPSHOT_REGULATION_MODE_INDEX = 10  # returned dataMatrix index, raw protocol byte is d28/res[12]
HOST_MODE = 2
USER_PORT_MODE = 4
HALO_SETPOINT_RESOLUTION = 10     # AEBus setPowerSetpoint uses tenths of watts
WARMUP_SECONDS = 15 * 60

# General timing
INSTRUMENT_TIMEOUT_MS = 30000
SCOPE_SAVE_TIMEOUT_MS = 180000
SCOPE_SAVE_SETTLE_S = 0.50
COMMAND_SETTLE_S = 0.25
STATE_SETTLE_S = 0.10
RF_STABILIZE_S = 1.0
SCOPE_ARM_S = 0.35
ACQUISITION_TIMEOUT_S = 10.0
RECOVERY_S = 1.0

# Wavegen routing. Both channels are available and independently configurable.
WG_CH_A = 1
WG_CH_B = 2

# Analog interface voltages
RF_ENABLE_LOW_V = 0.0
RF_ENABLE_HIGH_V = 6.0
THRESHOLD_START_V = 0.0
THRESHOLD_STOP_V = 5.0
THRESHOLD_STEP_V = 0.010
THRESHOLD_CYCLES = 10

# Assignment for slow threshold tests
# T1 requires both wavegen channels in User Port mode. Channel A supplies a
# fixed analog power setpoint while Channel B sweeps the RF Enable input.
T1_POWER_W = 1000.0
T1_SETPOINT_FULL_SCALE_V = 10.0
T1_SETPOINT_WG_CHANNEL = WG_CH_A
T1_RF_ENABLE_WG_CHANNEL = WG_CH_B
T2_WG_CHANNEL = WG_CH_A
T3_WG_CHANNEL = WG_CH_A

# T2/T3 use active regulation mode from reportGeneratorSnapshotData().
# Firmware command 222 places regulation mode at raw response byte d28. The AE
# dependency repackages that response into dataMatrix, where regulation mode is
# returned at index 10.
REGULATION_STATE_CONFIRM_READS = 2
T1_RF_REQUEST_CONFIRM_READS = 2
T1_ENDPOINT_STATE_TIMEOUT_S = 2.0
T1_LOW_ENDPOINT_RELEASE_TIMEOUT_S = 2.0
THRESHOLD_PROGRESS_INTERVAL_V = 0.10
PRINT_RECORDED_ROWS = True

# T4 setpoint transfer function
T4_VOLTAGES_V = [0.0, 0.1, 0.2, 0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
T4_SAMPLES_PER_POINT = 10
T4_SAMPLE_INTERVAL_S = 0.10
T4_SETTLE_S = 1.0
T4_SETPOINT_WG_CHANNEL = WG_CH_A
T4_RF_ENABLE_WG_CHANNEL = WG_CH_B
T4_FULL_SCALE_INPUT_V = 10.0
T4_TRANSFER_PLOT_FILENAME = "T4_Transfer_Function.png"
T4_ERROR_PLOT_FILENAME = "T4_Transfer_Function_Error.png"
T4_SUMMARY_FILENAME = "T4_Summary.csv"
T4_PLOT_DPI = 150

# Scope defaults. Change scales/offsets for the actual probes and monitor outputs.
SCOPE_RECORD_LENGTH = 100_000
SCOPE_DATA_START = 1
SCOPE_HORIZONTAL_POSITION_PERCENT = 35.0
SCOPE_DEFAULT_SCALE_V_DIV = {1: 2.0, 2: 2.0, 3: 2.0, 4: 2.0}
SCOPE_DEFAULT_TERMINATION_OHM = {1: 1_000_000, 2: 1_000_000, 3: 1_000_000, 4: 1_000_000}

# Per-test horizontal scales, seconds per division.
T5_SCOPE_TIME_PER_DIV_S = 1e-3
T6_SCOPE_TIME_PER_DIV_S = 1e-3
T7_SCOPE_TIME_PER_DIV_S = 20e-3
T8_SCOPE_TIME_PER_DIV_S = 200e-3
T9_SCOPE_TIME_PER_DIV_S = 1e-3

# T5-only scope settings.
T5_INITIAL_STATE_DWELL_S = 2.0
T5_TRIGGER_MODE = "NORM"
T5_SCOPE_ARM_TIMEOUT_S = 5.0
T5_SCOPE_ARMED_STATES = ("READY", "ARMED", "TRIGGER")
# T5 CH1 dynamic vertical scaling.
T5_CH1_STANDARD_SCALE_V_DIV = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
T5_CH1_TARGET_SPAN_DIV = 4.0
T5_CH1_VERTICAL_PADDING_FACTOR = 1.10
T5_CH1_MIN_SCALE_V_DIV = 0.5
T5_CH1_MAX_SCALE_V_DIV = 2.0
T5_CH1_POSITION_DIV = 0.0
T5_CH1_OFFSET_SIGN = 1.0

# T5 vertical scaling mode is selected by the operator after choosing T5.
# True preserves the existing per-transition dynamic scale and offset behavior.
T5_USE_DYNAMIC_VERTICAL_SCALING = True

# Used only when fixed T5 vertical scaling is selected. These two dictionaries
# control scale and offset for both connected T5 scope channels.
T5_FIXED_VERTICAL_SCALE_V_DIV = {1: 2.0, 2: 1.0}
T5_FIXED_VERTICAL_OFFSET_V = {1: 4.5, 2: 0.0}

# T5/T6 CH2 uses the scope's internal 50-ohm input and dynamic scaling.
T5_T6_CH2_TERMINATION_OHM = 50.0
T5_T6_TRIGGER_COUPLING = "NOISEREJ"
T5_T6_CH2_EXPECTED_VPP_AT_RATED_POWER = 4.0
T5_T6_CH2_STANDARD_SCALE_V_DIV = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
T5_T6_CH2_TARGET_SPAN_DIV = 4.0
T5_T6_CH2_VERTICAL_PADDING_FACTOR = 1.25
T5_T6_CH2_MIN_SCALE_V_DIV = 0.1
T5_T6_CH2_MAX_SCALE_V_DIV = 2.0
T5_T6_CH2_POSITION_DIV = 0.0
T5_T6_CH2_OFFSET_V = 0.0
T5_RF_OFF_TIMEOUT_S = 5.0

# T7 T6-style setpoint/RF Enable control with BK interlock stimulus.
# These settings intentionally appear before TIMING_CONFIG because that mapping
# references the trigger constants during module import.
T7_SETPOINT_FULL_SCALE_V = 10.0
T7_SETPOINT_WG_CHANNEL = WG_CH_A
T7_RF_ENABLE_WG_CHANNEL = WG_CH_B
T7_SETUP_SETTLE_S = 1.0
T7_RF_ON_SETTLE_S = 1.0
T7_RF_OFF_VERIFY_TIMEOUT_S = 2.0
T7_RF_OFF_TO_INTERLOCK_HIGH_DELAY_S = 1.0
T7_RF_OFF_CONFIRM_READS = 3
T7_WAVEGEN_LOAD = "HZ"
T7_SCOPE_TRIGGER_LEVEL_V = 15.0
T7_SCOPE_TRIGGER_SLOPE = "FALL"

# T5-only Wavegen CH1 square-wave manual burst. For Siglent SDG instruments,
# 180 degrees starts a rising capture at the low level and 0 degrees starts a
# falling capture at the high level. Swap these values if bench observation
# shows that the specific model uses opposite square-wave phase polarity.
T5_SQUARE_FREQUENCY_HZ = 100.0
T5_SQUARE_DUTY_PERCENT = 50.0
T5_SQUARE_RISE_START_PHASE_DEG = 180.0
T5_SQUARE_FALL_START_PHASE_DEG = 0.0
T5_BURST_CYCLES = 1
T5_BURST_CONFIGURATION_SETTLE_S = 0.50

TIMING_CONFIG = {
    "T5_RISE": dict(trigger_ch=1, trigger_level=2.0, slope="RISE", trigger_coupling=T5_T6_TRIGGER_COUPLING, trigger_mode=T5_TRIGGER_MODE,
                    time_per_div_s=T5_SCOPE_TIME_PER_DIV_S, save_channels=(1, 2),
                    require_arm_handshake=True, labels={1:"Analog_Setpoint", 2:"RF_Waveform"}),
    "T5_FALL": dict(trigger_ch=1, trigger_level=2.0, slope="FALL", trigger_coupling=T5_T6_TRIGGER_COUPLING, trigger_mode=T5_TRIGGER_MODE,
                    time_per_div_s=T5_SCOPE_TIME_PER_DIV_S, save_channels=(1, 2),
                    require_arm_handshake=True, labels={1:"Analog_Setpoint", 2:"RF_Waveform"}),
    "T6_ON": dict(trigger_ch=1, trigger_level=2.5, slope="RISE", trigger_coupling=T5_T6_TRIGGER_COUPLING, time_per_div_s=T6_SCOPE_TIME_PER_DIV_S,
                  save_channels=(1,2,3), labels={1:"RF_Enable",2:"RF_Monitor",3:"RF_Detector"}),
    "T6_OFF": dict(trigger_ch=1, trigger_level=2.5, slope="FALL", trigger_coupling=T5_T6_TRIGGER_COUPLING, time_per_div_s=T6_SCOPE_TIME_PER_DIV_S,
                   save_channels=(1,2,3), labels={1:"RF_Enable",2:"RF_Monitor",3:"RF_Detector"}),
    "T7": dict(
        trigger_ch=1,
        trigger_level=T7_SCOPE_TRIGGER_LEVEL_V,
        slope=T7_SCOPE_TRIGGER_SLOPE,
        trigger_coupling=T5_T6_TRIGGER_COUPLING,
        time_per_div_s=T7_SCOPE_TIME_PER_DIV_S,
        save_channels=(1, 2),
        channel_scale_v_div={1:5.0, 2:1.0},
        channel_position_div={1:0.0, 2:0.0},
        channel_offset_v={1:12.5, 2:0.0},
        channel_termination_ohm={1:1_000_000, 2:50.0},
        labels={1:"Interlock_Falling_Edge",2:"RF_Envelope_Falling_Edge"},
    ),
    "T8": dict(
        trigger_ch=1,
        trigger_level=0.25,
        slope="RISE",
        time_per_div_s=T8_SCOPE_TIME_PER_DIV_S,
        save_channels=(1, 2, 3),
        channel_scale_v_div={1: 0.2, 2: 1.0, 3: 1.0},
        channel_position_div={1: 0.0, 2: 0.0, 3: 0.0},
        channel_offset_v={1: 0.0, 2: 0.0, 3: 2.5},
        channel_termination_ohm={1: 1_000_000, 2: 50, 3: 1_000_000},
        labels={
            1:"CEX_13p56MHz_Reference",
            2:"RF_Waveform_Monitor",
            3:"CEX_LOCK_Active_Low_Pullup",
        },
    ),
    "T9": dict(trigger_ch=1, trigger_level=1.0, slope="FALL", time_per_div_s=T9_SCOPE_TIME_PER_DIV_S,
               save_channels=(1,2,3), labels={1:"Reference_Removed",2:"CEX_LOCK",3:"RF_Phase_Monitor"}),
}

# T5 analog-setpoint timing.
T5_STEPS_V = [(1.0, 3.0), (3.0, 5.0), (5.0, 8.0), (8.0, 5.0), (5.0, 3.0), (3.0, 1.0)]
T5_CYCLES = 10
T5_SETPOINT_WG_CHANNEL = WG_CH_A
T5_RF_ENABLE_WG_CHANNEL = WG_CH_B

# T6 RF Enable timing. Wavegen CH1 supplies the fixed analog power setpoint;
# Wavegen CH2 toggles RF Enable.
# T6_POWER_LEVELS_W = [500.0, 1500.0, 3000.0]
T6_POWER_LEVELS_W = [4000.0, 5000.0, 6000.0]
T6_SETPOINT_FULL_SCALE_V = 10.0
T6_CYCLES = 10
T6_SETPOINT_WG_CHANNEL = WG_CH_A
T6_RF_ENABLE_WG_CHANNEL = WG_CH_B
T6_SETUP_SETTLE_S = 1.0

# T7 power list and repetitions.
T7_POWER_LEVELS_W = [500.0, 1500.0, 3000.0]
T7_CYCLES = 10

# T8/T9 external reference. Verify amplitude/offset and electrical interface.
CEX_REFERENCE_WG_CHANNEL = WG_CH_A
CEX_REFERENCE_FREQ_HZ = 13_560_000.0
CEX_REFERENCE_VPP = 2.0
CEX_REFERENCE_OFFSET_V = 0.0
CEX_CYCLES = 10
CEX_UNLOCK_DWELL_S = 1.0
CEX_LOCK_DWELL_S = 1.0

# T8-specific routing and acquisition settings.
# The operator supplies the analog setpoint with an external DC supply.
# Wavegen CH1 cycles RF Enable and Wavegen CH2 applies the CEX reference.
T8_RF_ENABLE_WG_CHANNEL = WG_CH_A
T8_CEX_REFERENCE_WG_CHANNEL = WG_CH_B
T8_POWER_LEVELS_W = [500.0, 1500.0, 3000.0]
# T8_POWER_LEVELS_W = [4000.0, 5000.0, 6000.0]
T8_ACQUISITIONS_PER_POWER = 10
T8_SETPOINT_FULL_SCALE_V = 10.0
T8_RF_OFF_SETTLE_S = 1.0
T8_RF_ON_SETTLE_S = 1.0
T8_REQUIRE_CONFIRMED_UNLOCK = True
T8_REQUIRE_CONFIRMED_LOCK = True
T8_T1_DEFINITION = "Scope CH1 rolling-RMS activity rising 90% crossing"
T8_T2_DEFINITION = "Scope CH3 falling 90% level, first 10% of drop toward 0 V"


# =============================================================================
# SUPPORT TYPES
# =============================================================================

@dataclass
class RunContext:
    """Manage separate configuration, result, and event CSV files for one run."""

    test_id: str
    run_dir: Path = field(init=False)
    configuration_rows: list[dict] = field(default_factory=list)
    result_rows: list[dict] = field(default_factory=list)
    event_rows: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_test_id = self.test_id.split("_", 1)[0]
        self.run_dir = OUTPUT_ROOT / f"{short_test_id}_{stamp}"
        self.run_dir.mkdir(parents=True, exist_ok=False)
        print(f"Results directory: {self.run_dir}")
        print(f"Results path length: {len(str(self.run_dir))} characters")

    def add_configuration(self, **row) -> None:
        self._add("Run_Configuration", self.configuration_rows, **row)

    def add_result(self, **row) -> None:
        self._add("Cycle_Results", self.result_rows, **row)

    def add_event(self, severity: str = "INFO", **row) -> None:
        self._add("Event_Log", self.event_rows, Severity=severity, **row)

    def add(self, **row) -> None:
        """Backward-compatible alias. New measurements default to Cycle Results."""
        self.add_result(**row)

    def _add(self, file_stem: str, rows: list[dict], **row) -> None:
        row = {"Timestamp": datetime.now().isoformat(timespec="milliseconds"), **row}
        rows.append(row)
        path = self._write_csv(file_stem, rows)
        if PRINT_RECORDED_ROWS:
            print(f"\n[RECORDED: {file_stem}]")
            for key, value in row.items():
                print(f"  {key}: {value}")
            print(f"  Local CSV: {path}")

    def _write_csv(self, file_stem: str, rows: list[dict]) -> Path:
        # Keep filenames short because the project may reside inside a deeply
        # nested OneDrive folder on Windows, where long paths can fail even when
        # the parent directory was created successfully.
        path = self.run_dir / f"{file_stem}.csv"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        fields = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return path


class Instruments:
    def __init__(self, connect_bk9141: bool = False) -> None:
        self.rm = None
        self.scope = None
        self.wavegen = None
        self.bk9141 = None
        self.connect_bk9141 = bool(connect_bk9141)
        self.dut = None

    def connect(self) -> None:
        if not SCOPE_ADDRESS:
            raise RuntimeError("Oscilloscope address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_OSCILLOSCOPE_ADDRESS.")
        if not WAVEGEN_ADDRESS:
            raise RuntimeError("Waveform generator address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_WAVEFORM_GENERATOR_ADDRESS.")
        if self.connect_bk9141 and not BK9141_ADDRESS:
            raise RuntimeError("DC supply (BK 9141) address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_DC_SUPPLY_ADDRESS.")
        if not DUT_ADDRESS:
            raise RuntimeError("DUT generator address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_GENERATOR_ADDRESS.")

        print("Connecting to VISA instruments...")
        self.rm = pyvisa.ResourceManager()
        print(f"VISA backend: {self.rm.visalib}")
        try:
            resources = self.rm.list_resources()
            print(f"VISA resources found: {resources}")
        except Exception as exc:
            print(f"VISA resource-list warning: {exc}")

        self.scope = self.rm.open_resource(SCOPE_ADDRESS)
        self.scope.timeout = INSTRUMENT_TIMEOUT_MS
        print("Scope:", self.scope.query("*IDN?").strip())

        wavegen_errors = []
        for address in (WAVEGEN_ADDRESS, WAVEGEN_SOCKET_FALLBACK_ADDRESS):
            try:
                print(f"Opening wavegen at {address}...")
                candidate = self.rm.open_resource(address)
                candidate.timeout = INSTRUMENT_TIMEOUT_MS
                if address.endswith("::SOCKET"):
                    candidate.read_termination = "\n"
                    candidate.write_termination = "\n"
                identity = candidate.query("*IDN?").strip()
                self.wavegen = candidate
                print(f"Wavegen connected using {address}: {identity}")
                break
            except Exception as exc:
                wavegen_errors.append(f"{address}: {type(exc).__name__}: {exc}")
                print(f"Wavegen connection attempt failed: {wavegen_errors[-1]}")
                try:
                    candidate.close()
                except Exception:
                    pass

        if self.wavegen is None:
            raise RuntimeError(
                "Unable to connect to the wavegen with either VXI-11 INSTR or "
                "raw TCP socket port 5025. " + " | ".join(wavegen_errors)
            )

        if self.connect_bk9141:
            errors = []
            for address in (BK9141_ADDRESS, BK9141_SOCKET_FALLBACK_ADDRESS):
                candidate = None
                try:
                    print(f"Opening BK Precision 9141 at {address}...")
                    candidate = self.rm.open_resource(address)
                    candidate.timeout = INSTRUMENT_TIMEOUT_MS
                    if address.endswith("::SOCKET"):
                        candidate.read_termination = "\n"
                        candidate.write_termination = "\n"
                    identity = candidate.query("*IDN?").strip()
                    self.bk9141 = candidate
                    print(f"BK supply connected using {address}: {identity}")
                    break
                except Exception as exc:
                    errors.append(f"{address}: {type(exc).__name__}: {exc}")
                    try:
                        if candidate is not None:
                            candidate.close()
                    except Exception:
                        pass
            if self.bk9141 is None:
                raise RuntimeError("Could not connect to BK 9141. " + " | ".join(errors))
            configure_bk9141_interlock_supply(self.bk9141)

        print("Connecting to DUT...")
        self.dut = AE_Commands_PMNT_HF_1XX.generator(DUT_ADDRESS)
        print("DUT connected.")

    def safe_state(self) -> None:
        print("Placing equipment in a best-effort safe state...")
        if self.wavegen is not None:
            for ch in (1, 2):
                try:
                    self.wavegen.write(f"C{ch}:BSWV WVTP,DC")
                    self.wavegen.write(f"C{ch}:BSWV OFST,0")
                    self.wavegen.write(f"C{ch}:OUTP OFF")
                except Exception as exc:
                    print(f"Wavegen C{ch} cleanup warning: {exc}")
        if self.bk9141 is not None:
            try:
                bk9141_output(self.bk9141, False, verify_voltage=False)
            except Exception as exc:
                print(f"BK 9141 cleanup warning: {exc}")
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
        if self.scope is not None:
            try:
                self.scope.write("ACQ:STATE OFF")
            except Exception:
                pass

    def close(self) -> None:
        self.safe_state()
        for inst in (self.scope, self.wavegen, self.bk9141):
            try:
                if inst is not None:
                    inst.close()
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


# =============================================================================
# COMMON HELPERS
# =============================================================================

def bk9141_select_channel(supply) -> None:
    channel_index = int(BK9141_CHANNEL) - 1
    if channel_index not in (0, 1, 2):
        raise ValueError("BK9141_CHANNEL must be 1, 2, or 3.")
    supply.write(f"INST {channel_index}")


def configure_bk9141_interlock_supply(supply) -> None:
    bk9141_select_channel(supply)
    supply.write("*CLS")
    supply.write("OUTP:STAT OFF")
    supply.write(f"APPL {BK9141_INTERLOCK_VOLTAGE_V:.6f},{BK9141_CURRENT_LIMIT_A:.6f}")
    supply.query("*OPC?")
    print(f"BK 9141 programmed: {supply.query('APPL?').strip()}")


def bk9141_output(supply, enabled: bool, verify_voltage: bool = True) -> dict:
    """Switch the BK output and optionally wait until its voltage is ready.

    The scope is already armed before this function is used as the T7 stimulus.
    Therefore, polling after OUTP ON does not delay the electrical stimulus or
    its scope trigger. It only prevents a premature software abort while the BK
    measured-voltage reading catches up with the programmed 25 V output.
    """
    bk9141_select_channel(supply)
    command_time = time.monotonic()
    supply.write(f"OUTP:STAT {'ON' if enabled else 'OFF'}")
    supply.query("*OPC?")
    time.sleep(BK9141_OUTPUT_SETTLE_S)

    state = supply.query("OUTP:STAT?").strip().upper()
    expected_states = {"1", "ON"} if enabled else {"0", "OFF"}
    if state not in expected_states:
        raise RuntimeError(f"BK output state verification failed: {state!r}")

    result = {
        "Enabled": bool(enabled),
        "Output_State": state,
        "Measured_Voltage_V": None,
        "Measured_Current_A": None,
        "Ready_Time_s": time.monotonic() - command_time,
    }
    if not enabled or not verify_voltage:
        return result

    deadline = time.monotonic() + BK9141_VOLTAGE_READY_TIMEOUT_S
    last_voltage = None
    last_current = None
    sample_number = 0
    while True:
        sample_number += 1
        last_voltage = float(supply.query("MEAS:VOLT?").strip())
        last_current = float(supply.query("MEAS:CURR?").strip())
        elapsed = time.monotonic() - command_time
        print(
            f"  BK readiness sample {sample_number}: "
            f"{last_voltage:.3f} V, {last_current:.6f} A, "
            f"elapsed {elapsed:.3f} s"
        )

        result.update(
            Measured_Voltage_V=last_voltage,
            Measured_Current_A=last_current,
            Ready_Time_s=elapsed,
        )
        if abs(last_voltage - BK9141_INTERLOCK_VOLTAGE_V) <= BK9141_VERIFY_TOLERANCE_V:
            print(
                f"  BK output reached the valid voltage range in {elapsed:.3f} s."
            )
            return result

        if time.monotonic() >= deadline:
            near_current_limit = (
                last_current >= max(
                    0.0,
                    BK9141_CURRENT_LIMIT_A - BK9141_CURRENT_LIMIT_MARGIN_A,
                )
            )
            diagnostic = (
                "Measured current is near the programmed current limit; "
                "check for CC operation or excessive loading."
                if near_current_limit
                else "Measured current is below the programmed current limit; "
                "check BK voltage slew/delay settings and wiring."
            )
            raise RuntimeError(
                f"BK output did not reach {BK9141_INTERLOCK_VOLTAGE_V:.3f} V "
                f"+/- {BK9141_VERIFY_TOLERANCE_V:.3f} V within "
                f"{BK9141_VOLTAGE_READY_TIMEOUT_S:.3f} s. Last measurement: "
                f"{last_voltage:.3f} V, {last_current:.6f} A. {diagnostic}"
            )

        time.sleep(BK9141_VOLTAGE_POLL_INTERVAL_S)


def configure_t7_wavegen_channel(wavegen, channel: int, voltage: float, output_on: bool) -> None:
    """Configure one T7 wavegen channel as a high-impedance-referenced DC output."""
    wavegen.write(f"C{channel}:OUTP OFF")
    wavegen.write(f"C{channel}:OUTP LOAD,{T7_WAVEGEN_LOAD}")
    wavegen.write(f"C{channel}:BSWV WVTP,DC")
    wavegen.write(f"C{channel}:BSWV OFST,{float(voltage):.6f}")
    wavegen.write(f"C{channel}:OUTP {'ON' if output_on else 'OFF'}")
    try:
        output_state = wavegen.query(f"C{channel}:OUTP?").strip()
        print(f"T7 Wavegen CH{channel} output configuration: {output_state}")
        if "LOAD,HZ" not in output_state.upper().replace(" ", ""):
            raise RuntimeError(
                f"T7 Wavegen CH{channel} did not report High-Z load: {output_state!r}"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Unable to verify T7 Wavegen CH{channel} High-Z load setting."
        ) from exc


def set_t7_wavegen_dc(wavegen, channel: int, voltage: float, output_on: bool = True) -> None:
    """Change a preconfigured T7 DC level without rewriting waveform amplitude."""
    wavegen.write(f"C{channel}:BSWV OFST,{float(voltage):.6f}")
    wavegen.write(f"C{channel}:OUTP {'ON' if output_on else 'OFF'}")


def wait_for_t7_rf_off(inst: Instruments) -> None:
    """Confirm stable RF-off, then allow the hardware to settle before BK ON."""
    if not wait_for_stable_state(
        lambda: bool(inst.dut.isRFon()),
        False,
        T7_RF_OFF_VERIFY_TIMEOUT_S,
        consecutive_reads=T7_RF_OFF_CONFIRM_READS,
        poll_s=0.10,
    ):
        raise RuntimeError(
            "T7 could not confirm a stable RF-off state before restoring the "
            "interlock signal high."
        )
    print(
        f"T7 RF-off confirmed. Waiting "
        f"{T7_RF_OFF_TO_INTERLOCK_HIGH_DELAY_S:.3f} s before BK output ON..."
    )
    time.sleep(T7_RF_OFF_TO_INTERLOCK_HIGH_DELAY_S)


def prepare_t7_power_point(inst: Instruments, power_w: float) -> float:
    power_w = float(power_w)
    if not 0.0 <= power_w <= RATED_POWER_W:
        raise ValueError(f"T7 power {power_w:g} W is outside the rated-power range.")
    setpoint_v = power_w / RATED_POWER_W * T7_SETPOINT_FULL_SCALE_V

    configure_t7_wavegen_channel(
        inst.wavegen,
        T7_SETPOINT_WG_CHANNEL,
        setpoint_v,
        False,
    )
    configure_t7_wavegen_channel(
        inst.wavegen,
        T7_RF_ENABLE_WG_CHANNEL,
        RF_ENABLE_LOW_V,
        False,
    )
    bk9141_output(inst.bk9141, False, verify_voltage=False)
    set_host_configuration(inst.dut, power_w, rf_on=False, regulation_mode=None)
    enter_user_port(inst.dut)
    set_t7_wavegen_dc(inst.wavegen, T7_SETPOINT_WG_CHANNEL, setpoint_v, True)
    set_t7_wavegen_dc(inst.wavegen, T7_RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, True)
    time.sleep(T7_SETUP_SETTLE_S)
    return setpoint_v


def require_operator_acknowledgement(test_id: str, wiring: str) -> None:
    print("\n" + "=" * 78)
    print(f"{test_id} PRE-RUN CHECK")
    print(wiring)
    print("Verify probe attenuation, grounds/returns, load, cooling, interlocks,")
    print("power limits, wavegen voltage limits, and emergency shutdown access.")
    answer = input("Type RUN to continue: ").strip().upper()
    if answer != "RUN":
        raise RuntimeError("Operator cancelled the test.")


def inclusive_values(start: float, stop: float, step: float) -> list[float]:
    if step <= 0 or stop < start:
        raise ValueError("Invalid sweep bounds.")
    count = int(round((stop - start) / step))
    values = [round(start + i * step, 9) for i in range(count + 1)]
    if values[-1] < stop - step * 0.1:
        values.append(stop)
    return values


def set_dc(wavegen, channel: int, voltage: float, output_on: bool = True) -> None:
    wavegen.write(f"C{channel}:BSWV WVTP,DC")
    wavegen.write(f"C{channel}:BSWV OFST,{float(voltage):.6f}")
    wavegen.write(f"C{channel}:OUTP {'ON' if output_on else 'OFF'}")


def configure_t5_square_burst(wavegen, channel: int, start_v: float, end_v: float, direction: str) -> None:
    """Preload a one-cycle, manual-triggered square burst for one T5 step."""
    low_v, high_v = min(start_v, end_v), max(start_v, end_v)
    amplitude_vpp = high_v - low_v
    offset_v = (high_v + low_v) / 2.0
    if amplitude_vpp <= 0:
        raise ValueError("T5 square-burst endpoints must differ.")
    phase = T5_SQUARE_RISE_START_PHASE_DEG if direction == "RISE" else T5_SQUARE_FALL_START_PHASE_DEG
    wavegen.write(f"C{channel}:BTWV STATE,OFF")
    wavegen.write(f"C{channel}:BSWV WVTP,SQUARE,FRQ,{T5_SQUARE_FREQUENCY_HZ:.9f},AMP,{amplitude_vpp:.9f},OFST,{offset_v:.9f},DUTY,{T5_SQUARE_DUTY_PERCENT:.6f}")
    wavegen.write(f"C{channel}:BTWV STATE,ON")
    wavegen.write(f"C{channel}:BTWV GATE_NCYC,NCYC")
    wavegen.write(f"C{channel}:BTWV TRSR,MAN")
    wavegen.write(f"C{channel}:BTWV TIME,{T5_BURST_CYCLES}")
    wavegen.write(f"C{channel}:BTWV STPS,{phase:.6f}")
    wavegen.write(f"C{channel}:BTWV DLAY,0")
    wavegen.write(f"C{channel}:OUTP ON")
    time.sleep(T5_BURST_CONFIGURATION_SETTLE_S)
    print(f"T5 square burst: {start_v:.3f} V -> {end_v:.3f} V, {amplitude_vpp:.3f} Vpp, offset {offset_v:.3f} V, {T5_SQUARE_FREQUENCY_HZ:.3f} Hz, phase {phase:.1f} deg")
    try:
        print("  Wavegen burst:", wavegen.query(f"C{channel}:BTWV?").strip())
    except Exception as exc:
        print("  Burst query warning:", exc)


def trigger_t5_square_burst(wavegen, channel: int) -> None:
    wavegen.write(f"C{channel}:BTWV MTRIG")


def disable_t5_square_burst(wavegen, channel: int) -> None:
    wavegen.write(f"C{channel}:BTWV STATE,OFF")


def configure_sine(wavegen, channel: int, frequency_hz: float, vpp: float, offset_v: float) -> None:
    wavegen.write(f"C{channel}:OUTP OFF")
    wavegen.write(f"C{channel}:BSWV WVTP,SINE")
    wavegen.write(f"C{channel}:BSWV FRQ,{frequency_hz:.6f}")
    wavegen.write(f"C{channel}:BSWV AMP,{vpp:.6f}")
    wavegen.write(f"C{channel}:BSWV OFST,{offset_v:.6f}")


def set_host_configuration(
    dut,
    power_w: float,
    rf_on: bool = False,
    regulation_mode: Optional[int] = DEFAULT_REGULATION_MODE,
) -> None:
    dut.setActiveControlMode(HOST_MODE)
    time.sleep(COMMAND_SETTLE_S)
    dut.rfOFF()
    dut.setFixedFrequency(int(OPERATING_FREQUENCY_KHZ))
    # A Host-mode regulation selection does not carry into User Port mode.
    # T2/T3 therefore pass regulation_mode=None and let the physical user-port
    # inputs select the active mode after User Port control is entered.
    if regulation_mode is not None:
        dut.setRegulationMode(regulation_mode)
    dut.setFrequencyMode(0)
    dut.setPulsingFrequency(0)
    dut.setPowerSetpoint(int(round(power_w * HALO_SETPOINT_RESOLUTION)))
    if rf_on:
        dut.rfON()
        time.sleep(RF_STABILIZE_S)


def enter_user_port(dut) -> None:
    dut.setActiveControlMode(USER_PORT_MODE)
    time.sleep(COMMAND_SETTLE_S)


def verify_user_port_control_mode(dut, test_label: str) -> None:
    """Verify User Port control without requiring generator snapshot data."""
    control_mode = int(dut.reportActiveControlMode())
    if control_mode != USER_PORT_MODE:
        raise RuntimeError(
            f"{test_label}: expected User Port control mode {USER_PORT_MODE}, "
            f"but DUT reported control mode {control_mode}."
        )


def verify_user_port_forward_default(dut, test_label: str) -> None:
    """Verify the experimentally established User Port default is mode 6."""
    verify_user_port_control_mode(dut, test_label)
    active_mode = snapshot_regulation_mode(dut)
    if active_mode != REGULATION_FORWARD:
        raise RuntimeError(
            f"{test_label}: expected Forward regulation mode "
            f"{REGULATION_FORWARD} immediately after entering User Port mode "
            f"with the select input at {THRESHOLD_START_V:.3f} V, but DUT "
            f"reported mode {active_mode}. Check input polarity, wiring, and "
            "whether the wavegen output was connected too early."
        )


def snapshot_regulation_mode(dut) -> int:
    """Return the active regulation mode from dependency command-222 dataMatrix.

    FW00408 defines command 222 raw byte d28 as Regulation Mode. The supplied
    AE dependency parses the packet and returns a dataMatrix in which that field
    is index 10. This report is used instead of the restricted input-latch bits
    because it directly identifies the active control-loop regulation mode.
    """
    try:
        snapshot = dut.reportGeneratorSnapshotData()
    except Exception as exc:
        raise RuntimeError(
            "Unable to read generator snapshot while in User Port mode. "
            "Command 222 is restricted, so confirm this Ethernet interface is "
            "unlocked. If the dependency raises 'Unit not in host mode', update "
            "its HALO scaling logic to use the regulation mode already present "
            "in the snapshot instead of calling reportRegulationMode()."
        ) from exc

    if not isinstance(snapshot, (list, tuple)):
        raise RuntimeError(
            f"Unexpected generator snapshot type: {type(snapshot).__name__}."
        )
    if len(snapshot) <= GENERATOR_SNAPSHOT_REGULATION_MODE_INDEX:
        raise RuntimeError(
            f"Generator snapshot has {len(snapshot)} fields; expected at least "
            f"{GENERATOR_SNAPSHOT_REGULATION_MODE_INDEX + 1}."
        )

    mode = int(snapshot[GENERATOR_SNAPSHOT_REGULATION_MODE_INDEX])
    if mode not in {REGULATION_FORWARD, REGULATION_DELIVERED,
                    REGULATION_EXTERNAL, REGULATION_VA_LIMIT}:
        raise RuntimeError(f"Unexpected active regulation mode in snapshot: {mode}")
    return mode


def regulation_mode_is(dut, expected_mode: int) -> bool:
    """Require repeated matching snapshots to reject a one-read transient."""
    matches = 0
    for _ in range(REGULATION_STATE_CONFIRM_READS):
        mode = snapshot_regulation_mode(dut)
        if mode != expected_mode:
            return False
        matches += 1
    return matches == REGULATION_STATE_CONFIRM_READS


def process_status_bit(dut, byte_index: int, bit_number: int) -> bool:
    """Read one bit from reportProcessStatus()'s four MSB-to-LSB strings."""
    status = dut.reportProcessStatus()
    if not isinstance(status, (list, tuple)) or len(status) != 4:
        raise RuntimeError(f"Unexpected process-status response: {status!r}")
    byte = status[byte_index]
    if isinstance(byte, str):
        bits = byte.strip()
        if len(bits) != 8 or any(ch not in "01" for ch in bits):
            raise RuntimeError(f"Unexpected process-status byte: {byte!r}")
        return bits[7 - bit_number] == "1"
    value = int(byte)
    return bool(value & (1 << bit_number))


def rf_on_requested(dut) -> bool:
    """FW00408 command 162: d0 bit 6 is RF On Requested."""
    return process_status_bit(dut, 0, 6)


def rf_output_on(dut) -> bool:
    """FW00408 command 162: d0 bit 5 is RF Output On."""
    return process_status_bit(dut, 0, 5)


def t1_status_text(dut) -> str:
    """Return raw and decoded T1 process-status information for diagnostics."""
    raw = dut.reportProcessStatus()

    def decode(byte_index: int, bit_number: int) -> bool:
        byte = raw[byte_index]
        if isinstance(byte, str):
            bits = byte.strip()
            return bits[7 - bit_number] == "1"
        return bool(int(byte) & (1 << bit_number))

    return (
        f"raw={raw!r}, RF requested={decode(0, 6)}, "
        f"RF output on={decode(0, 5)}, "
        f"interlock open={decode(1, 7)}, "
        f"fault present={decode(3, 5)}, "
        f"warning present={decode(3, 6)}"
    )


def confirmed_state(reader: Callable[[], bool], expected: bool, reads: int) -> bool:
    """Require consecutive matching reads for a Boolean state."""
    for _ in range(reads):
        if bool(reader()) != expected:
            return False
    return True


def wait_for_stable_state(
    reader: Callable[[], bool],
    expected: bool,
    timeout_s: float,
    consecutive_reads: int = 2,
    poll_s: float = 0.05,
) -> bool:
    """Wait until a raw Boolean reader matches for consecutive reads."""
    deadline = time.monotonic() + timeout_s
    matches = 0
    while time.monotonic() < deadline:
        if bool(reader()) == expected:
            matches += 1
            if matches >= consecutive_reads:
                return True
        else:
            matches = 0
        time.sleep(poll_s)
    return False


def wait_until(predicate: Callable[[], bool], timeout_s: float, poll_s: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return False


def choose_standard_scale(required_scale: float, standards: Iterable[float],
                          minimum_scale: float, maximum_scale: float) -> float:
    required = max(float(required_scale), float(minimum_scale))
    for candidate in standards:
        if float(candidate) >= required:
            return min(float(candidate), float(maximum_scale))
    return float(maximum_scale)


def calculate_t5_t6_ch2_scale(power_fraction: float) -> float:
    fraction = min(max(float(power_fraction), 0.0), 1.0)
    expected_vpp = T5_T6_CH2_EXPECTED_VPP_AT_RATED_POWER * math.sqrt(fraction)
    required = expected_vpp * T5_T6_CH2_VERTICAL_PADDING_FACTOR / T5_T6_CH2_TARGET_SPAN_DIV
    return choose_standard_scale(required, T5_T6_CH2_STANDARD_SCALE_V_DIV,
                                 T5_T6_CH2_MIN_SCALE_V_DIV, T5_T6_CH2_MAX_SCALE_V_DIV)


def build_t5_scope_overrides(start_v: float, end_v: float) -> dict:
    span = abs(float(end_v) - float(start_v))
    ch1_required = span * T5_CH1_VERTICAL_PADDING_FACTOR / T5_CH1_TARGET_SPAN_DIV
    ch1_scale = choose_standard_scale(ch1_required, T5_CH1_STANDARD_SCALE_V_DIV,
                                      T5_CH1_MIN_SCALE_V_DIV, T5_CH1_MAX_SCALE_V_DIV)
    midpoint = (float(start_v) + float(end_v)) / 2.0
    ch2_scale = calculate_t5_t6_ch2_scale(max(float(start_v), float(end_v)) / 10.0)
    return {
        "channel_scale_v_div": {1: ch1_scale, 2: ch2_scale},
        "channel_position_div": {1: T5_CH1_POSITION_DIV, 2: T5_T6_CH2_POSITION_DIV},
        "channel_offset_v": {1: T5_CH1_OFFSET_SIGN * midpoint, 2: T5_T6_CH2_OFFSET_V},
        "channel_termination_ohm": {2: T5_T6_CH2_TERMINATION_OHM},
    }


def build_t5_fixed_scope_overrides() -> dict:
    """Return fixed T5 CH1/CH2 vertical settings selected by the operator."""
    required_channels = {1, 2}
    if set(T5_FIXED_VERTICAL_SCALE_V_DIV) != required_channels:
        raise ValueError(
            "T5_FIXED_VERTICAL_SCALE_V_DIV must contain exactly channels 1 and 2."
        )
    if set(T5_FIXED_VERTICAL_OFFSET_V) != required_channels:
        raise ValueError(
            "T5_FIXED_VERTICAL_OFFSET_V must contain exactly channels 1 and 2."
        )
    scales = {
        channel: float(T5_FIXED_VERTICAL_SCALE_V_DIV[channel])
        for channel in required_channels
    }
    offsets = {
        channel: float(T5_FIXED_VERTICAL_OFFSET_V[channel])
        for channel in required_channels
    }
    if any(value <= 0 for value in scales.values()):
        raise ValueError("All fixed T5 vertical scales must be greater than zero.")
    return {
        "channel_scale_v_div": scales,
        "channel_position_div": {
            1: T5_CH1_POSITION_DIV,
            2: T5_T6_CH2_POSITION_DIV,
        },
        "channel_offset_v": offsets,
        "channel_termination_ohm": {2: T5_T6_CH2_TERMINATION_OHM},
    }


def build_t6_scope_overrides(power_w: float) -> dict:
    return {
        "channel_scale_v_div": {2: calculate_t5_t6_ch2_scale(float(power_w) / RATED_POWER_W)},
        "channel_position_div": {2: T5_T6_CH2_POSITION_DIV},
        "channel_offset_v": {2: T5_T6_CH2_OFFSET_V},
        "channel_termination_ohm": {2: T5_T6_CH2_TERMINATION_OHM},
    }


def scope_termination_is_50_ohm(response: str) -> bool:
    text = response.strip().upper().replace("OHM", "")
    if "FIFTY" in text or text == "50":
        return True
    try:
        value = float(text)
    except ValueError:
        return False
    return 45.0 <= value <= 55.0


def set_scope_channel_termination(scope, channel: int, termination_ohm: float) -> str:
    """Set and verify termination, using Tektronix enum syntax for 50 ohms."""
    if abs(float(termination_ohm) - 50.0) < 1e-9:
        commands = [
            f"CH{channel}:TERMINATION FIFTY",
            f"CH{channel}:TERMINATION 50",
        ]
    else:
        commands = [
            f"CH{channel}:TERMINATION MEG",
            f"CH{channel}:TERMINATION {float(termination_ohm):g}",
        ]

    errors = []
    for command in commands:
        try:
            scope.write(command)
            scope.query("*OPC?")
            response = scope.query(f"CH{channel}:TERMINATION?").strip()
            is_expected = (
                scope_termination_is_50_ohm(response)
                if abs(float(termination_ohm) - 50.0) < 1e-9
                else not scope_termination_is_50_ohm(response)
            )
            if is_expected:
                print(f"  CH{channel} termination verified: {response} via {command}")
                return response
            errors.append(f"{command} returned {response!r}")
        except Exception as exc:
            errors.append(f"{command} raised {exc}")

    raise RuntimeError(
        f"Could not set CH{channel} termination to {termination_ohm:g} ohms. "
        + "; ".join(errors)
        + ". Check whether the installed probe or scope channel supports selectable 50-ohm termination."
    )


def configure_scope(scope, cfg: dict, time_per_div_s: Optional[float] = None) -> None:
    if time_per_div_s is None:
        time_per_div_s = float(cfg["time_per_div_s"])
    channels = tuple(cfg.get("save_channels", (cfg["trigger_ch"],)))
    scope.write("RECALL:SETUP DEFAULT"); time.sleep(1.5); scope.write("*CLS"); scope.write("ACQ:STATE OFF")
    for ch in range(1,5):
        if ch in channels:
            scales=cfg.get("channel_scale_v_div",SCOPE_DEFAULT_SCALE_V_DIV)
            positions=cfg.get("channel_position_div",{})
            offsets=cfg.get("channel_offset_v",{})
            terminations=cfg.get("channel_termination_ohm",{})
            scale=float(scales.get(ch,SCOPE_DEFAULT_SCALE_V_DIV[ch])); position=float(positions.get(ch,0.0))
            offset_v=float(offsets.get(ch,0.0)); termination=float(terminations.get(ch,SCOPE_DEFAULT_TERMINATION_OHM[ch]))
            scope.write(f"SEL:CH{ch} ON")
            scope.write(f"CH{ch}:COUP DC")
            verified_termination = set_scope_channel_termination(scope, ch, termination)
            scope.write(f"CH{ch}:SCA {scale}"); scope.write(f"CH{ch}:POS {position}")
            scope.write(f"CH{ch}:OFFSET {offset_v}")
            print(f"  CH{ch}: {scale:g} V/div, position {position:g} div, offset {offset_v:g} V, termination {verified_termination}")
        else: scope.write(f"SEL:CH{ch} OFF")
    scope.write(f"HOR:SCALE {time_per_div_s}"); scope.write("HORIZONTAL:DELAY:MODE OFF"); scope.write(f"HORIZONTAL:POSITION {SCOPE_HORIZONTAL_POSITION_PERCENT}")
    try: scope.write(f"HORIZONTAL:RECORDLENGTH {SCOPE_RECORD_LENGTH}")
    except Exception as exc: print("Record-length warning:",exc)
    scope.write(f"TRIG:A:EDGE:SOU CH{cfg['trigger_ch']}"); scope.write(f"TRIG:A:LEV:CH{cfg['trigger_ch']} {cfg['trigger_level']}")
    scope.write(f"TRIG:A:EDGE:SLO {cfg['slope']}")
    trigger_coupling = cfg.get("trigger_coupling", "DC")
    scope.write(f"TRIG:A:EDGE:COUP {trigger_coupling}")
    print(f"  Trigger edge coupling: {trigger_coupling}")
    if "trigger_mode" in cfg: scope.write(f"TRIG:A:MOD {cfg['trigger_mode']}")
    scope.write("ACQ:STOPA SEQ")


def arm_scope(scope) -> None:
    scope.write("*CLS")
    scope.write("ACQ:STOPA SEQ")
    scope.write("ACQ:STATE ON")
    time.sleep(SCOPE_ARM_S)


def arm_scope_and_confirm(scope) -> str:
    scope.write("*CLS"); scope.write("ACQ:STOPA SEQ"); scope.write("ACQ:STATE ON")
    deadline=time.monotonic()+T5_SCOPE_ARM_TIMEOUT_S
    last="UNKNOWN"
    while time.monotonic()<deadline:
        last=scope.query("TRIG:STATE?").strip().upper(); print(f"  Scope arm state: {last}")
        if last in T5_SCOPE_ARMED_STATES:
            time.sleep(SCOPE_ARM_S); return last
        time.sleep(0.05)
    raise RuntimeError(f"T5 scope failed to arm; last state={last!r}")


def wait_for_scope(scope, timeout_s: float = ACQUISITION_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = scope.query("TRIG:STATE?").strip().upper()
        if state in {"SAVE", "STOP"}:
            return True
        time.sleep(0.05)
    return False


def scope_filename(test_id: str, descriptor: str, channel: int) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    clean = "".join(c if c.isalnum() or c in "_-" else "_" for c in descriptor)
    return f"{SCOPE_DRIVE}{test_id}_{clean}_CH{channel}_{stamp}.csv"


def save_scope_channels(
    scope,
    test_id: str,
    descriptor: str,
    channels: Iterable[int],
) -> dict[int, str]:
    """Save selected scope channels with a dedicated long file-save timeout."""
    channels = tuple(channels)
    if not channels:
        print(f"No scope channels selected for {descriptor}; no waveforms saved.")
        return {}
    invalid_channels = [ch for ch in channels if ch not in (1, 2, 3, 4)]
    if invalid_channels:
        raise ValueError(f"Invalid scope channel selection: {invalid_channels}")

    saved = {}
    original_timeout = scope.timeout
    scope.write("SAVE:WAVEFORM:FILEFORMAT SPREADSHEET")
    time.sleep(SCOPE_SAVE_SETTLE_S)

    try:
        scope.timeout = SCOPE_SAVE_TIMEOUT_MS
        print(
            f"Scope waveform-save timeout temporarily set to "
            f"{SCOPE_SAVE_TIMEOUT_MS / 1000:.0f} seconds."
        )
        for ch in channels:
            name = scope_filename(test_id, descriptor, ch)
            print(f"Saving connected scope channel CH{ch} to {name}...")
            save_start = time.monotonic()
            try:
                scope.write(f'SAVE:WAVEFORM CH{ch},"{name}"')
                opc_response = scope.query("*OPC?").strip()
            except pyvisa.errors.VisaIOError as exc:
                elapsed = time.monotonic() - save_start
                raise RuntimeError(
                    f"Scope CH{ch} save did not complete within {elapsed:.1f} s. "
                    f"Destination: {name}. Check E:/ availability, free space, "
                    "and whether a partially written file exists."
                ) from exc
            elapsed = time.monotonic() - save_start
            if opc_response != "1":
                print(
                    f"Warning: CH{ch} save returned unexpected *OPC? response "
                    f"{opc_response!r}."
                )
            saved[ch] = name
            print(f"Saved CH{ch} in {elapsed:.1f} seconds.")
    finally:
        scope.timeout = original_timeout
        print(
            f"Scope VISA timeout restored to "
            f"{original_timeout / 1000:.0f} seconds."
        )
    return saved


def hysteresis(rising: Optional[float], falling: Optional[float]) -> Optional[float]:
    return None if rising is None or falling is None else rising - falling


# =============================================================================
# T1-T3: THRESHOLD TESTS
# =============================================================================

def threshold_sweep(
    inst: Instruments,
    ctx: RunContext,
    wavegen_channel: int,
    state_reader: Callable[[], bool],
    cycles: int,
    test_label: str,
    low_endpoint_timeout_s: float = STATE_SETTLE_S,
    high_endpoint_timeout_s: float = STATE_SETTLE_S,
) -> None:
    """Sweep low-to-high and high-to-low around a Boolean DUT state.

    Preconditions are checked on every cycle. The tested state must be inactive
    at THRESHOLD_START_V and active at THRESHOLD_STOP_V. A failed precondition is
    logged and aborts the run instead of producing a misleading threshold.
    """
    voltages = inclusive_values(
        THRESHOLD_START_V,
        THRESHOLD_STOP_V,
        THRESHOLD_STEP_V,
    )

    for cycle in range(1, cycles + 1):
        rising = None
        falling = None
        print("\n" + "-" * 72)
        print(f"{test_label}: starting cycle {cycle} of {cycles}")

        # Establish and verify the inactive endpoint before the rising sweep.
        set_dc(inst.wavegen, wavegen_channel, THRESHOLD_START_V, True)
        time.sleep(STATE_SETTLE_S)
        endpoint_inactive = wait_until(
            lambda: not bool(state_reader()),
            low_endpoint_timeout_s,
            poll_s=0.05,
        )
        prior = not bool(endpoint_inactive)
        print(
            f"  Low endpoint {THRESHOLD_START_V:.3f} V: "
            f"state={'ACTIVE' if prior else 'inactive'}"
        )
        if prior:
            ctx.add_event(
                severity="ERROR",
                Test=test_label,
                Cycle=cycle,
                Rising_Threshold_V=None,
                Falling_Threshold_V=None,
                Hysteresis_V=None,
                Rising_Found=False,
                Falling_Found=False,
                Error=(
                    f"Tested state was active at the low endpoint "
                    f"({THRESHOLD_START_V:.3f} V)."
                ),
            )
            raise RuntimeError(
                f"{test_label}: state must be inactive at "
                f"{THRESHOLD_START_V:.3f} V before the rising sweep."
            )

        next_progress_voltage = THRESHOLD_START_V + THRESHOLD_PROGRESS_INTERVAL_V
        for voltage in voltages[1:]:
            set_dc(inst.wavegen, wavegen_channel, voltage, True)
            time.sleep(STATE_SETTLE_S)
            current = bool(state_reader())
            if voltage + 1e-12 >= next_progress_voltage or current != prior:
                print(
                    f"  Rising sweep: {voltage:.3f} V, "
                    f"state={'ACTIVE' if current else 'inactive'}"
                )
                while voltage + 1e-12 >= next_progress_voltage:
                    next_progress_voltage += THRESHOLD_PROGRESS_INTERVAL_V
            if not prior and current:
                rising = voltage
                print(f"  >>> Rising threshold detected at {rising:.3f} V")
                break
            prior = current

        # Always drive and verify the high endpoint before the falling sweep.
        set_dc(inst.wavegen, wavegen_channel, THRESHOLD_STOP_V, True)
        time.sleep(STATE_SETTLE_S)
        endpoint_active = wait_until(
            lambda: bool(state_reader()),
            high_endpoint_timeout_s,
            poll_s=0.05,
        )
        prior = bool(endpoint_active)
        print(
            f"  High endpoint {THRESHOLD_STOP_V:.3f} V: "
            f"state={'ACTIVE' if prior else 'inactive'}; "
            f"rising threshold={rising}"
        )
        if test_label == "RF Enable":
            print(f"  T1 DUT status at high endpoint: {t1_status_text(inst.dut)}")
        if not prior:
            ctx.add_event(
                severity="ERROR",
                Test=test_label,
                Cycle=cycle,
                Rising_Threshold_V=rising,
                Falling_Threshold_V=None,
                Hysteresis_V=None,
                Rising_Found=rising is not None,
                Falling_Found=False,
                Error=(
                    f"Tested state was inactive at the high endpoint "
                    f"({THRESHOLD_STOP_V:.3f} V)."
                ),
            )
            set_dc(inst.wavegen, wavegen_channel, THRESHOLD_START_V, True)
            diagnostic = (
                f" DUT status: {t1_status_text(inst.dut)}"
                if test_label == "RF Enable" else ""
            )
            raise RuntimeError(
                f"{test_label}: state must be active at "
                f"{THRESHOLD_STOP_V:.3f} V before the falling sweep. "
                f"Rising threshold observed: {rising}.{diagnostic}"
            )

        next_progress_voltage = THRESHOLD_STOP_V - THRESHOLD_PROGRESS_INTERVAL_V
        for voltage in reversed(voltages[:-1]):
            set_dc(inst.wavegen, wavegen_channel, voltage, True)
            time.sleep(STATE_SETTLE_S)
            current = bool(state_reader())
            if voltage - 1e-12 <= next_progress_voltage or current != prior:
                print(
                    f"  Falling sweep: {voltage:.3f} V, "
                    f"state={'ACTIVE' if current else 'inactive'}"
                )
                while voltage - 1e-12 <= next_progress_voltage:
                    next_progress_voltage -= THRESHOLD_PROGRESS_INTERVAL_V
            if prior and not current:
                falling = voltage
                print(f"  >>> Falling threshold detected at {falling:.3f} V")
                break
            prior = current

        print(
            f"  Cycle {cycle} summary: rising={rising}, falling={falling}, "
            f"hysteresis={hysteresis(rising, falling)}"
        )
        ctx.add_result(
            Test=test_label,
            Cycle=cycle,
            Rising_Threshold_V=rising,
            Falling_Threshold_V=falling,
            Hysteresis_V=hysteresis(rising, falling),
            Rising_Found=rising is not None,
            Falling_Found=falling is not None,
        )

        set_dc(inst.wavegen, wavegen_channel, THRESHOLD_START_V, True)
        time.sleep(RECOVERY_S)


def run_t1(inst: Instruments) -> None:
    require_operator_acknowledgement(
        "T1",
        (
            f"WG C{T1_SETPOINT_WG_CHANNEL} -> analog setpoint input; "
            f"WG C{T1_RF_ENABLE_WG_CHANNEL} -> RF PWR ON input. "
            "Scope wiring is optional for cross-check."
        ),
    )
    ctx = RunContext("T1_RF_Enable_Threshold")

    if RATED_POWER_W <= 0:
        raise ValueError("RATED_POWER_W must be greater than zero for T1.")

    setpoint_voltage = (
        T1_POWER_W / RATED_POWER_W * T1_SETPOINT_FULL_SCALE_V
    )
    if not 0.0 <= setpoint_voltage <= T1_SETPOINT_FULL_SCALE_V:
        raise ValueError(
            f"Calculated T1 analog setpoint is {setpoint_voltage:.6f} V, "
            f"outside 0 to {T1_SETPOINT_FULL_SCALE_V:.3f} V."
        )

    print(
        f"T1 analog setpoint: {setpoint_voltage:.6f} V for "
        f"{T1_POWER_W:.1f} W on a {RATED_POWER_W:.1f} W full-scale unit."
    )

    # Prepare both user-port inputs at known values while physically disconnected.
    set_dc(
        inst.wavegen,
        T1_SETPOINT_WG_CHANNEL,
        setpoint_voltage,
        False,
    )
    set_dc(
        inst.wavegen,
        T1_RF_ENABLE_WG_CHANNEL,
        THRESHOLD_START_V,
        False,
    )

    # Configure only settings that are valid in Host mode. Do not rely on a Host
    # regulation selection carrying into User Port mode.
    set_host_configuration(
        inst.dut,
        T1_POWER_W,
        rf_on=False,
        regulation_mode=None,
    )

    # Enter User Port once, then apply the fixed analog power setpoint before
    # connecting RF Enable at its inactive endpoint.
    enter_user_port(inst.dut)
    set_dc(
        inst.wavegen,
        T1_SETPOINT_WG_CHANNEL,
        setpoint_voltage,
        True,
    )
    set_dc(
        inst.wavegen,
        T1_RF_ENABLE_WG_CHANNEL,
        THRESHOLD_START_V,
        True,
    )
    time.sleep(STATE_SETTLE_S)

    # T1 only needs confirmation that User Port control is active. Do not call
    # reportGeneratorSnapshotData() here: the supplied dependency's HALO scaling
    # path calls its Host-only reportRegulationMode() wrapper and fails in User
    # Port mode even though the firmware can report regulation mode.
    verify_user_port_control_mode(inst.dut, "T1")

    ctx.add_configuration(
        Test="RF Enable",
        Configuration="T1 Setup",
        Requested_Power_W=T1_POWER_W,
        Rated_Power_W=RATED_POWER_W,
        Analog_Setpoint_V=setpoint_voltage,
        RF_Enable_Start_V=THRESHOLD_START_V,
        RF_Enable_Stop_V=THRESHOLD_STOP_V,
        Regulation_Mode="Not read in T1; User Port defaults to Forward",
        Control_Mode=int(inst.dut.reportActiveControlMode()),
    )

    def read_t1_rf_enable_state() -> bool:
        # Use RF On Requested for the electrical RF Enable threshold. The shared
        # threshold engine now waits for the request to release at 0 V between
        # cycles, rather than assuming it clears within STATE_SETTLE_S.
        return rf_on_requested(inst.dut)

    print(f"Initial T1 DUT status: {t1_status_text(inst.dut)}")

    threshold_sweep(
        inst,
        ctx,
        T1_RF_ENABLE_WG_CHANNEL,
        read_t1_rf_enable_state,
        THRESHOLD_CYCLES,
        "RF Enable",
        low_endpoint_timeout_s=T1_LOW_ENDPOINT_RELEASE_TIMEOUT_S,
        high_endpoint_timeout_s=T1_ENDPOINT_STATE_TIMEOUT_S,
    )


def run_t2(inst: Instruments) -> None:
    require_operator_acknowledgement(
        "T2",
        f"WG C{T2_WG_CHANNEL} -> DC Bias Regulation Select. "
        "Snapshot mode 8 identifies External/DC Bias regulation.",
    )
    ctx = RunContext("T2_DC_Bias_Select_Threshold")

    # Keep the physical select input disconnected and at 0 V while preparing
    # settings that are valid only in Host mode.
    set_dc(inst.wavegen, T2_WG_CHANNEL, THRESHOLD_START_V, False)
    set_host_configuration(
        inst.dut,
        500.0,
        rf_on=False,
        regulation_mode=None,
    )

    # Enter User Port once. Do not return to Host mode during either sweep.
    enter_user_port(inst.dut)
    set_dc(inst.wavegen, T2_WG_CHANNEL, THRESHOLD_START_V, True)
    time.sleep(STATE_SETTLE_S)
    verify_user_port_forward_default(inst.dut, "T2")

    reader = lambda: regulation_mode_is(inst.dut, REGULATION_EXTERNAL)
    threshold_sweep(
        inst,
        ctx,
        T2_WG_CHANNEL,
        reader,
        THRESHOLD_CYCLES,
        "DC Bias Regulation Select",
    )


def run_t3(inst: Instruments) -> None:
    require_operator_acknowledgement(
        "T3",
        f"WG C{T3_WG_CHANNEL} -> FWD/LOAD Regulation Select. "
        "Snapshot mode 7 identifies Delivered/Real (Load) regulation.",
    )
    ctx = RunContext("T3_Load_Regulation_Select_Threshold")

    # Keep the physical select input disconnected and at 0 V while preparing
    # settings that are valid only in Host mode.
    set_dc(inst.wavegen, T3_WG_CHANNEL, THRESHOLD_START_V, False)
    set_host_configuration(
        inst.dut,
        500.0,
        rf_on=False,
        regulation_mode=None,
    )

    # Enter User Port once. Do not return to Host mode during either sweep.
    enter_user_port(inst.dut)
    set_dc(inst.wavegen, T3_WG_CHANNEL, THRESHOLD_START_V, True)
    time.sleep(STATE_SETTLE_S)
    verify_user_port_forward_default(inst.dut, "T3")

    reader = lambda: regulation_mode_is(inst.dut, REGULATION_DELIVERED)
    threshold_sweep(
        inst,
        ctx,
        T3_WG_CHANNEL,
        reader,
        THRESHOLD_CYCLES,
        "Load Regulation Select",
    )



# =============================================================================
# T4: SETPOINT TRANSFER FUNCTION
# =============================================================================

def write_t4_summary(ctx: RunContext, rows: list[dict]) -> Path:
    """Calculate and save transfer-function summary metrics."""
    if len(rows) < 2:
        raise RuntimeError("T4 requires at least two measured points for regression.")

    x = [float(row["Input_Voltage_V"]) for row in rows]
    y = [float(row["Measured_Delivered_Power_W"]) for row in rows]
    expected = [float(row["Expected_Power_W"]) for row in rows]

    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    denominator = sum((value - x_mean) ** 2 for value in x)
    if denominator == 0:
        raise RuntimeError("T4 regression cannot be calculated because all input voltages are equal.")

    slope = sum((xv - x_mean) * (yv - y_mean) for xv, yv in zip(x, y)) / denominator
    intercept = y_mean - slope * x_mean
    fitted = [slope * value + intercept for value in x]

    ss_res = sum((yv - fit) ** 2 for yv, fit in zip(y, fitted))
    ss_tot = sum((yv - y_mean) ** 2 for yv in y)
    r_squared = 1.0 if ss_tot == 0 and ss_res == 0 else (None if ss_tot == 0 else 1.0 - ss_res / ss_tot)

    ideal_slope = RATED_POWER_W / T4_FULL_SCALE_INPUT_V
    errors_w = [yv - ev for yv, ev in zip(y, expected)]
    max_abs_error_w = max(abs(value) for value in errors_w)
    max_abs_error_full_scale_pct = max_abs_error_w / RATED_POWER_W * 100.0 if RATED_POWER_W else None
    point_error_pct = [
        abs(float(row["Error_Percent"]))
        for row in rows
        if row["Error_Percent"] is not None
    ]
    max_abs_point_error_pct = max(point_error_pct) if point_error_pct else None
    linearity_errors_w = [yv - fit for yv, fit in zip(y, fitted)]
    max_abs_linearity_w = max(abs(value) for value in linearity_errors_w)
    max_abs_linearity_full_scale_pct = (
        max_abs_linearity_w / RATED_POWER_W * 100.0 if RATED_POWER_W else None
    )

    summary = {
        "Timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "Point_Count": len(rows),
        "Ideal_Slope_W_per_V": ideal_slope,
        "Measured_Slope_W_per_V": slope,
        "Slope_Error_Percent": ((slope - ideal_slope) / ideal_slope * 100.0) if ideal_slope else None,
        "Measured_Intercept_W": intercept,
        "R_Squared": r_squared,
        "Maximum_Absolute_Error_W": max_abs_error_w,
        "Maximum_Absolute_Error_Percent_Full_Scale": max_abs_error_full_scale_pct,
        "Maximum_Absolute_Point_Error_Percent": max_abs_point_error_pct,
        "Maximum_Absolute_Linearity_Error_W": max_abs_linearity_w,
        "Maximum_Absolute_Linearity_Error_Percent_Full_Scale": max_abs_linearity_full_scale_pct,
    }

    path = ctx.run_dir / T4_SUMMARY_FILENAME
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary.keys()))
        writer.writeheader()
        writer.writerow(summary)

    print("\n[T4 SUMMARY]")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print(f"  Summary CSV: {path}")
    return path


def generate_t4_plots(ctx: RunContext, rows: list[dict]) -> tuple[Path, Path]:
    """Generate transfer-function and error plots from completed T4 measurements."""
    if not rows:
        raise RuntimeError("No T4 measurements are available for plotting.")

    voltages = [float(row["Input_Voltage_V"]) for row in rows]
    expected = [float(row["Expected_Power_W"]) for row in rows]
    measured = [float(row["Measured_Delivered_Power_W"]) for row in rows]
    minimum = [float(row["Minimum_W"]) for row in rows]
    maximum = [float(row["Maximum_W"]) for row in rows]
    error_w = [float(row["Error_W"]) for row in rows]
    error_pct = [
        float("nan") if row["Error_Percent"] is None else float(row["Error_Percent"])
        for row in rows
    ]

    transfer_path = ctx.run_dir / T4_TRANSFER_PLOT_FILENAME
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.plot(voltages, expected, marker="o", label="Expected power")
    ax.plot(voltages, measured, marker="o", label="Measured delivered power")
    ax.fill_between(voltages, minimum, maximum, alpha=0.2, label="Measured minimum to maximum")
    ax.set_title("T4 Analog Setpoint Transfer Function")
    ax.set_xlabel("Analog setpoint voltage (V)")
    ax.set_ylabel("Delivered power (W)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(transfer_path, dpi=T4_PLOT_DPI)
    plt.close(fig)

    error_path = ctx.run_dir / T4_ERROR_PLOT_FILENAME
    fig, axes = plt.subplots(2, 1, figsize=(9, 8), sharex=True)
    axes[0].plot(voltages, error_w, marker="o")
    axes[0].axhline(0.0)
    axes[0].set_title("T4 Transfer-Function Error")
    axes[0].set_ylabel("Error (W)")

    axes[1].plot(voltages, error_pct, marker="o")
    axes[1].axhline(0.0)
    axes[1].set_xlabel("Analog setpoint voltage (V)")
    axes[1].set_ylabel("Error (%)")
    fig.tight_layout()
    fig.savefig(error_path, dpi=T4_PLOT_DPI)
    plt.close(fig)

    print("\n[T4 PLOTS]")
    print(f"  Transfer-function plot: {transfer_path}")
    print(f"  Error plot: {error_path}")
    return transfer_path, error_path


def run_t4(inst: Instruments) -> None:
    require_operator_acknowledgement(
        "T4",
        "WG C1 -> Setpoint input; WG C2 -> RF Enable; 50-ohm RF load connected.",
    )
    ctx = RunContext("T4_Setpoint_Transfer_Function")
    measurements_by_point: list[dict] = []

    ctx.add_configuration(
        Test="T4 Setpoint Transfer Function",
        DUT_Address=DUT_ADDRESS,
        Rated_Power_W=RATED_POWER_W,
        Full_Scale_Input_V=T4_FULL_SCALE_INPUT_V,
        Voltage_Points=str(T4_VOLTAGES_V),
        Samples_Per_Point=T4_SAMPLES_PER_POINT,
        Sample_Interval_s=T4_SAMPLE_INTERVAL_S,
        Settle_Time_s=T4_SETTLE_S,
        Setpoint_Wavegen_Channel=T4_SETPOINT_WG_CHANNEL,
        RF_Enable_Wavegen_Channel=T4_RF_ENABLE_WG_CHANNEL,
    )

    set_dc(inst.wavegen, T4_SETPOINT_WG_CHANNEL, 0.0, False)
    set_dc(inst.wavegen, T4_RF_ENABLE_WG_CHANNEL, 0.0, False)
    set_host_configuration(inst.dut, RATED_POWER_W, rf_on=False)
    enter_user_port(inst.dut)
    set_dc(inst.wavegen, T4_SETPOINT_WG_CHANNEL, 0.0, True)
    set_dc(inst.wavegen, T4_RF_ENABLE_WG_CHANNEL, RF_ENABLE_HIGH_V, True)
    time.sleep(RF_STABILIZE_S)

    try:
        for point_number, voltage in enumerate(T4_VOLTAGES_V, start=1):
            print(
                f"\nT4 point {point_number} of {len(T4_VOLTAGES_V)}: "
                f"commanding {voltage:.3f} V"
            )
            set_dc(inst.wavegen, T4_SETPOINT_WG_CHANNEL, voltage, True)
            time.sleep(T4_SETTLE_S)

            samples = []
            for sample_number in range(1, T4_SAMPLES_PER_POINT + 1):
                value = float(inst.dut.reportDeliveredPower())
                samples.append(value)
                print(
                    f"  Sample {sample_number}/{T4_SAMPLES_PER_POINT}: "
                    f"{value:.3f} W"
                )
                time.sleep(T4_SAMPLE_INTERVAL_S)

            measured = sum(samples) / len(samples)
            expected_power = RATED_POWER_W * voltage / T4_FULL_SCALE_INPUT_V
            error_value_w = measured - expected_power
            error_percent = (
                None if expected_power == 0
                else error_value_w / expected_power * 100.0
            )

            row = {
                "Point": point_number,
                "Input_Voltage_V": voltage,
                "Expected_Power_W": expected_power,
                "Measured_Delivered_Power_W": measured,
                "Error_W": error_value_w,
                "Error_Percent": error_percent,
                "Samples": T4_SAMPLES_PER_POINT,
                "Minimum_W": min(samples),
                "Maximum_W": max(samples),
            }
            measurements_by_point.append(row)
            ctx.add_result(**row)

            print(
                f"  Point result: expected={expected_power:.3f} W, "
                f"measured={measured:.3f} W, error={error_value_w:.3f} W, "
                f"error percent={'N/A' if error_percent is None else f'{error_percent:.3f}%'}"
            )
    finally:
        set_dc(inst.wavegen, T4_RF_ENABLE_WG_CHANNEL, 0.0, True)

    summary_path = write_t4_summary(ctx, measurements_by_point)
    transfer_path, error_path = generate_t4_plots(ctx, measurements_by_point)

    ctx.add_event(
        severity="INFO",
        Event="T4 output generation completed",
        Summary_CSV=str(summary_path),
        Transfer_Function_Plot=str(transfer_path),
        Error_Plot=str(error_path),
    )


# =============================================================================
# T5-T9: OSCILLOSCOPE WAVEFORM TESTS
# =============================================================================

def timing_capture(inst: Instruments, ctx: RunContext, cfg_key: str, descriptor: str,
                   stimulus: Callable[[], None], trigger_level_override: Optional[float] = None,
                   pre_arm_dwell_s: float = 0.0,
                   config_overrides: Optional[dict] = None) -> bool:
    cfg=dict(TIMING_CONFIG[cfg_key])
    if config_overrides:
        cfg.update(config_overrides)
    if trigger_level_override is not None: cfg["trigger_level"]=float(trigger_level_override)
    channels=tuple(cfg["save_channels"])
    print(f"\nConfiguring scope for {descriptor}...")
    print(f"Trigger CH{cfg['trigger_ch']} {cfg['slope']} at {cfg['trigger_level']:.3f} V; {cfg['time_per_div_s']:.9g} s/div")
    configure_scope(inst.scope,cfg)
    if pre_arm_dwell_s>0: print(f"Holding initial state {pre_arm_dwell_s:.3f} s..."); time.sleep(pre_arm_dwell_s)
    print(f"Arming scope for {descriptor}...")
    armed=arm_scope_and_confirm(inst.scope) if cfg.get("require_arm_handshake") else None
    if armed is None: arm_scope(inst.scope)
    print(f"Applying stimulus for {descriptor}..."); stimulus(); print("Waiting for trigger...")
    captured=wait_for_scope(inst.scope); final=inst.scope.query("TRIG:STATE?").strip().upper()
    print(f"Capture {descriptor}: {'triggered' if captured else 'timeout'}, final state {final}")
    files=save_scope_channels(inst.scope,ctx.test_id,descriptor,channels=channels) if captured else {}
    row={"Capture":descriptor,"Triggered":captured,"Trigger_Level_V":cfg["trigger_level"],"Trigger_Slope":cfg["slope"],"Trigger_Coupling":cfg.get("trigger_coupling","DC"),"Confirmed_Arm_State":armed,"Final_Trigger_State":final,"Saved_Channels":','.join(f'CH{x}' for x in channels),"Channel_Labels":str(cfg["labels"])}
    for ch in channels: row[f"CH{ch}_File"]=files.get(ch)
    ctx.add_result(**row); return captured


def initialize_t5(inst: Instruments, first_start_v: float) -> None:
    set_dc(inst.wavegen,T5_SETPOINT_WG_CHANNEL,first_start_v,False)
    set_dc(inst.wavegen,T5_RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True)
    def off():
        try: return (not rf_on_requested(inst.dut)) and (not rf_output_on(inst.dut))
        except Exception: return not bool(inst.dut.isRFon())
    if not wait_for_stable_state(off,True,T5_RF_OFF_TIMEOUT_S,2,0.05): raise RuntimeError("T5 could not confirm RF off before setup.")
    set_host_configuration(inst.dut,RATED_POWER_W,rf_on=False,regulation_mode=None); enter_user_port(inst.dut)
    set_dc(inst.wavegen,T5_SETPOINT_WG_CHANNEL,first_start_v,True); set_dc(inst.wavegen,T5_RF_ENABLE_WG_CHANNEL,RF_ENABLE_HIGH_V,True); time.sleep(RF_STABILIZE_S)


def run_t5(inst: Instruments) -> None:
    require_operator_acknowledgement(
        "T5",
        "WG C1 -> analog setpoint square-burst steps; WG C2 -> RF Enable held "
        "high. Scope CH1 setpoint, CH2 raw RF.",
    )
    ctx = RunContext("T5_Analog_Setpoint_Timing")
    vertical_mode = (
        "Dynamic per transition"
        if T5_USE_DYNAMIC_VERTICAL_SCALING
        else "Fixed for complete T5 run"
    )
    ctx.add_configuration(
        Test="T5 square-burst capture",
        Steps=str(T5_STEPS_V),
        Frequency_Hz=T5_SQUARE_FREQUENCY_HZ,
        Duty_Percent=T5_SQUARE_DUTY_PERCENT,
        Rise_Phase_deg=T5_SQUARE_RISE_START_PHASE_DEG,
        Fall_Phase_deg=T5_SQUARE_FALL_START_PHASE_DEG,
        Vertical_Scaling_Mode=vertical_mode,
        Fixed_Vertical_Scale_V_Div=(
            None if T5_USE_DYNAMIC_VERTICAL_SCALING
            else str(T5_FIXED_VERTICAL_SCALE_V_DIV)
        ),
        Fixed_Vertical_Offset_V=(
            None if T5_USE_DYNAMIC_VERTICAL_SCALING
            else str(T5_FIXED_VERTICAL_OFFSET_V)
        ),
    )
    print(f"T5 vertical scaling mode: {vertical_mode}")
    if not T5_USE_DYNAMIC_VERTICAL_SCALING:
        print(f"  Fixed scale settings: {T5_FIXED_VERTICAL_SCALE_V_DIV}")
        print(f"  Fixed offset settings: {T5_FIXED_VERTICAL_OFFSET_V}")

    initialize_t5(inst, float(T5_STEPS_V[0][0]))
    try:
        for cycle in range(1, T5_CYCLES + 1):
            for start_v, end_v in T5_STEPS_V:
                direction = "RISE" if end_v > start_v else "FALL"
                midpoint = (start_v + end_v) / 2.0
                descriptor = f"Cycle{cycle}_{start_v:g}V_to_{end_v:g}V_{direction}"
                if T5_USE_DYNAMIC_VERTICAL_SCALING:
                    scope_overrides = build_t5_scope_overrides(start_v, end_v)
                else:
                    scope_overrides = build_t5_fixed_scope_overrides()
                print(
                    f"T5 vertical setup ({vertical_mode}): "
                    f"CH1 {scope_overrides['channel_scale_v_div'][1]:g} V/div, "
                    f"offset {scope_overrides['channel_offset_v'][1]:g} V; "
                    f"CH2 {scope_overrides['channel_scale_v_div'][2]:g} V/div, "
                    f"offset {scope_overrides['channel_offset_v'][2]:g} V, "
                    "requested 50 ohm"
                )
                configure_t5_square_burst(
                    inst.wavegen, T5_SETPOINT_WG_CHANNEL,
                    start_v, end_v, direction,
                )
                timing_capture(
                    inst, ctx, f"T5_{direction}", descriptor,
                    lambda: trigger_t5_square_burst(
                        inst.wavegen, T5_SETPOINT_WG_CHANNEL
                    ),
                    trigger_level_override=midpoint,
                    pre_arm_dwell_s=T5_INITIAL_STATE_DWELL_S,
                    config_overrides=scope_overrides,
                )
                time.sleep(RECOVERY_S)
    finally:
        try:
            disable_t5_square_burst(inst.wavegen, T5_SETPOINT_WG_CHANNEL)
        except Exception as exc:
            print("T5 burst cleanup warning:", exc)
        set_dc(
            inst.wavegen, T5_RF_ENABLE_WG_CHANNEL,
            RF_ENABLE_LOW_V, True,
        )


def prepare_t6_rf_enable(inst: Instruments, power_w: float) -> float:
    """Prepare one T6 power point with CH1 fixed setpoint and CH2 RF Enable low."""
    power_w = float(power_w)
    if RATED_POWER_W <= 0:
        raise ValueError("RATED_POWER_W must be greater than zero for T6.")
    if not 0.0 <= power_w <= RATED_POWER_W:
        raise ValueError(
            f"T6 power {power_w:.3f} W is outside 0 to {RATED_POWER_W:.3f} W."
        )

    setpoint_voltage = (
        power_w / float(RATED_POWER_W)
        * float(T6_SETPOINT_FULL_SCALE_V)
    )
    if not 0.0 <= setpoint_voltage <= T6_SETPOINT_FULL_SCALE_V:
        raise ValueError(
            f"Calculated T6 setpoint {setpoint_voltage:.6f} V is outside "
            f"0 to {T6_SETPOINT_FULL_SCALE_V:.3f} V."
        )

    print(
        f"T6 fixed analog setpoint: {setpoint_voltage:.6f} V on "
        f"Wavegen CH{T6_SETPOINT_WG_CHANNEL} for {power_w:.1f} W."
    )
    try:
        inst.wavegen.write(
            f"C{T6_SETPOINT_WG_CHANNEL}:BTWV STATE,OFF"
        )
    except Exception as exc:
        print(f"T6 CH1 burst-disable warning: {exc}")

    set_dc(inst.wavegen, T6_SETPOINT_WG_CHANNEL, setpoint_voltage, False)
    set_dc(inst.wavegen, T6_RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, False)
    set_host_configuration(
        inst.dut,
        power_w,
        rf_on=False,
        regulation_mode=None,
    )
    enter_user_port(inst.dut)
    set_dc(inst.wavegen, T6_SETPOINT_WG_CHANNEL, setpoint_voltage, True)
    set_dc(inst.wavegen, T6_RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, True)
    time.sleep(T6_SETUP_SETTLE_S)

    try:
        ch1_state = inst.wavegen.query(
            f"C{T6_SETPOINT_WG_CHANNEL}:BSWV?"
        ).strip()
        ch1_output = inst.wavegen.query(
            f"C{T6_SETPOINT_WG_CHANNEL}:OUTP?"
        ).strip()
        print(f"T6 Wavegen CH1 state: {ch1_state}")
        print(f"T6 Wavegen CH1 output: {ch1_output}")
    except Exception as exc:
        print(f"T6 CH1 verification query warning: {exc}")
    return setpoint_voltage


def run_t6(inst: Instruments) -> None:
    require_operator_acknowledgement(
        "T6",
        "WG C1 -> fixed analog setpoint; WG C2 -> RF Enable toggled low/high. "
        "Scope CH1 monitors RF Enable and triggers on it. Scope CH2 measures "
        "the raw RF waveform. The test repeats at every T6_POWER_LEVELS_W entry.",
    )
    if not T6_POWER_LEVELS_W:
        raise ValueError("T6_POWER_LEVELS_W must contain at least one power level.")

    ctx = RunContext("T6_RF_Enable_Timing")
    ctx.add_configuration(
        Test="T6 RF Enable ON/OFF Timing",
        Power_Levels_W=str(T6_POWER_LEVELS_W),
        Rated_Power_W=RATED_POWER_W,
        Setpoint_Full_Scale_V=T6_SETPOINT_FULL_SCALE_V,
        Setpoint_Wavegen_Channel=T6_SETPOINT_WG_CHANNEL,
        RF_Enable_Wavegen_Channel=T6_RF_ENABLE_WG_CHANNEL,
        Cycles_Per_Power=T6_CYCLES,
        CH1_Behavior="Fixed DC setpoint for each power level",
        CH2_Behavior="RF Enable toggled low/high",
    )

    try:
        for power_w in T6_POWER_LEVELS_W:
            power_w = float(power_w)
            setpoint_voltage = prepare_t6_rf_enable(inst, power_w)
            ctx.add_configuration(
                Test="T6 Power Point",
                Requested_Power_W=power_w,
                Analog_Setpoint_V=setpoint_voltage,
                Cycles=T6_CYCLES,
            )
            t6_scope_overrides = build_t6_scope_overrides(power_w)

            for cycle in range(1, T6_CYCLES + 1):
                print(
                    f"\nT6 {power_w:g} W, cycle {cycle}/{T6_CYCLES}: RF ON capture"
                )
                timing_capture(
                    inst,
                    ctx,
                    "T6_ON",
                    f"{power_w:g}W_Cycle{cycle}_RF_ON",
                    lambda: set_dc(
                        inst.wavegen,
                        T6_RF_ENABLE_WG_CHANNEL,
                        RF_ENABLE_HIGH_V,
                        True,
                    ),
                    config_overrides=t6_scope_overrides,
                )
                time.sleep(RF_STABILIZE_S)

                timing_capture(
                    inst,
                    ctx,
                    "T6_OFF",
                    f"{power_w:g}W_Cycle{cycle}_RF_OFF",
                    lambda: set_dc(
                        inst.wavegen,
                        T6_RF_ENABLE_WG_CHANNEL,
                        RF_ENABLE_LOW_V,
                        True,
                    ),
                    config_overrides=t6_scope_overrides,
                )
                time.sleep(RECOVERY_S)
    finally:
        set_dc(
            inst.wavegen,
            T6_RF_ENABLE_WG_CHANNEL,
            RF_ENABLE_LOW_V,
            True,
        )


def run_t7(inst: Instruments) -> None:
    require_operator_acknowledgement(
        "T7",
        "WG C1 -> fixed analog setpoint; WG C2 -> RF Enable. Both Wavegen "
        "channels are explicitly configured for High-Z load. BK 9141 HI -> "
        "pin 23 and LO -> chassis. Scope CH1 captures the falling interlock "
        "edge; Scope CH2 captures the falling RF envelope.",
    )
    if inst.bk9141 is None:
        raise RuntimeError("T7 requires the BK Precision 9141 connection.")
    if not T7_POWER_LEVELS_W:
        raise ValueError("T7_POWER_LEVELS_W must contain at least one power level.")

    ctx = RunContext("T7_Interlock_Response_Timing")
    configure_bk9141_interlock_supply(inst.bk9141)
    ctx.add_configuration(
        Test="T7 Falling Interlock Response Timing",
        Power_Levels_W=str(T7_POWER_LEVELS_W),
        Cycles_Per_Power=T7_CYCLES,
        Wavegen_Load=T7_WAVEGEN_LOAD,
        RF_Off_To_Interlock_High_Delay_s=T7_RF_OFF_TO_INTERLOCK_HIGH_DELAY_S,
        RF_Off_Confirm_Reads=T7_RF_OFF_CONFIRM_READS,
        Scope_Time_Per_Div_s=T7_SCOPE_TIME_PER_DIV_S,
        Scope_Trigger_Level_V=T7_SCOPE_TRIGGER_LEVEL_V,
        Scope_Trigger_Slope=T7_SCOPE_TRIGGER_SLOPE,
        Saved_Scope_Channels="CH1,CH2",
    )

    try:
        for power_w in T7_POWER_LEVELS_W:
            power_w = float(power_w)
            setpoint_v = prepare_t7_power_point(inst, power_w)
            ctx.add_configuration(
                Test="T7 Power Point",
                Requested_Power_W=power_w,
                Analog_Setpoint_V=setpoint_v,
                Cycles=T7_CYCLES,
            )

            for cycle in range(1, T7_CYCLES + 1):
                print(
                    f"\nT7 {power_w:g} W, cycle {cycle}/{T7_CYCLES}: "
                    "disabling RF before restoring interlock high"
                )
                set_t7_wavegen_dc(
                    inst.wavegen,
                    T7_RF_ENABLE_WG_CHANNEL,
                    RF_ENABLE_LOW_V,
                    True,
                )
                wait_for_t7_rf_off(inst)

                bk_high = bk9141_output(inst.bk9141, True)
                time.sleep(RECOVERY_S)

                set_t7_wavegen_dc(
                    inst.wavegen,
                    T7_RF_ENABLE_WG_CHANNEL,
                    RF_ENABLE_HIGH_V,
                    True,
                )
                if not wait_until(
                    lambda: bool(inst.dut.isRFon()),
                    T7_RF_ON_SETTLE_S + ACQUISITION_TIMEOUT_S,
                ):
                    raise RuntimeError(
                        f"T7 {power_w:g} W cycle {cycle}: RF did not turn on "
                        "after RF Enable was asserted with interlock high."
                    )
                time.sleep(T7_RF_ON_SETTLE_S)

                descriptor = f"{power_w:g}W_Cycle{cycle}_Interlock_FALL"
                captured = timing_capture(
                    inst,
                    ctx,
                    "T7",
                    descriptor,
                    lambda: bk9141_output(
                        inst.bk9141,
                        False,
                        verify_voltage=False,
                    ),
                )

                rf_off = wait_until(
                    lambda: not bool(inst.dut.isRFon()),
                    T7_RF_OFF_VERIFY_TIMEOUT_S,
                )
                ctx.add_result(
                    Test="T7 RF Off Verification",
                    Power_W=power_w,
                    Analog_Setpoint_V=setpoint_v,
                    Cycle=cycle,
                    Scope_Captured=captured,
                    RF_Off_Verified=rf_off,
                    BK_High_Measured_Voltage_V=bk_high.get("Measured_Voltage_V"),
                    BK_High_Measured_Current_A=bk_high.get("Measured_Current_A"),
                    BK_High_Ready_Time_s=bk_high.get("Ready_Time_s"),
                )
                if not rf_off:
                    try:
                        inst.dut.rfOFF()
                    finally:
                        raise RuntimeError(
                            "T7 abort: RF remained on after the falling "
                            "interlock stimulus."
                        )

                set_t7_wavegen_dc(
                    inst.wavegen,
                    T7_RF_ENABLE_WG_CHANNEL,
                    RF_ENABLE_LOW_V,
                    True,
                )
                time.sleep(RECOVERY_S)
    finally:
        set_t7_wavegen_dc(
            inst.wavegen,
            T7_RF_ENABLE_WG_CHANNEL,
            RF_ENABLE_LOW_V,
            True,
        )
        try:
            bk9141_output(inst.bk9141, False, verify_voltage=False)
        except Exception as exc:
            print(f"T7 BK cleanup warning: {exc}")


def cex_locked(dut) -> bool:
    # FW00408 command 162 defines CEX lock as d3 bit 7. reportProcessStatus()
    # returns each byte as an MSB-to-LSB string, so d3 bit 7 is status[3][0].
    status = dut.reportProcessStatus()
    return status[3][0] == "1"


def t8_manual_setpoint_voltage(power_w: float) -> float:
    """Return the external DC setpoint voltage for one requested T8 power."""
    power_w = float(power_w)
    if RATED_POWER_W <= 0:
        raise ValueError("RATED_POWER_W must be greater than zero for T8.")
    if not 0.0 <= power_w <= RATED_POWER_W:
        raise ValueError(
            f"T8 power {power_w:.3f} W is outside 0 to {RATED_POWER_W:.3f} W."
        )
    return power_w / RATED_POWER_W * T8_SETPOINT_FULL_SCALE_V


def prepare_t8_user_port(inst: Instruments) -> None:
    """Prepare T8 with RF Enable low and the CEX reference configured but off."""
    set_dc(inst.wavegen, T8_RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, False)
    configure_sine(
        inst.wavegen,
        T8_CEX_REFERENCE_WG_CHANNEL,
        CEX_REFERENCE_FREQ_HZ,
        CEX_REFERENCE_VPP,
        CEX_REFERENCE_OFFSET_V,
    )
    set_host_configuration(
        inst.dut,
        RATED_POWER_W,
        rf_on=False,
        regulation_mode=None,
    )
    enter_user_port(inst.dut)
    set_dc(inst.wavegen, T8_RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, True)
    inst.wavegen.write(f"C{T8_CEX_REFERENCE_WG_CHANNEL}:OUTP OFF")
    time.sleep(T8_RF_OFF_SETTLE_S)


def prompt_for_t8_manual_setpoint(power_w: float, setpoint_v: float) -> None:
    """Require explicit operator confirmation at every T8 power point."""
    print("\n" + "=" * 78)
    print(f"T8 MANUAL SETPOINT CHANGE: {power_w:g} W")
    print(
        f"Set the external DC setpoint supply to {setpoint_v:.6f} V "
        f"for the requested {power_w:.1f} W operating point."
    )
    print("Verify the voltage at the DUT setpoint pins before continuing.")
    expected = f"{power_w:g}W"
    answer = input(f"Type {expected} after the setpoint is stable: ").strip().upper()
    if answer != expected.upper():
        raise RuntimeError(
            f"T8 cancelled before confirmation of the {power_w:g} W setpoint."
        )


def run_t8(inst: Instruments) -> None:
    require_operator_acknowledgement(
        "T8",
        "External DC supply -> DUT analog setpoint. WG C1 -> DUT RF Enable. "
        "WG C2 -> DUT CEX input and Scope CH1. Scope CH2 -> RF waveform monitor. "
        "Scope CH3 -> active-low CEX LOCK pull-up rail. The script pauses for "
        "a manual DC-setpoint change at every T8 power point.",
    )
    if not T8_POWER_LEVELS_W:
        raise ValueError("T8_POWER_LEVELS_W must contain at least one power level.")
    if T8_ACQUISITIONS_PER_POWER < 1:
        raise ValueError("T8_ACQUISITIONS_PER_POWER must be at least 1.")

    ctx = RunContext("T8_CEX_Lock_Acquisition")
    prepare_t8_user_port(inst)
    ctx.add_configuration(
        Test="T8 CEX Lock Acquisition Timing",
        Power_Levels_W=str(T8_POWER_LEVELS_W),
        Acquisitions_Per_Power=T8_ACQUISITIONS_PER_POWER,
        Setpoint_Full_Scale_V=T8_SETPOINT_FULL_SCALE_V,
        Setpoint_Control="External DC supply, manually changed per power point",
        RF_Enable_Wavegen_Channel=T8_RF_ENABLE_WG_CHANNEL,
        RF_Enable_Low_V=RF_ENABLE_LOW_V,
        RF_Enable_High_V=RF_ENABLE_HIGH_V,
        CEX_Reference_Wavegen_Channel=T8_CEX_REFERENCE_WG_CHANNEL,
        CEX_Reference_Frequency_Hz=CEX_REFERENCE_FREQ_HZ,
        CEX_Reference_Commanded_Vpp=CEX_REFERENCE_VPP,
        CEX_Reference_Observed_Scope_Vpp_Approx=1.18,
        Scope_CH1="13.56 MHz CEX reference",
        Scope_CH2="RF waveform monitor",
        Scope_CH3="Active-low CEX LOCK pull-up rail",
        T1_Definition=T8_T1_DEFINITION,
        T2_Definition=T8_T2_DEFINITION,
        Between_Capture_RF_Disable="Wavegen CH1 RF Enable commanded low",
    )

    try:
        for power_w in T8_POWER_LEVELS_W:
            power_w=float(power_w)
            setpoint_v=t8_manual_setpoint_voltage(power_w)

            # Put RF and CEX in their inactive states before asking the operator
            # to change the external setpoint supply.
            set_dc(inst.wavegen,T8_RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True)
            inst.wavegen.write(f"C{T8_CEX_REFERENCE_WG_CHANNEL}:OUTP OFF")
            time.sleep(T8_RF_OFF_SETTLE_S)
            prompt_for_t8_manual_setpoint(power_w,setpoint_v)

            ctx.add_configuration(
                Test="T8 Power Point",
                Requested_Power_W=power_w,
                Manual_Setpoint_V=setpoint_v,
                Acquisitions=T8_ACQUISITIONS_PER_POWER,
            )

            for acquisition in range(1,T8_ACQUISITIONS_PER_POWER+1):
                print(
                    f"\nT8 {power_w:g} W, acquisition "
                    f"{acquisition}/{T8_ACQUISITIONS_PER_POWER}: "
                    "establishing RF-off and unlocked state..."
                )
                set_dc(inst.wavegen,T8_RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True)
                inst.wavegen.write(f"C{T8_CEX_REFERENCE_WG_CHANNEL}:OUTP OFF")
                time.sleep(max(T8_RF_OFF_SETTLE_S,CEX_UNLOCK_DWELL_S))

                unlocked_before=not cex_locked(inst.dut)
                if T8_REQUIRE_CONFIRMED_UNLOCK and not unlocked_before:
                    raise RuntimeError(
                        f"T8 {power_w:g} W acquisition {acquisition}: DUT still "
                        "reports CEX locked after RF Enable and CEX were removed."
                    )

                set_dc(inst.wavegen,T8_RF_ENABLE_WG_CHANNEL,RF_ENABLE_HIGH_V,True)
                time.sleep(T8_RF_ON_SETTLE_S)

                descriptor=f"{power_w:g}W_Acquisition{acquisition}_CEX_Acquire"
                captured=timing_capture(
                    inst,ctx,"T8",descriptor,
                    lambda: inst.wavegen.write(
                        f"C{T8_CEX_REFERENCE_WG_CHANNEL}:OUTP ON"
                    ),
                )
                locked_after=wait_until(
                    lambda: cex_locked(inst.dut),ACQUISITION_TIMEOUT_S
                )
                ctx.add_result(
                    Test="T8 CEX Lock State Verification",
                    Power_W=power_w,
                    Manual_Setpoint_V=setpoint_v,
                    Acquisition=acquisition,
                    Scope_Captured=captured,
                    Unlocked_Before=unlocked_before,
                    Locked_After=locked_after,
                    T1_Definition=T8_T1_DEFINITION,
                    T2_Definition=T8_T2_DEFINITION,
                )
                if T8_REQUIRE_CONFIRMED_LOCK and not locked_after:
                    raise RuntimeError(
                        f"T8 {power_w:g} W acquisition {acquisition}: DUT did not "
                        f"report CEX lock within {ACQUISITION_TIMEOUT_S:.1f} seconds."
                    )
                time.sleep(CEX_LOCK_DWELL_S)

                set_dc(inst.wavegen,T8_RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True)
                inst.wavegen.write(f"C{T8_CEX_REFERENCE_WG_CHANNEL}:OUTP OFF")
                time.sleep(T8_RF_OFF_SETTLE_S)
    finally:
        set_dc(inst.wavegen,T8_RF_ENABLE_WG_CHANNEL,RF_ENABLE_LOW_V,True)
        inst.wavegen.write(f"C{T8_CEX_REFERENCE_WG_CHANNEL}:OUTP OFF")
        print("\nT8 cleanup: RF Enable is LOW and CEX reference is OFF.")
        print("Manually return the external DC setpoint supply to 0 V now.")


def run_t9(inst: Instruments) -> None:
    require_operator_acknowledgement("T9", "WG C1 -> approved CEX-reference interface. Scope CH1 reference removal, CH2 CEX LOCK.")
    ctx = RunContext("T9_CEX_Lock_Loss")
    configure_sine(inst.wavegen, CEX_REFERENCE_WG_CHANNEL, CEX_REFERENCE_FREQ_HZ,
                   CEX_REFERENCE_VPP, CEX_REFERENCE_OFFSET_V)
    for cycle in range(1, CEX_CYCLES + 1):
        inst.wavegen.write(f"C{CEX_REFERENCE_WG_CHANNEL}:OUTP ON")
        locked_before = wait_until(lambda: cex_locked(inst.dut), ACQUISITION_TIMEOUT_S)
        time.sleep(CEX_LOCK_DWELL_S)
        descriptor = f"Cycle{cycle}_CEX_Loss"
        timing_capture(inst, ctx, "T9", descriptor,
                       lambda: inst.wavegen.write(f"C{CEX_REFERENCE_WG_CHANNEL}:OUTP OFF"))
        unlocked_after = wait_until(lambda: not cex_locked(inst.dut), ACQUISITION_TIMEOUT_S)
        ctx.add_result(Cycle=cycle, Locked_Before=locked_before, Unlocked_After=unlocked_after)
        time.sleep(CEX_UNLOCK_DWELL_S)


# =============================================================================
# OPERATOR CONNECTION GUIDE
# =============================================================================
# User Port pins are taken from the Paramount HF Analog Input Threshold
# Transparency Test Plan. Signals whose pin/return is not identified in that
# plan are explicitly marked for verification against User Analog Interface
# Section 10.4 rather than guessed.
TEST_CONNECTION_GUIDE = {
    "1": """WAVEGEN / USER PORT
  CH1 HI -> pin 5  (Setpoint Input)
  CH1 LO -> pin 18 (Setpoint Return)
  CH2 HI -> pin 4  (RF PWR ON)
  CH2 LO -> pin 17 (RF PWR ON Return)

OSCILLOSCOPE
  No scope channel is required by the T1 automation.
  Optional plan cross-check: CH1 across pins 4/17 (RF Enable).
  Optional CH2: RF Power Monitor pin 3; verify its return in User Analog
  Interface Section 10.4 before connecting the probe.""",

    "2": """WAVEGEN / USER PORT
  CH1 HI -> pin 6  (DC Bias Regulation Select)
  CH1 LO -> pin 19 (Ground)
  CH2 -> not used

OSCILLOSCOPE
  No scope channel is required by the T2 automation; regulation mode is read
  from the DUT over Ethernet. The plan's optional CH1 is the input voltage
  across pins 6/19.""",

    "3": """WAVEGEN / USER PORT
  CH1 HI -> pin 8  (FWD/LOAD Regulation Select)
  CH1 LO -> pin 21 (Ground)
  CH2 -> not used

OSCILLOSCOPE
  No scope channel is required by the T3 automation; regulation mode is read
  from the DUT over Ethernet. The optional input-voltage probe is across
  pins 8/21.""",

    "4": """WAVEGEN / USER PORT
  CH1 HI -> pin 5  (Setpoint Input)
  CH1 LO -> pin 18 (Setpoint Return)
  CH2 HI -> pin 4  (RF PWR ON)
  CH2 LO -> pin 17 (RF PWR ON Return)

OSCILLOSCOPE
  No scope channel is required by the T4 automation. Delivered power is read
  from the DUT over Ethernet.""",

    "5": """WAVEGEN / USER PORT
  CH1 HI -> pin 5  (Setpoint Input)
  CH1 LO -> pin 18 (Setpoint Return)
  CH2 HI -> pin 4  (RF PWR ON)
  CH2 LO -> pin 17 (RF PWR ON Return)

OSCILLOSCOPE
  CH1 probe across pins 5/18 to monitor the setpoint step.
  CH2 -> RF Power Monitor pin 3; verify the monitor return in User Analog
  Interface Section 10.4. CH2 is configured for the scope's 50-ohm input.""",

    "6": """WAVEGEN / USER PORT
  CH1 HI -> pin 5  (Setpoint Input)
  CH1 LO -> pin 18 (Setpoint Return)
  CH2 HI -> pin 4  (RF PWR ON)
  CH2 LO -> pin 17 (RF PWR ON Return)

OSCILLOSCOPE
  CH1 probe across pins 4/17 to monitor RF Enable.
  CH2 -> RF Power Monitor pin 3; verify its return in User Analog Interface
  Section 10.4. CH2 is configured for the scope's 50-ohm input.
  CH3 -> RF Detector only if that approved detector is installed; its User Port
  pin is not identified in the supplied test plan.""",

    "7": """WAVEGEN / USER PORT
  CH1 HI -> pin 5 (Setpoint); CH1 LO -> pin 18 (Setpoint Return)
  CH2 HI -> pin 4 (RF PWR ON); CH2 LO -> pin 17 (RF PWR ON Return)
  T7 explicitly configures both Wavegen outputs for High-Z load.

BK 9141
  Output HI -> pin 23; Output LO -> chassis; programmed for 25 V.

OSCILLOSCOPE
  CH1 pin 23 to chassis, 1 Mohm input, falling-edge trigger near 15 V.
  CH2 RF envelope/waveform monitor, 50 ohm input.
  T7 saves CH1 and CH2 only. CH3 and CH4 are not used by T7.""",

    "8": """WAVEGEN / USER PORT
  External DC setpoint supply HI -> pin 5  (Setpoint Input)
  External DC setpoint supply LO -> pin 18 (Setpoint Return)
  Wavegen CH1 HI -> pin 4  (RF PWR ON)
  Wavegen CH1 LO -> pin 17 (RF PWR ON Return)
  Wavegen CH2 -> approved CEX reference-input interface. The supplied test plan
  does not identify the CEX reference-input pin; verify it in Section 10.4.

OSCILLOSCOPE
  CH1 -> Wavegen CH2/CEX reference node using an appropriate tee/high-impedance
  monitor arrangement.
  CH2 -> RF waveform monitor used to verify RF is active; use the approved RF
  monitor connection for the bench setup.
  CH3 across the active-low CEX LOCK pull-up circuit associated with pin 12
  (CEX LOCK) and pin 25 (Return). The pull-up reads about 5 V unlocked and
  about 0 V locked.""",

    "9": """WAVEGEN / USER PORT
  Wavegen CH1 -> approved CEX reference-input interface. The supplied plan does
  not identify that input pin; verify it in User Analog Interface Section 10.4.

OSCILLOSCOPE
  CH1 -> CEX reference node to observe reference removal.
  CH2 across pin 12 (CEX LOCK) and pin 25 (Return).
  CH3 -> approved RF phase-monitor connection; its pin is not identified in the
  supplied plan.
  Note: the supplied test plan marks T9 as 'ignore for now'.""",
}


def display_selected_test_connections(choice: str, test_name: str) -> None:
    """Print test-specific wavegen, User Port, and oscilloscope connections."""
    print("\n" + "=" * 78)
    print(f"SELECTED TEST CONNECTIONS: {test_name}")
    print("=" * 78)
    print(TEST_CONNECTION_GUIDE[choice])
    print("\nGENERAL CHECKS")
    print("  - Confirm DB25 pin numbering from the mating-side view.")
    print("  - Connect each source/probe return only to the specified return pin.")
    print("  - Verify probe attenuation, termination, grounding, isolation, load,")
    print("    cooling, voltage limits, interlocks, and emergency shutdown access.")
    print("=" * 78)


TESTS = {
    "1": ("T1 RF Enable Threshold", run_t1),
    "2": ("T2 DC Bias Regulation Select Threshold", run_t2),
    "3": ("T3 Load Power Regulation Select Threshold", run_t3),
    "4": ("T4 Setpoint Transfer Function", run_t4),
    "5": ("T5 Analog Processing Timing", run_t5),
    "6": ("T6 RF Enable Timing", run_t6),
    "7": ("T7 Interlock Response Timing", run_t7),
    "8": ("T8 CEX Lock Acquisition Timing", run_t8),
    "9": ("T9 CEX Lock Loss Timing", run_t9),
}


def main() -> int:
    print(__doc__)
    print("Available tests:")
    for key, (name, _) in TESTS.items():
        print(f"  {key}. {name}")
    choice = input("Select one test (1-9): ").strip()
    if choice not in TESTS:
        print("Invalid selection.")
        return 2

    global T5_USE_DYNAMIC_VERTICAL_SCALING
    name, runner = TESTS[choice]
    print(f"\nSelected: {name}")
    if choice == "5":
        print("\nT5 VERTICAL SCALING SELECTION")
        print("  1. Dynamic scale and offset for every setpoint transition")
        print("  2. Fixed scale and offset for the complete T5 run")
        scaling_choice = input("Select T5 vertical scaling mode (1-2): ").strip()
        if scaling_choice == "1":
            T5_USE_DYNAMIC_VERTICAL_SCALING = True
        elif scaling_choice == "2":
            T5_USE_DYNAMIC_VERTICAL_SCALING = False
        else:
            print("Invalid T5 vertical scaling selection.")
            return 2
    display_selected_test_connections(choice, name)
    answer = input("Type WIRED after verifying all connections: ").strip().upper()
    if answer != "WIRED":
        print("Operator cancelled before instrument connection.")
        return 2
    inst = Instruments(connect_bk9141=(choice == "7"))
    try:
        inst.connect()
        runner(inst)
        print("\nTest completed. Review Run Configuration, Cycle Results, Event Log (if created), and scope-drive waveform CSVs.")
        return 0
    except KeyboardInterrupt:
        print("\nOperator interrupted the test.")
        return 130
    except Exception as exc:
        print(f"\nTEST ABORTED: {exc}")
        traceback.print_exc()
        return 1
    finally:
        inst.close()


if __name__ == "__main__":
    raise SystemExit(main())

