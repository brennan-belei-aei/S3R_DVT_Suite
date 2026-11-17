"""Analyze raw-RF DLP waveform CSV files from DLPDataCollectionProduction.py.

The collection script triggers on an RF-carrier zero crossing, so the DLP edge
is not fixed at time zero. This analyzer extracts a smoothed RMS/peak envelope,
locates the ordered State 0 -> State 1 transition named in each filename, and
measures the 10%-to-90% rise time or 90%-to-10% fall time between the measured
low and high DLP plateaus.
"""

import json
import os
import re
import shutil
import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.signals import centered_moving_average  # noqa: E402
from suite_common.powerpoint import powerpoint_export_requested, snapshot_png_files, changed_png_files
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading

import numpy as np
import pandas as pd

# This batch analyzer only writes plot files. Force Matplotlib to use its
# non-GUI renderer so it never creates hidden Tk windows or Tk image objects
# while hundreds of waveform figures are being created and destroyed.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

print("DLP rise/fall analysis script started.")

# ==========================================================
# USER SETTINGS
# ==========================================================
DATA_START_ROW_INDEX = 14
TIME_COLUMN_INDEX = 0
VOLTAGE_COLUMN_INDEX = 1
LOW_THRESHOLD_FRACTION = 0.10
HIGH_THRESHOLD_FRACTION = 0.90
USE_LINEAR_INTERPOLATION = True

# Envelope extraction. The centered RMS window spans this many RF cycles.
ENVELOPE_RMS_CYCLES = 8.0
MIN_ENVELOPE_WINDOW_SAMPLES = 5
MAX_ENVELOPE_WINDOW_SAMPLES = 5001

# Plateau estimation and edge validation.
PLATEAU_FRACTION_OF_SHORT_STATE = 0.30
MIN_PLATEAU_SAMPLES = 20
MIN_EDGE_SEPARATION_FRACTION = 0.02
EDGE_SEARCH_MARGIN_FRACTION = 0.02
MIN_STATE_SEPARATION_RATIO = 0.03

# A valid DLP pulse must contain the requested State 0 -> State 1 edge, the
# companion State 1 -> State 0 edge, and stable State 0 plateaus on both sides.
MIN_COMPLETE_PULSE_DURATION_RATIO = 0.50
MAX_COMPLETE_PULSE_DURATION_RATIO = 1.50
COMPLETE_PULSE_CENTER_WEIGHT = 0.25

# IQR outlier filtering is applied independently to each complete condition.
MIN_SAMPLES_FOR_IQR = 4
IQR_MULTIPLIER = 1.5

EXCEL_FILENAME = "DLP_Rise_Fall_Analysis.xlsx"
SUMMARY_CSV_FILENAME = "DLP_Rise_Fall_Summary.csv"
PLOT_FOLDER_NAME = "Plots"
PLOT_EVERY_WAVEFORM = True
PLOT_SUMMARY_TRENDS = True
OPEN_EXCEL_WHEN_COMPLETE = True

def export_powerpoint_package(images,summary_a,summary_b,rise_comp,fall_comp,comparison_excel):
    if not powerpoint_export_requested(): return None
    text=os.environ.get("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER","").strip()
    if not text: raise ValueError("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER is not set.")
    package=Path(text).expanduser().resolve();package.mkdir(parents=True,exist_ok=True);manifest=package/"manifest.json"
    if manifest.exists():manifest.unlink()
    ir=package/"images";tr=package/"tables"
    if ir.exists():shutil.rmtree(ir)
    if tr.exists():shutil.rmtree(tr)
    tr.mkdir(parents=True,exist_ok=True)
    if summary_a is not None and not summary_a.empty:summary_a.to_csv(tr/"dataset_a_dlp_summary.csv",index=False)
    if summary_b is not None and not summary_b.empty:summary_b.to_csv(tr/"dataset_b_dlp_summary.csv",index=False)
    if rise_comp is not None and not rise_comp.empty:rise_comp.to_csv(tr/"rise_comparison_b_minus_a.csv",index=False)
    if fall_comp is not None and not fall_comp.empty:fall_comp.to_csv(tr/"fall_comparison_b_minus_a.csv",index=False)
    slides=[];copied=[]
    for role,path in images:
        source=Path(path)
        if not source.is_file():continue
        dd=ir/role;dd.mkdir(parents=True,exist_ok=True);dest=dd/source.name
        if dest.exists():dest=dd/f"{source.parent.name}_{source.name}"
        shutil.copy2(source,dest);rel=dest.relative_to(package).as_posix();copied.append({"dataset_role":role.upper(),"source":str(source),"package_file":rel})
        slide={"slide_type":"image","title":source.name,"image_file":rel,"dataset_role":role.upper()}
        if role=="comparison":slide["difference_convention"]="B_MINUS_A"
        slides.append(slide)
    db={"label":configuration_b_name,"source_folder":comparison_folder} if ANALYSIS_MODE=="COMPARE" else None
    comp={"completed":ANALYSIS_MODE=="COMPARE","difference_convention":"B_MINUS_A","workbook":str(comparison_excel) if comparison_excel else ""}
    payload={"schema_version":int(os.environ.get("PARAMOUNT_POWERPOINT_SCHEMA_VERSION","1")),"analysis_id":os.environ.get("PARAMOUNT_POWERPOINT_ANALYSIS_ID","rise_fall_dlp").strip(),"display_name":"DLP Rise/Fall Timing","status":"completed","analysis_mode":ANALYSIS_MODE,"dataset_label":configuration_a_name if ANALYSIS_MODE=="SINGLE" else f"{configuration_b_name} vs {configuration_a_name}","source_folder":input_folder,"dataset_a":{"label":configuration_a_name,"source_folder":input_folder,"workbook":excel_output_file},"dataset_b":db,"comparison":comp,"highlights":[f"Dataset A: {configuration_a_name}",f"Generated {len(slides)} image slide(s) from this analysis run."],"metrics":{"Analysis mode":ANALYSIS_MODE,"Generated image slides":len(slides)},"generated_images":copied,"slides":slides}
    manifest.write_text(json.dumps(payload,indent=2),encoding="utf-8");return manifest

# ==========================================================
# UNIFIED DLP ANALYSIS CONFIGURATION
# ==========================================================
def configure_dlp_analysis_with_gui():
    """Collect all DLP analysis choices in one validated tabbed window."""
    # Tcl/Tk requires its interpreter to be created, used, and destroyed on
    # the process main thread. Fail immediately instead of crashing later
    # during Tk Variable/Image garbage collection.
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(
            "DLPDataAnalysis_GUI.py must run on the process main thread because "
            "it owns a Tkinter GUI. Launch this analysis as a separate Python "
            "subprocess, or invoke it directly from a terminal; do not execute "
            "the script inside threading.Thread or a worker executor."
        )
    global DATA_START_ROW_INDEX, TIME_COLUMN_INDEX, VOLTAGE_COLUMN_INDEX
    global LOW_THRESHOLD_FRACTION, HIGH_THRESHOLD_FRACTION, USE_LINEAR_INTERPOLATION
    global ENVELOPE_RMS_CYCLES, MIN_ENVELOPE_WINDOW_SAMPLES, MAX_ENVELOPE_WINDOW_SAMPLES
    global PLATEAU_FRACTION_OF_SHORT_STATE, MIN_PLATEAU_SAMPLES
    global MIN_EDGE_SEPARATION_FRACTION, EDGE_SEARCH_MARGIN_FRACTION
    global MIN_STATE_SEPARATION_RATIO, MIN_COMPLETE_PULSE_DURATION_RATIO
    global MAX_COMPLETE_PULSE_DURATION_RATIO, COMPLETE_PULSE_CENTER_WEIGHT
    global MIN_SAMPLES_FOR_IQR, IQR_MULTIPLIER
    global EXCEL_FILENAME, SUMMARY_CSV_FILENAME, PLOT_FOLDER_NAME
    global PLOT_EVERY_WAVEFORM, PLOT_SUMMARY_TRENDS, OPEN_EXCEL_WHEN_COMPLETE

    root = tk.Tk()
    root.title("DLP Statistical Timing Data Analysis")
    root.geometry("1040x780")
    root.minsize(920, 680)

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
    ttk.Label(outer, text="DLP Statistical Timing Data Analysis", style="Header.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Select a DLP collection folder and configure envelope extraction, complete-pulse "
            "validation, outlier filtering, plots, and output files before analysis begins."
        ),
        wraplength=980,
    ).pack(anchor="w", pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    input_tab = ttk.Frame(notebook, padding=12)
    edge_tab = ttk.Frame(notebook, padding=12)
    filtering_tab = ttk.Frame(notebook, padding=12)
    plots_tab = ttk.Frame(notebook, padding=12)
    comparison_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(input_tab, text="Input Data")
    notebook.add(edge_tab, text="Edge Processing")
    notebook.add(filtering_tab, text="Filtering")
    notebook.add(plots_tab, text="Plots")
    notebook.add(comparison_tab, text="Comparison")
    notebook.add(output_tab, text="Output")

    variables = {}
    launcher_mode = os.environ.get("PARAMOUNT_ANALYSIS_MODE", "INDIVIDUAL").strip().upper()
    initial_mode = launcher_mode if launcher_mode in {"SINGLE", "COMPARE"} else "SINGLE"
    mode_var = tk.StringVar(value=initial_mode)
    input_folder_var = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_FOLDER", "").strip())
    comparison_folder_var = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_FOLDER", "").strip())
    configuration_a_name_var = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_LABEL", "").strip() or "Configuration A")
    configuration_b_name_var = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_LABEL", "").strip() or "Configuration B")
    discovery_text = tk.StringVar(value="Select a DLP collection folder to preview waveform files.")

    def add_entry(parent, row, label, key, default, help_text="", width=24):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        variable = tk.StringVar(value=str(default))
        variables[key] = variable
        ttk.Entry(parent, textvariable=variable, width=width).grid(row=row, column=1, sticky="w", pady=4)
        if help_text:
            ttk.Label(parent, text=help_text, foreground="#555555", wraplength=430).grid(
                row=row, column=2, sticky="w", padx=(10, 0), pady=4)

    input_tab.columnconfigure(1, weight=1)
    ttk.Label(input_tab, text="Analysis mode", style="Section.TLabel").grid(
        row=0, column=0, sticky="w", pady=(0, 6)
    )
    mode_frame = ttk.Frame(input_tab)
    mode_frame.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 12))
    ttk.Radiobutton(mode_frame, text="Single dataset", variable=mode_var, value="SINGLE").pack(
        side="left", padx=(0, 20)
    )
    ttk.Radiobutton(
        mode_frame, text="Compare two hardware configurations", variable=mode_var, value="COMPARE"
    ).pack(side="left")

    ttk.Label(input_tab, text="Configuration A folder").grid(row=2, column=0, sticky="w", pady=5)
    ttk.Entry(input_tab, textvariable=input_folder_var, width=70).grid(
        row=2, column=1, sticky="ew", pady=5
    )
    ttk.Label(input_tab, text="Display name").grid(row=3, column=0, sticky="w", pady=5)
    ttk.Entry(input_tab, textvariable=configuration_a_name_var, width=35).grid(
        row=3, column=1, sticky="w", pady=5
    )
    ttk.Label(input_tab, text="Configuration B folder").grid(row=4, column=0, sticky="w", pady=5)
    comparison_folder_entry = ttk.Entry(input_tab, textvariable=comparison_folder_var, width=70)
    comparison_folder_entry.grid(row=4, column=1, sticky="ew", pady=5)
    ttk.Label(input_tab, text="Display name").grid(row=5, column=0, sticky="w", pady=5)
    configuration_b_name_entry = ttk.Entry(input_tab, textvariable=configuration_b_name_var, width=35)
    configuration_b_name_entry.grid(row=5, column=1, sticky="w", pady=5)

    def preview_folder(folder_text):
        folder = Path(folder_text.strip()) if folder_text.strip() else None
        if folder is None or not folder.is_dir():
            return "Folder not selected or does not exist."
        csv_files = sorted(folder.glob("*.csv"))
        rise = fall = recognized = invalid = 0
        frequencies = set(); pulse_frequencies = set(); duties = set(); transitions = set()
        for item in csv_files:
            try:
                info = parse_filename(item.name)
                recognized += 1
                rise += info["Type"] == "RISE"; fall += info["Type"] == "FALL"
                frequencies.add(info["RF Frequency (kHz)"])
                pulse_frequencies.add(info["Pulse Frequency (Hz)"])
                duties.add(info["Duty Cycle (%)"])
                transitions.add(info["Transition"])
            except Exception:
                invalid += 1
        return (
            f"CSV files: {len(csv_files)} | Recognized DLP files: {recognized} | "
            f"RISE: {rise} | FALL: {fall} | Unrecognized: {invalid}\\n"
            f"RF frequencies (kHz): {', '.join(f'{v:g}' for v in sorted(frequencies)) or 'None'}\\n"
            f"Pulse frequencies (Hz): {', '.join(f'{v:g}' for v in sorted(pulse_frequencies)) or 'None'}\\n"
            f"Duty cycles (%): {', '.join(f'{v:g}' for v in sorted(duties)) or 'None'}\\n"
            f"Ordered transitions: {', '.join(sorted(transitions)) or 'None'}"
        )

    def browse_folder(variable, name_variable, preview=False):
        current = variable.get().strip()
        selected = filedialog.askdirectory(
            title="Select folder containing DLP waveform CSV files", parent=root, mustexist=True,
            initialdir=current if current and Path(current).is_dir() else None
        )
        if selected:
            variable.set(selected)
            name_variable.set(os.path.basename(os.path.normpath(selected)) or name_variable.get())
            if preview:
                discovery_text.set(preview_folder(selected))

    ttk.Button(
        input_tab, text="Browse...",
        command=lambda: browse_folder(input_folder_var, configuration_a_name_var, True),
    ).grid(row=2, column=2, padx=(10, 0))
    comparison_folder_button = ttk.Button(
        input_tab, text="Browse...",
        command=lambda: browse_folder(comparison_folder_var, configuration_b_name_var, False),
    )
    comparison_folder_button.grid(row=4, column=2, padx=(10, 0))
    ttk.Label(
        input_tab, textvariable=discovery_text, foreground="#444444", wraplength=900, justify="left"
    ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(16, 0))

    add_entry(edge_tab, 0, "Data start Excel row", "data_start", DATA_START_ROW_INDEX + 1,
              "First row containing numeric waveform data. Stored internally as a zero-based index.")
    add_entry(edge_tab, 1, "Time column index", "time_column", TIME_COLUMN_INDEX)
    add_entry(edge_tab, 2, "Voltage column index", "voltage_column", VOLTAGE_COLUMN_INDEX)
    add_entry(edge_tab, 3, "Low threshold fraction", "low_threshold", LOW_THRESHOLD_FRACTION)
    add_entry(edge_tab, 4, "High threshold fraction", "high_threshold", HIGH_THRESHOLD_FRACTION)
    add_entry(edge_tab, 5, "Envelope RMS cycles", "rms_cycles", ENVELOPE_RMS_CYCLES)
    add_entry(edge_tab, 6, "Minimum envelope window samples", "min_window", MIN_ENVELOPE_WINDOW_SAMPLES)
    add_entry(edge_tab, 7, "Maximum envelope window samples", "max_window", MAX_ENVELOPE_WINDOW_SAMPLES)
    linear_interpolation = tk.BooleanVar(value=USE_LINEAR_INTERPOLATION)
    ttk.Checkbutton(edge_tab, text="Use linear threshold-crossing interpolation", variable=linear_interpolation).grid(
        row=8, column=0, columnspan=3, sticky="w", pady=(10, 4))

    add_entry(filtering_tab, 0, "Plateau fraction of short state", "plateau_fraction", PLATEAU_FRACTION_OF_SHORT_STATE)
    add_entry(filtering_tab, 1, "Minimum plateau samples", "min_plateau", MIN_PLATEAU_SAMPLES)
    add_entry(filtering_tab, 2, "Minimum edge-separation fraction", "edge_separation", MIN_EDGE_SEPARATION_FRACTION)
    add_entry(filtering_tab, 3, "Edge-search margin fraction", "search_margin", EDGE_SEARCH_MARGIN_FRACTION)
    add_entry(filtering_tab, 4, "Minimum state-separation ratio", "state_separation", MIN_STATE_SEPARATION_RATIO)
    add_entry(filtering_tab, 5, "Minimum complete-pulse duration ratio", "min_duration", MIN_COMPLETE_PULSE_DURATION_RATIO)
    add_entry(filtering_tab, 6, "Maximum complete-pulse duration ratio", "max_duration", MAX_COMPLETE_PULSE_DURATION_RATIO)
    add_entry(filtering_tab, 7, "Complete-pulse center weight", "center_weight", COMPLETE_PULSE_CENTER_WEIGHT)
    add_entry(filtering_tab, 8, "Minimum samples for IQR filtering", "min_iqr_samples", MIN_SAMPLES_FOR_IQR)
    add_entry(filtering_tab, 9, "IQR multiplier", "iqr_multiplier", IQR_MULTIPLIER,
              "Applied independently to each complete DLP condition group.")

    plot_every = tk.BooleanVar(value=PLOT_EVERY_WAVEFORM)
    plot_trends = tk.BooleanVar(value=PLOT_SUMMARY_TRENDS)
    ttk.Label(plots_tab, text="Plot outputs", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
    ttk.Checkbutton(plots_tab, text="Create an analysis plot for every valid waveform", variable=plot_every).pack(anchor="w", pady=5)
    ttk.Checkbutton(plots_tab, text="Create summary trend plots", variable=plot_trends).pack(anchor="w", pady=5)
    ttk.Label(
        plots_tab,
        text=(
            "Waveform plots show raw RF voltage, the RMS-derived envelope, the selected complete "
            "State 1 pulse, requested and companion edges, and interpolated 10%/90% crossings."
        ),
        foreground="#555555", wraplength=850,
    ).pack(anchor="w", pady=(14, 0))

    ttk.Label(comparison_tab, text="DLP comparison metrics", style="Section.TLabel").pack(
        anchor="w", pady=(0, 8)
    )
    ttk.Label(
        comparison_tab,
        text=(
            "Matched conditions use edge direction, RF frequency, ordered start and end power, "
            "pulse frequency, and duty cycle. Comparison outputs report Configuration B minus "
            "Configuration A, absolute and percent differences, B/A ratio, combined-standard-"
            "deviation separation, sample counts, and availability."
        ),
        wraplength=860, justify="left",
    ).pack(anchor="w")
    for text in (
        "Separate RISE and FALL comparison worksheets",
        "Non-transparency ranking by absolute percent and absolute time difference",
        "Average edge-time overlays by ordered DLP transition",
        "B - A difference panel with a common symmetric scale",
        "Configuration B summary, failures, and excluded-outlier audit sheets",
    ):
        ttk.Label(comparison_tab, text="• " + text).pack(anchor="w", pady=3)

    add_entry(output_tab, 0, "Excel workbook filename", "excel_filename", EXCEL_FILENAME)
    add_entry(output_tab, 1, "Summary CSV filename", "summary_filename", SUMMARY_CSV_FILENAME)
    add_entry(output_tab, 2, "Plot folder name", "plot_folder", PLOT_FOLDER_NAME)
    open_excel = tk.BooleanVar(value=OPEN_EXCEL_WHEN_COMPLETE)
    ttk.Checkbutton(output_tab, text="Open Excel workbook when analysis completes", variable=open_excel).grid(
        row=3, column=0, columnspan=3, sticky="w", pady=(10, 4))
    output_preview = tk.StringVar()
    ttk.Label(output_tab, textvariable=output_preview, foreground="#333333", wraplength=850, justify="left").grid(
        row=4, column=0, columnspan=3, sticky="w", pady=(16, 0))

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
            raise ValueError(f"{label} must not exceed {maximum}.")
        return value

    def refresh_preview(*_):
        compare = mode_var.get() == "COMPARE"
        state = "normal" if compare else "disabled"
        comparison_folder_entry.configure(state=state)
        configuration_b_name_entry.configure(state=state)
        comparison_folder_button.configure(state=state)
        folder = input_folder_var.get().strip() or "<select input folder>"
        output_preview.set(
            f"Outputs remain in the selected collection folder:\n{folder}\n\n"
            f"Workbook: {variables['excel_filename'].get()}\n"
            f"Summary CSV: {variables['summary_filename'].get()}\n"
            f"Plot folder: {variables['plot_folder'].get()} | "
            f"Every waveform: {'Yes' if plot_every.get() else 'No'} | "
            f"Trend plots: {'Yes' if plot_trends.get() else 'No'}\n"
            f"Comparison: {'Configuration B minus Configuration A' if compare else 'Disabled'}"
        )
    mode_var.trace_add("write", refresh_preview)
    input_folder_var.trace_add("write", refresh_preview)
    plot_every.trace_add("write", refresh_preview); plot_trends.trace_add("write", refresh_preview)
    for key in ("excel_filename", "summary_filename", "plot_folder"):
        variables[key].trace_add("write", refresh_preview)

    def start_analysis():
        try:
            folder = Path(input_folder_var.get().strip()).resolve()
            if not folder.is_dir():
                raise ValueError("Select an existing DLP collection folder.")
            csv_files = sorted(folder.glob("*.csv"))
            if not csv_files:
                raise ValueError("The selected folder contains no CSV files.")
            recognized = 0
            for item in csv_files:
                try:
                    parse_filename(item.name); recognized += 1
                except Exception:
                    pass
            if recognized == 0:
                raise ValueError("No filenames match the expected DLP collection format.")

            comparison_folder = None
            if mode_var.get() == "COMPARE":
                comparison_folder = Path(comparison_folder_var.get().strip()).resolve()
                if not comparison_folder.is_dir():
                    raise ValueError("Select an existing Configuration B DLP collection folder.")
                comparison_csv_files = sorted(comparison_folder.glob("*.csv"))
                if not comparison_csv_files:
                    raise ValueError("Configuration B contains no CSV files.")
                if os.path.normcase(str(folder)) == os.path.normcase(str(comparison_folder)):
                    raise ValueError("Select two different dataset folders.")
                comparison_recognized = 0
                for item in comparison_csv_files:
                    try:
                        parse_filename(item.name); comparison_recognized += 1
                    except Exception:
                        pass
                if comparison_recognized == 0:
                    raise ValueError("No Configuration B filenames match the expected DLP format.")

            data_start_row = number("data_start", "Data start row", 1, integer=True) - 1
            time_column = number("time_column", "Time column index", 0, integer=True)
            voltage_column = number("voltage_column", "Voltage column index", 0, integer=True)
            low_threshold = number("low_threshold", "Low threshold fraction", 0, 1)
            high_threshold = number("high_threshold", "High threshold fraction", 0, 1)
            if low_threshold >= high_threshold:
                raise ValueError("Low threshold fraction must be less than high threshold fraction.")
            min_window = number("min_window", "Minimum envelope window", 1, integer=True)
            max_window = number("max_window", "Maximum envelope window", min_window, integer=True)
            min_duration = number("min_duration", "Minimum pulse-duration ratio", 0)
            max_duration = number("max_duration", "Maximum pulse-duration ratio", min_duration)

            excel_name = variables["excel_filename"].get().strip()
            summary_name = variables["summary_filename"].get().strip()
            plot_name = variables["plot_folder"].get().strip()
            if not excel_name.lower().endswith(".xlsx"):
                raise ValueError("Excel workbook filename must end in .xlsx.")
            if not summary_name.lower().endswith(".csv"):
                raise ValueError("Summary filename must end in .csv.")
            if not plot_name:
                raise ValueError("Plot folder name cannot be blank.")
            invalid = '<>:"/\\|?*'
            if any(character in plot_name for character in invalid):
                raise ValueError("Plot folder name contains a Windows-invalid character.")

            result.update({
                "accepted": True, "root": root, "mode": mode_var.get(),
                "input_folder": str(folder),
                "comparison_folder": str(comparison_folder) if comparison_folder else None,
                "configuration_a_name": configuration_a_name_var.get().strip() or folder.name or "Configuration A",
                "configuration_b_name": configuration_b_name_var.get().strip() or (
                    comparison_folder.name if comparison_folder else "Configuration B"
                ),
                "data_start": data_start_row, "time_column": time_column,
                "voltage_column": voltage_column, "low_threshold": low_threshold,
                "high_threshold": high_threshold,
                "linear_interpolation": bool(linear_interpolation.get()),
                "rms_cycles": number("rms_cycles", "Envelope RMS cycles", 0.000001),
                "min_window": min_window, "max_window": max_window,
                "plateau_fraction": number("plateau_fraction", "Plateau fraction", 0, 1),
                "min_plateau": number("min_plateau", "Minimum plateau samples", 1, integer=True),
                "edge_separation": number("edge_separation", "Edge-separation fraction", 0, 1),
                "search_margin": number("search_margin", "Search-margin fraction", 0, 1),
                "state_separation": number("state_separation", "State-separation ratio", 0, 1),
                "min_duration": min_duration, "max_duration": max_duration,
                "center_weight": number("center_weight", "Center weight", 0),
                "min_iqr_samples": number("min_iqr_samples", "Minimum IQR samples", 1, integer=True),
                "iqr_multiplier": number("iqr_multiplier", "IQR multiplier", 0),
                "plot_every": bool(plot_every.get()), "plot_trends": bool(plot_trends.get()),
                "excel_filename": excel_name, "summary_filename": summary_name,
                "plot_folder": plot_name, "open_excel": bool(open_excel.get()),
            })
            root.withdraw()
            root.update_idletasks()
            root.quit()
        except Exception as error:
            messagebox.showerror("Invalid DLP analysis settings", str(error), parent=root)

    buttons = ttk.Frame(outer)
    buttons.pack(fill="x", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text="Start Analysis", style="Start.TButton", command=start_analysis).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    refresh_preview(); root.mainloop()
    return result


# ==========================================================
# FILENAME PARSING
# ==========================================================
def parse_filename(filename):
    """Parse filenames generated by DLPDataCollectionProduction.py."""
    stem = Path(filename).stem
    pattern = re.compile(
        r"RF_Freq_(?P<edge>RISE|FALL)(?P<rf>\d+(?:\.\d+)?)_"
        r"DLP_(?P<start>\d+(?:p\d+|\.\d+)?)W_to_"
        r"(?P<end>\d+(?:p\d+|\.\d+)?)W_"
        r"PulseFreq(?P<pulse>\d+(?:\.\d+)?)Hz_"
        r"Duty(?P<duty>\d+(?:\.\d+)?)pct_"
        r"Acq(?P<acq>\d+)",
        re.IGNORECASE,
    )
    match = pattern.search(stem)
    if not match:
        raise ValueError("Filename does not match the expected DLP collection format.")

    def number(text):
        return float(text.replace("p", "."))

    edge = match.group("edge").upper()
    start_w = number(match.group("start"))
    end_w = number(match.group("end"))
    expected_edge = "RISE" if end_w > start_w else "FALL"
    if start_w == end_w:
        raise ValueError("DLP transition endpoints are equal.")
    if edge != expected_edge:
        raise ValueError(
            f"Filename edge {edge} conflicts with transition {start_w:g} W -> {end_w:g} W."
        )
    rf_khz = number(match.group("rf"))
    pulse_hz = number(match.group("pulse"))
    duty_pct = number(match.group("duty"))
    if pulse_hz <= 0 or not 0 < duty_pct < 100:
        raise ValueError("Pulse frequency and duty cycle must define two nonzero states.")
    return {
        "Type": edge,
        "RF Frequency (kHz)": rf_khz,
        "RF Frequency (MHz)": rf_khz / 1000.0,
        "Start Power (W)": start_w,
        "End Power (W)": end_w,
        "Low Power (W)": min(start_w, end_w),
        "High Power (W)": max(start_w, end_w),
        "Transition": f"{start_w:g} W -> {end_w:g} W",
        "Pulse Frequency (Hz)": pulse_hz,
        "Pulse Frequency (kHz)": pulse_hz / 1000.0,
        "Duty Cycle (%)": duty_pct,
        "Acquisition Number": int(match.group("acq")),
    }


class AnalysisProgressWindow:
    """Visible, responsive status window for the existing synchronous analysis."""
    def __init__(self, parent):
        self.window = tk.Toplevel(parent)
        self.window.title("DLP Analysis Progress")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._prevent_close)
        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="DLP waveform analysis is running",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(frame, text="The current file and output phase are shown below.",
                  foreground="#555555").pack(anchor="w", pady=(3, 14))
        self.phase_var = tk.StringVar(value="Preparing analysis...")
        self.detail_var = tk.StringVar(value="Initializing")
        self.percent_var = tk.StringVar(value="0%")
        ttk.Label(frame, textvariable=self.phase_var,
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.detail_var, wraplength=600).pack(anchor="w", pady=(3, 9))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.bar = ttk.Progressbar(row, mode="determinate", maximum=100, length=520)
        self.bar.pack(side="left", fill="x", expand=True)
        ttk.Label(row, textvariable=self.percent_var, width=6, anchor="e").pack(side="left", padx=(10, 0))
        self.activity = ttk.Progressbar(frame, mode="indeterminate", length=580)
        self.activity.pack(fill="x", pady=(10, 0))
        self.activity.start(12)
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = max(0, (self.window.winfo_screenwidth() - width) // 2)
        y = max(0, (self.window.winfo_screenheight() - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.deiconify()
        try:
            self.window.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.refresh()

    def _prevent_close(self):
        try: self.window.bell()
        except tk.TclError: pass

    def refresh(self):
        try:
            if not self.window.winfo_viewable(): self.window.deiconify()
            self.window.lift()
            self.window.update_idletasks()
            self.window.update()
        except tk.TclError:
            pass

    def update(self, percent=None, phase=None, detail=None):
        if percent is not None:
            value = max(0.0, min(100.0, float(percent)))
            self.bar["value"] = value
            self.percent_var.set(f"{value:.0f}%")
        if phase is not None: self.phase_var.set(str(phase))
        if detail is not None: self.detail_var.set(str(detail))
        self.refresh()

    def close(self):
        try:
            self.activity.stop()
            self.window.destroy()
        except tk.TclError:
            pass


configuration = configure_dlp_analysis_with_gui()
if not configuration.get("accepted"):
    raise SystemExit("DLP analysis cancelled before processing.")
root = configuration["root"]
ANALYSIS_MODE = configuration["mode"]
input_folder = configuration["input_folder"]
comparison_folder = configuration["comparison_folder"]
configuration_a_name = configuration["configuration_a_name"]
configuration_b_name = configuration["configuration_b_name"]
DATA_START_ROW_INDEX = configuration["data_start"]
TIME_COLUMN_INDEX = configuration["time_column"]
VOLTAGE_COLUMN_INDEX = configuration["voltage_column"]
LOW_THRESHOLD_FRACTION = configuration["low_threshold"]
HIGH_THRESHOLD_FRACTION = configuration["high_threshold"]
USE_LINEAR_INTERPOLATION = configuration["linear_interpolation"]
ENVELOPE_RMS_CYCLES = configuration["rms_cycles"]
MIN_ENVELOPE_WINDOW_SAMPLES = configuration["min_window"]
MAX_ENVELOPE_WINDOW_SAMPLES = configuration["max_window"]
PLATEAU_FRACTION_OF_SHORT_STATE = configuration["plateau_fraction"]
MIN_PLATEAU_SAMPLES = configuration["min_plateau"]
MIN_EDGE_SEPARATION_FRACTION = configuration["edge_separation"]
EDGE_SEARCH_MARGIN_FRACTION = configuration["search_margin"]
MIN_STATE_SEPARATION_RATIO = configuration["state_separation"]
MIN_COMPLETE_PULSE_DURATION_RATIO = configuration["min_duration"]
MAX_COMPLETE_PULSE_DURATION_RATIO = configuration["max_duration"]
COMPLETE_PULSE_CENTER_WEIGHT = configuration["center_weight"]
MIN_SAMPLES_FOR_IQR = configuration["min_iqr_samples"]
IQR_MULTIPLIER = configuration["iqr_multiplier"]
PLOT_EVERY_WAVEFORM = configuration["plot_every"]
PLOT_SUMMARY_TRENDS = configuration["plot_trends"]
EXCEL_FILENAME = configuration["excel_filename"]
SUMMARY_CSV_FILENAME = configuration["summary_filename"]
PLOT_FOLDER_NAME = configuration["plot_folder"]
OPEN_EXCEL_WHEN_COMPLETE = configuration["open_excel"]

input_folder = os.path.abspath(input_folder)
plot_folder = os.path.join(input_folder, PLOT_FOLDER_NAME)
individual_plot_folder = os.path.join(plot_folder, "Waves")
trend_plot_folder = os.path.join(plot_folder, "Trends")
excel_output_file = os.path.join(input_folder, EXCEL_FILENAME)
summary_csv_file = os.path.join(input_folder, SUMMARY_CSV_FILENAME)
if PLOT_EVERY_WAVEFORM:
    os.makedirs(individual_plot_folder, exist_ok=True)
if PLOT_SUMMARY_TRENDS:
    os.makedirs(trend_plot_folder, exist_ok=True)
image_state_a = snapshot_png_files(input_folder)
image_state_b = snapshot_png_files(comparison_folder) if ANALYSIS_MODE == "COMPARE" else {}


# ==========================================================
# CSV READING
# ==========================================================
def read_scope_csv(csv_path):
    frame = pd.read_csv(csv_path)
    if frame.shape[1] < 2:
        raise ValueError("CSV file has fewer than two columns.")
    time_raw = pd.to_numeric(
        frame.iloc[DATA_START_ROW_INDEX:, TIME_COLUMN_INDEX], errors="coerce"
    ).to_numpy(dtype=float)
    voltage_raw = pd.to_numeric(
        frame.iloc[DATA_START_ROW_INDEX:, VOLTAGE_COLUMN_INDEX], errors="coerce"
    ).to_numpy(dtype=float)
    valid = np.isfinite(time_raw) & np.isfinite(voltage_raw)
    time_values = time_raw[valid]
    voltage_values = voltage_raw[valid]
    if len(time_values) < 10:
        raise ValueError("Fewer than ten valid waveform samples were found.")
    order = np.argsort(time_values)
    time_values = time_values[order]
    voltage_values = voltage_values[order]
    if np.any(np.diff(time_values) <= 0):
        raise ValueError("Time values are duplicated or not strictly increasing.")
    return time_values, voltage_values, len(time_raw), int(np.sum(~valid))

# ==========================================================
# ENVELOPE EXTRACTION
# ==========================================================
def extract_rf_envelope(time_values, voltage_values, rf_frequency_khz):
    """Return peak-equivalent envelope from a centered moving RMS calculation."""
    dt = float(np.median(np.diff(time_values)))
    if not np.isfinite(dt) or dt <= 0:
        raise ValueError("Invalid sample interval.")
    sample_rate_hz = 1.0 / dt
    rf_hz = float(rf_frequency_khz) * 1000.0
    samples_per_cycle = sample_rate_hz / rf_hz
    window = int(round(samples_per_cycle * ENVELOPE_RMS_CYCLES))
    window = max(MIN_ENVELOPE_WINDOW_SAMPLES, window)
    window = min(MAX_ENVELOPE_WINDOW_SAMPLES, window)
    if window % 2 == 0:
        window += 1
    if window >= len(voltage_values):
        raise ValueError("Envelope window is longer than the waveform.")
    rms = np.sqrt(np.maximum(centered_moving_average(voltage_values ** 2, window), 0.0))
    peak_equivalent = rms * np.sqrt(2.0)
    return peak_equivalent, window, sample_rate_hz

# ==========================================================
# EDGE LOCATION AND TIMING
# ==========================================================
def interpolate_crossing(time_values, signal, left_index, threshold):
    right_index = left_index + 1
    t0, t1 = float(time_values[left_index]), float(time_values[right_index])
    v0, v1 = float(signal[left_index]), float(signal[right_index])
    if not USE_LINEAR_INTERPOLATION or v1 == v0:
        return t1
    fraction = float(np.clip((threshold - v0) / (v1 - v0), 0.0, 1.0))
    return t0 + fraction * (t1 - t0)


def robust_median(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        raise ValueError("A required plateau region contains no valid samples.")
    return float(np.median(values))


def candidate_midpoint_crossings(envelope, midpoint, edge_type, start_index, stop_index):
    left = envelope[:-1]
    right = envelope[1:]
    indices = np.arange(len(envelope) - 1)
    in_search = (indices >= start_index) & (indices < stop_index)
    if edge_type == "RISE":
        mask = in_search & (left < midpoint) & (right >= midpoint)
    else:
        mask = in_search & (left > midpoint) & (right <= midpoint)
    return np.where(mask)[0]


def analyze_dlp_edge(time_values, raw_voltage, envelope, info):
    """Measure the requested edge only when it belongs to a complete DLP pulse.

    State 0 is the filename's starting power and State 1 is its ending power.
    A complete pulse is therefore:

        stable State 0 -> requested edge -> stable State 1
        -> companion edge -> stable State 0

    Pulse fragments touching either waveform boundary are ignored because they
    cannot provide all three required plateau regions.
    """
    pulse_period_s = 1.0 / info["Pulse Frequency (Hz)"]
    state1_s = pulse_period_s * info["Duty Cycle (%)"] / 100.0
    state0_s = pulse_period_s - state1_s
    short_state_s = min(state0_s, state1_s)
    dt = float(np.median(np.diff(time_values)))

    plateau_samples = max(
        MIN_PLATEAU_SAMPLES,
        int(round(short_state_s * PLATEAU_FRACTION_OF_SHORT_STATE / dt)),
    )
    plateau_samples = min(
        plateau_samples,
        max(MIN_PLATEAU_SAMPLES, len(envelope) // 10),
    )

    finite_envelope = envelope[np.isfinite(envelope)]
    sorted_env = np.sort(finite_envelope)
    tail_count = max(MIN_PLATEAU_SAMPLES, int(round(0.15 * len(sorted_env))))
    tail_count = min(tail_count, len(sorted_env) // 2)
    low_global = robust_median(sorted_env[:tail_count])
    high_global = robust_median(sorted_env[-tail_count:])
    if high_global <= low_global:
        raise ValueError("Could not separate the two DLP envelope states.")
    if (high_global - low_global) / max(high_global, 1e-15) < MIN_STATE_SEPARATION_RATIO:
        raise ValueError("Measured DLP state separation is too small for reliable timing.")
    global_midpoint = 0.5 * (low_global + high_global)

    margin = max(
        plateau_samples,
        int(round(len(envelope) * EDGE_SEARCH_MARGIN_FRACTION)),
    )
    rise_edges = candidate_midpoint_crossings(
        envelope, global_midpoint, "RISE", 0, len(envelope) - 1
    )
    fall_edges = candidate_midpoint_crossings(
        envelope, global_midpoint, "FALL", 0, len(envelope) - 1
    )
    all_edges = sorted(
        [(int(index), "RISE") for index in rise_edges]
        + [(int(index), "FALL") for index in fall_edges],
        key=lambda item: item[0],
    )
    requested_type = info["Type"]
    companion_type = "FALL" if requested_type == "RISE" else "RISE"

    complete_candidates = []
    boundary_rejected = 0
    duration_rejected = 0
    plateau_rejected = 0

    expected_state1_duration_s = state1_s
    minimum_duration_s = expected_state1_duration_s * MIN_COMPLETE_PULSE_DURATION_RATIO
    maximum_duration_s = expected_state1_duration_s * MAX_COMPLETE_PULSE_DURATION_RATIO

    for edge_position, (requested_index, detected_type) in enumerate(all_edges):
        if detected_type != requested_type:
            continue

        # Pair this requested edge with the immediately following detected edge.
        # If it is not the opposite edge, this is not a clean complete pulse.
        if edge_position + 1 >= len(all_edges):
            boundary_rejected += 1
            continue
        companion_index, next_type = all_edges[edge_position + 1]
        if next_type != companion_type:
            plateau_rejected += 1
            continue

        before_start = requested_index - plateau_samples
        state1_start = requested_index + 1
        state1_stop = companion_index
        after_start = companion_index + 1
        after_stop = companion_index + 1 + plateau_samples
        if before_start < 0 or after_stop > len(envelope):
            boundary_rejected += 1
            continue
        if state1_stop - state1_start < MIN_PLATEAU_SAMPLES:
            plateau_rejected += 1
            continue

        measured_state1_duration_s = (
            time_values[companion_index] - time_values[requested_index]
        )
        if not minimum_duration_s <= measured_state1_duration_s <= maximum_duration_s:
            duration_rejected += 1
            continue

        before_plateau = envelope[before_start:requested_index]
        state1_plateau = envelope[state1_start:state1_stop]
        after_plateau = envelope[after_start:after_stop]
        before_level = robust_median(before_plateau)
        state1_level = robust_median(state1_plateau)
        after_level = robust_median(after_plateau)
        state0_level = robust_median(np.concatenate([before_plateau, after_plateau]))

        signed_step = state1_level - state0_level
        if requested_type == "RISE" and signed_step <= 0:
            plateau_rejected += 1
            continue
        if requested_type == "FALL" and signed_step >= 0:
            plateau_rejected += 1
            continue

        state_span = abs(signed_step)
        state0_consistency = abs(before_level - after_level) / max(state_span, 1e-15)
        duration_error_fraction = abs(
            measured_state1_duration_s - expected_state1_duration_s
        ) / expected_state1_duration_s
        pulse_center_index = 0.5 * (requested_index + companion_index)
        center_error_fraction = abs(
            pulse_center_index - 0.5 * len(envelope)
        ) / len(envelope)

        # Prefer pulses with expected duration, matching pre/post State 0
        # plateaus, strong state separation, and a location near record center.
        score = (
            state_span
            / (1.0 + duration_error_fraction)
            / (1.0 + state0_consistency)
            / (1.0 + COMPLETE_PULSE_CENTER_WEIGHT * center_error_fraction)
        )
        complete_candidates.append({
            "score": score,
            "requested_index": requested_index,
            "companion_index": companion_index,
            "state0_level": state0_level,
            "state1_level": state1_level,
            "before_level": before_level,
            "after_level": after_level,
            "measured_state1_duration_s": measured_state1_duration_s,
            "duration_error_fraction": duration_error_fraction,
            "state0_consistency": state0_consistency,
        })

    if not complete_candidates:
        raise ValueError(
            "No complete DLP pulse was found. "
            f"Boundary partials ignored: {boundary_rejected}; "
            f"duration rejects: {duration_rejected}; "
            f"plateau/sequence rejects: {plateau_rejected}."
        )

    selected = max(complete_candidates, key=lambda candidate: candidate["score"])
    requested_index = selected["requested_index"]
    companion_index = selected["companion_index"]
    start_level = selected["state0_level"]
    end_level = selected["state1_level"]
    low_level = min(start_level, end_level)
    high_level = max(start_level, end_level)
    span = high_level - low_level
    threshold10 = low_level + LOW_THRESHOLD_FRACTION * span
    threshold90 = low_level + HIGH_THRESHOLD_FRACTION * span

    # Restrict threshold searches to the selected requested transition. The
    # midpoint between the requested and companion edges prevents the companion
    # edge from being inadvertently selected.
    local_half_width = max(
        plateau_samples,
        int(round(short_state_s * 0.45 / dt)),
        int(round(len(envelope) * MIN_EDGE_SEPARATION_FRACTION)),
    )
    left_bound = max(0, requested_index - local_half_width)
    right_bound = min(
        len(envelope) - 2,
        requested_index + local_half_width,
        max(requested_index + 1, (requested_index + companion_index) // 2),
    )
    indices = np.arange(len(envelope) - 1)

    if requested_type == "RISE":
        cross10 = np.where(
            (indices >= left_bound) & (indices <= requested_index)
            & (envelope[:-1] < threshold10) & (envelope[1:] >= threshold10)
        )[0]
        cross90 = np.where(
            (indices >= requested_index) & (indices <= right_bound)
            & (envelope[:-1] < threshold90) & (envelope[1:] >= threshold90)
        )[0]
        if len(cross10) == 0 or len(cross90) == 0:
            raise ValueError("Selected complete pulse lacks a valid rising 10%-to-90% pair.")
        idx10 = int(cross10[-1])
        idx90 = int(cross90[0])
    else:
        cross90 = np.where(
            (indices >= left_bound) & (indices <= requested_index)
            & (envelope[:-1] > threshold90) & (envelope[1:] <= threshold90)
        )[0]
        cross10 = np.where(
            (indices >= requested_index) & (indices <= right_bound)
            & (envelope[:-1] > threshold10) & (envelope[1:] <= threshold10)
        )[0]
        if len(cross90) == 0 or len(cross10) == 0:
            raise ValueError("Selected complete pulse lacks a valid falling 90%-to-10% pair.")
        idx90 = int(cross90[-1])
        idx10 = int(cross10[0])

    t10 = interpolate_crossing(time_values, envelope, idx10, threshold10)
    t90 = interpolate_crossing(time_values, envelope, idx90, threshold90)
    edge_time_s = (t90 - t10) if requested_type == "RISE" else (t10 - t90)
    if not np.isfinite(edge_time_s) or edge_time_s <= 0:
        raise ValueError(f"Calculated edge time is invalid: {edge_time_s}")

    requested_edge_time_s = interpolate_crossing(
        time_values, envelope, requested_index, global_midpoint
    )
    companion_edge_time_s = interpolate_crossing(
        time_values, envelope, companion_index, global_midpoint
    )
    selected_pulse_number = complete_candidates.index(selected) + 1

    return {
        "Start State Envelope (Vpk eq.)": start_level,
        "End State Envelope (Vpk eq.)": end_level,
        "Low State Envelope (Vpk eq.)": low_level,
        "High State Envelope (Vpk eq.)": high_level,
        "State Envelope Span (Vpk eq.)": span,
        "10% Envelope (Vpk eq.)": threshold10,
        "90% Envelope (Vpk eq.)": threshold90,
        "t10 (s)": t10,
        "t90 (s)": t90,
        "Edge Time (s)": edge_time_s,
        "Detected Edge Index": requested_index,
        "Companion Edge Index": companion_index,
        "Requested Edge Time (s)": requested_edge_time_s,
        "Companion Edge Time (s)": companion_edge_time_s,
        "Selected Pulse Start Time (s)": requested_edge_time_s,
        "Selected Pulse End Time (s)": companion_edge_time_s,
        "Detected Complete Pulse Count": len(complete_candidates),
        "Selected Complete Pulse Number": selected_pulse_number,
        "Boundary Partial Pulses Ignored": boundary_rejected,
        "Duration-Rejected Pulse Candidates": duration_rejected,
        "Plateau-Rejected Pulse Candidates": plateau_rejected,
        "Measured State 1 Duration (s)": selected["measured_state1_duration_s"],
        "Expected State 1 Duration (s)": expected_state1_duration_s,
        "State 1 Duration Error (%)": selected["duration_error_fraction"] * 100.0,
        "Pre/Post State 0 Mismatch Ratio": selected["state0_consistency"],
        "Candidate Edge Count": len(all_edges),
        "Plateau Window Samples": plateau_samples,
        "Pulse Period (s)": pulse_period_s,
        "State 0 Duration (s)": state0_s,
        "State 1 Duration (s)": state1_s,
    }


# ==========================================================
# PLOTTING
# ==========================================================
def _windows_extended_path(file_path):
    """Return an absolute extended-length path on Windows."""
    absolute_path = os.path.abspath(os.fspath(file_path))
    if os.name != "nt" or absolute_path.startswith("\\\\?\\"):
        return absolute_path
    if absolute_path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute_path.lstrip("\\")
    return "\\\\?\\" + absolute_path


def safe_save_figure(figure, output_path, **kwargs):
    """Create the destination immediately before saving and support long Windows paths."""
    output_path = os.path.abspath(os.fspath(output_path))
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    figure.savefig(_windows_extended_path(output_path), **kwargs)
    return output_path


def report_nonfatal_warning(description, error):
    message = f"WARNING: {description}: {error}"
    print("\\n" + message, flush=True)
    try:
        progress_window.update(detail=message)
    except Exception:
        pass
    return message


def run_plot_step_nonfatal(description, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception as error:
        report_nonfatal_warning(description, error)
        return []


def save_waveform_plot(filename, time_values, raw_voltage, envelope, info, calc):
    figure, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    pulse_start_us = calc["Selected Pulse Start Time (s)"] * 1e6
    pulse_end_us = calc["Selected Pulse End Time (s)"] * 1e6

    axes[0].plot(time_values * 1e6, raw_voltage, linewidth=0.6, color="#4472C4")
    axes[0].axvspan(pulse_start_us, pulse_end_us, color="#D9EAF7", alpha=0.45)
    axes[0].set_ylabel("Raw RF voltage (V)")
    axes[0].set_title(
        f"{filename}\n{info['Transition']} | {info['Type']} = "
        f"{calc['Edge Time (s)'] * 1e6:.3f} us | "
        f"complete pulses found: {calc['Detected Complete Pulse Count']}"
    )
    axes[0].grid(True)

    axes[1].plot(
        time_values * 1e6, envelope, linewidth=1.2,
        color="#1F4E79", label="RMS RF envelope"
    )
    axes[1].axvspan(
        pulse_start_us, pulse_end_us, color="#D9EAF7", alpha=0.45,
        label="Selected complete State 1 pulse"
    )
    axes[1].axhline(
        calc["10% Envelope (Vpk eq.)"], color="green", linestyle=":", label="10% level"
    )
    axes[1].axhline(
        calc["90% Envelope (Vpk eq.)"], color="red", linestyle=":", label="90% level"
    )
    axes[1].axvline(
        calc["t10 (s)"] * 1e6, color="green", linestyle="--", label="t10"
    )
    axes[1].axvline(
        calc["t90 (s)"] * 1e6, color="red", linestyle="--", label="t90"
    )
    axes[1].axvline(
        calc["Requested Edge Time (s)"] * 1e6,
        color="#00A651", linestyle="-.", linewidth=1.5, label="Requested edge"
    )
    axes[1].axvline(
        calc["Companion Edge Time (s)"] * 1e6,
        color="#F28E2B", linestyle="-.", linewidth=1.5, label="Companion edge"
    )
    axes[1].set_xlabel("Time (us)")
    axes[1].set_ylabel("Peak-equivalent envelope (V)")
    axes[1].grid(True)
    axes[1].legend(loc="best")
    figure.tight_layout()
    output = os.path.join(
        individual_plot_folder, f"{Path(filename).stem}_DLP_analysis.png"
    )
    safe_save_figure(figure, output, dpi=200)
    plt.close(figure)
    return output


# ==========================================================
# OUTLIER FILTER AND SUMMARIES
# ==========================================================
GROUP_COLUMNS = [
    "Type", "RF Frequency (kHz)", "Start Power (W)", "End Power (W)",
    "Pulse Frequency (Hz)", "Duty Cycle (%)",
]


def add_iqr_flags(frame):
    frame = frame.copy()
    frame["Use In Average"] = True
    frame["Outlier Reason"] = ""
    frame["IQR Lower Bound (s)"] = np.nan
    frame["IQR Upper Bound (s)"] = np.nan
    for _, group in frame.groupby(GROUP_COLUMNS, dropna=False):
        values = group["Edge Time (s)"].dropna()
        if len(values) < MIN_SAMPLES_FOR_IQR:
            frame.loc[group.index, "Outlier Reason"] = "Included: insufficient samples for IQR filtering"
            continue
        q1, q3 = values.quantile(0.25), values.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - IQR_MULTIPLIER * iqr
        upper = q3 + IQR_MULTIPLIER * iqr
        frame.loc[group.index, "IQR Lower Bound (s)"] = lower
        frame.loc[group.index, "IQR Upper Bound (s)"] = upper
        excluded = (group["Edge Time (s)"] < lower) | (group["Edge Time (s)"] > upper)
        frame.loc[group.index[excluded], "Use In Average"] = False
        frame.loc[group.index[excluded], "Outlier Reason"] = "Excluded by 1.5x IQR filter"
        frame.loc[group.index[~excluded], "Outlier Reason"] = "Included within IQR bounds"
    return frame


def create_summary(flagged):
    if flagged.empty:
        return pd.DataFrame()
    total = flagged.groupby(GROUP_COLUMNS, dropna=False).size().rename("Total Sample Count")
    included = flagged[flagged["Use In Average"]].copy()
    if included.empty:
        return pd.DataFrame()
    summary = included.groupby(GROUP_COLUMNS, dropna=False).agg(
        **{
            "Included Sample Count": ("Edge Time (s)", "count"),
            "Average Edge Time (us)": ("Edge Time (us)", "mean"),
            "Minimum Edge Time (us)": ("Edge Time (us)", "min"),
            "Maximum Edge Time (us)": ("Edge Time (us)", "max"),
            "Std Dev Edge Time (us)": ("Edge Time (us)", "std"),
            "Average Start Envelope (Vpk eq.)": ("Start State Envelope (Vpk eq.)", "mean"),
            "Average End Envelope (Vpk eq.)": ("End State Envelope (Vpk eq.)", "mean"),
        }
    ).join(total).reset_index()
    summary["Excluded Outlier Count"] = summary["Total Sample Count"] - summary["Included Sample Count"]
    return summary.sort_values(GROUP_COLUMNS).reset_index(drop=True)


def create_trend_plots(summary):
    if summary.empty or not PLOT_SUMMARY_TRENDS:
        return
    for (edge, pulse_hz, duty), group in summary.groupby(
        ["Type", "Pulse Frequency (Hz)", "Duty Cycle (%)"], dropna=False
    ):
        figure, axis = plt.subplots(figsize=(11, 7))
        for rf_khz, rf_group in group.groupby("RF Frequency (kHz)"):
            labels = [f"{a:g}->{b:g}" for a, b in zip(rf_group["Start Power (W)"], rf_group["End Power (W)"])]
            x = np.arange(len(rf_group))
            y = rf_group["Average Edge Time (us)"].to_numpy()
            ymin = rf_group["Minimum Edge Time (us)"].to_numpy()
            ymax = rf_group["Maximum Edge Time (us)"].to_numpy()
            axis.errorbar(x, y, yerr=[y - ymin, ymax - y], marker="o", capsize=5, label=f"{rf_khz:g} kHz")
            axis.set_xticks(x, labels, rotation=30, ha="right")
        axis.set_title(f"DLP {edge} Time by Ordered Transition\nPulse {pulse_hz:g} Hz, Duty {duty:g}%")
        axis.set_xlabel("Ordered transition (W)")
        axis.set_ylabel(f"Average {edge.title()} Time (us)")
        axis.grid(True)
        axis.legend(title="RF frequency")
        figure.tight_layout()
        path = os.path.join(trend_plot_folder, f"DLP_{edge}_Pulse{pulse_hz:g}Hz_Duty{duty:g}pct.png")
        safe_save_figure(figure, path, dpi=250)
        plt.close(figure)

# ==========================================================
# EXCEL FORMATTING
# ==========================================================
def format_workbook(writer):
    for sheet in writer.book.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            letter = column[0].column_letter
            width = min(max(max(len(str(cell.value or "")) for cell in column) + 2, 12), 55)
            sheet.column_dimensions[letter].width = width


def open_output(path):
    try:
        if os.name == "nt":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as error:
        print(f"Could not open output automatically: {error}")

# ==========================================================
# TWO-DATASET DLP COMPARISON
# ==========================================================
def process_dlp_comparison_folder(folder_path, configuration_name, progress=None,
                                  start_percent=45.0, end_percent=68.0):
    """Analyze Configuration B with the exact same DLP waveform engine."""
    rows, failures = [], []
    files = sorted(name for name in os.listdir(folder_path) if name.lower().endswith(".csv"))
    for number, filename in enumerate(files, 1):
        if progress is not None:
            progress.update(
                start_percent + (number - 1) / max(1, len(files)) * (end_percent - start_percent),
                f"Analyzing {configuration_name}",
                f"File {number} of {len(files)}: {filename}"
            )
        path = os.path.join(folder_path, filename)
        print(f"[{configuration_name}] Processing {number}/{len(files)}: {filename}")
        try:
            info = parse_filename(filename)
            time_values, raw_voltage, raw_rows, invalid_removed = read_scope_csv(path)
            envelope, envelope_window, sample_rate = extract_rf_envelope(
                time_values, raw_voltage, info["RF Frequency (kHz)"]
            )
            calc = analyze_dlp_edge(time_values, raw_voltage, envelope, info)
            edge_time_s = calc["Edge Time (s)"]
            rows.append({
                "Configuration": configuration_name, "Filename": filename, "CSV Path": path,
                **info, **calc, "Edge Time (us)": edge_time_s * 1e6,
                "Edge Time (ns)": edge_time_s * 1e9,
                "Requested Edge Time (us)": calc["Requested Edge Time (s)"] * 1e6,
                "Companion Edge Time (us)": calc["Companion Edge Time (s)"] * 1e6,
                "Measured State 1 Duration (us)": calc["Measured State 1 Duration (s)"] * 1e6,
                "Expected State 1 Duration (us)": calc["Expected State 1 Duration (s)"] * 1e6,
                "Envelope RMS Window Samples": envelope_window,
                "Envelope RMS Window Cycles": ENVELOPE_RMS_CYCLES,
                "Sample Rate (Sa/s)": sample_rate, "Valid Samples": len(time_values),
                "Raw Data Rows": raw_rows, "Invalid Samples Removed": invalid_removed,
                "Linear Interpolation Used": USE_LINEAR_INTERPOLATION,
            })
        except Exception as error:
            failures.append({"Configuration": configuration_name, "Filename": filename,
                             "CSV Path": path, "Error": str(error)})
    return pd.DataFrame(rows), pd.DataFrame(failures)


def create_dlp_configuration_comparison(summary_a, summary_b, edge_type, name_a, name_b):
    """Compare matched ordered DLP transitions for one edge direction."""
    keys = ["RF Frequency (kHz)", "Start Power (W)", "End Power (W)",
            "Pulse Frequency (Hz)", "Duty Cycle (%)"]
    wanted = keys + ["Included Sample Count", "Total Sample Count",
                     "Excluded Outlier Count", "Average Edge Time (us)",
                     "Minimum Edge Time (us)", "Maximum Edge Time (us)",
                     "Std Dev Edge Time (us)"]
    a = summary_a[summary_a["Type"] == edge_type] if not summary_a.empty else pd.DataFrame()
    b = summary_b[summary_b["Type"] == edge_type] if not summary_b.empty else pd.DataFrame()
    if a.empty and b.empty:
        return pd.DataFrame()
    a = a[wanted].rename(columns={column: f"{name_a} {column}" for column in wanted if column not in keys})
    b = b[wanted].rename(columns={column: f"{name_b} {column}" for column in wanted if column not in keys})
    merged = a.merge(b, on=keys, how="outer", indicator=True)
    avg_a = f"{name_a} Average Edge Time (us)"; avg_b = f"{name_b} Average Edge Time (us)"
    std_a = f"{name_a} Std Dev Edge Time (us)"; std_b = f"{name_b} Std Dev Edge Time (us)"
    merged["Matched Condition"] = merged["_merge"].eq("both")
    merged["Difference B-A (us)"] = merged[avg_b] - merged[avg_a]
    merged["Absolute Difference (us)"] = merged["Difference B-A (us)"].abs()
    merged["Percent Difference vs A (%)"] = np.where(
        merged[avg_a].notna() & (merged[avg_a] != 0),
        100.0 * merged["Difference B-A (us)"] / merged[avg_a], np.nan)
    merged["Absolute Percent Difference (%)"] = merged["Percent Difference vs A (%)"].abs()
    merged["B/A Ratio"] = np.where(
        merged[avg_a].notna() & (merged[avg_a] != 0), merged[avg_b] / merged[avg_a], np.nan)
    combined_std = np.sqrt(merged[std_a].fillna(0) ** 2 + merged[std_b].fillna(0) ** 2)
    merged["Difference / Combined Std Dev"] = np.where(
        combined_std > 0, merged["Absolute Difference (us)"] / combined_std, np.nan)
    merged["Edge"] = edge_type
    merged["Transition"] = merged.apply(
        lambda row: f"{row['Start Power (W)']:g} W -> {row['End Power (W)']:g} W", axis=1)
    merged["Availability"] = merged["_merge"].map({
        "both": "Matched", "left_only": f"Only {name_a}", "right_only": f"Only {name_b}"})
    return merged.drop(columns=["_merge"]).sort_values(keys).reset_index(drop=True)


def create_dlp_comparison_plots(comparison_df, edge_type, name_a, name_b, folder):
    """Plot matched ordered-transition averages and B-minus-A values."""
    if comparison_df.empty:
        return []
    matched = comparison_df[comparison_df["Matched Condition"]].copy()
    if matched.empty:
        return []
    os.makedirs(folder, exist_ok=True)
    avg_a = f"{name_a} Average Edge Time (us)"; avg_b = f"{name_b} Average Edge Time (us)"
    y_values = pd.concat([matched[avg_a], matched[avg_b]]).dropna()
    y_min, y_max = float(y_values.min()), float(y_values.max())
    y_pad = max((y_max - y_min) * 0.08, abs(y_max) * 0.02, 0.001)
    dlim = max(float(matched["Absolute Difference (us)"].max()) * 1.10, 0.001)
    paths = []
    for (pulse_hz, duty, rf_khz), group in matched.groupby(
        ["Pulse Frequency (Hz)", "Duty Cycle (%)", "RF Frequency (kHz)"], dropna=False
    ):
        group = group.sort_values(["Start Power (W)", "End Power (W)"])
        labels = [f"{start:g}->{end:g}" for start, end in zip(group["Start Power (W)"], group["End Power (W)"])]
        x = np.arange(len(group))
        fig, (ax_top, ax_delta) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                                gridspec_kw={"height_ratios": [2.2, 1.0]})
        ax_top.plot(x, group[avg_a], marker="o", linewidth=2, label=name_a)
        ax_top.plot(x, group[avg_b], marker="s", linestyle="--", linewidth=2, label=name_b)
        ax_delta.plot(x, group["Difference B-A (us)"], marker="o", color="#C00000")
        ax_top.set_ylim(y_min - y_pad, y_max + y_pad)
        ax_delta.set_ylim(-dlim, dlim); ax_delta.axhline(0, color="black", linewidth=1)
        ax_top.set_ylabel(f"Average {edge_type.title()} Time (us)")
        ax_delta.set_ylabel("B - A (us)"); ax_delta.set_xlabel("Ordered DLP transition (W)")
        ax_delta.set_xticks(x, labels, rotation=30, ha="right")
        ax_top.grid(True, alpha=0.3); ax_delta.grid(True, alpha=0.3); ax_top.legend(loc="best")
        fig.suptitle(f"DLP {edge_type} Comparison | RF {rf_khz:g} kHz | Pulse {pulse_hz:g} Hz | Duty {duty:g}%")
        fig.tight_layout()
        path = os.path.join(folder, f"DLP_{edge_type}_RF{rf_khz:g}kHz_Pulse{pulse_hz:g}Hz_Duty{duty:g}pct.png")
        safe_save_figure(fig, path, dpi=250, bbox_inches="tight"); plt.close(fig); paths.append(path)
    return paths


# ==========================================================
# PROCESS FILES
# ==========================================================
progress_window = AnalysisProgressWindow(root)
progress_window.update(1, "Discovering input files", configuration_a_name)
csv_files = sorted(name for name in os.listdir(input_folder) if name.lower().endswith(".csv"))
if not csv_files:
    progress_window.close()
    root.destroy()
    raise SystemExit("No CSV files were found in the selected folder.")

results = []
failures = []
for number, filename in enumerate(csv_files, 1):
    primary_end = 43.0 if ANALYSIS_MODE == "COMPARE" else 62.0
    progress_window.update(
        3.0 + (number - 1) / max(1, len(csv_files)) * (primary_end - 3.0),
        f"Analyzing {configuration_a_name}",
        f"File {number} of {len(csv_files)}: {filename}"
    )
    print(f"\nProcessing {number}/{len(csv_files)}: {filename}")
    path = os.path.join(input_folder, filename)
    try:
        info = parse_filename(filename)
        time_values, raw_voltage, raw_rows, invalid_removed = read_scope_csv(path)
        envelope, envelope_window, sample_rate = extract_rf_envelope(
            time_values, raw_voltage, info["RF Frequency (kHz)"]
        )
        calc = analyze_dlp_edge(time_values, raw_voltage, envelope, info)
        edge_time_s = calc["Edge Time (s)"]
        row = {
            "Filename": filename,
            "CSV Path": path,
            **info,
            **calc,
            "Edge Time (us)": edge_time_s * 1e6,
            "Edge Time (ns)": edge_time_s * 1e9,
            "Requested Edge Time (us)": calc["Requested Edge Time (s)"] * 1e6,
            "Companion Edge Time (us)": calc["Companion Edge Time (s)"] * 1e6,
            "Measured State 1 Duration (us)": calc["Measured State 1 Duration (s)"] * 1e6,
            "Expected State 1 Duration (us)": calc["Expected State 1 Duration (s)"] * 1e6,
            "Envelope RMS Window Samples": envelope_window,
            "Envelope RMS Window Cycles": ENVELOPE_RMS_CYCLES,
            "Sample Rate (Sa/s)": sample_rate,
            "Valid Samples": len(time_values),
            "Raw Data Rows": raw_rows,
            "Invalid Samples Removed": invalid_removed,
            "Linear Interpolation Used": USE_LINEAR_INTERPOLATION,
        }
        results.append(row)
        print(f"Transition = {info['Transition']} ({info['Type']})")
        print(f"Edge time  = {edge_time_s * 1e6:.6f} us")
        print(
            f"Complete pulses found = {calc['Detected Complete Pulse Count']}; "
            f"selected pulse = {calc['Selected Complete Pulse Number']}"
        )
        print(
            f"Boundary partial pulses ignored = "
            f"{calc['Boundary Partial Pulses Ignored']}"
        )
        if PLOT_EVERY_WAVEFORM:
            run_plot_step_nonfatal(
                f"Waveform plot failed for {filename}",
                save_waveform_plot, filename, time_values, raw_voltage, envelope, info, calc
            )
    except Exception as error:
        print(f"ERROR: {error}")
        failures.append({"Filename": filename, "CSV Path": path, "Error": str(error)})

results_df = pd.DataFrame(results)
failures_df = pd.DataFrame(failures)
if results_df.empty:
    failure_file = os.path.join(input_folder, "DLP_Analysis_Failed_Files.xlsx")
    with pd.ExcelWriter(failure_file, engine="openpyxl") as writer:
        failures_df.to_excel(writer, sheet_name="Failed Files", index=False)
        format_workbook(writer)
    progress_window.close()
    root.destroy()
    raise SystemExit(f"No valid DLP files were analyzed. Failure log: {failure_file}")

progress_window.update(64 if ANALYSIS_MODE == "SINGLE" else 44, "Calculating summaries",
                       "Applying IQR filtering and building summary tables")
results_df = results_df.sort_values(GROUP_COLUMNS + ["Acquisition Number", "Filename"])
flagged_df = add_iqr_flags(results_df)
summary_df = create_summary(flagged_df)
outliers_df = flagged_df[~flagged_df["Use In Average"]].copy()
comparison_excel_file = None
comparison_output_folder = None
comparison_results_df = pd.DataFrame()
comparison_failures_df = pd.DataFrame()
comparison_flagged_df = pd.DataFrame()
comparison_summary_df = pd.DataFrame()
comparison_outliers_df = pd.DataFrame()
rise_comparison_df = pd.DataFrame()
fall_comparison_df = pd.DataFrame()
if ANALYSIS_MODE == "COMPARE":
    print("\nAnalyzing Configuration B for DLP comparison...")
    comparison_results_df, comparison_failures_df = process_dlp_comparison_folder(
        comparison_folder, configuration_b_name, progress_window, 45.0, 68.0)
    if not comparison_results_df.empty:
        comparison_results_df = comparison_results_df.sort_values(
            GROUP_COLUMNS + ["Acquisition Number", "Filename"])
        comparison_flagged_df = add_iqr_flags(comparison_results_df)
        comparison_summary_df = create_summary(comparison_flagged_df)
        comparison_outliers_df = comparison_flagged_df[
            ~comparison_flagged_df["Use In Average"]].copy()
    rise_comparison_df = create_dlp_configuration_comparison(
        summary_df, comparison_summary_df, "RISE", configuration_a_name, configuration_b_name)
    fall_comparison_df = create_dlp_configuration_comparison(
        summary_df, comparison_summary_df, "FALL", configuration_a_name, configuration_b_name)
    comparison_output_folder = os.path.join(input_folder, "Hardware Configuration Comparison")
    comparison_plot_folder = os.path.join(comparison_output_folder, "Comparison Plots")
    os.makedirs(comparison_output_folder, exist_ok=True)
    progress_window.update(72, "Generating comparison plots",
                           f"{configuration_a_name} versus {configuration_b_name}")
    run_plot_step_nonfatal(
        "RISE comparison plots could not be completed",
        create_dlp_comparison_plots, rise_comparison_df, "RISE", configuration_a_name,
        configuration_b_name, comparison_plot_folder
    )
    run_plot_step_nonfatal(
        "FALL comparison plots could not be completed",
        create_dlp_comparison_plots, fall_comparison_df, "FALL", configuration_a_name,
        configuration_b_name, comparison_plot_folder
    )
    comparison_excel_file = os.path.join(
        comparison_output_folder, "DLP_Hardware_Configuration_Comparison.xlsx")
    progress_window.update(78, "Writing comparison workbook", comparison_excel_file)
    with pd.ExcelWriter(comparison_excel_file, engine="openpyxl") as comparison_writer:
        if not rise_comparison_df.empty:
            rise_comparison_df.to_excel(comparison_writer, sheet_name="RISE Comparison", index=False)
        if not fall_comparison_df.empty:
            fall_comparison_df.to_excel(comparison_writer, sheet_name="FALL Comparison", index=False)
        ranking = pd.concat([rise_comparison_df, fall_comparison_df], ignore_index=True)
        if not ranking.empty:
            ranking.sort_values(["Absolute Percent Difference (%)", "Absolute Difference (us)"],
                                ascending=[False, False], na_position="last").to_excel(
                comparison_writer, sheet_name="Non-Transparency Ranking", index=False)
        if not comparison_summary_df.empty:
            comparison_summary_df.to_excel(comparison_writer, sheet_name="B DLP Summary", index=False)
        if not comparison_flagged_df.empty:
            comparison_flagged_df.to_excel(comparison_writer, sheet_name="B Individual Results", index=False)
        if not comparison_outliers_df.empty:
            comparison_outliers_df.to_excel(comparison_writer, sheet_name="B Excluded Outliers", index=False)
        if not comparison_failures_df.empty:
            comparison_failures_df.to_excel(comparison_writer, sheet_name="B Failed Files", index=False)
        pd.DataFrame({
            "Item": ["Configuration A", "Configuration A Folder", "Configuration B",
                     "Configuration B Folder", "Matching Conditions", "Difference Convention",
                     "Percent Difference Convention"],
            "Value": [configuration_a_name, input_folder, configuration_b_name, comparison_folder,
                      "Edge, RF frequency, start power, end power, pulse frequency, duty cycle",
                      "Configuration B minus Configuration A", "100 * (B - A) / A"],
        }).to_excel(comparison_writer, sheet_name="Comparison Settings", index=False)
        format_workbook(comparison_writer)
    print(f"DLP comparison workbook saved to: {comparison_excel_file}")

progress_window.update(82, "Writing summary CSV", summary_csv_file)
summary_df.to_csv(summary_csv_file, index=False)
progress_window.update(86, "Generating trend plots", configuration_a_name)
run_plot_step_nonfatal(
    "DLP summary trend plots could not be completed", create_trend_plots, summary_df
)

settings_df = pd.DataFrame({
    "Setting": [
        "Data Start Excel Row", "Low Threshold Fraction", "High Threshold Fraction",
        "Envelope RMS Cycles", "Minimum Samples for IQR", "IQR Multiplier",
        "Plateau Fraction of Short State", "Linear Interpolation",
        "Minimum Complete Pulse Duration Ratio", "Maximum Complete Pulse Duration Ratio",
    ],
    "Value": [
        DATA_START_ROW_INDEX + 1, LOW_THRESHOLD_FRACTION, HIGH_THRESHOLD_FRACTION,
        ENVELOPE_RMS_CYCLES, MIN_SAMPLES_FOR_IQR, IQR_MULTIPLIER,
        PLATEAU_FRACTION_OF_SHORT_STATE, USE_LINEAR_INTERPOLATION,
        MIN_COMPLETE_PULSE_DURATION_RATIO, MAX_COMPLETE_PULSE_DURATION_RATIO,
    ],
})

progress_window.update(92, "Writing Excel workbook", excel_output_file)
with pd.ExcelWriter(excel_output_file, engine="openpyxl") as writer:
    summary_df.to_excel(writer, sheet_name="DLP Summary", index=False)
    rise_summary = summary_df[summary_df["Type"] == "RISE"]
    fall_summary = summary_df[summary_df["Type"] == "FALL"]
    if not rise_summary.empty:
        rise_summary.to_excel(writer, sheet_name="RISE Summary", index=False)
    if not fall_summary.empty:
        fall_summary.to_excel(writer, sheet_name="FALL Summary", index=False)
    flagged_df.to_excel(writer, sheet_name="Individual Results", index=False)
    if not outliers_df.empty:
        outliers_df.to_excel(writer, sheet_name="Excluded Outliers", index=False)
    if not failures_df.empty:
        failures_df.to_excel(writer, sheet_name="Failed Files", index=False)
    settings_df.to_excel(writer, sheet_name="Analysis Settings", index=False)
    format_workbook(writer)

changed_a = changed_png_files(image_state_a, input_folder)
if comparison_output_folder:
    comparison_root=Path(comparison_output_folder).resolve()
    comparison_images=[x for x in changed_a if comparison_root in x.resolve().parents]
    dataset_a_images=[x for x in changed_a if comparison_root not in x.resolve().parents]
else:
    comparison_images,dataset_a_images=[],changed_a
generated_images=[("dataset_a",x) for x in dataset_a_images]
if ANALYSIS_MODE=="COMPARE":
    generated_images.extend(("dataset_b",x) for x in changed_png_files(image_state_b,comparison_folder))
    generated_images.extend(("comparison",x) for x in comparison_images)
package_warning=None
if powerpoint_export_requested():
    try:
        manifest_path=export_powerpoint_package(generated_images,summary_df,comparison_summary_df,rise_comparison_df,fall_comparison_df,comparison_excel_file)
        if manifest_path:print(f"PowerPoint package manifest saved to: {manifest_path}")
    except Exception as error:
        package_warning=f"PowerPoint package export warning: {error}";print(package_warning,file=sys.stderr)

progress_window.update(100, "Analysis complete", "All available outputs have been generated")
print("\nDLP analysis complete.")
print(f"Valid files: {len(flagged_df)}")
print(f"Rise files: {int((flagged_df['Type'] == 'RISE').sum())}")
print(f"Fall files: {int((flagged_df['Type'] == 'FALL').sum())}")
print(f"Excluded outliers: {len(outliers_df)}")
print(f"Failed files: {len(failures_df)}")
print(f"Excel workbook: {excel_output_file}")
print(f"Summary CSV: {summary_csv_file}")
if comparison_excel_file:
    print(f"Comparison workbook: {comparison_excel_file}")

if OPEN_EXCEL_WHEN_COMPLETE:
    open_output(excel_output_file)
progress_window.close()
messagebox.showinfo(
    "DLP analysis complete",
    f"Valid files: {len(flagged_df)}\n"
    f"Excluded outliers: {len(outliers_df)}\n"
    f"Failed files: {len(failures_df)}\n\n"
    f"Workbook:\n{excel_output_file}" +
    (f"\n\nComparison workbook:\n{comparison_excel_file}" if comparison_excel_file else "") +
    (f"\n\n{package_warning}\nNormal analysis outputs were still completed." if package_warning else ""),
    parent=root,
)
root.destroy()
print("Script finished.")
