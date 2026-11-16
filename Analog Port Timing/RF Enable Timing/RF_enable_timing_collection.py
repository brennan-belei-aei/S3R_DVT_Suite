"""RF Enable Timing data collection GUI.

Measures RF turn-on and turn-off timing at selected power levels. Waveform
generator CH1 supplies the fixed analog setpoint and CH2 toggles RF Enable.
Scope CH1 monitors RF Enable, CH2 monitors the raw RF waveform, and CH3
monitors the approved RF detector. The child GUI inherits defaults from the
Paramount unified collection launcher while allowing per-run overrides.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import pyvisa
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default, visa_socket_resource
import AECommandsUniversal

TEST_NAME = "RF Enable Timing"
TEST_ID = "RF_ENABLE_TIMING"
CONTRACT_NAME = "Paramount.RFEnableTiming"
CONTRACT_VERSION = "1.0.0"

# Unified-launcher defaults. The GUI exposes all three addresses for editing.
SCOPE_ADDRESS_DEFAULT = bench_default("oscilloscope")
WAVEGEN_ADDRESS_DEFAULT = bench_default("waveform_generator")
DUT_ADDRESS_DEFAULT = bench_default("generator")

# The original active power list is 4000/5000/6000 W. A 6000 W default rating
# makes those points internally consistent; both values are editable in the GUI.
DEFAULT_RATED_POWER_W = 6000.0
DEFAULT_POWER_LEVELS_W = [4000.0, 5000.0, 6000.0]
DEFAULT_CYCLES_PER_POWER = 10
MAX_CYCLES = 100

OPERATING_FREQUENCY_KHZ = 13_560
REGULATION_FORWARD = 6
HOST_MODE = 2
USER_PORT_MODE = 4
HALO_SETPOINT_RESOLUTION = 10
SETPOINT_FULL_SCALE_V = 10.0
SETPOINT_WG_CHANNEL = 1
RF_ENABLE_WG_CHANNEL = 2
RF_ENABLE_LOW_V = 0.0
RF_ENABLE_HIGH_V = 6.0
SETUP_SETTLE_S = 1.0
RF_STABILIZE_S = 1.0
RECOVERY_S = 1.0
COMMAND_SETTLE_S = 0.25

INSTRUMENT_TIMEOUT_MS = 30_000
WAVEFORM_TRANSFER_TIMEOUT_MS = 120_000
WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES = 25 * 1024 * 1024
SCOPE_ARM_S = 0.35
ACQUISITION_TIMEOUT_S = 10.0
SCOPE_RECORD_LENGTH = 100_000
SCOPE_HORIZONTAL_POSITION_PERCENT = 35.0
SCOPE_TIME_PER_DIV_S = 1e-3
TRIGGER_LEVEL_V = 2.5
TRIGGER_COUPLING = "NOISEREJ"

SCOPE_DEFAULT_SCALE_V_DIV = {1: 2.0, 2: 2.0, 3: 2.0, 4: 2.0}
SCOPE_DEFAULT_TERMINATION_OHM = {1: 1_000_000.0, 2: 1_000_000.0, 3: 1_000_000.0, 4: 1_000_000.0}
RF_MONITOR_TERMINATION_OHM = 50.0
RF_MONITOR_EXPECTED_VPP_AT_RATED_POWER = 4.0
RF_MONITOR_STANDARD_SCALE_V_DIV = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
RF_MONITOR_TARGET_SPAN_DIV = 4.0
RF_MONITOR_VERTICAL_PADDING_FACTOR = 1.25
RF_MONITOR_MIN_SCALE_V_DIV = 0.1
RF_MONITOR_MAX_SCALE_V_DIV = 2.0
RF_MONITOR_OFFSET_V = 0.0
RF_MONITOR_POSITION_DIV = 0.0

# CH1 and CH3 are intentionally explicit and editable in code after bench review.
RF_ENABLE_SCALE_V_DIV = 2.0
RF_ENABLE_OFFSET_V = 0.0
RF_ENABLE_POSITION_DIV = 0.0
RF_DETECTOR_SCALE_V_DIV = 2.0
RF_DETECTOR_OFFSET_V = 0.0
RF_DETECTOR_POSITION_DIV = 0.0
RF_DETECTOR_TERMINATION_OHM = 1_000_000.0

PRINT_RECORDED_ROWS = True


def safe_component(value: object, fallback: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "").strip())
    return re.sub(r"\s+", " ", text).strip(" .") or fallback


def parse_power_levels(text: str) -> list[float]:
    values = []
    for token in re.split(r"[,;\s]+", text.strip()):
        if token:
            value = float(token)
            if not math.isfinite(value):
                raise ValueError("Power levels must be finite numbers.")
            values.append(value)
    if not values:
        raise ValueError("Enter at least one power level.")
    if len(set(values)) != len(values):
        raise ValueError("Power levels cannot contain duplicates.")
    return values


def choose_standard_scale(required: float, standards: Iterable[float], minimum: float, maximum: float) -> float:
    required = max(float(required), float(minimum))
    for candidate in standards:
        if float(candidate) >= required:
            return min(float(candidate), float(maximum))
    return float(maximum)


def rf_monitor_scale(power_w: float, rated_power_w: float) -> float:
    fraction = min(max(float(power_w) / float(rated_power_w), 0.0), 1.0)
    expected_vpp = RF_MONITOR_EXPECTED_VPP_AT_RATED_POWER * math.sqrt(fraction)
    required = expected_vpp * RF_MONITOR_VERTICAL_PADDING_FACTOR / RF_MONITOR_TARGET_SPAN_DIV
    return choose_standard_scale(required, RF_MONITOR_STANDARD_SCALE_V_DIV,
                                 RF_MONITOR_MIN_SCALE_V_DIV, RF_MONITOR_MAX_SCALE_V_DIV)


def validate_run_settings(rated_power_w: float, power_levels_w: list[float], cycles: int) -> None:
    if not math.isfinite(rated_power_w) or rated_power_w <= 0:
        raise ValueError("Rated power must be greater than zero.")
    if not 1 <= cycles <= MAX_CYCLES:
        raise ValueError(f"Cycles must be from 1 through {MAX_CYCLES}.")
    for power in power_levels_w:
        if not 0 < power <= rated_power_w:
            raise ValueError(f"Power level {power:g} W is outside 0 to {rated_power_w:g} W.")
        voltage = power / rated_power_w * SETPOINT_FULL_SCALE_V
        if not 0 <= voltage <= SETPOINT_FULL_SCALE_V:
            raise ValueError(f"Calculated setpoint {voltage:g} V is outside the 0 to {SETPOINT_FULL_SCALE_V:g} V range.")


@dataclass
class RunContext:
    run_dir: Path
    run_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    configuration_rows: list[dict[str, Any]] = field(default_factory=list)
    result_rows: list[dict[str, Any]] = field(default_factory=list)
    event_rows: list[dict[str, Any]] = field(default_factory=list)

    def _stamp(self, row: dict[str, Any]) -> dict[str, Any]:
        return {"Timestamp": datetime.now().isoformat(timespec="milliseconds"),
                "Contract_Name": CONTRACT_NAME, "Contract_Version": CONTRACT_VERSION,
                "Test_ID": TEST_ID, "Run_ID": self.run_id, **row}

    def _write(self, name: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        fields = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
        target = self.run_dir / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader(); writer.writerows(rows)
        os.replace(temporary, target)

    def add_configuration(self, **row: Any) -> None:
        self.configuration_rows.append(self._stamp(row)); self._write("Run_Configuration.csv", self.configuration_rows)

    def add_result(self, **row: Any) -> None:
        stamped = self._stamp(row); self.result_rows.append(stamped); self._write("Capture_Results.csv", self.result_rows)
        if PRINT_RECORDED_ROWS:
            print("\n[RECORDED: Capture Results]")
            for key, value in stamped.items(): print(f"  {key}: {value}")

    def add_event(self, severity: str, event: str, **row: Any) -> None:
        self.event_rows.append(self._stamp({"Severity": severity, "Event": event, **row})); self._write("Event_Log.csv", self.event_rows)

    def write_contract(self) -> None:
        data = {
            "contract_name": CONTRACT_NAME, "contract_version": CONTRACT_VERSION,
            "test_id": TEST_ID, "test_name": TEST_NAME,
            "waveform_channels": {"CH1": "RF Enable", "CH2": "RF Monitor", "CH3": "RF Detector"},
            "capture_key": ["Run_ID", "Requested_Power_W", "Cycle", "Edge"],
            "waveforms_are_transferred_over_ethernet": True,
            "waveform_location": "Local run folder",
            "waveform_format": "Two-column CSV: Time and channel voltage",
            "analysis_pairing": "Power, cycle, edge and channel from local waveform filename plus Capture_Results.csv",
        }
        (self.run_dir / "Output_Contract.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    def write_manifest(self, status: str, expected_captures: int, **extra: Any) -> None:
        data = {"contract_name": CONTRACT_NAME, "contract_version": CONTRACT_VERSION,
                "run_id": self.run_id, "status": status,
                "updated": datetime.now().isoformat(timespec="seconds"),
                "expected_captures": expected_captures, "recorded_captures": len(self.result_rows),
                "run_directory": str(self.run_dir),
                "files": sorted(p.name for p in self.run_dir.iterdir() if p.is_file()), **extra}
        (self.run_dir / "Run_Manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


class Instruments:
    def __init__(self, dut_address: str, scope_address: str, wavegen_address: str):
        self.dut_address = dut_address.strip(); self.scope_address = scope_address.strip(); self.wavegen_address = wavegen_address.strip()
        self.wavegen_fallback = visa_socket_resource(self.wavegen_address)
        self.rm = self.scope = self.wavegen = self.dut = None

    def connect(self) -> None:
        self.rm = pyvisa.ResourceManager()
        print(f"VISA backend: {self.rm.visalib}")
        try: print(f"VISA resources found: {self.rm.list_resources()}")
        except Exception as exc: print(f"VISA resource-list warning: {exc}")
        self.scope = self.rm.open_resource(self.scope_address); self.scope.timeout = INSTRUMENT_TIMEOUT_MS
        self.scope.read_termination = "\n"; self.scope.write_termination = "\n"
        self.scope.chunk_size = WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES
        print("Scope:", self.scope.query("*IDN?").strip())
        errors = []
        for address in dict.fromkeys((self.wavegen_address, self.wavegen_fallback)):
            candidate = None
            try:
                candidate = self.rm.open_resource(address); candidate.timeout = INSTRUMENT_TIMEOUT_MS
                if str(address).endswith("::SOCKET"):
                    candidate.read_termination = "\n"; candidate.write_termination = "\n"
                print(f"Waveform generator connected using {address}: {candidate.query('*IDN?').strip()}")
                self.wavegen = candidate; break
            except Exception as exc:
                errors.append(f"{address}: {type(exc).__name__}: {exc}")
                try:
                    if candidate is not None: candidate.close()
                except Exception: pass
        if self.wavegen is None: raise RuntimeError("Unable to connect to waveform generator. " + " | ".join(errors))
        self.dut = AECommandsUniversal.generator(self.dut_address); print("DUT connected.")

    def safe_state(self) -> None:
        print("Placing equipment in a best-effort safe state...")
        if self.wavegen is not None:
            for channel in (SETPOINT_WG_CHANNEL, RF_ENABLE_WG_CHANNEL):
                try:
                    self.wavegen.write(f"C{channel}:BSWV WVTP,DC"); self.wavegen.write(f"C{channel}:BSWV OFST,0"); self.wavegen.write(f"C{channel}:OUTP OFF")
                except Exception as exc: print(f"Waveform-generator CH{channel} cleanup warning: {exc}")
        if self.dut is not None:
            try: self.dut.setActiveControlMode(HOST_MODE); time.sleep(COMMAND_SETTLE_S)
            except Exception: pass
            try: self.dut.rfOFF()
            except Exception as exc: print(f"DUT RF-off cleanup warning: {exc}")
        if self.scope is not None:
            try: self.scope.write("ACQ:STATE OFF")
            except Exception: pass

    def close(self) -> None:
        self.safe_state()
        for instrument in (self.scope, self.wavegen):
            try:
                if instrument is not None: instrument.close()
            except Exception: pass
        try:
            if self.dut is not None: self.dut.close()
        except Exception: pass
        try:
            if self.rm is not None: self.rm.close()
        except Exception: pass


def set_dc(wavegen: Any, channel: int, voltage: float, output_on: bool = True) -> None:
    wavegen.write(f"C{channel}:BSWV WVTP,DC")
    wavegen.write(f"C{channel}:BSWV OFST,{float(voltage):.6f}")
    wavegen.write(f"C{channel}:OUTP {'ON' if output_on else 'OFF'}")


def set_host_configuration(dut: Any, power_w: float) -> None:
    dut.setActiveControlMode(HOST_MODE); time.sleep(COMMAND_SETTLE_S); dut.rfOFF()
    dut.setFixedFrequency(int(OPERATING_FREQUENCY_KHZ)); dut.setFrequencyMode(0); dut.setPulsingFrequency(0)
    dut.setPowerSetpoint(int(round(float(power_w) * HALO_SETPOINT_RESOLUTION)))


def enter_user_port(dut: Any) -> None:
    dut.setActiveControlMode(USER_PORT_MODE); time.sleep(COMMAND_SETTLE_S)
    reported = int(dut.reportActiveControlMode())
    if reported != USER_PORT_MODE: raise RuntimeError(f"Expected User Port mode {USER_PORT_MODE}; DUT reported {reported}.")


def prepare_power_point(inst: Instruments, power_w: float, rated_power_w: float) -> float:
    setpoint_v = power_w / rated_power_w * SETPOINT_FULL_SCALE_V
    try: inst.wavegen.write(f"C{SETPOINT_WG_CHANNEL}:BTWV STATE,OFF")
    except Exception as exc: print(f"Setpoint burst-disable warning: {exc}")
    set_dc(inst.wavegen, SETPOINT_WG_CHANNEL, setpoint_v, False)
    set_dc(inst.wavegen, RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, False)
    set_host_configuration(inst.dut, power_w); enter_user_port(inst.dut)
    set_dc(inst.wavegen, SETPOINT_WG_CHANNEL, setpoint_v, True)
    set_dc(inst.wavegen, RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, True)
    time.sleep(SETUP_SETTLE_S)
    try:
        print("Waveform-generator CH1 state:", inst.wavegen.query(f"C{SETPOINT_WG_CHANNEL}:BSWV?").strip())
        print("Waveform-generator CH1 output:", inst.wavegen.query(f"C{SETPOINT_WG_CHANNEL}:OUTP?").strip())
    except Exception as exc: print(f"CH1 verification query warning: {exc}")
    return setpoint_v


def termination_is_50_ohm(response: str) -> bool:
    text = response.strip().upper().replace("OHM", "")
    if "FIFTY" in text or text == "50": return True
    try: value = float(text)
    except ValueError: return False
    return 45 <= value <= 55


def set_scope_termination(scope: Any, channel: int, termination_ohm: float) -> str:
    commands = ([f"CH{channel}:TERMINATION FIFTY", f"CH{channel}:TERMINATION 50"]
                if abs(termination_ohm - 50) < 1e-9 else
                [f"CH{channel}:TERMINATION MEG", f"CH{channel}:TERMINATION {termination_ohm:g}"])
    errors = []
    for command in commands:
        try:
            scope.write(command); scope.query("*OPC?"); response = scope.query(f"CH{channel}:TERMINATION?").strip()
            expected = termination_is_50_ohm(response) if abs(termination_ohm - 50) < 1e-9 else not termination_is_50_ohm(response)
            if expected: print(f"  CH{channel} termination verified: {response}"); return response
            errors.append(f"{command} returned {response!r}")
        except Exception as exc: errors.append(f"{command}: {exc}")
    raise RuntimeError(f"Could not set CH{channel} termination to {termination_ohm:g} ohms. " + "; ".join(errors))


def scope_configuration(edge: str, power_w: float, rated_power_w: float) -> dict[str, Any]:
    return {
        "trigger_ch": 1, "trigger_level": TRIGGER_LEVEL_V,
        "slope": "RISE" if edge == "ON" else "FALL", "trigger_coupling": TRIGGER_COUPLING,
        "time_per_div_s": SCOPE_TIME_PER_DIV_S, "save_channels": (1, 2, 3),
        "labels": {1: "RF_Enable", 2: "RF_Monitor", 3: "RF_Detector"},
        # Preserve the original behavior: only CH2 is overridden dynamically.
        # CH1 and CH3 therefore use the shared 2 V/div, 0 V offset, 1 Mohm defaults.
        "channel_scale_v_div": {2: rf_monitor_scale(power_w, rated_power_w)},
        "channel_position_div": {2: RF_MONITOR_POSITION_DIV},
        "channel_offset_v": {2: RF_MONITOR_OFFSET_V},
        "channel_termination_ohm": {2: RF_MONITOR_TERMINATION_OHM},
    }


def configure_scope(scope: Any, cfg: dict[str, Any]) -> None:
    channels = cfg["save_channels"]
    scope.write("RECALL:SETUP DEFAULT"); time.sleep(1.5); scope.write("*CLS"); scope.write("ACQ:STATE OFF")
    for channel in range(1, 5):
        if channel in channels:
            scope.write(f"SEL:CH{channel} ON"); scope.write(f"CH{channel}:COUP DC")
            termination = float(cfg.get("channel_termination_ohm", {}).get(channel, SCOPE_DEFAULT_TERMINATION_OHM[channel]))
            scale = float(cfg.get("channel_scale_v_div", {}).get(channel, SCOPE_DEFAULT_SCALE_V_DIV[channel]))
            position = float(cfg.get("channel_position_div", {}).get(channel, 0.0))
            offset = float(cfg.get("channel_offset_v", {}).get(channel, 0.0))
            set_scope_termination(scope, channel, termination)
            scope.write(f"CH{channel}:SCA {scale}")
            scope.write(f"CH{channel}:POS {position}")
            scope.write(f"CH{channel}:OFFSET {offset}")
        else: scope.write(f"SEL:CH{channel} OFF")
    scope.write(f"HOR:SCALE {cfg['time_per_div_s']}"); scope.write("HORIZONTAL:DELAY:MODE OFF")
    scope.write(f"HORIZONTAL:POSITION {SCOPE_HORIZONTAL_POSITION_PERCENT}")
    try: scope.write(f"HORIZONTAL:RECORDLENGTH {SCOPE_RECORD_LENGTH}")
    except Exception as exc: print(f"Record-length warning: {exc}")
    scope.write(f"TRIG:A:EDGE:SOU CH{cfg['trigger_ch']}"); scope.write(f"TRIG:A:LEV:CH{cfg['trigger_ch']} {cfg['trigger_level']}")
    scope.write(f"TRIG:A:EDGE:SLO {cfg['slope']}"); scope.write(f"TRIG:A:EDGE:COUP {cfg['trigger_coupling']}"); scope.write("ACQ:STOPA SEQ")


def arm_scope(scope: Any) -> None:
    scope.write("*CLS"); scope.write("ACQ:STOPA SEQ"); scope.write("ACQ:STATE ON"); time.sleep(SCOPE_ARM_S)


def wait_for_scope(scope: Any, timeout_s: float = ACQUISITION_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if scope.query("TRIG:STATE?").strip().upper() in {"SAVE", "STOP"}: return True
        time.sleep(0.05)
    return False


def local_waveform_filename(run_dir: Path, descriptor: str, channel: int) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", descriptor)
    return run_dir / f"{TEST_ID}_{clean}_CH{channel}_{stamp}.csv"


def transfer_scope_channel(scope: Any, channel: int, output_path: Path) -> dict[str, Any]:
    """Download one stopped acquisition from the scope over Ethernet.

    This follows the verified CURVE? binary-transfer method in
    CWDataCollection_GUI.py. A .partial file is used so an interrupted transfer
    cannot be mistaken for a complete waveform.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_suffix(output_path.suffix + ".partial")
    original_timeout = scope.timeout
    try:
        scope.timeout = WAVEFORM_TRANSFER_TIMEOUT_MS
        scope.write("ACQUIRE:STATE STOP")
        scope.write("*WAI")
        scope.write(f"DATA:SOURCE CH{channel}")
        scope.write("DATA:ENCDG RIBINARY")
        scope.write("DATA:WIDTH 2")
        record_length = int(float(scope.query("HORIZONTAL:RECORDLENGTH?")))
        if record_length < 1:
            raise RuntimeError(f"Scope reported invalid record length {record_length}.")
        scope.write("DATA:START 1")
        scope.write(f"DATA:STOP {record_length}")
        xincr = float(scope.query("WFMOUTPRE:XINCR?"))
        xzero = float(scope.query("WFMOUTPRE:XZERO?"))
        pt_off = float(scope.query("WFMOUTPRE:PT_OFF?"))
        ymult = float(scope.query("WFMOUTPRE:YMULT?"))
        yzero = float(scope.query("WFMOUTPRE:YZERO?"))
        yoff = float(scope.query("WFMOUTPRE:YOFF?"))
        xunit = scope.query("WFMOUTPRE:XUNIT?").strip().strip('"') or "s"
        yunit = scope.query("WFMOUTPRE:YUNIT?").strip().strip('"') or "V"
        raw = np.asarray(scope.query_binary_values(
            "CURVE?", datatype="h", is_big_endian=True,
            container=np.array, expect_termination=False,
        ), dtype=np.float64)
        if raw.size == 0:
            raise RuntimeError(f"No waveform points returned for CH{channel}.")
        index = np.arange(raw.size, dtype=np.float64)
        times = xzero + (index - pt_off) * xincr
        values = (raw - yoff) * ymult + yzero
        np.savetxt(partial_path, np.column_stack((times, values)), delimiter=",",
                   header=f"Time ({xunit}),CH{channel} ({yunit})", comments="", fmt="%.12e")
        partial_path.replace(output_path)
        print(f"Transferred CH{channel}: {raw.size:,} points to {output_path}")
        return {"Path": str(output_path), "Points": int(raw.size),
                "Sample_Interval_s": xincr, "X_Unit": xunit, "Y_Unit": yunit}
    except Exception:
        if partial_path.exists(): partial_path.unlink()
        raise
    finally:
        scope.timeout = original_timeout


def transfer_scope_channels(scope: Any, run_dir: Path, descriptor: str,
                            channels: Iterable[int]) -> dict[int, dict[str, Any]]:
    transferred = {}
    for channel in channels:
        output_path = local_waveform_filename(run_dir, descriptor, channel)
        print(f"Transferring CH{channel} over Ethernet to {output_path}...")
        transferred[channel] = transfer_scope_channel(scope, channel, output_path)
    return transferred


def capture_edge(inst: Instruments, ctx: RunContext, power_w: float, rated_power_w: float,
                 setpoint_v: float, cycle: int, edge: str) -> bool:
    cfg = scope_configuration(edge, power_w, rated_power_w)
    descriptor = f"{power_w:g}W_Cycle{cycle}_RF_{edge}"
    configure_scope(inst.scope, cfg); arm_scope(inst.scope)
    target = RF_ENABLE_HIGH_V if edge == "ON" else RF_ENABLE_LOW_V
    set_dc(inst.wavegen, RF_ENABLE_WG_CHANNEL, target, True)
    captured = wait_for_scope(inst.scope); final_state = inst.scope.query("TRIG:STATE?").strip().upper()
    files = transfer_scope_channels(inst.scope, ctx.run_dir, descriptor, cfg["save_channels"]) if captured else {}
    ctx.add_result(Capture_ID=descriptor, Requested_Power_W=power_w, Rated_Power_W=rated_power_w,
                   Analog_Setpoint_V=setpoint_v, Cycle=cycle, Edge=edge, Triggered=captured,
                   Trigger_Level_V=cfg["trigger_level"], Trigger_Slope=cfg["slope"],
                   Trigger_Coupling=cfg["trigger_coupling"], Final_Trigger_State=final_state,
                   CH1_Label="RF_Enable", CH2_Label="RF_Monitor", CH3_Label="RF_Detector",
                   CH1_File=files.get(1, {}).get("Path"), CH2_File=files.get(2, {}).get("Path"), CH3_File=files.get(3, {}).get("Path"),
                   CH1_Points=files.get(1, {}).get("Points"), CH2_Points=files.get(2, {}).get("Points"), CH3_Points=files.get(3, {}).get("Points"),
                   CH1_Scale_V_Div=cfg.get("channel_scale_v_div", {}).get(1, SCOPE_DEFAULT_SCALE_V_DIV[1]),
                   CH2_Scale_V_Div=cfg.get("channel_scale_v_div", {}).get(2, SCOPE_DEFAULT_SCALE_V_DIV[2]),
                   CH3_Scale_V_Div=cfg.get("channel_scale_v_div", {}).get(3, SCOPE_DEFAULT_SCALE_V_DIV[3]))
    return captured


def create_run_folder(parent: Path, dut_address: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = parent / f"{stamp} - {TEST_NAME} - {safe_component(dut_address, 'Generator')}"
    candidate = base; run = 1
    while candidate.exists(): run += 1; candidate = parent / f"{base.name} - Run {run}"
    candidate.mkdir(parents=False, exist_ok=False); return candidate


def run_collection(parent: Path, dut_address: str, scope_address: str, wavegen_address: str,
                   rated_power_w: float, power_levels_w: list[float], cycles: int,
                   progress) -> tuple[int, Path]:
    validate_run_settings(rated_power_w, power_levels_w, cycles)
    run_dir = create_run_folder(parent, dut_address); ctx = RunContext(run_dir); ctx.write_contract()
    expected = len(power_levels_w) * cycles * 2
    ctx.add_configuration(Configuration="Run", DUT_Address=dut_address, Scope_Address=scope_address,
                          Wavegen_Address=wavegen_address, Waveform_Storage="Local Ethernet transfer",
                          Rated_Power_W=rated_power_w, Power_Levels_W=str(power_levels_w),
                          Cycles_Per_Power=cycles, Expected_Captures=expected,
                          Setpoint_Full_Scale_V=SETPOINT_FULL_SCALE_V,
                          Setpoint_Wavegen_Channel=SETPOINT_WG_CHANNEL,
                          RF_Enable_Wavegen_Channel=RF_ENABLE_WG_CHANNEL,
                          Saved_Scope_Channels="CH1,CH2,CH3")
    ctx.write_manifest("initialized", expected)
    inst = Instruments(dut_address, scope_address, wavegen_address); status = "aborted"
    try:
        inst.connect(); ctx.add_event("INFO", "Instrument connection completed")
        for power_index, power_w in enumerate(power_levels_w, 1):
            progress(f"Preparing {power_w:g} W ({power_index}/{len(power_levels_w)})")
            setpoint_v = prepare_power_point(inst, power_w, rated_power_w)
            ctx.add_configuration(Configuration="Power Point", Requested_Power_W=power_w,
                                  Analog_Setpoint_V=setpoint_v, Cycles=cycles)
            for cycle in range(1, cycles + 1):
                progress(f"{power_w:g} W, cycle {cycle}/{cycles}: RF ON")
                capture_edge(inst, ctx, power_w, rated_power_w, setpoint_v, cycle, "ON")
                time.sleep(RF_STABILIZE_S)
                progress(f"{power_w:g} W, cycle {cycle}/{cycles}: RF OFF")
                capture_edge(inst, ctx, power_w, rated_power_w, setpoint_v, cycle, "OFF")
                time.sleep(RECOVERY_S)
                ctx.write_manifest("running", expected, current_power_w=power_w, current_cycle=cycle)
        status = "completed"; ctx.add_event("INFO", "RF Enable timing collection completed"); return 0, run_dir
    except KeyboardInterrupt:
        status = "interrupted"; ctx.add_event("WARNING", "Operator interrupted collection"); return 130, run_dir
    except Exception as exc:
        ctx.add_event("ERROR", "Collection aborted", Error_Type=type(exc).__name__, Error=str(exc)); raise
    finally:
        try: set_dc(inst.wavegen, RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, True) if inst.wavegen is not None else None
        except Exception: pass
        inst.close(); ctx.write_manifest(status, expected)


class CollectionGUI:
    def __init__(self, root: Any):
        import tkinter as tk
        from tkinter import ttk
        self.root=root; self.tk=tk; self.ttk=ttk; self.running=False; self.exit_code=2
        self.parent=tk.StringVar(value=os.environ.get("PARAMOUNT_COLLECTION_PARENT_FOLDER", "") or str(Path.home()))
        self.dut=tk.StringVar(value=DUT_ADDRESS_DEFAULT or ""); self.scope=tk.StringVar(value=SCOPE_ADDRESS_DEFAULT or "")
        self.wavegen=tk.StringVar(value=WAVEGEN_ADDRESS_DEFAULT or "")
        self.rated=tk.StringVar(value=f"{DEFAULT_RATED_POWER_W:g}")
        self.powers=tk.StringVar(value=", ".join(f"{x:g}" for x in DEFAULT_POWER_LEVELS_W))
        self.cycles=tk.StringVar(value=str(DEFAULT_CYCLES_PER_POWER)); self.status=tk.StringVar(value="Ready")
        self._build()

    def _build(self):
        t=self.ttk; r=self.root; r.title("RF Enable Timing Collection"); r.geometry("940x820"); r.minsize(820,700)
        outer=t.Frame(r,padding=14); outer.pack(fill="both",expand=True)
        t.Label(outer,text="RF Enable Timing Collection",font=("Segoe UI",16,"bold")).pack(anchor="w")
        t.Label(outer,text="Capture RF ON and RF OFF timing at each requested power using Scope CH1, CH2, and CH3.").pack(anchor="w",pady=(2,10))
        settings=t.LabelFrame(outer,text="Run Settings",padding=10); settings.pack(fill="x"); settings.columnconfigure(1,weight=1)
        fields=[("Rated power (W)",self.rated),("Power levels (W, comma separated)",self.powers),("Cycles per power",self.cycles),("Results parent folder",self.parent)]
        for row,(label,var) in enumerate(fields):
            t.Label(settings,text=label).grid(row=row,column=0,sticky="w",pady=4); t.Entry(settings,textvariable=var).grid(row=row,column=1,sticky="ew",padx=8)
        t.Button(settings,text="Browse...",command=self._browse).grid(row=3,column=2)
        t.Label(settings,text="Defaults use a 6000 W rating with 4000, 5000, and 6000 W points. The GUI rejects points above the entered rating.",foreground="#555555",wraplength=760).grid(row=5,column=0,columnspan=3,sticky="w",pady=(5,0))
        equipment=t.LabelFrame(outer,text="Equipment Addresses",padding=10); equipment.pack(fill="x",pady=10); equipment.columnconfigure(1,weight=1)
        for row,(label,var) in enumerate((("DUT IP / hostname",self.dut),("Oscilloscope VISA address / IP",self.scope),("Waveform generator VISA address / IP",self.wavegen))):
            t.Label(equipment,text=label).grid(row=row,column=0,sticky="w",pady=4); t.Entry(equipment,textvariable=var).grid(row=row,column=1,sticky="ew",padx=8)
        t.Label(equipment,text="Defaults are inherited from the active unified collection bench profile and may be overridden for this run.",foreground="#555555").grid(row=3,column=0,columnspan=2,sticky="w",pady=(5,0))
        wiring=t.LabelFrame(outer,text="Required Connections",padding=10); wiring.pack(fill="x")
        t.Label(wiring,justify="left",wraplength=860,text=("Waveform generator CH1 to User Port setpoint pins 5/18. CH2 to RF Enable pins 4/17. "
            "Scope CH1 across RF Enable pins 4/17. Scope CH2 to RF Power Monitor pin 3 using its verified return and 50-ohm input. "
            "Scope CH3 to the approved RF detector. Verify load, cooling, interlocks, probe attenuation, grounding, detector limits, and emergency shutdown access.")).pack(anchor="w")
        self.start=t.Button(outer,text="START RF ENABLE TIMING COLLECTION",command=self._start); self.start.pack(fill="x",ipady=10,pady=10)
        box=t.LabelFrame(outer,text="Progress",padding=10); box.pack(fill="both",expand=True)
        self.bar=t.Progressbar(box,mode="indeterminate"); self.bar.pack(fill="x")
        self.log=self.tk.Text(box,height=11,state="disabled",wrap="word"); self.log.pack(fill="both",expand=True,pady=(8,0))
        footer=t.Frame(outer); footer.pack(fill="x",pady=(10,0)); t.Label(footer,textvariable=self.status).pack(side="left")
        self.close=t.Button(footer,text="Close",command=self._close); self.close.pack(side="right"); r.protocol("WM_DELETE_WINDOW",self._close)

    def _browse(self):
        from tkinter import filedialog
        value=filedialog.askdirectory(parent=self.root,initialdir=self.parent.get() or str(Path.home()))
        if value: self.parent.set(value)

    def _say(self,message: str):
        self.log.configure(state="normal"); self.log.insert("end",message+"\n"); self.log.see("end"); self.log.configure(state="disabled")

    def _start(self):
        from tkinter import messagebox
        try:
            rated=float(self.rated.get()); powers=parse_power_levels(self.powers.get()); cycles=int(self.cycles.get()); validate_run_settings(rated,powers,cycles)
        except Exception as exc: messagebox.showerror("Invalid run settings",str(exc),parent=self.root); return
        parent=Path(self.parent.get().strip()).expanduser(); addresses=[self.dut.get().strip(),self.scope.get().strip(),self.wavegen.get().strip()]
        if not parent.is_dir() or any(not x for x in addresses):
            messagebox.showerror("Invalid setup","Select an existing parent folder and enter all equipment addresses.",parent=self.root); return
        expected=len(powers)*cycles*2
        if not messagebox.askyesno("Start RF Enable timing",f"Run {cycles} cycle(s) at {len(powers)} power level(s), totaling {expected} acquisitions?\n\nEach acquisition transfers CH1, CH2, and CH3 over Ethernet to the local run folder. RF will be enabled during the test.",parent=self.root): return
        self.running=True; self.start.configure(state="disabled"); self.close.configure(state="disabled"); self.bar.start(10); self.status.set("Running")
        def worker():
            try:
                code,folder=run_collection(parent,*addresses,rated,powers,cycles,lambda m:self.root.after(0,lambda x=m:self._say(x))); error=None
            except Exception as exc:
                traceback.print_exc(); code=1; folder=None; error=exc
            self.root.after(0,lambda:self._finish(code,folder,error))
        threading.Thread(target=worker,daemon=True).start()

    def _finish(self,code: int,folder: Optional[Path],error: Optional[Exception]):
        from tkinter import messagebox
        self.running=False; self.exit_code=code; self.bar.stop(); self.start.configure(state="normal"); self.close.configure(state="normal")
        if code==0:
            self.status.set("Completed successfully"); self._say(f"Completed successfully: {folder}"); messagebox.showinfo("Collection complete",f"Results saved under:\n{folder}",parent=self.root)
        else:
            self.status.set(f"Stopped with exit code {code}"); self._say(f"ERROR: {error}"); messagebox.showerror("Collection did not complete",str(error or "Collection was interrupted."),parent=self.root)

    def _close(self):
        from tkinter import messagebox
        if self.running: messagebox.showwarning("Collection running","Wait for cleanup to finish before closing.",parent=self.root); return
        self.root.destroy()


def main() -> int:
    import tkinter as tk
    root=tk.Tk(); app=CollectionGUI(root); root.mainloop(); return app.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
