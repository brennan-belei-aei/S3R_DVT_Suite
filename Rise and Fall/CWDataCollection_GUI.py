# Note: this script has been verified as functional on 9/9/26
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default
from suite_common.inputs import format_number_list, parse_number_list
from suite_common.output_folders import create_unique_test_output_folder

import pyvisa
import numpy as np
import pandas as pd
from scipy.signal import hilbert
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from datetime import datetime
from time import sleep, time
import AECommandsUniversal

# TODO: Send command in scope setup function to set channel input impedance to 50 Ohms.

# ==========================================================
# USER SETTINGS
# ==========================================================

SCOPE_ADDRESS = bench_default("oscilloscope")


def extract_ip_address(value):
    """Return only the IPv4/hostname portion from an IP or VISA resource string."""
    value = str(value).strip()
    if value.upper().startswith("TCPIP") and "::" in value:
        parts = value.split("::")
        if len(parts) >= 2 and parts[1].strip():
            return parts[1].strip()
    return value


def scope_visa_resource(ip_address):
    """Build the PyVISA resource string from the scope IP address."""
    ip_address = extract_ip_address(ip_address)
    if not ip_address:
        raise ValueError("Oscilloscope IP address cannot be blank.")
    return f"TCPIP0::{ip_address}::INSTR"


# The GUI accepts only the numeric IP/hostname, including when a bench profile
# supplies a complete VISA resource string.
SCOPE_ADDRESS = extract_ip_address(SCOPE_ADDRESS)
GEN_IP = bench_default("generator")
SELECTED_CAPTURE_MODE = "RISE"

NUM_ACQUISITIONS = 25
# Channels
TRIGGER_CHANNEL = "CH1"      # RF Enable
RF_CHANNEL = "CH1"           # 13.56 MHz RF

CH_1 = "CH1"
CH_2 = "CH2"

# Scope setup
TIME_PER_DIV = 5e-3
VERT_SCALE_TRIG = 0.3        # V/div
VERT_SCALE_RF = 1        # V/div
# These can be adjusted if trigger at certain setpoints is having a tough time

TRIGGER_LEVEL = 2.5          # Sync Out RISE trigger level, volts
# TRIGGER_SLOPE = "RISE"
TRIGGER_LEVEL_FALL = 2.5     # Sync Out FALL trigger level, volts

# FALL-only scope settings. DUT Sync Out is monitored by oscilloscope CH2.
FALL_TIME_PER_DIV = 2e-3

# FALL command-processing delays. These are intentionally conservative for
# initial hardware testing and can be shortened after reliable operation is
# confirmed.
FALL_MODE_2_SETTLE_SEC = 0.250
FALL_SETPOINT_SETTLE_SEC = 1.000
FALL_RF_ON_SETTLE_SEC = 1.000
FALL_SCOPE_ARM_SETTLE_SEC = 0.250

# FALL horizontal-position mapping. The lowest configured power setpoint maps
# to 60 percent and the highest maps to 40 percent. Intermediate setpoints are
# linearly interpolated and clamped to this range.
FALL_HORIZONTAL_POSITION_LOWEST_SETPOINT_PERCENT = 45.0
FALL_HORIZONTAL_POSITION_HIGHEST_SETPOINT_PERCENT = 35.0
# Used only as a fallback when no configured setpoint range is available.
FALL_HORIZONTAL_POSITION_PERCENT = 50.0
# False: use FALL_HORIZONTAL_POSITION_PERCENT for every FALL capture.
# True: interpolate horizontal position between the low/high-setpoint values.
FALL_SHIFT_HORIZONTAL_POSITION_BY_SETPOINT = False

RECORD_LENGTH = 10000000

# Envelope rise calculation
RISE_LOW = 0.10
RISE_HIGH = 0.90


HALO_MODE = 10
freqsMHz = [1800, 1985, 2170]

# Belei Note: We need averages for each setpoint at each frequency, and
power_setpoints = [6, 10, 25, 50, 100, 150, 200, 250, 300, 400, 500]
COUPLER_DB = 60
SYSTEM_IMPEDANCE = 50

# Waveforms are transferred from the scope over Ethernet and saved locally.
# The user selects a parent folder each time the script runs.
SAVE_DIRECTORY = None
STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER = str(Path.home())
DEFAULT_OUTPUT_PARENT_FOLDER = (
    os.environ.get(
        "PARAMOUNT_COLLECTION_PARENT_FOLDER",
        STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER,
    ).strip()
    or STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER
)
TEST_FOLDER_NAME = "CW Statistical Timing Analysis"
WAVEFORM_TRANSFER_TIMEOUT_MS = 120_000
WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES = 25 * 1024 * 1024


def calculate_fall_horizontal_position_percent(
    power_setpoint,
    configured_setpoints=None,
):
    """Linearly map a FALL power setpoint to scope horizontal position.

    The lowest configured setpoint maps to
    FALL_HORIZONTAL_POSITION_LOWEST_SETPOINT_PERCENT. The highest configured
    setpoint maps to FALL_HORIZONTAL_POSITION_HIGHEST_SETPOINT_PERCENT.
    Intermediate setpoints are linearly interpolated. Values outside the
    configured range are clamped to the nearest endpoint.
    """
    if configured_setpoints is None:
        configured_setpoints = power_setpoints

    finite_setpoints = [
        float(value)
        for value in configured_setpoints
        if np.isfinite(float(value))
    ]
    if not finite_setpoints:
        return float(FALL_HORIZONTAL_POSITION_PERCENT)

    lowest_setpoint = min(finite_setpoints)
    highest_setpoint = max(finite_setpoints)
    requested_setpoint = float(power_setpoint)
    if not np.isfinite(requested_setpoint):
        raise ValueError("FALL power setpoint must be finite.")

    if highest_setpoint == lowest_setpoint:
        return float(
            (
                FALL_HORIZONTAL_POSITION_LOWEST_SETPOINT_PERCENT
                + FALL_HORIZONTAL_POSITION_HIGHEST_SETPOINT_PERCENT
            )
            / 2.0
        )

    bounded_setpoint = min(
        max(requested_setpoint, lowest_setpoint),
        highest_setpoint,
    )
    setpoint_fraction = (
        (bounded_setpoint - lowest_setpoint)
        / (highest_setpoint - lowest_setpoint)
    )
    horizontal_position = (
        FALL_HORIZONTAL_POSITION_LOWEST_SETPOINT_PERCENT
        + setpoint_fraction
        * (
            FALL_HORIZONTAL_POSITION_HIGHEST_SETPOINT_PERCENT
            - FALL_HORIZONTAL_POSITION_LOWEST_SETPOINT_PERCENT
        )
    )
    return float(horizontal_position)

# ==========================================================
# GRAPHICAL RUN CONFIGURATION
# ==========================================================
def configure_run_with_gui():
    """Edit run settings graphically while preserving script values as defaults."""
    global SCOPE_ADDRESS, GEN_IP, SELECTED_CAPTURE_MODE
    global NUM_ACQUISITIONS, TRIGGER_CHANNEL, RF_CHANNEL, CH_1, CH_2
    global TIME_PER_DIV, VERT_SCALE_TRIG, VERT_SCALE_RF
    global TRIGGER_LEVEL, TRIGGER_LEVEL_FALL
    global FALL_TIME_PER_DIV
    global FALL_MODE_2_SETTLE_SEC, FALL_SETPOINT_SETTLE_SEC
    global FALL_RF_ON_SETTLE_SEC, FALL_SCOPE_ARM_SETTLE_SEC
    global FALL_HORIZONTAL_POSITION_LOWEST_SETPOINT_PERCENT
    global FALL_HORIZONTAL_POSITION_HIGHEST_SETPOINT_PERCENT
    global FALL_HORIZONTAL_POSITION_PERCENT, FALL_SHIFT_HORIZONTAL_POSITION_BY_SETPOINT, RECORD_LENGTH
    global RISE_LOW, RISE_HIGH, HALO_MODE
    global freqsMHz, power_setpoints, COUPLER_DB, SYSTEM_IMPEDANCE
    global TEST_FOLDER_NAME, WAVEFORM_TRANSFER_TIMEOUT_MS
    global WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES

    root = tk.Tk()
    root.title("CW Statistical Timing Data Collection Setup")
    root.geometry("940x760")
    root.minsize(820, 650)

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
    ttk.Label(outer, text="CW Statistical Timing Data Collection", style="Header.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Review or adjust this run. Every field starts from the global variables "
            "in the script, so editing the script directly remains the persistent default."
        ),
        wraplength=880,
    ).pack(anchor="w", pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    run_tab = ttk.Frame(notebook, padding=12)
    instrument_tab = ttk.Frame(notebook, padding=12)
    scope_tab = ttk.Frame(notebook, padding=12)
    fall_tab = ttk.Frame(notebook, padding=12)
    advanced_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(run_tab, text="Test Matrix")
    notebook.add(instrument_tab, text="Instruments")
    notebook.add(scope_tab, text="Scope")
    notebook.add(fall_tab, text="FALL Sequence")
    notebook.add(advanced_tab, text="Advanced")
    notebook.add(output_tab, text="Output")

    variables = {}

    def add_entry(parent, row, label, key, default, help_text="", width=34):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        var = tk.StringVar(value=str(default))
        variables[key] = var
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, sticky="ew", pady=4)
        if help_text:
            ttk.Label(parent, text=help_text, foreground="#555555", wraplength=350).grid(
                row=row, column=2, sticky="w", padx=(10, 0), pady=4
            )

    output_parent = tk.StringVar(value=DEFAULT_OUTPUT_PARENT_FOLDER)
    ttk.Label(output_tab, text="Parent output folder", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
    ttk.Entry(output_tab, textvariable=output_parent, width=72).grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
    def browse_output_parent():
        current = output_parent.get().strip()
        selected = filedialog.askdirectory(title="Select parent folder for CW waveform data", initialdir=current if Path(current).is_dir() else STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER, mustexist=True, parent=root)
        if selected:
            output_parent.set(selected)
    ttk.Button(output_tab, text="Browse...", command=browse_output_parent).grid(row=1, column=2, sticky="w", padx=(10, 0), pady=4)
    ttk.Label(output_tab, text="The timestamped CW test folder is created inside this parent folder only after Start Collection is clicked and all settings validate successfully.", foreground="#555555", wraplength=760).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
    output_tab.columnconfigure(0, weight=1)

    capture_mode = tk.StringVar(value=SELECTED_CAPTURE_MODE)
    ttk.Label(run_tab, text="Capture mode", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 5))
    mode_frame = ttk.Frame(run_tab)
    mode_frame.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 12))
    ttk.Radiobutton(mode_frame, text="Rising Edge", variable=capture_mode, value="RISE").pack(side="left", padx=(0, 20))
    ttk.Radiobutton(mode_frame, text="Falling Edge", variable=capture_mode, value="FALL").pack(side="left")
    add_entry(run_tab, 2, "Acquisitions per setpoint", "num_acq", NUM_ACQUISITIONS)
    add_entry(run_tab, 3, "RF frequencies (kHz)", "freqs", format_number_list(freqsMHz), "Comma-separated values.")
    add_entry(run_tab, 4, "Power setpoints (script units)", "setpoints", format_number_list(power_setpoints), "Actual watts = value x HALO_MODE.")
    add_entry(run_tab, 5, "HALO mode multiplier", "halo", HALO_MODE)
    planned = tk.StringVar()
    ttk.Label(run_tab, textvariable=planned, style="Section.TLabel").grid(row=6, column=0, columnspan=3, sticky="w", pady=(14, 0))

    add_entry(instrument_tab, 0, "Oscilloscope IP address", "scope_address", SCOPE_ADDRESS, width=48)
    add_entry(instrument_tab, 2, "DUT IP / hostname", "gen_ip", GEN_IP, width=48)
    add_entry(instrument_tab, 3, "Trigger channel", "trigger_channel", TRIGGER_CHANNEL)
    add_entry(instrument_tab, 4, "RF channel", "rf_channel", RF_CHANNEL)
    add_entry(instrument_tab, 5, "Scope CH1 alias", "ch1", CH_1)
    add_entry(instrument_tab, 6, "Scope CH2 alias", "ch2", CH_2)
    add_entry(instrument_tab, 7, "Test folder name", "folder_name", TEST_FOLDER_NAME)

    add_entry(scope_tab, 0, "RISE time per division (s/div)", "time_div", TIME_PER_DIV)
    add_entry(scope_tab, 1, "FALL time per division (s/div)", "fall_time_div", FALL_TIME_PER_DIV)
    add_entry(scope_tab, 2, "Trigger-channel scale (V/div)", "vert_trig", VERT_SCALE_TRIG)
    add_entry(scope_tab, 3, "RF-channel scale (V/div)", "vert_rf", VERT_SCALE_RF)
    add_entry(scope_tab, 4, "Sync Out RISE trigger level (V)", "trig_level", TRIGGER_LEVEL)
    add_entry(scope_tab, 5, "Sync Out FALL trigger level (V)", "fall_trig_level", TRIGGER_LEVEL_FALL)
    add_entry(scope_tab, 6, "Record length", "record_length", RECORD_LENGTH)
    shift_fall_position = tk.BooleanVar(
        value=FALL_SHIFT_HORIZONTAL_POSITION_BY_SETPOINT
    )
    ttk.Checkbutton(
        scope_tab,
        text="Shift FALL horizontal position based on power setpoint",
        variable=shift_fall_position,
    ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 4))
    add_entry(scope_tab, 8, "FALL low-setpoint horizontal position (%)", "fall_pos_low", FALL_HORIZONTAL_POSITION_LOWEST_SETPOINT_PERCENT)
    add_entry(scope_tab, 9, "FALL high-setpoint horizontal position (%)", "fall_pos_high", FALL_HORIZONTAL_POSITION_HIGHEST_SETPOINT_PERCENT)
    add_entry(scope_tab, 10, "FALL fixed horizontal position (%)", "fall_pos_fallback", FALL_HORIZONTAL_POSITION_PERCENT)

    add_entry(fall_tab, 2, "Mode 2 settle (s)", "mode2_settle", FALL_MODE_2_SETTLE_SEC)
    add_entry(fall_tab, 3, "Setpoint settle (s)", "setpoint_settle", FALL_SETPOINT_SETTLE_SEC)
    add_entry(fall_tab, 6, "RF On settle (s)", "rf_on_settle", FALL_RF_ON_SETTLE_SEC)
    add_entry(fall_tab, 7, "Scope-arm settle (s)", "scope_arm_settle", FALL_SCOPE_ARM_SETTLE_SEC)
    ttk.Label(
        fall_tab,
        text=(
            "RISE and FALL use DUT host control for power and RF On/Off. "
            "Oscilloscope CH2 triggers on the DUT Sync Out edge; CH1 captures "
            "the RF envelope."
        ),
        foreground="#555555",
        wraplength=750,
    ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(14, 0))

    add_entry(advanced_tab, 0, "Rise low fraction", "rise_low", RISE_LOW)
    add_entry(advanced_tab, 1, "Rise high fraction", "rise_high", RISE_HIGH)
    add_entry(advanced_tab, 3, "Coupler attenuation (dB)", "coupler_db", COUPLER_DB)
    add_entry(advanced_tab, 4, "System impedance (ohms)", "impedance", SYSTEM_IMPEDANCE)
    add_entry(advanced_tab, 5, "Transfer timeout (ms)", "transfer_timeout", WAVEFORM_TRANSFER_TIMEOUT_MS)
    add_entry(advanced_tab, 6, "Transfer chunk size (bytes)", "chunk_size", WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES)

    result = {"accepted": False}

    def number(key, label, minimum=None, maximum=None, integer=False):
        try:
            value = float(variables[key].get().strip())
        except ValueError as exc:
            raise ValueError(f"{label} must be numeric.") from exc
        if not np.isfinite(value):
            raise ValueError(f"{label} must be finite.")
        if integer:
            if not value.is_integer():
                raise ValueError(f"{label} must be a whole number.")
            value = int(value)
        if minimum is not None and value < minimum:
            raise ValueError(f"{label} must be at least {minimum}.")
        if maximum is not None and value > maximum:
            raise ValueError(f"{label} must be no greater than {maximum}.")
        return value

    def update_preview(*_):
        try:
            count = int(float(variables["num_acq"].get()))
            f_count = len(parse_number_list(variables["freqs"].get(), "RF frequencies"))
            p_count = len(parse_number_list(variables["setpoints"].get(), "power setpoints"))
            planned.set(f"Planned waveform files: {count * f_count * p_count:,}")
        except Exception:
            planned.set("Planned waveform files: check entered values")

    def apply_and_start():
        nonlocal result
        global SCOPE_ADDRESS, GEN_IP, SELECTED_CAPTURE_MODE
        global SAVE_DIRECTORY
        global NUM_ACQUISITIONS, TRIGGER_CHANNEL, RF_CHANNEL, CH_1, CH_2
        global TIME_PER_DIV, VERT_SCALE_TRIG, VERT_SCALE_RF
        global TRIGGER_LEVEL, TRIGGER_LEVEL_FALL
        global FALL_TIME_PER_DIV
        global FALL_MODE_2_SETTLE_SEC, FALL_SETPOINT_SETTLE_SEC
        global FALL_RF_ON_SETTLE_SEC, FALL_SCOPE_ARM_SETTLE_SEC
        global FALL_HORIZONTAL_POSITION_LOWEST_SETPOINT_PERCENT
        global FALL_HORIZONTAL_POSITION_HIGHEST_SETPOINT_PERCENT
        global FALL_HORIZONTAL_POSITION_PERCENT, FALL_SHIFT_HORIZONTAL_POSITION_BY_SETPOINT, RECORD_LENGTH
        global RISE_LOW, RISE_HIGH, HALO_MODE
        global freqsMHz, power_setpoints, COUPLER_DB, SYSTEM_IMPEDANCE
        global TEST_FOLDER_NAME, WAVEFORM_TRANSFER_TIMEOUT_MS
        global WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES
        try:
            selected_parent_text = output_parent.get().strip()
            if not selected_parent_text:
                raise ValueError("Select a parent output folder on the Output tab.")
            selected_parent = Path(selected_parent_text).expanduser().resolve()
            if not selected_parent.exists() or not selected_parent.is_dir():
                raise ValueError("The selected parent output folder does not exist.")
            mode = capture_mode.get()
            if mode not in ("RISE", "FALL"):
                raise ValueError("Select Rising Edge or Falling Edge capture.")
            new_freqs = parse_number_list(variables["freqs"].get(), "RF frequencies", integer_only=True)
            new_setpoints = parse_number_list(variables["setpoints"].get(), "power setpoints")
            if any(value <= 0 for value in new_freqs):
                raise ValueError("RF frequencies must be greater than zero.")
            if any(value < 0 for value in new_setpoints):
                raise ValueError("Power setpoints cannot be negative.")

            values = {
                "num_acq": number("num_acq", "Acquisitions", 1, integer=True),
                "halo": number("halo", "HALO mode", 0.000001),
                "time_div": number("time_div", "RISE time per division", 0.000000001),
                "fall_time_div": number("fall_time_div", "FALL time per division", 0.000000001),
                "vert_trig": number("vert_trig", "Trigger scale", 0.000001),
                "vert_rf": number("vert_rf", "RF scale", 0.000001),
                "trig_level": number("trig_level", "RISE trigger level"),
                "fall_trig_level": number("fall_trig_level", "FALL trigger level"),
                "record_length": number("record_length", "Record length", 1, integer=True),
                "fall_pos_low": number("fall_pos_low", "FALL low-setpoint position", 0, 100),
                "fall_pos_high": number("fall_pos_high", "FALL high-setpoint position", 0, 100),
                "fall_pos_fallback": number("fall_pos_fallback", "FALL fallback position", 0, 100),
                "mode2_settle": number("mode2_settle", "Mode 2 settle", 0),
                "setpoint_settle": number("setpoint_settle", "Setpoint settle", 0),
                "rf_on_settle": number("rf_on_settle", "RF On settle", 0),
                "scope_arm_settle": number("scope_arm_settle", "Scope-arm settle", 0),
                "rise_low": number("rise_low", "Rise low fraction", 0, 1),
                "rise_high": number("rise_high", "Rise high fraction", 0, 1),
                "coupler_db": number("coupler_db", "Coupler attenuation", 0),
                "impedance": number("impedance", "System impedance", 0.000001),
                "transfer_timeout": number("transfer_timeout", "Transfer timeout", 1, integer=True),
                "chunk_size": number("chunk_size", "Transfer chunk size", 1, integer=True),
            }
            if values["rise_low"] >= values["rise_high"]:
                raise ValueError("Rise low fraction must be less than rise high fraction.")
            text_values = {
                key: variables[key].get().strip()
                for key in ("scope_address", "gen_ip", "trigger_channel", "rf_channel", "ch1", "ch2", "folder_name")
            }
            if any(not value for value in text_values.values()):
                raise ValueError("Scope/DUT addresses, channels, and folder name cannot be blank.")

            SCOPE_ADDRESS = text_values["scope_address"]
            GEN_IP = text_values["gen_ip"]
            SELECTED_CAPTURE_MODE = mode
            NUM_ACQUISITIONS = values["num_acq"]
            TRIGGER_CHANNEL = text_values["trigger_channel"].upper()
            RF_CHANNEL = text_values["rf_channel"].upper()
            CH_1 = text_values["ch1"].upper()
            CH_2 = text_values["ch2"].upper()
            TIME_PER_DIV = values["time_div"]
            FALL_TIME_PER_DIV = values["fall_time_div"]
            VERT_SCALE_TRIG = values["vert_trig"]
            VERT_SCALE_RF = values["vert_rf"]
            TRIGGER_LEVEL = values["trig_level"]
            TRIGGER_LEVEL_FALL = values["fall_trig_level"]
            RECORD_LENGTH = values["record_length"]
            FALL_HORIZONTAL_POSITION_LOWEST_SETPOINT_PERCENT = values["fall_pos_low"]
            FALL_HORIZONTAL_POSITION_HIGHEST_SETPOINT_PERCENT = values["fall_pos_high"]
            FALL_HORIZONTAL_POSITION_PERCENT = values["fall_pos_fallback"]
            FALL_SHIFT_HORIZONTAL_POSITION_BY_SETPOINT = bool(
                shift_fall_position.get()
            )
            FALL_MODE_2_SETTLE_SEC = values["mode2_settle"]
            FALL_SETPOINT_SETTLE_SEC = values["setpoint_settle"]
            FALL_RF_ON_SETTLE_SEC = values["rf_on_settle"]
            FALL_SCOPE_ARM_SETTLE_SEC = values["scope_arm_settle"]
            RISE_LOW = values["rise_low"]
            RISE_HIGH = values["rise_high"]
            HALO_MODE = values["halo"]
            freqsMHz = new_freqs
            power_setpoints = new_setpoints
            COUPLER_DB = values["coupler_db"]
            SYSTEM_IMPEDANCE = values["impedance"]
            TEST_FOLDER_NAME = text_values["folder_name"]
            WAVEFORM_TRANSFER_TIMEOUT_MS = values["transfer_timeout"]
            WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES = values["chunk_size"]
            SAVE_DIRECTORY = create_unique_test_output_folder(selected_parent, TEST_FOLDER_NAME, GEN_IP)
            print(f"Selected parent folder: {selected_parent}")
            print(f"Test data folder: {SAVE_DIRECTORY}")
            result["accepted"] = True
            root.destroy()
        except Exception as exc:
            messagebox.showerror("Invalid collection settings", str(exc), parent=root)

    for var in variables.values():
        var.trace_add("write", update_preview)
    update_preview()
    buttons = ttk.Frame(outer)
    buttons.pack(fill="x", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text="Start Collection", style="Start.TButton", command=apply_and_start).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    if not result["accepted"]:
        raise SystemExit("Collection cancelled before instrument connection.")

def validate_gui_global_definitions():
    """Fail clearly if a future edit removes a setting required by the GUI."""
    required_names = (
        "SCOPE_ADDRESS", "GEN_IP",
        "SELECTED_CAPTURE_MODE", "NUM_ACQUISITIONS", "TRIGGER_CHANNEL",
        "RF_CHANNEL", "CH_1", "CH_2", "TIME_PER_DIV",
        "FALL_TIME_PER_DIV", "VERT_SCALE_TRIG", "VERT_SCALE_RF",
        "TRIGGER_LEVEL", "TRIGGER_LEVEL_FALL", "RECORD_LENGTH",
        "freqsMHz", "power_setpoints", "HALO_MODE", "TEST_FOLDER_NAME",
    )
    missing_names = [name for name in required_names if name not in globals()]
    if missing_names:
        raise RuntimeError(
            "Required GUI settings are not defined: " + ", ".join(missing_names)
        )


# ==========================================================
# SELECT LOCAL SAVE LOCATION
# ==========================================================
def select_save_directory():
    """Prompt for a parent folder and create the CW test run folder."""
    global SAVE_DIRECTORY
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    try:
        selected_directory = filedialog.askdirectory(
            title="Select parent folder for CW waveform data",
            initialdir=(
                DEFAULT_OUTPUT_PARENT_FOLDER
                if Path(DEFAULT_OUTPUT_PARENT_FOLDER).is_dir()
                else STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER
            ),
            mustexist=True,
            parent=root,
        )
    finally:
        root.destroy()

    if not selected_directory:
        raise SystemExit("No waveform save location was selected. Test cancelled.")

    selected_parent = Path(selected_directory).resolve()
    SAVE_DIRECTORY = create_unique_test_output_folder(
        selected_parent,
        TEST_FOLDER_NAME,
        GEN_IP,
    )

    print(f"\nSelected parent folder: {selected_parent}")
    print(f"Test data folder: {SAVE_DIRECTORY}")

class CollectionProgressWindow:
    """Visible status window for the existing synchronous collection sequence."""
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CW Data Collection Progress")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._prevent_close)
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="CW waveform collection is running",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(frame, text="The current setup or acquisition step is shown below.",
                  foreground="#555555").pack(anchor="w", pady=(3, 14))
        self.phase_var = tk.StringVar(value="Preparing collection...")
        self.detail_var = tk.StringVar(value="Initializing")
        self.count_var = tk.StringVar(value="0 of 0 attempts")
        self.percent_var = tk.StringVar(value="0%")
        self.warning_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.phase_var,
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.detail_var, wraplength=620).pack(anchor="w", pady=(3, 5))
        ttk.Label(frame, textvariable=self.count_var, foreground="#555555").pack(anchor="w", pady=(0, 9))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.bar = ttk.Progressbar(row, mode="determinate", maximum=100, length=540)
        self.bar.pack(side="left", fill="x", expand=True)
        ttk.Label(row, textvariable=self.percent_var, width=6, anchor="e").pack(side="left", padx=(10, 0))
        self.activity = ttk.Progressbar(frame, mode="indeterminate", length=600)
        self.activity.pack(fill="x", pady=(10, 0))
        self.activity.start(12)
        ttk.Label(frame, textvariable=self.warning_var, foreground="#A4262C",
                  wraplength=620).pack(anchor="w", pady=(10, 0))
        self.root.update_idletasks()
        width, height = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        try: self.root.attributes("-topmost", True)
        except tk.TclError: pass
        self.refresh()

    def _prevent_close(self):
        try: self.root.bell()
        except tk.TclError: pass

    def refresh(self):
        try:
            if not self.root.winfo_viewable(): self.root.deiconify()
            self.root.lift()
            self.root.update_idletasks()
            self.root.update()
        except tk.TclError:
            pass

    def update(self, percent=None, phase=None, detail=None, completed=None, total=None):
        if percent is not None:
            value = max(0.0, min(100.0, float(percent)))
            self.bar["value"] = value
            self.percent_var.set(f"{value:.0f}%")
        if phase is not None: self.phase_var.set(str(phase))
        if detail is not None: self.detail_var.set(str(detail))
        if completed is not None and total is not None:
            self.count_var.set(f"{completed:,} of {total:,} attempts")
        self.refresh()

    def warning(self, message):
        self.warning_var.set(str(message))
        self.refresh()

    def close(self):
        try:
            self.activity.stop()
            self.root.destroy()
        except tk.TclError:
            pass


def report_collection_warning(message):
    """Print a visible nonfatal warning without altering collection sequencing."""
    text = f"WARNING: {message}"
    print("\n" + text, flush=True)
    try: progress_window.warning(text)
    except Exception: pass


def planned_collection_attempts():
    return max(1, len(freqsMHz) * NUM_ACQUISITIONS * len(power_setpoints))


def update_collection_attempt(attempt_number, freq, cycle, power_setpoint):
    total = planned_collection_attempts()
    percent = 12.0 + (max(0, attempt_number - 1) / total) * 84.0
    progress_window.update(
        percent,
        f"Collecting {SELECTED_CAPTURE_MODE} waveforms",
        f"RF {freq} kHz | Cycle {cycle}/{NUM_ACQUISITIONS} | "
        f"Power {power_setpoint * HALO_MODE:g} W",
        attempt_number - 1, total
    )


validate_gui_global_definitions()
configure_run_with_gui()
# integrated into Output tab
progress_window = CollectionProgressWindow()
progress_window.update(1, "Preparing collection", f"Output folder: {SAVE_DIRECTORY}",
                       0, planned_collection_attempts())

# ==========================================================
# CONNECT TO SCOPE
# ==========================================================

if not SCOPE_ADDRESS:
    raise RuntimeError("Oscilloscope IP address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_OSCILLOSCOPE_ADDRESS.")

progress_window.update(3, "Connecting to oscilloscope", SCOPE_ADDRESS,
                       0, planned_collection_attempts())
print("Connecting to scope...")
rm_scope = pyvisa.ResourceManager()
scope = rm_scope.open_resource(scope_visa_resource(SCOPE_ADDRESS))
scope.timeout = 30000
scope.read_termination = "\n"
scope.write_termination = "\n"
scope.chunk_size = WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES
print(scope.query("*IDN?"))

# ==========================================================
# CONFIGURE SCOPE
# ==========================================================

def configure_scope(edge):
    # Add input impedance to 50 Ohm command

    scope.write("RECALL:SETUP DEFAULT")
    sleep(2)
    scope.write("MEASUrement:DELETEALL")
    sleep(1)
    TRIGGER_SLOPE = edge
    print("Configuring scope...")
    scope.write("*CLS")
    # Stop acquisition
    scope.write("ACQ:STATE OFF")
    # Turn channels on
    scope.write(f"SEL:{CH_2} ON")
    scope.write(f"SEL:{CH_1} ON")
    # Set Ch1 to 50 Ohm termination since it will be getting RF
    scope.write(f"{CH_1}:TERMINATION 50")
    # Vertical scales
    scope.write(f"{CH_1}:SCA {VERT_SCALE_TRIG}")
    scope.write(f"{CH_2}:SCA {VERT_SCALE_RF}")
    # Coupling
    scope.write(f"{TRIGGER_CHANNEL}:COUP DC")
    scope.write(f"{CH_1}:COUP DC")
    # Horizontal
    scope.write(f"HOR:SCALE {TIME_PER_DIV}")
    # Record length

    # Create Measurement 1
    scope.write("MEASU:ADDMEAS MAXIMUM")
    # Use CH1 as the source
    scope.write("MEASU:MEAS1:SOUR CH1")

    scope.write("HORIZONTAL:RECORDLENGTH 1000000")

    scope.write("DATA:START 1")
    scope.write("DATA:STOP 1000000")

    try:
        scope.write(f"HOR:RECO {RECORD_LENGTH}")
    except:
        pass
    # Trigger
    if edge == "FALL":
        scope.write(f"TRIG:A:EDGE:SOU CH2")
        scope.write(f"TRIG:A:LEV:CH2 {TRIGGER_LEVEL_FALL}")


    if edge == "RISE":
        scope.write("TRIG:A:EDGE:SOU CH2")
        scope.write(f"TRIG:A:LEV:CH2 {TRIGGER_LEVEL}")


    #scope.write(f"TRIG:A:EDGE:SOU {TRIGGER_CHANNEL}")
    # scope.write(
    #     f"TRIG:A:LEV:{TRIGGER_CHANNEL} {TRIGGER_LEVEL}"
    # )
    scope.write(
        f"TRIG:A:EDGE:SLO {TRIGGER_SLOPE}"
    )
    # Single sequence
    scope.write("ACQ:STOPA SEQ")
    # Data source
    scope.write(f"DATA:SOURCE {CH_1}")
    scope.write("DATA:ENCDG RIBINARY")
    scope.write("DATA:WIDTH 2")
    scope.write("DATA:START 1")
    print("Scope configured.")

    print("CH1 Enabled:", scope.query("SEL:CH1?"))
    print("CH2 Enabled:", scope.query("SEL:CH2?"))

# ==========================================================
# CONFIGURE DUT SYNC OUT
# ==========================================================
def configure_sync_out_track_rf():
    """Enable Pulse Sync Output and configure it to track RF.

    AECommandsUniversal.setPulsingConfiguration() blocks these settings when
    pulsing is disabled, even though this DUT supports Sync Out in CW mode.
    Send only CMD 26 subcommands 2 and 10 directly, then verify both readbacks.
    """
    print("Configuring DUT Pulse Sync Output = On...")
    response = gen.gen.create_buffer_cmd(26, '1:2,1:2,1:2', '1:1', 2, 1, 0)
    AECommandsUniversal.csrChecker(response)

    print("Configuring DUT Pulse Sync Output Mode = Track RF...")
    # CMD 26, subcommand 10: 0 = Track RF, 1 = alternate mode.
    response = gen.gen.create_buffer_cmd(26, '1:2,1:2,1:2', '1:1', 10, 0, 0)
    AECommandsUniversal.csrChecker(response)

    sync_output_state = gen.reportPulsingConfiguration(2)
    sync_output_mode = gen.reportPulsingConfiguration(10)
    if sync_output_state != 1:
        raise RuntimeError(
            f"Pulse Sync Output verification failed; expected 1 (On), "
            f"received {sync_output_state}."
        )
    if sync_output_mode != 0:
        raise RuntimeError(
            f"Pulse Sync Output Mode verification failed; expected 0 "
            f"(Track RF), received {sync_output_mode}."
        )
    print("DUT Sync Out verified: On, Track RF.")

# ==========================================================
# SET DUT FIXED FREQUENCY
# ==========================================================
def set_fixed_frequency_khz(freq_khz):
    """Set CMD 61 using the kHz value entered in the GUI.

    AECommandsUniversal.setFixedFrequency() divides the CMD 161 min/max
    readbacks by 1000 before validating a kHz input. On this DUT, those
    readbacks are already returned in kHz, causing valid values such as
    12882 kHz to be rejected as "Invalid frequency". Validate against the
    DUT readbacks in their reported units and then send CMD 61 directly.
    """
    freq_khz = int(freq_khz)
    min_reported = int(gen.reportFixedFrequency(3))
    max_reported = int(gen.reportFixedFrequency(2))

    # Support either documented Hz readbacks or DUTs that return kHz.
    if min_reported > 100000 or max_reported > 100000:
        min_khz = min_reported // 1000
        max_khz = max_reported // 1000
    else:
        min_khz = min_reported
        max_khz = max_reported

    if not min_khz <= freq_khz <= max_khz:
        raise ValueError(
            f"Requested frequency {freq_khz} kHz is outside the DUT range "
            f"{min_khz} to {max_khz} kHz."
        )

    response = gen.gen.create_buffer_cmd(61, '1:4', '1:1', freq_khz)
    AECommandsUniversal.csrChecker(response)
    print(f"DUT fixed frequency set to {freq_khz} kHz.")

# ==========================================================
# CONFIGURE GEN
# ==========================================================
def configure_gen():
    global gen
    if not GEN_IP:
        raise RuntimeError("DUT generator address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_GENERATOR_ADDRESS.")
    gen = AECommandsUniversal.generator(GEN_IP)
    gen.setActiveControlMode(2)
    gen.rfOFF()
    configure_sync_out_track_rf()

# ==========================================================
# SCOPE TRIGGER
# ==========================================================
def expected_coupler_vpk(power_w):
    coupled_power = (power_w*HALO_MODE) / (
        10 ** (COUPLER_DB / 10)
    )
    vrms = np.sqrt(
        coupled_power * SYSTEM_IMPEDANCE
    )
    vpk = vrms * np.sqrt(2)
    trigger = vpk
    return trigger

# ==========================================================
# SCOPE ARMING
# ==========================================================

def arm_scope(edge, trigger, fall_horizontal_position_percent=None):
    # Clear status/errors
    scope.write("*CLS")

    # Single sequence mode
    scope.write("ACQ:STOPA SEQ")

    if edge == "FALL":
        # FALL uses the DUT Sync Out falling edge, monitored on CH2.
        scope.write("ACQ:STATE OFF")
        scope.write(f"HORIZONTAL:SCALE {FALL_TIME_PER_DIV}")
        scope.write("HORIZONTAL:DELAY:MODE OFF")
        if fall_horizontal_position_percent is None:
            fall_horizontal_position_percent = FALL_HORIZONTAL_POSITION_PERCENT
        fall_horizontal_position_percent = float(
            fall_horizontal_position_percent
        )
        if not 0.0 <= fall_horizontal_position_percent <= 100.0:
            raise ValueError(
                "FALL horizontal position percent must be from 0 to 100."
            )
        scope.write(
            f"HORIZONTAL:POSITION {fall_horizontal_position_percent}"
        )
        print(
            "FALL horizontal position = "
            f"{fall_horizontal_position_percent:.3f}%"
        )
        scope.write("CH2:SCA 1")
        scope.write("TRIG:A:EDGE:SOU CH2")
        scope.write(f"TRIG:A:LEV:CH2 {TRIGGER_LEVEL_FALL}")
        scope.write("TRIG:A:EDGE:SLO FALL")
        scope.write("TRIG:A:EDGE:COUP DC")

        # Start acquisition only after the FALL setup is complete.
        scope.write("ACQ:STATE ON")

    if edge == "RISE":
        # RISE uses the DUT Sync Out rising edge on CH2 while CH1 records
        # the RF envelope.
        scope.write("ACQ:STATE OFF")
        scope.write(f"HORIZONTAL:SCALE {TIME_PER_DIV}")
        scope.write("CH2:SCA 1")
        scope.write("TRIG:A:EDGE:SOU CH2")
        scope.write(f"TRIG:A:LEV:CH2 {TRIGGER_LEVEL}")
        scope.write("TRIG:A:EDGE:SLO RISE")
        scope.write("TRIG:A:EDGE:COUP DC")
        scope.write("ACQ:STATE ON")

    print(scope.query("HORIZONTAL:RECORDLENGTH?"))
    print(scope.query("DATA:STOP?"))
    print(scope.query("WFMOUTPRE?"))

# ==========================================================
# SCOPE DISARMING
# ==========================================================

def disarm_scope(edge, trigger):
    if edge == "FALL":
        scope.write("ACQUIRE:STATE STOP")
        scope.write(f"TRIG:A:EDGE:SOU CH2")
        scope.write(f"TRIG:A:LEV:CH2 {trigger}")
    if edge == "RISE":
        scope.write("ACQUIRE:STATE STOP")
        scope.write("TRIG:A:EDGE:SOU CH2")
        scope.write(f"TRIG:A:LEV:CH2 {TRIGGER_LEVEL}")


# ==========================================================
# SAVE WAVEFORM
# ==========================================================

def save_wave(filename):
    """Download the completed CH1 waveform over Ethernet and save it locally."""
    if SAVE_DIRECTORY is None:
        raise RuntimeError("The waveform save location has not been selected.")

    output_path = Path(filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_suffix(output_path.suffix + ".partial")
    print(f"Transferring waveform over Ethernet to {output_path}")
    original_timeout = scope.timeout

    try:
        scope.timeout = WAVEFORM_TRANSFER_TIMEOUT_MS
        scope.write("ACQUIRE:STATE STOP")
        scope.write("*WAI")
        scope.write("DATA:SOURCE CH1")
        scope.write("DATA:ENCDG RIBINARY")
        scope.write("DATA:WIDTH 2")

        actual_record_length = int(float(scope.query("HORIZONTAL:RECORDLENGTH?")))
        scope.write("DATA:START 1")
        scope.write(f"DATA:STOP {actual_record_length}")

        xincr = float(scope.query("WFMOUTPRE:XINCR?"))
        xzero = float(scope.query("WFMOUTPRE:XZERO?"))
        pt_off = float(scope.query("WFMOUTPRE:PT_OFF?"))
        ymult = float(scope.query("WFMOUTPRE:YMULT?"))
        yzero = float(scope.query("WFMOUTPRE:YZERO?"))
        yoff = float(scope.query("WFMOUTPRE:YOFF?"))
        xunit = scope.query("WFMOUTPRE:XUNIT?").strip().strip('"') or "s"
        yunit = scope.query("WFMOUTPRE:YUNIT?").strip().strip('"') or "V"

        raw = np.asarray(
            scope.query_binary_values(
                "CURVE?",
                datatype="h",
                is_big_endian=True,
                container=np.array,
                expect_termination=False,
            ),
            dtype=np.float64,
        )
        if raw.size == 0:
            raise RuntimeError("No waveform points returned for CH1.")

        index = np.arange(raw.size, dtype=np.float64)
        times = xzero + (index - pt_off) * xincr
        values = (raw - yoff) * ymult + yzero

        np.savetxt(
            partial_path,
            np.column_stack((times, values)),
            delimiter=",",
            header=f"Time ({xunit}),CH1 ({yunit})",
            comments="",
            fmt="%.12e",
        )
        partial_path.replace(output_path)
        print(
            f"Waveform transfer complete: {raw.size:,} points saved locally "
            f"to {output_path}"
        )
    except Exception:
        if partial_path.exists():
            partial_path.unlink()
        raise
    finally:
        scope.timeout = original_timeout

# ==========================================================
# SMART TRIGGER
# ==========================================================

def smart_trigger(
    freq,
    edge,
    setpoint,
    timeout_sec=2,
    fall_horizontal_position_percent=None,
):
    if edge == "RISE":
        # Host-control RISE sequence:
        #   1. Keep RF off while frequency, regulation, and power are set.
        #   2. Arm the scope on the DUT Sync Out rising edge on CH2.
        #   3. Command RF on. Sync Out tracks RF and creates the trigger edge.
        commanded_power_w = float(setpoint) * HALO_MODE
        commanded_setpoint_tenths_w = int(round(commanded_power_w * 10.0))

        gen.rfOFF()
        gen.setActiveControlMode(2)
        set_fixed_frequency_khz(freq)
        gen.setRegulationMode(6)
        gen.setFrequencyMode(0)
        gen.setPulsingFrequency(0)
        gen.setPowerSetpoint(commanded_setpoint_tenths_w)
        sleep(FALL_SETPOINT_SETTLE_SEC)

        print(
            f"RISE host-control setpoint = {commanded_power_w:g} W "
            f"({commanded_setpoint_tenths_w} tenths of a watt)"
        )
        arm_scope(edge, TRIGGER_LEVEL)
        sleep(FALL_SCOPE_ARM_SETTLE_SEC)

        print("Commanding RF ON; DUT Sync Out rising edge should trigger CH2.")
        gen.rfON()
        start_time = time()
        while time() - start_time < timeout_sec:
            state = scope.query("TRIG:STATE?").strip()
            if state in ["SAVE", "STOP"]:
                print(
                    f"Triggered from DUT Sync Out rising edge at "
                    f"{TRIGGER_LEVEL:.4f} V"
                )
                print(f"Power setpoint = {commanded_power_w:g} W")
                return True
            sleep(0.05)

        print("No trigger from DUT Sync Out rising edge")
        gen.rfOFF()
        return False
    if edge == "FALL":
        # Host-control FALL sequence:
        #   1. Keep the DUT in host mode and command the requested setpoint.
        #   2. Turn RF on and allow the output to settle.
        #   3. Arm the scope on the DUT Sync Out falling edge on CH2.
        #   4. Command RF off. Sync Out tracks RF and creates the trigger edge.
        commanded_power_w = float(setpoint) * HALO_MODE
        commanded_setpoint_tenths_w = int(round(commanded_power_w * 10.0))

        gen.rfOFF()
        gen.setActiveControlMode(2)
        set_fixed_frequency_khz(freq)
        gen.setRegulationMode(6)
        gen.setFrequencyMode(0)
        gen.setPulsingFrequency(0)
        gen.setPowerSetpoint(commanded_setpoint_tenths_w)
        sleep(FALL_SETPOINT_SETTLE_SEC)

        print(
            f"FALL host-control setpoint = {commanded_power_w:g} W "
            f"({commanded_setpoint_tenths_w} tenths of a watt)"
        )
        gen.rfON()
        sleep(FALL_RF_ON_SETTLE_SEC)

        arm_scope(
            edge,
            TRIGGER_LEVEL_FALL,
            fall_horizontal_position_percent=fall_horizontal_position_percent,
        )
        sleep(FALL_SCOPE_ARM_SETTLE_SEC)

        print("Commanding RF OFF; DUT Sync Out falling edge should trigger CH2.")
        gen.rfOFF()
        start_time = time()
        while time() - start_time < timeout_sec:
            state = scope.query("TRIG:STATE?").strip()
            if state in ["SAVE", "STOP"]:
                print(
                    f"Triggered from DUT Sync Out falling edge at "
                    f"{TRIGGER_LEVEL_FALL:.4f} V"
                )
                print(f"Power setpoint = {commanded_power_w:g} W")
                return True
            sleep(0.05)

        print("No trigger from DUT Sync Out falling edge")
        gen.rfOFF()
        return False

# ==========================================================
# SCOPE CAPTURE PREP
# ==========================================================
def scope_capture_prep(freq, edge, setpoint):
    # Host-control sequencing for both RISE and FALL is centralized in
    # smart_trigger so the scope is always armed before the commanded RF edge.
    return

# ==========================================================
# MAIN TEST LOOP
# ==========================================================
results = []

print()
print(f"Running {NUM_ACQUISITIONS} acquisitions")
print(scope.query("*IDN?"))
print()

def captureRiseEdges():
    progress_window.update(8, "Connecting to DUT", GEN_IP, 0, planned_collection_attempts())
    configure_gen()
    edge = "RISE"
    configure_scope(edge)
    attempt_number = 0
    for freq in freqsMHz:
        for cycle in range(NUM_ACQUISITIONS):
            for power_setpoint in power_setpoints:
                attempt_number += 1
                update_collection_attempt(attempt_number, freq, cycle + 1, power_setpoint)
                try:
                    print(f"\nCycle {cycle+1}/{NUM_ACQUISITIONS}")
                    scope_capture_prep(freq, edge, power_setpoint)
                    triggered = smart_trigger(freq, edge, power_setpoint)
                    if not triggered:
                        report_collection_warning(
                            f"No RISE trigger at {freq} kHz, cycle {cycle+1}, "
                            f"{power_setpoint * HALO_MODE:g} W. Skipping this sample."
                        )
                        continue
                    # save channel 1 waveform csv to thumbdrive with title based off of frequency and setpoint

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = SAVE_DIRECTORY / (
                        f'RF_Freq_RISE{freq}_Power{power_setpoint*HALO_MODE}W_'
                        f'{timestamp}.csv'
                    )
                    save_wave(filename)

                    # Turn RF OFF after capture
                    gen.rfOFF()
                    # Cool-down delay
                    sleep(0.100)
                except Exception as e:
                    report_collection_warning(
                        f"RISE sample failed at {freq} kHz, cycle {cycle+1}, "
                        f"{power_setpoint * HALO_MODE:g} W: {e}"
                    )
                    results.append({
                        "Cycle": cycle + 1,
                        "RiseTime_us": np.nan,
                        "FinalAmplitude": np.nan
                    })
                    try:
                        gen.rfOFF()
                    except:
                        pass

def captureFallEdges():
    progress_window.update(8, "Connecting to DUT", GEN_IP, 0, planned_collection_attempts())
    configure_gen()
    edge = "FALL"
    configure_scope(edge)
    attempt_number = 0

    for freq in freqsMHz:
        gen.rfOFF()
        gen.setActiveControlMode(2)
        sleep(FALL_MODE_2_SETTLE_SEC)
        set_fixed_frequency_khz(freq)
        gen.setRegulationMode(6)
        gen.setFrequencyMode(0)
        gen.setPulsingFrequency(0)

        for cycle in range(NUM_ACQUISITIONS):
            sleep(0.5)
            for power_setpoint in power_setpoints:
                attempt_number += 1
                update_collection_attempt(attempt_number, freq, cycle + 1, power_setpoint)
                if FALL_SHIFT_HORIZONTAL_POSITION_BY_SETPOINT:
                    fall_horizontal_position_percent = (
                        calculate_fall_horizontal_position_percent(power_setpoint)
                    )
                    position_mode = "setpoint-adjusted"
                else:
                    fall_horizontal_position_percent = float(
                        FALL_HORIZONTAL_POSITION_PERCENT
                    )
                    position_mode = "fixed"
                print(
                    f"FALL setpoint {power_setpoint * HALO_MODE:g} W uses "
                    f"{position_mode} horizontal position "
                    f"{fall_horizontal_position_percent:.3f}%"
                )
                try:
                    triggered = smart_trigger(
                        freq,
                        edge,
                        power_setpoint,
                        fall_horizontal_position_percent=(
                            fall_horizontal_position_percent
                        ),
                    )
                    if not triggered:
                        report_collection_warning(
                            f"No FALL trigger at {freq} kHz, cycle {cycle+1}, "
                            f"{power_setpoint * HALO_MODE:g} W. Skipping this sample."
                        )
                        continue

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    filename = SAVE_DIRECTORY / (
                        f"RF_Freq_FALL{freq}_Power"
                        f"{power_setpoint * HALO_MODE}W_{timestamp}.csv"
                    )
                    save_wave(filename)
                    print(f"Power Setpoint = {power_setpoint * HALO_MODE:g} W")
                    disarm_scope("FALL", TRIGGER_LEVEL_FALL)
                except Exception as exc:
                    report_collection_warning(
                        f"FALL sample failed at {freq} kHz, cycle {cycle+1}, "
                        f"{power_setpoint * HALO_MODE:g} W: {exc}"
                    )
                finally:
                    try:
                        gen.rfOFF()
                    except Exception as cleanup_exc:
                        report_collection_warning(
                            f"Could not command DUT RF OFF after FALL attempt: {cleanup_exc}"
                        )

def main():
    if SELECTED_CAPTURE_MODE == "RISE":
        capture_mode = "Rising Edge"
        captureRiseEdges()
    elif SELECTED_CAPTURE_MODE == "FALL":
        capture_mode = "Falling Edge"
        captureFallEdges()
    else:
        raise ValueError(f"Unsupported capture mode: {SELECTED_CAPTURE_MODE}")

    progress_window.update(100, "Collection complete",
                           f"{capture_mode} collection finished. Files: {SAVE_DIRECTORY}",
                           planned_collection_attempts(), planned_collection_attempts())
    print(f"\nSelected mode: {capture_mode}")


def safely_stop_hardware():
    """Best-effort shutdown used only after completion or an unexpected run-level error."""
    try:
        if "gen" in globals(): gen.rfOFF()
    except Exception as exc:
        print(f"Cleanup warning while disabling DUT RF: {exc}")


if __name__ == "__main__":
    run_error = None
    try:
        main()
    except Exception as exc:
        run_error = exc
        message = f"CW collection stopped because of an unrecoverable error: {exc}"
        print("\nERROR: " + message, flush=True)
        try:
            progress_window.warning("ERROR: " + message)
            messagebox.showerror("CW collection stopped", message, parent=progress_window.root)
        except Exception:
            pass
    finally:
        safely_stop_hardware()
        try:
            if run_error is None:
                messagebox.showinfo(
                    "CW collection complete",
                    f"Collection is complete.\n\nData folder:\n{SAVE_DIRECTORY}",
                    parent=progress_window.root
                )
            progress_window.close()
        except Exception:
            pass