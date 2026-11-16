"""
DLP waveform data collection for AE Paramount DLP-capable RF generators.

Baseline: PulsingDataCollection.py
DUT API: AECommandsDLPUnits.py

Configure ordered transitions in DLP_TRANSITIONS_W. For each (start, end) pair,
the DUT is configured with State 0 = start power and State 1 = end power.
The scope acquires on a reliable RF-carrier zero crossing and records enough
of the waveform to include the requested DLP state transition for offline analysis.
"""

import sys
import os
from pathlib import Path
from time import sleep, time
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

import numpy as np
import pyvisa

# ==========================================================
# LOAD AE-SPECIFIC DEPENDENCIES
# ==========================================================
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default
from suite_common.inputs import format_number_list, parse_number_list
from suite_common.output_folders import create_unique_test_output_folder

import AECommandsDLPUnits

# ==========================================================
# UNIFIED LAUNCHER COMPATIBILITY
# ==========================================================
LAUNCHED_BY_UNIFIED_GUI = os.environ.get("PARAMOUNT_UNIFIED_LAUNCHER", "").strip() == "1"
LAUNCHER_RISE_FALL_MODE = os.environ.get(
    "PARAMOUNT_RISE_FALL_MODE",
    os.environ.get("PARAMOUNT_RISE_FALL_TEST_TYPE", ""),
).strip().upper()
LAUNCHER_RISE_FALL_EDGE = os.environ.get(
    "PARAMOUNT_RISE_FALL_EDGE",
    os.environ.get("PARAMOUNT_RISE_FALL_TRANSITION", ""),
).strip().upper()

def _launcher_default(environment_name, standalone_default):
    """Use a nonblank unified-launcher value as an editable GUI default."""
    value = os.environ.get(environment_name, "").strip()
    return value if value else standalone_default

def validate_unified_launcher_request():
    """Reject a wrong mode or unusable edge before any instrument connection."""
    if not LAUNCHED_BY_UNIFIED_GUI:
        return
    if LAUNCHER_RISE_FALL_MODE and LAUNCHER_RISE_FALL_MODE != "DLP":
        raise SystemExit(
            "This is the DLP collection script, but the unified launcher requested "
            f"{LAUNCHER_RISE_FALL_MODE!r}. Link the DLP sequence entry to the DLP script."
        )
    if LAUNCHER_RISE_FALL_EDGE not in ("RISE", "FALL"):
        raise SystemExit(
            "The unified launcher must provide PARAMOUNT_RISE_FALL_EDGE as "
            "RISE or FALL for the DLP collection script."
        )

# ==========================================================
# USER SETTINGS
# ==========================================================
SCOPE_ADDRESS = bench_default("oscilloscope")
GEN_IP = bench_default("generator")

NUM_ACQUISITIONS = 25
RF_CHANNEL = "CH1"

# Adaptive scope vertical scaling. The expected RF coupler peak voltage is
# calculated from the larger power state, then rounded up to a normal scope
# scale so the waveform remains on screen with headroom.
ADAPTIVE_VERTICAL_SCALE = True
FIXED_VERTICAL_SCALE_V_PER_DIV = 0.02
VERTICAL_TARGET_PEAK_DIVISIONS = 4.5
VERTICAL_HEADROOM_FACTOR = 1.10
MIN_VERTICAL_SCALE_V_PER_DIV = 0.01
MAX_VERTICAL_SCALE_V_PER_DIV = 1.0
VERTICAL_SCALE_MANTISSAS = (1.0, 2.0, 2.5, 5.0)

RECORD_LENGTH = 100_000
HORIZONTAL_DIVISIONS = 10
PULSE_PERIODS_ON_SCREEN = 2
TRIGGER_POSITION_PERCENT = 20.0

TRIGGER_TIMEOUT_SEC = 5.0
PULSE_SETTLE_TIME_SEC = 1.0
ACQUISITION_DELAY_SEC = 0.3
CONDITION_DELAY_SEC = 0.5
DUT_COMMAND_DELAY_SEC = 0.10

# RF frequencies are in kHz.
RF_FREQUENCIES_KHZ = [12882, 13560, 14238]

# Ordered transitions in actual watts: (starting power, ending power).
# State 0 is assigned the starting power and State 1 the ending power.

# Sanjay requests: (500, 1000), (1000, 2000), (2000, 3000)
# DLP_TRANSITIONS_W = [
#     (50, 100),
#     (100, 200),
#     (200, 400),
#     (400, 800),
#     (800, 1600),
#     (1600, 3000),
#     (100, 50),
#     (200, 100),
#     (400, 200),
#     (800, 400),
#     (1600, 800),
#     (3000, 1600),
# ]

DLP_TRANSITIONS_W = [
    (50, 100),
    (100, 200),
    (200, 400),
    (400, 800),
    (500, 1000),
    (800, 1600),
    (1000, 2000),
    (1600, 3000),
    (2000, 3000),
    (100, 50),
    (200, 100),
    (400, 200),
    (800, 400),
    (1000, 500),
    (2000, 1000),
    (1600, 800),
    (3000, 2000),
    (3000, 1600),
]
# AE Command 8 setpoints are normally expressed in tenths of a watt.
# Leave at 10 unless the target DUT's command definition specifies otherwise.
DLP_SETPOINT_UNITS_PER_WATT = 10

# DLP pulse settings. Duty cycle is the State 1 portion of the period.

# Sanjay requests: PULSE_FREQUENCIES_HZ = [1000]
PULSE_FREQUENCIES_HZ = [4000]
PULSE_DUTY_CYCLES_PERCENT = [50]

# Both DLP states use forward-power regulation and fixed frequency.
REGULATION_MODE = 6
DLP_FREQUENCY_MODE_STATE_1 = 0
DLP_FREQUENCY_MODE_STATE_0 = 0

COUPLER_DB = 60
SYSTEM_IMPEDANCE_OHMS = 50
# Waveforms are transferred from the scope over Ethernet and saved on this
# computer. The user selects the actual destination folder each time the script runs.
DEFAULT_LOCAL_WAVEFORM_DIRECTORY = Path.home()
DEFAULT_OUTPUT_PARENT_FOLDER = Path(_launcher_default("PARAMOUNT_COLLECTION_PARENT_FOLDER", str(DEFAULT_LOCAL_WAVEFORM_DIRECTORY)))
LOCAL_WAVEFORM_DIRECTORY = None
TEST_NAME = "DLP Statistical Timing Analysis"
WAVEFORM_TRANSFER_TIMEOUT_MS = 120_000
WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES = 25 * 1024 * 1024

# ==========================================================
# GLOBAL CONNECTION OBJECTS
# ==========================================================
scope_resource_manager = None
scope = None
gen = None

# ==========================================================
# LOCAL OUTPUT FOLDER SELECTION
# ==========================================================
def select_local_waveform_directory():
    """Ask for a parent folder and create the timestamped DLP test folder."""
    global LOCAL_WAVEFORM_DIRECTORY
    initial_directory = Path(DEFAULT_OUTPUT_PARENT_FOLDER)
    if not initial_directory.is_dir():
        initial_directory = Path.home()
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    try:
        selected_directory = filedialog.askdirectory(
            title="Select parent folder for DLP waveform CSV files",
            initialdir=str(initial_directory),
            mustexist=True,
            parent=root,
        )
    finally:
        root.destroy()
    if not selected_directory:
        raise SystemExit("No waveform save folder was selected. Test cancelled.")
    selected_parent_directory = Path(selected_directory).resolve()
    LOCAL_WAVEFORM_DIRECTORY = create_unique_test_output_folder(
        selected_parent_directory,
        TEST_NAME,
        GEN_IP,
    )
    print(f"\nSelected parent folder: {selected_parent_directory}")
    print(f"Test data subfolder: {LOCAL_WAVEFORM_DIRECTORY}")


# ==========================================================
# GRAPHICAL TEST CONFIGURATION
# ==========================================================
def _format_transition_list(transitions):
    return "\n".join(f"{start:g} -> {end:g}" for start, end in transitions)


def _parse_transition_list(text):
    transitions = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        normalized = (
            line.replace("-->", ",")
                .replace("->", ",")
                .replace("→", ",")
                .replace(";", ",")
        )
        pieces = [piece.strip() for piece in normalized.split(",") if piece.strip()]
        if len(pieces) != 2:
            raise ValueError(
                f"Transition line {line_number} must look like 50 -> 100."
            )
        try:
            start_power = float(pieces[0])
            end_power = float(pieces[1])
        except ValueError as error:
            raise ValueError(
                f"Transition line {line_number} contains a non-numeric power."
            ) from error
        transitions.append((start_power, end_power))
    if not transitions:
        raise ValueError("At least one DLP transition is required.")
    return transitions


def configure_test_with_gui():
    """Display script defaults in a friendly editor and apply the selected run settings.

    All fields are initialized from the module-level variables above. Editing those
    variables directly therefore remains fully supported and changes the GUI defaults.
    Returns RISE, FALL, or None for the chosen transition filter.
    """
    global SCOPE_ADDRESS, GEN_IP, NUM_ACQUISITIONS
    global RF_FREQUENCIES_KHZ, DLP_TRANSITIONS_W
    global PULSE_FREQUENCIES_HZ, PULSE_DUTY_CYCLES_PERCENT
    global ADAPTIVE_VERTICAL_SCALE, FIXED_VERTICAL_SCALE_V_PER_DIV
    global VERTICAL_TARGET_PEAK_DIVISIONS, VERTICAL_HEADROOM_FACTOR
    global RECORD_LENGTH, PULSE_PERIODS_ON_SCREEN, TRIGGER_POSITION_PERCENT
    global TRIGGER_TIMEOUT_SEC, PULSE_SETTLE_TIME_SEC
    global ACQUISITION_DELAY_SEC, CONDITION_DELAY_SEC, DUT_COMMAND_DELAY_SEC
    global COUPLER_DB, SYSTEM_IMPEDANCE_OHMS

    root = tk.Tk()
    window_edge = (
        f" - {LAUNCHER_RISE_FALL_EDGE}"
        if LAUNCHED_BY_UNIFIED_GUI and LAUNCHER_RISE_FALL_EDGE
        else ""
    )
    root.title(f"DLP Data Collection Setup{window_edge}")
    root.geometry("860x760")
    root.minsize(760, 650)

    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass
    style.configure("Header.TLabel", font=("Segoe UI", 15, "bold"))
    style.configure("Subheader.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Start.TButton", font=("Segoe UI", 11, "bold"), padding=(14, 8))

    outer = ttk.Frame(root, padding=14)
    outer.pack(fill="both", expand=True)
    ttk.Label(
        outer,
        text=f"Dual Level Pulse Data Collection{window_edge}",
        style="Header.TLabel",
    ).pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Review or edit the test matrix below. Launcher-provided equipment "
            "addresses are editable defaults. Direct script settings remain the "
            "standalone defaults."
        ),
        wraplength=810,
    ).pack(anchor="w", pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    test_tab = ttk.Frame(notebook, padding=12)
    scope_tab = ttk.Frame(notebook, padding=12)
    advanced_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(test_tab, text="Test Matrix")
    notebook.add(scope_tab, text="Scope & Instruments")
    notebook.add(advanced_tab, text="Advanced Timing")
    notebook.add(output_tab, text="Output")

    variables = {}

    def add_entry(parent, row, label, key, default, width=34, help_text=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        variable = tk.StringVar(value=str(default))
        variables[key] = variable
        entry = ttk.Entry(parent, textvariable=variable, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        if help_text:
            ttk.Label(parent, text=help_text, foreground="#555555", wraplength=360).grid(
                row=row, column=2, sticky="w", padx=(10, 0), pady=4
            )
        return entry

    output_parent = tk.StringVar(value=str(DEFAULT_OUTPUT_PARENT_FOLDER))
    ttk.Label(output_tab, text="Parent output folder", style="Subheader.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
    ttk.Entry(output_tab, textvariable=output_parent, width=68).grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
    def browse_output_parent():
        current = output_parent.get().strip()
        initial = Path(current) if current and Path(current).is_dir() else Path.home()
        selected = filedialog.askdirectory(title="Select parent folder for DLP waveform CSV files", initialdir=str(initial), mustexist=True, parent=root)
        if selected:
            output_parent.set(selected)
    ttk.Button(output_tab, text="Browse...", command=browse_output_parent).grid(row=1, column=2, sticky="w", padx=(10, 0), pady=4)
    ttk.Label(output_tab, text="The timestamped DLP test folder is created inside this parent folder only after Start Test is clicked and all settings validate successfully.", foreground="#555555", wraplength=700).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
    output_tab.columnconfigure(0, weight=1)

    # Test Matrix tab.
    test_tab.columnconfigure(1, weight=1)
    ttk.Label(test_tab, text="Run selection", style="Subheader.TLabel").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 6)
    )
    run_mode = tk.StringVar(
        value=LAUNCHER_RISE_FALL_EDGE if LAUNCHED_BY_UNIFIED_GUI else "ALL"
    )
    run_frame = ttk.Frame(test_tab)
    run_frame.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
    if LAUNCHED_BY_UNIFIED_GUI:
        selected_edge_text = (
            "Rising transitions only"
            if LAUNCHER_RISE_FALL_EDGE == "RISE"
            else "Falling transitions only"
        )
        ttk.Label(
            run_frame,
            text=f"{selected_edge_text} (selected by unified sequence)",
            foreground="#1F4E78",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        ttk.Label(
            run_frame,
            text=(
                "Finish or cancel this edge, then close this GUI. The unified launcher "
                "will show a separate setup page before the next edge."
            ),
            foreground="#555555",
            wraplength=500,
        ).pack(side="left", padx=(18, 0))
    else:
        for text, value in (
            ("All configured transitions", "ALL"),
            ("Rising only", "RISE"),
            ("Falling only", "FALL"),
        ):
            ttk.Radiobutton(run_frame, text=text, value=value, variable=run_mode).pack(
                side="left", padx=(0, 18)
            )

    add_entry(test_tab, 2, "Acquisitions per condition", "num_acquisitions", NUM_ACQUISITIONS,
              help_text="Number of CSV waveforms saved for each complete condition.")
    add_entry(test_tab, 3, "RF frequencies (kHz)", "rf_frequencies", format_number_list(RF_FREQUENCIES_KHZ),
              help_text="Comma-separated, for example: 12882, 13560, 14238")
    add_entry(test_tab, 4, "Pulse frequencies (Hz)", "pulse_frequencies", format_number_list(PULSE_FREQUENCIES_HZ),
              help_text="Comma-separated list. Every frequency is paired with every duty cycle.")
    add_entry(test_tab, 5, "Duty cycles (%)", "duty_cycles", format_number_list(PULSE_DUTY_CYCLES_PERCENT),
              help_text="Comma-separated whole percentages from 1 through 99.")

    ttk.Label(test_tab, text="Ordered DLP transitions (W)").grid(
        row=6, column=0, sticky="nw", padx=(0, 10), pady=4
    )
    transition_text = tk.Text(test_tab, width=32, height=9, font=("Consolas", 10))
    transition_text.insert("1.0", _format_transition_list(DLP_TRANSITIONS_W))
    transition_text.grid(row=6, column=1, sticky="nsew", pady=4)
    ttk.Label(
        test_tab,
        text="One transition per line. Examples:\n50 -> 100\n100 -> 50\n500 -> 2000",
        foreground="#555555",
    ).grid(row=6, column=2, sticky="nw", padx=(10, 0), pady=4)
    test_tab.rowconfigure(6, weight=1)

    preview_variable = tk.StringVar()
    ttk.Label(test_tab, textvariable=preview_variable, style="Subheader.TLabel").grid(
        row=7, column=0, columnspan=3, sticky="w", pady=(12, 0)
    )

    # Scope and instruments tab.
    scope_tab.columnconfigure(1, weight=1)
    add_entry(scope_tab, 0, "Scope VISA address", "scope_address", SCOPE_ADDRESS, width=45)
    add_entry(scope_tab, 1, "DUT IP / hostname", "gen_ip", GEN_IP, width=45)
    ttk.Label(
        scope_tab,
        text=(
            "After you click Start Test, select a parent folder. The script will "
            "create a timestamped 'DLP Statistical Timing Analysis - Generator Name' "
            "subfolder inside it."
        ),
        foreground="#555555",
        wraplength=650,
    ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 10))
    add_entry(scope_tab, 3, "Record length", "record_length", RECORD_LENGTH)
    add_entry(scope_tab, 4, "Pulse periods on screen", "pulse_periods", PULSE_PERIODS_ON_SCREEN)
    add_entry(scope_tab, 5, "Trigger position (%)", "trigger_position", TRIGGER_POSITION_PERCENT)
    add_entry(scope_tab, 6, "Coupler attenuation (dB)", "coupler_db", COUPLER_DB)
    add_entry(scope_tab, 7, "System impedance (ohms)", "impedance", SYSTEM_IMPEDANCE_OHMS)

    adaptive_variable = tk.BooleanVar(value=ADAPTIVE_VERTICAL_SCALE)
    ttk.Checkbutton(
        scope_tab,
        text="Use adaptive vertical scaling",
        variable=adaptive_variable,
    ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(10, 4))
    add_entry(scope_tab, 9, "Fixed vertical scale (V/div)", "fixed_vertical", FIXED_VERTICAL_SCALE_V_PER_DIV)
    add_entry(scope_tab, 10, "Target peak divisions", "target_divisions", VERTICAL_TARGET_PEAK_DIVISIONS)
    add_entry(scope_tab, 11, "Vertical headroom factor", "headroom", VERTICAL_HEADROOM_FACTOR)

    # Advanced timing tab.
    advanced_tab.columnconfigure(1, weight=1)
    add_entry(advanced_tab, 0, "Trigger timeout (s)", "trigger_timeout", TRIGGER_TIMEOUT_SEC)
    add_entry(advanced_tab, 1, "Pulse settle time (s)", "pulse_settle", PULSE_SETTLE_TIME_SEC)
    add_entry(advanced_tab, 2, "Acquisition delay (s)", "acquisition_delay", ACQUISITION_DELAY_SEC)
    add_entry(advanced_tab, 3, "Condition delay (s)", "condition_delay", CONDITION_DELAY_SEC)
    add_entry(advanced_tab, 4, "DUT command delay (s)", "dut_delay", DUT_COMMAND_DELAY_SEC)
    ttk.Label(
        advanced_tab,
        text=(
            "These fields are initialized from the script variables. Normally only the "
            "Test Matrix tab needs changing."
        ),
        foreground="#555555",
        wraplength=650,
    ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(14, 0))

    result = {"accepted": False, "edge_filter": None}

    def parse_float(key, label, minimum=None, strictly_positive=False):
        try:
            value = float(variables[key].get().strip())
        except ValueError as error:
            raise ValueError(f"{label} must be numeric.") from error
        if strictly_positive and value <= 0:
            raise ValueError(f"{label} must be greater than zero.")
        if minimum is not None and value < minimum:
            raise ValueError(f"{label} must be at least {minimum}.")
        return value

    def calculate_preview(*_):
        try:
            acquisitions = int(float(variables["num_acquisitions"].get().strip()))
            rf_count = len(parse_number_list(variables["rf_frequencies"].get(), "RF frequencies"))
            pulse_count = len(parse_number_list(variables["pulse_frequencies"].get(), "Pulse frequencies"))
            duty_count = len(parse_number_list(variables["duty_cycles"].get(), "Duty cycles"))
            transitions = _parse_transition_list(transition_text.get("1.0", "end"))
            mode = run_mode.get()
            if mode == "RISE":
                transitions = [pair for pair in transitions if pair[1] > pair[0]]
            elif mode == "FALL":
                transitions = [pair for pair in transitions if pair[1] < pair[0]]
            total = acquisitions * rf_count * pulse_count * duty_count * len(transitions)
            preview_variable.set(
                f"Planned waveform files: {total:,}  "
                f"({len(transitions)} selected transitions)"
            )
        except Exception:
            preview_variable.set("Planned waveform files: check entered values")

    def start_test():
        nonlocal result
        global SCOPE_ADDRESS, GEN_IP, NUM_ACQUISITIONS
        global RF_FREQUENCIES_KHZ, DLP_TRANSITIONS_W
        global PULSE_FREQUENCIES_HZ, PULSE_DUTY_CYCLES_PERCENT
        global ADAPTIVE_VERTICAL_SCALE, FIXED_VERTICAL_SCALE_V_PER_DIV
        global VERTICAL_TARGET_PEAK_DIVISIONS, VERTICAL_HEADROOM_FACTOR
        global RECORD_LENGTH, PULSE_PERIODS_ON_SCREEN, TRIGGER_POSITION_PERCENT
        global TRIGGER_TIMEOUT_SEC, PULSE_SETTLE_TIME_SEC
        global ACQUISITION_DELAY_SEC, CONDITION_DELAY_SEC, DUT_COMMAND_DELAY_SEC
        global COUPLER_DB, SYSTEM_IMPEDANCE_OHMS
        global LOCAL_WAVEFORM_DIRECTORY
        try:
            selected_parent_text = output_parent.get().strip()
            if not selected_parent_text:
                raise ValueError("Select a parent output folder on the Output tab.")
            selected_parent = Path(selected_parent_text).expanduser().resolve()
            if not selected_parent.exists() or not selected_parent.is_dir():
                raise ValueError("The selected parent output folder does not exist.")
            acquisitions_value = int(float(variables["num_acquisitions"].get().strip()))
            if acquisitions_value < 1:
                raise ValueError("Acquisitions per condition must be at least 1.")
            rf_values = parse_number_list(
                variables["rf_frequencies"].get(), "RF frequencies", integer_only=True
            )
            pulse_values = parse_number_list(
                variables["pulse_frequencies"].get(), "Pulse frequencies", integer_only=True
            )
            duty_values = parse_number_list(
                variables["duty_cycles"].get(), "Duty cycles", integer_only=True
            )
            transitions_value = _parse_transition_list(transition_text.get("1.0", "end"))

            for frequency in pulse_values:
                for duty in duty_values:
                    validate_pulse_settings(frequency, duty)

            mode = run_mode.get()
            if LAUNCHED_BY_UNIFIED_GUI and mode != LAUNCHER_RISE_FALL_EDGE:
                raise ValueError(
                    "The selected transition direction no longer matches the unified "
                    "sequence request. Close this GUI and relaunch the sequence entry."
                )
            selected_transitions = transitions_value
            if mode == "RISE":
                selected_transitions = [pair for pair in transitions_value if pair[1] > pair[0]]
            elif mode == "FALL":
                selected_transitions = [pair for pair in transitions_value if pair[1] < pair[0]]
            if not selected_transitions:
                raise ValueError(f"No {mode.lower()} transitions are configured.")

            settings = {
                "scope_address": variables["scope_address"].get().strip(),
                "gen_ip": variables["gen_ip"].get().strip(),
                "record_length": int(float(variables["record_length"].get().strip())),
                "pulse_periods": parse_float("pulse_periods", "Pulse periods on screen", strictly_positive=True),
                "trigger_position": parse_float("trigger_position", "Trigger position"),
                "coupler_db": parse_float("coupler_db", "Coupler attenuation"),
                "impedance": parse_float("impedance", "System impedance", strictly_positive=True),
                "fixed_vertical": parse_float("fixed_vertical", "Fixed vertical scale", strictly_positive=True),
                "target_divisions": parse_float("target_divisions", "Target peak divisions", strictly_positive=True),
                "headroom": parse_float("headroom", "Vertical headroom", strictly_positive=True),
                "trigger_timeout": parse_float("trigger_timeout", "Trigger timeout", strictly_positive=True),
                "pulse_settle": parse_float("pulse_settle", "Pulse settle time", minimum=0),
                "acquisition_delay": parse_float("acquisition_delay", "Acquisition delay", minimum=0),
                "condition_delay": parse_float("condition_delay", "Condition delay", minimum=0),
                "dut_delay": parse_float("dut_delay", "DUT command delay", minimum=0),
            }
            if not settings["scope_address"] or not settings["gen_ip"]:
                raise ValueError("Scope address and DUT hostname cannot be empty.")
            if settings["record_length"] < 1:
                raise ValueError("Record length must be at least 1.")
            if not 0 <= settings["trigger_position"] <= 100:
                raise ValueError("Trigger position must be from 0 through 100 percent.")

            SCOPE_ADDRESS = settings["scope_address"]
            GEN_IP = settings["gen_ip"]
            NUM_ACQUISITIONS = acquisitions_value
            RF_FREQUENCIES_KHZ = rf_values
            DLP_TRANSITIONS_W = transitions_value
            PULSE_FREQUENCIES_HZ = pulse_values
            PULSE_DUTY_CYCLES_PERCENT = duty_values
            ADAPTIVE_VERTICAL_SCALE = adaptive_variable.get()
            FIXED_VERTICAL_SCALE_V_PER_DIV = settings["fixed_vertical"]
            VERTICAL_TARGET_PEAK_DIVISIONS = settings["target_divisions"]
            VERTICAL_HEADROOM_FACTOR = settings["headroom"]
            RECORD_LENGTH = settings["record_length"]
            PULSE_PERIODS_ON_SCREEN = settings["pulse_periods"]
            TRIGGER_POSITION_PERCENT = settings["trigger_position"]
            TRIGGER_TIMEOUT_SEC = settings["trigger_timeout"]
            PULSE_SETTLE_TIME_SEC = settings["pulse_settle"]
            ACQUISITION_DELAY_SEC = settings["acquisition_delay"]
            CONDITION_DELAY_SEC = settings["condition_delay"]
            DUT_COMMAND_DELAY_SEC = settings["dut_delay"]
            COUPLER_DB = settings["coupler_db"]
            SYSTEM_IMPEDANCE_OHMS = settings["impedance"]

            LOCAL_WAVEFORM_DIRECTORY = create_unique_test_output_folder(selected_parent, TEST_NAME, GEN_IP)
            print(f"\nSelected parent folder: {selected_parent}")
            print(f"Test data subfolder: {LOCAL_WAVEFORM_DIRECTORY}")
            result["accepted"] = True
            result["edge_filter"] = None if mode == "ALL" else mode
            root.destroy()
        except Exception as error:
            messagebox.showerror("Invalid test configuration", str(error), parent=root)

    def cancel_test():
        root.destroy()

    for variable in variables.values():
        variable.trace_add("write", calculate_preview)
    run_mode.trace_add("write", calculate_preview)
    transition_text.bind("<KeyRelease>", calculate_preview)
    calculate_preview()

    button_frame = ttk.Frame(outer)
    button_frame.pack(fill="x", pady=(12, 0))
    ttk.Button(button_frame, text="Cancel", command=cancel_test).pack(side="right", padx=(8, 0))
    ttk.Button(
        button_frame, text="Start Test", command=start_test, style="Start.TButton"
    ).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", cancel_test)
    root.mainloop()

    if not result["accepted"]:
        raise SystemExit("Test cancelled before instrument connection.")
    return result["edge_filter"]


# ==========================================================
# BASIC VALIDATION AND CONVERSION
# ==========================================================
def validate_user_settings():
    if LOCAL_WAVEFORM_DIRECTORY is None:
        raise RuntimeError("The local waveform save folder has not been selected.")
    if NUM_ACQUISITIONS < 1:
        raise ValueError("NUM_ACQUISITIONS must be at least 1.")
    if DLP_SETPOINT_UNITS_PER_WATT <= 0:
        raise ValueError("DLP_SETPOINT_UNITS_PER_WATT must be greater than zero.")
    if not DLP_TRANSITIONS_W:
        raise ValueError("DLP_TRANSITIONS_W cannot be empty.")
    for transition in DLP_TRANSITIONS_W:
        if not isinstance(transition, (tuple, list)) or len(transition) != 2:
            raise ValueError(f"Invalid transition {transition!r}; expected (start_w, end_w).")
        start_w, end_w = transition
        if start_w < 0 or end_w < 0:
            raise ValueError(f"Transition powers must be nonnegative: {transition!r}")
        if start_w == end_w:
            raise ValueError(f"Transition endpoints must differ: {transition!r}")

def power_w_to_dut_units(power_w):
    value = power_w * DLP_SETPOINT_UNITS_PER_WATT
    rounded = int(round(value))
    if not np.isclose(value, rounded, rtol=0.0, atol=1e-9):
        raise ValueError(
            f"{power_w} W cannot be represented exactly with "
            f"{DLP_SETPOINT_UNITS_PER_WATT} DUT units/W."
        )
    return rounded


def transition_edge(start_power_w, end_power_w):
    return "RISE" if end_power_w > start_power_w else "FALL"


def format_number_for_filename(value):
    return f"{value:g}".replace("-", "neg").replace(".", "p")

# ==========================================================
# CONNECT TO OSCILLOSCOPE
# ==========================================================
def connect_scope():
    global scope_resource_manager, scope
    if not SCOPE_ADDRESS:
        raise RuntimeError("Oscilloscope address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_OSCILLOSCOPE_ADDRESS.")
    print("\nConnecting to oscilloscope...")
    scope_resource_manager = pyvisa.ResourceManager()
    scope = scope_resource_manager.open_resource(SCOPE_ADDRESS)
    scope.timeout = 30000
    scope.read_termination = "\n"
    scope.write_termination = "\n"
    scope.chunk_size = WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES
    print(f"Oscilloscope connected: {scope.query('*IDN?').strip()}")

# ==========================================================
# CONNECT TO RF GENERATOR
# ==========================================================
def connect_generator():
    global gen
    if not GEN_IP:
        raise RuntimeError("DUT generator address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_GENERATOR_ADDRESS.")
    print("\nConnecting to RF generator...")
    gen = AECommandsDLPUnits.generator(GEN_IP)
    gen.RF_OFF()
    sleep(DUT_COMMAND_DELAY_SEC)
    try:
        gen.pulseEnable(0)
    except Exception as error:
        print(f"Startup warning while disabling pulse switching: {error}")
    try:
        gen.setPulseMode(0)
    except Exception as error:
        print(f"Startup warning while disabling pulse mode: {error}")
    print("RF generator connected and RF is off.")

# ==========================================================
# CLOSE CONNECTIONS
# ==========================================================
def close_generator_socket():
    """Close the actual AETCP object; the supplied library close() references self.gen."""
    if gen is None:
        return
    device = getattr(gen, "device", None)
    if device is not None and hasattr(device, "close"):
        device.close()
    else:
        gen.close()


def close_connections():
    global scope, scope_resource_manager, gen
    print("\nClosing instrument connections...")
    if gen is not None:
        disable_generator_dlp_mode()
        try:
            close_generator_socket()
        except Exception as error:
            print(f"Warning while closing generator connection: {error}")
    if scope is not None:
        try:
            scope.close()
        except Exception as error:
            print(f"Warning while closing scope connection: {error}")
    if scope_resource_manager is not None:
        try:
            scope_resource_manager.close()
        except Exception as error:
            print(f"Warning while closing VISA resource manager: {error}")
    print("Instrument connections closed.")

# ==========================================================
# POWER AND COUPLER CALCULATIONS
# ==========================================================
def expected_coupler_vpk(actual_power_w):
    coupled_power_w = actual_power_w / (10 ** (COUPLER_DB / 10))
    coupled_vrms = np.sqrt(coupled_power_w * SYSTEM_IMPEDANCE_OHMS)
    return coupled_vrms * np.sqrt(2)


def calculate_transition_trigger_level(start_power_w, end_power_w):
    start_vpk = expected_coupler_vpk(start_power_w)
    end_vpk = expected_coupler_vpk(end_power_w)
    low_vpk = min(start_vpk, end_vpk)
    high_vpk = max(start_vpk, end_vpk)
    return low_vpk + 0.5 * (high_vpk - low_vpk)


def round_vertical_scale_up(requested_scale):
    """Round upward to a conventional 1-2-2.5-5 scope scale sequence."""
    if not np.isfinite(requested_scale) or requested_scale <= 0:
        raise ValueError(f"Invalid requested vertical scale: {requested_scale}")

    exponent = int(np.floor(np.log10(requested_scale)))
    for decade in range(exponent - 1, exponent + 3):
        multiplier = 10.0 ** decade
        for mantissa in VERTICAL_SCALE_MANTISSAS:
            candidate = mantissa * multiplier
            if candidate >= requested_scale * (1.0 - 1e-12):
                return candidate
    raise ValueError(f"Could not round vertical scale: {requested_scale}")


def calculate_adaptive_vertical_scale(start_power_w, end_power_w):
    """Choose V/div from the larger commanded DLP state.

    The scope sees a bipolar RF waveform. The larger state therefore controls
    clipping risk. The calculation places its expected positive/negative peak
    within VERTICAL_TARGET_PEAK_DIVISIONS and adds explicit headroom before
    rounding upward to a conventional scope scale.
    """
    if not ADAPTIVE_VERTICAL_SCALE:
        return FIXED_VERTICAL_SCALE_V_PER_DIV

    maximum_power_w = max(start_power_w, end_power_w)
    maximum_expected_vpk = expected_coupler_vpk(maximum_power_w)
    requested_scale = (
        maximum_expected_vpk
        * VERTICAL_HEADROOM_FACTOR
        / VERTICAL_TARGET_PEAK_DIVISIONS
    )
    selected_scale = round_vertical_scale_up(requested_scale)
    return float(np.clip(
        selected_scale,
        MIN_VERTICAL_SCALE_V_PER_DIV,
        MAX_VERTICAL_SCALE_V_PER_DIV,
    ))

# ==========================================================
# VALIDATE PULSE SETTINGS
# ==========================================================
def validate_pulse_settings(pulse_frequency_hz, duty_cycle_percent):
    if not isinstance(pulse_frequency_hz, int):
        raise TypeError("Pulse frequency must be an integer in Hz.")
    if pulse_frequency_hz < 2 or pulse_frequency_hz > 100000:
        raise ValueError("Pulse frequency must be between 2 and 100,000 Hz.")
    if not isinstance(duty_cycle_percent, int):
        raise TypeError("Duty cycle must be an integer percentage.")
    if duty_cycle_percent < 1 or duty_cycle_percent > 99:
        raise ValueError("DLP duty cycle must be between 1 and 99 percent.")

    pulse_period_sec = 1.0 / pulse_frequency_hz
    state_1_time_sec = pulse_period_sec * duty_cycle_percent / 100.0
    state_0_time_sec = pulse_period_sec - state_1_time_sec
    if state_1_time_sec < 5e-6:
        raise ValueError(
            f"State 1 time is {state_1_time_sec * 1e6:.3f} us; minimum is 5 us."
        )
    if state_0_time_sec < 5e-6:
        raise ValueError(
            f"State 0 time is {state_0_time_sec * 1e6:.3f} us; minimum is 5 us."
        )
    return pulse_period_sec, state_1_time_sec, state_0_time_sec

# ==========================================================
# SCOPE CONFIGURATION
# ==========================================================
def calculate_scope_timebase(pulse_frequency_hz):
    pulse_period_sec = 1.0 / pulse_frequency_hz
    total_screen_time_sec = pulse_period_sec * PULSE_PERIODS_ON_SCREEN
    return pulse_period_sec, total_screen_time_sec, total_screen_time_sec / HORIZONTAL_DIVISIONS


def configure_scope(edge, pulse_frequency_hz, start_power_w, end_power_w):
    edge = edge.upper()
    if edge not in ("RISE", "FALL"):
        raise ValueError("Scope edge must be RISE or FALL.")

    pulse_period_sec, total_screen_time_sec, time_per_div_sec = calculate_scope_timebase(
        pulse_frequency_hz
    )
    vertical_scale = calculate_adaptive_vertical_scale(start_power_w, end_power_w)
    maximum_power_w = max(start_power_w, end_power_w)
    maximum_expected_vpk = expected_coupler_vpk(maximum_power_w)

    print("\nConfiguring oscilloscope...")
    print(f"Requested transition = {edge}")
    print(f"Pulse frequency       = {pulse_frequency_hz} Hz")
    print(f"Pulse period          = {pulse_period_sec:.9e} s")
    print(f"Total screen time     = {total_screen_time_sec:.9e} s")
    print(f"Scope time per div    = {time_per_div_sec:.9e} s/div")
    print(f"Maximum state power   = {maximum_power_w:g} W")
    print(f"Expected maximum Vpk  = {maximum_expected_vpk:.6f} V")
    print(f"Selected vertical scale = {vertical_scale:.6f} V/div")

    scope.write("RECALL:SETUP DEFAULT")
    scope.write("MEASUrement:DELETEALL")
    sleep(1)
    scope.write("*CLS")
    scope.write("ACQ:STATE OFF")
    scope.write(f"SEL:{RF_CHANNEL} ON")
    scope.write(f"{RF_CHANNEL}:SCA {vertical_scale}")
    scope.write(f"{RF_CHANNEL}:POS 0")
    scope.write(f"{RF_CHANNEL}:COUP DC")
    scope.write(f"{RF_CHANNEL}:TERMINATION 50")
    scope.write(f"HOR:SCALE {time_per_div_sec}")
    try:
        scope.write(f"HOR:POS {TRIGGER_POSITION_PERCENT}")
    except Exception as error:
        print(f"Warning while setting horizontal trigger position: {error}")
    try:
        scope.write(f"HORIZONTAL:RECORDLENGTH {RECORD_LENGTH}")
    except Exception as error:
        print(f"Warning while setting record length: {error}")
    scope.write(f"TRIG:A:EDGE:SOU {RF_CHANNEL}")
    scope.write(f"TRIG:A:EDGE:SLO {edge}")
    scope.write("ACQ:STOPA SEQ")
    scope.write(f"DATA:SOURCE {RF_CHANNEL}")
    scope.write("DATA:ENCDG RIBINARY")
    scope.write("DATA:WIDTH 2")
    scope.write("DATA:START 1")
    try:
        scope.write(f"DATA:STOP {RECORD_LENGTH}")
    except Exception as error:
        print(f"Warning while setting DATA:STOP: {error}")

    print("Oscilloscope configuration complete.")
    print(f"{RF_CHANNEL} enabled state = {scope.query(f'SEL:{RF_CHANNEL}?').strip()}")
    actual_vertical_scale = float(scope.query(f"{RF_CHANNEL}:SCA?").strip())
    print(f"Reported vertical scale = {actual_vertical_scale:.9e} V/div")
    if actual_vertical_scale + 1e-15 < vertical_scale:
        print(
            "Warning: scope selected a smaller vertical scale than requested; "
            "check the waveform for clipping."
        )
    reported_scale = float(scope.query("HOR:SCALE?").strip())
    print(f"Reported time per div = {reported_scale:.9e} s/div")
    print(
        f"Reported screen span = "
        f"{reported_scale * HORIZONTAL_DIVISIONS:.9e} s"
    )
    try:
        actual_record_length = scope.query("HORIZONTAL:RECORDLENGTH?").strip()
        print(f"Reported record length = {actual_record_length} points")
    except Exception as error:
        print(f"Warning while reading actual record length: {error}")


def arm_scope(edge, trigger_level):
    scope.write("ACQ:STATE OFF")
    scope.write("*CLS")
    scope.write(f"TRIG:A:EDGE:SOU {RF_CHANNEL}")
    scope.write(f"TRIG:A:EDGE:SLO {edge}")
    scope.write(f"TRIG:A:LEV:{RF_CHANNEL} {trigger_level}")
    scope.write("TRIG:A:MOD NORM")
    scope.write("ACQ:STOPA SEQ")
    scope.write("ACQ:STATE ON")
    sleep(0.10)

    acquisition_state = scope.query("ACQ:STATE?").strip()
    trigger_state = scope.query("TRIG:STATE?").strip().upper()
    print(f"Scope armed for {edge} edge at {trigger_level:.6f} V")
    print(
        f"Initial scope status: ACQ:STATE={acquisition_state}, "
        f"TRIG:STATE={trigger_state}"
    )

# ==========================================================
# GENERATOR DLP CONFIGURATION
# ==========================================================
def configure_generator_dlp_mode(
    rf_frequency_khz,
    start_power_w,
    end_power_w,
    pulse_frequency_hz,
    duty_cycle_percent,
):
    pulse_period_sec, state_1_time_sec, state_0_time_sec = validate_pulse_settings(
        pulse_frequency_hz, duty_cycle_percent
    )
    state_0_units = power_w_to_dut_units(start_power_w)
    state_1_units = power_w_to_dut_units(end_power_w)

    print("\nConfiguring RF generator for true DLP mode...")
    print(f"RF frequency          = {rf_frequency_khz} kHz (both states)")
    print(f"State 0 power         = {start_power_w} W ({state_0_units} DUT units)")
    print(f"State 1 power         = {end_power_w} W ({state_1_units} DUT units)")
    print(f"Requested transition  = {start_power_w} W -> {end_power_w} W")
    print(f"Pulse frequency       = {pulse_frequency_hz} Hz")
    print(f"State 1 duty cycle    = {duty_cycle_percent} percent")
    print(f"Pulse period          = {pulse_period_sec * 1e6:.3f} us")
    print(f"State 1 time          = {state_1_time_sec * 1e6:.3f} us")
    print(f"State 0 time          = {state_0_time_sec * 1e6:.3f} us")

    gen.RF_OFF()
    sleep(DUT_COMMAND_DELAY_SEC)

    # Pulse switching must be off while the DLP state parameters are changed.
    # Keep/select true DLP mode BEFORE sending the state-specific commands.
    # Sending DLP_setpoint while pulse mode is disabled returns CSR 12
    # (Feature not available) on the DLP-capable DUT.
    gen.pulseEnable(0)
    gen.setPulseMode(3)
    sleep(DUT_COMMAND_DELAY_SEC)

    gen.setRegulationMode(REGULATION_MODE)
    gen.setDLPfreqMode(DLP_FREQUENCY_MODE_STATE_1, DLP_FREQUENCY_MODE_STATE_0)
    gen.setDLPFreq_s1(int(rf_frequency_khz))
    gen.setDLPFreq_s0(int(rf_frequency_khz))
    gen.DLP_setpoint(state_1_units, state_0_units)
    gen.setPulseFreq_DutyCycle(int(pulse_frequency_hz), int(duty_cycle_percent))
    gen.pulseEnable(1)
    sleep(DUT_COMMAND_DELAY_SEC)

    print("\nTurning RF on...")
    gen.RF_ON()
    print("RF generator is on in DLP mode.")
    print(f"Waiting {PULSE_SETTLE_TIME_SEC} seconds for DLP output to settle...")
    sleep(PULSE_SETTLE_TIME_SEC)


def turn_rf_off_between_conditions():
    """Turn RF off without clearing pulse-enable or true DLP mode."""
    if gen is None:
        return
    print("\nTurning RF off before the next DLP condition...")
    try:
        gen.RF_OFF()
        sleep(DUT_COMMAND_DELAY_SEC)
        print("RF is off. DLP mode remains enabled.")
    except Exception as error:
        print(f"Warning: RF could not be turned off between conditions: {error}")


def disable_generator_dlp_mode():
    if gen is None:
        return
    print("\nTurning RF off and disabling DLP mode...")
    try:
        gen.RF_OFF()
        sleep(DUT_COMMAND_DELAY_SEC)
        print("RF is off.")
    except Exception as error:
        print(f"Warning: RF could not be turned off: {error}")
    try:
        gen.pulseEnable(0)
        print("Pulse switching disabled.")
    except Exception as error:
        print(f"Warning: pulse switching could not be disabled: {error}")
    try:
        gen.setPulseMode(0)
        print("DLP mode disabled.")
    except Exception as error:
        print(f"Warning: DLP mode could not be disabled: {error}")

# ==========================================================
# TRIGGER AND WAVEFORM SAVE
# ==========================================================
def wait_for_scope_trigger(timeout_sec):
    """Wait for a completed single-sequence acquisition.

    TRIG:STATE? = SAVE is not treated as proof of a new trigger on the MSO44.
    Completion is determined from ACQ:STATE? returning 0 after the scope has
    been armed with ACQ:STOPA SEQ and ACQ:STATE ON.
    """
    start_time = time()
    last_trigger_state = None
    last_acquisition_state = None

    while time() - start_time < timeout_sec:
        acquisition_state = scope.query("ACQ:STATE?").strip()
        trigger_state = scope.query("TRIG:STATE?").strip().upper()

        if (
            acquisition_state != last_acquisition_state
            or trigger_state != last_trigger_state
        ):
            elapsed = time() - start_time
            print(
                f"Scope status after {elapsed:.2f} s: "
                f"ACQ:STATE={acquisition_state}, TRIG:STATE={trigger_state}"
            )
            last_acquisition_state = acquisition_state
            last_trigger_state = trigger_state

        # In single-sequence mode, the acquisition state returns to zero only
        # after the triggered acquisition has completed.
        if acquisition_state in ("0", "OFF"):
            return True

        sleep(0.05)

    print(
        f"Scope did not complete a single-sequence acquisition within "
        f"{timeout_sec:.1f} seconds."
    )
    try:
        scope.write("ACQ:STATE OFF")
    except Exception as error:
        print(f"Warning while stopping timed-out acquisition: {error}")
    return False


def smart_dlp_trigger(edge, start_power_w, end_power_w, timeout_sec=TRIGGER_TIMEOUT_SEC):
    """Acquire a full DLP period using a reliable RF-carrier zero crossing.

    CH1 contains the raw RF carrier, not a detected envelope. An edge trigger
    placed between calculated RF peak amplitudes is not a valid DLP-envelope
    trigger and may never be crossed. Triggering at 0 V locks acquisition to
    the carrier while the longer time window captures at least one complete
    DLP cycle for offline transition analysis.
    """
    start_vpk = expected_coupler_vpk(start_power_w)
    end_vpk = expected_coupler_vpk(end_power_w)
    envelope_midpoint = calculate_transition_trigger_level(start_power_w, end_power_w)
    carrier_trigger_level = 0.0

    print(f"\nEstimated State 0 coupler peak = {start_vpk:.6f} V")
    print(f"Estimated State 1 coupler peak = {end_vpk:.6f} V")
    print(f"Estimated envelope midpoint     = {envelope_midpoint:.6f} V")
    print("CH1 is raw RF, so the scope will trigger on a 0 V RF-carrier crossing.")
    print("The complete DLP transition will be located later from the captured envelope.")

    arm_scope("RISE", carrier_trigger_level)
    if wait_for_scope_trigger(timeout_sec):
        print("Single-sequence RF-carrier acquisition completed.")
        return True

    print("Normal-mode carrier trigger timed out; trying Auto mode once.")
    scope.write("ACQ:STATE OFF")
    scope.write("TRIG:A:MOD AUTO")
    scope.write("ACQ:STOPA SEQ")
    scope.write("ACQ:STATE ON")
    if wait_for_scope_trigger(timeout_sec):
        print("Auto-mode single-sequence acquisition completed.")
        return True

    print("Scope failed to complete an acquisition in both Normal and Auto modes.")
    return False


def save_wave(filename):
    """Download the completed waveform over Ethernet and save the CSV locally."""
    acquisition_state = scope.query("ACQ:STATE?").strip()
    if acquisition_state not in ("0", "OFF"):
        raise RuntimeError(
            "Refusing to save because the single-sequence acquisition is "
            f"still active (ACQ:STATE={acquisition_state})."
        )

    output_path = Path(filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_suffix(output_path.suffix + ".partial")

    print(f"Transferring waveform over Ethernet to {output_path}")
    original_timeout = scope.timeout
    try:
        scope.timeout = WAVEFORM_TRANSFER_TIMEOUT_MS
        scope.write("ACQUIRE:STATE STOP")
        scope.write("*WAI")
        scope.write(f"DATA:SOURCE {RF_CHANNEL}")
        scope.write("DATA:ENCDG RIBINARY")
        scope.write("DATA:WIDTH 2")

        actual_record_length = int(
            float(scope.query("HORIZONTAL:RECORDLENGTH?"))
        )
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
            raise RuntimeError(f"No waveform points returned for {RF_CHANNEL}.")

        index = np.arange(raw.size, dtype=np.float64)
        times = xzero + (index - pt_off) * xincr
        values = (raw - yoff) * ymult + yzero

        np.savetxt(
            partial_path,
            np.column_stack((times, values)),
            delimiter=",",
            header=f"Time ({xunit}),{RF_CHANNEL} ({yunit})",
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


def build_filename(
    edge,
    rf_frequency_khz,
    start_power_w,
    end_power_w,
    pulse_frequency_hz,
    duty_cycle_percent,
    acquisition_number,
):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    start_text = format_number_for_filename(start_power_w)
    end_text = format_number_for_filename(end_power_w)
    filename = (
        f"RF_Freq_{edge}{rf_frequency_khz}_"
        f"DLP_{start_text}W_to_{end_text}W_"
        f"PulseFreq{pulse_frequency_hz}Hz_"
        f"Duty{duty_cycle_percent}pct_"
        f"Acq{acquisition_number}_{timestamp}.csv"
    )
    return LOCAL_WAVEFORM_DIRECTORY / filename

# ==========================================================
# CAPTURE ROUTINES
# ==========================================================
def capture_test_condition(
    rf_frequency_khz,
    start_power_w,
    end_power_w,
    pulse_frequency_hz,
    duty_cycle_percent,
):
    edge = transition_edge(start_power_w, end_power_w)
    configure_scope(edge, pulse_frequency_hz, start_power_w, end_power_w)
    configure_generator_dlp_mode(
        rf_frequency_khz,
        start_power_w,
        end_power_w,
        pulse_frequency_hz,
        duty_cycle_percent,
    )

    successful_acquisitions = 0
    failed_acquisitions = 0
    for acquisition_index in range(1, NUM_ACQUISITIONS + 1):
        print("\n==================================================")
        print(f"Transition           = {start_power_w} W -> {end_power_w} W")
        print(f"Transition direction = {edge}")
        print(f"RF frequency         = {rf_frequency_khz} kHz")
        print(f"Pulse frequency      = {pulse_frequency_hz} Hz")
        print(f"State 1 duty cycle   = {duty_cycle_percent} percent")
        print(f"Acquisition          = {acquisition_index}/{NUM_ACQUISITIONS}")
        print("==================================================")

        triggered = smart_dlp_trigger(edge, start_power_w, end_power_w)
        if not triggered:
            failed_acquisitions += 1
            print("Failed to trigger for this acquisition.")
            continue

        filename = build_filename(
            edge,
            rf_frequency_khz,
            start_power_w,
            end_power_w,
            pulse_frequency_hz,
            duty_cycle_percent,
            acquisition_index,
        )
        save_wave(filename)
        successful_acquisitions += 1
        sleep(ACQUISITION_DELAY_SEC)

    print("\nTest condition complete.")
    print(f"Successful acquisitions = {successful_acquisitions}")
    print(f"Failed acquisitions     = {failed_acquisitions}")


def capture_dlp_transitions(edge_filter=None):
    if edge_filter is not None:
        edge_filter = edge_filter.upper()
        if edge_filter not in ("RISE", "FALL"):
            raise ValueError("edge_filter must be RISE, FALL, or None.")

    selected = [
        transition for transition in DLP_TRANSITIONS_W
        if edge_filter is None or transition_edge(*transition) == edge_filter
    ]
    if not selected:
        print(f"\nNo {edge_filter} transitions are present in DLP_TRANSITIONS_W.")
        return

    label = edge_filter if edge_filter is not None else "RISE and FALL"
    print(f"\nStarting DLP {label} captures.")
    for pulse_frequency_hz in PULSE_FREQUENCIES_HZ:
        for duty_cycle_percent in PULSE_DUTY_CYCLES_PERCENT:
            validate_pulse_settings(pulse_frequency_hz, duty_cycle_percent)
            for rf_frequency_khz in RF_FREQUENCIES_KHZ:
                for start_power_w, end_power_w in selected:
                    try:
                        capture_test_condition(
                            rf_frequency_khz,
                            start_power_w,
                            end_power_w,
                            pulse_frequency_hz,
                            duty_cycle_percent,
                        )
                    except Exception as error:
                        print("\nCapture condition failed.")
                        print(f"Transition = {start_power_w} W -> {end_power_w} W")
                        print(f"RF frequency = {rf_frequency_khz} kHz")
                        print(f"Pulse frequency = {pulse_frequency_hz} Hz")
                        print(f"Duty cycle = {duty_cycle_percent} percent")
                        print(f"Error = {error}")
                    finally:
                        # Keep true DLP mode selected between capture conditions.
                        # Only remove RF so the next state pair can be configured safely.
                        turn_rf_off_between_conditions()
                        sleep(CONDITION_DELAY_SEC)
    print(f"\nDLP {label} captures complete.")

# ==========================================================
# MAIN MENU
# ==========================================================
def main():
    validate_unified_launcher_request()
    edge_filter = configure_test_with_gui()
    validate_user_settings()
    connect_scope()
    connect_generator()
    capture_dlp_transitions(edge_filter)
    print("\nDLP capture complete.")

# ==========================================================
# RUN SCRIPT
# ==========================================================
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nScript interrupted by user.")
    except Exception as error:
        print(f"\nUnexpected script error: {error}")
    finally:
        close_connections()
        print("\nScript finished.")
