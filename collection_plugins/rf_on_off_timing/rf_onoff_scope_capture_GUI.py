"""
RF ON / RF OFF command-to-RF latency capture using a Tektronix MSO46 oscilloscope
(scope over Ethernet, generator over serial).

For the selected unit, for each setpoint and each iteration:
    RF ON (scope at 1 ms/div):
        - arm the scope (single sequence, trigger = CH2 rising edge)
        - gen.rfON_TEST()  (or gen.rfON(), see USE_RFON_TEST)
        - wait for the capture
        - cursor A = end of the command on CH2 (last falling edge of the first burst)
          cursor B = see measurement method below
        - measure delta t, place the scope cursors at A/B, save a screenshot
    RF OFF (scope at 2 ms/div so the whole RF OFF pulse is captured):
        - same with gen.rfOFF()

Measurement method (-m), cursor B relative to the first CH3 rising edge (generator response):
    -m 1 (default)  RF ON : CH1 becomes > 0  within the 500 us BEFORE the CH3 edge
                    RF OFF: CH1 becomes 0    within the 500 us BEFORE the CH3 edge
    -m 2            RF ON : CH1 voltage reaches 10 % of the CH1 maximum voltagethe
                            scope's "Maximum"); before or after the CH3 edge - CH3
                            is not used (B2_RFON_MODE = "step_end": end of the
                            initial low-level RF ON step instead)
                    RF OFF: CH1 drops to 90 % of its maximum, before the CH3 edge
                    (percent of the range between CH1's RF-off level and its maximum)
    If cursor B can't be placed, delta t is left empty and the reason is in the
    details CSV. Settings: HSCALE_S_PER_DIV, B_WINDOW_S, B2_LEVEL and those around them.

Output (unit = "legacy 3013", "refreshed 3013", "legacy 6013" or "refreshed 6013"):
    <REPORT_ROOT>/<model>/<platform>_<model>_rf_latency_m<1|2>_<timestamp>.csv
        setpoint_W, command, iteration, delta_t_ms   (one row per capture)
    <REPORT_ROOT>/<model>/<platform>_<model>_rf_latency_m<1|2>_<timestamp>_details.csv
        cursor times, scope cursor readout, status and image path (troubleshooting)
    <REPORT_ROOT>/<model>/<platform>/<RFON|RFOFF>_<setpoint W>_<iteration>.png          (-m 1)
    <REPORT_ROOT>/<model>/<platform>/<RFON|RFOFF>_<setpoint W>_<iteration>_<10|90>pct.png (-m 2)
e.g. ...\\6013\\refreshed\\RFON_15_7.png (-m 1) and RFON_15_7_10pct.png (-m 2)

Usage (from this folder; scope IP and COM port are set below):
    python rf_onoff_scope_capture.py --check
        connection check only; RF is not turned on
    python rf_onoff_scope_capture.py --unit "refreshed 6013" --trial -m 1
        trial run on the real unit and scope: 1 RF ON/OFF iteration per setpoint;
        files are prefixed TRIAL_ (add --setpoints 150 to try just 15 W)
    python rf_onoff_scope_capture.py --unit "refreshed 6013" -m 2
        full run with measurement method 2 (if --unit is omitted you are asked)
    Press Ctrl+C to stop at any time; RF is turned off and the CSVs are kept.

Notes
-----
* gen.rfON_TEST() (default) sends only the RF ON command, so the scope triggers on
  the actual command. Cursor A ignores the PC's later ACK burst on CH2. Keep
  USE_RFON_TEST = True: gen.rfON() sends a query first, so the scope would
  trigger on (and measure) the query instead of the RF ON command.
* Setpoints are in tenths of watts (same as gen.setPowerSetpoint); the CSV and file
  names use watts (150 -> 15 W). A setpoint the unit rejects is logged and skipped.
* Existing images are never overwritten; a " (2)", " (3)", ... suffix is added.

Requirements:  pip install pyvisa pyvisa-py numpy
"""

import argparse
import ipaddress
import re
import csv
import os
import sys
import time
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
import suite_common.bootstrap  # noqa: E402,F401
from suite_instruments.errors import InstrumentError  # noqa: E402
from suite_instruments.factory import InstrumentFactory  # noqa: E402
from suite_instruments.transport.visa import resource_manager  # noqa: E402

# ---------------------------
# Configuration - edit these
# ---------------------------
# Generator connection - serial by default (same meaning as in CommandProcessing.py)
COMM_TYPE = "serial"          # "serial" or "ethernet"
COM_PORT = "COM5"            # generator serial port (check Device Manager); or --com-port
IP_ADDRESS = "PMNT012837"     # used when COMM_TYPE == "ethernet"

# Units under test: name -> (model folder, platform folder)
UNITS = {
    "legacy 3013": ("3013", "legacy"),
    "refreshed 3013": ("3013", "refreshed"),
    "legacy 6013": ("6013", "legacy"),
    "refreshed 6013": ("6013", "refreshed"),
    "legacy 5002": ("5002", "legacy"),
    "refresh 5002": ("5002", "legacy"),
}
# Setpoints per model, in tenths of watts (same as gen.setPowerSetpoint). Edit per run.
SETPOINTS_BY_MODEL = {
    "3013": [100, 1000, 5000, 10000, 20000, 30000],   # 3 kW unit: 10, 100, 500, 1000, 2000, 3000 W
    "6013": [150, 1000, 5000, 15000, 30000, 60000],   # 6 kW unit: 15, 100, 500, 1500, 3000, 6000 W
    "5002": [500, 1000, 5000, 15000, 30000, 50000],   # 6 kW unit: 15, 100, 500, 1500, 3000, 6000 W
}
ITERATIONS = 10
RF_ON_DWELL_S = 1.0           # time RF stays on before the RF OFF capture
RF_OFF_DWELL_S = 1.0          # time RF stays off before the next RF ON capture
# True  -> gen.rfON_TEST(): sends only the RF ON command (one CH2 burst; trigger is on
#          the real command). Control mode is already set to 2 at the start.
# False -> gen.rfON(): sends reportActiveControlMode first, then RF ON (two CH2 bursts).
USE_RFON_TEST = True

# Results:  <REPORT_ROOT>/<model>/<platform>_<model>_rf_latency_<timestamp>.csv
# Captures: <REPORT_ROOT>/<model>/<platform>/<RFON|RFOFF>_<setpoint W>_<iteration>.png
REPORT_ROOT = r"c:\My_Designs\Transparency Reports\AEBus Command Execution"
COLLECTION_PARENT_FOLDER_ENV = "PARAMOUNT_COLLECTION_PARENT_FOLDER"
DUT_NAME_ENV = "PARAMOUNT_GENERATOR_ADDRESS"
TEST_NAME = "Serial RF On-Off Latency"

# Scope (Ethernet). Find the IP on the scope under Utility > I/O > LAN; or --scope-ip.
# SCOPE_IP = "172.24.242.29"   # a hostname also works
SCOPE_IP = "10.9.13.212"   # a hostname also works
# VXI-11 (inst0) supports the binary screenshot/waveform transfers reliably.
# Set VISA_RESOURCE only for a different interface, e.g. "TCPIP0::<ip>::hislip0::INSTR".
VISA_RESOURCE = None
TRIGGER_SOURCE = "CH2"
CMD_CHANNEL = "CH2"           # AE Bus command (PC -> generator)  -> cursor A
RF_CHANNEL = "CH1"            # RF output                          -> cursor B
RESP_CHANNEL = "CH3"          # AE Bus response (generator -> PC)  -> anchor for cursor B
CURSOR_SOURCE = "CH2"         # waveform the on-screen cursors are attached to
SCOPE_TEMP_IMAGE = "C:/tek_capture_tmp.png"
VISA_TIMEOUT_MS = 60000
ARM_TIMEOUT_S = 10            # max time to wait for the scope to report READY
ACQ_TIMEOUT_S = 10            # max time to wait for the trigger after the command
POLL_INTERVAL_S = 0.02

# Horizontal scale (s/div) set before each capture; 10 divisions per screen.
# RF OFF uses 2 ms/div (20 ms) so the whole RF OFF pulse and the CH3 response fit.
HSCALE_S_PER_DIV = {"RFON": 2e-3, "RFOFF": 2e-3}

# Cursor A = end of the first CH2 burst (the command): the first CH2 falling edge
# followed by at least CMD_GAP_S of idle line (the PC's ACK later on is ignored).
CMD_GAP_S = 0.5e-3
CH3_BURST_GAP_S = 0.5e-3
CH3_BURST_MIN_EDGES = 3
CH3_BURST_MIN_DURATION_S = 100e-6
CH3_EDGE_DIAGNOSTIC_LIMIT = 12
MIN_CMD_SWING_V = 3.0         # minimum CH2/CH3 swing to count as a valid burst

# Cursor B, anchored on t3 = first CH3 rising edge after cursor A, searched in the
# B_WINDOW_S *before* t3 (default: t3 - 2 ms ... t3):
#   RF ON : point where CH1 becomes > 0 (and stays > 0)
#   RF OFF: point where CH1 becomes 0 (and stays 0)
# "0" = CH1 RF envelope (peak-to-peak) at its RF-off noise level.
B_WINDOW_S = 2e-3
RFON_POST_CH3_WINDOW_S = 500e-6  # allow RF ON slightly after CH3 response starts
ENV_WINDOW_S = 1e-6           # envelope window; also B's time resolution
SMOOTH_WINDOWS = 10           # moving average over this many windows (noise immunity)
ZERO_NOISE_K = 5              # CH1 counts as > 0 above RF-off level + K * noise ...
MIN_ACTIVITY_V = 0.02         # ... and at least this much above it (V p-p) / 2
HOLD_FRACTION = 0.9           # after B, CH1 must stay > 0 (RF ON) / at 0 (RF OFF) for at
                              #   least this fraction of the time up to the CH3 edge
MIN_HOLD_WINDOWS = 3          # ... checked over at least this many windows
MEAS1_RFON_HOLD_S = 100e-6    # Measurement 1 sustained-ON requirement
MEAS1_RFOFF_HOLD_S = 100e-6   # Measurement 1 sustained-OFF requirement
PRE_CHECK_S = 100e-6          # "already changed before the window" check length

# Measurement 2 (-m 2), searched from cursor A:
#   RF ON : CH1 voltage reaches 10 % of the CH1 maximum voltage (same value as the
#           scope's "Maximum" measurement); before or after the CH3 edge - CH3 ignored.
#           B2_RFON_MODE = "step_end" instead uses the end of the initial low-level
#           RF ON step (where CH1 starts ramping to full power).
#   RF OFF: CH1 drops to 90 % of its maximum, before the CH3 edge
#           (percent of the range between CH1's RF-off level and its maximum envelope).
B2_LEVEL = {"RFON": 0.10, "RFOFF": 0.90}
B2_RFON_MODE = "level"        # "level" (10 % of max voltage) or "step_end"
B2_RFON_HOLD_S = 100e-6       # RF ON: a level change must last this long to count
B2_STEP_SKIP_S = 20e-6        # step_end: step level is measured starting this long after RF starts ...
B2_STEP_LEN_S = 100e-6        # ... over this long
B2_STEP_RISE_FRAC = 0.05      # step_end: rise above the step by 5 % of (max - step) = step has ended

# Cursor B method, chosen with -m / --measurement
MEASUREMENTS = {
    1: "B where CH1 > 0 (RF ON) / CH1 = 0 (RF OFF), within 500 us before the CH3 edge",
    2: "B where CH1 = 10 % of max voltage (RF ON, CH3 ignored) / 90 % of max (RF OFF, before the CH3 edge)",
}
DEFAULT_MEASUREMENT = 1
END_TAIL_S = 1e-3             # RF OFF: RF-off level measured on the last 1 ms of the record


# ---------------------------
# Scope helpers
# ---------------------------
def normalize_scope_address(value):
    """Return a clean host/IP from plain text or a complete VISA resource string."""
    text = (value or "").strip()
    if not text:
        raise ValueError("Enter the oscilloscope IP address or hostname.")
    # Accept pasted VISA resource strings without forcing the user to edit them.
    if "::" in text:
        parts = text.split("::")
        if len(parts) >= 2 and parts[0].upper().startswith("TCPIP"):
            text = parts[1].strip()
    text = text.removeprefix("http://").removeprefix("https://").strip().strip("/")
    if "/" in text or " " in text:
        raise ValueError("Scope address must be an IP address or hostname, not a URL/path.")
    try:
        ipaddress.ip_address(text)
    except ValueError:
        # Hostnames are allowed, but only with normal DNS-safe characters.
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", text):
            raise ValueError(f"Invalid scope IP address or hostname: {text!r}")
    return text


def build_scope_resource(scope_address, interface="VXI-11"):
    """Build a Tektronix LAN VISA resource from a clean address and interface."""
    host = normalize_scope_address(scope_address)
    if interface == "HiSLIP":
        return f"TCPIP0::{host}::hislip0::INSTR"
    if interface == "VXI-11":
        return f"TCPIP0::{host}::inst0::INSTR"
    raise ValueError(f"Unsupported scope interface: {interface}")


def open_scope(scope_ip=None, scope_interface="VXI-11"):
    rm = resource_manager()
    scope_address = scope_ip or SCOPE_IP
    if VISA_RESOURCE:
        resource = VISA_RESOURCE
    elif scope_address:
        resource = build_scope_resource(scope_address, scope_interface)
    else:
        resources = rm.list_resources("TCPIP?*")
        if not resources:
            rm.close()
            raise RuntimeError("No LAN oscilloscope was found. Enter its IP address in the GUI.")
        resource = resources[0]
    print(f"Opening scope: {resource}")
    try:
        scope = InstrumentFactory({"oscilloscope": resource}).create("oscilloscope", resource_manager=rm, open_attempts=1)
    except Exception as e:
        try:
            backend = type(rm.visalib).__module__
        except Exception:
            backend = "unknown VISA backend"
        try:
            rm.close()
        except Exception:
            pass
        extra = ""
        if "creating link: 3" in str(e).lower():
            extra = (" VXI-11 link creation failed. Close other applications or Python processes "
                     "connected to the scope, power-cycle the scope if the session is stale, or "
                     "select HiSLIP in the GUI.")
        raise RuntimeError(
            f"Cannot connect to scope at {resource} using {backend}: {e}.{extra}"
        ) from e
    scope.timeout = VISA_TIMEOUT_MS
    scope.read_termination = "\n"
    scope.write_termination = "\n"
    scope.write("HEADer OFF")
    scope.write("VERBose ON")
    print(f"Scope: {scope.query('*IDN?').strip()}  ({resource})")
    # Retain the resource manager for the lifetime of the VISA session.
    scope._rf_latency_resource_manager = rm
    return scope

def configure_trigger(scope):
    """Configure the scope trigger without an unbounded *OPC? wait.

    Each command is written separately with a progress message. The setup uses a
    short temporary VISA timeout and verifies the resulting settings directly,
    which makes a scope-side problem visible instead of appearing to hang.
    """
    print("Configuring scope trigger...", flush=True)
    original_timeout = scope.timeout
    setup_timeout_ms = 5000

    commands = (
        ("stop acquisition", "ACQuire:STATE OFF"),
        ("set edge trigger type", "TRIGger:A:TYPe EDGE"),
        (f"set trigger source to {TRIGGER_SOURCE}",
         f"TRIGger:A:EDGE:SOUrce {TRIGGER_SOURCE}"),
        ("set rising-edge slope", "TRIGger:A:EDGE:SLOpe RISe"),
        ("set normal trigger mode", "TRIGger:A:MODe NORMal"),
        ("set single-sequence acquisition", "ACQuire:STOPAfter SEQuence"),
    )

    try:
        scope.timeout = min(original_timeout, setup_timeout_ms)
        scope.write("*CLS")
        for description, command in commands:
            print(f"  {description}...", end="", flush=True)
            scope.write(command)
            print(" OK", flush=True)

        # Query the settings that matter instead of waiting indefinitely on
        # *OPC?. Tektronix writes are processed in order on this connection.
        print("  verifying trigger configuration...", end="", flush=True)
        source = scope.query("TRIGger:A:EDGE:SOUrce?").strip().upper()
        slope = scope.query("TRIGger:A:EDGE:SLOpe?").strip().upper()
        mode = scope.query("TRIGger:A:MODe?").strip().upper()
        stop_after = scope.query("ACQuire:STOPAfter?").strip().upper()
        expected_source = TRIGGER_SOURCE.upper()
        if expected_source not in source:
            raise RuntimeError(
                f"scope reported trigger source {source!r}; expected {expected_source!r}"
            )
        if not slope.startswith("RIS"):
            raise RuntimeError(f"scope reported trigger slope {slope!r}; expected rising")
        if not mode.startswith("NORM"):
            raise RuntimeError(f"scope reported trigger mode {mode!r}; expected normal")
        if not (stop_after.startswith("SEQ") or stop_after in ("1", "ON")):
            raise RuntimeError(
                f"scope reported STOPAfter {stop_after!r}; expected sequence"
            )
        print(" OK", flush=True)
        print("Scope trigger configuration complete.", flush=True)

    except InstrumentError as exc:
        try:
            scope.clear()
        except Exception:
            pass
        raise RuntimeError(
            "Scope timed out while configuring the trigger. Check the last printed "
            "setup step, close any Save/Recall dialog on the Tektronix scope, and "
            "confirm that no other application is controlling it."
        ) from exc
    finally:
        scope.timeout = original_timeout


def recover_scope(scope):
    """After a VISA error/timeout: clear the interface so later commands don't read
    stale replies, and return the scope's own error/event messages (or "")."""
    msgs = ""
    try:
        scope.clear()                      # VISA device clear: aborts pending I/O
    except Exception:
        pass
    old = scope.timeout
    try:
        scope.timeout = 5000
        scope.query("*ESR?")
        msgs = scope.query("ALLEv?").strip()
        if msgs.startswith("0,") and "No events" in msgs:
            msgs = ""
        scope.write("*CLS")
        scope.write("ACQuire:STATE OFF")
    except Exception as e:
        msgs = msgs or f"(no reply after device clear: {e})"
    finally:
        scope.timeout = old
    return msgs


def ensure_channels_on(scope):
    """CH1/CH2/CH3 must be displayed: CURVe? on a channel that is off returns no data
    (the read then times out). Turn any that are off back on."""
    for ch in dict.fromkeys((RF_CHANNEL, CMD_CHANNEL, RESP_CHANNEL)):
        state = scope.query(f"DISplay:GLObal:{ch}:STATE?").strip()
        if state not in ("1", "ON"):
            print(f"WARNING: {ch} was turned off on the scope - turning it on.")
            scope.write(f"DISplay:GLObal:{ch}:STATE ON")
    scope.query("*OPC?")


def set_hscale(scope, command, horizontal_scales=None):
    scales = horizontal_scales or HSCALE_S_PER_DIV
    scale = scales.get(command)
    if scale:
        scope.write(f"HORizontal:SCAle {scale}")
        scope.query("*OPC?")


def apply_vertical_scales(scope, vertical_scales):
    """Apply optional V/div overrides; None leaves the present scope setting unchanged."""
    for channel, scale in vertical_scales.items():
        if scale is None:
            continue
        print(f"Setting {channel} vertical scale to {scale:g} V/div...", flush=True)
        scope.write(f"{channel}:SCAle {scale}")
        actual = float(scope.query(f"{channel}:SCAle?"))
        print(f"  {channel} scale confirmed at {actual:g} V/div", flush=True)


def arm(scope):
    """Arm a single acquisition and wait until the trigger system is READY."""
    scope.write("ACQuire:STATE ON")
    start = time.time()
    while scope.query("TRIGger:STATE?").strip().upper() != "READY":
        if time.time() - start > ARM_TIMEOUT_S:
            raise TimeoutError("Scope did not become READY after arming.")
        time.sleep(POLL_INTERVAL_S)


def wait_for_capture(scope):
    start = time.time()
    while scope.query("ACQuire:STATE?").strip() not in ("0", "OFF"):
        if time.time() - start > ACQ_TIMEOUT_S:
            scope.write("ACQuire:STATE OFF")
            raise TimeoutError("No trigger on CH2 rising edge.")
        time.sleep(POLL_INTERVAL_S)


def fetch_waveform(scope, channel):
    """Return (time array, volts array) relative to the trigger."""
    record_len = int(float(scope.query("HORizontal:RECOrdlength?")))
    scope.write(f"DATa:SOUrce {channel}")
    scope.write("DATa:ENCdg SRIbinary")
    scope.write("WFMOutpre:BYT_Nr 2")
    scope.write("DATa:STARt 1")
    scope.write(f"DATa:STOP {record_len}")
    x_incr = float(scope.query("WFMOutpre:XINcr?"))
    x_zero = float(scope.query("WFMOutpre:XZEro?"))
    pt_off = float(scope.query("WFMOutpre:PT_Off?"))
    y_mult = float(scope.query("WFMOutpre:YMUlt?"))
    y_off = float(scope.query("WFMOutpre:YOFf?"))
    y_zero = float(scope.query("WFMOutpre:YZEro?"))
    raw = scope.query_binary_values("CURVe?", datatype="h", is_big_endian=False,
                                    container=np.array)
    volts = (raw.astype(np.float64) - y_off) * y_mult + y_zero
    t = x_zero + (np.arange(volts.size) - pt_off) * x_incr
    return t, volts


def set_cursors(scope, t_a, t_b):
    base = "DISplay:WAVEView1:CURSor:CURSOR1"
    if t_a is None or t_b is None:
        # No measurement: hide cursors so the screenshot doesn't show stale positions.
        scope.write(f"{base}:STATE OFF")
        scope.query("*OPC?")
        return None
    scope.write(f"{base}:STATE ON")
    scope.write(f"{base}:FUNCtion WAVEFORM")
    scope.write(f"{base}:ASOUrce {CURSOR_SOURCE}")
    scope.write(f"{base}:BSOUrce {CURSOR_SOURCE}")
    scope.write(f"{base}:WAVEform:APOSition {t_a:.9e}")
    scope.write(f"{base}:WAVEform:BPOSition {t_b:.9e}")
    scope.query("*OPC?")
    try:
        return float(scope.query(f"{base}:VBArs:DELTa?"))
    except (ValueError, InstrumentError):
        return None


def save_screenshot(scope, dest_path):
    scope.write("*CLS")
    scope.write(f'SAVe:IMAGe "{SCOPE_TEMP_IMAGE}"')
    scope.query("*OPC?")
    # SAVe:IMAGe reports problems (e.g. scope drive full) only in the event queue;
    # check it here, otherwise READFile waits for a file that doesn't exist.
    if int(scope.query("*ESR?").strip()) & 0x3C:
        msg = scope.query("ALLEv?").strip()
        if "insufficient space" in msg:
            raise RuntimeError("scope drive is full - free space on the scope's C: drive "
                               f"(screenshot could not be saved to {SCOPE_TEMP_IMAGE})")
        raise RuntimeError(f"scope could not save the screenshot: {msg}")
    scope.write(f'FILESystem:READFile "{SCOPE_TEMP_IMAGE}"')
    data = scope.read_raw()
    with open(dest_path, "wb") as f:
        f.write(data)
    scope.write(f'FILESystem:DELEte "{SCOPE_TEMP_IMAGE}"')
    scope.query("*OPC?")


# ---------------------------
# Measurement
# ---------------------------
def _envelope(t, v):
    """Return (window times, smoothed peak-to-peak envelope)."""
    dt = t[1] - t[0]
    w = max(1, int(round(ENV_WINDOW_S / dt)))
    n = v.size // w
    blocks = v[: n * w].reshape(n, w)
    env = blocks.max(axis=1) - blocks.min(axis=1)
    k = min(SMOOTH_WINDOWS, n)
    env_s = np.convolve(env, np.ones(k) / k, "same")
    # Edge windows are averaged with zero padding; replace them with the nearest valid value.
    h = k // 2
    if n > 2 * h + 1 and h:
        env_s[:h] = env_s[h]
        env_s[-h:] = env_s[-h - 1]
    return t[: n * w : w], env_s


def _robust(x):
    """(median, robust std) of x."""
    med = np.median(x)
    return med, 1.4826 * np.median(np.abs(x - med))


def _first_held(mask, i0, i_end, i_hold_end, hold_windows=None):
    """First index k in [i0, i_end] where mask[k] is True and mask stays True for at
    least HOLD_FRACTION of the windows from k up to i_hold_end (just before the CH3
    edge) - or, if hold_windows is given, for the next hold_windows windows.
    Brief glitches are tolerated. Returns None if there is no such k."""
    cs = np.concatenate(([0], np.cumsum(mask.astype(int))))
    for k in np.flatnonzero(mask[i0:i_end + 1]) + i0:
        if hold_windows:
            e = min(k + hold_windows, mask.size)
        else:
            e = min(max(i_hold_end + 1, k + MIN_HOLD_WINDOWS), mask.size)
        if (cs[e] - cs[k]) / (e - k) >= HOLD_FRACTION:
            return int(k)
    return None


def _first_fixed_hold(mask, start, end, hold_windows):
    """First transition that remains asserted for a fixed number of windows."""
    start = max(0, int(start))
    end = min(int(end), mask.size)
    hold_windows = max(MIN_HOLD_WINDOWS, int(hold_windows))
    for k in range(start, end - hold_windows + 1):
        if mask[k] and (k == 0 or not mask[k - 1]) and mask[k:k + hold_windows].all():
            return k
    return None


def _mostly(mask, a, b):
    seg = mask[max(0, a):max(0, b)]
    return seg.size > 0 and seg.mean() >= HOLD_FRACTION


def _zero_level(tw, env, t_a, command):
    """CH1 RF-off envelope level and noise: before the command for RF ON, at the end
    of the record for RF OFF. Returns (base, noise) or None if there's too little data."""
    if command == "RFON":
        ref = env[tw < t_a]                          # RF still off before the command ends
    else:
        ref = env[tw >= tw[-1] - END_TAIL_S]         # RF fully off at the end of the record
    return _robust(ref) if ref.size >= 20 else None


def _window_indices(tw, t3):
    # The smoothed envelope blurs transitions by up to half the smoothing length:
    # B may be found up to that much past t3 (it is capped at t3), and the
    # "stays > 0 / stays at 0" check stops that much before t3.
    h = SMOOTH_WINDOWS // 2
    i3 = int(np.searchsorted(tw, t3, side="right")) - 1
    return min(i3 + h, tw.size - 1), i3 - h


def _format_window(window_s):
    """Human-readable search-window duration for terminal diagnostics."""
    if window_s < 1e-3:
        return f"{window_s * 1e6:g} us"
    return f"{window_s * 1e3:g} ms"


def find_b(t, v, t_a, t3, command, b_window_s=B_WINDOW_S, rfon_post_ch3_s=RFON_POST_CH3_WINDOW_S):
    """Cursor B on CH1 within b_window_s before t3. Returns (t_B, status)."""
    tw, env = _envelope(t, v)
    step = tw[1] - tw[0]
    zero = _zero_level(tw, env, t_a, command)
    if zero is None:
        return None, "not enough RF-off data on CH1 to define 0"
    base, noise = zero
    thr = base + max(ZERO_NOISE_K * noise, MIN_ACTIVITY_V / 2)
    active = env > thr

    i0 = int(np.searchsorted(tw, t3 - b_window_s))
    search_end = t3 + rfon_post_ch3_s if command == "RFON" else t3
    i1 = min(int(np.searchsorted(tw, search_end, side="right")), tw.size)
    pre = max(1, int(round(PRE_CHECK_S / step)))
    window_text = _format_window(b_window_s)
    if command == "RFON":
        hold_s = MEAS1_RFON_HOLD_S
        state = active
        if _mostly(state, i0 - pre, i0 + 1):
            return None, (f"CH1 was already ON at the start of the {window_text} search window "
                          "before the CH3 edge; increase the search window if appropriate")
    else:
        hold_s = MEAS1_RFOFF_HOLD_S
        if not active[tw < t_a].any():
            return None, "CH1 was not on before the RF OFF command"
        state = ~active
        if _mostly(state, i0 - pre, i0 + 1):
            return None, (f"CH1 was already OFF at the start of the {window_text} search window "
                          "before the CH3 edge; increase the search window if appropriate")
    hold_windows = max(MIN_HOLD_WINDOWS, int(np.ceil(hold_s / step)))
    k = _first_fixed_hold(state, i0, i1, hold_windows)
    if k is None:
        extreme = float(np.max(env[i0:i1])) if command == "RFON" and i1 > i0 else (float(np.min(env[i0:i1])) if i1 > i0 else float("nan"))
        label = "max" if command == "RFON" else "min"
        target = "ON" if command == "RFON" else "OFF"
        return None, (f"CH1 does not become {target} and remain {target} for {hold_s * 1e6:g} us "
                      f"within {window_text} before through {rfon_post_ch3_s * 1e6:g} us after the CH3 edge "
                      f"(threshold {thr * 1e3:.2f} mV, {label} envelope {extreme * 1e3:.2f} mV)")
    return min(float(tw[k]), t3), "OK"


def _rfon_level(t, v, t_a):
    """Measurement 2, RF ON (B2_RFON_MODE = "level"): first point after cursor A where
    the CH1 voltage reaches B2_LEVEL['RFON'] (10 %) of the CH1 maximum voltage (the
    same value as the scope's "Maximum" measurement) and stays there. CH3 is ignored."""
    dt = t[1] - t[0]
    w = max(1, int(round(ENV_WINDOW_S / dt)))
    n = v.size // w
    peaks = v[: n * w].reshape(n, w).max(axis=1)          # positive peak per window
    k_s = min(SMOOTH_WINDOWS, n)
    peaks = np.convolve(peaks, np.ones(k_s) / k_s, "same")
    tw = t[: n * w : w]
    step = tw[1] - tw[0]

    ref = peaks[tw < t_a]
    if ref.size < 20:
        return None, "not enough RF-off data on CH1 before the command"
    base, noise = _robust(ref)
    vmax = float(v.max())
    level = B2_LEVEL["RFON"] * vmax
    floor = base + ZERO_NOISE_K * noise
    if vmax <= floor + MIN_ACTIVITY_V / 2:
        return None, "no CH1 RF activity found"
    # If 10 % of max is inside the RF-off noise, use the noise floor instead.
    level = max(level, floor)
    mask = peaks >= level
    ia = int(np.searchsorted(tw, t_a))
    pre = max(1, int(round(PRE_CHECK_S / step)))
    if _mostly(mask, ia - pre, ia + 1):
        return None, f"CH1 already above {level * 1e3:.1f} mV before the command ended"
    hold = max(MIN_HOLD_WINDOWS, int(round(B2_RFON_HOLD_S / step)))
    k = _first_held(mask, ia, tw.size - 1, None, hold_windows=hold)
    if k is None:
        return None, f"CH1 does not reach {level * 1e3:.1f} mV (10 % of max) within the capture"
    return float(tw[k]), "OK"


def find_b2(t, v, t_a, t3, command):
    """Second measurement: cursor B2 on CH1 between the command end (A) and t3.
    RF ON : first point CH1 rises to B2_LEVEL['RFON'] (10 %) of its maximum
    RF OFF: first point CH1 drops below B2_LEVEL['RFOFF'] (90 %) of its maximum
    Levels are relative to the RF-off (0) level. Returns (t_B2, status)."""
    tw, env = _envelope(t, v)
    step = tw[1] - tw[0]
    zero = _zero_level(tw, env, t_a, command)
    if zero is None:
        return None, "not enough RF-off data on CH1 to define 0"
    base, noise = zero
    vmax = np.percentile(env, 99.5)                  # robust maximum (ignores single spikes)
    if vmax - base < max(ZERO_NOISE_K * noise, MIN_ACTIVITY_V):
        return None, "no CH1 RF activity found"
    pct = B2_LEVEL[command]
    if command == "RFON" and B2_RFON_MODE == "level":
        return _rfon_level(t, v, t_a)
    level = base + pct * (vmax - base)
    ia = int(np.searchsorted(tw, t_a))
    pre = max(1, int(round(PRE_CHECK_S / step)))
    if command == "RFON":
        # CH3 doesn't matter. RF ON starts with a low-level step (~10 % of the final
        # level); B = end of that step, where CH1 starts rising to full power.
        hold = max(MIN_HOLD_WINDOWS, int(round(B2_RFON_HOLD_S / step)))
        on_thr = base + max(ZERO_NOISE_K * noise, MIN_ACTIVITY_V / 2)
        if _mostly(env > on_thr, ia - pre, ia + 1):
            return None, "CH1 already on before the command ended"
        k0 = _first_held(env > on_thr, ia, tw.size - 1, None, hold_windows=hold)
        if k0 is None:
            return None, "CH1 RF ON not found within the capture"
        p0 = k0 + max(1, int(round(B2_STEP_SKIP_S / step)))
        p1 = p0 + max(5, int(round(B2_STEP_LEN_S / step)))
        if p1 >= tw.size:
            return None, "capture ends during the initial RF ON step"
        step_level, step_noise = _robust(env[p0:p1])
        rise_thr = step_level + max(ZERO_NOISE_K * step_noise,
                                    B2_STEP_RISE_FRAC * (vmax - step_level), MIN_ACTIVITY_V / 2)
        k = _first_held(env > rise_thr, p0, tw.size - 1, None, hold_windows=hold)
        if k is None:
            return None, "CH1 does not rise above the initial RF ON step within the capture"
        # Walk back to where the envelope last sat at the step level = end of the step.
        flat = np.flatnonzero(env[p0:k + 1] <= step_level + ZERO_NOISE_K * step_noise)
        if flat.size:
            k = p0 + flat[-1] + 1
        return float(tw[k]), "OK"

    mask = env < level
    if _mostly(mask, ia - pre, ia + 1):
        return None, f"CH1 already below {pct:.0%} of max before the command ended"
    i1, i_hold = _window_indices(tw, t3)
    k = _first_held(mask, ia, i1, max(i_hold, ia))
    if k is None:
        return None, f"CH1 does not drop to {pct:.0%} of max before the CH3 edge"
    return min(float(tw[k]), t3), "OK"


def measure2(t1, v1, t_a, t3, command, status1):
    """Measurement 2 (10 % RF ON / 90 % RF OFF). RF ON needs only cursor A;
    RF OFF also needs the CH3 edge."""
    if t_a is None or (command == "RFOFF" and t3 is None):
        return None, status1
    return find_b2(t1, v1, t_a, t3, command)


def _edges(t, v):
    vmin, vmax = v.min(), v.max()
    if vmax - vmin < MIN_CMD_SWING_V:
        return None, None
    high = v > (vmin + vmax) / 2
    falls = np.flatnonzero(high[:-1] & ~high[1:]) + 1
    rises = np.flatnonzero(~high[:-1] & high[1:]) + 1
    return t[falls], t[rises]


def command_end(t, v):
    """End of the first CH2 burst after the trigger: first falling edge followed by
    at least CMD_GAP_S without a new rising edge."""
    falls, rises = _edges(t, v)
    if falls is None:
        return None
    for tf in falls[falls >= 0]:
        nxt = rises[rises > tf]
        if not nxt.size or nxt[0] - tf >= CMD_GAP_S:
            return float(tf)
    return None


def response_bursts_after(t, v, t_after):
    """Group CH3 transitions and return qualified multi-edge response bursts."""
    falls, rises = _edges(t, v)
    if falls is None:
        return [], np.array([], dtype=float)
    edges = np.sort(np.concatenate((falls, rises)))
    edges = edges[edges >= t_after]
    if not edges.size:
        return [], edges
    groups = np.split(edges, np.flatnonzero(np.diff(edges) > CH3_BURST_GAP_S) + 1)
    bursts = []
    for group in groups:
        duration = float(group[-1] - group[0]) if group.size > 1 else 0.0
        if group.size >= CH3_BURST_MIN_EDGES and duration >= CH3_BURST_MIN_DURATION_S:
            bursts.append({"start": float(group[0]), "end": float(group[-1]),
                           "edge_count": int(group.size), "duration": duration})
    return bursts, edges


def first_response_burst_after(t, v, t_after):
    bursts, edges = response_bursts_after(t, v, t_after)
    return (bursts[0]["start"] if bursts else None), bursts, edges


def measure(t1, v1, t2, v2, t3w, v3, command="RFON", b_window_s=B_WINDOW_S, rfon_post_ch3_s=RFON_POST_CH3_WINDOW_S):
    """Return timing using the first qualified multi-edge CH3 response burst."""
    t_a = command_end(t2, v2)
    if t_a is None:
        return None, None, None, "no CH2 command burst found"
    t3, bursts, edges = first_response_burst_after(t3w, v3, t_a)
    preview = ", ".join(f"{x * 1e3:.3f}" for x in edges[:CH3_EDGE_DIAGNOSTIC_LIMIT])
    if edges.size > CH3_EDGE_DIAGNOSTIC_LIMIT:
        preview += ", ..."
    if t3 is None:
        suffix = f"; CH3 edges after A (ms): {preview}" if preview else ""
        return t_a, None, None, (f"no qualifying CH3 response burst after the command "
            f"(need >= {CH3_BURST_MIN_EDGES} edges spanning >= {CH3_BURST_MIN_DURATION_S * 1e6:g} us){suffix}")
    burst = bursts[0]
    search_end = t3 + rfon_post_ch3_s if command == "RFON" else t3
    print(f"        timing: CH2 end {t_a * 1e3:.3f} ms; CH3 burst start {t3 * 1e3:.3f} ms; "
          f"burst {burst['edge_count']} edges over {burst['duration'] * 1e3:.3f} ms; "
          f"CH1 search {(t3-b_window_s) * 1e3:.3f} to {search_end * 1e3:.3f} ms", flush=True)
    t_b, status = find_b(t1, v1, t_a, t3, command, b_window_s, rfon_post_ch3_s)
    return t_a, t_b, t3, status


# ---------------------------
# Test sequence
# ---------------------------
def unique_path(path):
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{root} ({n}){ext}"):
        n += 1
    return f"{root} ({n}){ext}"


def capture_step(scope, action_name, action, setpoint, iteration, img_dir, writer, detail_writer,
                 prefix="", method=1, horizontal_scales=None, b_window_s=B_WINDOW_S, rfon_post_ch3_s=RFON_POST_CH3_WINDOW_S):
    label = f"{setpoint / 10:g}"
    suffix = f"_{B2_LEVEL[action_name] * 100:g}pct" if method == 2 else ""
    img_path = unique_path(os.path.join(
        img_dir, f"{prefix}{action_name}_{label}_{iteration}{suffix}.png"))

    t_a = t_b = t3 = dt_scope = None
    stage = "start"
    try:
        stage = "set horizontal scale"
        set_hscale(scope, action_name, horizontal_scales)
        stage = "arm scope"
        arm(scope)
        stage = f"generator {action_name}"
        action()
        stage = "wait for trigger"
        wait_for_capture(scope)
        stage = f"read {RF_CHANNEL}"
        t1, v1 = fetch_waveform(scope, RF_CHANNEL)
        stage = f"read {CMD_CHANNEL}"
        t2, v2 = fetch_waveform(scope, CMD_CHANNEL)
        stage = f"read {RESP_CHANNEL}"
        t3w, v3 = fetch_waveform(scope, RESP_CHANNEL)
        stage = "measure"
        t_a, t_b, t3, status = measure(t1, v1, t2, v2, t3w, v3, action_name, b_window_s, rfon_post_ch3_s)
        if method == 2:
            t_b, status = measure2(t1, v1, t_a, t3, action_name, status)
        stage = "set cursors"
        dt_scope = set_cursors(scope, t_a if status == "OK" else None, t_b)
        stage = "screenshot"
        save_screenshot(scope, img_path)
    except Exception as e:
        # VISA errors, timeouts included, arrive as InstrumentError; a bare TimeoutError is this
        # script's own arm/trigger timeout.
        visa_error = isinstance(e, InstrumentError)
        status = str(e) if isinstance(e, TimeoutError) and not visa_error else f"ERROR in {stage}: {e}"
        if visa_error:
            scope_err = recover_scope(scope)
            if scope_err:
                status += f" | scope says: {scope_err}"
        else:
            try:
                scope.write("ACQuire:STATE OFF")
            except Exception:
                pass
        if not os.path.exists(img_path):
            img_path = ""

    fmt_ms = lambda x: f"{x * 1e3:.4f}" if x is not None else ""
    dt = (t_b - t_a) if (t_a is not None and t_b is not None and status == "OK") else None
    writer.writerow([label, action_name, iteration, fmt_ms(dt)])
    detail_writer.writerow([datetime.now().isoformat(timespec="milliseconds"), setpoint, label,
                            action_name, iteration, method, b_window_s * 1e3, t_a, t3, t_b, fmt_ms(dt),
                            fmt_ms(dt_scope), status, img_path])
    dt_txt = f"dt = {dt * 1e3:.3f} ms" if dt is not None else status
    print(f"  {label:>6} W  {action_name:<5} #{iteration:<2}  {dt_txt}")
    return dt


def open_generator(com_port):
    if COMM_TYPE == "serial":
        return InstrumentFactory({"generator_com_port": com_port}).create("generator_com_port")
    return InstrumentFactory({"generator": IP_ADDRESS}, {"generator": "ae.serial_timing"}).create("generator")


def choose_unit():
    names = list(UNITS)
    print("Select the unit under test:")
    for i, name in enumerate(names, 1):
        print(f"  {i}. {name}")
    while True:
        choice = input(f"Enter 1-{len(names)}: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            return names[int(choice) - 1]
        if choice.lower() in UNITS:
            return choice.lower()
        print("Invalid choice.")


def parse_args():
    p = argparse.ArgumentParser(description="RF ON/OFF latency capture with a Tektronix MSO46.")
    p.add_argument("--unit", choices=list(UNITS),
                   help='unit under test, e.g. --unit "refreshed 6013" (asked if omitted)')
    p.add_argument("--scope-ip", help="scope IP address (overrides SCOPE_IP)")
    p.add_argument("--scope-interface", choices=("VXI-11", "HiSLIP"), default="VXI-11",
                   help="scope LAN protocol; default VXI-11")
    p.add_argument("--no-gui", action="store_true",
                   help="use command-line arguments instead of the configuration GUI")
    p.add_argument("--output-parent", default=os.environ.get(COLLECTION_PARENT_FOLDER_ENV, REPORT_ROOT),
                   help="parent folder where the timestamped test folder will be created")
    p.add_argument("--dut-name", default=os.environ.get(DUT_NAME_ENV, ""),
                   help="DUT name used in the output-folder name")
    p.add_argument("--com-port", help="generator serial port (overrides COM_PORT)")
    p.add_argument("--iterations", type=int, default=ITERATIONS)
    p.add_argument("--rfon-time-scale-ms", type=float, default=HSCALE_S_PER_DIV["RFON"] * 1e3,
                   help="RF ON horizontal time scale in ms/div")
    p.add_argument("--rfoff-time-scale-ms", type=float, default=HSCALE_S_PER_DIV["RFOFF"] * 1e3,
                   help="RF OFF horizontal time scale in ms/div")
    p.add_argument("--ch1-scale-v", type=float, default=0.020, help="CH1 vertical scale in V/div; default 0.020")
    p.add_argument("--ch2-scale-v", type=float, default=2.0, help="CH2 vertical scale in V/div; default 2.0")
    p.add_argument("--ch3-scale-v", type=float, default=2.0, help="CH3 vertical scale in V/div; default 2.0")
    p.add_argument("--ch1-ch3-search-window-ms", type=float, default=B_WINDOW_S * 1e3,
                   help="maximum CH1-to-CH3 association window in ms; default 2 ms")
    p.add_argument("--rfon-post-ch3-search-us", type=float, default=RFON_POST_CH3_WINDOW_S * 1e6,
                   help="RF ON search extension after CH3 burst start in us; default 500 us")
    p.add_argument("--setpoints", type=int, nargs="+",
                   help="setpoints in tenths of watts (overrides SETPOINTS_BY_MODEL)")
    p.add_argument("--check", action="store_true",
                   help="only check the scope and generator connections; RF is not turned on")
    p.add_argument("--trial", action="store_true",
                   help="test run on the real unit and scope: 1 iteration per setpoint; "
                        "files are prefixed TRIAL_ so they stay separate from real data")
    p.add_argument("-m", "--measurement", type=int, choices=sorted(MEASUREMENTS),
                   default=DEFAULT_MEASUREMENT,
                   help="cursor B method: 1 = RF ON CH1 > 0 / RF OFF CH1 = 0 (500 us before "
                        "CH3 edge); 2 = RF ON 10%% of max voltage (CH3 ignored) / "
                        "RF OFF 90%% of max (before CH3 edge). "
                        f"Default {DEFAULT_MEASUREMENT}")
    return p.parse_args()


def configure_with_gui(args):
    """Collect test and connection settings in one validated GUI."""
    root = tk.Tk()
    root.title("RF ON/OFF Scope Capture")
    root.minsize(800, 860)
    root.columnconfigure(0, weight=1)

    unit_var = tk.StringVar(value=args.unit or next(iter(UNITS)))
    scope_var = tk.StringVar(value=args.scope_ip or SCOPE_IP)
    interface_var = tk.StringVar(value=args.scope_interface or "VXI-11")
    com_var = tk.StringVar(value=args.com_port or COM_PORT)
    iterations_var = tk.StringVar(value=str(args.iterations))
    measurement_var = tk.IntVar(value=args.measurement)
    trial_var = tk.BooleanVar(value=bool(args.trial))
    setpoints_var = tk.StringVar(value="")
    output_parent_var = tk.StringVar(value=args.output_parent or REPORT_ROOT)
    dut_name_var = tk.StringVar(value=args.dut_name or os.environ.get(DUT_NAME_ENV, ""))
    rfon_time_var = tk.StringVar(value=f"{args.rfon_time_scale_ms:g}")
    rfoff_time_var = tk.StringVar(value=f"{args.rfoff_time_scale_ms:g}")
    ch1_scale_var = tk.StringVar(value="" if args.ch1_scale_v is None else f"{args.ch1_scale_v:g}")
    ch2_scale_var = tk.StringVar(value="" if args.ch2_scale_v is None else f"{args.ch2_scale_v:g}")
    ch3_scale_var = tk.StringVar(value="" if args.ch3_scale_v is None else f"{args.ch3_scale_v:g}")
    search_window_var = tk.StringVar(value=f"{args.ch1_ch3_search_window_ms:g}")
    rfon_post_ch3_var = tk.StringVar(value=f"{args.rfon_post_ch3_search_us:g}")
    action = {"value": None}

    outer = ttk.Frame(root, padding=16)
    outer.grid(sticky="nsew")
    outer.columnconfigure(1, weight=1)

    row = 0
    ttk.Label(outer, text="Unit under test").grid(row=row, column=0, sticky="w", pady=6)
    ttk.Combobox(outer, textvariable=unit_var, values=list(UNITS), state="readonly").grid(
        row=row, column=1, columnspan=2, sticky="ew", pady=6)
    row += 1
    ttk.Separator(outer).grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
    row += 1
    ttk.Label(outer, text="Scope IP / hostname").grid(row=row, column=0, sticky="w", pady=6)
    ttk.Entry(outer, textvariable=scope_var).grid(row=row, column=1, columnspan=2, sticky="ew", pady=6)
    row += 1
    ttk.Label(outer, text="Scope interface").grid(row=row, column=0, sticky="w", pady=6)
    ttk.Combobox(outer, textvariable=interface_var, values=("VXI-11", "HiSLIP"),
                 state="readonly", width=15).grid(row=row, column=1, sticky="w", pady=6)
    ttk.Label(outer, text="Try HiSLIP if VXI-11 reports link error 3.", foreground="#555555").grid(
        row=row, column=2, sticky="w", padx=(10, 0))
    row += 1
    resource_preview = tk.StringVar()
    ttk.Label(outer, text="VISA resource").grid(row=row, column=0, sticky="w", pady=6)
    ttk.Label(outer, textvariable=resource_preview, foreground="#444444").grid(
        row=row, column=1, columnspan=2, sticky="w", pady=6)
    row += 1
    ttk.Label(outer, text="Generator COM port").grid(row=row, column=0, sticky="w", pady=6)
    ttk.Entry(outer, textvariable=com_var, width=14).grid(row=row, column=1, sticky="w", pady=6)
    row += 1
    ttk.Label(outer, text="DUT name").grid(row=row, column=0, sticky="w", pady=6)
    ttk.Entry(outer, textvariable=dut_name_var).grid(row=row, column=1, columnspan=2, sticky="ew", pady=6)
    row += 1
    ttk.Label(outer, text="Save data under").grid(row=row, column=0, sticky="w", pady=6)
    ttk.Entry(outer, textvariable=output_parent_var).grid(row=row, column=1, sticky="ew", pady=6)
    def browse_output_parent():
        selected = filedialog.askdirectory(parent=root, title="Select data parent folder",
                                           initialdir=output_parent_var.get() or str(Path.home()))
        if selected:
            output_parent_var.set(selected)
    ttk.Button(outer, text="Browse...", command=browse_output_parent).grid(row=row, column=2, sticky="w", padx=(10, 0), pady=6)
    row += 1
    ttk.Label(outer, text=f"Creates: YYYY-MM-DD_HH-MM-SS - {TEST_NAME} - <DUT>",
              foreground="#555555").grid(row=row, column=1, columnspan=2, sticky="w")
    row += 1
    ttk.Separator(outer).grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
    row += 1
    ttk.Label(outer, text="Iterations").grid(row=row, column=0, sticky="w", pady=6)
    ttk.Entry(outer, textvariable=iterations_var, width=10).grid(row=row, column=1, sticky="w", pady=6)
    row += 1
    ttk.Label(outer, text="Setpoints (optional)").grid(row=row, column=0, sticky="w", pady=6)
    ttk.Entry(outer, textvariable=setpoints_var).grid(row=row, column=1, columnspan=2, sticky="ew", pady=6)
    row += 1
    ttk.Label(outer, text="Enter comma-separated tenths of watts, or leave blank for unit defaults.",
              foreground="#555555").grid(row=row, column=1, columnspan=2, sticky="w")
    row += 1
    scope_box = ttk.LabelFrame(outer, text="Scope scales", padding=10)
    scope_box.grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
    for col in range(4):
        scope_box.columnconfigure(col, weight=1 if col in (1, 3) else 0)
    ttk.Label(scope_box, text="RF ON time scale").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
    ttk.Entry(scope_box, textvariable=rfon_time_var, width=10).grid(row=0, column=1, sticky="w", pady=4)
    ttk.Label(scope_box, text="ms/div").grid(row=0, column=2, sticky="w", padx=(4, 18), pady=4)
    ttk.Label(scope_box, text="RF OFF time scale").grid(row=0, column=3, sticky="e", padx=(0, 6), pady=4)
    ttk.Entry(scope_box, textvariable=rfoff_time_var, width=10).grid(row=0, column=4, sticky="w", pady=4)
    ttk.Label(scope_box, text="ms/div").grid(row=0, column=5, sticky="w", padx=(4, 0), pady=4)
    for r, (label, variable) in enumerate((("CH1 RF output", ch1_scale_var),
                                           ("CH2 command", ch2_scale_var),
                                           ("CH3 response", ch3_scale_var)), start=1):
        ttk.Label(scope_box, text=label).grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(scope_box, textvariable=variable, width=10).grid(row=r, column=1, sticky="w", pady=4)
        ttk.Label(scope_box, text="V/div").grid(
            row=r, column=2, columnspan=4, sticky="w", padx=(4, 0), pady=4)
    ttk.Label(scope_box, text="CH1-to-CH3 search window").grid(row=4, column=0, sticky="w", pady=(10, 4))
    ttk.Entry(scope_box, textvariable=search_window_var, width=10).grid(row=4, column=1, sticky="w", pady=(10, 4))
    ttk.Label(scope_box, text="ms (default 2 ms; applies to Measurement 1)").grid(
        row=4, column=2, columnspan=4, sticky="w", padx=(4, 0), pady=(10, 4))
    ttk.Label(scope_box, text="RF ON search after CH3").grid(row=5, column=0, sticky="w", pady=4)
    ttk.Entry(scope_box, textvariable=rfon_post_ch3_var, width=10).grid(row=5, column=1, sticky="w", pady=4)
    ttk.Label(scope_box, text="us (default 500 us; RF ON only)").grid(
        row=5, column=2, columnspan=4, sticky="w", padx=(4, 0), pady=4)
    row += 1
    measurement_box = ttk.LabelFrame(outer, text="Measurement method", padding=10)
    measurement_box.grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
    for number, description in MEASUREMENTS.items():
        ttk.Radiobutton(measurement_box, text=f"{number}. {description}", variable=measurement_var,
                        value=number).pack(anchor="w", pady=3)
    row += 1
    ttk.Checkbutton(outer, text="Trial run: one iteration per setpoint", variable=trial_var).grid(
        row=row, column=0, columnspan=3, sticky="w", pady=6)
    row += 1

    def refresh_preview(*_):
        try:
            resource_preview.set(build_scope_resource(scope_var.get(), interface_var.get()))
        except ValueError as exc:
            resource_preview.set(str(exc))
    scope_var.trace_add("write", refresh_preview)
    interface_var.trace_add("write", refresh_preview)
    refresh_preview()

    def finish(mode):
        try:
            address = normalize_scope_address(scope_var.get())
            iterations = int(iterations_var.get())
            if iterations < 1:
                raise ValueError("Iterations must be at least 1.")
            com_port = com_var.get().strip()
            if COMM_TYPE == "serial" and not re.fullmatch(r"COM\d+", com_port, re.IGNORECASE):
                raise ValueError("Generator COM port must look like COM5.")
            dut_name = dut_name_var.get().strip()
            if not dut_name:
                raise ValueError("Enter the DUT name, such as PMNT012837.")
            output_parent = Path(output_parent_var.get().strip()).expanduser()
            if not output_parent.is_dir():
                raise ValueError("Select an existing parent folder for the collected data.")
            rfon_time_ms = float(rfon_time_var.get())
            rfoff_time_ms = float(rfoff_time_var.get())
            if rfon_time_ms <= 0 or rfoff_time_ms <= 0:
                raise ValueError("RF ON and RF OFF time scales must be positive values in ms/div.")
            def optional_positive_scale(text, channel):
                text = text.strip()
                if not text:
                    return None
                value = float(text)
                if value <= 0:
                    raise ValueError(f"{channel} vertical scale must be positive.")
                return value
            ch1_scale = optional_positive_scale(ch1_scale_var.get(), "CH1")
            ch2_scale = optional_positive_scale(ch2_scale_var.get(), "CH2")
            ch3_scale = optional_positive_scale(ch3_scale_var.get(), "CH3")
            search_window_ms = float(search_window_var.get())
            if search_window_ms <= 0:
                raise ValueError("CH1-to-CH3 search window must be a positive value in milliseconds.")
            rfon_post_ch3_us = float(rfon_post_ch3_var.get())
            if rfon_post_ch3_us < 0:
                raise ValueError("RF ON search after CH3 cannot be negative.")
            text = setpoints_var.get().strip()
            setpoints = None
            if text:
                setpoints = [int(x.strip()) for x in text.replace(";", ",").split(",") if x.strip()]
                if not setpoints or any(x <= 0 for x in setpoints):
                    raise ValueError("Setpoints must be positive integers in tenths of watts.")
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=root)
            return
        args.unit = unit_var.get()
        args.scope_ip = address
        args.scope_interface = interface_var.get()
        args.com_port = com_port
        args.dut_name = dut_name
        args.output_parent = str(output_parent)
        args.iterations = iterations
        args.measurement = measurement_var.get()
        args.trial = trial_var.get()
        args.setpoints = setpoints
        args.rfon_time_scale_ms = rfon_time_ms
        args.rfoff_time_scale_ms = rfoff_time_ms
        args.ch1_scale_v = ch1_scale
        args.ch2_scale_v = ch2_scale
        args.ch3_scale_v = ch3_scale
        args.ch1_ch3_search_window_ms = search_window_ms
        args.rfon_post_ch3_search_us = rfon_post_ch3_us
        args.check = mode == "check"
        action["value"] = args
        root.destroy()

    buttons = ttk.Frame(outer)
    buttons.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
    ttk.Button(buttons, text="Check Connections", command=lambda: finish("check")).pack(side="left", padx=5)
    ttk.Button(buttons, text="Start Capture", command=lambda: finish("run")).pack(side="left", padx=5)
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="left", padx=5)
    root.mainloop()
    return action["value"]


def check_connections(scope_ip, com_port, scope_interface="VXI-11"):
    scope = open_scope(scope_ip, scope_interface)
    print(f"Scope trigger state: {scope.query('TRIGger:STATE?').strip()}, "
          f"record length: {scope.query('HORizontal:RECOrdlength?').strip()}")
    # Exercise every scope step the test uses (no arming, RF stays off).
    ok = True
    for ch in dict.fromkeys((RF_CHANNEL, CMD_CHANNEL, RESP_CHANNEL)):
        on = scope.query(f"DISplay:GLObal:{ch}:STATE?").strip() in ("1", "ON")
        if not on:
            print(f"  {ch}: OFF on the scope (the test turns it on automatically)")
            continue
        try:
            t, v = fetch_waveform(scope, ch)
            print(f"  {ch}: read OK ({v.size} points)")
        except Exception as e:
            ok = False
            print(f"  {ch}: READ FAILED - {e} | scope says: {recover_scope(scope)}")
    tmp_png = os.path.join(os.environ.get("TEMP", "."), "rf_onoff_check.png")
    try:
        save_screenshot(scope, tmp_png)
        print(f"  screenshot: OK ({os.path.getsize(tmp_png)} bytes)")
        os.remove(tmp_png)
    except Exception as e:
        ok = False
        print(f"  screenshot: FAILED - {e} | scope says: {recover_scope(scope)}")
    scope.close()
    if not ok:
        raise RuntimeError("Scope check failed (see above).")
    gen = open_generator(com_port)
    try:
        mode = gen.reportActiveControlMode()
        size = gen.reportPowerSupplySize()
    except IndexError:
        raise RuntimeError(
            f"Generator did not respond on {com_port} (no AE Bus ACK). Check that the generator "
            "is powered, the serial cable/adapter is connected to its AE Bus host port, and the "
            "unit's serial settings (19200 baud, odd parity, address 1) match.") from None
    finally:
        gen.close()
    print(f"Generator on {com_port}: active control mode = {mode} (2 = host), "
          f"power supply size = {size}")
    print("Connections OK.")


def sanitize_windows_name(value):
    """Return a Windows-safe folder-name component."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value).strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "Unknown DUT"


def create_unique_test_folder(parent_folder, dut_name, now=None):
    """Create YYYY-MM-DD_HH-MM-SS - Test Name - DUT, adding Run N on collision."""
    parent = Path(parent_folder).expanduser()
    if not parent.is_dir():
        raise ValueError(f"Selected data parent folder does not exist: {parent}")
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    base = f"{timestamp} - {TEST_NAME} - {sanitize_windows_name(dut_name)}"
    candidate = parent / base
    run_number = 2
    while candidate.exists():
        candidate = parent / f"{base} - Run {run_number}"
        run_number += 1
    candidate.mkdir()
    return candidate


def main():
    args = parse_args()
    if not args.no_gui:
        args = configure_with_gui(args)
        if args is None:
            print("Cancelled.")
            return
    com_port = args.com_port or COM_PORT
    if args.check:
        check_connections(args.scope_ip, com_port, args.scope_interface)
        return

    unit = args.unit or choose_unit()
    prefix = "TRIAL_" if args.trial else ""
    iterations = 1 if args.trial else args.iterations
    model, platform = UNITS[unit]
    setpoints = args.setpoints or SETPOINTS_BY_MODEL[model]
    dut_name = args.dut_name or os.environ.get(DUT_NAME_ENV, "")
    if not dut_name:
        raise RuntimeError("DUT name is missing. Enter it in the GUI or provide PARAMOUNT_GENERATOR_ADDRESS.")
    test_dir = create_unique_test_folder(args.output_parent, dut_name)
    img_dir = test_dir / "Scope Captures"
    img_dir.mkdir()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    m = f"m{args.measurement}"
    csv_path = test_dir / f"{prefix}{platform}_{model}_rf_latency_{m}_{stamp}.csv"
    detail_path = test_dir / f"{prefix}{platform}_{model}_rf_latency_{m}_{stamp}_details.csv"
    print(f"DUT: {dut_name}")
    print(f"Data folder: {test_dir}")
    print(f"{'TRIAL RUN - ' if args.trial else ''}Unit: {unit}   setpoints: {setpoints}   "
          f"iterations: {iterations}")
    print(f"Measurement {args.measurement}: {MEASUREMENTS[args.measurement]}")

    scope = open_scope(args.scope_ip, args.scope_interface)
    configure_trigger(scope)
    ensure_channels_on(scope)
    horizontal_scales = {
        "RFON": args.rfon_time_scale_ms * 1e-3,
        "RFOFF": args.rfoff_time_scale_ms * 1e-3,
    }
    vertical_scales = {
        RF_CHANNEL: args.ch1_scale_v,
        CMD_CHANNEL: args.ch2_scale_v,
        RESP_CHANNEL: args.ch3_scale_v,
    }
    print(f"Time scales: RF ON {args.rfon_time_scale_ms:g} ms/div; "
          f"RF OFF {args.rfoff_time_scale_ms:g} ms/div")
    b_window_s = args.ch1_ch3_search_window_ms * 1e-3
    rfon_post_ch3_s = args.rfon_post_ch3_search_us * 1e-6
    print(f"Measurement 1 CH1-to-CH3 search window: {args.ch1_ch3_search_window_ms:g} ms")
    print(f"Measurement 1 RF ON search after CH3: {args.rfon_post_ch3_search_us:g} us")
    print(f"Measurement 1 fixed hold: RF ON {MEAS1_RFON_HOLD_S * 1e6:g} us; RF OFF {MEAS1_RFOFF_HOLD_S * 1e6:g} us")
    print(f"CH3 response qualification: >= {CH3_BURST_MIN_EDGES} edges over >= {CH3_BURST_MIN_DURATION_S * 1e6:g} us; burst gap {CH3_BURST_GAP_S * 1e6:g} us")
    apply_vertical_scales(scope, vertical_scales)
    gen = open_generator(com_port)
    results = {}  # (setpoint, action) -> [dt, ...]

    try:
        with open(csv_path, "w", newline="") as f, open(detail_path, "w", newline="") as fd:
            writer = csv.writer(f)
            writer.writerow(["setpoint_W", "command", "iteration", "delta_t_ms"])
            detail_writer = csv.writer(fd)
            detail_writer.writerow(["timestamp", "setpoint_raw", "setpoint_W", "command", "iteration",
                                    "measurement", "ch1_ch3_search_window_ms", "t_A_s", "t_CH3_edge_s", "t_B_s", "delta_t_ms",
                                    "delta_t_scope_cursor_ms", "status", "image"])

            gen.setActiveControlMode(2)
            rf_on = gen.rfON_TEST if USE_RFON_TEST else gen.rfON
            print(f"RF ON command: {'rfON_TEST' if USE_RFON_TEST else 'rfON'}")
            for sp in setpoints:
                try:
                    gen.setPowerSetpoint(sp)
                except Exception as e:
                    print(f"Setpoint {sp}: could not set ({e}) - skipping")
                    detail_writer.writerow([datetime.now().isoformat(), sp, f"{sp / 10:g}",
                                            "SETPOINT", "", "", "", "", "", f"ERROR: {e}", ""])
                    continue
                print(f"\nSetpoint {sp} ({sp / 10:g} W)")

                for i in range(1, iterations + 1):
                    for name, action, dwell in (("RFON", rf_on, RF_ON_DWELL_S),
                                                ("RFOFF", gen.rfOFF, RF_OFF_DWELL_S)):
                        dt = capture_step(scope, name, action, sp, i, img_dir, writer,
                                          detail_writer, prefix, args.measurement,
                                          horizontal_scales, b_window_s, rfon_post_ch3_s)
                        f.flush()
                        fd.flush()
                        if dt is not None:
                            results.setdefault((sp, name), []).append(dt)
                        time.sleep(dwell)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        try:
            gen.rfOFF()
        except Exception as e:
            print(f"WARNING: final rfOFF failed: {e}")
        gen.close()
        try:
            scope.write("ACQuire:STATE OFF")
            scope.close()
        except Exception:
            pass

    print(f"\nSummary (delta t in ms) - measurement {args.measurement}: {MEASUREMENTS[args.measurement]}")
    for (sp, name), dts in results.items():
        a = np.array(dts) * 1e3
        print(f"  {sp / 10:>6g} W  {name:<5} n={a.size:<3} mean={a.mean():.3f}  "
              f"std={a.std():.3f}  min={a.min():.3f}  max={a.max():.3f}")
    print(f"\nResults:  {csv_path}")
    print(f"Details:  {detail_path}")
    print(f"Captures: {img_dir}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
