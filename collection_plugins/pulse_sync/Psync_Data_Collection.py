"""Master/slave pulsing sweep with Tektronix MSO44 captures.

Safety defaults:
  * DRY_RUN is True, so no instruments are contacted.
  * ENABLE_RF is False, so this script never commands RF on unless edited.

Expected local modules:
  * AE_Commands_7434753.py
  * AE_Com_Module and fixedpoint dependencies used by AE_Commands_7434753.py
"""
from __future__ import annotations

import csv
import logging
import os
import re
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.output_folders import create_unique_folder, path_component  # noqa: E402
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default

from suite_instruments.factory import InstrumentFactory
from suite_instruments.tektronix import read_waveform
from suite_instruments.transport.visa import resource_manager

# Both generators keep the 7434753 command set (AE_Commands_7434753), not the default AECommandsUniversal.
PSYNC_ADAPTERS = {"generator": "ae.7434753", "psync_slave_generator": "ae.7434753"}


# ----------------------- integrated Tektronix MSO44 helpers -----------------------
DEFAULT_RESOURCE = bench_default("oscilloscope")
BASE_OUTPUT_DIRECTORY = Path(r"C:\TekWaveforms")
TIMEOUT_MS = 120_000
CHANNELS = ("CH1", "CH2", "CH3", "CH4")


def _channel(channel: int | str) -> str:
    text = str(channel).strip().upper()
    if text.isdigit():
        text = f"CH{text}"
    if text not in CHANNELS:
        raise ValueError(f"channel must be 1-4 or CH1-CH4, not {channel!r}")
    return text


@contextmanager
def _session(resource: str = DEFAULT_RESOURCE) -> Iterator[Any]:
    rm = resource_manager()
    instrument = None
    try:
        instrument = InstrumentFactory({"oscilloscope": resource}).create(
            "oscilloscope", resource_manager=rm, open_attempts=1)
        instrument.timeout = TIMEOUT_MS
        instrument.read_termination = "\n"
        instrument.write_termination = "\n"
        instrument.chunk_size = 25 * 1024 * 1024
        yield instrument
    finally:
        if instrument is not None:
            instrument.close()
        rm.close()


def _write(command: str, resource: str = DEFAULT_RESOURCE) -> None:
    with _session(resource) as instrument:
        instrument.write(command)
        instrument.query("*OPC?")


def enable_channel(channel: int | str, resource: str = DEFAULT_RESOURCE) -> None:
    """Turn on an analog channel display."""
    ch = _channel(channel)
    _write(f"DISPLAY:WAVEVIEW1:{ch}:STATE ON", resource)


def disable_channel(channel: int | str, resource: str = DEFAULT_RESOURCE) -> None:
    """Turn off an analog channel display."""
    ch = _channel(channel)
    _write(f"DISPLAY:WAVEVIEW1:{ch}:STATE OFF", resource)


def set_channel_enabled(
    channel: int | str, enabled: bool, resource: str = DEFAULT_RESOURCE
) -> None:
    """Enable or disable an analog channel."""
    enable_channel(channel, resource) if enabled else disable_channel(channel, resource)


def set_vertical_scale(
    channel: int | str,
    volts_per_division: float,
    position_divisions: float | None = None,
    resource: str = DEFAULT_RESOURCE,
) -> dict[str, float]:
    """Set channel volts/div and optional vertical position in divisions."""
    ch = _channel(channel)
    if volts_per_division <= 0:
        raise ValueError("volts_per_division must be greater than zero")
    with _session(resource) as instrument:
        instrument.write(f"{ch}:SCALE {float(volts_per_division):.12g}")
        if position_divisions is not None:
            instrument.write(f"{ch}:POSITION {float(position_divisions):.12g}")
        instrument.query("*OPC?")
        return {
            "scale": float(instrument.query(f"{ch}:SCALE?")),
            "position": float(instrument.query(f"{ch}:POSITION?")),
        }


def set_horizontal_scale(
    seconds_per_division: float,
    position_percent: float | None = None,
    resource: str = DEFAULT_RESOURCE,
) -> dict[str, float]:
    """Set the main timebase in seconds/div and optional trigger position percent."""
    if seconds_per_division <= 0:
        raise ValueError("seconds_per_division must be greater than zero")
    with _session(resource) as instrument:
        instrument.write("HORIZONTAL:MODE AUTO")
        instrument.write(f"HORIZONTAL:MODE:SCALE {float(seconds_per_division):.12g}")
        if position_percent is not None:
            if not 0 <= position_percent <= 100:
                raise ValueError("position_percent must be from 0 through 100")
            instrument.write(f"HORIZONTAL:POSITION {float(position_percent):.12g}")
        instrument.query("*OPC?")
        return {
            "scale": float(instrument.query("HORIZONTAL:MODE:SCALE?")),
            "position": float(instrument.query("HORIZONTAL:POSITION?")),
            "sample_rate": float(instrument.query("HORIZONTAL:MODE:SAMPLERATE?")),
            "record_length": float(instrument.query("HORIZONTAL:MODE:RECORDLENGTH?")),
        }


def setup_edge_trigger(
    channel: int | str,
    level: float,
    slope: str = "RISE",
    mode: str = "NORMAL",
    coupling: str = "DC",
    resource: str = DEFAULT_RESOURCE,
) -> dict[str, str | float]:
    """Configure the A trigger as an edge trigger on an analog channel."""
    ch = _channel(channel)
    slope = slope.strip().upper()
    mode = mode.strip().upper()
    coupling = coupling.strip().upper()
    if slope not in {"RISE", "FALL", "EITHER"}:
        raise ValueError("slope must be RISE, FALL, or EITHER")
    if mode not in {"AUTO", "NORMAL"}:
        raise ValueError("mode must be AUTO or NORMAL")
    with _session(resource) as instrument:
        instrument.write("TRIGGER:A:TYPE EDGE")
        instrument.write(f"TRIGGER:A:EDGE:SOURCE {ch}")
        instrument.write(f"TRIGGER:A:EDGE:SLOPE {slope}")
        instrument.write(f"TRIGGER:A:EDGE:COUPLING {coupling}")
        instrument.write(f"TRIGGER:A:LEVEL:{ch} {float(level):.12g}")
        instrument.write(f"TRIGGER:A:MODE {mode}")
        instrument.query("*OPC?")
        return {
            "source": instrument.query("TRIGGER:A:EDGE:SOURCE?").strip(),
            "level": float(instrument.query(f"TRIGGER:A:LEVEL:{ch}?")),
            "slope": instrument.query("TRIGGER:A:EDGE:SLOPE?").strip(),
            "mode": instrument.query("TRIGGER:A:MODE?").strip(),
            "coupling": instrument.query("TRIGGER:A:EDGE:COUPLING?").strip(),
        }


def arm(
    single: bool = True,
    resource: str = DEFAULT_RESOURCE,
) -> None:
    """Arm the scope. By default, stop after one triggered acquisition."""
    with _session(resource) as instrument:
        instrument.write(f"ACQUIRE:STOPAFTER {'SEQUENCE' if single else 'RUNSTOP'}")
        instrument.write("ACQUIRE:STATE RUN")
        instrument.query("*OPC?")


def stop(resource: str = DEFAULT_RESOURCE) -> None:
    """Stop acquisition."""
    _write("ACQUIRE:STATE STOP", resource)


def wait_for_acquisition(
    timeout_sec: float = 30.0,
    poll_interval_sec: float = 0.05,
    resource: str = DEFAULT_RESOURCE,
) -> bool:
    """Wait until a single acquisition stops; raise TimeoutError on timeout."""
    deadline = time.monotonic() + timeout_sec
    with _session(resource) as instrument:
        while time.monotonic() < deadline:
            state = instrument.query("ACQUIRE:STATE?").strip().upper()
            if state in {"0", "OFF", "STOP"}:
                return True
            time.sleep(poll_interval_sec)
    raise TimeoutError(f"Scope acquisition did not complete within {timeout_sec:g} s")


# def check_scope_errors(instrument: Any) -> list[str]:
#     errors = []
#     for _ in range(20):
#         response = instrument.query("SYSTEM:ERROR?").strip()
#         if response.startswith("0"):
#             break
#         errors.append(response)
#     return errors
#

def parse_ieee4882_block(data: bytes) -> bytes:
    data = data.lstrip()
    if not data.startswith(b"#"):
        return data.rstrip(b"\r\n")
    digits = int(chr(data[1]))
    if digits == 0:
        return data[2:].rstrip(b"\r\n")
    end = 2 + digits
    length = int(data[2:end].decode("ascii"))
    if len(data) < end + length:
        raise ValueError("Incomplete IEEE 488.2 binary block")
    return data[end:end + length]


def capture(
    filename: str | Path,
    resource: str = DEFAULT_RESOURCE,
    base_directory: str | Path = BASE_OUTPUT_DIRECTORY,
) -> dict[str, Any]:
    """Save four CSVs, combined plot, screenshot, and capture information."""
    name = Path(filename)
    output = name if name.is_absolute() else Path(base_directory) / name
    output.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    captured = {}
    started = datetime.now()
    with _session(resource) as instrument:
        identity = instrument.query("*IDN?").strip()
        instrument.write("ACQUIRE:STATE STOP")
        instrument.query("*OPC?")
        for ch in CHANNELS:
            times, values, meta = read_waveform(instrument, ch)
            captured[ch] = (times, values, meta)
            path = output / f"{ch}.csv"
            np.savetxt(path, np.column_stack((times, values)), delimiter=",",
                       header=f"Time ({meta['xunit'] or 's'}),{ch} ({meta['yunit'] or 'V'})",
                       comments="", fmt="%.12e")
            files[ch] = path
        errors = None
        files["info"] = output / "capture_info.txt"
        with files["info"].open("w", encoding="utf-8") as f:
            f.write(f"Instrument: {identity}\nCaptured: {started.isoformat(timespec='seconds')}\n")
            f.write(f"VISA resource: {resource}\nOutput directory: {output}\n\n")
            for ch in CHANNELS:
                f.write(f"{ch}: {captured[ch][2]}\n")
            f.write(f"\nScope errors: {errors or 'None reported'}\n")
    return {"instrument": identity, "output_directory": output, "files": files,
            "metadata": {ch: captured[ch][2] for ch in CHANNELS}, "scope_errors": errors}


# Expose the integrated scope helpers through the original scope.* interface so the
# collection engine remains unchanged while scope1.py is no longer required.
scope = SimpleNamespace(
    DEFAULT_RESOURCE=DEFAULT_RESOURCE,
    enable_channel=enable_channel,
    disable_channel=disable_channel,
    set_channel_enabled=set_channel_enabled,
    set_vertical_scale=set_vertical_scale,
    set_horizontal_scale=set_horizontal_scale,
    setup_edge_trigger=setup_edge_trigger,
    arm=arm,
    stop=stop,
    wait_for_acquisition=wait_for_acquisition,
    parse_ieee4882_block=parse_ieee4882_block,
    capture=capture,
)


# ------------------------------- test configuration -------------------------------
MASTER_ADDRESS = bench_default("generator")
SLAVE_ADDRESS = bench_default("psync_slave_generator")
SCOPE_RESOURCE = scope.DEFAULT_RESOURCE
STANDALONE_DEFAULT_OUTPUT_PARENT = BASE_OUTPUT_DIRECTORY
OUTPUT_ROOT = Path(
    os.environ.get("PARAMOUNT_COLLECTION_PARENT_FOLDER", str(STANDALONE_DEFAULT_OUTPUT_PARENT)).strip()
    or STANDALONE_DEFAULT_OUTPUT_PARENT
)
TEST_FOLDER_NAME = "Pulse Sync"

DRY_RUN = True
ENABLE_RF = False
CONTINUE_ON_ERROR = True
SKIP_COMPLETED = True
SETTLE_TIME_SEC = 0.25
ACQUISITION_TIMEOUT_SEC = 30.0

RF_FREQUENCIES_MHZ = [12.882, 13.56, 14.238]

POWER_SETPOINTS_W = [15, 50, 100, 500, 1000, 2000, 4000, 6000]

PULSE_FREQUENCIES_HZ = [2, 10, 100, 1000, 10000, 100000]

# Requested duty cycles are fractions: 0.01 = 1%, 0.50 = 50%, 0.99 = 99%.
# Values outside the product-specification limits for a pulse frequency are skipped.
REQUESTED_DUTY_CYCLES = [0.01, 0.10, 0.20, 0.40, 0.50, 0.60, 0.80, 0.90, 0.99]
SAMPLES_PER_CONDITION = 10

# Capture views are not all unique trigger edges. FULL_PULSE uses a rising-edge
# trigger with a wider timebase, while RISE and FALL are edge close-ups.
TRIGGER_VIEWS = ["RISE", "FALL", "FULL_PULSE"]
TRIGGER_SLOPE_BY_VIEW = {
    "RISE": "RISE",
    "FALL": "FALL",
    "FULL_PULSE": "RISE",
}

MIN_PULSE_FREQUENCY_HZ = 2.0
MAX_PULSE_FREQUENCY_HZ = 100_000.0
MIN_PULSE_TIME_SEC = 5e-6

# Bench wiring differs (e.g. Zynq bench uses CH4); editable on the Instruments tab.
TRIGGER_CHANNEL = int(os.environ.get("PARAMOUNT_PSYNC_TRIGGER_CHANNEL", "3"))
TRIGGER_LEVEL_V = 0.5
VERTICAL_SCALE_CHANNELS = (1, 2)
HORIZONTAL_PERIODS_ON_SCREEN = .00025
FULL_PULSE_PERIODS_ON_SCREEN = 2.5

# CMD 26 subcommand 1 values in this library. Verify these against the unit setup.
PULSE_MODE_MASTER = 1
PULSE_MODE_SLAVE = 2
RUNTIME_DUTY_LIMITS = None


@dataclass(frozen=True)
class TestPoint:
    rf_mhz: float
    power_w: int
    pulse_hz: int
    duty_fraction: float
    sample: int
    view: str

    @property
    def duty_percent(self) -> int:
        # The AE library expects whole percent, not a 0..1 fraction.
        return int(round(self.duty_fraction * 100.0))

    @property
    def capture_name(self) -> str:
        raw = (
            f"rf_{self.rf_mhz:.3f}MHz"
            f"__pwr_{self.power_w:04d}W"
            f"__pulse_{self.pulse_hz:06d}Hz"
            f"__duty_{self.duty_percent:02d}pct"
            f"__{self.view.lower()}"
            f"__sample_{self.sample:02d}"
        )
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)


def duty_cycle_limits(pulse_hz: float) -> tuple[float, float]:
    """Return the allowed duty-cycle range as fractions for pulse_hz."""
    pulse_hz = float(pulse_hz)

    if not MIN_PULSE_FREQUENCY_HZ <= pulse_hz <= MAX_PULSE_FREQUENCY_HZ:
        raise ValueError(
            f"Pulse frequency {pulse_hz:g} Hz is outside the specified "
            f"{MIN_PULSE_FREQUENCY_HZ:g} Hz to "
            f"{MAX_PULSE_FREQUENCY_HZ:g} Hz range."
        )

    if pulse_hz == 2.0:
        return 0.50, 0.50
    if pulse_hz == 3.0:
        return 0.24, 0.76
    if pulse_hz <= 2000.0:
        return 0.01, 0.99

    # Above 2 kHz, both the on-time and off-time must be at least 5 us.
    minimum_duty = pulse_hz * MIN_PULSE_TIME_SEC
    maximum_duty = 1.0 - minimum_duty

    if minimum_duty > maximum_duty:
        raise ValueError(
            f"No valid duty cycle exists at {pulse_hz:g} Hz with a "
            f"{MIN_PULSE_TIME_SEC * 1e6:g} us minimum on/off time."
        )

    return minimum_duty, maximum_duty


def duty_cycles_for_frequency(pulse_hz: float, requested_duty_cycles=REQUESTED_DUTY_CYCLES,
                              reported_min_percent=None, reported_max_percent=None) -> list[float]:
    """Intersect timing-derived limits with connected-generator reported limits."""
    calculated_minimum, calculated_maximum = duty_cycle_limits(pulse_hz)
    minimum_duty = max(calculated_minimum, (reported_min_percent or 0) / 100.0)
    maximum_duty = min(calculated_maximum, (reported_max_percent if reported_max_percent is not None else 99) / 100.0)
    if minimum_duty > maximum_duty:
        raise ValueError(f"No common duty cycle at {pulse_hz:g} Hz; effective range is empty.")
    valid=[]
    for duty in requested_duty_cycles:
        duty=float(duty)
        if not 0.0 < duty < 1.0:raise ValueError(f"Duty cycle must be between 0 and 1, exclusive; got {duty!r}.")
        if minimum_duty <= duty <= maximum_duty:valid.append(duty)
        else:logging.warning("Skipping unsupported duty cycle %.3f%% at %.3f Hz; effective range is %.3f%% to %.3f%%.",duty*100,pulse_hz,minimum_duty*100,maximum_duty*100)
    if not valid:raise ValueError(f"None of the requested duty cycles are valid at {pulse_hz:g} Hz; effective range is {minimum_duty*100:.3f}% to {maximum_duty*100:.3f}%.")
    return valid


def iter_test_points(runtime_limits=None) -> Iterable[TestPoint]:
    reported_min=runtime_limits['effective_min_percent'] if runtime_limits else None
    reported_max=runtime_limits['effective_max_percent'] if runtime_limits else None
    by_frequency={pulse_hz:duty_cycles_for_frequency(pulse_hz,REQUESTED_DUTY_CYCLES,reported_min,reported_max) for pulse_hz in dict.fromkeys(PULSE_FREQUENCIES_HZ)}
    for rf_mhz in RF_FREQUENCIES_MHZ:
        for power_w in POWER_SETPOINTS_W:
            for pulse_hz in PULSE_FREQUENCIES_HZ:
                for duty_fraction in by_frequency[pulse_hz]:
                    for sample in range(1,SAMPLES_PER_CONDITION+1):
                        for view in TRIGGER_VIEWS:yield TestPoint(rf_mhz,power_w,pulse_hz,duty_fraction,sample,view)


def query_generator_duty_limits(master,slave)->dict:
    limits={'master_min_percent':int(master.reportPulsingConfiguration(16)),'master_max_percent':int(master.reportPulsingConfiguration(17)),'slave_min_percent':int(slave.reportPulsingConfiguration(16)),'slave_max_percent':int(slave.reportPulsingConfiguration(17))}
    for name,value in limits.items():
        if not 0 <= value <= 99:raise ValueError(f"Generator reported invalid {name}: {value}")
    limits['effective_min_percent']=max(limits['master_min_percent'],limits['slave_min_percent']);limits['effective_max_percent']=min(limits['master_max_percent'],limits['slave_max_percent'])
    if limits['effective_min_percent']>limits['effective_max_percent']:raise ValueError(f"Master/slave duty-cycle ranges do not overlap: {limits}")
    logging.info("Reported duty limits: master=%d%%..%d%% slave=%d%%..%d%% common=%d%%..%d%%",limits['master_min_percent'],limits['master_max_percent'],limits['slave_min_percent'],limits['slave_max_percent'],limits['effective_min_percent'],limits['effective_max_percent'])
    return limits


def configure_pulse_sync_roles(master,slave)->None:
    for generator in (master,slave):generator.rfOFF();generator.enablePulsing()
    master.setPulsingConfiguration(1,PULSE_MODE_MASTER);master.setPulsingConfiguration(2,1);slave.setPulsingConfiguration(1,PULSE_MODE_SLAVE)
    master_mode=master.reportPulsingConfiguration(1);slave_mode=slave.reportPulsingConfiguration(1);sync_output=master.reportPulsingConfiguration(2)
    if master_mode!=PULSE_MODE_MASTER or slave_mode!=PULSE_MODE_SLAVE or sync_output!=1:raise RuntimeError(f"Pulse Sync role readback failed: master_mode={master_mode}, slave_mode={slave_mode}, master_sync_output={sync_output}")
    logging.info("Pulse Sync roles configured: master mode=%s sync output=%s; slave mode=%s",master_mode,sync_output,slave_mode)


def append_runtime_duty_limits(run_directory:Path,limits)->None:
    rows=[]
    if limits:rows.extend((name,value) for name,value in limits.items())
    allowed={pulse_hz:[round(value*100,6) for value in duty_cycles_for_frequency(pulse_hz,REQUESTED_DUTY_CYCLES,limits['effective_min_percent'] if limits else None,limits['effective_max_percent'] if limits else None)] for pulse_hz in dict.fromkeys(PULSE_FREQUENCIES_HZ)}
    rows.append(('effective_duty_cycles_by_frequency_percent',allowed))
    with (run_directory/'Psync_Run_Settings.csv').open('a',newline='',encoding='utf-8') as handle:csv.writer(handle).writerows(rows)

def vertical_scale_from_power(power_w: float) -> float:
    """Return V/div from the previously defined power-to-scale schedule."""
    if power_w <= 250:
        return .025
    if power_w <= 500:
        return 0.05
    if power_w <= 2000:
        return 0.10
    if power_w <= 4000:
        return 0.15
    return 0.20


def horizontal_scale_from_pulse_frequency(pulse_hz: float) -> float:
    """Fit the requested number of periods across the scope's 10 divisions."""
    if pulse_hz <= 0:
        raise ValueError("pulse_hz must be greater than zero")
    return HORIZONTAL_PERIODS_ON_SCREEN / (10.0 * pulse_hz)

def full_horizontal_scale(pulse_hz: float) -> float:
    if pulse_hz <= 0:
        raise ValueError("pulse_hz must be greater than zero")
    return FULL_PULSE_PERIODS_ON_SCREEN / (10.0 * pulse_hz)

def configure_logging(run_directory: Path) -> None:
    run_directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(run_directory / "run.log", encoding="utf-8"),
        ],
    )


def configure_scope(point: TestPoint) -> None:
    volts_per_div = vertical_scale_from_power(point.power_w)
    trigger_slope = TRIGGER_SLOPE_BY_VIEW[point.view]
    if point.view == "FULL_PULSE":
        seconds_per_div = full_horizontal_scale(point.pulse_hz)
    else:
        seconds_per_div = horizontal_scale_from_pulse_frequency(point.pulse_hz)
    if DRY_RUN:
        logging.info(
            "DRY RUN scope: %.3g V/div, %.3g s/div, view=%s, slope=%s",
            volts_per_div, seconds_per_div, point.view, trigger_slope,
        )
        return
    for channel in VERTICAL_SCALE_CHANNELS:
        scope.enable_channel(channel, resource=SCOPE_RESOURCE)
        scope.set_vertical_scale(channel, volts_per_div, resource=SCOPE_RESOURCE)
    if point.view == 'FULL_PULSE':
        scope.set_horizontal_scale(seconds_per_div, position_percent=5.0, resource=SCOPE_RESOURCE)
        scope.setup_edge_trigger(
            TRIGGER_CHANNEL,
            level=TRIGGER_LEVEL_V,
            slope=trigger_slope,
            mode="NORMAL",
            resource=SCOPE_RESOURCE,
        )
    else:
        scope.set_horizontal_scale(seconds_per_div, position_percent=50.0, resource=SCOPE_RESOURCE)
        scope.setup_edge_trigger(
            TRIGGER_CHANNEL,
            level=TRIGGER_LEVEL_V,
            slope=trigger_slope,
            mode="NORMAL",
            resource=SCOPE_RESOURCE,
        )


def configure_generators(master, slave, point: TestPoint) -> None:
    """Apply one test condition while RF is off."""
    frequency_khz = int(round(point.rf_mhz * 1000.0))
    if DRY_RUN:
        logging.info(
            "DRY RUN generators: RF=%d kHz, setpoint=%d W, pulse=%d Hz, duty=%d%%",
            frequency_khz, point.power_w, point.pulse_hz, point.duty_percent,
        )
        return

    # Configuration changes are intentionally made with RF off.
    master.rfOFF()
    slave.rfOFF()
    for generator in (master, slave):
        generator.setFixedFrequency(frequency_khz)
        generator.setPowerSetpoint(point.power_w * 10)
        generator.setPulsingFrequency(point.pulse_hz)
        generator.setPulsingDutyCycle(point.duty_percent)

    master_frequency=int(master.reportPulsingFrequency());slave_frequency=int(slave.reportPulsingFrequency());master_duty=int(master.reportPulsingDutyCycle());slave_duty=int(slave.reportPulsingDutyCycle())
    if (master_frequency,slave_frequency)!=(point.pulse_hz,point.pulse_hz):raise RuntimeError(f"Pulse-frequency readback mismatch: requested {point.pulse_hz} Hz; master={master_frequency} Hz slave={slave_frequency} Hz")
    if (master_duty,slave_duty)!=(point.duty_percent,point.duty_percent):raise RuntimeError(f"Duty-cycle readback mismatch: requested {point.duty_percent}%; master={master_duty}% slave={slave_duty}%")

    # Read back the key values before any acquisition.
    logging.info(
        "Readback master: pulse=%s Hz duty=%s%% mode=%s sync_out=%s",
        master_frequency,
        master_duty,
        master.reportPulsingConfiguration(1),
        master.reportPulsingConfiguration(2),
    )
    logging.info(
        "Readback slave: pulse=%s Hz duty=%s%% mode=%s",
        slave_frequency,
        slave_duty,
        slave.reportPulsingConfiguration(1),
    )


def set_rf_state(master, slave, enabled: bool) -> None:
    if DRY_RUN:
        logging.info("DRY RUN RF state: %s", "ON" if enabled else "OFF")
        return
    if enabled:
        master.rfON()
        slave.rfON()
    else:
        # Always attempt both OFF commands, even if the first one fails.
        first_error = None
        for generator in (master, slave):
            try:
                generator.rfOFF()
            except Exception as exc:  # cleanup path
                first_error = first_error or exc
                logging.exception("RF OFF command failed during cleanup")
        if first_error:
            raise first_error


def append_result(manifest: Path, point: TestPoint, status: str, detail: str = "") -> None:
    new_file = not manifest.exists()
    with manifest.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow([
                "timestamp", "capture_name", "rf_mhz", "power_w", "pulse_hz",
                "duty_percent", "sample", "view", "status", "detail",
            ])
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"), point.capture_name,
            point.rf_mhz, point.power_w, point.pulse_hz, point.duty_percent,
            point.sample, point.view, status, detail,
        ])


def capture_one(master, slave, point: TestPoint, run_directory: Path) -> None:
    capture_directory = run_directory / point.capture_name
    if SKIP_COMPLETED and (capture_directory / "capture_info.txt").exists():
        logging.info("Skipping completed capture: %s", point.capture_name)
        return

    configure_generators(master, slave, point)
    configure_scope(point)
    time.sleep(SETTLE_TIME_SEC)

    rf_enabled = ENABLE_RF
    try:
        if rf_enabled:
            set_rf_state(master, slave, True)
        if DRY_RUN:
            logging.info("DRY RUN capture: %s", capture_directory)
            return
        scope.arm(single=True, resource=SCOPE_RESOURCE)
        scope.wait_for_acquisition(timeout_sec=ACQUISITION_TIMEOUT_SEC, resource=SCOPE_RESOURCE)
        scope.capture(capture_directory, resource=SCOPE_RESOURCE)
    finally:
        if rf_enabled:
            set_rf_state(master, slave, False)


def _parse_number_list(text: str, label: str, cast=float) -> list:
    values = []
    for raw in re.split(r"[,;\s]+", text.strip()):
        if not raw:
            continue
        try:
            values.append(cast(raw))
        except ValueError as error:
            raise ValueError(f"{label} contains an invalid value: {raw!r}") from error
    if not values:
        raise ValueError(f"Enter at least one {label.lower()} value.")
    return values


def _parse_text_list(text: str, label: str) -> list[str]:
    values = [item.strip().upper() for item in re.split(r"[,;\n]+", text) if item.strip()]
    if not values:
        raise ValueError(f"Select at least one {label.lower()}.")
    return values


def _scope_address_for_display(value: str) -> str:
    """Show only the hostname/IP for a standard TCPIP instrument resource."""
    text = value.strip()
    match = re.fullmatch(r"TCPIP\d*::([^:]+)::(?:inst\d*)::INSTR", text, flags=re.IGNORECASE)
    return match.group(1) if match else text


def _normalise_scope_resource(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("Enter the oscilloscope hostname, IP address, or VISA resource.")
    upper = text.upper()
    if upper.startswith(("TCPIP", "USB", "GPIB", "ASRL")):
        return text
    return f"TCPIP0::{text}::inst0::INSTR"


def _safe_component(value: str) -> str:
    return path_component(value, "Unknown")


def _create_unique_run_folder(parent: Path) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return create_unique_folder(parent, f"{timestamp} - {TEST_FOLDER_NAME} - {_safe_component(MASTER_ADDRESS)}")


def _write_run_settings(run_directory: Path) -> None:
    rows = [
        ("master_address", MASTER_ADDRESS),
        ("slave_address", SLAVE_ADDRESS),
        ("scope_resource", SCOPE_RESOURCE),
        ("dry_run", DRY_RUN),
        ("enable_rf", ENABLE_RF),
        ("continue_on_error", CONTINUE_ON_ERROR),
        ("skip_completed", SKIP_COMPLETED),
        ("settle_time_sec", SETTLE_TIME_SEC),
        ("acquisition_timeout_sec", ACQUISITION_TIMEOUT_SEC),
        ("rf_frequencies_mhz", RF_FREQUENCIES_MHZ),
        ("power_setpoints_w", POWER_SETPOINTS_W),
        ("pulse_frequencies_hz", PULSE_FREQUENCIES_HZ),
        ("requested_duty_cycles", REQUESTED_DUTY_CYCLES),
        ("samples_per_condition", SAMPLES_PER_CONDITION),
        ("trigger_views", TRIGGER_VIEWS),
        ("trigger_channel", TRIGGER_CHANNEL),
        ("trigger_level_v", TRIGGER_LEVEL_V),
        ("vertical_scale_channels", VERTICAL_SCALE_CHANNELS),
        ("horizontal_periods_on_screen", HORIZONTAL_PERIODS_ON_SCREEN),
        ("full_pulse_periods_on_screen", FULL_PULSE_PERIODS_ON_SCREEN),
    ]
    with (run_directory / "Psync_Run_Settings.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["setting", "value"])
        writer.writerows(rows)


def configure_run_with_gui() -> Path:
    """Collect run settings without changing the existing collection engine."""
    global MASTER_ADDRESS, SLAVE_ADDRESS, SCOPE_RESOURCE, OUTPUT_ROOT
    global DRY_RUN, ENABLE_RF, CONTINUE_ON_ERROR, SKIP_COMPLETED
    global SETTLE_TIME_SEC, ACQUISITION_TIMEOUT_SEC
    global RF_FREQUENCIES_MHZ, POWER_SETPOINTS_W, PULSE_FREQUENCIES_HZ
    global REQUESTED_DUTY_CYCLES, SAMPLES_PER_CONDITION, TRIGGER_VIEWS
    global TRIGGER_CHANNEL, TRIGGER_LEVEL_V, VERTICAL_SCALE_CHANNELS
    global HORIZONTAL_PERIODS_ON_SCREEN, FULL_PULSE_PERIODS_ON_SCREEN

    root = tk.Tk()
    root.title("Pulse Sync Data Collection Setup")
    root.geometry("980x720")
    root.minsize(860, 640)
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass
    style.configure("Header.TLabel", font=("Segoe UI", 15, "bold"))
    style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Start.TButton", font=("Segoe UI", 11, "bold"), padding=(14, 8))

    outer = ttk.Frame(root, padding=14)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="Pulse Sync Data Collection", style="Header.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text=("Configure the master/slave generators, Tektronix MSO44, pulse-sync sweep, "
              "safety behavior, and output location. No instruments are contacted until Start Test."),
        wraplength=920,
    ).pack(anchor="w", pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    instruments_tab = ttk.Frame(notebook, padding=12)
    matrix_tab = ttk.Frame(notebook, padding=12)
    timing_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(instruments_tab, text="Instruments")
    notebook.add(matrix_tab, text="Test Matrix")
    notebook.add(timing_tab, text="Timing & Safety")
    notebook.add(output_tab, text="Output")

    variables: dict[str, tk.Variable] = {}

    def add_entry(parent, row, label, key, default, help_text=""):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        variable = tk.StringVar(value=str(default))
        variables[key] = variable
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        if help_text:
            ttk.Label(parent, text=help_text, foreground="#555555", wraplength=380).grid(
                row=row, column=2, sticky="w", padx=(10, 0), pady=5
            )

    add_entry(instruments_tab, 0, "Master generator hostname / IP", "master", MASTER_ADDRESS,
              "Defaults from PARAMOUNT_GENERATOR_ADDRESS when launched by the unified GUI.")
    add_entry(instruments_tab, 1, "Slave generator hostname / IP", "slave", SLAVE_ADDRESS,
              "May also default from PARAMOUNT_PSYNC_SLAVE_ADDRESS.")
    add_entry(instruments_tab, 2, "Oscilloscope hostname or IP address", "scope", _scope_address_for_display(SCOPE_RESOURCE),
              "Defaults from PARAMOUNT_OSCILLOSCOPE_ADDRESS. A standard VISA resource is created internally when Start Test is clicked.")
    add_entry(instruments_tab, 3, "Trigger channel", "trigger_channel", TRIGGER_CHANNEL, "Analog channel 1 through 4.")
    add_entry(instruments_tab, 4, "Trigger level [V]", "trigger_level", TRIGGER_LEVEL_V)
    add_entry(instruments_tab, 5, "Auto-scale channels", "vertical_channels",
              ", ".join(str(value) for value in VERTICAL_SCALE_CHANNELS), "Comma-separated channel numbers.")

    add_entry(matrix_tab, 0, "RF frequencies [MHz]", "rf_frequencies",
              ", ".join(f"{value:g}" for value in RF_FREQUENCIES_MHZ))
    add_entry(matrix_tab, 1, "Power setpoints [W]", "power_setpoints",
              ", ".join(str(value) for value in POWER_SETPOINTS_W))
    add_entry(matrix_tab, 2, "Pulse frequencies [Hz]", "pulse_frequencies",
              ", ".join(str(value) for value in PULSE_FREQUENCIES_HZ))
    add_entry(matrix_tab, 3, "Requested duty cycles [%]", "duty_cycles",
              ", ".join(f"{100 * value:g}" for value in REQUESTED_DUTY_CYCLES),
              "Invalid combinations are skipped by the existing product-limit logic.")
    add_entry(matrix_tab, 4, "Samples per condition", "samples", SAMPLES_PER_CONDITION)
    add_entry(matrix_tab, 5, "Capture views", "views", ", ".join(TRIGGER_VIEWS),
              "Allowed values: RISE, FALL, FULL_PULSE.")

    add_entry(timing_tab, 0, "Settle time [s]", "settle", SETTLE_TIME_SEC)
    add_entry(timing_tab, 1, "Acquisition timeout [s]", "timeout", ACQUISITION_TIMEOUT_SEC)
    add_entry(timing_tab, 2, "Edge close-up periods on screen", "edge_periods", HORIZONTAL_PERIODS_ON_SCREEN)
    add_entry(timing_tab, 3, "Full-pulse periods on screen", "full_periods", FULL_PULSE_PERIODS_ON_SCREEN)
    dry_var = tk.BooleanVar(value=DRY_RUN)
    rf_var = tk.BooleanVar(value=ENABLE_RF)
    continue_var = tk.BooleanVar(value=CONTINUE_ON_ERROR)
    skip_var = tk.BooleanVar(value=SKIP_COMPLETED)
    ttk.Checkbutton(timing_tab, text="Dry run: do not contact instruments", variable=dry_var).grid(
        row=4, column=0, columnspan=3, sticky="w", pady=(14, 4)
    )
    ttk.Checkbutton(timing_tab, text="Enable RF during captures", variable=rf_var).grid(
        row=5, column=0, columnspan=3, sticky="w", pady=4
    )
    ttk.Checkbutton(timing_tab, text="Continue after an individual capture error", variable=continue_var).grid(
        row=6, column=0, columnspan=3, sticky="w", pady=4
    )
    ttk.Checkbutton(timing_tab, text="Skip capture folders already containing capture_info.txt", variable=skip_var).grid(
        row=7, column=0, columnspan=3, sticky="w", pady=4
    )
    ttk.Label(
        timing_tab,
        text=("RF commands are now governed by the Enable RF setting. Dry Run always prevents "
              "instrument contact regardless of the RF selection."),
        foreground="#8B0000", wraplength=760,
    ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(12, 0))

    output_parent = tk.StringVar(value=str(OUTPUT_ROOT))
    ttk.Label(output_tab, text="Parent output folder", style="Section.TLabel").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
    )
    ttk.Entry(output_tab, textvariable=output_parent).grid(row=1, column=0, columnspan=2, sticky="ew", pady=5)
    output_tab.columnconfigure(0, weight=1)

    def browse_output():
        current = output_parent.get().strip()
        selected = filedialog.askdirectory(
            parent=root,
            title="Select parent folder for Pulse Sync collection data",
            initialdir=current if current and Path(current).is_dir() else str(Path.home()),
            mustexist=True,
        )
        if selected:
            output_parent.set(selected)

    ttk.Button(output_tab, text="Browse...", command=browse_output).grid(row=1, column=2, padx=(10, 0))
    preview_var = tk.StringVar()
    ttk.Label(output_tab, textvariable=preview_var, foreground="#555555", wraplength=820).grid(
        row=2, column=0, columnspan=3, sticky="w", pady=(12, 0)
    )

    result = {"accepted": False, "run_directory": None}

    def update_preview(*_args):
        master = variables["master"].get().strip() or "Generator"
        preview_var.set(
            "Run folder format:\n"
            f"YYYY-MM-DD_HH-MM-SS - {TEST_FOLDER_NAME} - {_safe_component(master)}\n\n"
            "The run folder is created only after Start Test passes validation."
        )

    variables["master"].trace_add("write", update_preview)
    update_preview()

    def start_test():
        global MASTER_ADDRESS, SLAVE_ADDRESS, SCOPE_RESOURCE, OUTPUT_ROOT
        global DRY_RUN, ENABLE_RF, CONTINUE_ON_ERROR, SKIP_COMPLETED
        global SETTLE_TIME_SEC, ACQUISITION_TIMEOUT_SEC
        global RF_FREQUENCIES_MHZ, POWER_SETPOINTS_W, PULSE_FREQUENCIES_HZ
        global REQUESTED_DUTY_CYCLES, SAMPLES_PER_CONDITION, TRIGGER_VIEWS
        global TRIGGER_CHANNEL, TRIGGER_LEVEL_V, VERTICAL_SCALE_CHANNELS
        global HORIZONTAL_PERIODS_ON_SCREEN, FULL_PULSE_PERIODS_ON_SCREEN
        try:
            master = variables["master"].get().strip()
            slave = variables["slave"].get().strip()
            if not master or not slave:
                raise ValueError("Enter both master and slave generator addresses.")
            parent_text = output_parent.get().strip()
            if not parent_text:
                raise ValueError("Select a parent output folder on the Output tab.")
            parent = Path(parent_text).expanduser().resolve()
            if not parent.is_dir():
                raise ValueError("The selected parent output folder does not exist.")

            rf_values = _parse_number_list(variables["rf_frequencies"].get(), "RF frequencies", float)
            power_values = _parse_number_list(variables["power_setpoints"].get(), "Power setpoints", int)
            pulse_values = _parse_number_list(variables["pulse_frequencies"].get(), "Pulse frequencies", int)
            duty_percent = _parse_number_list(variables["duty_cycles"].get(), "Duty cycles", float)
            duty_values = [value / 100.0 for value in duty_percent]
            sample_count = int(variables["samples"].get())
            if sample_count < 1:
                raise ValueError("Samples per condition must be a positive whole number.")
            views = _parse_text_list(variables["views"].get(), "Capture views")
            invalid_views = [view for view in views if view not in TRIGGER_SLOPE_BY_VIEW]
            if invalid_views:
                raise ValueError(f"Unsupported capture views: {', '.join(invalid_views)}")
            trigger_channel = int(variables["trigger_channel"].get())
            if trigger_channel not in {1, 2, 3, 4}:
                raise ValueError("Trigger channel must be 1, 2, 3, or 4.")
            vertical_channels = tuple(_parse_number_list(
                variables["vertical_channels"].get(), "Auto-scale channels", int
            ))
            if any(channel not in {1, 2, 3, 4} for channel in vertical_channels):
                raise ValueError("Auto-scale channels must be from 1 through 4.")
            settle = float(variables["settle"].get())
            timeout = float(variables["timeout"].get())
            edge_periods = float(variables["edge_periods"].get())
            full_periods = float(variables["full_periods"].get())
            if settle < 0 or timeout <= 0 or edge_periods <= 0 or full_periods <= 0:
                raise ValueError("Timing values must be positive; settle time may be zero.")

            # Validate the complete requested matrix before creating the output folder.
            for pulse_hz in pulse_values:
                duty_cycles_for_frequency(pulse_hz, duty_values)

            MASTER_ADDRESS = master
            SLAVE_ADDRESS = slave
            SCOPE_RESOURCE = _normalise_scope_resource(variables["scope"].get())
            OUTPUT_ROOT = parent
            DRY_RUN = bool(dry_var.get())
            ENABLE_RF = bool(rf_var.get())
            CONTINUE_ON_ERROR = bool(continue_var.get())
            SKIP_COMPLETED = bool(skip_var.get())
            SETTLE_TIME_SEC = settle
            ACQUISITION_TIMEOUT_SEC = timeout
            RF_FREQUENCIES_MHZ = rf_values
            POWER_SETPOINTS_W = power_values
            PULSE_FREQUENCIES_HZ = pulse_values
            REQUESTED_DUTY_CYCLES = duty_values
            SAMPLES_PER_CONDITION = sample_count
            TRIGGER_VIEWS = views
            TRIGGER_CHANNEL = trigger_channel
            TRIGGER_LEVEL_V = float(variables["trigger_level"].get())
            VERTICAL_SCALE_CHANNELS = vertical_channels
            HORIZONTAL_PERIODS_ON_SCREEN = edge_periods
            FULL_PULSE_PERIODS_ON_SCREEN = full_periods

            run_directory = _create_unique_run_folder(parent)
            _write_run_settings(run_directory)
            result["accepted"] = True
            result["run_directory"] = run_directory
            root.destroy()
        except Exception as error:
            messagebox.showerror("Invalid Pulse Sync settings", str(error), parent=root)

    buttons = ttk.Frame(outer)
    buttons.pack(fill="x", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text="Start Test", style="Start.TButton", command=start_test).pack(side="right")
    root.mainloop()
    if not result["accepted"]:
        raise SystemExit(0)
    return result["run_directory"]


class CollectionProgress:
    def __init__(self, total: int):
        self.total = max(1, int(total))
        self.stop_requested = False
        self.root = tk.Tk()
        self.root.title("Pulse Sync Collection Progress")
        self.root.geometry("700x260")
        self.root.protocol("WM_DELETE_WINDOW", self.request_stop)
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        self.heading = tk.StringVar(value="Preparing collection")
        self.detail = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.heading, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.detail, wraplength=650).pack(anchor="w", pady=(8, 12))
        self.bar = ttk.Progressbar(frame, maximum=self.total, mode="determinate")
        self.bar.pack(fill="x")
        self.count = tk.StringVar(value=f"0 of {self.total}")
        ttk.Label(frame, textvariable=self.count).pack(anchor="w", pady=(6, 12))
        ttk.Button(frame, text="Stop After Current Capture", command=self.request_stop).pack(anchor="e")
        self.root.update_idletasks()

    def request_stop(self):
        self.stop_requested = True
        self.heading.set("Stop requested")
        self.detail.set("Collection will stop before the next capture. RF cleanup remains active.")
        self.root.update_idletasks()

    def update(self, index: int, point: TestPoint):
        self.bar["value"] = index
        self.count.set(f"{index} of {self.total}")
        self.heading.set("Collecting Pulse Sync waveforms")
        self.detail.set(point.capture_name)
        self.root.update_idletasks()
        self.root.update()

    def close(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main() -> int:
    global RUNTIME_DUTY_LIMITS
    run_directory=configure_run_with_gui();configure_logging(run_directory);manifest=run_directory/'manifest.csv';master=slave=None;progress=None
    try:
        if DRY_RUN:RUNTIME_DUTY_LIMITS=None;logging.info("Dry run: using timing-derived duty-cycle limits only.")
        else:
            factory=InstrumentFactory({"generator":MASTER_ADDRESS,"psync_slave_generator":SLAVE_ADDRESS},PSYNC_ADAPTERS);master=factory.create("generator");slave=factory.create("psync_slave_generator");RUNTIME_DUTY_LIMITS=query_generator_duty_limits(master,slave);configure_pulse_sync_roles(master,slave)
        test_points=list(iter_test_points(RUNTIME_DUTY_LIMITS));append_runtime_duty_limits(run_directory,RUNTIME_DUTY_LIMITS);total=len(test_points);logging.info("Planned captures after duty-cycle filtering: %d",total);logging.info("DRY_RUN=%s ENABLE_RF=%s",DRY_RUN,ENABLE_RF);progress=CollectionProgress(total)
        for index,point in enumerate(test_points,start=1):
            if progress.stop_requested:logging.warning("Stop requested by user before capture %d of %d",index,total);break
            progress.update(index,point);logging.info("[%d/%d] %s",index,total,point.capture_name)
            try:capture_one(master,slave,point,run_directory);append_result(manifest,point,"dry_run" if DRY_RUN else "complete")
            except KeyboardInterrupt:append_result(manifest,point,"interrupted");raise
            except Exception as exc:
                logging.exception("Test point failed: %s",point.capture_name);append_result(manifest,point,"failed",repr(exc))
                if not CONTINUE_ON_ERROR:raise
    finally:
        if progress is not None:progress.close()
        if not DRY_RUN and master is not None and slave is not None:
            try:set_rf_state(master,slave,False)
            except Exception:logging.exception("Final RF OFF cleanup encountered an error")
            for generator in (master,slave):
                try:generator.disablePulsing()
                except Exception:logging.exception("Final pulsing-disable cleanup encountered an error")
                try:generator.close()
                except Exception:logging.exception("Generator close failed")
    logging.info("Run finished. Results: %s",run_directory);return 0


if __name__ == "__main__":
    raise SystemExit(main())
