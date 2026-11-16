import sys
import os
from pathlib import Path
from time import sleep, time
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

import numpy as np

# ==========================================================
# LOAD AE-SPECIFIC DEPENDENCIES
# ==========================================================

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default
from suite_common.inputs import format_number_list, parse_number_list
from suite_common.output_folders import create_unique_test_output_folder

from suite_instruments.factory import InstrumentFactory
from suite_instruments.transport.visa import resource_manager

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
    """Use a nonblank launcher value as an editable GUI default."""
    value = os.environ.get(environment_name, "").strip()
    return value if value else standalone_default

def validate_unified_launcher_request():
    """Reject an accidental cross-mode launch before any device communication."""
    if not LAUNCHED_BY_UNIFIED_GUI:
        return
    if LAUNCHER_RISE_FALL_MODE and LAUNCHER_RISE_FALL_MODE != "SLP":
        raise SystemExit(
            "This is the SLP collection script, but the unified launcher requested "
            f"{LAUNCHER_RISE_FALL_MODE!r}. Link the SLP sequence entry to the SLP script."
        )
    if LAUNCHER_RISE_FALL_EDGE not in ("RISE", "FALL"):
        raise SystemExit(
            "The unified launcher must provide PARAMOUNT_RISE_FALL_EDGE as "
            "RISE or FALL for the SLP collection script."
        )

# ==========================================================
# USER SETTINGS
# ==========================================================

SCOPE_ADDRESS = bench_default("oscilloscope")
GEN_IP = bench_default("generator")
SELECTED_CAPTURE_MODE = (
    LAUNCHER_RISE_FALL_EDGE
    if LAUNCHED_BY_UNIFIED_GUI and LAUNCHER_RISE_FALL_EDGE in ("RISE", "FALL")
    else "BOTH"
)

NUM_ACQUISITIONS = 25
RF_CHANNEL = "CH1"
VERT_SCALE_RF = 0.2
RECORD_LENGTH = 1000000

# The scope is assumed to have 10 horizontal divisions.
HORIZONTAL_DIVISIONS = 10

# 1.0 displays approximately one pulse period across the full screen.
# Use 1.2 for a small amount of margin around the pulse.
PULSE_PERIODS_ON_SCREEN = 1.0

TRIGGER_TIMEOUT_SEC = 5.0
PULSE_SETTLE_TIME_SEC = 1.0 #Belei Note: was 1.0
ACQUISITION_DELAY_SEC = 0.3
CONDITION_DELAY_SEC = 0.5

# RF frequencies are in kHz.
RF_FREQUENCIES_KHZ = [12882, 13560, 14238]

# Actual requested power is base setpoint multiplied by HALO_MODE.
POWER_SETPOINTS = [3, 10, 25, 50, 100, 150, 200, 250, 300]
# POWER_SETPOINTS = [6, 10, 25, 50, 100, 150, 200, 250, 300, 400, 500, 600]
HALO_MODE = 10

# Pulse settings.
PULSE_FREQUENCIES_HZ = [4000] # Belei Note: was 1000
PULSE_DUTY_CYCLES_PERCENT = [20]

COUPLER_DB = 60
SYSTEM_IMPEDANCE = 50
# The user selects a parent location each time the script runs. A dated test
# folder is then created inside that selected location.
SAVE_DIRECTORY = None
STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER = str(Path.home())
DEFAULT_OUTPUT_PARENT_FOLDER = _launcher_default("PARAMOUNT_COLLECTION_PARENT_FOLDER", STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER)
TEST_FOLDER_NAME = "SLP Statistical Timing Analysis"
WAVEFORM_TRANSFER_TIMEOUT_MS = 120_000
WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES = 25 * 1024 * 1024

# ==========================================================
# GLOBAL CONNECTION OBJECTS
# ==========================================================

scope_resource_manager = None
scope = None
gen = None

# ==========================================================
# GRAPHICAL RUN CONFIGURATION
# ==========================================================
def validate_gui_global_definitions():
    """Give a clear error if a future edit removes a GUI-backed setting."""
    required_names = (
        "SCOPE_ADDRESS", "GEN_IP", "SELECTED_CAPTURE_MODE",
        "NUM_ACQUISITIONS", "RF_CHANNEL", "VERT_SCALE_RF",
        "RECORD_LENGTH", "HORIZONTAL_DIVISIONS",
        "PULSE_PERIODS_ON_SCREEN", "TRIGGER_TIMEOUT_SEC",
        "PULSE_SETTLE_TIME_SEC", "ACQUISITION_DELAY_SEC",
        "CONDITION_DELAY_SEC", "RF_FREQUENCIES_KHZ",
        "POWER_SETPOINTS", "HALO_MODE", "PULSE_FREQUENCIES_HZ",
        "PULSE_DUTY_CYCLES_PERCENT", "COUPLER_DB",
        "SYSTEM_IMPEDANCE", "TEST_FOLDER_NAME",
        "WAVEFORM_TRANSFER_TIMEOUT_MS",
        "WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES",
    )
    missing_names = [name for name in required_names if name not in globals()]
    if missing_names:
        raise RuntimeError(
            "Required GUI settings are not defined: " + ", ".join(missing_names)
        )


def configure_run_with_gui():
    """Edit this run graphically while script globals remain persistent defaults."""
    global SCOPE_ADDRESS, GEN_IP, SELECTED_CAPTURE_MODE
    global NUM_ACQUISITIONS, RF_CHANNEL, VERT_SCALE_RF, RECORD_LENGTH
    global HORIZONTAL_DIVISIONS, PULSE_PERIODS_ON_SCREEN
    global TRIGGER_TIMEOUT_SEC, PULSE_SETTLE_TIME_SEC
    global ACQUISITION_DELAY_SEC, CONDITION_DELAY_SEC
    global RF_FREQUENCIES_KHZ, POWER_SETPOINTS, HALO_MODE
    global PULSE_FREQUENCIES_HZ, PULSE_DUTY_CYCLES_PERCENT
    global COUPLER_DB, SYSTEM_IMPEDANCE, TEST_FOLDER_NAME
    global WAVEFORM_TRANSFER_TIMEOUT_MS, WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES

    root = tk.Tk()
    window_edge = (
        f" - {LAUNCHER_RISE_FALL_EDGE}"
        if LAUNCHED_BY_UNIFIED_GUI and LAUNCHER_RISE_FALL_EDGE
        else ""
    )
    root.title(f"SLP Statistical Timing Data Collection Setup{window_edge}")
    root.geometry("940x720")
    root.minsize(820, 620)

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
    ttk.Label(
        outer,
        text=f"SLP Statistical Timing Data Collection{window_edge}",
        style="Header.TLabel",
    ).pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Review or adjust this run. Launcher-provided equipment addresses are "
            "editable defaults. Direct script settings remain the standalone defaults."
        ),
        wraplength=880,
    ).pack(anchor="w", pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    matrix_tab = ttk.Frame(notebook, padding=12)
    instrument_tab = ttk.Frame(notebook, padding=12)
    scope_tab = ttk.Frame(notebook, padding=12)
    timing_tab = ttk.Frame(notebook, padding=12)
    advanced_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(matrix_tab, text="Test Matrix")
    notebook.add(instrument_tab, text="Instruments")
    notebook.add(scope_tab, text="Scope")
    notebook.add(timing_tab, text="Timing")
    notebook.add(advanced_tab, text="Advanced")
    notebook.add(output_tab, text="Output")

    variables = {}

    def add_entry(parent, row, label, key, default, help_text="", width=34):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=4
        )
        variable = tk.StringVar(value=str(default))
        variables[key] = variable
        ttk.Entry(parent, textvariable=variable, width=width).grid(
            row=row, column=1, sticky="ew", pady=4
        )
        if help_text:
            ttk.Label(
                parent,
                text=help_text,
                foreground="#555555",
                wraplength=360,
            ).grid(row=row, column=2, sticky="w", padx=(10, 0), pady=4)

    output_parent = tk.StringVar(value=DEFAULT_OUTPUT_PARENT_FOLDER)
    ttk.Label(output_tab, text="Parent output folder", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
    ttk.Entry(output_tab, textvariable=output_parent, width=72).grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
    def browse_output_parent():
        current = output_parent.get().strip()
        selected = filedialog.askdirectory(title="Select parent folder for SLP waveform data", initialdir=current if current and Path(current).is_dir() else STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER, mustexist=True, parent=root)
        if selected:
            output_parent.set(selected)
    ttk.Button(output_tab, text="Browse...", command=browse_output_parent).grid(row=1, column=2, sticky="w", padx=(10, 0), pady=4)
    ttk.Label(output_tab, text="The timestamped SLP test folder is created inside this parent folder only after Start Collection is clicked and all settings validate successfully.", foreground="#555555", wraplength=760).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
    output_tab.columnconfigure(0, weight=1)

    capture_mode = tk.StringVar(value=SELECTED_CAPTURE_MODE)
    ttk.Label(matrix_tab, text="Capture mode", style="Section.TLabel").grid(
        row=0, column=0, sticky="w", pady=(0, 5)
    )
    mode_frame = ttk.Frame(matrix_tab)
    mode_frame.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 12))
    if LAUNCHED_BY_UNIFIED_GUI:
        selected_edge_text = (
            "Rising Edge" if SELECTED_CAPTURE_MODE == "RISE" else "Falling Edge"
        )
        ttk.Label(
            mode_frame,
            text=f"{selected_edge_text} (selected by unified sequence)",
            foreground="#1F4E78",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        ttk.Label(
            mode_frame,
            text=(
                "Finish or cancel this edge, then close this GUI. The unified launcher "
                "will show a separate setup page before the next edge."
            ),
            foreground="#555555",
            wraplength=570,
        ).pack(side="left", padx=(18, 0))
    else:
        for label, value in (
            ("Rising Edge", "RISE"),
            ("Falling Edge", "FALL"),
            ("Both Edges", "BOTH"),
        ):
            ttk.Radiobutton(
                mode_frame,
                text=label,
                variable=capture_mode,
                value=value,
            ).pack(side="left", padx=(0, 20))

    add_entry(matrix_tab, 2, "Acquisitions per condition", "num_acq", NUM_ACQUISITIONS)
    add_entry(
        matrix_tab, 3, "RF frequencies (kHz)", "rf_frequencies",
        format_number_list(RF_FREQUENCIES_KHZ), "Comma-separated whole numbers."
    )
    add_entry(
        matrix_tab, 4, "Base power setpoints", "power_setpoints",
        format_number_list(POWER_SETPOINTS),
        "Actual power = base setpoint x HALO_MODE."
    )
    add_entry(matrix_tab, 5, "HALO mode multiplier", "halo_mode", HALO_MODE)
    add_entry(
        matrix_tab, 6, "Pulse frequencies (Hz)", "pulse_frequencies",
        format_number_list(PULSE_FREQUENCIES_HZ), "Comma-separated whole numbers."
    )
    add_entry(
        matrix_tab, 7, "Duty cycles (%)", "duty_cycles",
        format_number_list(PULSE_DUTY_CYCLES_PERCENT),
        "Comma-separated whole percentages."
    )
    planned = tk.StringVar()
    ttk.Label(matrix_tab, textvariable=planned, style="Section.TLabel").grid(
        row=8, column=0, columnspan=3, sticky="w", pady=(14, 0)
    )

    add_entry(instrument_tab, 0, "Scope VISA address", "scope_address", SCOPE_ADDRESS, width=50)
    add_entry(instrument_tab, 1, "DUT IP / hostname", "gen_ip", GEN_IP, width=50)
    add_entry(instrument_tab, 2, "RF scope channel", "rf_channel", RF_CHANNEL)
    add_entry(instrument_tab, 3, "Test folder name", "folder_name", TEST_FOLDER_NAME)
    ttk.Label(
        instrument_tab,
        text=(
            "After Start Collection, a folder picker selects the parent directory. "
            "A timestamped test-name and generator-name subfolder is created inside it."
        ),
        foreground="#555555",
        wraplength=760,
    ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 0))

    add_entry(scope_tab, 0, "RF vertical scale (V/div)", "vertical_scale", VERT_SCALE_RF)
    add_entry(scope_tab, 1, "Record length", "record_length", RECORD_LENGTH)
    add_entry(scope_tab, 2, "Horizontal divisions", "horizontal_divisions", HORIZONTAL_DIVISIONS)
    add_entry(
        scope_tab, 3, "Pulse periods on screen", "periods_on_screen",
        PULSE_PERIODS_ON_SCREEN,
        "Total screen time = pulse period x this value."
    )
    add_entry(scope_tab, 4, "Trigger timeout (s)", "trigger_timeout", TRIGGER_TIMEOUT_SEC)

    add_entry(timing_tab, 0, "Pulse settle time (s)", "pulse_settle", PULSE_SETTLE_TIME_SEC)
    add_entry(timing_tab, 1, "Acquisition delay (s)", "acquisition_delay", ACQUISITION_DELAY_SEC)
    add_entry(timing_tab, 2, "Condition delay (s)", "condition_delay", CONDITION_DELAY_SEC)
    ttk.Label(
        timing_tab,
        text=(
            "Pulse frequency and duty-cycle combinations retain the script's existing "
            "5 us minimum Pulse On and Pulse Off validation."
        ),
        foreground="#555555",
        wraplength=760,
    ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(14, 0))

    add_entry(advanced_tab, 0, "Coupler attenuation (dB)", "coupler_db", COUPLER_DB)
    add_entry(advanced_tab, 1, "System impedance (ohms)", "impedance", SYSTEM_IMPEDANCE)
    add_entry(
        advanced_tab, 2, "Waveform transfer timeout (ms)",
        "transfer_timeout", WAVEFORM_TRANSFER_TIMEOUT_MS
    )
    add_entry(
        advanced_tab, 3, "Transfer chunk size (bytes)",
        "chunk_size", WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES
    )

    result = {"accepted": False}

    def number(key, label, minimum=None, maximum=None, integer=False):
        try:
            value = float(variables[key].get().strip())
        except ValueError as error:
            raise ValueError(f"{label} must be numeric.") from error
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
            acquisitions = int(float(variables["num_acq"].get()))
            rf_count = len(parse_number_list(
                variables["rf_frequencies"].get(), "RF frequencies"
            ))
            power_count = len(parse_number_list(
                variables["power_setpoints"].get(), "Power setpoints"
            ))
            pulse_count = len(parse_number_list(
                variables["pulse_frequencies"].get(), "Pulse frequencies"
            ))
            duty_count = len(parse_number_list(
                variables["duty_cycles"].get(), "Duty cycles"
            ))
            edge_count = 2 if capture_mode.get() == "BOTH" else 1
            total = acquisitions * rf_count * power_count * pulse_count * duty_count * edge_count
            planned.set(f"Planned waveform files: {total:,}")
        except Exception:
            planned.set("Planned waveform files: check entered values")

    def apply_and_start():
        nonlocal result
        global SCOPE_ADDRESS, GEN_IP, SELECTED_CAPTURE_MODE
        global NUM_ACQUISITIONS, RF_CHANNEL, VERT_SCALE_RF, RECORD_LENGTH
        global HORIZONTAL_DIVISIONS, PULSE_PERIODS_ON_SCREEN
        global TRIGGER_TIMEOUT_SEC, PULSE_SETTLE_TIME_SEC
        global ACQUISITION_DELAY_SEC, CONDITION_DELAY_SEC
        global RF_FREQUENCIES_KHZ, POWER_SETPOINTS, HALO_MODE
        global PULSE_FREQUENCIES_HZ, PULSE_DUTY_CYCLES_PERCENT
        global COUPLER_DB, SYSTEM_IMPEDANCE, TEST_FOLDER_NAME
        global WAVEFORM_TRANSFER_TIMEOUT_MS, WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES
        global SAVE_DIRECTORY

        try:
            selected_parent_text = output_parent.get().strip()
            if not selected_parent_text:
                raise ValueError("Select a parent output folder on the Output tab.")
            selected_parent = Path(selected_parent_text).expanduser().resolve()
            if not selected_parent.exists() or not selected_parent.is_dir():
                raise ValueError("The selected parent output folder does not exist.")
            mode = capture_mode.get()
            if LAUNCHED_BY_UNIFIED_GUI and mode != LAUNCHER_RISE_FALL_EDGE:
                raise ValueError(
                    "The selected edge no longer matches the unified sequence request. "
                    "Close this GUI and relaunch the sequence entry."
                )
            if mode not in ("RISE", "FALL", "BOTH"):
                raise ValueError("Select Rising Edge, Falling Edge, or Both Edges.")

            rf_values = parse_number_list(
                variables["rf_frequencies"].get(), "RF frequencies", integer_only=True
            )
            power_values = parse_number_list(
                variables["power_setpoints"].get(), "Power setpoints"
            )
            pulse_values = parse_number_list(
                variables["pulse_frequencies"].get(), "Pulse frequencies", integer_only=True
            )
            duty_values = parse_number_list(
                variables["duty_cycles"].get(), "Duty cycles", integer_only=True
            )
            if any(value <= 0 for value in rf_values):
                raise ValueError("RF frequencies must be greater than zero.")
            if any(value < 0 for value in power_values):
                raise ValueError("Power setpoints cannot be negative.")
            for pulse_frequency in pulse_values:
                for duty_cycle in duty_values:
                    validate_pulse_settings(pulse_frequency, duty_cycle)

            values = {
                "num_acq": number("num_acq", "Acquisitions", 1, integer=True),
                "halo_mode": number("halo_mode", "HALO mode", 0.000001),
                "vertical_scale": number("vertical_scale", "RF vertical scale", 0.000001),
                "record_length": number("record_length", "Record length", 1, integer=True),
                "horizontal_divisions": number(
                    "horizontal_divisions", "Horizontal divisions", 1, integer=True
                ),
                "periods_on_screen": number(
                    "periods_on_screen", "Pulse periods on screen", 0.000001
                ),
                "trigger_timeout": number("trigger_timeout", "Trigger timeout", 0.000001),
                "pulse_settle": number("pulse_settle", "Pulse settle time", 0),
                "acquisition_delay": number("acquisition_delay", "Acquisition delay", 0),
                "condition_delay": number("condition_delay", "Condition delay", 0),
                "coupler_db": number("coupler_db", "Coupler attenuation", 0),
                "impedance": number("impedance", "System impedance", 0.000001),
                "transfer_timeout": number(
                    "transfer_timeout", "Transfer timeout", 1, integer=True
                ),
                "chunk_size": number("chunk_size", "Transfer chunk size", 1, integer=True),
            }

            text_values = {
                key: variables[key].get().strip()
                for key in ("scope_address", "gen_ip", "rf_channel", "folder_name")
            }
            if any(not value for value in text_values.values()):
                raise ValueError(
                    "Scope address, DUT hostname, RF channel, and folder name cannot be blank."
                )

            SCOPE_ADDRESS = text_values["scope_address"]
            GEN_IP = text_values["gen_ip"]
            SELECTED_CAPTURE_MODE = mode
            NUM_ACQUISITIONS = values["num_acq"]
            RF_CHANNEL = text_values["rf_channel"].upper()
            VERT_SCALE_RF = values["vertical_scale"]
            RECORD_LENGTH = values["record_length"]
            HORIZONTAL_DIVISIONS = values["horizontal_divisions"]
            PULSE_PERIODS_ON_SCREEN = values["periods_on_screen"]
            TRIGGER_TIMEOUT_SEC = values["trigger_timeout"]
            PULSE_SETTLE_TIME_SEC = values["pulse_settle"]
            ACQUISITION_DELAY_SEC = values["acquisition_delay"]
            CONDITION_DELAY_SEC = values["condition_delay"]
            RF_FREQUENCIES_KHZ = rf_values
            POWER_SETPOINTS = power_values
            HALO_MODE = values["halo_mode"]
            PULSE_FREQUENCIES_HZ = pulse_values
            PULSE_DUTY_CYCLES_PERCENT = duty_values
            COUPLER_DB = values["coupler_db"]
            SYSTEM_IMPEDANCE = values["impedance"]
            TEST_FOLDER_NAME = text_values["folder_name"]
            WAVEFORM_TRANSFER_TIMEOUT_MS = values["transfer_timeout"]
            WAVEFORM_TRANSFER_CHUNK_SIZE_BYTES = values["chunk_size"]

            SAVE_DIRECTORY = create_unique_test_output_folder(selected_parent, TEST_FOLDER_NAME, GEN_IP)
            print(f"\nSelected parent folder: {selected_parent}")
            print(f"Test data folder: {SAVE_DIRECTORY}")
            result["accepted"] = True
            root.destroy()
        except Exception as error:
            messagebox.showerror("Invalid collection settings", str(error), parent=root)

    for variable in variables.values():
        variable.trace_add("write", update_preview)
    capture_mode.trace_add("write", update_preview)
    update_preview()

    buttons = ttk.Frame(outer)
    buttons.pack(fill="x", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(
        side="right", padx=(8, 0)
    )
    ttk.Button(
        buttons,
        text="Start Collection",
        style="Start.TButton",
        command=apply_and_start,
    ).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    if not result["accepted"]:
        raise SystemExit("Collection cancelled before instrument connection.")


# ==========================================================
# SELECT LOCAL SAVE LOCATION
# ==========================================================

def select_save_directory():
    """Prompt for a parent folder and create the timestamped SLP test folder."""
    global SAVE_DIRECTORY
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    try:
        selected_directory = filedialog.askdirectory(
            title="Select parent folder for SLP waveform data",
            initialdir=(
                DEFAULT_OUTPUT_PARENT_FOLDER
                if Path(DEFAULT_OUTPUT_PARENT_FOLDER).is_dir()
                else str(Path.home())
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

# ==========================================================
# CONNECT TO OSCILLOSCOPE
# ==========================================================

def connect_scope():
    global scope_resource_manager, scope
    if not SCOPE_ADDRESS:
        raise RuntimeError("Oscilloscope address is not set. Select a bench profile in the Unified Collection GUI or set PARAMOUNT_OSCILLOSCOPE_ADDRESS.")

    print("\nConnecting to oscilloscope...")
    scope_resource_manager = resource_manager()
    scope = InstrumentFactory({"oscilloscope": SCOPE_ADDRESS}).create(
        "oscilloscope", resource_manager=scope_resource_manager, open_attempts=1)
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
    gen = InstrumentFactory({"generator": GEN_IP}).create("generator")

    gen.rfOFF()
    sleep(0.2)
    gen.setActiveControlMode(2)

    try:
        gen.setPulsingConfiguration(8, 0)
    except Exception as error:
        print(f"Startup warning while disabling pulse mode: {error}")

    print("RF generator connected.")

# ==========================================================
# CLOSE CONNECTIONS
# ==========================================================

def close_connections():
    global scope, scope_resource_manager, gen

    print("\nClosing instrument connections...")

    if gen is not None:
        try:
            gen.rfOFF()
            sleep(0.2)
        except Exception as error:
            print(f"Warning while turning RF off: {error}")

        try:
            gen.setPulsingConfiguration(8, 0)
        except Exception as error:
            print(f"Warning while disabling pulse mode: {error}")

        try:
            gen.close()
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

def calculate_actual_power_w(base_power_setpoint):
    return base_power_setpoint * HALO_MODE


def expected_coupler_vpk(actual_power_w):
    coupled_power_w = actual_power_w / (10 ** (COUPLER_DB / 10))
    coupled_vrms = np.sqrt(coupled_power_w * SYSTEM_IMPEDANCE)
    return coupled_vrms * np.sqrt(2)

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

    if duty_cycle_percent < 0 or duty_cycle_percent > 99:
        raise ValueError("Duty cycle must be between 0 and 99 percent.")

    pulse_period_sec = 1.0 / pulse_frequency_hz
    pulse_on_time_sec = pulse_period_sec * duty_cycle_percent / 100.0
    pulse_off_time_sec = pulse_period_sec - pulse_on_time_sec

    if pulse_on_time_sec < 5e-6:
        raise ValueError(
            f"Pulse on-time is {pulse_on_time_sec * 1e6:.3f} us. "
            "The minimum allowed on-time is 5 us."
        )

    if pulse_off_time_sec < 5e-6:
        raise ValueError(
            f"Pulse off-time is {pulse_off_time_sec * 1e6:.3f} us. "
            "The minimum allowed off-time is 5 us."
        )

    return pulse_period_sec, pulse_on_time_sec, pulse_off_time_sec

# ==========================================================
# SCOPE TIME BASE
# ==========================================================

def calculate_scope_timebase(pulse_frequency_hz):
    if pulse_frequency_hz <= 0:
        raise ValueError("Pulse frequency must be greater than zero.")

    pulse_period_sec = 1.0 / pulse_frequency_hz
    total_screen_time_sec = pulse_period_sec * PULSE_PERIODS_ON_SCREEN
    time_per_div_sec = total_screen_time_sec / HORIZONTAL_DIVISIONS

    return pulse_period_sec, total_screen_time_sec, time_per_div_sec

# ==========================================================
# CONFIGURE OSCILLOSCOPE
# ==========================================================

def configure_scope(edge, pulse_frequency_hz):
    edge = edge.upper()

    if edge not in ["RISE", "FALL"]:
        raise ValueError("Scope edge must be RISE or FALL.")

    pulse_period_sec, total_screen_time_sec, time_per_div_sec = (
        calculate_scope_timebase(pulse_frequency_hz)
    )

    print("\nConfiguring oscilloscope...")
    print(f"Trigger edge          = {edge}")
    print(f"Pulse frequency       = {pulse_frequency_hz} Hz")
    print(f"Pulse period          = {pulse_period_sec:.9e} s")
    print(f"Total screen time     = {total_screen_time_sec:.9e} s")
    print(f"Scope time per div    = {time_per_div_sec:.9e} s/div")

    scope.write("RECALL:SETUP DEFAULT")
    scope.write("MEASUrement:DELETEALL")
    sleep(1)

    scope.write("*CLS")
    scope.write("ACQ:STATE OFF")
    scope.write(f"SEL:{RF_CHANNEL} ON")
    scope.write(f"{RF_CHANNEL}:SCA {VERT_SCALE_RF}")
    scope.write(f"{RF_CHANNEL}:COUP DC")
    scope.write(f"HOR:SCALE {time_per_div_sec}")

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
    # Set Ch1 to 50 Ohm termination since it will be getting RF
    scope.write(f"{RF_CHANNEL}:TERMINATION 50")

    try:
        scope.write(f"DATA:STOP {RECORD_LENGTH}")
    except Exception as error:
        print(f"Warning while setting DATA:STOP: {error}")

    channel_state = scope.query(f"SEL:{RF_CHANNEL}?").strip()
    reported_timebase = scope.query("HOR:SCALE?").strip()

    print("Oscilloscope configuration complete.")
    print(f"{RF_CHANNEL} enabled state = {channel_state}")
    print(f"Reported time per div = {reported_timebase} s/div")

# ==========================================================
# ARM OSCILLOSCOPE
# ==========================================================

def arm_scope(edge, trigger_level):
    edge = edge.upper()

    if edge not in ["RISE", "FALL"]:
        raise ValueError("Trigger edge must be RISE or FALL.")

    # Explicitly stop and clear the preceding single-sequence acquisition so a
    # stale STOP/SAVE trigger state cannot be mistaken for a new capture.
    scope.write("ACQ:STATE OFF")
    scope.write("*CLS")
    scope.write(f"TRIG:A:EDGE:SOU {RF_CHANNEL}")
    scope.write(f"TRIG:A:EDGE:SLO {edge}")
    scope.write(f"TRIG:A:LEV:{RF_CHANNEL} {trigger_level}")
    scope.write("TRIG:A:MOD NORM")
    scope.write("ACQ:STOPA SEQ")
    scope.write("ACQ:STATE ON")

    # Allow the instrument to process the arm command before status polling.
    sleep(0.10)
    acquisition_state = scope.query("ACQ:STATE?").strip().upper()
    trigger_state = scope.query("TRIG:STATE?").strip().upper()

    print(f"Scope armed for {edge} edge at {trigger_level:.6f} V")
    print(
        f"Initial scope status: ACQ:STATE={acquisition_state}, "
        f"TRIG:STATE={trigger_state}"
    )

# ==========================================================
# CONFIGURE GENERATOR PULSE MODE
# ==========================================================

def configure_generator_pulse_mode(
    rf_frequency_khz,
    base_power_setpoint,
    pulse_frequency_hz,
    duty_cycle_percent
):
    pulse_period_sec, pulse_on_time_sec, pulse_off_time_sec = (
        validate_pulse_settings(pulse_frequency_hz, duty_cycle_percent)
    )

    actual_power_w = calculate_actual_power_w(base_power_setpoint)
    generator_setpoint_tenths_w = int(actual_power_w * 10)

    print("\nConfiguring RF generator...")
    print(f"RF frequency          = {rf_frequency_khz} kHz")
    print(f"Actual power          = {actual_power_w} W")
    print(f"Generator setpoint    = {generator_setpoint_tenths_w} tenths of a watt")
    print(f"Pulse frequency       = {pulse_frequency_hz} Hz")
    print(f"Duty cycle            = {duty_cycle_percent} percent")
    print(f"Pulse period          = {pulse_period_sec * 1e6:.3f} us")
    print(f"Pulse on-time         = {pulse_on_time_sec * 1e6:.3f} us")
    print(f"Pulse off-time        = {pulse_off_time_sec * 1e6:.3f} us")

    gen.rfOFF()
    sleep(0.2)
    gen.setActiveControlMode(2)
    gen.setFixedFrequency(int(rf_frequency_khz))
    gen.setRegulationMode(6)
    gen.setFrequencyMode(0)
    gen.setPowerSetpoint(generator_setpoint_tenths_w)

    # Command 8, data 1 enables generator pulsing.
    gen.setPulsingConfiguration(8, 1)

    # Command 1, data 1 selects mode 1 for internally generated pulsing.
    gen.setPulsingConfiguration(1, 1)

    gen.setPulsingFrequency(int(pulse_frequency_hz))
    gen.setPulsingDutyCycle(int(duty_cycle_percent))

    reported_system_mode = gen.reportPulsingConfiguration(8)
    reported_pulsing_mode = gen.reportPulsingConfiguration(1)
    reported_pulse_frequency = gen.reportPulsingFrequency()
    reported_duty_cycle = gen.reportPulsingDutyCycle()

    print("\nGenerator pulse configuration readback:")
    print(f"Pulsing system mode   = {reported_system_mode}")
    print(f"Pulsing mode          = {reported_pulsing_mode}")
    print(f"Pulse frequency       = {reported_pulse_frequency} Hz")
    print(f"Duty cycle            = {reported_duty_cycle} percent")

    if reported_system_mode != 1:
        raise RuntimeError("The generator did not report that pulsing is enabled.")

    if reported_pulsing_mode != 1:
        raise RuntimeError("The generator did not report pulsing mode 1.")

    if reported_pulse_frequency != pulse_frequency_hz:
        raise RuntimeError(
            f"Pulse frequency mismatch. Requested {pulse_frequency_hz} Hz, "
            f"reported {reported_pulse_frequency} Hz."
        )

    if reported_duty_cycle != duty_cycle_percent:
        raise RuntimeError(
            f"Duty cycle mismatch. Requested {duty_cycle_percent} percent, "
            f"reported {reported_duty_cycle} percent."
        )

    print("\nTurning RF on...")
    gen.rfON()
    sleep(0.2)

    if not gen.isRFon():
        raise RuntimeError(
            "The RF ON command was sent, but the generator does not report RF output on."
        )

    print("RF generator is on and pulsing.")
    print(f"Waiting {PULSE_SETTLE_TIME_SEC} seconds for the pulse train to settle...")
    sleep(PULSE_SETTLE_TIME_SEC)

# ==========================================================
# DISABLE GENERATOR PULSE MODE
# ==========================================================

def disable_generator_pulse_mode():
    if gen is None:
        return

    print("\nTurning RF off and disabling pulse mode...")

    try:
        gen.rfOFF()
        sleep(0.2)
        print("RF is off.")
    except Exception as error:
        print(f"Warning: RF could not be turned off: {error}")

    try:
        gen.setPulsingConfiguration(8, 0)
        print("Pulse mode disabled.")
    except Exception as error:
        print(f"Warning: pulse mode could not be disabled: {error}")

    try:
        reported_mode = gen.reportPulsingConfiguration(8)
        print(f"Reported pulsing system mode = {reported_mode}")
    except Exception as error:
        print(f"Warning: pulse mode could not be verified: {error}")

# ==========================================================
# WAIT FOR SCOPE TRIGGER
# ==========================================================

def wait_for_scope_trigger(timeout_sec):
    """Wait for a new single-sequence acquisition to finish.

    Completion is determined with ACQ:STATE?, not merely TRIG:STATE? reporting
    STOP or SAVE. STOP/SAVE can persist from an earlier acquisition and caused
    false-positive captures in the previous implementation.
    """
    start_time = time()
    last_acquisition_state = None
    last_trigger_state = None

    while time() - start_time < timeout_sec:
        acquisition_state = scope.query("ACQ:STATE?").strip().upper()
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

        # In single-sequence mode ACQ:STATE returns to zero only after the
        # newly armed acquisition has triggered and completed. The arm routine
        # clears prior status before issuing ACQ:STATE ON.
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

# ==========================================================
# SMART PULSE TRIGGER
# ==========================================================

def smart_pulse_trigger(edge, actual_power_w, timeout_sec=TRIGGER_TIMEOUT_SEC):
    expected_vpk = expected_coupler_vpk(actual_power_w)

    trigger_levels = [
        max(expected_vpk * 0.50, 0.010),
        max(expected_vpk * 0.35, 0.005),
        max(expected_vpk * 0.20, 0.002),
        max(expected_vpk * 0.10, 0.001),
        0.001
    ]

    print(f"\nEstimated coupler peak voltage = {expected_vpk:.6f} V")

    for trigger_level in trigger_levels:
        print(f"Trying {edge} trigger at {trigger_level:.6f} V")
        arm_scope(edge, trigger_level)

        if wait_for_scope_trigger(timeout_sec):
            print(f"Triggered on {edge} pulse edge at {trigger_level:.6f} V")
            return True

        print(f"No trigger at {trigger_level:.6f} V")

    return False

# ==========================================================
# SAVE WAVEFORM
# ==========================================================

def save_wave(filename):
    """Download the completed waveform over Ethernet and save the CSV locally."""
    acquisition_state = scope.query("ACQ:STATE?").strip().upper()
    if acquisition_state not in ("0", "OFF"):
        raise RuntimeError(
            "Refusing to transfer because the single-sequence acquisition is "
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

# ==========================================================
# BUILD OUTPUT FILENAME
# ==========================================================

def build_filename(
    edge,
    rf_frequency_khz,
    actual_power_w,
    pulse_frequency_hz,
    duty_cycle_percent,
    acquisition_number
):
    if SAVE_DIRECTORY is None:
        raise RuntimeError("The waveform save location has not been selected.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    filename = (
        f"RF_Freq_{edge}{rf_frequency_khz}_"
        f"Power{actual_power_w}W_"
        f"PulseFreq{pulse_frequency_hz}Hz_"
        f"Duty{duty_cycle_percent}pct_"
        f"Acq{acquisition_number}_"
        f"{timestamp}.csv"
    )

    return SAVE_DIRECTORY / filename

# ==========================================================
# CAPTURE ONE TEST CONDITION
# ==========================================================

def capture_test_condition(
    edge,
    rf_frequency_khz,
    base_power_setpoint,
    pulse_frequency_hz,
    duty_cycle_percent
):
    actual_power_w = calculate_actual_power_w(base_power_setpoint)

    configure_scope(edge, pulse_frequency_hz)

    configure_generator_pulse_mode(
        rf_frequency_khz,
        base_power_setpoint,
        pulse_frequency_hz,
        duty_cycle_percent
    )

    successful_acquisitions = 0
    failed_acquisitions = 0

    for acquisition_index in range(1, NUM_ACQUISITIONS + 1):
        print("\n==================================================")
        print(f"Edge                 = {edge}")
        print(f"RF frequency         = {rf_frequency_khz} kHz")
        print(f"Actual power         = {actual_power_w} W")
        print(f"Pulse frequency      = {pulse_frequency_hz} Hz")
        print(f"Duty cycle           = {duty_cycle_percent} percent")
        print(f"Acquisition          = {acquisition_index}/{NUM_ACQUISITIONS}")
        print("==================================================")

        triggered = smart_pulse_trigger(edge, actual_power_w)

        if not triggered:
            failed_acquisitions += 1
            print("Failed to trigger for this acquisition.")
            continue

        filename = build_filename(
            edge,
            rf_frequency_khz,
            actual_power_w,
            pulse_frequency_hz,
            duty_cycle_percent,
            acquisition_index
        )

        save_wave(filename)
        successful_acquisitions += 1
        sleep(ACQUISITION_DELAY_SEC)

    print("\nTest condition complete.")
    print(f"Successful acquisitions = {successful_acquisitions}")
    print(f"Failed acquisitions     = {failed_acquisitions}")

# ==========================================================
# CAPTURE ALL CONDITIONS FOR ONE EDGE
# ==========================================================

def capture_pulse_edges(edge):
    edge = edge.upper()

    if edge not in ["RISE", "FALL"]:
        raise ValueError("Capture edge must be RISE or FALL.")

    print(f"\nStarting pulse-mode {edge} captures.")

    for pulse_frequency_hz in PULSE_FREQUENCIES_HZ:
        for duty_cycle_percent in PULSE_DUTY_CYCLES_PERCENT:
            for rf_frequency_khz in RF_FREQUENCIES_KHZ:
                for base_power_setpoint in POWER_SETPOINTS:
                    actual_power_w = calculate_actual_power_w(base_power_setpoint)

                    try:
                        capture_test_condition(
                            edge,
                            rf_frequency_khz,
                            base_power_setpoint,
                            pulse_frequency_hz,
                            duty_cycle_percent
                        )

                    except Exception as error:
                        print("\nCapture condition failed.")
                        print(f"Edge = {edge}")
                        print(f"RF frequency = {rf_frequency_khz} kHz")
                        print(f"Power = {actual_power_w} W")
                        print(f"Pulse frequency = {pulse_frequency_hz} Hz")
                        print(f"Duty cycle = {duty_cycle_percent} percent")
                        print(f"Error = {error}")

                    finally:
                        disable_generator_pulse_mode()
                        sleep(CONDITION_DELAY_SEC)

    print(f"\nPulse-mode {edge} captures complete.")

# ==========================================================
# CAPTURE BOTH EDGES
# ==========================================================

def capture_rise_and_fall_edges():
    capture_pulse_edges("RISE")
    capture_pulse_edges("FALL")

# ==========================================================
# MAIN MENU
# ==========================================================

def main():
    validate_unified_launcher_request()
    validate_gui_global_definitions()
    configure_run_with_gui()

    # Select and create the output folder before opening instrument connections.
    connect_scope()
    connect_generator()

    if SELECTED_CAPTURE_MODE == "RISE":
        print("\nSelected pulse rising-edge capture.")
        capture_pulse_edges("RISE")
    elif SELECTED_CAPTURE_MODE == "FALL":
        print("\nSelected pulse falling-edge capture.")
        capture_pulse_edges("FALL")
    elif SELECTED_CAPTURE_MODE == "BOTH":
        print("\nSelected pulse rising and falling-edge capture.")
        capture_rise_and_fall_edges()
    else:
        raise ValueError(
            f"Unsupported pulse capture mode: {SELECTED_CAPTURE_MODE}"
        )

    print("\nPulse-mode capture complete.")

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
