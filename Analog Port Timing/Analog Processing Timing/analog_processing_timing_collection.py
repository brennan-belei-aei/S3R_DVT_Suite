"""Analog Processing Timing data collection GUI.

Captures analog-setpoint transitions and corresponding RF waveform responses.
This standalone child GUI is compatible with the Paramount unified collection
launcher and inherits its DUT, oscilloscope, waveform-generator, and output
folder defaults while allowing per-run overrides.
"""
from __future__ import annotations
import csv
import math
import os
import sys
import time
import traceback
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional
import pyvisa
import numpy as np
import matplotlib.pyplot as plt
import suite_common.bootstrap
from suite_common.bench import bench_default, visa_socket_resource
import AE_Commands_PMNT_HF_1XX
SCOPE_ADDRESS = bench_default('oscilloscope')
WAVEGEN_ADDRESS = bench_default('waveform_generator')
WAVEGEN_SOCKET_FALLBACK_ADDRESS = visa_socket_resource(WAVEGEN_ADDRESS)
BK9141_ADDRESS = bench_default('dc_supply')
BK9141_SOCKET_FALLBACK_ADDRESS = visa_socket_resource(BK9141_ADDRESS)
BK9141_CHANNEL = 1
BK9141_INTERLOCK_VOLTAGE_V = 25.0
BK9141_CURRENT_LIMIT_A = 0.5
BK9141_OUTPUT_SETTLE_S = 0.25
BK9141_VERIFY_TOLERANCE_V = 1.0
BK9141_VOLTAGE_READY_TIMEOUT_S = 5.0
BK9141_VOLTAGE_POLL_INTERVAL_S = 0.05
BK9141_CURRENT_LIMIT_MARGIN_A = 0.01
DUT_ADDRESS = bench_default('generator')
OUTPUT_ROOT = Path(__file__).resolve().parent / 'Results'
OPERATING_FREQUENCY_KHZ = 13560
RATED_POWER_W = 3000.0
# Paramount regulation-mode codes. Forward regulation is required by this test.
REGULATION_FORWARD = 6
REGULATION_DELIVERED = 7
REGULATION_LOAD = 8
DEFAULT_REGULATION_MODE = REGULATION_FORWARD
HOST_MODE = 2
USER_PORT_MODE = 4
HALO_SETPOINT_RESOLUTION = 10
INSTRUMENT_TIMEOUT_MS = 30000
WAVEFORM_TRANSFER_TIMEOUT_MS = 120000
WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES = 25 * 1024 * 1024
COMMAND_SETTLE_S = 0.25
RF_STABILIZE_S = 1.0
SCOPE_ARM_S = 0.35
ACQUISITION_TIMEOUT_S = 10.0
RECOVERY_S = 1.0
RF_ENABLE_LOW_V = 0.0
RF_ENABLE_HIGH_V = 6.0
# Waveform-generator channels and oscilloscope acquisition settings retained
# from the original combined test implementation.
WG_CH_A = 1
WG_CH_B = 2
ANALOG_TIMING_SCOPE_TIME_PER_DIV_S = 1e-3
ANALOG_TIMING_TRIGGER_MODE = "NORM"
PRINT_RECORDED_ROWS = True
SCOPE_RECORD_LENGTH = 100000
SCOPE_HORIZONTAL_POSITION_PERCENT = 35.0
SCOPE_DEFAULT_SCALE_V_DIV = {1: 2.0, 2: 2.0, 3: 2.0, 4: 2.0}
SCOPE_DEFAULT_TERMINATION_OHM = {1: 1000000, 2: 1000000, 3: 1000000, 4: 1000000}
ANALOG_TIMING_INITIAL_STATE_DWELL_S = 2.0
ANALOG_TIMING_SCOPE_ARM_TIMEOUT_S = 5.0
ANALOG_TIMING_SCOPE_ARMED_STATES = ('READY', 'ARMED', 'TRIGGER')
ANALOG_TIMING_CH1_STANDARD_SCALE_V_DIV = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
ANALOG_TIMING_CH1_TARGET_SPAN_DIV = 4.0
ANALOG_TIMING_CH1_VERTICAL_PADDING_FACTOR = 1.1
ANALOG_TIMING_CH1_MIN_SCALE_V_DIV = 0.5
ANALOG_TIMING_CH1_MAX_SCALE_V_DIV = 2.0
ANALOG_TIMING_CH1_POSITION_DIV = 0.0
ANALOG_TIMING_CH1_OFFSET_SIGN = 1.0
ANALOG_TIMING_USE_DYNAMIC_VERTICAL_SCALING = True
ANALOG_TIMING_FIXED_VERTICAL_SCALE_V_DIV = {1: 2.0, 2: 1.0}
ANALOG_TIMING_FIXED_VERTICAL_OFFSET_V = {1: 4.5, 2: 0.0}
# Oscilloscope trigger coupling used for setpoint-edge captures.
RF_MONITOR_TRIGGER_COUPLING = "NOISEREJ"
RF_MONITOR_CH2_TERMINATION_OHM = 50.0
RF_MONITOR_CH2_EXPECTED_VPP_AT_RATED_POWER = 4.0
RF_MONITOR_CH2_STANDARD_SCALE_V_DIV = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
RF_MONITOR_CH2_TARGET_SPAN_DIV = 4.0
RF_MONITOR_CH2_VERTICAL_PADDING_FACTOR = 1.25
RF_MONITOR_CH2_MIN_SCALE_V_DIV = 0.1
RF_MONITOR_CH2_MAX_SCALE_V_DIV = 2.0
RF_MONITOR_CH2_POSITION_DIV = 0.0
RF_MONITOR_CH2_OFFSET_V = 0.0
ANALOG_TIMING_RF_OFF_TIMEOUT_S = 5.0
ANALOG_TIMING_SQUARE_FREQUENCY_HZ = 100.0
ANALOG_TIMING_SQUARE_DUTY_PERCENT = 50.0
ANALOG_TIMING_SQUARE_RISE_START_PHASE_DEG = 180.0
ANALOG_TIMING_SQUARE_FALL_START_PHASE_DEG = 0.0
ANALOG_TIMING_BURST_CYCLES = 1
ANALOG_TIMING_BURST_CONFIGURATION_SETTLE_S = 0.5
TIMING_CONFIG = {
    "ANALOG_TIMING_RISE": dict(
        trigger_ch=1, trigger_level=2.0, slope="RISE",
        trigger_coupling=RF_MONITOR_TRIGGER_COUPLING,
        trigger_mode=ANALOG_TIMING_TRIGGER_MODE,
        time_per_div_s=ANALOG_TIMING_SCOPE_TIME_PER_DIV_S,
        save_channels=(1, 2), require_arm_handshake=True,
        labels={1: "Analog_Setpoint", 2: "RF_Waveform"},
    ),
    "ANALOG_TIMING_FALL": dict(
        trigger_ch=1, trigger_level=2.0, slope="FALL",
        trigger_coupling=RF_MONITOR_TRIGGER_COUPLING,
        trigger_mode=ANALOG_TIMING_TRIGGER_MODE,
        time_per_div_s=ANALOG_TIMING_SCOPE_TIME_PER_DIV_S,
        save_channels=(1, 2), require_arm_handshake=True,
        labels={1: "Analog_Setpoint", 2: "RF_Waveform"},
    ),
}
ANALOG_TIMING_STEPS_V = [(1.0, 3.0), (3.0, 5.0), (5.0, 8.0), (8.0, 5.0), (5.0, 3.0), (3.0, 1.0)]
ANALOG_TIMING_CYCLES = 10
ANALOG_TIMING_SETPOINT_WG_CHANNEL = WG_CH_A
ANALOG_TIMING_RF_ENABLE_WG_CHANNEL = WG_CH_B

@dataclass
class RunContext:
    """Manage separate configuration, result, and event CSV files for one run."""
    test_id: str
    run_dir: Path = field(init=False)
    configuration_rows: list[dict] = field(default_factory=list)
    result_rows: list[dict] = field(default_factory=list)
    event_rows: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        short_test_id = self.test_id.split('_', 1)[0]
        self.run_dir = OUTPUT_ROOT / f'{short_test_id}_{stamp}'
        self.run_dir.mkdir(parents=True, exist_ok=False)
        print(f'Results directory: {self.run_dir}')
        print(f'Results path length: {len(str(self.run_dir))} characters')

    def add_configuration(self, **row) -> None:
        self._add('Run_Configuration', self.configuration_rows, **row)

    def add_result(self, **row) -> None:
        self._add('Cycle_Results', self.result_rows, **row)

    def add_event(self, severity: str='INFO', **row) -> None:
        self._add('Event_Log', self.event_rows, Severity=severity, **row)

    def add(self, **row) -> None:
        """Backward-compatible alias. New measurements default to Cycle Results."""
        self.add_result(**row)

    def _add(self, file_stem: str, rows: list[dict], **row) -> None:
        row = {'Timestamp': datetime.now().isoformat(timespec='milliseconds'), **row}
        rows.append(row)
        path = self._write_csv(file_stem, rows)
        if PRINT_RECORDED_ROWS:
            print(f'\n[RECORDED: {file_stem}]')
            for key, value in row.items():
                print(f'  {key}: {value}')
            print(f'  Local CSV: {path}')

    def _write_csv(self, file_stem: str, rows: list[dict]) -> Path:
        path = self.run_dir / f'{file_stem}.csv'
        self.run_dir.mkdir(parents=True, exist_ok=True)
        fields = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
        with path.open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        return path

class Instruments:

    def __init__(self, connect_bk9141: bool=False) -> None:
        self.rm = None
        self.scope = None
        self.wavegen = None
        self.bk9141 = None
        self.connect_bk9141 = bool(connect_bk9141)
        self.dut = None

    def connect(self) -> None:
        if not SCOPE_ADDRESS:
            raise RuntimeError('Oscilloscope address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_OSCILLOSCOPE_ADDRESS.')
        if not WAVEGEN_ADDRESS:
            raise RuntimeError('Waveform generator address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_WAVEFORM_GENERATOR_ADDRESS.')
        if self.connect_bk9141 and (not BK9141_ADDRESS):
            raise RuntimeError('DC supply (BK 9141) address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_DC_SUPPLY_ADDRESS.')
        if not DUT_ADDRESS:
            raise RuntimeError('DUT generator address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_GENERATOR_ADDRESS.')
        print('Connecting to VISA instruments...')
        self.rm = pyvisa.ResourceManager()
        print(f'VISA backend: {self.rm.visalib}')
        try:
            resources = self.rm.list_resources()
            print(f'VISA resources found: {resources}')
        except Exception as exc:
            print(f'VISA resource-list warning: {exc}')
        self.scope = self.rm.open_resource(SCOPE_ADDRESS)
        self.scope.timeout = INSTRUMENT_TIMEOUT_MS
        self.scope.read_termination = '\n'
        self.scope.write_termination = '\n'
        self.scope.chunk_size = WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES
        print('Scope:', self.scope.query('*IDN?').strip())
        wavegen_errors = []
        for address in (WAVEGEN_ADDRESS, WAVEGEN_SOCKET_FALLBACK_ADDRESS):
            try:
                print(f'Opening wavegen at {address}...')
                candidate = self.rm.open_resource(address)
                candidate.timeout = INSTRUMENT_TIMEOUT_MS
                if address.endswith('::SOCKET'):
                    candidate.read_termination = '\n'
                    candidate.write_termination = '\n'
                identity = candidate.query('*IDN?').strip()
                self.wavegen = candidate
                print(f'Wavegen connected using {address}: {identity}')
                break
            except Exception as exc:
                wavegen_errors.append(f'{address}: {type(exc).__name__}: {exc}')
                print(f'Wavegen connection attempt failed: {wavegen_errors[-1]}')
                try:
                    candidate.close()
                except Exception:
                    pass
        if self.wavegen is None:
            raise RuntimeError('Unable to connect to the wavegen with either VXI-11 INSTR or raw TCP socket port 5025. ' + ' | '.join(wavegen_errors))
        if self.connect_bk9141:
            errors = []
            for address in (BK9141_ADDRESS, BK9141_SOCKET_FALLBACK_ADDRESS):
                candidate = None
                try:
                    print(f'Opening BK Precision 9141 at {address}...')
                    candidate = self.rm.open_resource(address)
                    candidate.timeout = INSTRUMENT_TIMEOUT_MS
                    if address.endswith('::SOCKET'):
                        candidate.read_termination = '\n'
                        candidate.write_termination = '\n'
                    identity = candidate.query('*IDN?').strip()
                    self.bk9141 = candidate
                    print(f'BK supply connected using {address}: {identity}')
                    break
                except Exception as exc:
                    errors.append(f'{address}: {type(exc).__name__}: {exc}')
                    try:
                        if candidate is not None:
                            candidate.close()
                    except Exception:
                        pass
            if self.bk9141 is None:
                raise RuntimeError('Could not connect to BK 9141. ' + ' | '.join(errors))
            configure_bk9141_interlock_supply(self.bk9141)
        print('Connecting to DUT...')
        self.dut = AE_Commands_PMNT_HF_1XX.generator(DUT_ADDRESS)
        print('DUT connected.')

    def safe_state(self) -> None:
        print('Placing equipment in a best-effort safe state...')
        if self.wavegen is not None:
            for ch in (1, 2):
                try:
                    self.wavegen.write(f'C{ch}:BSWV WVTP,DC')
                    self.wavegen.write(f'C{ch}:BSWV OFST,0')
                    self.wavegen.write(f'C{ch}:OUTP OFF')
                except Exception as exc:
                    print(f'Wavegen C{ch} cleanup warning: {exc}')
        if self.bk9141 is not None:
            try:
                bk9141_output(self.bk9141, False, verify_voltage=False)
            except Exception as exc:
                print(f'BK 9141 cleanup warning: {exc}')
        if self.dut is not None:
            try:
                self.dut.setActiveControlMode(HOST_MODE)
                time.sleep(COMMAND_SETTLE_S)
            except Exception:
                pass
            try:
                self.dut.rfOFF()
            except Exception as exc:
                print(f'DUT RF-off cleanup warning: {exc}')
        if self.scope is not None:
            try:
                self.scope.write('ACQ:STATE OFF')
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

def bk9141_select_channel(supply) -> None:
    channel_index = int(BK9141_CHANNEL) - 1
    if channel_index not in (0, 1, 2):
        raise ValueError('BK9141_CHANNEL must be 1, 2, or 3.')
    supply.write(f'INST {channel_index}')

def configure_bk9141_interlock_supply(supply) -> None:
    bk9141_select_channel(supply)
    supply.write('*CLS')
    supply.write('OUTP:STAT OFF')
    supply.write(f'APPL {BK9141_INTERLOCK_VOLTAGE_V:.6f},{BK9141_CURRENT_LIMIT_A:.6f}')
    supply.query('*OPC?')
    print(f"BK 9141 programmed: {supply.query('APPL?').strip()}")

def bk9141_output(supply, enabled: bool, verify_voltage: bool=True) -> dict:
    """Switch the BK output and optionally wait until its voltage is ready.

    The scope may already be armed before an externally triggered stimulus.
    Therefore, polling after OUTP ON does not delay the electrical stimulus or
    its scope trigger. It only prevents a premature software abort while the BK
    measured-voltage reading catches up with the programmed 25 V output.
    """
    bk9141_select_channel(supply)
    command_time = time.monotonic()
    supply.write(f"OUTP:STAT {('ON' if enabled else 'OFF')}")
    supply.query('*OPC?')
    time.sleep(BK9141_OUTPUT_SETTLE_S)
    state = supply.query('OUTP:STAT?').strip().upper()
    expected_states = {'1', 'ON'} if enabled else {'0', 'OFF'}
    if state not in expected_states:
        raise RuntimeError(f'BK output state verification failed: {state!r}')
    result = {'Enabled': bool(enabled), 'Output_State': state, 'Measured_Voltage_V': None, 'Measured_Current_A': None, 'Ready_Time_s': time.monotonic() - command_time}
    if not enabled or not verify_voltage:
        return result
    deadline = time.monotonic() + BK9141_VOLTAGE_READY_TIMEOUT_S
    last_voltage = None
    last_current = None
    sample_number = 0
    while True:
        sample_number += 1
        last_voltage = float(supply.query('MEAS:VOLT?').strip())
        last_current = float(supply.query('MEAS:CURR?').strip())
        elapsed = time.monotonic() - command_time
        print(f'  BK readiness sample {sample_number}: {last_voltage:.3f} V, {last_current:.6f} A, elapsed {elapsed:.3f} s')
        result.update(Measured_Voltage_V=last_voltage, Measured_Current_A=last_current, Ready_Time_s=elapsed)
        if abs(last_voltage - BK9141_INTERLOCK_VOLTAGE_V) <= BK9141_VERIFY_TOLERANCE_V:
            print(f'  BK output reached the valid voltage range in {elapsed:.3f} s.')
            return result
        if time.monotonic() >= deadline:
            near_current_limit = last_current >= max(0.0, BK9141_CURRENT_LIMIT_A - BK9141_CURRENT_LIMIT_MARGIN_A)
            diagnostic = 'Measured current is near the programmed current limit; check for CC operation or excessive loading.' if near_current_limit else 'Measured current is below the programmed current limit; check BK voltage slew/delay settings and wiring.'
            raise RuntimeError(f'BK output did not reach {BK9141_INTERLOCK_VOLTAGE_V:.3f} V +/- {BK9141_VERIFY_TOLERANCE_V:.3f} V within {BK9141_VOLTAGE_READY_TIMEOUT_S:.3f} s. Last measurement: {last_voltage:.3f} V, {last_current:.6f} A. {diagnostic}')
        time.sleep(BK9141_VOLTAGE_POLL_INTERVAL_S)

def require_operator_acknowledgement(test_id: str, wiring: str) -> None:
    print('\n' + '=' * 78)
    print(f'{test_id} PRE-RUN CHECK')
    print(wiring)
    print('Verify probe attenuation, grounds/returns, load, cooling, interlocks,')
    print('power limits, wavegen voltage limits, and emergency shutdown access.')
    answer = input('Type RUN to continue: ').strip().upper()
    if answer != 'RUN':
        raise RuntimeError('Operator cancelled the test.')

def set_dc(wavegen, channel: int, voltage: float, output_on: bool=True) -> None:
    wavegen.write(f'C{channel}:BSWV WVTP,DC')
    wavegen.write(f'C{channel}:BSWV OFST,{float(voltage):.6f}')
    wavegen.write(f"C{channel}:OUTP {('ON' if output_on else 'OFF')}")

def configure_analog_timing_square_burst(wavegen, channel: int, start_v: float, end_v: float, direction: str) -> None:
    """Preload a one-cycle, manual-triggered square burst for one Analog Processing Timing step."""
    low_v, high_v = (min(start_v, end_v), max(start_v, end_v))
    amplitude_vpp = high_v - low_v
    offset_v = (high_v + low_v) / 2.0
    if amplitude_vpp <= 0:
        raise ValueError('Analog Processing Timing square-burst endpoints must differ.')
    phase = ANALOG_TIMING_SQUARE_RISE_START_PHASE_DEG if direction == 'RISE' else ANALOG_TIMING_SQUARE_FALL_START_PHASE_DEG
    wavegen.write(f'C{channel}:BTWV STATE,OFF')
    wavegen.write(f'C{channel}:BSWV WVTP,SQUARE,FRQ,{ANALOG_TIMING_SQUARE_FREQUENCY_HZ:.9f},AMP,{amplitude_vpp:.9f},OFST,{offset_v:.9f},DUTY,{ANALOG_TIMING_SQUARE_DUTY_PERCENT:.6f}')
    wavegen.write(f'C{channel}:BTWV STATE,ON')
    wavegen.write(f'C{channel}:BTWV GATE_NCYC,NCYC')
    wavegen.write(f'C{channel}:BTWV TRSR,MAN')
    wavegen.write(f'C{channel}:BTWV TIME,{ANALOG_TIMING_BURST_CYCLES}')
    wavegen.write(f'C{channel}:BTWV STPS,{phase:.6f}')
    wavegen.write(f'C{channel}:BTWV DLAY,0')
    wavegen.write(f'C{channel}:OUTP ON')
    time.sleep(ANALOG_TIMING_BURST_CONFIGURATION_SETTLE_S)
    print(f'Analog Processing Timing square burst: {start_v:.3f} V -> {end_v:.3f} V, {amplitude_vpp:.3f} Vpp, offset {offset_v:.3f} V, {ANALOG_TIMING_SQUARE_FREQUENCY_HZ:.3f} Hz, phase {phase:.1f} deg')
    try:
        print('  Wavegen burst:', wavegen.query(f'C{channel}:BTWV?').strip())
    except Exception as exc:
        print('  Burst query warning:', exc)

def trigger_analog_timing_square_burst(wavegen, channel: int) -> None:
    wavegen.write(f'C{channel}:BTWV MTRIG')

def disable_analog_timing_square_burst(wavegen, channel: int) -> None:
    wavegen.write(f'C{channel}:BTWV STATE,OFF')

def set_host_configuration(dut, power_w: float, rf_on: bool=False, regulation_mode: Optional[int]=DEFAULT_REGULATION_MODE) -> None:
    dut.setActiveControlMode(HOST_MODE)
    time.sleep(COMMAND_SETTLE_S)
    dut.rfOFF()
    dut.setFixedFrequency(int(OPERATING_FREQUENCY_KHZ))
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

def process_status_bit(dut, byte_index: int, bit_number: int) -> bool:
    """Read one bit from reportProcessStatus()'s four MSB-to-LSB strings."""
    status = dut.reportProcessStatus()
    if not isinstance(status, (list, tuple)) or len(status) != 4:
        raise RuntimeError(f'Unexpected process-status response: {status!r}')
    byte = status[byte_index]
    if isinstance(byte, str):
        bits = byte.strip()
        if len(bits) != 8 or any((ch not in '01' for ch in bits)):
            raise RuntimeError(f'Unexpected process-status byte: {byte!r}')
        return bits[7 - bit_number] == '1'
    value = int(byte)
    return bool(value & 1 << bit_number)

def rf_on_requested(dut) -> bool:
    """FW00408 command 162: d0 bit 6 is RF On Requested."""
    return process_status_bit(dut, 0, 6)

def rf_output_on(dut) -> bool:
    """FW00408 command 162: d0 bit 5 is RF Output On."""
    return process_status_bit(dut, 0, 5)

def wait_for_stable_state(reader: Callable[[], bool], expected: bool, timeout_s: float, consecutive_reads: int=2, poll_s: float=0.05) -> bool:
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

def choose_standard_scale(required_scale: float, standards: Iterable[float], minimum_scale: float, maximum_scale: float) -> float:
    required = max(float(required_scale), float(minimum_scale))
    for candidate in standards:
        if float(candidate) >= required:
            return min(float(candidate), float(maximum_scale))
    return float(maximum_scale)

def calculate_rf_monitor_scale(power_fraction: float) -> float:
    fraction = min(max(float(power_fraction), 0.0), 1.0)
    expected_vpp = RF_MONITOR_CH2_EXPECTED_VPP_AT_RATED_POWER * math.sqrt(fraction)
    required = expected_vpp * RF_MONITOR_CH2_VERTICAL_PADDING_FACTOR / RF_MONITOR_CH2_TARGET_SPAN_DIV
    return choose_standard_scale(required, RF_MONITOR_CH2_STANDARD_SCALE_V_DIV, RF_MONITOR_CH2_MIN_SCALE_V_DIV, RF_MONITOR_CH2_MAX_SCALE_V_DIV)

def build_analog_timing_scope_overrides(start_v: float, end_v: float) -> dict:
    span = abs(float(end_v) - float(start_v))
    ch1_required = span * ANALOG_TIMING_CH1_VERTICAL_PADDING_FACTOR / ANALOG_TIMING_CH1_TARGET_SPAN_DIV
    ch1_scale = choose_standard_scale(ch1_required, ANALOG_TIMING_CH1_STANDARD_SCALE_V_DIV, ANALOG_TIMING_CH1_MIN_SCALE_V_DIV, ANALOG_TIMING_CH1_MAX_SCALE_V_DIV)
    midpoint = (float(start_v) + float(end_v)) / 2.0
    ch2_scale = calculate_rf_monitor_scale(max(float(start_v), float(end_v)) / 10.0)
    return {'channel_scale_v_div': {1: ch1_scale, 2: ch2_scale}, 'channel_position_div': {1: ANALOG_TIMING_CH1_POSITION_DIV, 2: RF_MONITOR_CH2_POSITION_DIV}, 'channel_offset_v': {1: ANALOG_TIMING_CH1_OFFSET_SIGN * midpoint, 2: RF_MONITOR_CH2_OFFSET_V}, 'channel_termination_ohm': {2: RF_MONITOR_CH2_TERMINATION_OHM}}

def build_analog_timing_fixed_scope_overrides() -> dict:
    """Return fixed Analog Processing Timing CH1/CH2 vertical settings selected by the operator."""
    required_channels = {1, 2}
    if set(ANALOG_TIMING_FIXED_VERTICAL_SCALE_V_DIV) != required_channels:
        raise ValueError('ANALOG_TIMING_FIXED_VERTICAL_SCALE_V_DIV must contain exactly channels 1 and 2.')
    if set(ANALOG_TIMING_FIXED_VERTICAL_OFFSET_V) != required_channels:
        raise ValueError('ANALOG_TIMING_FIXED_VERTICAL_OFFSET_V must contain exactly channels 1 and 2.')
    scales = {channel: float(ANALOG_TIMING_FIXED_VERTICAL_SCALE_V_DIV[channel]) for channel in required_channels}
    offsets = {channel: float(ANALOG_TIMING_FIXED_VERTICAL_OFFSET_V[channel]) for channel in required_channels}
    if any((value <= 0 for value in scales.values())):
        raise ValueError('All fixed Analog Processing Timing vertical scales must be greater than zero.')
    return {'channel_scale_v_div': scales, 'channel_position_div': {1: ANALOG_TIMING_CH1_POSITION_DIV, 2: RF_MONITOR_CH2_POSITION_DIV}, 'channel_offset_v': offsets, 'channel_termination_ohm': {2: RF_MONITOR_CH2_TERMINATION_OHM}}

def scope_termination_is_50_ohm(response: str) -> bool:
    text = response.strip().upper().replace('OHM', '')
    if 'FIFTY' in text or text == '50':
        return True
    try:
        value = float(text)
    except ValueError:
        return False
    return 45.0 <= value <= 55.0

def set_scope_channel_termination(scope, channel: int, termination_ohm: float) -> str:
    """Set and verify termination, using Tektronix enum syntax for 50 ohms."""
    if abs(float(termination_ohm) - 50.0) < 1e-09:
        commands = [f'CH{channel}:TERMINATION FIFTY', f'CH{channel}:TERMINATION 50']
    else:
        commands = [f'CH{channel}:TERMINATION MEG', f'CH{channel}:TERMINATION {float(termination_ohm):g}']
    errors = []
    for command in commands:
        try:
            scope.write(command)
            scope.query('*OPC?')
            response = scope.query(f'CH{channel}:TERMINATION?').strip()
            is_expected = scope_termination_is_50_ohm(response) if abs(float(termination_ohm) - 50.0) < 1e-09 else not scope_termination_is_50_ohm(response)
            if is_expected:
                print(f'  CH{channel} termination verified: {response} via {command}')
                return response
            errors.append(f'{command} returned {response!r}')
        except Exception as exc:
            errors.append(f'{command} raised {exc}')
    raise RuntimeError(f'Could not set CH{channel} termination to {termination_ohm:g} ohms. ' + '; '.join(errors) + '. Check whether the installed probe or scope channel supports selectable 50-ohm termination.')

def configure_scope(scope, cfg: dict, time_per_div_s: Optional[float]=None) -> None:
    if time_per_div_s is None:
        time_per_div_s = float(cfg['time_per_div_s'])
    channels = tuple(cfg.get('save_channels', (cfg['trigger_ch'],)))
    scope.write('RECALL:SETUP DEFAULT')
    time.sleep(1.5)
    scope.write('*CLS')
    scope.write('ACQ:STATE OFF')
    for ch in range(1, 5):
        if ch in channels:
            scales = cfg.get('channel_scale_v_div', SCOPE_DEFAULT_SCALE_V_DIV)
            positions = cfg.get('channel_position_div', {})
            offsets = cfg.get('channel_offset_v', {})
            terminations = cfg.get('channel_termination_ohm', {})
            scale = float(scales.get(ch, SCOPE_DEFAULT_SCALE_V_DIV[ch]))
            position = float(positions.get(ch, 0.0))
            offset_v = float(offsets.get(ch, 0.0))
            termination = float(terminations.get(ch, SCOPE_DEFAULT_TERMINATION_OHM[ch]))
            scope.write(f'SEL:CH{ch} ON')
            scope.write(f'CH{ch}:COUP DC')
            verified_termination = set_scope_channel_termination(scope, ch, termination)
            scope.write(f'CH{ch}:SCA {scale}')
            scope.write(f'CH{ch}:POS {position}')
            scope.write(f'CH{ch}:OFFSET {offset_v}')
            print(f'  CH{ch}: {scale:g} V/div, position {position:g} div, offset {offset_v:g} V, termination {verified_termination}')
        else:
            scope.write(f'SEL:CH{ch} OFF')
    scope.write(f'HOR:SCALE {time_per_div_s}')
    scope.write('HORIZONTAL:DELAY:MODE OFF')
    scope.write(f'HORIZONTAL:POSITION {SCOPE_HORIZONTAL_POSITION_PERCENT}')
    try:
        scope.write(f'HORIZONTAL:RECORDLENGTH {SCOPE_RECORD_LENGTH}')
    except Exception as exc:
        print('Record-length warning:', exc)
    scope.write(f"TRIG:A:EDGE:SOU CH{cfg['trigger_ch']}")
    scope.write(f"TRIG:A:LEV:CH{cfg['trigger_ch']} {cfg['trigger_level']}")
    scope.write(f"TRIG:A:EDGE:SLO {cfg['slope']}")
    trigger_coupling = cfg.get('trigger_coupling', 'DC')
    scope.write(f'TRIG:A:EDGE:COUP {trigger_coupling}')
    print(f'  Trigger edge coupling: {trigger_coupling}')
    if 'trigger_mode' in cfg:
        scope.write(f"TRIG:A:MOD {cfg['trigger_mode']}")
    scope.write('ACQ:STOPA SEQ')

def arm_scope(scope) -> None:
    scope.write('*CLS')
    scope.write('ACQ:STOPA SEQ')
    scope.write('ACQ:STATE ON')
    time.sleep(SCOPE_ARM_S)

def arm_scope_and_confirm(scope) -> str:
    scope.write('*CLS')
    scope.write('ACQ:STOPA SEQ')
    scope.write('ACQ:STATE ON')
    deadline = time.monotonic() + ANALOG_TIMING_SCOPE_ARM_TIMEOUT_S
    last = 'UNKNOWN'
    while time.monotonic() < deadline:
        last = scope.query('TRIG:STATE?').strip().upper()
        print(f'  Scope arm state: {last}')
        if last in ANALOG_TIMING_SCOPE_ARMED_STATES:
            time.sleep(SCOPE_ARM_S)
            return last
        time.sleep(0.05)
    raise RuntimeError(f'Analog Processing Timing scope failed to arm; last state={last!r}')

def wait_for_scope(scope, timeout_s: float=ACQUISITION_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = scope.query('TRIG:STATE?').strip().upper()
        if state in {'SAVE', 'STOP'}:
            return True
        time.sleep(0.05)
    return False

def local_waveform_filename(run_dir: Path, test_id: str, descriptor: str, channel: int) -> Path:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    clean = ''.join((c if c.isalnum() or c in '_-' else '_' for c in descriptor))
    return run_dir / f'{test_id}_{clean}_CH{channel}_{stamp}.csv'


def transfer_scope_channel(scope, channel: int, output_path: Path) -> dict:
    """Download one stopped acquisition from the scope over Ethernet."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_suffix(output_path.suffix + '.partial')
    original_timeout = scope.timeout
    try:
        scope.timeout = WAVEFORM_TRANSFER_TIMEOUT_MS
        scope.write('ACQUIRE:STATE STOP')
        scope.write('*WAI')
        scope.write(f'DATA:SOURCE CH{channel}')
        scope.write('DATA:ENCDG RIBINARY')
        scope.write('DATA:WIDTH 2')
        record_length = int(float(scope.query('HORIZONTAL:RECORDLENGTH?')))
        if record_length < 1:
            raise RuntimeError(f'Scope reported invalid record length {record_length}.')
        scope.write('DATA:START 1')
        scope.write(f'DATA:STOP {record_length}')
        xincr = float(scope.query('WFMOUTPRE:XINCR?'))
        xzero = float(scope.query('WFMOUTPRE:XZERO?'))
        pt_off = float(scope.query('WFMOUTPRE:PT_OFF?'))
        ymult = float(scope.query('WFMOUTPRE:YMULT?'))
        yzero = float(scope.query('WFMOUTPRE:YZERO?'))
        yoff = float(scope.query('WFMOUTPRE:YOFF?'))
        xunit = scope.query('WFMOUTPRE:XUNIT?').strip().strip('"') or 's'
        yunit = scope.query('WFMOUTPRE:YUNIT?').strip().strip('"') or 'V'
        raw = np.asarray(scope.query_binary_values(
            'CURVE?', datatype='h', is_big_endian=True,
            container=np.array, expect_termination=False,
        ), dtype=np.float64)
        if raw.size == 0:
            raise RuntimeError(f'No waveform points returned for CH{channel}.')
        index = np.arange(raw.size, dtype=np.float64)
        times = xzero + (index - pt_off) * xincr
        values = (raw - yoff) * ymult + yzero
        np.savetxt(partial_path, np.column_stack((times, values)), delimiter=',',
                   header=f'Time ({xunit}),CH{channel} ({yunit})', comments='', fmt='%.12e')
        partial_path.replace(output_path)
        print(f'Transferred CH{channel}: {raw.size:,} points to {output_path}')
        return {'Path': str(output_path), 'Points': int(raw.size),
                'Sample_Interval_s': xincr, 'X_Unit': xunit, 'Y_Unit': yunit}
    except Exception:
        if partial_path.exists():
            partial_path.unlink()
        raise
    finally:
        scope.timeout = original_timeout


def transfer_scope_channels(scope, run_dir: Path, test_id: str, descriptor: str,
                            channels: Iterable[int]) -> dict[int, dict]:
    channels = tuple(channels)
    if not channels:
        return {}
    invalid = [channel for channel in channels if channel not in (1, 2, 3, 4)]
    if invalid:
        raise ValueError(f'Invalid scope channel selection: {invalid}')
    transferred = {}
    for channel in channels:
        output_path = local_waveform_filename(run_dir, test_id, descriptor, channel)
        print(f'Transferring CH{channel} over Ethernet to {output_path}...')
        transferred[channel] = transfer_scope_channel(scope, channel, output_path)
    return transferred


def timing_capture(inst: Instruments, ctx: RunContext, cfg_key: str, descriptor: str, stimulus: Callable[[], None], trigger_level_override: Optional[float]=None, pre_arm_dwell_s: float=0.0, config_overrides: Optional[dict]=None) -> bool:
    cfg = dict(TIMING_CONFIG[cfg_key])
    if config_overrides:
        cfg.update(config_overrides)
    if trigger_level_override is not None:
        cfg['trigger_level'] = float(trigger_level_override)
    channels = tuple(cfg['save_channels'])
    print(f'\nConfiguring scope for {descriptor}...')
    print(f"Trigger CH{cfg['trigger_ch']} {cfg['slope']} at {cfg['trigger_level']:.3f} V; {cfg['time_per_div_s']:.9g} s/div")
    configure_scope(inst.scope, cfg)
    if pre_arm_dwell_s > 0:
        print(f'Holding initial state {pre_arm_dwell_s:.3f} s...')
        time.sleep(pre_arm_dwell_s)
    print(f'Arming scope for {descriptor}...')
    armed = arm_scope_and_confirm(inst.scope) if cfg.get('require_arm_handshake') else None
    if armed is None:
        arm_scope(inst.scope)
    print(f'Applying stimulus for {descriptor}...')
    stimulus()
    print('Waiting for trigger...')
    captured = wait_for_scope(inst.scope)
    final = inst.scope.query('TRIG:STATE?').strip().upper()
    print(f"Capture {descriptor}: {('triggered' if captured else 'timeout')}, final state {final}")
    files = transfer_scope_channels(inst.scope, ctx.run_dir, ctx.test_id, descriptor, channels=channels) if captured else {}
    row = {'Capture': descriptor, 'Triggered': captured, 'Trigger_Level_V': cfg['trigger_level'], 'Trigger_Slope': cfg['slope'], 'Trigger_Coupling': cfg.get('trigger_coupling', 'DC'), 'Confirmed_Arm_State': armed, 'Final_Trigger_State': final, 'Saved_Channels': ','.join((f'CH{x}' for x in channels)), 'Channel_Labels': str(cfg['labels'])}
    for ch in channels:
        row[f'CH{ch}_File'] = files.get(ch, {}).get('Path')
        row[f'CH{ch}_Points'] = files.get(ch, {}).get('Points')
        row[f'CH{ch}_Sample_Interval_s'] = files.get(ch, {}).get('Sample_Interval_s')
    ctx.add_result(**row)
    return captured

def initialize_analog_processing_timing(inst: Instruments, first_start_v: float) -> None:
    set_dc(inst.wavegen, ANALOG_TIMING_SETPOINT_WG_CHANNEL, first_start_v, False)
    set_dc(inst.wavegen, ANALOG_TIMING_RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, True)

    def off():
        try:
            return not rf_on_requested(inst.dut) and (not rf_output_on(inst.dut))
        except Exception:
            return not bool(inst.dut.isRFon())
    if not wait_for_stable_state(off, True, ANALOG_TIMING_RF_OFF_TIMEOUT_S, 2, 0.05):
        raise RuntimeError('Analog Processing Timing could not confirm RF off before setup.')
    set_host_configuration(inst.dut, RATED_POWER_W, rf_on=False, regulation_mode=None)
    enter_user_port(inst.dut)
    set_dc(inst.wavegen, ANALOG_TIMING_SETPOINT_WG_CHANNEL, first_start_v, True)
    set_dc(inst.wavegen, ANALOG_TIMING_RF_ENABLE_WG_CHANNEL, RF_ENABLE_HIGH_V, True)
    time.sleep(RF_STABILIZE_S)

def run_analog_processing_timing(inst: Instruments) -> None:
    require_operator_acknowledgement('Analog Processing Timing', 'WG C1 -> analog setpoint square-burst steps; WG C2 -> RF Enable held high. Scope CH1 setpoint, CH2 raw RF.')
    ctx = RunContext('ANALOG_TIMING_Analog_Setpoint_Timing')
    vertical_mode = 'Dynamic per transition' if ANALOG_TIMING_USE_DYNAMIC_VERTICAL_SCALING else 'Fixed for complete Analog Processing Timing run'
    ctx.add_configuration(Test='Analog Processing Timing square-burst capture', Steps=str(ANALOG_TIMING_STEPS_V), Frequency_Hz=ANALOG_TIMING_SQUARE_FREQUENCY_HZ, Duty_Percent=ANALOG_TIMING_SQUARE_DUTY_PERCENT, Rise_Phase_deg=ANALOG_TIMING_SQUARE_RISE_START_PHASE_DEG, Fall_Phase_deg=ANALOG_TIMING_SQUARE_FALL_START_PHASE_DEG, Vertical_Scaling_Mode=vertical_mode, Fixed_Vertical_Scale_V_Div=None if ANALOG_TIMING_USE_DYNAMIC_VERTICAL_SCALING else str(ANALOG_TIMING_FIXED_VERTICAL_SCALE_V_DIV), Fixed_Vertical_Offset_V=None if ANALOG_TIMING_USE_DYNAMIC_VERTICAL_SCALING else str(ANALOG_TIMING_FIXED_VERTICAL_OFFSET_V))
    print(f'Analog Processing Timing vertical scaling mode: {vertical_mode}')
    if not ANALOG_TIMING_USE_DYNAMIC_VERTICAL_SCALING:
        print(f'  Fixed scale settings: {ANALOG_TIMING_FIXED_VERTICAL_SCALE_V_DIV}')
        print(f'  Fixed offset settings: {ANALOG_TIMING_FIXED_VERTICAL_OFFSET_V}')
    initialize_analog_processing_timing(inst, float(ANALOG_TIMING_STEPS_V[0][0]))
    try:
        for cycle in range(1, ANALOG_TIMING_CYCLES + 1):
            for start_v, end_v in ANALOG_TIMING_STEPS_V:
                direction = 'RISE' if end_v > start_v else 'FALL'
                midpoint = (start_v + end_v) / 2.0
                descriptor = f'Cycle{cycle}_{start_v:g}V_to_{end_v:g}V_{direction}'
                if ANALOG_TIMING_USE_DYNAMIC_VERTICAL_SCALING:
                    scope_overrides = build_analog_timing_scope_overrides(start_v, end_v)
                else:
                    scope_overrides = build_analog_timing_fixed_scope_overrides()
                print(f"Analog Processing Timing vertical setup ({vertical_mode}): CH1 {scope_overrides['channel_scale_v_div'][1]:g} V/div, offset {scope_overrides['channel_offset_v'][1]:g} V; CH2 {scope_overrides['channel_scale_v_div'][2]:g} V/div, offset {scope_overrides['channel_offset_v'][2]:g} V, requested 50 ohm")
                configure_analog_timing_square_burst(inst.wavegen, ANALOG_TIMING_SETPOINT_WG_CHANNEL, start_v, end_v, direction)
                timing_capture(inst, ctx, f'ANALOG_TIMING_{direction}', descriptor, lambda: trigger_analog_timing_square_burst(inst.wavegen, ANALOG_TIMING_SETPOINT_WG_CHANNEL), trigger_level_override=midpoint, pre_arm_dwell_s=ANALOG_TIMING_INITIAL_STATE_DWELL_S, config_overrides=scope_overrides)
                time.sleep(RECOVERY_S)
    finally:
        try:
            disable_analog_timing_square_burst(inst.wavegen, ANALOG_TIMING_SETPOINT_WG_CHANNEL)
        except Exception as exc:
            print('Analog Processing Timing burst cleanup warning:', exc)
        set_dc(inst.wavegen, ANALOG_TIMING_RF_ENABLE_WG_CHANNEL, RF_ENABLE_LOW_V, True)


TEST_NAME = "Analog Processing Timing"
CONTRACT_NAME = "Paramount.AnalogProcessingTiming"
CONTRACT_VERSION = "1.0.0"
DEFAULT_CYCLES = ANALOG_TIMING_CYCLES
MAX_CYCLES = 100


def _safe(value, fallback):
    import re
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value or '').strip())
    return re.sub(r'\s+', ' ', value).strip(' .') or fallback


def _unique_folder(parent, dut_address):
    stamp=datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    base=parent/f"{stamp} - {TEST_NAME} - {_safe(dut_address,'Generator')}"
    candidate=base; index=1
    while candidate.exists():
        index+=1; candidate=parent/f"{base.name} - Run {index}"
    candidate.mkdir(parents=False,exist_ok=False)
    return candidate


def _run_collection(parent, cycles, dynamic_scaling, dut_address, scope_address, wavegen_address):
    global OUTPUT_ROOT, ANALOG_TIMING_CYCLES, ANALOG_TIMING_USE_DYNAMIC_VERTICAL_SCALING
    global DUT_ADDRESS, SCOPE_ADDRESS, WAVEGEN_ADDRESS, WAVEGEN_SOCKET_FALLBACK_ADDRESS
    OUTPUT_ROOT=parent
    ANALOG_TIMING_CYCLES=cycles
    ANALOG_TIMING_USE_DYNAMIC_VERTICAL_SCALING=dynamic_scaling
    DUT_ADDRESS=dut_address
    SCOPE_ADDRESS=scope_address
    WAVEGEN_ADDRESS=wavegen_address
    WAVEGEN_SOCKET_FALLBACK_ADDRESS=visa_socket_resource(wavegen_address)
    # Preserve established descriptive timestamp folder contract by supplying a
    # dedicated RunContext subclass only for this child process.
    original_post=RunContext.__post_init__
    def descriptive_post(self):
        self.run_dir=_unique_folder(parent,dut_address)
        print(f"Results directory: {self.run_dir}")
    RunContext.__post_init__=descriptive_post
    inst=Instruments()
    try:
        inst.connect()
        run_analog_processing_timing(inst)
        return 0
    finally:
        inst.close()
        RunContext.__post_init__=original_post


class AnalogProcessingTimingGUI:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        self.root=root; self.tk=tk; self.ttk=ttk; self.running=False; self.exit_code=2
        self.cycles=tk.StringVar(value=str(DEFAULT_CYCLES))
        self.scaling=tk.StringVar(value='dynamic')
        self.parent=tk.StringVar(value=os.environ.get('PARAMOUNT_COLLECTION_PARENT_FOLDER','') or str(Path.home()))
        self.dut=tk.StringVar(value=DUT_ADDRESS or '')
        self.scope=tk.StringVar(value=SCOPE_ADDRESS or '')
        self.wavegen=tk.StringVar(value=WAVEGEN_ADDRESS or '')
        self.status=tk.StringVar(value='Ready')
        self._build()

    def _build(self):
        ttk=self.ttk; r=self.root
        r.title('Analog Processing Timing Collection'); r.geometry('920x760'); r.minsize(800,650)
        o=ttk.Frame(r,padding=14); o.pack(fill='both',expand=True)
        ttk.Label(o,text='Analog Processing Timing Collection',font=('Segoe UI',16,'bold')).pack(anchor='w')
        ttk.Label(o,text='Capture analog setpoint transitions on Scope CH1 and the RF waveform response on Scope CH2.').pack(anchor='w',pady=(2,10))
        s=ttk.LabelFrame(o,text='Run Settings',padding=10); s.pack(fill='x'); s.columnconfigure(1,weight=1)
        ttk.Label(s,text='Complete cycles').grid(row=0,column=0,sticky='w',pady=4)
        ttk.Spinbox(s,from_=1,to=MAX_CYCLES,textvariable=self.cycles,width=10).grid(row=0,column=1,sticky='w',padx=8)
        ttk.Label(s,text='Vertical scaling').grid(row=1,column=0,sticky='w',pady=4)
        ttk.Radiobutton(s,text='Dynamic per transition',variable=self.scaling,value='dynamic').grid(row=1,column=1,sticky='w')
        ttk.Radiobutton(s,text='Fixed for complete run',variable=self.scaling,value='fixed').grid(row=2,column=1,sticky='w')
        ttk.Label(s,text='Results parent folder').grid(row=3,column=0,sticky='w',pady=4)
        ttk.Entry(s,textvariable=self.parent).grid(row=3,column=1,sticky='ew',padx=8); ttk.Button(s,text='Browse...',command=self._browse).grid(row=3,column=2)
        eq=ttk.LabelFrame(o,text='Equipment Addresses',padding=10); eq.pack(fill='x',pady=10); eq.columnconfigure(1,weight=1)
        for row,(label,var) in enumerate([('DUT IP / hostname',self.dut),('Oscilloscope VISA address / IP',self.scope),('Waveform generator VISA address / IP',self.wavegen)]):
            ttk.Label(eq,text=label).grid(row=row,column=0,sticky='w',pady=4); ttk.Entry(eq,textvariable=var).grid(row=row,column=1,sticky='ew',padx=8)
        ttk.Label(eq,text='Defaults are inherited from the active unified collection bench profile and may be overridden for this run.',foreground='#555555').grid(row=3,column=0,columnspan=2,sticky='w',pady=(5,0))
        check=ttk.LabelFrame(o,text='Required Connections',padding=10); check.pack(fill='x')
        ttk.Label(check,justify='left',wraplength=850,text='Waveform generator CH1 to User Port setpoint input (pins 5/18). Waveform generator CH2 to RF Enable (pins 4/17). Scope CH1 across setpoint pins 5/18. Scope CH2 to RF Power Monitor pin 3 using the verified monitor return and 50-ohm scope input. Verify load, cooling, interlocks, probe attenuation, and emergency shutdown access.').pack(anchor='w')
        self.start=ttk.Button(o,text='START ANALOG PROCESSING TIMING COLLECTION',command=self._start); self.start.pack(fill='x',ipady=10,pady=10)
        p=ttk.LabelFrame(o,text='Progress',padding=10); p.pack(fill='both',expand=True)
        self.bar=ttk.Progressbar(p,mode='indeterminate'); self.bar.pack(fill='x')
        self.log=self.tk.Text(p,height=11,state='disabled',wrap='word'); self.log.pack(fill='both',expand=True,pady=(8,0))
        f=ttk.Frame(o); f.pack(fill='x',pady=(10,0)); ttk.Label(f,textvariable=self.status).pack(side='left'); self.close=ttk.Button(f,text='Close',command=self._close); self.close.pack(side='right')
        r.protocol('WM_DELETE_WINDOW',self._close)

    def _browse(self):
        from tkinter import filedialog
        v=filedialog.askdirectory(parent=self.root,initialdir=self.parent.get() or str(Path.home()))
        if v:self.parent.set(v)

    def _say(self,msg):
        self.log.configure(state='normal'); self.log.insert('end',msg+'\n'); self.log.see('end'); self.log.configure(state='disabled')

    def _start(self):
        from tkinter import messagebox
        try: cycles=int(self.cycles.get()); assert 1<=cycles<=MAX_CYCLES
        except Exception: messagebox.showerror('Invalid cycles',f'Enter a whole number from 1 through {MAX_CYCLES}.',parent=self.root); return
        parent=Path(self.parent.get().strip()).expanduser()
        addresses=[self.dut.get().strip(),self.scope.get().strip(),self.wavegen.get().strip()]
        if not parent.is_dir() or any(not x for x in addresses): messagebox.showerror('Invalid setup','Select an existing results folder and enter all three equipment addresses.',parent=self.root); return
        captures=cycles*len(ANALOG_TIMING_STEPS_V)
        if not messagebox.askyesno('Start collection',f'Run {cycles} complete cycle(s), totaling {captures} triggered captures?\n\nRF will be enabled during this test.',parent=self.root): return
        self.running=True; self.start.configure(state='disabled'); self.close.configure(state='disabled'); self.bar.start(10); self.status.set('Running'); self._say(f'Starting {cycles} cycle(s), {captures} captures.')
        def worker():
            try: code=_run_collection(parent,cycles,self.scaling.get()=='dynamic',*addresses); err=None
            except Exception as exc: traceback.print_exc(); code=1; err=exc
            self.root.after(0,lambda:self._finish(code,err))
        threading.Thread(target=worker,daemon=True).start()

    def _finish(self,code,error):
        from tkinter import messagebox
        self.running=False; self.exit_code=code; self.bar.stop(); self.start.configure(state='normal'); self.close.configure(state='normal')
        if code==0: self.status.set('Completed successfully'); self._say('Collection completed successfully.'); messagebox.showinfo('Collection complete','Analog processing timing collection completed successfully.',parent=self.root)
        else: self.status.set('Collection failed'); self._say(f'ERROR: {error}'); messagebox.showerror('Collection failed',str(error),parent=self.root)

    def _close(self):
        from tkinter import messagebox
        if self.running: messagebox.showwarning('Collection running','Wait for collection cleanup to finish before closing.',parent=self.root); return
        self.root.destroy()


def main():
    import tkinter as tk
    root=tk.Tk(); app=AnalogProcessingTimingGUI(root); root.mainloop(); return app.exit_code


if __name__=='__main__':
    raise SystemExit(main())
