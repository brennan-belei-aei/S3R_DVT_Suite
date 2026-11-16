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
    -m 2            RF ON : CH1 voltage reaches 10 % of the CH1 maximum voltage (the
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
import csv
import os
import sys
import time
from datetime import datetime

import numpy as np
import pyvisa

import AE_Commands_PMNT_HF_Anj

# ---------------------------
# Configuration - edit these
# ---------------------------
# Generator connection - serial by default (same meaning as in CommandProcessing.py)
COMM_TYPE = "serial"          # "serial" or "ethernet"
COM_PORT = "COM10"            # generator serial port (check Device Manager); or --com-port
IP_ADDRESS = "RFGN0714C2"     # used when COMM_TYPE == "ethernet"

# Units under test: name -> (model folder, platform folder)
UNITS = {
    "legacy 3013": ("3013", "legacy"),
    "refreshed 3013": ("3013", "refreshed"),
    "legacy 6013": ("6013", "legacy"),
    "refreshed 6013": ("6013", "refreshed"),
}
# Setpoints per model, in tenths of watts (same as gen.setPowerSetpoint). Edit per run.
SETPOINTS_BY_MODEL = {
    "3013": [100, 1000, 5000, 10000, 20000, 30000],   # 3 kW unit: 10, 100, 500, 1000, 2000, 3000 W
    "6013": [150, 1000, 5000, 15000, 30000, 60000],   # 6 kW unit: 15, 100, 500, 1500, 3000, 6000 W
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

# Scope (Ethernet). Find the IP on the scope under Utility > I/O > LAN; or --scope-ip.
SCOPE_IP = "172.24.242.29"   # a hostname also works
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
HSCALE_S_PER_DIV = {"RFON": 1e-3, "RFOFF": 2e-3}

# Cursor A = end of the first CH2 burst (the command): the first CH2 falling edge
# followed by at least CMD_GAP_S of idle line (the PC's ACK later on is ignored).
CMD_GAP_S = 0.5e-3
MIN_CMD_SWING_V = 3.0         # minimum CH2/CH3 swing to count as a valid burst

# Cursor B, anchored on t3 = first CH3 rising edge after cursor A, searched in the
# B_WINDOW_S *before* t3 (t3 - 500 us ... t3):
#   RF ON : point where CH1 becomes > 0 (and stays > 0)
#   RF OFF: point where CH1 becomes 0 (and stays 0)
# "0" = CH1 RF envelope (peak-to-peak) at its RF-off noise level.
B_WINDOW_S = 500e-6
ENV_WINDOW_S = 1e-6           # envelope window; also B's time resolution
SMOOTH_WINDOWS = 10           # moving average over this many windows (noise immunity)
ZERO_NOISE_K = 5              # CH1 counts as > 0 above RF-off level + K * noise ...
MIN_ACTIVITY_V = 0.02         # ... and at least this much above it (V p-p) / 2
HOLD_FRACTION = 0.9           # after B, CH1 must stay > 0 (RF ON) / at 0 (RF OFF) for at
                              #   least this fraction of the time up to the CH3 edge
MIN_HOLD_WINDOWS = 3          # ... checked over at least this many windows
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
def open_scope(scope_ip=None):
    rm = pyvisa.ResourceManager()
    scope_ip = scope_ip or SCOPE_IP
    resource = VISA_RESOURCE or (f"TCPIP0::{scope_ip}::inst0::INSTR" if scope_ip else None)
    if not resource:
        res = rm.list_resources("TCPIP?*")
        if not res:
            raise RuntimeError(
                "Scope IP not set and no LAN instruments found. Set SCOPE_IP at the top "
                "of this script (scope: Utility > I/O > LAN shows the IP address).")
        resource = res[0]
    try:
        scope = rm.open_resource(resource)
    except pyvisa.VisaIOError as e:
        raise RuntimeError(f"Cannot connect to scope at {resource}: {e}. Check SCOPE_IP, "
                           "that the PC and scope are on the same network, and that "
                           "LXI/VXI-11 is enabled on the scope.") from e
    scope.timeout = VISA_TIMEOUT_MS
    scope.read_termination = "\n"
    scope.write_termination = "\n"
    scope.write("HEADer OFF")
    scope.write("VERBose ON")
    print(f"Scope: {scope.query('*IDN?').strip()}  ({resource})")
    return scope


def configure_trigger(scope):
    scope.write("TRIGger:A:TYPe EDGE")
    scope.write(f"TRIGger:A:EDGE:SOUrce {TRIGGER_SOURCE}")
    scope.write("TRIGger:A:EDGE:SLOpe RISe")
    scope.write("TRIGger:A:MODe NORMal")
    scope.write("ACQuire:STOPAfter SEQuence")
    scope.query("*OPC?")


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


def set_hscale(scope, command):
    scale = HSCALE_S_PER_DIV.get(command)
    if scale:
        scope.write(f"HORizontal:SCAle {scale}")
        scope.query("*OPC?")


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
    except (ValueError, pyvisa.VisaIOError):
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


def find_b(t, v, t_a, t3, command):
    """Cursor B on CH1 within the B_WINDOW_S before t3. Returns (t_B, status)."""
    tw, env = _envelope(t, v)
    step = tw[1] - tw[0]
    zero = _zero_level(tw, env, t_a, command)
    if zero is None:
        return None, "not enough RF-off data on CH1 to define 0"
    base, noise = zero
    thr = base + max(ZERO_NOISE_K * noise, MIN_ACTIVITY_V / 2)
    active = env > thr

    i0 = int(np.searchsorted(tw, t3 - B_WINDOW_S))
    i1, i_hold = _window_indices(tw, t3)
    pre = max(1, int(round(PRE_CHECK_S / step)))
    if command == "RFON":
        if _mostly(active, i0 - pre, i0 + 1):
            return None, "CH1 already > 0 more than 500 us before the CH3 edge"
        k = _first_held(active, i0, i1, i_hold)
        if k is None:
            return None, "CH1 does not become > 0 within 500 us before the CH3 edge"
    else:
        if not active[tw < t_a].any():
            return None, "CH1 was not on before the RF OFF command"
        quiet = ~active
        if _mostly(quiet, i0 - pre, i0 + 1):
            return None, "CH1 already 0 more than 500 us before the CH3 edge"
        k = _first_held(quiet, i0, i1, i_hold)
        if k is None:
            return None, "CH1 does not reach 0 within 500 us before the CH3 edge"
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


def first_rise_after(t, v, t_after):
    _, rises = _edges(t, v)
    if rises is None:
        return None
    rises = rises[rises > t_after]
    return float(rises[0]) if rises.size else None


def measure(t1, v1, t2, v2, t3w, v3, command="RFON"):
    """Return (t_A, t_B, t_CH3, status).
    A = end of the command on CH2; B = CH1 > 0 (RF ON) or CH1 = 0 (RF OFF)
    within B_WINDOW_S of the first CH3 rising edge after A."""
    t_a = command_end(t2, v2)
    if t_a is None:
        return None, None, None, "no CH2 command burst found"
    t3 = first_rise_after(t3w, v3, t_a)
    if t3 is None:
        return t_a, None, None, "no CH3 response edge found after the command"
    t_b, status = find_b(t1, v1, t_a, t3, command)
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
                 prefix="", method=1):
    label = f"{setpoint / 10:g}"
    suffix = f"_{B2_LEVEL[action_name] * 100:g}pct" if method == 2 else ""
    img_path = unique_path(os.path.join(
        img_dir, f"{prefix}{action_name}_{label}_{iteration}{suffix}.png"))

    t_a = t_b = t3 = dt_scope = None
    stage = "start"
    try:
        stage = "set horizontal scale"
        set_hscale(scope, action_name)
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
        t_a, t_b, t3, status = measure(t1, v1, t2, v2, t3w, v3, action_name)
        if method == 2:
            t_b, status = measure2(t1, v1, t_a, t3, action_name, status)
        stage = "set cursors"
        dt_scope = set_cursors(scope, t_a if status == "OK" else None, t_b)
        stage = "screenshot"
        save_screenshot(scope, img_path)
    except Exception as e:
        status = str(e) if isinstance(e, TimeoutError) else f"ERROR in {stage}: {e}"
        if not isinstance(e, TimeoutError) and isinstance(e, pyvisa.VisaIOError):
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
                            action_name, iteration, method, t_a, t3, t_b, fmt_ms(dt),
                            fmt_ms(dt_scope), status, img_path])
    dt_txt = f"dt = {dt * 1e3:.3f} ms" if dt is not None else status
    print(f"  {label:>6} W  {action_name:<5} #{iteration:<2}  {dt_txt}")
    return dt


def open_generator(com_port):
    if COMM_TYPE == "serial":
        return AE_Commands_PMNT_HF_Anj.generator(0, com_port)
    return AE_Commands_PMNT_HF_Anj.generator(IP_ADDRESS, None)


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
    p.add_argument("--com-port", help="generator serial port (overrides COM_PORT)")
    p.add_argument("--iterations", type=int, default=ITERATIONS)
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


def check_connections(scope_ip, com_port):
    scope = open_scope(scope_ip)
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


def main():
    args = parse_args()
    com_port = args.com_port or COM_PORT
    if args.check:
        check_connections(args.scope_ip, com_port)
        return

    unit = args.unit or choose_unit()
    prefix = "TRIAL_" if args.trial else ""
    iterations = 1 if args.trial else args.iterations
    model, platform = UNITS[unit]
    setpoints = args.setpoints or SETPOINTS_BY_MODEL[model]
    unit_dir = os.path.join(REPORT_ROOT, model)
    img_dir = os.path.join(unit_dir, platform)
    os.makedirs(img_dir, exist_ok=True)
    stamp = f"{datetime.now():%Y-%m-%d_%H%M%S}"
    m = f"m{args.measurement}"
    csv_path = os.path.join(unit_dir, f"{prefix}{platform}_{model}_rf_latency_{m}_{stamp}.csv")
    detail_path = os.path.join(unit_dir, f"{prefix}{platform}_{model}_rf_latency_{m}_{stamp}_details.csv")
    print(f"{'TRIAL RUN - ' if args.trial else ''}Unit: {unit}   setpoints: {setpoints}   "
          f"iterations: {iterations}")
    print(f"Measurement {args.measurement}: {MEASUREMENTS[args.measurement]}")

    scope = open_scope(args.scope_ip)
    configure_trigger(scope)
    ensure_channels_on(scope)
    gen = open_generator(com_port)
    results = {}  # (setpoint, action) -> [dt, ...]

    try:
        with open(csv_path, "w", newline="") as f, open(detail_path, "w", newline="") as fd:
            writer = csv.writer(f)
            writer.writerow(["setpoint_W", "command", "iteration", "delta_t_ms"])
            detail_writer = csv.writer(fd)
            detail_writer.writerow(["timestamp", "setpoint_raw", "setpoint_W", "command", "iteration",
                                    "measurement", "t_A_s", "t_CH3_edge_s", "t_B_s", "delta_t_ms",
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
                                          detail_writer, prefix, args.measurement)
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
