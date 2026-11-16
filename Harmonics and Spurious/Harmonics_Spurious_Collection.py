import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default
from suite_common.inputs import format_number_list, parse_number_list
from suite_common.output_folders import create_unique_test_output_folder

from time import sleep, strftime, localtime
from datetime import date, datetime
import AE_Commands_PMNT_HF_1XX
# EverestBusCommands import everest
import os
import csv
import json
import shutil
import numpy as np
from SpectrumAnalyzer.spectrum_analyzer import *
from MISC.helperFunctions import *
import matplotlib
from matplotlib import pyplot as plt
from scipy.signal import find_peaks
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

matplotlib.use("TkAgg")

"""Harmonics and Spurious test for Paramount generators.

The GUI configures the existing frequency/setpoint matrix, limits, spectrum
analyzer, optional coupler correction, and output folder before instruments are
opened. The original plotting and test engines remain below unchanged.
"""

# ==========================================================
# USER-EDITABLE DEFAULTS
# ==========================================================
HALO_Mode = 10
GENERATOR_ADDRESS = bench_default("generator")
SPECTRUM_ANALYZER_ADDRESS = bench_default("spectrum_analyzer")

harmonics_test = True
spurious_test = False
cst_test = False
coupler_Map = False
coupler_path = ""

COUPLER_SPEC = -60
HARM_LP_SPEC = -50
SPUR_LP_SPEC = -50
HP_SPEC = -50
SIZE = 468

frequencies = [12882000, 13560000, 14238000]
setpoints = [50, 100, 500, 1000, 2000, 2500, 3000]
R = 50
X = 0
timeWaitS = 1
NUMBER_OF_CYCLES = 1

TEST_FOLDER_NAME = "Harmonics and Spurious"
STANDALONE_DEFAULT_BASE_PATH = str(Path.home())
basePath = (
    os.environ.get('PARAMOUNT_COLLECTION_PARENT_FOLDER', STANDALONE_DEFAULT_BASE_PATH).strip()
    or STANDALONE_DEFAULT_BASE_PATH
)
outPath = None

# Deferred instrument/output objects.
generator = None
sa = None
gen_type = None
SN = None
now = None
today = None
CENTER = None
f = None
g = None
hc = None
sc = None
har_writer = None
spur_writer = None
HName = None
SName = None
COUPLER_DATA = []
settings_file = None
harmonics_file = None
spurious_file = None
harmonics_writer = None
spurious_writer = None
harmonics_average_file = None
spurious_average_file = None
harmonics_average_writer = None
spurious_average_writer = None
cycle_spectra = {"Harmonics": {}, "Spurious": {}}
copied_coupler_path = ""


# ==========================================================
# GUI AND OUTPUT HELPERS
# ==========================================================
def validate_gui_global_definitions():
    required = (
        "HALO_Mode", "GENERATOR_ADDRESS", "SPECTRUM_ANALYZER_ADDRESS",
        "harmonics_test", "spurious_test", "cst_test", "coupler_Map",
        "coupler_path", "COUPLER_SPEC", "HARM_LP_SPEC", "SPUR_LP_SPEC",
        "HP_SPEC", "SIZE", "frequencies", "setpoints", "R", "X",
        "timeWaitS", "NUMBER_OF_CYCLES", "TEST_FOLDER_NAME", "basePath",
    )
    missing = [name for name in required if name not in globals()]
    if missing:
        raise RuntimeError(
            "Required Harmonics and Spurious GUI settings are not defined: "
            + ", ".join(missing)
        )


def configure_harmonics_spurious_with_gui():
    global HALO_Mode, GENERATOR_ADDRESS, SPECTRUM_ANALYZER_ADDRESS
    global harmonics_test, spurious_test, cst_test, coupler_Map, coupler_path
    global COUPLER_SPEC, HARM_LP_SPEC, SPUR_LP_SPEC, HP_SPEC, SIZE
    global frequencies, setpoints, R, X, timeWaitS, NUMBER_OF_CYCLES, basePath

    root = tk.Tk()
    root.title("Paramount Harmonics and Spurious Setup")
    root.geometry("960x720")
    root.minsize(840, 620)

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
        text="Paramount Harmonics and Spurious",
        style="Header.TLabel",
    ).pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Configure the DUT, spectrum analyzer, selected spectral tests, frequency "
            "and power matrix, dBc limits, optional coupler correction, and output."
        ),
        wraplength=900,
    ).pack(anchor="w", pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    tests_tab = ttk.Frame(notebook, padding=12)
    instruments_tab = ttk.Frame(notebook, padding=12)
    matrix_tab = ttk.Frame(notebook, padding=12)
    limits_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(tests_tab, text="Tests")
    notebook.add(instruments_tab, text="Instruments")
    notebook.add(matrix_tab, text="Test Matrix")
    notebook.add(limits_tab, text="Limits and Coupler")
    notebook.add(output_tab, text="Output")

    variables = {}

    def add_entry(parent, row, label, key, default, help_text="", width=38):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=5
        )
        variable = tk.StringVar(value=str(default))
        variables[key] = variable
        ttk.Entry(parent, textvariable=variable, width=width).grid(
            row=row, column=1, sticky="ew", pady=5
        )
        if help_text:
            ttk.Label(
                parent, text=help_text, foreground="#555555", wraplength=360
            ).grid(row=row, column=2, sticky="w", padx=(10, 0), pady=5)

    run_harmonics = tk.BooleanVar(value=harmonics_test)
    run_spurious = tk.BooleanVar(value=spurious_test)
    run_cst = tk.BooleanVar(value=cst_test)
    ttk.Checkbutton(
        tests_tab, text="Run Harmonics test", variable=run_harmonics
    ).grid(row=0, column=0, columnspan=3, sticky="w", pady=5)
    ttk.Checkbutton(
        tests_tab, text="Run Spurious test", variable=run_spurious
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=5)
    ttk.Checkbutton(
        tests_tab,
        text="CST mode flag",
        variable=run_cst,
    ).grid(row=2, column=0, columnspan=3, sticky="w", pady=5)
    ttk.Label(
        tests_tab,
        text=(
            "At least one spectral test must be selected. The original harmonics and "
            "spurious analyzer spans, resolution bandwidths, plotting, and pass/fail "
            "classification remain unchanged."
        ),
        foreground="#555555",
        wraplength=780,
    ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(14, 0))

    add_entry(
        instruments_tab, 0, "DUT IP / hostname", "generator_address",
        GENERATOR_ADDRESS, width=50
    )
    add_entry(
        instruments_tab, 1, "Spectrum analyzer IP / hostname", "sa_address",
        SPECTRUM_ANALYZER_ADDRESS, width=50
    )
    add_entry(instruments_tab, 2, "Spectrum trace point count", "size", SIZE)
    add_entry(instruments_tab, 3, "Setpoint settle time (s)", "settle", timeWaitS)

    add_entry(
        matrix_tab, 0, "Generator frequencies (Hz)", "frequencies",
        format_number_list(frequencies), "Comma-separated whole frequencies."
    )
    add_entry(
        matrix_tab, 1, "Power setpoints (W)", "setpoints",
        format_number_list(setpoints), "Comma-separated whole setpoints."
    )
    add_entry(matrix_tab, 2, "HALO mode multiplier", "halo", HALO_Mode)
    add_entry(matrix_tab, 3, "Load resistance R (ohms)", "resistance", R)
    add_entry(matrix_tab, 4, "Load reactance X (ohms)", "reactance", X)
    add_entry(matrix_tab, 5, "Number of collection cycles", "cycles", NUMBER_OF_CYCLES,
              "Each cycle repeats the complete selected test matrix.")
    planned = tk.StringVar()
    ttk.Label(matrix_tab, textvariable=planned, style="Section.TLabel").grid(
        row=6, column=0, columnspan=3, sticky="w", pady=(14, 0)
    )

    add_entry(limits_tab, 0, "Low-power harmonics limit (dBc)", "harm_lp", HARM_LP_SPEC)
    add_entry(limits_tab, 1, "Low-power spurious limit (dBc)", "spur_lp", SPUR_LP_SPEC)
    add_entry(limits_tab, 2, "High-power limit (dBc)", "hp_spec", HP_SPEC)
    add_entry(limits_tab, 3, "Coupler reference/spec (dB)", "coupler_spec", COUPLER_SPEC)
    use_coupler = tk.BooleanVar(value=coupler_Map)
    ttk.Checkbutton(
        limits_tab,
        text="Apply coupler correction map",
        variable=use_coupler,
    ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 5))
    coupler_file = tk.StringVar(value=coupler_path)
    ttk.Label(limits_tab, text="Coupler CSV").grid(
        row=5, column=0, sticky="w", padx=(0, 10), pady=5
    )
    ttk.Entry(limits_tab, textvariable=coupler_file, width=58).grid(
        row=5, column=1, sticky="ew", pady=5
    )

    def browse_coupler():
        selected = filedialog.askopenfilename(
            title="Select coupler correction CSV",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
            parent=root,
        )
        if selected:
            coupler_file.set(selected)

    ttk.Button(limits_tab, text="Browse...", command=browse_coupler).grid(
        row=5, column=2, padx=(10, 0), pady=5
    )

    output_parent = tk.StringVar(value=basePath)
    ttk.Label(output_tab, text="Parent output folder").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=5
    )
    ttk.Entry(output_tab, textvariable=output_parent, width=60).grid(
        row=0, column=1, sticky="ew", pady=5
    )
    output_tab.columnconfigure(1, weight=1)

    def browse_output():
        initial = output_parent.get().strip()
        if not os.path.isdir(initial):
            initial = str(Path.home())
        selected = filedialog.askdirectory(
            title="Select parent folder for Harmonics and Spurious data",
            initialdir=initial,
            parent=root,
            mustexist=True,
        )
        if selected:
            output_parent.set(selected)

    ttk.Button(output_tab, text="Browse...", command=browse_output).grid(
        row=0, column=2, padx=(10, 0), pady=5
    )
    ttk.Label(
        output_tab,
        text=(
            "A run folder will be created as: YYYY-MM-DD_HH-MM-SS - Harmonics and "
            "Spurious - Generator Name. Exact same-second collisions use Run 2, Run 3, etc."
        ),
        foreground="#555555",
        wraplength=780,
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))

    result = {"accepted": False}

    def number(key, label, minimum=None, integer=False):
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
        return value

    def update_preview(*_):
        try:
            frequency_count = len(parse_number_list(
                variables["frequencies"].get(), "Frequencies"
            ))
            setpoint_count = len(parse_number_list(
                variables["setpoints"].get(), "Setpoints"
            ))
            test_count = int(run_harmonics.get()) + int(run_spurious.get())
            cycle_count = int(float(variables["cycles"].get().strip()))
            planned.set(
                f"Planned analyzer captures: {frequency_count * setpoint_count * test_count * cycle_count:,} "
                f"({cycle_count} cycle{'s' if cycle_count != 1 else ''})"
            )
        except Exception:
            planned.set("Planned analyzer captures: check entered values")

    def apply_and_start():
        nonlocal result
        global HALO_Mode, GENERATOR_ADDRESS, SPECTRUM_ANALYZER_ADDRESS
        global harmonics_test, spurious_test, cst_test, coupler_Map, coupler_path
        global COUPLER_SPEC, HARM_LP_SPEC, SPUR_LP_SPEC, HP_SPEC, SIZE
        global frequencies, setpoints, R, X, timeWaitS, NUMBER_OF_CYCLES, basePath
        try:
            if not run_harmonics.get() and not run_spurious.get():
                raise ValueError("Select Harmonics, Spurious, or both tests.")
            generator_address = variables["generator_address"].get().strip()
            analyzer_address = variables["sa_address"].get().strip()
            if not generator_address or not analyzer_address:
                raise ValueError("DUT and spectrum analyzer addresses cannot be blank.")
            frequency_values = parse_number_list(
                variables["frequencies"].get(), "Frequencies", integer_only=True
            )
            setpoint_values = parse_number_list(
                variables["setpoints"].get(), "Setpoints", integer_only=True
            )
            if len(frequency_values) < 2:
                raise ValueError(
                    "At least two frequencies are required because the original analyzer "
                    "configuration uses frequencies[1]."
                )
            if any(value <= 0 for value in frequency_values):
                raise ValueError("Frequencies must be greater than zero.")
            if any(value < 0 for value in setpoint_values):
                raise ValueError("Setpoints cannot be negative.")
            parent = Path(output_parent.get().strip()).resolve()
            if not parent.exists() or not parent.is_dir():
                raise ValueError("Select an existing parent output folder.")
            selected_coupler = coupler_file.get().strip()
            if use_coupler.get() and not os.path.isfile(selected_coupler):
                raise ValueError("Select an existing coupler correction CSV.")

            GENERATOR_ADDRESS = generator_address
            SPECTRUM_ANALYZER_ADDRESS = analyzer_address
            harmonics_test = bool(run_harmonics.get())
            spurious_test = bool(run_spurious.get())
            cst_test = bool(run_cst.get())
            coupler_Map = bool(use_coupler.get())
            coupler_path = selected_coupler
            frequencies = frequency_values
            setpoints = setpoint_values
            selected_halo_mode = number("halo", "HALO mode", 0.000001)
            if not float(selected_halo_mode).is_integer():
                raise ValueError("HALO mode multiplier must be a whole number.")
            HALO_Mode = int(selected_halo_mode)
            R = number("resistance", "Load resistance")
            X = number("reactance", "Load reactance")
            SIZE = number("size", "Trace point count", 8, integer=True)
            timeWaitS = number("settle", "Setpoint settle time", 0)
            NUMBER_OF_CYCLES = number("cycles", "Number of collection cycles", 1, integer=True)
            HARM_LP_SPEC = number("harm_lp", "Low-power harmonics limit")
            SPUR_LP_SPEC = number("spur_lp", "Low-power spurious limit")
            HP_SPEC = number("hp_spec", "High-power limit")
            COUPLER_SPEC = number("coupler_spec", "Coupler reference/spec")
            basePath = str(parent)

            result["accepted"] = True
            root.destroy()
        except Exception as error:
            messagebox.showerror(
                "Invalid Harmonics and Spurious settings", str(error), parent=root
            )

    for variable in variables.values():
        variable.trace_add("write", update_preview)
    run_harmonics.trace_add("write", update_preview)
    run_spurious.trace_add("write", update_preview)
    update_preview()

    buttons = ttk.Frame(outer)
    buttons.pack(fill="x", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(
        side="right", padx=(8, 0)
    )
    ttk.Button(
        buttons, text="Start Test", style="Start.TButton", command=apply_and_start
    ).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    if not result["accepted"]:
        raise SystemExit("Harmonics and Spurious cancelled before instrument connection.")


def initialize_instruments_and_output():
    """Connect instruments and create explicit analysis-ready collection files."""
    global generator, sa, gen_type, SN, now, today, CENTER, outPath
    global settings_file, harmonics_file, spurious_file
    global harmonics_writer, spurious_writer
    global harmonics_average_file, spurious_average_file
    global harmonics_average_writer, spurious_average_writer
    global cycle_spectra, COUPLER_DATA, copied_coupler_path

    generator = AE_Commands_PMNT_HF_1XX.generator(GENERATOR_ADDRESS)
    sa = SpectrumAnalyzer(SPECTRUM_ANALYZER_ADDRESS)
    gen_type = generator.reportPowerSupplyType()
    sa.singleSweep()
    SN = generator.reportSerialNumber()
    now = strftime('%Y_%m_%d_%H_%M_%S', localtime())
    today = date(
        int(strftime('%Y', localtime())),
        int(strftime('%m', localtime())),
        int(strftime('%d', localtime())),
    ).isoformat()
    CENTER = round((frequencies[1] * 1e-6), 3)

    outPath = str(
        create_unique_test_output_folder(basePath, TEST_FOLDER_NAME, GENERATOR_ADDRESS)
    )
    print("Saving data to:", outPath)

    settings_path = os.path.join(outPath, "Harmonics_Spurious_Run_Settings.csv")
    harmonics_path = os.path.join(outPath, "Harmonics_Spectrum_Data.csv")
    spurious_path = os.path.join(outPath, "Spurious_Spectrum_Data.csv")
    harmonics_average_path = os.path.join(outPath, "Harmonics_Cycle_Averages.csv")
    spurious_average_path = os.path.join(outPath, "Spurious_Cycle_Averages.csv")

    settings_file = open(settings_path, "w", newline="")
    harmonics_file = open(harmonics_path, "w", newline="")
    spurious_file = open(spurious_path, "w", newline="")
    harmonics_average_file = open(harmonics_average_path, "w", newline="")
    spurious_average_file = open(spurious_average_path, "w", newline="")
    harmonics_writer = csv.writer(harmonics_file, lineterminator="\n")
    spurious_writer = csv.writer(spurious_file, lineterminator="\n")
    harmonics_average_writer = csv.writer(harmonics_average_file, lineterminator="\n")
    spurious_average_writer = csv.writer(spurious_average_file, lineterminator="\n")
    cycle_spectra = {"Harmonics": {}, "Spurious": {}}

    spectrum_header = [
        "measurement_timestamp",
        "acquisition_id",
        "cycle_number",
        "total_cycles",
        "test_type",
        "generator_hostname",
        "generator_serial",
        "generator_type",
        "spectrum_analyzer_hostname",
        "rf_frequency_Hz",
        "rf_frequency_kHz",
        "power_setpoint_W",
        "generator_command_setpoint",
        "halo_mode_multiplier",
        "load_resistance_ohm",
        "load_reactance_ohm",
        "settle_time_s",
        "analyzer_start_frequency_Hz",
        "analyzer_stop_frequency_Hz",
        "analyzer_center_frequency_Hz",
        "analyzer_span_Hz",
        "analyzer_resolution_bandwidth_Hz",
        "analyzer_trace_point_count_configured",
        "analyzer_trace_point_count_saved",
        "analyzer_high_sensitivity_enabled",
        "analyzer_input_attenuation_dB",
        "analyzer_reference_level_dBm",
        "analyzer_offset_level_dB",
        "low_power_boundary_W",
        "harmonics_low_power_spec_dBc",
        "spurious_low_power_spec_dBc",
        "high_power_spec_dBc",
        "active_spec_dBc",
        "coupler_correction_enabled",
        "coupler_reference_spec_dB",
        "coupler_map_original_path",
        "coupler_map_copied_path",
        "spectrum_point_index",
        "spectrum_frequency_Hz",
        "spectrum_amplitude_dBm",
        "driver_metadata_row",
        "collection_status",
    ]
    harmonics_writer.writerow(spectrum_header)
    spurious_writer.writerow(spectrum_header)
    average_header = [
        "test_type", "rf_frequency_Hz", "rf_frequency_kHz", "power_setpoint_W",
        "spectrum_point_index", "spectrum_frequency_Hz", "cycles_requested",
        "cycles_in_average", "average_amplitude_dBm", "standard_deviation_dB",
        "minimum_amplitude_dBm", "maximum_amplitude_dBm", "collection_status",
    ]
    harmonics_average_writer.writerow(average_header)
    spurious_average_writer.writerow(average_header)

    COUPLER_DATA = []
    copied_coupler_path = ""
    if coupler_Map:
        copied_coupler_path = os.path.join(outPath, "Coupler_Correction_Map_Used.csv")
        shutil.copy2(coupler_path, copied_coupler_path)
        with open(coupler_path, newline="") as csvfile:
            COUPLER_DATA.extend(csv.reader(csvfile))

    settings_writer = csv.writer(settings_file, lineterminator="\n")
    settings_writer.writerow(["setting", "value"])
    settings = [
        ("collection_start_timestamp", datetime.now().isoformat(timespec="seconds")),
        ("test_folder", outPath),
        ("generator_hostname", GENERATOR_ADDRESS),
        ("generator_serial", SN),
        ("generator_type", gen_type),
        ("spectrum_analyzer_hostname", SPECTRUM_ANALYZER_ADDRESS),
        ("harmonics_enabled", harmonics_test),
        ("spurious_enabled", spurious_test),
        ("cst_test_flag", cst_test),
        ("halo_mode_multiplier", HALO_Mode),
        ("frequencies_Hz", json.dumps(frequencies)),
        ("power_setpoints_W", json.dumps(setpoints)),
        ("load_resistance_ohm", R),
        ("load_reactance_ohm", X),
        ("settle_time_s", timeWaitS),
        ("number_of_cycles", NUMBER_OF_CYCLES),
        ("spectrum_trace_point_count", SIZE),
        ("harmonics_low_power_spec_dBc", HARM_LP_SPEC),
        ("spurious_low_power_spec_dBc", SPUR_LP_SPEC),
        ("high_power_spec_dBc", HP_SPEC),
        ("low_power_boundary_W", 50),
        ("coupler_correction_enabled", coupler_Map),
        ("coupler_reference_spec_dB", COUPLER_SPEC),
        ("coupler_map_original_path", coupler_path),
        ("coupler_map_copied_path", copied_coupler_path),
    ]
    settings_writer.writerows(settings)
    for handle in (settings_file, harmonics_file, spurious_file, harmonics_average_file, spurious_average_file):
        handle.flush()
        os.fsync(handle.fileno())


validate_gui_global_definitions()
configure_harmonics_spurious_with_gui()
initialize_instruments_and_output()


########## COLLECTION HELPERS ##########
class _DriverRowCapture:
    """Capture the driver's metadata row without relying on its private CSV schema."""
    def __init__(self):
        self.rows = []

    def writerow(self, row):
        self.rows.append(list(row))


def _active_spec(test_type, setpoint_w):
    if setpoint_w <= 50:
        return HARM_LP_SPEC if test_type == "Harmonics" else SPUR_LP_SPEC
    return HP_SPEC


def _write_spectrum_acquisition(
    writer,
    handle,
    test_type,
    cycle_number,
    spectrum_data,
    driver_row,
    rf_frequency_hz,
    setpoint_w,
    generator_command_setpoint,
    start_hz,
    stop_hz,
    rbw_hz,
    high_sensitivity,
    input_attenuation_db,
    reference_level_dbm,
    offset_level_db,
):
    """Write one long-form spectrum acquisition with explicit metadata per point."""
    timestamp = datetime.now().isoformat(timespec="milliseconds")
    acquisition_id = (
        f"{test_type}_Cycle{cycle_number}_{int(rf_frequency_hz)}Hz_{int(setpoint_w)}W_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    )
    usable_count = min(max(int(SIZE) - 7, 0), len(spectrum_data))
    if usable_count <= 0:
        raise ValueError("Spectrum analyzer returned no usable trace points.")
    frequencies_hz = np.linspace(float(start_hz), float(stop_hz), usable_count)
    center_hz = (float(start_hz) + float(stop_hz)) / 2.0
    span_hz = float(stop_hz) - float(start_hz)
    metadata = json.dumps(driver_row, default=str)
    active_spec = _active_spec(test_type, setpoint_w)
    copied_map = copied_coupler_path if coupler_Map else ""

    for index in range(usable_count):
        writer.writerow([
            timestamp,
            acquisition_id,
            cycle_number,
            NUMBER_OF_CYCLES,
            test_type,
            GENERATOR_ADDRESS,
            SN,
            gen_type,
            SPECTRUM_ANALYZER_ADDRESS,
            rf_frequency_hz,
            rf_frequency_hz / 1000.0,
            setpoint_w,
            generator_command_setpoint,
            HALO_Mode,
            R,
            X,
            timeWaitS,
            start_hz,
            stop_hz,
            center_hz,
            span_hz,
            rbw_hz,
            SIZE,
            usable_count,
            high_sensitivity,
            input_attenuation_db,
            reference_level_dbm,
            offset_level_db,
            50,
            HARM_LP_SPEC,
            SPUR_LP_SPEC,
            HP_SPEC,
            active_spec,
            coupler_Map,
            COUPLER_SPEC,
            coupler_path,
            copied_map,
            index,
            frequencies_hz[index],
            float(spectrum_data[index]),
            metadata,
            "Captured",
        ])
    handle.flush()
    os.fsync(handle.fileno())
    print(
        f"Saved {test_type} spectrum (cycle {cycle_number}/{NUMBER_OF_CYCLES}): {rf_frequency_hz / 1e6:g} MHz, "
        f"{setpoint_w:g} W, {usable_count} points"
    )


def _acquire_spectrum(test_type, cycle_number, writer, handle, rf_frequency_hz, setpoint_w,
                      generator_command_setpoint, start_hz, stop_hz, rbw_hz, high_sensitivity,
                      input_attenuation_db=None, reference_level_dbm=None,
                      offset_level_db=None):
    capture = _DriverRowCapture()
    spectrum_data, returned_row = sa.getSpectrumAndWriteToFile(
        capture, R, X, setpoint_w, rf_frequency_hz
    )
    driver_row = returned_row
    if driver_row is None or (hasattr(driver_row, "__len__") and len(driver_row) == 0):
        driver_row = capture.rows[-1] if capture.rows else []
    _write_spectrum_acquisition(
        writer, handle, test_type, cycle_number, list(spectrum_data), driver_row,
        rf_frequency_hz, setpoint_w, generator_command_setpoint,
        start_hz, stop_hz, rbw_hz,
        high_sensitivity, input_attenuation_db, reference_level_dbm,
        offset_level_db,
    )
    usable_count = min(max(int(SIZE) - 7, 0), len(spectrum_data))
    key = (int(rf_frequency_hz), float(setpoint_w), float(start_hz), float(stop_hz))
    cycle_spectra[test_type].setdefault(key, []).append(
        np.asarray(list(spectrum_data)[:usable_count], dtype=float)
    )


def collect_harmonics(cycle_number):
    for freq in frequencies:
        start_hz = int(freq / 10)
        stop_hz = int(freq * 10)
        if 12_882_000 <= freq <= 14_238_000:
            rbw_hz = 1000
        elif 38_646_000 <= freq <= 42_714_000:
            rbw_hz = 3000
        elif 900_000 <= freq <= 1_100_000:
            rbw_hz = 1000
        elif 1_800_000 <= freq <= 2_200_000:
            rbw_hz = 1000
        else:
            rbw_hz = 3000
        sa.setFrequencySpan(start_hz, stop_hz)
        sa.setResBW(rbw_hz)
        generator.setFixedFrequency(int(freq / 1000))

        for setpoint_w in setpoints:
            high_sensitivity = setpoint_w <= 50
            if high_sensitivity:
                sa.enableHighSense()
                attenuation_db = reference_dbm = offset_db = None
            else:
                sa.disableHighSense()
                sa.setOffsetLevel(0)
                sa.setInputAttenuation(20)
                sa.setRefLevel(0)
                attenuation_db, reference_dbm, offset_db = 20, 0, 0
            generator_command_setpoint = int(setpoint_w * HALO_Mode)
            generator.setPowerSetpoint(generator_command_setpoint)
            generator.rfON()
            try:
                sleep(timeWaitS)
                _acquire_spectrum(
                    "Harmonics", cycle_number, harmonics_writer, harmonics_file, freq, setpoint_w,
                    generator_command_setpoint, start_hz, stop_hz, rbw_hz, high_sensitivity,
                    attenuation_db, reference_dbm, offset_db,
                )
            finally:
                generator.rfOFF()
                sleep(0.5)


def collect_spurious(cycle_number):
    for freq in frequencies:
        if 12_882_000 <= freq <= 14_238_000:
            start_hz, stop_hz, rbw_hz = int(freq - freq * 0.12), int(freq + freq * 0.12), 100
        elif 38_646_000 <= freq <= 42_714_000:
            start_hz, stop_hz, rbw_hz = int(freq - freq * 0.10), int(freq + freq * 0.10), 100
        elif 900_000 <= freq <= 1_100_000:
            start_hz, stop_hz, rbw_hz = int(freq - freq * 0.55), int(freq + freq * 0.55), 300
        elif 1_800_000 <= freq <= 2_200_000:
            start_hz, stop_hz, rbw_hz = int(freq - freq * 0.40), int(freq + freq * 0.40), 300
        else:
            start_hz, stop_hz, rbw_hz = int(freq - freq * 0.30), int(freq + freq * 0.30), 3000
        sa.setResBW(rbw_hz)
        sa.setFrequencySpan(start_hz, stop_hz)
        generator.setFixedFrequency(int(freq / 1000))

        for setpoint_w in setpoints:
            high_sensitivity = setpoint_w <= 50
            if high_sensitivity:
                sa.enableHighSense()
            else:
                sa.disableHighSense()
            generator_command_setpoint = int(setpoint_w * HALO_Mode)
            generator.setPowerSetpoint(generator_command_setpoint)
            generator.rfON()
            try:
                sleep(timeWaitS)
                _acquire_spectrum(
                    "Spurious", cycle_number, spurious_writer, spurious_file, freq, setpoint_w,
                    generator_command_setpoint, start_hz, stop_hz, rbw_hz, high_sensitivity,
                )
            finally:
                generator.rfOFF()
                sleep(0.5)


def _write_cycle_averages(test_type, writer, handle):
    """Write point-by-point averages across cycles for every matching test condition."""
    for key, traces in cycle_spectra[test_type].items():
        rf_frequency_hz, setpoint_w, start_hz, stop_hz = key
        if not traces:
            continue
        shortest = min(len(trace) for trace in traces)
        matrix = np.vstack([trace[:shortest] for trace in traces])
        frequencies_hz = np.linspace(start_hz, stop_hz, shortest)
        means = np.mean(matrix, axis=0)
        stds = np.std(matrix, axis=0, ddof=1) if len(traces) > 1 else np.zeros(shortest)
        minimums = np.min(matrix, axis=0)
        maximums = np.max(matrix, axis=0)
        for index in range(shortest):
            writer.writerow([
                test_type, rf_frequency_hz, rf_frequency_hz / 1000.0, setpoint_w,
                index, frequencies_hz[index], NUMBER_OF_CYCLES, len(traces),
                means[index], stds[index], minimums[index], maximums[index], "Averaged",
            ])
    handle.flush()
    os.fsync(handle.fileno())

########## COLLECTION PROGRAM ##########
try:
    generator.rfOFF()
    print(f"\nParamount SN:{SN} identified")
    for cycle_number in range(1, NUMBER_OF_CYCLES + 1):
        print(f"\nStarting collection cycle {cycle_number} of {NUMBER_OF_CYCLES}")
        if harmonics_test:
            print("Starting Harmonics spectrum collection")
            collect_harmonics(cycle_number)
        if spurious_test:
            print("Starting Spurious spectrum collection")
            collect_spurious(cycle_number)
    if harmonics_test:
        _write_cycle_averages("Harmonics", harmonics_average_writer, harmonics_average_file)
    if spurious_test:
        _write_cycle_averages("Spurious", spurious_average_writer, spurious_average_file)
    print("Spectrum collection and cycle averaging complete.")
finally:
    print("Shutting down safely...")
    try:
        generator.rfOFF()
    except Exception:
        pass
    for handle in (settings_file, harmonics_file, spurious_file, harmonics_average_file, spurious_average_file):
        if handle is not None and not handle.closed:
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
    print("Data saved to:", outPath)
