import json
import os
import re
import shutil
import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.powerpoint import powerpoint_export_requested, snapshot_png_files, changed_png_files

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

# ==========================================================
# SCRIPT START
# ==========================================================

print("Pulse-mode rise/fall analysis script started.")

# ==========================================================
# USER SETTINGS
# ==========================================================

# Data begins at Excel row 15 in the scope CSV export.
# Python uses zero-based indexing, so row 15 is index 14.
DATA_START_ROW_INDEX = 14
TIME_COLUMN_INDEX = 0
VOLTAGE_COLUMN_INDEX = 1

# Rise and fall thresholds.
LOW_THRESHOLD_FRACTION = 0.10
HIGH_THRESHOLD_FRACTION = 0.90

# IQR outlier filtering settings.
MIN_SAMPLES_FOR_IQR = 4
IQR_MULTIPLIER = 1.5

# Set True to use linear interpolation between samples for threshold times.
# This generally gives a better result than selecting the nearest sample.
USE_LINEAR_INTERPOLATION = True

# Set True to subtract a baseline estimate before calculating thresholds.
# The baseline is estimated from the lowest 10 percent of voltage samples.
# This is useful when the RF envelope has a nonzero DC offset.
SUBTRACT_BASELINE = True

# The first 200 valid waveform samples are known to be in the Pulse Off state.
# Their median is used as the low-state baseline. A median is used instead of
# a mean so isolated noise points have less influence on the baseline.
BASELINE_OFF_STATE_SAMPLE_COUNT = 200

# Power setpoints at or below this value use the low-power 10 percent rule.
LOW_POWER_THRESHOLD_W = 30.0

# For low-power cases, the maximum of the first Pulse Off samples defines the
# top of the measured noise floor. The 10 percent cursor is placed 10 percent
# of the way from this noise-floor maximum to the waveform maximum.
USE_LOW_POWER_NOISE_FLOOR_THRESHOLD = True

# Set True to use signal amplitude from baseline to maximum.
# Thresholds become baseline + 10 percent and baseline + 90 percent
# of the signal span.
USE_BASELINE_REFERENCED_THRESHOLDS = True

# Excel workbook name.
EXCEL_FILENAME = "Pulse_Mode_Rise_Fall_Analysis.xlsx"
FREQUENCY_MIN_MAX_CSV_FILENAME = "Pulse_Mode_Frequency_Min_Max.csv"
POWERPOINT_FILENAME = "Pulse_Mode_Rise_Fall_Summary.pptx"

# Number of decimal places shown for every time value in the PowerPoint table.
# All PowerPoint table times are always displayed in microseconds (us).
POWERPOINT_TABLE_DECIMAL_PLACES = 3

# Optional RMS-envelope/complete-pulse analysis settings. The ORIGINAL method
# below remains the existing algorithm and is not modified.
ANALYSIS_METHOD_ORIGINAL = "ORIGINAL"
ANALYSIS_METHOD_RMS_COMPLETE_PULSE = "RMS_COMPLETE_PULSE"
ENVELOPE_RMS_CYCLES = 8.0
MIN_ENVELOPE_WINDOW_SAMPLES = 5
MAX_ENVELOPE_WINDOW_SAMPLES = 5001
PLATEAU_FRACTION_OF_SHORT_STATE = 0.30
MIN_PLATEAU_SAMPLES = 20
EDGE_SEARCH_MARGIN_FRACTION = 0.02
MIN_STATE_SEPARATION_RATIO = 0.03
MIN_COMPLETE_PULSE_DURATION_RATIO = 0.50
MAX_COMPLETE_PULSE_DURATION_RATIO = 1.50
COMPLETE_PULSE_CENTER_WEIGHT = 0.25

PLOT_MODE_ALL = "ALL"
PLOT_MODE_SUMMARY = "SUMMARY"
PLOT_MODE_NONE = "NONE"


def select_plot_mode(parent):
    """Display a reliable modal three-option plot-mode dialog."""
    selected = {"mode": PLOT_MODE_NONE}
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title("Plot Options")
    dialog.resizable(False, False)

    frame = tk.Frame(dialog, padx=24, pady=18)
    frame.pack(fill="both", expand=True)
    tk.Label(
        frame,
        text="Select the waveform plot mode:",
        font=("Segoe UI", 11, "bold")
    ).pack(pady=(0, 14))
    tk.Label(
        frame,
        text=(
            "Choose whether to save every waveform, one representative summary "
            "plot per test-setting group, or no plots."
        ),
        wraplength=420,
        justify="left"
    ).pack(pady=(0, 14))

    def choose(mode):
        selected["mode"] = mode
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    tk.Button(frame, text="Plot every CSV", width=48,
              command=lambda: choose(PLOT_MODE_ALL)).pack(pady=4)
    tk.Button(frame, text="One summary plot per test-setting group", width=48,
              command=lambda: choose(PLOT_MODE_SUMMARY), default="active").pack(pady=4)
    tk.Button(frame, text="No plots (Excel summary only)", width=48,
              command=lambda: choose(PLOT_MODE_NONE)).pack(pady=4)

    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(PLOT_MODE_NONE))
    dialog.update_idletasks()
    width = dialog.winfo_reqwidth()
    height = dialog.winfo_reqheight()
    x = max(0, (dialog.winfo_screenwidth() - width) // 2)
    y = max(0, (dialog.winfo_screenheight() - height) // 2)
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    dialog.deiconify()
    dialog.lift()
    dialog.attributes("-topmost", True)
    dialog.focus_force()
    dialog.grab_set()
    dialog.after(500, lambda: dialog.attributes("-topmost", False))
    parent.wait_window(dialog)
    return selected["mode"]

def select_summary_sample_info(parent):
    """Ask whether summary plots should show sample and outlier counts."""
    selected = {"show": True}
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title("Summary Plot Information")
    dialog.resizable(False, False)

    frame = tk.Frame(dialog, padx=24, pady=18)
    frame.pack(fill="both", expand=True)
    tk.Label(
        frame,
        text="Show sample-count information?",
        font=("Segoe UI", 11, "bold")
    ).pack(pady=(0, 12))
    tk.Label(
        frame,
        text=(
            "Choose whether each summary waveform plot should display "
            "the 'Samples used' and 'Outliers excluded' lines."
        ),
        wraplength=420,
        justify="left"
    ).pack(pady=(0, 14))

    def choose(show):
        selected["show"] = bool(show)
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    tk.Button(
        frame, text="Show samples used and outliers excluded", width=48,
        command=lambda: choose(True), default="active"
    ).pack(pady=4)
    tk.Button(
        frame, text="Hide samples used and outliers excluded", width=48,
        command=lambda: choose(False)
    ).pack(pady=4)

    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(True))
    dialog.update_idletasks()
    width = dialog.winfo_reqwidth()
    height = dialog.winfo_reqheight()
    x = max(0, (dialog.winfo_screenwidth() - width) // 2)
    y = max(0, (dialog.winfo_screenheight() - height) // 2)
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    dialog.deiconify()
    dialog.lift()
    dialog.attributes("-topmost", True)
    dialog.focus_force()
    dialog.grab_set()
    dialog.after(500, lambda: dialog.attributes("-topmost", False))
    parent.wait_window(dialog)
    return selected["show"]


# ==========================================================
# OPTIONAL MANUAL EDGE-TIME VERTICAL SCALES
# ==========================================================

def select_edge_time_vertical_scales(parent):
    """Choose automatic or manual RISE/FALL edge-time limits in us."""
    result = {"RISE": None, "FALL": None}
    if not messagebox.askyesno(
        "Vertical Scale Options",
        "Manually set the RISE and FALL edge-time vertical scales?\n\n"
        "Choose No to retain automatic scaling.",
        parent=parent,
    ):
        return result

    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title("Manual Edge-Time Vertical Scales")
    dialog.resizable(False, False)
    frame = tk.Frame(dialog, padx=22, pady=18)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="Enter limits in microseconds (us)",
             font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=3, pady=(0, 6))
    tk.Label(frame, text="Leave both fields blank for automatic scaling.").grid(
        row=1, column=0, columnspan=3, pady=(0, 14))
    tk.Label(frame, text="Edge").grid(row=2, column=0, padx=6)
    tk.Label(frame, text="Minimum (us)").grid(row=2, column=1, padx=6)
    tk.Label(frame, text="Maximum (us)").grid(row=2, column=2, padx=6)
    entries = {}
    for row, edge in enumerate(("RISE", "FALL"), start=3):
        tk.Label(frame, text=edge).grid(row=row, column=0, sticky="e", padx=6, pady=4)
        min_entry, max_entry = tk.Entry(frame, width=16), tk.Entry(frame, width=16)
        min_entry.grid(row=row, column=1, padx=6, pady=4)
        max_entry.grid(row=row, column=2, padx=6, pady=4)
        entries[edge] = (min_entry, max_entry)

    def apply_values():
        parsed = {}
        try:
            for edge, (min_entry, max_entry) in entries.items():
                min_text, max_text = min_entry.get().strip(), max_entry.get().strip()
                if not min_text and not max_text:
                    parsed[edge] = None
                    continue
                if not min_text or not max_text:
                    raise ValueError(f"Enter both {edge} limits or leave both blank.")
                minimum, maximum = float(min_text), float(max_text)
                if not np.isfinite(minimum) or not np.isfinite(maximum):
                    raise ValueError(f"{edge} limits must be finite numbers.")
                if minimum >= maximum:
                    raise ValueError(f"{edge} minimum must be less than maximum.")
                parsed[edge] = (minimum, maximum)
        except ValueError as error:
            messagebox.showerror("Invalid Vertical Scale", str(error), parent=dialog)
            return
        result.update(parsed)
        dialog.destroy()

    def use_auto():
        dialog.destroy()

    buttons = tk.Frame(frame)
    buttons.grid(row=5, column=0, columnspan=3, pady=(14, 0))
    tk.Button(buttons, text="Apply", width=15, command=apply_values).pack(side="left", padx=5)
    tk.Button(buttons, text="Use Automatic", width=15, command=use_auto).pack(side="left", padx=5)
    dialog.protocol("WM_DELETE_WINDOW", use_auto)
    dialog.update_idletasks()
    x = max(0, (dialog.winfo_screenwidth() - dialog.winfo_reqwidth()) // 2)
    y = max(0, (dialog.winfo_screenheight() - dialog.winfo_reqheight()) // 2)
    dialog.geometry(f"+{x}+{y}")
    dialog.deiconify(); dialog.lift(); dialog.grab_set()
    parent.wait_window(dialog)
    return result


def apply_manual_edge_time_ylim(axis, edge_type):
    limits = EDGE_TIME_VERTICAL_LIMITS_US.get(edge_type.upper())
    if limits is None:
        return False
    axis.set_yscale("linear")
    axis.set_ylim(*limits)
    return True


# ==========================================================
# ANALYSIS MODE
# ==========================================================

def select_analysis_mode(parent):
    """Choose the original single-data-set workflow or two-data-set comparison."""
    choice = messagebox.askyesnocancel(
        "Analysis Mode",
        "Analyze two hardware configurations?\n\n"
        "Yes: select two data folders and generate comparison outputs.\n"
        "No: run the original single-folder analysis.\n"
        "Cancel: stop the script.",
        parent=parent
    )
    if choice is None:
        return None
    return "COMPARE" if choice else "SINGLE"


# ==========================================================
# WAVEFORM ANALYSIS METHOD
# ==========================================================
def select_waveform_analysis_method(parent):
    """Show explicit named choices for the waveform timing algorithm."""
    selection = {"method": None}

    dialog = tk.Toplevel(parent)
    dialog.title("Select Waveform Analysis Method")
    dialog.resizable(False, False)

    # The main Tk root is intentionally withdrawn. Making this dialog transient
    # to a withdrawn parent can leave it hidden or behind other applications on
    # some Windows/Tk versions, so only apply transient behavior when the parent
    # is actually viewable.
    if parent.winfo_viewable():
        dialog.transient(parent)

    container = ttk.Frame(dialog, padding=18)
    container.pack(fill="both", expand=True)

    ttk.Label(
        container,
        text="Select the waveform analysis method",
        font=("Segoe UI", 13, "bold"),
    ).pack(anchor="w", pady=(0, 8))
    ttk.Label(
        container,
        text=(
            "The selected method applies to all processed waveforms. "
            "All existing summaries, plots, Excel outputs, PowerPoint outputs, "
            "IQR filtering, and comparison features remain available."
        ),
        wraplength=610,
        justify="left",
    ).pack(anchor="w", pady=(0, 14))

    original_frame = ttk.LabelFrame(
        container,
        text="Original Pulse-Mode Method",
        padding=12,
    )
    original_frame.pack(fill="x", pady=(0, 10))
    ttk.Label(
        original_frame,
        text=(
            "Uses the script's existing baseline, threshold, and crossing-selection "
            "algorithm unchanged. Choose this option for results directly comparable "
            "to previous runs of the original script."
        ),
        wraplength=575,
        justify="left",
    ).pack(anchor="w", pady=(0, 8))

    rms_frame = ttk.LabelFrame(
        container,
        text="RMS Envelope + Complete-Pulse Method",
        padding=12,
    )
    rms_frame.pack(fill="x", pady=(0, 14))
    ttk.Label(
        rms_frame,
        text=(
            "Builds a centered moving-RMS peak-equivalent envelope, collects all "
            "candidate edges, validates plateaus and companion edges, rejects partial "
            "boundary pulses, and measures the best complete pulse."
        ),
        wraplength=575,
        justify="left",
    ).pack(anchor="w", pady=(0, 8))

    def choose(method):
        selection["method"] = method
        dialog.destroy()

    ttk.Button(
        original_frame,
        text="Use Original Method",
        command=lambda: choose(ANALYSIS_METHOD_ORIGINAL),
        width=30,
    ).pack(anchor="e")
    ttk.Button(
        rms_frame,
        text="Use RMS Complete-Pulse Method",
        command=lambda: choose(ANALYSIS_METHOD_RMS_COMPLETE_PULSE),
        width=30,
    ).pack(anchor="e")
    ttk.Button(
        container,
        text="Cancel Analysis",
        command=dialog.destroy,
        width=20,
    ).pack(anchor="e")

    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.update_idletasks()

    # Center against the physical screen rather than the withdrawn root.
    dialog_width = dialog.winfo_reqwidth()
    dialog_height = dialog.winfo_reqheight()
    x_position = max(0, (dialog.winfo_screenwidth() - dialog_width) // 2)
    y_position = max(0, (dialog.winfo_screenheight() - dialog_height) // 2)
    dialog.geometry(f"{dialog_width}x{dialog_height}+{x_position}+{y_position}")

    # Explicitly surface and focus the modal selector. The temporary topmost
    # flag prevents the window from opening behind an IDE or console.
    dialog.deiconify()
    dialog.lift()
    try:
        dialog.attributes("-topmost", True)
        dialog.after(300, lambda: dialog.attributes("-topmost", False))
    except tk.TclError:
        pass
    dialog.focus_force()
    dialog.grab_set()
    parent.wait_window(dialog)
    return selection["method"]


def export_powerpoint_package(images,rise_a,fall_a,rise_b,fall_b,rise_comp,fall_comp,comparison_excel):
    if not powerpoint_export_requested(): return None
    text=os.environ.get("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER","").strip()
    if not text: raise ValueError("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER is not set.")
    package=Path(text).expanduser().resolve();package.mkdir(parents=True,exist_ok=True);manifest=package/"manifest.json"
    if manifest.exists():manifest.unlink()
    ir=package/"images";tr=package/"tables"
    if ir.exists():shutil.rmtree(ir)
    if tr.exists():shutil.rmtree(tr)
    tr.mkdir(parents=True,exist_ok=True)
    for frame,name in ((rise_a,"dataset_a_rise_summary.csv"),(fall_a,"dataset_a_fall_summary.csv"),(rise_b,"dataset_b_rise_summary.csv"),(fall_b,"dataset_b_fall_summary.csv"),(rise_comp,"rise_comparison_b_minus_a.csv"),(fall_comp,"fall_comparison_b_minus_a.csv")):
        if frame is not None and not frame.empty:frame.to_csv(tr/name,index=False)
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
    payload={"schema_version":int(os.environ.get("PARAMOUNT_POWERPOINT_SCHEMA_VERSION","1")),"analysis_id":os.environ.get("PARAMOUNT_POWERPOINT_ANALYSIS_ID","rise_fall_slp").strip(),"display_name":"SLP Rise/Fall Timing","status":"completed","analysis_mode":ANALYSIS_MODE,"dataset_label":configuration_a_name if ANALYSIS_MODE=="SINGLE" else f"{configuration_b_name} vs {configuration_a_name}","source_folder":input_folder,"dataset_a":{"label":configuration_a_name,"source_folder":input_folder,"workbook":excel_output_file},"dataset_b":db,"comparison":comp,"highlights":[f"Dataset A: {configuration_a_name}",f"Generated {len(slides)} image slide(s) from this analysis run."],"metrics":{"Analysis mode":ANALYSIS_MODE,"Generated image slides":len(slides)},"generated_images":copied,"slides":slides}
    manifest.write_text(json.dumps(payload,indent=2),encoding="utf-8");return manifest

# ==========================================================
# UNIFIED SLP ANALYSIS CONFIGURATION
# ==========================================================
def configure_slp_analysis_with_gui():
    """Collect all pre-analysis SLP choices in one tabbed window."""
    root = tk.Tk()
    root.title("SLP Statistical Timing Data Analysis")
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
    ttk.Label(outer, text="SLP Statistical Timing Data Analysis", style="Header.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Select one or two SLP collection folders and configure the waveform timing method, "
            "plot behavior, manual edge-time scales, comparison labels, and outputs before analysis."
        ),
        wraplength=980,
    ).pack(anchor="w", pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    input_tab = ttk.Frame(notebook, padding=12)
    method_tab = ttk.Frame(notebook, padding=12)
    waveform_tab = ttk.Frame(notebook, padding=12)
    scales_tab = ttk.Frame(notebook, padding=12)
    comparison_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(input_tab, text="Input Data")
    notebook.add(method_tab, text="Analysis Method")
    notebook.add(waveform_tab, text="Waveform Plots")
    notebook.add(scales_tab, text="Trend Scales")
    notebook.add(comparison_tab, text="Comparison")
    notebook.add(output_tab, text="Output Summary")

    launcher_mode = os.environ.get("PARAMOUNT_ANALYSIS_MODE", "INDIVIDUAL").strip().upper()
    initial_mode = launcher_mode if launcher_mode in {"SINGLE", "COMPARE"} else "SINGLE"
    mode = tk.StringVar(value=initial_mode)
    folder_a = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_FOLDER", "").strip())
    folder_b = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_FOLDER", "").strip())
    name_a = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_LABEL", "").strip() or "Configuration A")
    name_b = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_LABEL", "").strip() or "Configuration B")
    analysis_method = tk.StringVar(value=ANALYSIS_METHOD_ORIGINAL)
    plot_mode = tk.StringVar(value=PLOT_MODE_SUMMARY)
    show_summary_info = tk.BooleanVar(value=True)
    use_manual_scales = tk.BooleanVar(value=False)
    rise_min = tk.StringVar(); rise_max = tk.StringVar()
    fall_min = tk.StringVar(); fall_max = tk.StringVar()
    discovery_text = tk.StringVar(value="Select an SLP collection folder to preview discovered CSV files.")
    output_preview = tk.StringVar()

    ttk.Label(input_tab, text="Analysis mode", style="Section.TLabel").grid(row=0,column=0,sticky="w",pady=(0,6))
    mode_frame=ttk.Frame(input_tab); mode_frame.grid(row=1,column=0,columnspan=4,sticky="w",pady=(0,12))
    ttk.Radiobutton(mode_frame,text="Single dataset",variable=mode,value="SINGLE").pack(side="left",padx=(0,20))
    ttk.Radiobutton(mode_frame,text="Compare two hardware configurations",variable=mode,value="COMPARE").pack(side="left")
    input_tab.columnconfigure(1,weight=1)
    ttk.Label(input_tab,text="Configuration A folder").grid(row=2,column=0,sticky="w",pady=5)
    ttk.Entry(input_tab,textvariable=folder_a,width=65).grid(row=2,column=1,sticky="ew",pady=5)
    ttk.Label(input_tab,text="Display name").grid(row=3,column=0,sticky="w",pady=5)
    ttk.Entry(input_tab,textvariable=name_a,width=35).grid(row=3,column=1,sticky="w",pady=5)
    ttk.Label(input_tab,text="Configuration B folder").grid(row=4,column=0,sticky="w",pady=5)
    folder_b_entry=ttk.Entry(input_tab,textvariable=folder_b,width=65); folder_b_entry.grid(row=4,column=1,sticky="ew",pady=5)
    ttk.Label(input_tab,text="Display name").grid(row=5,column=0,sticky="w",pady=5)
    name_b_entry=ttk.Entry(input_tab,textvariable=name_b,width=35); name_b_entry.grid(row=5,column=1,sticky="w",pady=5)

    def preview_folder(path_text):
        folder=Path(path_text.strip()) if path_text.strip() else None
        if folder is None or not folder.is_dir(): return "Folder not selected or does not exist."
        files=sorted(folder.glob("*.csv")); rise=fall=invalid=0
        rf=set(); pulse=set(); duty=set(); power=set()
        for item in files:
            try:
                info=extract_file_info(item.name)
                rise += info["Type"]=="RISE"; fall += info["Type"]=="FALL"
                rf.add(info["RF Frequency (kHz)"]); pulse.add(info["Pulse Frequency (Hz)"])
                duty.add(info["Duty Cycle (%)"]); power.add(info["Power Setpoint (W)"])
            except Exception: invalid += 1
        return (
            f"CSV files: {len(files)} | RISE: {rise} | FALL: {fall} | Unrecognized: {invalid}\n"
            f"RF frequencies (kHz): {', '.join(f'{v:g}' for v in sorted(rf)) or 'None'}\n"
            f"Power setpoints (W): {', '.join(f'{v:g}' for v in sorted(power)) or 'None'}\n"
            f"Pulse frequencies (Hz): {', '.join(f'{v:g}' for v in sorted(pulse)) or 'None'}\n"
            f"Duty cycles (%): {', '.join(f'{v:g}' for v in sorted(duty)) or 'None'}"
        )

    def browse_folder(variable,label_variable,preview=False):
        current=variable.get().strip()
        selected=filedialog.askdirectory(title="Select SLP waveform CSV folder",parent=root,mustexist=True,
                                         initialdir=current if current and Path(current).is_dir() else None)
        if selected:
            variable.set(selected); label_variable.set(os.path.basename(os.path.normpath(selected)) or label_variable.get())
            if preview: discovery_text.set(preview_folder(selected))
    ttk.Button(input_tab,text="Browse...",command=lambda:browse_folder(folder_a,name_a,True)).grid(row=2,column=2,padx=(10,0))
    folder_b_button=ttk.Button(input_tab,text="Browse...",command=lambda:browse_folder(folder_b,name_b,False)); folder_b_button.grid(row=4,column=2,padx=(10,0))
    ttk.Label(input_tab,textvariable=discovery_text,foreground="#444444",wraplength=900,justify="left").grid(row=6,column=0,columnspan=3,sticky="w",pady=(16,0))

    ttk.Label(method_tab,text="Waveform timing algorithm",style="Section.TLabel").pack(anchor="w",pady=(0,8))
    original_frame=ttk.LabelFrame(method_tab,text="Original Pulse-Mode Method",padding=12); original_frame.pack(fill="x",pady=(0,10))
    ttk.Radiobutton(original_frame,text="Use original baseline, threshold, and crossing-selection algorithm",variable=analysis_method,value=ANALYSIS_METHOD_ORIGINAL).pack(anchor="w")
    ttk.Label(original_frame,text="Best for direct comparison with previous runs of the original SLP analyzer.",foreground="#555555",wraplength=820).pack(anchor="w",pady=(6,0))
    rms_frame=ttk.LabelFrame(method_tab,text="RMS Envelope + Complete-Pulse Method",padding=12); rms_frame.pack(fill="x")
    ttk.Radiobutton(rms_frame,text="Use centered RMS envelope and validate a complete OFF-ON-OFF pulse",variable=analysis_method,value=ANALYSIS_METHOD_RMS_COMPLETE_PULSE).pack(anchor="w")
    ttk.Label(rms_frame,text="Collects candidate edges, validates plateaus and companion edges, rejects boundary partial pulses, and measures the best complete pulse.",foreground="#555555",wraplength=820).pack(anchor="w",pady=(6,0))

    ttk.Label(waveform_tab,text="Waveform plot mode",style="Section.TLabel").pack(anchor="w",pady=(0,8))
    for label,value in (("Plot every CSV",PLOT_MODE_ALL),("One representative summary plot per complete test-setting group",PLOT_MODE_SUMMARY),("No waveform plots; Excel, PowerPoint, and trend outputs only",PLOT_MODE_NONE)):
        ttk.Radiobutton(waveform_tab,text=label,variable=plot_mode,value=value).pack(anchor="w",pady=4)
    sample_info_check=ttk.Checkbutton(waveform_tab,text="Include both 'Samples used' and 'Outliers excluded' lines on summary plots",variable=show_summary_info)
    sample_info_check.pack(anchor="w",pady=(18,5))
    ttk.Label(waveform_tab,text="The paired sample/outlier option applies only to summary waveform plots.",foreground="#555555").pack(anchor="w")

    manual_check=ttk.Checkbutton(scales_tab,text="Use manual RISE/FALL edge-time vertical limits (microseconds)",variable=use_manual_scales)
    manual_check.grid(row=0,column=0,columnspan=3,sticky="w",pady=(0,10))
    ttk.Label(scales_tab,text="Edge").grid(row=1,column=0,sticky="w"); ttk.Label(scales_tab,text="Minimum (us)").grid(row=1,column=1,sticky="w"); ttk.Label(scales_tab,text="Maximum (us)").grid(row=1,column=2,sticky="w")
    scale_entries=[]
    for row,edge,lo,hi in ((2,"RISE",rise_min,rise_max),(3,"FALL",fall_min,fall_max)):
        ttk.Label(scales_tab,text=edge).grid(row=row,column=0,sticky="w",pady=5)
        e1=ttk.Entry(scales_tab,textvariable=lo,width=18);e1.grid(row=row,column=1,sticky="w",pady=5)
        e2=ttk.Entry(scales_tab,textvariable=hi,width=18);e2.grid(row=row,column=2,sticky="w",pady=5)
        scale_entries.extend([e1,e2])
    ttk.Label(scales_tab,text="Automatic scaling is retained when manual limits are disabled.",foreground="#555555").grid(row=4,column=0,columnspan=3,sticky="w",pady=(12,0))

    ttk.Label(comparison_tab,text="Comparison outputs",style="Section.TLabel").pack(anchor="w",pady=(0,8))
    comparison_controls=[]
    for text in ("Configuration B minus Configuration A", "Percent difference = 100 x (B - A) / A", "Matched-condition RISE and FALL workbooks", "Non-transparency ranking", "Overlay and B-minus-A plots"):
        label=ttk.Label(comparison_tab,text=text);label.pack(anchor="w",pady=3);comparison_controls.append(label)
    ttk.Label(comparison_tab,text="Configuration B controls on the Input Data tab are enabled only in comparison mode.",foreground="#555555",wraplength=820).pack(anchor="w",pady=(14,0))

    ttk.Label(output_tab,text="Outputs generated by the existing analysis engine",style="Section.TLabel").pack(anchor="w")
    ttk.Label(output_tab,text=(
        "Configuration A retains its current outputs: Pulse_Mode_Rise_Fall_Analysis.xlsx, "
        "Pulse_Mode_Frequency_Min_Max.csv, Pulse_Mode_Rise_Fall_Summary.pptx, waveform plots "
        "according to the selected mode, RISE/FALL trend plots, outlier sheets, failed-file details, "
        "and analysis settings. Comparison mode also creates Hardware Configuration Comparison outputs."
    ),wraplength=880,justify="left").pack(anchor="w",pady=(8,16))
    ttk.Label(output_tab,textvariable=output_preview,foreground="#333333",wraplength=870,justify="left").pack(anchor="w")

    result={"accepted":False}
    def parse_pair(lo_var,hi_var,edge):
        if not use_manual_scales.get(): return None
        lo_text=lo_var.get().strip(); hi_text=hi_var.get().strip()
        if not lo_text or not hi_text: raise ValueError(f"Enter both {edge} limits when manual scaling is enabled.")
        lo=float(lo_text); hi=float(hi_text)
        if not np.isfinite(lo) or not np.isfinite(hi): raise ValueError(f"{edge} limits must be finite.")
        if lo>=hi: raise ValueError(f"{edge} minimum must be less than maximum.")
        return (lo,hi)
    def refresh(*_):
        compare=mode.get()=="COMPARE"; state="normal" if compare else "disabled"
        folder_b_entry.configure(state=state);name_b_entry.configure(state=state);folder_b_button.configure(state=state)
        sample_info_check.configure(state="normal" if plot_mode.get()==PLOT_MODE_SUMMARY else "disabled")
        scale_state="normal" if use_manual_scales.get() else "disabled"
        for entry in scale_entries: entry.configure(state=scale_state)
        output_preview.set(
            f"Mode: {'Two-dataset comparison' if compare else 'Single dataset'}\n"
            f"Analysis method: {analysis_method.get()}\nWaveform plots: {plot_mode.get()}\n"
            f"PowerPoint table times: microseconds, {POWERPOINT_TABLE_DECIMAL_PLACES} decimal places"
        )
    for variable in (mode,analysis_method,plot_mode,use_manual_scales): variable.trace_add("write",refresh)
    def start_analysis():
        try:
            a=Path(folder_a.get().strip()).resolve()
            if not a.is_dir(): raise ValueError("Select an existing Configuration A SLP folder.")
            if not any(a.glob("*.csv")): raise ValueError("Configuration A contains no CSV files.")
            recognized=sum(1 for item in a.glob("*.csv") if _filename_is_valid(item.name))
            if recognized==0: raise ValueError("No Configuration A filenames match the expected SLP format.")
            b=None
            if mode.get()=="COMPARE":
                b=Path(folder_b.get().strip()).resolve()
                if not b.is_dir(): raise ValueError("Select an existing Configuration B SLP folder.")
                if not any(b.glob("*.csv")): raise ValueError("Configuration B contains no CSV files.")
                if os.path.normcase(str(a))==os.path.normcase(str(b)): raise ValueError("Select two different folders.")
            result.update({"accepted":True,"root":root,"mode":mode.get(),"analysis_method":analysis_method.get(),
                "folder_a":str(a),"folder_b":str(b) if b else None,
                "name_a":name_a.get().strip() or a.name or "Configuration A",
                "name_b":name_b.get().strip() or (b.name if b else "Configuration B"),
                "plot_mode":plot_mode.get(),"show_summary_info":bool(show_summary_info.get()),
                "vertical_limits":{"RISE":parse_pair(rise_min,rise_max,"RISE"),"FALL":parse_pair(fall_min,fall_max,"FALL")}})
            # Hide the setup window and exit its mainloop so analysis can begin.
            root.withdraw()
            root.update_idletasks()
            root.quit()
        except Exception as error: messagebox.showerror("Invalid SLP analysis settings",str(error),parent=root)
    buttons=ttk.Frame(outer);buttons.pack(fill="x",pady=(12,0))
    ttk.Button(buttons,text="Cancel",command=root.destroy).pack(side="right",padx=(8,0))
    ttk.Button(buttons,text="Start Analysis",style="Start.TButton",command=start_analysis).pack(side="right")
    root.protocol("WM_DELETE_WINDOW",root.destroy);refresh();root.mainloop();return result


def _filename_is_valid(filename):
    try:
        extract_file_info(filename)
        return True
    except Exception:
        return False



# ==========================================================
# FILENAME PARSING
# ==========================================================

def extract_file_info(filename):
    """
    Extracts test settings from filenames generated by the pulse capture script.

    Expected format:

        New_RF_Freq_RISE13560_Power1000W_PulseFreq1000Hz_
        Duty50pct_Acq1_20260807_120000.csv

    FALL is also accepted.

    The parser permits additional text before New_RF and does not depend on
    the exact timestamp format after the acquisition number.
    """

    name = os.path.splitext(os.path.basename(filename))[0]

    pattern = re.compile(
        r"RF_Freq_(RISE|FALL)"
        r"(?P<rf_freq>\d+(?:\.\d+)?)_"
        r"Power(?P<power>\d+(?:\.\d+)?)W_"
        r"PulseFreq(?P<pulse_freq>\d+(?:\.\d+)?)Hz_"
        r"Duty(?P<duty>\d+(?:\.\d+)?)pct"
        r"(?:_Acq(?P<acq>\d+))?",
        re.IGNORECASE
    )

    match = pattern.search(name)

    if not match:
        raise ValueError(
            "Filename does not match the expected pulse-mode format. "
            "Expected fields include RF_Freq_RISE or RF_Freq_FALL, "
            "Power, PulseFreq, and Duty."
        )

    edge_type = match.group(1).upper()
    rf_frequency_khz = float(match.group("rf_freq"))
    power_setpoint_w = float(match.group("power"))
    pulse_frequency_hz = float(match.group("pulse_freq"))
    duty_cycle_percent = float(match.group("duty"))

    acquisition_text = match.group("acq")
    acquisition_number = int(acquisition_text) if acquisition_text else np.nan

    return {
        "Type": edge_type,
        "RF Frequency (kHz)": rf_frequency_khz,
        "RF Frequency (MHz)": rf_frequency_khz / 1000.0,
        "Power Setpoint (W)": power_setpoint_w,
        "Pulse Frequency (Hz)": pulse_frequency_hz,
        "Pulse Frequency (kHz)": pulse_frequency_hz / 1000.0,
        "Duty Cycle (%)": duty_cycle_percent,
        "Acquisition Number": acquisition_number
    }


configuration = configure_slp_analysis_with_gui()
if not configuration.get("accepted"):
    raise SystemExit("SLP analysis cancelled before processing.")
root = configuration["root"]
ANALYSIS_MODE = configuration["mode"]
WAVEFORM_ANALYSIS_METHOD = configuration["analysis_method"]
input_folder = configuration["folder_a"]
comparison_folder = configuration["folder_b"]
configuration_a_name = configuration["name_a"]
configuration_b_name = configuration["name_b"]
PLOT_MODE = configuration["plot_mode"]
EDGE_TIME_VERTICAL_LIMITS_US = configuration["vertical_limits"]
GENERATE_PLOTS = PLOT_MODE == PLOT_MODE_ALL
GENERATE_SUMMARY_PLOTS = PLOT_MODE == PLOT_MODE_SUMMARY
SHOW_SUMMARY_SAMPLE_INFO = configuration["show_summary_info"]
print(f"Selected waveform analysis method: {WAVEFORM_ANALYSIS_METHOD}", flush=True)
print(f"Selected folder: {input_folder}")
if comparison_folder:
    print(f"Selected comparison folder: {comparison_folder}")
print(f"Selected plot mode: {PLOT_MODE}", flush=True)

output_folder = os.path.join(input_folder, "Modified Plots")
excel_output_file = os.path.join(input_folder, EXCEL_FILENAME)
powerpoint_output_file = os.path.join(input_folder, POWERPOINT_FILENAME)
frequency_min_max_csv_file = os.path.join(input_folder, FREQUENCY_MIN_MAX_CSV_FILENAME)
individual_plots_folder = os.path.join(output_folder, "Individual Plots")
if GENERATE_PLOTS:
    os.makedirs(individual_plots_folder, exist_ok=True)
if GENERATE_SUMMARY_PLOTS:
    os.makedirs(output_folder, exist_ok=True)
image_state_a = snapshot_png_files(input_folder)
image_state_b = snapshot_png_files(comparison_folder) if ANALYSIS_MODE == "COMPARE" else {}

# ==========================================================
# CSV READING
# ==========================================================

def read_scope_csv(csv_path):
    """
    Reads time from column A and voltage from column B.
    Data is expected to begin at Excel row 15.
    """

    dataframe = pd.read_csv(csv_path)

    if dataframe.shape[1] < 2:
        raise ValueError("CSV file has fewer than two columns.")

    time_raw = pd.to_numeric(
        dataframe.iloc[DATA_START_ROW_INDEX:, TIME_COLUMN_INDEX],
        errors="coerce"
    ).to_numpy(dtype=float)

    voltage_raw = pd.to_numeric(
        dataframe.iloc[DATA_START_ROW_INDEX:, VOLTAGE_COLUMN_INDEX],
        errors="coerce"
    ).to_numpy(dtype=float)

    valid_mask = np.isfinite(time_raw) & np.isfinite(voltage_raw)
    time_values = time_raw[valid_mask]
    voltage_values = voltage_raw[valid_mask]

    if len(time_values) < 2:
        raise ValueError("Fewer than two valid time/voltage samples were found.")

    # Sort by time if the CSV is unexpectedly out of order.
    sort_order = np.argsort(time_values)
    time_values = time_values[sort_order]
    voltage_values = voltage_values[sort_order]

    if np.any(np.diff(time_values) <= 0):
        raise ValueError(
            "Time values are duplicated or are not strictly increasing."
        )

    return (
        time_values,
        voltage_values,
        len(time_raw),
        int(np.sum(~valid_mask))
    )

# ==========================================================
# THRESHOLD AND INTERPOLATION HELPERS
# ==========================================================

def estimate_baseline(voltage):
    """
    Estimate the Pulse Off baseline from the first valid waveform samples.

    The capture setup guarantees that the first
    BASELINE_OFF_STATE_SAMPLE_COUNT points are in the Pulse Off state. The
    median is intentionally used so isolated noise or undershoot samples do
    not pull the baseline away from the representative off-state voltage.
    """

    voltage = np.asarray(voltage, dtype=float)
    valid_voltage = voltage[np.isfinite(voltage)]

    if len(valid_voltage) < BASELINE_OFF_STATE_SAMPLE_COUNT:
        raise ValueError(
            "Waveform contains only "
            f"{len(valid_voltage)} valid voltage samples; at least "
            f"{BASELINE_OFF_STATE_SAMPLE_COUNT} are required for the "
            "Pulse Off baseline estimate."
        )

    off_state_samples = valid_voltage[:BASELINE_OFF_STATE_SAMPLE_COUNT]
    baseline_v = float(np.median(off_state_samples))

    if not np.isfinite(baseline_v):
        raise ValueError("Pulse Off baseline estimate is not finite.")

    return baseline_v


def calculate_thresholds(voltage, power_setpoint_w):
    """Calculate thresholds, including the special low-power 10 percent rule."""
    voltage = np.asarray(voltage, dtype=float)
    baseline_v = estimate_baseline(voltage) if SUBTRACT_BASELINE else 0.0
    maximum_v = float(np.max(voltage))
    signal_span_v = maximum_v - baseline_v

    if not np.isfinite(signal_span_v) or signal_span_v <= 0:
        raise ValueError("Signal amplitude is zero or invalid.")

    off_state_samples = voltage[:BASELINE_OFF_STATE_SAMPLE_COUNT]
    noise_floor_max_v = float(np.max(off_state_samples))
    low_power_rule_used = bool(
        USE_LOW_POWER_NOISE_FLOOR_THRESHOLD
        and float(power_setpoint_w) <= LOW_POWER_THRESHOLD_W
    )

    if USE_BASELINE_REFERENCED_THRESHOLDS:
        if low_power_rule_used:
            noise_to_peak_span_v = maximum_v - noise_floor_max_v
            if not np.isfinite(noise_to_peak_span_v) or noise_to_peak_span_v <= 0:
                raise ValueError(
                    "Waveform maximum is not greater than the measured Pulse Off "
                    f"noise-floor maximum. Noise max={noise_floor_max_v:.6g} V, "
                    f"waveform max={maximum_v:.6g} V."
                )
            low_threshold_v = (
                noise_floor_max_v
                + LOW_THRESHOLD_FRACTION * noise_to_peak_span_v
            )
        else:
            low_threshold_v = (
                baseline_v + LOW_THRESHOLD_FRACTION * signal_span_v
            )

        high_threshold_v = (
            baseline_v + HIGH_THRESHOLD_FRACTION * signal_span_v
        )
    else:
        if low_power_rule_used:
            noise_to_peak_span_v = maximum_v - noise_floor_max_v
            low_threshold_v = (
                noise_floor_max_v
                + LOW_THRESHOLD_FRACTION * noise_to_peak_span_v
            )
        else:
            low_threshold_v = LOW_THRESHOLD_FRACTION * maximum_v
        high_threshold_v = HIGH_THRESHOLD_FRACTION * maximum_v

    return (
        baseline_v,
        maximum_v,
        signal_span_v,
        low_threshold_v,
        high_threshold_v,
        noise_floor_max_v,
        low_power_rule_used
    )


def interpolate_crossing_time(time, voltage, left_index, threshold):
    """
    Interpolates a threshold crossing between left_index and left_index + 1.
    If interpolation is disabled or impossible, returns the second sample time.
    """

    right_index = left_index + 1

    if right_index >= len(time):
        raise IndexError("Crossing interpolation index is outside the waveform.")

    t_left = float(time[left_index])
    t_right = float(time[right_index])
    v_left = float(voltage[left_index])
    v_right = float(voltage[right_index])

    if not USE_LINEAR_INTERPOLATION or v_right == v_left:
        return t_right

    fraction = (threshold - v_left) / (v_right - v_left)
    fraction = min(max(fraction, 0.0), 1.0)

    return t_left + fraction * (t_right - t_left)

# ==========================================================
# RISE TIME CALCULATION
# ==========================================================

def calculate_rise_time(time, voltage, power_setpoint_w):
    """
    Calculates 10-to-90 percent rise time from a rising pulse edge.

    The first valid 10 percent rising crossing is found. The 90 percent
    crossing is then required to occur after that 10 percent crossing.
    """

    (
        baseline_v,
        maximum_v,
        signal_span_v,
        v10,
        v90,
        noise_floor_max_v,
        low_power_rule_used
    ) = calculate_thresholds(voltage, power_setpoint_w)

    rising_10 = np.where(
        (voltage[:-1] < v10) &
        (voltage[1:] >= v10)
    )[0]

    if len(rising_10) == 0:
        raise ValueError("No rising 10 percent threshold crossing was found.")

    selected_idx10 = None
    selected_idx90 = None

    for idx10_left in rising_10:
        rising_90_after_10 = np.where(
            (np.arange(len(voltage) - 1) >= idx10_left) &
            (voltage[:-1] < v90) &
            (voltage[1:] >= v90)
        )[0]

        if len(rising_90_after_10) > 0:
            selected_idx10 = int(idx10_left)
            selected_idx90 = int(rising_90_after_10[0])
            break

    if selected_idx10 is None or selected_idx90 is None:
        raise ValueError(
            "No valid rising 10-to-90 percent crossing pair was found."
        )

    t10 = interpolate_crossing_time(
        time,
        voltage,
        selected_idx10,
        v10
    )

    t90 = interpolate_crossing_time(
        time,
        voltage,
        selected_idx90,
        v90
    )

    rise_time = t90 - t10

    if not np.isfinite(rise_time) or rise_time <= 0:
        raise ValueError(
            f"Calculated rise time is invalid: {rise_time}"
        )

    return {
        "Baseline Voltage (V)": baseline_v,
        "Max Voltage (V)": maximum_v,
        "Signal Span (V)": signal_span_v,
        "Pulse Off Noise Floor Max (V)": noise_floor_max_v,
        "Low Power Threshold Rule Used": low_power_rule_used,
        "10% Voltage (V)": v10,
        "90% Voltage (V)": v90,
        "t10 (s)": t10,
        "t90 (s)": t90,
        "Edge Time (s)": rise_time,
        "Left Index 10": selected_idx10,
        "Left Index 90": selected_idx90
    }

# ==========================================================
# FALL TIME CALCULATION
# ==========================================================

def calculate_fall_time(time, voltage, power_setpoint_w):
    """
    Calculates 90-to-10 percent fall time from a falling pulse edge.

    The first valid 90 percent falling crossing is found. The 10 percent
    crossing is then required to occur after that 90 percent crossing.
    """

    (
        baseline_v,
        maximum_v,
        signal_span_v,
        v10,
        v90,
        noise_floor_max_v,
        low_power_rule_used
    ) = calculate_thresholds(voltage, power_setpoint_w)

    falling_90 = np.where(
        (voltage[:-1] > v90) &
        (voltage[1:] <= v90)
    )[0]

    if len(falling_90) == 0:
        raise ValueError("No falling 90 percent threshold crossing was found.")

    selected_idx90 = None
    selected_idx10 = None

    for idx90_left in reversed(falling_90):
        falling_10_after_90 = np.where(
            (np.arange(len(voltage) - 1) >= idx90_left) &
            (voltage[:-1] > v10) &
            (voltage[1:] <= v10)
        )[0]

        if len(falling_10_after_90) > 0:
            selected_idx90 = int(idx90_left)
            selected_idx10 = int(falling_10_after_90[0])
            break

    if selected_idx90 is None or selected_idx10 is None:
        raise ValueError(
            "No valid falling 90-to-10 percent crossing pair was found."
        )

    t90 = interpolate_crossing_time(
        time,
        voltage,
        selected_idx90,
        v90
    )

    t10 = interpolate_crossing_time(
        time,
        voltage,
        selected_idx10,
        v10
    )

    fall_time = t10 - t90

    if not np.isfinite(fall_time) or fall_time <= 0:
        raise ValueError(
            f"Calculated fall time is invalid: {fall_time}"
        )

    return {
        "Baseline Voltage (V)": baseline_v,
        "Max Voltage (V)": maximum_v,
        "Signal Span (V)": signal_span_v,
        "Pulse Off Noise Floor Max (V)": noise_floor_max_v,
        "Low Power Threshold Rule Used": low_power_rule_used,
        "10% Voltage (V)": v10,
        "90% Voltage (V)": v90,
        "t10 (s)": t10,
        "t90 (s)": t90,
        "Edge Time (s)": fall_time,
        "Left Index 10": selected_idx10,
        "Left Index 90": selected_idx90
    }

# ==========================================================
# RMS ENVELOPE AND COMPLETE-PULSE ANALYSIS
# ==========================================================
def centered_moving_average(values, window):
    """Centered moving average with reflected padding and unchanged length."""
    values = np.asarray(values, dtype=float)
    if window <= 1:
        return values.copy()
    if window % 2 == 0:
        window += 1
    pad = window // 2
    padded = np.pad(values, pad, mode="reflect")
    return np.convolve(padded, np.ones(window) / window, mode="valid")


def extract_rms_peak_envelope(time, voltage, rf_frequency_khz):
    """Create a peak-equivalent centered moving-RMS RF envelope."""
    time = np.asarray(time, dtype=float)
    voltage = np.asarray(voltage, dtype=float)
    dt = float(np.median(np.diff(time)))
    if not np.isfinite(dt) or dt <= 0:
        raise ValueError("Invalid waveform sample interval for RMS envelope.")
    rf_hz = float(rf_frequency_khz) * 1000.0
    if not np.isfinite(rf_hz) or rf_hz <= 0:
        raise ValueError("RF frequency must be positive for RMS envelope extraction.")
    sample_rate_hz = 1.0 / dt
    window = int(round((sample_rate_hz / rf_hz) * ENVELOPE_RMS_CYCLES))
    window = max(MIN_ENVELOPE_WINDOW_SAMPLES, window)
    window = min(MAX_ENVELOPE_WINDOW_SAMPLES, window)
    if window % 2 == 0:
        window += 1
    if window >= len(voltage):
        raise ValueError("RMS envelope window is longer than the waveform.")
    rms = np.sqrt(np.maximum(centered_moving_average(voltage ** 2, window), 0.0))
    return rms * np.sqrt(2.0), window, sample_rate_hz


def robust_median(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        raise ValueError("A required plateau region has no finite samples.")
    return float(np.median(values))


def midpoint_crossings(signal, midpoint, edge_type):
    indices = np.arange(len(signal) - 1)
    if edge_type == "RISE":
        mask = (signal[:-1] < midpoint) & (signal[1:] >= midpoint)
    else:
        mask = (signal[:-1] > midpoint) & (signal[1:] <= midpoint)
    return indices[mask]


def calculate_rms_complete_pulse_edge(time, raw_voltage, file_info):
    """Measure a requested SLP edge only within a complete OFF-ON-OFF pulse.

    A complete SLP pulse is always validated as:

        stable Pulse Off -> RISE -> stable Pulse On -> FALL -> stable Pulse Off

    RISE files measure the first edge of that validated pulse. FALL files measure
    the second edge. This allows a FALL capture to use the preceding RISE as its
    companion edge instead of incorrectly requiring another later RISE.
    """
    edge_type = file_info["Type"]
    pulse_hz = float(file_info["Pulse Frequency (Hz)"])
    duty_fraction = float(file_info["Duty Cycle (%)"]) / 100.0
    if pulse_hz <= 0 or not 0 < duty_fraction < 1:
        raise ValueError("Pulse frequency and duty cycle must define two nonzero states.")

    time = np.asarray(time, dtype=float)
    envelope, window, sample_rate_hz = extract_rms_peak_envelope(
        time, raw_voltage, file_info["RF Frequency (kHz)"]
    )
    period_s = 1.0 / pulse_hz
    on_time_s = period_s * duty_fraction
    off_time_s = period_s - on_time_s
    short_state_s = min(on_time_s, off_time_s)
    dt = float(np.median(np.diff(time)))
    plateau_samples = max(
        MIN_PLATEAU_SAMPLES,
        int(round(short_state_s * PLATEAU_FRACTION_OF_SHORT_STATE / dt)),
    )
    plateau_samples = min(
        plateau_samples,
        max(MIN_PLATEAU_SAMPLES, len(envelope) // 10),
    )

    sorted_env = np.sort(envelope[np.isfinite(envelope)])
    tail_count = max(MIN_PLATEAU_SAMPLES, int(round(0.15 * len(sorted_env))))
    tail_count = min(tail_count, max(1, len(sorted_env) // 2))
    low_global = robust_median(sorted_env[:tail_count])
    high_global = robust_median(sorted_env[-tail_count:])
    if high_global <= low_global:
        raise ValueError("Could not separate Pulse Off and Pulse On envelope states.")
    if (high_global - low_global) / max(high_global, 1e-15) < MIN_STATE_SEPARATION_RATIO:
        raise ValueError("Pulse-state envelope separation is too small for reliable timing.")
    midpoint = 0.5 * (low_global + high_global)

    rises = midpoint_crossings(envelope, midpoint, "RISE")
    falls = midpoint_crossings(envelope, midpoint, "FALL")
    all_edges = sorted(
        [(int(index), "RISE") for index in rises]
        + [(int(index), "FALL") for index in falls],
        key=lambda item: item[0],
    )

    candidates = []
    boundary_rejected = 0
    duration_rejected = 0
    plateau_rejected = 0
    minimum_on_duration = on_time_s * MIN_COMPLETE_PULSE_DURATION_RATIO
    maximum_on_duration = on_time_s * MAX_COMPLETE_PULSE_DURATION_RATIO

    # Build complete pulses from RISE followed immediately by FALL. Both RISE
    # and FALL files select their requested edge from the same validated pulse.
    for position, (rise_index, detected_type) in enumerate(all_edges):
        if detected_type != "RISE":
            continue
        if position + 1 >= len(all_edges):
            boundary_rejected += 1
            continue
        fall_index, next_type = all_edges[position + 1]
        if next_type != "FALL":
            plateau_rejected += 1
            continue

        before_start = rise_index - plateau_samples
        after_stop = fall_index + 1 + plateau_samples
        if before_start < 0 or after_stop > len(envelope):
            boundary_rejected += 1
            continue

        on_plateau = envelope[rise_index + 1:fall_index]
        if len(on_plateau) < MIN_PLATEAU_SAMPLES:
            plateau_rejected += 1
            continue

        measured_on_duration = float(time[fall_index] - time[rise_index])
        if not minimum_on_duration <= measured_on_duration <= maximum_on_duration:
            duration_rejected += 1
            continue

        off_before = envelope[before_start:rise_index]
        off_after = envelope[fall_index + 1:after_stop]
        off_before_level = robust_median(off_before)
        on_level = robust_median(on_plateau)
        off_after_level = robust_median(off_after)
        off_level = robust_median(np.concatenate([off_before, off_after]))
        span = on_level - off_level
        if span <= 0:
            plateau_rejected += 1
            continue

        off_consistency = abs(off_before_level - off_after_level) / max(span, 1e-15)
        duration_error = abs(measured_on_duration - on_time_s) / on_time_s
        center_error = abs(
            0.5 * (rise_index + fall_index) - 0.5 * len(envelope)
        ) / len(envelope)
        score = span / (1 + duration_error) / (1 + off_consistency) / (
            1 + COMPLETE_PULSE_CENTER_WEIGHT * center_error
        )
        candidates.append({
            "score": score,
            "rise_index": rise_index,
            "fall_index": fall_index,
            "off_level": off_level,
            "on_level": on_level,
            "off_before_level": off_before_level,
            "off_after_level": off_after_level,
            "measured_on_duration": measured_on_duration,
            "duration_error": duration_error,
            "off_consistency": off_consistency,
        })

    if not candidates:
        raise ValueError(
            "No complete OFF-ON-OFF pulse containing the requested edge was found. "
            f"Boundary partials ignored: {boundary_rejected}; "
            f"duration rejects: {duration_rejected}; "
            f"plateau/sequence rejects: {plateau_rejected}."
        )

    selected = max(candidates, key=lambda item: item["score"])
    rise_index = selected["rise_index"]
    fall_index = selected["fall_index"]
    requested_index = rise_index if edge_type == "RISE" else fall_index
    companion_index = fall_index if edge_type == "RISE" else rise_index
    low_level = selected["off_level"]
    high_level = selected["on_level"]
    span = high_level - low_level
    v10 = low_level + LOW_THRESHOLD_FRACTION * span
    v90 = low_level + HIGH_THRESHOLD_FRACTION * span

    half_width = max(
        plateau_samples,
        int(round(short_state_s * 0.45 / dt)),
    )
    indices = np.arange(len(envelope) - 1)
    if edge_type == "RISE":
        left_bound = max(0, rise_index - half_width)
        right_bound = min(
            len(envelope) - 2,
            rise_index + half_width,
            max(rise_index + 1, (rise_index + fall_index) // 2),
        )
        crossings10 = np.where(
            (indices >= left_bound) & (indices <= rise_index)
            & (envelope[:-1] < v10) & (envelope[1:] >= v10)
        )[0]
        crossings90 = np.where(
            (indices >= rise_index) & (indices <= right_bound)
            & (envelope[:-1] < v90) & (envelope[1:] >= v90)
        )[0]
        if len(crossings10) == 0 or len(crossings90) == 0:
            raise ValueError(
                "Selected complete pulse lacks a valid rising 10%-to-90% pair."
            )
        idx10 = int(crossings10[-1])
        idx90 = int(crossings90[0])
    else:
        left_bound = max(
            0,
            fall_index - half_width,
            min(fall_index - 1, (rise_index + fall_index) // 2),
        )
        right_bound = min(len(envelope) - 2, fall_index + half_width)
        crossings90 = np.where(
            (indices >= left_bound) & (indices <= fall_index)
            & (envelope[:-1] > v90) & (envelope[1:] <= v90)
        )[0]
        crossings10 = np.where(
            (indices >= fall_index) & (indices <= right_bound)
            & (envelope[:-1] > v10) & (envelope[1:] <= v10)
        )[0]
        if len(crossings90) == 0 or len(crossings10) == 0:
            raise ValueError(
                "Selected complete pulse lacks a valid falling 90%-to-10% pair."
            )
        idx90 = int(crossings90[-1])
        idx10 = int(crossings10[0])

    t10 = interpolate_crossing_time(time, envelope, idx10, v10)
    t90 = interpolate_crossing_time(time, envelope, idx90, v90)
    edge_time = t90 - t10 if edge_type == "RISE" else t10 - t90
    if not np.isfinite(edge_time) or edge_time <= 0:
        raise ValueError(f"Calculated RMS-envelope edge time is invalid: {edge_time}")

    requested_edge_time = interpolate_crossing_time(
        time, envelope, requested_index, midpoint
    )
    companion_edge_time = interpolate_crossing_time(
        time, envelope, companion_index, midpoint
    )

    return {
        "Baseline Voltage (V)": low_level,
        "Max Voltage (V)": high_level,
        "Signal Span (V)": span,
        "Pulse Off Noise Floor Max (V)": np.nan,
        "Low Power Threshold Rule Used": False,
        "10% Voltage (V)": v10,
        "90% Voltage (V)": v90,
        "t10 (s)": t10,
        "t90 (s)": t90,
        "Edge Time (s)": edge_time,
        "Left Index 10": idx10,
        "Left Index 90": idx90,
        "Analysis Method": ANALYSIS_METHOD_RMS_COMPLETE_PULSE,
        "Analysis Signal": envelope,
        "Envelope RMS Window Samples": window,
        "Envelope RMS Window Cycles": ENVELOPE_RMS_CYCLES,
        "Sample Rate (Sa/s)": sample_rate_hz,
        "Detected Complete Pulse Count": len(candidates),
        "Selected Complete Pulse Number": candidates.index(selected) + 1,
        "Boundary Partial Pulses Ignored": boundary_rejected,
        "Duration-Rejected Pulse Candidates": duration_rejected,
        "Plateau-Rejected Pulse Candidates": plateau_rejected,
        "Requested Edge Index": requested_index,
        "Companion Edge Index": companion_index,
        "Requested Edge Time (s)": requested_edge_time,
        "Companion Edge Time (s)": companion_edge_time,
        "Measured Selected-State Duration (s)": selected["measured_on_duration"],
        "Expected Selected-State Duration (s)": on_time_s,
        "Selected-State Duration Error (%)": selected["duration_error"] * 100.0,
        "Pre/Post Starting-State Mismatch Ratio": selected["off_consistency"],
        "Complete Pulse Rise Time (s)": interpolate_crossing_time(
            time, envelope, rise_index, midpoint
        ),
        "Complete Pulse Fall Time (s)": interpolate_crossing_time(
            time, envelope, fall_index, midpoint
        ),
    }


def analyze_waveform(time, voltage, file_info):
    """Dispatch to the selected method while preserving the original path."""
    if WAVEFORM_ANALYSIS_METHOD == ANALYSIS_METHOD_ORIGINAL:
        calculation = (
            calculate_rise_time(time, voltage, file_info["Power Setpoint (W)"])
            if file_info["Type"] == "RISE"
            else calculate_fall_time(time, voltage, file_info["Power Setpoint (W)"])
        )
        calculation = dict(calculation)
        calculation["Analysis Method"] = ANALYSIS_METHOD_ORIGINAL
        calculation["Analysis Signal"] = np.asarray(voltage, dtype=float)
        return calculation
    if WAVEFORM_ANALYSIS_METHOD == ANALYSIS_METHOD_RMS_COMPLETE_PULSE:
        return calculate_rms_complete_pulse_edge(time, voltage, file_info)
    raise ValueError(f"Unsupported waveform analysis method: {WAVEFORM_ANALYSIS_METHOD}")


def calculation_diagnostics(calculation):
    """Return optional method diagnostics without changing original result columns."""
    excluded = {"Analysis Signal"}
    return {
        key: value for key, value in calculation.items()
        if key not in excluded and key not in {
            "Baseline Voltage (V)", "Max Voltage (V)", "Signal Span (V)",
            "Pulse Off Noise Floor Max (V)", "Low Power Threshold Rule Used",
            "10% Voltage (V)", "90% Voltage (V)", "t10 (s)", "t90 (s)",
            "Edge Time (s)", "Left Index 10", "Left Index 90"
        }
    }


# ==========================================================
# PLOT CREATION
# ==========================================================

def save_analysis_plot(filename, edge_type, time, voltage, calculation):
    edge_time = calculation["Edge Time (s)"]
    v10 = calculation["10% Voltage (V)"]
    v90 = calculation["90% Voltage (V)"]
    t10 = calculation["t10 (s)"]
    t90 = calculation["t90 (s)"]

    # Preserve the algorithm-derived times and horizontal thresholds, but place
    # cursor markers at the interpolated voltage of the displayed waveform.
    marker_y10 = float(np.interp(t10, time, voltage))
    marker_y90 = float(np.interp(t90, time, voltage))

    plt.figure(figsize=(12, 6))
    plt.plot(
        time,
        voltage,
        color="blue",
        linewidth=1.2,
        label=("Original waveform" if WAVEFORM_ANALYSIS_METHOD == ANALYSIS_METHOD_RMS_COMPLETE_PULSE else "RF envelope")
    )

    plt.axhline(
        v10,
        color="green",
        linestyle=":",
        label=f"10% = {v10:.4f} V"
    )

    plt.axhline(
        v90,
        color="red",
        linestyle=":",
        label=f"90% = {v90:.4f} V"
    )

    plt.axvline(
        t10,
        color="green",
        linestyle="--",
        label=f"10% time = {t10:.6e} s"
    )

    plt.axvline(
        t90,
        color="red",
        linestyle="--",
        label=f"90% time = {t90:.6e} s"
    )

    # plt.plot(t10, marker_y10, "go", markersize=8)
    # plt.plot(t90, marker_y90, "ro", markersize=8)

    plt.annotate(
        f"10%\n{t10:.3e} s",
        (t10, marker_y10),
        textcoords="offset points",
        xytext=(10, 10)
    )

    plt.annotate(
        f"90%\n{t90:.3e} s",
        (t90, marker_y90),
        textcoords="offset points",
        xytext=(10, 10)
    )

    if edge_type == "RISE":
        title_label = "10%-90% Rise Time"
        suffix = "pulse_risetime"
    else:
        title_label = "90%-10% Fall Time"
        suffix = "pulse_falltime"

    plt.title(
        f"{filename}\n{title_label} = {edge_time:.6e} s"
    )
    plt.xlabel("Time (s)")
    plt.ylabel("Voltage (V)")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()

    output_file = os.path.join(
        individual_plots_folder,
        f"{os.path.splitext(filename)[0]}_{suffix}.png"
    )

    plt.savefig(output_file, dpi=300)
    plt.close()

    return output_file

def safe_filename_value(value):
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(numeric).replace(".", "p")


def _windows_extended_path(file_path):
    """Return an absolute Windows extended-length path when appropriate."""
    absolute_path = os.path.abspath(os.fspath(file_path))
    if os.name != "nt" or absolute_path.startswith("\\\\?\\"):
        return absolute_path
    if absolute_path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute_path.lstrip("\\")
    return "\\\\?\\" + absolute_path


def safe_save_figure(figure, output_path, **savefig_kwargs):
    """Create the destination folder and save, including long Windows paths."""
    output_path = os.path.abspath(os.fspath(output_path))
    parent_folder = os.path.dirname(output_path)
    if parent_folder:
        os.makedirs(parent_folder, exist_ok=True)
    figure.savefig(_windows_extended_path(output_path), **savefig_kwargs)
    return output_path


def run_plot_step_nonfatal(description, plot_function, *args, **kwargs):
    """Run a plot step without allowing an optional plot failure to end analysis."""
    try:
        return plot_function(*args, **kwargs)
    except Exception as error:
        message = f"WARNING: {description} could not be completed: {error}"
        print("\n" + message, flush=True)
        try:
            progress_window.update(detail=message)
        except Exception:
            pass
        return []


def create_group_summary_plots(
    results_df, edge_type, show_sample_info=True, destination_folder=None
):
    """
    Create one edge-aligned representative plot for every test-setting group.

    Absolute acquisition times are not averaged directly onto an unrelated
    waveform. Instead, the group-average cursor offsets are calculated about
    each capture's edge center and then anchored to the representative edge.
    """
    if results_df.empty:
        return

    included_df = results_df[results_df["Use In Average"]].copy()
    if included_df.empty:
        return

    group_columns = [
        "RF Frequency (kHz)",
        "Power Setpoint (W)",
        "Pulse Frequency (Hz)",
        "Duty Cycle (%)"
    ]
    if destination_folder is None:
        destination_folder = output_folder
    summary_folder = os.path.join(destination_folder, f"{edge_type} Summary Plots")
    os.makedirs(summary_folder, exist_ok=True)
    total_counts = results_df.groupby(group_columns, dropna=False).size()

    for group_key, group in included_df.groupby(
        group_columns,
        dropna=False,
        sort=True
    ):
        rf_khz, power_w, pulse_hz, duty_pct = group_key

        try:
            # Use the non-outlier capture whose measured edge time is closest
            # to the group mean. This is more representative than the first file.
            average_edge_time_s = float(group["Edge Time (s)"].mean())
            representative_index = (
                group["Edge Time (s)"] - average_edge_time_s
            ).abs().idxmin()
            representative = group.loc[representative_index]

            time_values, voltage_values, _, _ = read_scope_csv(
                representative["CSV Path"]
            )

            # Calculate cursor locations relative to each capture's edge center.
            # This removes trigger-position jitter between acquisitions.
            edge_centers = (group["t10 (s)"] + group["t90 (s)"]) / 2.0
            relative_t10 = group["t10 (s)"] - edge_centers
            relative_t90 = group["t90 (s)"] - edge_centers
            average_relative_t10 = float(relative_t10.mean())
            average_relative_t90 = float(relative_t90.mean())

            representative_center = (
                float(representative["t10 (s)"])
                + float(representative["t90 (s)"])
            ) / 2.0
            plot_t10 = representative_center + average_relative_t10
            plot_t90 = representative_center + average_relative_t90

            # Keep the plotted cursor separation numerically consistent with
            # the true average of the included edge-time measurements.
            plotted_separation_s = abs(plot_t90 - plot_t10)
            if not np.isclose(
                plotted_separation_s,
                average_edge_time_s,
                rtol=1e-9,
                atol=max(1e-15, abs(average_edge_time_s) * 1e-12)
            ):
                half_average_edge_time = average_edge_time_s / 2.0
                plot_t10 = representative_center - half_average_edge_time
                plot_t90 = representative_center + half_average_edge_time

            # Horizontal thresholds must belong to the displayed waveform.
            # Group-average voltage thresholds can be visually incorrect when
            # amplitudes or baselines vary between captures.
            plot_v10 = float(representative["10% Voltage (V)"])
            plot_v90 = float(representative["90% Voltage (V)"])
            group_avg_v10 = float(group["10% Voltage (V)"].mean())
            group_avg_v90 = float(group["90% Voltage (V)"].mean())

            average_absolute_t10 = float(group["t10 (s)"].mean())
            average_absolute_t90 = float(group["t90 (s)"].mean())
            average_time_us = float(group["Edge Time (us)"].mean())
            std_time_us = group["Edge Time (us)"].std()
            std_text = (
                "N/A" if pd.isna(std_time_us) else f"{std_time_us:.3f} us"
            )
            included_count = len(group)
            total_count = int(total_counts.loc[group_key])
            excluded_count = total_count - included_count

            # Marker coordinates use the actual waveform at the aligned times.
            marker_y10 = float(np.interp(plot_t10, time_values, voltage_values))
            marker_y90 = float(np.interp(plot_t90, time_values, voltage_values))

            fig, ax = plt.subplots(figsize=(12, 7))
            ax.plot(
                time_values,
                voltage_values,
                color="blue",
                linewidth=1.2,
                label=("Representative original waveform" if WAVEFORM_ANALYSIS_METHOD == ANALYSIS_METHOD_RMS_COMPLETE_PULSE else "Representative RF envelope")
            )
            ax.axvline(
                plot_t10,
                color="green",
                linestyle="--",
                label=f"Edge-aligned average t10 = {plot_t10:.3e} s"
            )
            ax.axvline(
                plot_t90,
                color="red",
                linestyle="--",
                label=f"Edge-aligned average t90 = {plot_t90:.3e} s"
            )
            ax.axhline(
                plot_v10,
                color="green",
                linestyle=":",
                label=f"Displayed waveform 10% = {plot_v10:.4f} V"
            )
            ax.axhline(
                plot_v90,
                color="red",
                linestyle=":",
                label=f"Displayed waveform 90% = {plot_v90:.4f} V"
            )
            # ax.plot(plot_t10, marker_y10, "go", markersize=8)
            # ax.plot(plot_t90, marker_y90, "ro", markersize=8)
            ax.annotate(
                "Edge-aligned average 10% cursor",
                (plot_t10, marker_y10),
                xytext=(10, 12),
                textcoords="offset points"
            )
            ax.annotate(
                "Edge-aligned average 90% cursor",
                (plot_t90, marker_y90),
                xytext=(10, -20),
                textcoords="offset points"
            )

            timing_label = "Rise" if edge_type == "RISE" else "Fall"
            sample_information = ""
            if show_sample_info:
                sample_information = (
                    f"Samples used: {included_count}\n"
                    f"Outliers excluded: {excluded_count}\n"
                )

            stats = (
                sample_information
                + f"Average {timing_label} Time: {average_time_us:.3f} us\n"
                f"Std. Dev.: {std_text}\n"
                f"Group mean absolute t10: {average_absolute_t10:.3e} s\n"
                f"Group mean absolute t90: {average_absolute_t90:.3e} s\n"
                f"Group mean 10% voltage: {group_avg_v10:.4f} V\n"
                f"Group mean 90% voltage: {group_avg_v90:.4f} V\n"
                f"Displayed waveform:\n{representative['Filename']}"
            )
            ax.text(
                0.98,
                0.98,
                stats,
                transform=ax.transAxes,
                ha="right",
                va="top",
                fontsize=9,
                bbox={
                    "boxstyle": "round",
                    "facecolor": "white",
                    "alpha": 0.85
                }
            )
            ax.set_title(
                f"{edge_type} SUMMARY | RF {rf_khz:g} kHz | {power_w:g} W | "
                f"Pulse {pulse_hz:g} Hz | Duty {duty_pct:g}%\n"
                f"Edge-Aligned Average {timing_label} Time = "
                f"{average_time_us:.3f} us"
            )
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Voltage (V)")
            ax.grid(True)
            ax.legend(loc="best")
            fig.tight_layout()

            plot_name = (
                f"{edge_type}_RF{safe_filename_value(rf_khz)}kHz_"
                f"Power{safe_filename_value(power_w)}W_"
                f"Pulse{safe_filename_value(pulse_hz)}Hz_"
                f"Duty{safe_filename_value(duty_pct)}pct_Summary.png"
            )
            plot_path = os.path.join(summary_folder, plot_name)
            safe_save_figure(fig, plot_path, dpi=300)
            plt.close(fig)
            print(f"Saved edge-aligned summary plot: {plot_path}")

        except Exception as error:
            plt.close("all")
            print(
                f"Summary plot failed for {edge_type}, RF {rf_khz:g} kHz, "
                f"{power_w:g} W, pulse {pulse_hz:g} Hz, "
                f"duty {duty_pct:g}%: {error}"
            )


# ==========================================================
# IQR OUTLIER FILTER
# ==========================================================

def add_iqr_outlier_flags(dataframe, value_column, group_columns):
    dataframe = dataframe.copy()

    dataframe["Use In Average"] = True
    dataframe["Outlier Reason"] = ""
    dataframe["IQR Lower Bound (s)"] = np.nan
    dataframe["IQR Upper Bound (s)"] = np.nan

    for _, group_df in dataframe.groupby(group_columns, dropna=False):
        values = group_df[value_column].dropna()

        if len(values) < MIN_SAMPLES_FOR_IQR:
            dataframe.loc[group_df.index, "Outlier Reason"] = (
                f"Included without IQR filtering: fewer than "
                f"{MIN_SAMPLES_FOR_IQR} samples in group"
            )
            continue

        q1 = values.quantile(0.25)
        q3 = values.quantile(0.75)
        iqr = q3 - q1

        lower_bound = q1 - IQR_MULTIPLIER * iqr
        upper_bound = q3 + IQR_MULTIPLIER * iqr

        dataframe.loc[group_df.index, "IQR Lower Bound (s)"] = lower_bound
        dataframe.loc[group_df.index, "IQR Upper Bound (s)"] = upper_bound

        outlier_mask = (
            (group_df[value_column] < lower_bound) |
            (group_df[value_column] > upper_bound)
        )

        outlier_indices = group_df.index[outlier_mask]
        included_indices = group_df.index[~outlier_mask]

        dataframe.loc[outlier_indices, "Use In Average"] = False
        dataframe.loc[outlier_indices, "Outlier Reason"] = (
            f"Excluded by IQR filter: outside "
            f"{lower_bound:.6e} to {upper_bound:.6e} s"
        )

        dataframe.loc[included_indices, "Outlier Reason"] = (
            f"Included: within IQR bounds "
            f"{lower_bound:.6e} to {upper_bound:.6e} s"
        )

    return dataframe

# ==========================================================
# SUMMARY CREATION
# ==========================================================

def create_summary(individual_df, edge_type):
    if individual_df.empty:
        return pd.DataFrame(), individual_df, pd.DataFrame()

    group_columns = [
        "RF Frequency (kHz)",
        "RF Frequency (MHz)",
        "Power Setpoint (W)",
        "Pulse Frequency (Hz)",
        "Pulse Frequency (kHz)",
        "Duty Cycle (%)"
    ]

    flagged_df = add_iqr_outlier_flags(
        individual_df,
        value_column="Edge Time (s)",
        group_columns=[
            "RF Frequency (kHz)",
            "Power Setpoint (W)",
            "Pulse Frequency (Hz)",
            "Duty Cycle (%)"
        ]
    )

    filtered_df = flagged_df[flagged_df["Use In Average"]].copy()
    outliers_df = flagged_df[~flagged_df["Use In Average"]].copy()

    total_counts = (
        flagged_df
        .groupby(group_columns, dropna=False)
        .agg(
            Total_Sample_Count=("Edge Time (s)", "count"),
            Excluded_Outlier_Count=(
                "Use In Average",
                lambda values: int((~values).sum())
            )
        )
        .reset_index()
    )

    if filtered_df.empty:
        return pd.DataFrame(), flagged_df, outliers_df

    summary_df = (
        filtered_df
        .groupby(group_columns, dropna=False)
        .agg(
            Included_Sample_Count=("Edge Time (s)", "count"),
            Average_Edge_Time_s=("Edge Time (s)", "mean"),
            Std_Dev_Edge_Time_s=("Edge Time (s)", "std"),
            Average_Edge_Time_us=("Edge Time (us)", "mean"),
            Minimum_Edge_Time_us=("Edge Time (us)", "min"),
            Maximum_Edge_Time_us=("Edge Time (us)", "max"),
            Std_Dev_Edge_Time_us=("Edge Time (us)", "std"),
            Average_Edge_Time_ns=("Edge Time (ns)", "mean"),
            Std_Dev_Edge_Time_ns=("Edge Time (ns)", "std"),
            Average_Max_Voltage_V=("Max Voltage (V)", "mean"),
            Std_Dev_Max_Voltage_V=("Max Voltage (V)", "std"),
            Average_Baseline_Voltage_V=("Baseline Voltage (V)", "mean"),
            Average_Signal_Span_V=("Signal Span (V)", "mean")
        )
        .reset_index()
    )

    summary_df = summary_df.merge(
        total_counts,
        on=group_columns,
        how="left"
    )

    if edge_type == "RISE":
        rename_map = {
            "Average_Edge_Time_s": "Average_Rise_Time_s",
            "Std_Dev_Edge_Time_s": "Std_Dev_Rise_Time_s",
            "Average_Edge_Time_us": "Average_Rise_Time_us",
            "Minimum_Edge_Time_us": "Minimum_Rise_Time_us",
            "Maximum_Edge_Time_us": "Maximum_Rise_Time_us",
            "Std_Dev_Edge_Time_us": "Std_Dev_Rise_Time_us",
            "Average_Edge_Time_ns": "Average_Rise_Time_ns",
            "Std_Dev_Edge_Time_ns": "Std_Dev_Rise_Time_ns"
        }
    else:
        rename_map = {
            "Average_Edge_Time_s": "Average_Fall_Time_s",
            "Std_Dev_Edge_Time_s": "Std_Dev_Fall_Time_s",
            "Average_Edge_Time_us": "Average_Fall_Time_us",
            "Minimum_Edge_Time_us": "Minimum_Fall_Time_us",
            "Maximum_Edge_Time_us": "Maximum_Fall_Time_us",
            "Std_Dev_Edge_Time_us": "Std_Dev_Fall_Time_us",
            "Average_Edge_Time_ns": "Average_Fall_Time_ns",
            "Std_Dev_Edge_Time_ns": "Std_Dev_Fall_Time_ns"
        }

    summary_df = summary_df.rename(columns=rename_map)

    count_columns = [
        "Total_Sample_Count",
        "Included_Sample_Count",
        "Excluded_Outlier_Count"
    ]

    other_columns = [
        column for column in summary_df.columns
        if column not in group_columns + count_columns
    ]

    summary_df = summary_df[
        group_columns + count_columns + other_columns
    ]

    summary_df = summary_df.sort_values(
        by=[
            "Pulse Frequency (Hz)",
            "Duty Cycle (%)",
            "RF Frequency (kHz)",
            "Power Setpoint (W)"
        ],
        ascending=True
    )

    return summary_df, flagged_df, outliers_df

# ==========================================================
# MINIMUM AND MAXIMUM EDGE TIMES BY FREQUENCY
# ==========================================================

def create_frequency_min_max_summary(rise_df, fall_df):
    """
    Create one combined table of accepted RISE and FALL time ranges.

    RISE versus FALL is preserved from extract_file_info(), which reads the
    RF_Freq_RISE... or RF_Freq_FALL... token in each source CSV filename.
    The input dataframes have already been separated by that Type value.

    Results are grouped by pulse frequency, duty cycle, and RF frequency.
    Minima and maxima are calculated only from rows retained by the IQR filter
    (Use In Average == True), so excluded outliers do not define the range.
    """

    group_columns = [
        "Pulse Frequency (Hz)",
        "Pulse Frequency (kHz)",
        "Duty Cycle (%)",
        "RF Frequency (kHz)",
        "RF Frequency (MHz)"
    ]

    def summarize_edge(dataframe, edge_label):
        if dataframe.empty:
            return pd.DataFrame(columns=group_columns)

        included = dataframe[
            dataframe["Use In Average"] == True
        ].copy()

        if included.empty:
            return pd.DataFrame(columns=group_columns)

        return (
            included
            .groupby(group_columns, dropna=False)
            .agg(**{
                f"{edge_label} Included Sample Count": (
                    "Edge Time (s)", "count"
                ),
                f"Minimum {edge_label} Time (s)": (
                    "Edge Time (s)", "min"
                ),
                f"Maximum {edge_label} Time (s)": (
                    "Edge Time (s)", "max"
                ),
                f"Minimum {edge_label} Time (us)": (
                    "Edge Time (us)", "min"
                ),
                f"Maximum {edge_label} Time (us)": (
                    "Edge Time (us)", "max"
                ),
                f"Minimum {edge_label} Time (ns)": (
                    "Edge Time (ns)", "min"
                ),
                f"Maximum {edge_label} Time (ns)": (
                    "Edge Time (ns)", "max"
                ),
                f"Minimum {edge_label} Power Setpoint (W)": (
                    "Power Setpoint (W)", "min"
                ),
                f"Maximum {edge_label} Power Setpoint (W)": (
                    "Power Setpoint (W)", "max"
                )
            })
            .reset_index()
        )

    rise_ranges = summarize_edge(rise_df, "Rise")
    fall_ranges = summarize_edge(fall_df, "Fall")

    if rise_ranges.empty:
        combined = fall_ranges.copy()
    elif fall_ranges.empty:
        combined = rise_ranges.copy()
    else:
        combined = rise_ranges.merge(
            fall_ranges,
            on=group_columns,
            how="outer"
        )

    if combined.empty:
        return combined

    return combined.sort_values(
        by=[
            "Pulse Frequency (Hz)",
            "Duty Cycle (%)",
            "RF Frequency (kHz)"
        ],
        ascending=True
    ).reset_index(drop=True)

# ==========================================================
# CROSS-RF-FREQUENCY SETPOINT AVERAGES
# ==========================================================

def create_cross_rf_frequency_setpoint_average(summary_df, edge_type):
    """Average frequency-level edge times for each pulsing test condition.

    Each RF frequency contributes one outlier-filtered summary average, which
    prevents a frequency with more captures from receiving extra weight.
    Pulse frequency and duty cycle remain separate test conditions.
    """
    if summary_df.empty:
        return pd.DataFrame()

    edge_label = "Rise" if edge_type == "RISE" else "Fall"
    average_s_column = f"Average_{edge_label}_Time_s"
    average_us_column = f"Average_{edge_label}_Time_us"
    average_ns_column = f"Average_{edge_label}_Time_ns"
    condition_columns = [
        "Power Setpoint (W)",
        "Pulse Frequency (Hz)",
        "Pulse Frequency (kHz)",
        "Duty Cycle (%)",
    ]
    required_columns = set(condition_columns + [
        "RF Frequency (kHz)",
        average_s_column,
        average_us_column,
        average_ns_column,
        "Included_Sample_Count",
        "Total_Sample_Count",
        "Excluded_Outlier_Count",
    ])
    missing_columns = required_columns.difference(summary_df.columns)
    if missing_columns:
        raise ValueError(
            "Cannot create cross-RF-frequency setpoint averages. Missing columns: "
            + ", ".join(sorted(missing_columns))
        )

    grouped = summary_df.groupby(condition_columns, dropna=False)
    result = grouped.agg(**{
        "RF Frequency Count": ("RF Frequency (kHz)", "nunique"),
        f"Average {edge_label} Time Across RF Frequencies (s)": (average_s_column, "mean"),
        f"Average {edge_label} Time Across RF Frequencies (us)": (average_us_column, "mean"),
        f"Average {edge_label} Time Across RF Frequencies (ns)": (average_ns_column, "mean"),
        f"Std Dev of RF-Frequency {edge_label} Averages (s)": (average_s_column, "std"),
        f"Std Dev of RF-Frequency {edge_label} Averages (us)": (average_us_column, "std"),
        f"Std Dev of RF-Frequency {edge_label} Averages (ns)": (average_ns_column, "std"),
        f"Included {edge_label} Samples Across RF Frequencies": ("Included_Sample_Count", "sum"),
        f"Total {edge_label} Samples Across RF Frequencies": ("Total_Sample_Count", "sum"),
        f"Excluded {edge_label} Outliers Across RF Frequencies": ("Excluded_Outlier_Count", "sum"),
    }).reset_index()

    frequency_lists = grouped["RF Frequency (kHz)"].apply(
        lambda values: ", ".join(
            f"{frequency:g}" for frequency in sorted(values.dropna().unique())
        )
    ).rename("RF Frequencies Included (kHz)").reset_index()
    result = result.merge(frequency_lists, on=condition_columns, how="left")
    first_columns = condition_columns + [
        "RF Frequency Count", "RF Frequencies Included (kHz)"
    ]
    remaining = [column for column in result.columns if column not in first_columns]
    return result[first_columns + remaining].sort_values(
        ["Pulse Frequency (Hz)", "Duty Cycle (%)", "Power Setpoint (W)"]
    )


def combine_setpoint_averages(rise_average, fall_average):
    """Combine pulsing rise and fall cross-frequency averages."""
    if rise_average.empty:
        return fall_average.copy()
    if fall_average.empty:
        return rise_average.copy()

    keys = [
        "Power Setpoint (W)",
        "Pulse Frequency (Hz)",
        "Pulse Frequency (kHz)",
        "Duty Cycle (%)",
    ]
    shared_descriptive = ["RF Frequency Count", "RF Frequencies Included (kHz)"]
    fall_only = [
        column for column in fall_average.columns
        if column not in keys + shared_descriptive
    ]
    return rise_average.merge(
        fall_average[keys + fall_only],
        on=keys,
        how="outer",
    ).sort_values(["Pulse Frequency (Hz)", "Duty Cycle (%)", "Power Setpoint (W)"])


# ==========================================================
# RISE/FALL TREND PLOTS BY PULSING CONDITION
# ==========================================================

def create_edge_time_trend_plots(summary_df, edge_type, destination_folder):
    """Connect power-setpoint averages for each RF frequency.

    One plot is generated for each pulse-frequency/duty-cycle condition so
    unrelated pulsing conditions are never combined. No signal filtering and
    no curve fitting are performed.
    """
    if summary_df.empty:
        print(f"No {edge_type.lower()} summary data were available for trend plotting.")
        return []

    from matplotlib.ticker import (
        AutoMinorLocator,
        LogLocator,
        MaxNLocator,
        NullFormatter,
        ScalarFormatter,
    )

    edge_label = "Rise" if edge_type == "RISE" else "Fall"
    value_column = f"Average_{edge_label}_Time_us"
    required_columns = {
        "Power Setpoint (W)",
        "RF Frequency (kHz)",
        "Pulse Frequency (Hz)",
        "Duty Cycle (%)",
        value_column,
    }
    missing_columns = required_columns.difference(summary_df.columns)
    if missing_columns:
        raise ValueError(
            f"Cannot create {edge_label.lower()}-time trend plots. Missing columns: "
            + ", ".join(sorted(missing_columns))
        )

    plot_data = summary_df[list(required_columns)].dropna().copy()
    if plot_data.empty:
        return []

    trend_folder = os.path.join(destination_folder, f"{edge_type} Trend Plots")
    os.makedirs(trend_folder, exist_ok=True)
    colors = ["#4472C4", "#ED7D31", "#70AD47", "#A5A5A5", "#5B9BD5", "#FFC000"]
    markers = ["o", "s", "^", "D", "v", "P"]
    output_paths = []

    condition_columns = ["Pulse Frequency (Hz)", "Duty Cycle (%)"]
    for (pulse_frequency_hz, duty_cycle), condition_df in plot_data.groupby(
        condition_columns, dropna=False, sort=True
    ):
        condition_df = condition_df.sort_values(
            ["RF Frequency (kHz)", "Power Setpoint (W)"]
        )
        figure, axis = plt.subplots(figsize=(12, 7.5))

        for index, (rf_frequency_khz, frequency_df) in enumerate(
            condition_df.groupby("RF Frequency (kHz)", sort=True)
        ):
            frequency_df = frequency_df.sort_values("Power Setpoint (W)")
            axis.plot(
                frequency_df["Power Setpoint (W)"],
                frequency_df[value_column],
                color=colors[index % len(colors)],
                marker=markers[index % len(markers)],
                markersize=7,
                linewidth=2.0,
                markeredgecolor="white",
                markeredgewidth=0.8,
                label=f"{rf_frequency_khz:g} kHz",
            )

        axis.set_title(
            f"Average {edge_label} Time vs. Power Setpoint by RF Frequency\n"
            f"Pulse Frequency = {pulse_frequency_hz:g} Hz, Duty Cycle = {duty_cycle:g}%",
            fontsize=14,
            fontweight="bold",
            pad=14,
        )
        axis.set_xlabel("Power Setpoint (W)", fontsize=12)
        axis.set_ylabel(f"Average {edge_label} Time (us)", fontsize=12)

        # Report the full range of the plotted, outlier-filtered averages
        # across every RF frequency and power setpoint on this graph.
        minimum_average_index = condition_df[value_column].idxmin()
        maximum_average_index = condition_df[value_column].idxmax()
        minimum_average_row = condition_df.loc[minimum_average_index]
        maximum_average_row = condition_df.loc[maximum_average_index]

        minimum_average_us = float(minimum_average_row[value_column])
        maximum_average_us = float(maximum_average_row[value_column])
        minimum_average_frequency_khz = float(
            minimum_average_row["RF Frequency (kHz)"]
        )
        maximum_average_frequency_khz = float(
            maximum_average_row["RF Frequency (kHz)"]
        )
        minimum_average_power_w = float(
            minimum_average_row["Power Setpoint (W)"]
        )
        maximum_average_power_w = float(
            maximum_average_row["Power Setpoint (W)"]
        )

        average_range_text = (
            f"Across all plotted RF frequencies:\n"
            f"Minimum average: {minimum_average_us:.3f} us\n"
            f"  {minimum_average_frequency_khz:g} kHz, "
            f"{minimum_average_power_w:g} W\n"
            f"Maximum average: {maximum_average_us:.3f} us\n"
            f"  {maximum_average_frequency_khz:g} kHz, "
            f"{maximum_average_power_w:g} W"
        )

        # Save the unchanged min/max text as a separate companion image.
        info_figure, info_axis = plt.subplots(figsize=(5.2, 1.75))
        info_axis.set_axis_off()
        info_axis.text(
            0.5, 0.5, average_range_text,
            transform=info_axis.transAxes,
            horizontalalignment="center",
            verticalalignment="center",
            fontsize=9,
            bbox={"boxstyle": "round", "facecolor": "white",
                  "edgecolor": "#7F7F7F", "alpha": 0.90},
        )
        axis.xaxis.set_major_locator(MaxNLocator(nbins=10, steps=[1, 2, 2.5, 5, 10]))
        axis.xaxis.set_minor_locator(AutoMinorLocator(2))
        axis.xaxis.set_major_formatter(ScalarFormatter(useOffset=False))
        axis.tick_params(axis="both", which="major", direction="out", length=6, width=1.0)
        axis.tick_params(axis="both", which="minor", direction="out", length=3, width=0.8)

        x_values = condition_df["Power Setpoint (W)"].to_numpy(dtype=float)
        y_values = condition_df[value_column].to_numpy(dtype=float)
        x_min, x_max = float(np.min(x_values)), float(np.max(x_values))
        x_padding = max((x_max - x_min) * 0.04, 1.0)
        axis.set_xlim(x_min - x_padding, x_max + x_padding)

        positive_y = y_values[y_values > 0]
        if edge_type == "FALL" and len(positive_y) > 0:
            smallest_positive = float(np.min(positive_y))
            largest_positive = float(np.max(positive_y))
            dynamic_range = largest_positive / smallest_positive
        else:
            smallest_positive = 0.0
            largest_positive = 0.0
            dynamic_range = 1.0

        if edge_type == "FALL" and dynamic_range >= 20.0 and np.all(y_values > 0):
            axis.set_yscale("log")
            axis.yaxis.set_major_locator(LogLocator(base=10.0))
            axis.yaxis.set_minor_locator(
                LogLocator(base=10.0, subs=np.arange(2, 10) * 0.1)
            )
            axis.yaxis.set_major_formatter(ScalarFormatter())
            axis.yaxis.set_minor_formatter(NullFormatter())
            axis.set_ylim(smallest_positive / 1.25, largest_positive * 1.25)
            axis.set_ylabel("Average Fall Time (us, logarithmic scale)", fontsize=12)
        elif edge_type == "FALL" and dynamic_range >= 20.0 and smallest_positive > 0:
            axis.set_yscale(
                "symlog", linthresh=smallest_positive, linscale=1.0, base=10
            )
            axis.set_ylim(min(0.0, float(np.min(y_values))), largest_positive * 1.25)
            axis.set_ylabel("Average Fall Time (us, hybrid log scale)", fontsize=12)
        else:
            y_min, y_max = float(np.min(y_values)), float(np.max(y_values))
            y_padding = max((y_max - y_min) * 0.08, abs(y_max) * 0.01, 0.001)
            axis.yaxis.set_major_locator(
                MaxNLocator(nbins=10, steps=[1, 2, 2.5, 5, 10])
            )
            axis.yaxis.set_minor_locator(AutoMinorLocator(2))
            axis.yaxis.set_major_formatter(ScalarFormatter(useOffset=False))
            axis.set_ylim(max(0.0, y_min - y_padding), y_max + y_padding)

        if apply_manual_edge_time_ylim(axis, edge_type):
            axis.set_ylabel(f"Average {edge_label} Time (us)", fontsize=12)
            axis.yaxis.set_major_locator(MaxNLocator(nbins=10, steps=[1, 2, 2.5, 5, 10]))
            axis.yaxis.set_minor_locator(AutoMinorLocator(2))
            axis.yaxis.set_major_formatter(ScalarFormatter(useOffset=False))

        axis.ticklabel_format(axis="x", style="plain", useOffset=False)
        axis.grid(True, which="major", color="#BFBFBF", linewidth=0.8, alpha=0.75)
        axis.grid(True, which="minor", color="#E2E2E2", linewidth=0.5, alpha=0.55)
        axis.legend(title="RF Frequency", loc="upper right", frameon=True)
        figure.tight_layout()

        pulse_text = safe_filename_value(pulse_frequency_hz)
        duty_text = safe_filename_value(duty_cycle)
        output_path = os.path.join(
            trend_folder,
            f"{edge_type}_Time_vs_Power_Pulse{pulse_text}Hz_Duty{duty_text}pct.png",
        )
        info_output_path = os.path.join(
            trend_folder,
            f"{edge_type}_Time_vs_Power_Pulse{pulse_text}Hz_Duty{duty_text}pct_Min_Max_Averages.png",
        )
        safe_save_figure(info_figure, info_output_path, dpi=200, bbox_inches="tight", facecolor="white")
        plt.close(info_figure)
        safe_save_figure(figure, output_path, dpi=300, bbox_inches="tight")
        plt.close(figure)
        output_paths.append(output_path)
        print(f"Saved {edge_label.lower()}-time trend plot: {output_path}")

    return output_paths


# ==========================================================
# POWERPOINT SUMMARY TABLES
# ==========================================================

def create_powerpoint_summary(
    rise_summary_df,
    fall_summary_df,
    setpoint_averages_df,
    output_path,
):
    """Create one formatted RISE or FALL table slide per pulsing condition."""
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
        from pptx.util import Inches, Pt
    except ImportError as error:
        raise ImportError(
            "PowerPoint generation requires the 'python-pptx' package. "
            "Install it with: pip install python-pptx"
        ) from error

    if setpoint_averages_df.empty:
        print(
            "Setpoint Averages data are empty. PowerPoint standard deviation "
            "cells will display N/A."
        )

    presentation = Presentation()
    presentation.slide_width = Inches(13.333333)
    presentation.slide_height = Inches(7.5)

    navy = RGBColor(31, 105, 139)
    light_gray = RGBColor(226, 230, 235)
    medium_gray = RGBColor(202, 208, 215)
    white = RGBColor(255, 255, 255)
    black = RGBColor(20, 20, 20)

    def set_cell_text(cell, text, font_size, bold=False, color=black):
        cell.text = str(text)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        paragraph = cell.text_frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.CENTER
        paragraph.space_before = Pt(0)
        paragraph.space_after = Pt(0)
        run = paragraph.runs[0]
        run.font.name = "Arial"
        run.font.size = Pt(font_size)
        run.font.bold = bold
        run.font.color.rgb = color

    def set_cell_fill(cell, color):
        cell.fill.solid()
        cell.fill.fore_color.rgb = color

    def format_time_us(value):
        """Format a microsecond time value using the user setting above."""
        if pd.isna(value):
            return "N/A"
        return f"{float(value):.{POWERPOINT_TABLE_DECIMAL_PLACES}f}"

    def add_edge_slide(edge_summary, edge_type, pulse_hz, duty_pct):
        edge_label = "Rise" if edge_type == "RISE" else "Fall"
        average_col = f"Average_{edge_label}_Time_us"
        minimum_col = f"Minimum_{edge_label}_Time_us"
        maximum_col = f"Maximum_{edge_label}_Time_us"
        std_dev_col = f"Std Dev of RF-Frequency {edge_label} Averages (us)"
        frequencies = sorted(edge_summary["RF Frequency (kHz)"].dropna().unique())
        setpoints = sorted(edge_summary["Power Setpoint (W)"].dropna().unique())
        if not frequencies or not setpoints:
            return

        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = white

        title = slide.shapes.add_textbox(Inches(0.35), Inches(0.12), Inches(12.63), Inches(0.46))
        title_frame = title.text_frame
        title_frame.clear()
        title_paragraph = title_frame.paragraphs[0]
        title_paragraph.alignment = PP_ALIGN.CENTER
        title_run = title_paragraph.add_run()
        title_run.text = (
            f"Pulse-Mode {edge_type} Summary | Pulse {pulse_hz:g} Hz | "
            f"Duty {duty_pct:g}%"
        )
        title_run.font.name = "Arial"
        title_run.font.size = Pt(22)
        title_run.font.bold = True
        title_run.font.color.rgb = navy

        rows = 2 + len(setpoints)
        columns = 2 + 3 * len(frequencies)
        table_shape = slide.shapes.add_table(
            rows, columns, Inches(0.3), Inches(0.72), Inches(12.73), Inches(6.42)
        )
        table = table_shape.table
        table.first_row = True
        table.horz_banding = True

        setpoint_width = 1.35
        std_width = 1.55
        metric_width = (12.73 - setpoint_width - std_width) / (3 * len(frequencies))
        table.columns[0].width = Inches(setpoint_width)
        for column_index in range(1, 1 + 3 * len(frequencies)):
            table.columns[column_index].width = Inches(metric_width)
        table.columns[columns - 1].width = Inches(std_width)

        table.rows[0].height = Inches(0.68)
        table.rows[1].height = Inches(0.48)
        data_height = max(0.34, min(0.54, 5.2 / max(1, len(setpoints))))
        for row_index in range(2, rows):
            table.rows[row_index].height = Inches(data_height)

        table.cell(0, 0).merge(table.cell(1, 0))
        set_cell_text(table.cell(0, 0), "Power\nSetpoint", 13, True, white)
        set_cell_fill(table.cell(0, 0), navy)

        for frequency_index, frequency_khz in enumerate(frequencies):
            start_column = 1 + 3 * frequency_index
            table.cell(0, start_column).merge(table.cell(0, start_column + 2))
            frequency_mhz = float(frequency_khz) / 1000.0
            set_cell_text(
                table.cell(0, start_column),
                f"{frequency_mhz:.3f} MHz {edge_label} (us)",
                13,
                True,
                white,
            )
            set_cell_fill(table.cell(0, start_column), navy)
            for offset, heading in enumerate(("Avg", "Min", "Max")):
                cell = table.cell(1, start_column + offset)
                set_cell_text(cell, heading, 12, True, black)
                set_cell_fill(cell, medium_gray)

        std_column = columns - 1
        table.cell(0, std_column).merge(table.cell(1, std_column))
        set_cell_text(table.cell(0, std_column), "Std Deviation", 13, True, white)
        set_cell_fill(table.cell(0, std_column), navy)

        for data_index, power_w in enumerate(setpoints, start=2):
            row_color = light_gray if data_index % 2 == 0 else medium_gray
            set_cell_text(table.cell(data_index, 0), f"{power_w:g}", 11)
            set_cell_fill(table.cell(data_index, 0), row_color)
            for frequency_index, frequency_khz in enumerate(frequencies):
                start_column = 1 + 3 * frequency_index
                match = edge_summary[
                    np.isclose(edge_summary["Power Setpoint (W)"], power_w)
                    & np.isclose(edge_summary["RF Frequency (kHz)"], frequency_khz)
                ]
                if match.empty:
                    values = (np.nan, np.nan, np.nan)
                else:
                    row = match.iloc[0]
                    values = (
                        row[average_col],
                        row[minimum_col],
                        row[maximum_col],
                    )

                for offset, value in enumerate(values):
                    cell = table.cell(data_index, start_column + offset)
                    set_cell_text(cell, format_time_us(value), 10.5)
                    set_cell_fill(cell, row_color)

            # Use the existing value written to the Setpoint Averages sheet.
            # This avoids independently recalculating the standard deviation.
            std_match = setpoint_averages_df[
                np.isclose(setpoint_averages_df["Power Setpoint (W)"], power_w)
                & np.isclose(
                    setpoint_averages_df["Pulse Frequency (Hz)"], pulse_hz
                )
                & np.isclose(setpoint_averages_df["Duty Cycle (%)"], duty_pct)
            ]
            std_value = (
                std_match.iloc[0][std_dev_col]
                if not std_match.empty and std_dev_col in std_match.columns
                else np.nan
            )
            set_cell_text(
                table.cell(data_index, std_column),
                format_time_us(std_value),
                10.5,
            )
            set_cell_fill(table.cell(data_index, std_column), row_color)

    for edge_summary, edge_type in (
        (rise_summary_df, "RISE"),
        (fall_summary_df, "FALL"),
    ):
        if edge_summary.empty:
            continue
        for (pulse_hz, duty_pct), condition_df in edge_summary.groupby(
            ["Pulse Frequency (Hz)", "Duty Cycle (%)"],
            dropna=False,
            sort=True,
        ):
            add_edge_slide(condition_df, edge_type, pulse_hz, duty_pct)

    if len(presentation.slides) == 0:
        print("No summary data were available for PowerPoint generation.")
        return None

    presentation.save(output_path)
    print(f"PowerPoint summary saved to: {output_path}")
    return output_path


# ==========================================================
# EXCEL FORMATTING
# ==========================================================

def format_excel_workbook(writer):
    workbook = writer.book

    for worksheet in workbook.worksheets:
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        for column_cells in worksheet.columns:
            max_length = 0
            column_letter = column_cells[0].column_letter

            for cell in column_cells:
                value = "" if cell.value is None else str(cell.value)
                max_length = max(max_length, len(value))

            worksheet.column_dimensions[column_letter].width = min(
                max(max_length + 2, 12),
                60
            )

# ==========================================================
# OPEN OUTPUT FILE
# ==========================================================

def open_output_file(file_path):
    try:
        if os.name == "nt":
            os.startfile(file_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", file_path])
        else:
            subprocess.Popen(["xdg-open", file_path])
    except Exception as error:
        print(f"Could not automatically open Excel file: {error}")

# ==========================================================
# ANALYSIS PROGRESS WINDOW
# ==========================================================
class AnalysisProgressWindow:
    """Responsive progress display for long-running, single-threaded analysis."""

    def __init__(self, parent):
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("SLP Analysis Progress")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._prevent_close)

        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="SLP waveform analysis is running",
            font=("Segoe UI", 13, "bold")
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="This window will remain responsive while files and outputs are processed.",
            foreground="#555555", wraplength=560
        ).pack(anchor="w", pady=(3, 14))

        self.phase_var = tk.StringVar(value="Preparing analysis...")
        self.detail_var = tk.StringVar(value="Initializing")
        self.percent_var = tk.StringVar(value="0%")
        ttk.Label(frame, textvariable=self.phase_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.detail_var, wraplength=560).pack(anchor="w", pady=(3, 9))

        progress_row = ttk.Frame(frame)
        progress_row.pack(fill="x")
        self.bar = ttk.Progressbar(progress_row, mode="determinate", maximum=100, length=500)
        self.bar.pack(side="left", fill="x", expand=True)
        ttk.Label(progress_row, textvariable=self.percent_var, width=6, anchor="e").pack(side="left", padx=(10, 0))

        self.activity = ttk.Progressbar(frame, mode="indeterminate", length=560)
        self.activity.pack(fill="x", pady=(10, 0))
        self.activity.start(12)

        ttk.Label(
            frame, text="Do not close the console or Python process while analysis is running.",
            foreground="#666666"
        ).pack(anchor="w", pady=(12, 0))

        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = max(0, (self.window.winfo_screenwidth() - width) // 2)
        y = max(0, (self.window.winfo_screenheight() - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.deiconify()
        self.window.lift()
        try:
            self.window.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.refresh()

    def _prevent_close(self):
        try:
            self.window.bell()
        except tk.TclError:
            pass

    def refresh(self):
        try:
            if not self.window.winfo_viewable():
                self.window.deiconify()
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
        if phase is not None:
            self.phase_var.set(str(phase))
        if detail is not None:
            self.detail_var.set(str(detail))
        self.refresh()

    def close(self):
        try:
            self.activity.stop()
            self.window.destroy()
        except tk.TclError:
            pass

# ==========================================================
# TWO-DATA-SET COMPARISON HELPERS
# ==========================================================

def process_comparison_folder(folder_path, configuration_name, progress=None, start_percent=42.0, end_percent=65.0):
    """Analyze a second folder without changing the original first-folder workflow."""
    rise_rows, fall_rows, failures = [], [], []
    files = sorted(
        filename for filename in os.listdir(folder_path)
        if filename.lower().endswith(".csv")
    )
    for number, filename in enumerate(files, start=1):
        if progress is not None:
            progress.update(
                start_percent + (number - 1) / max(1, len(files)) * (end_percent - start_percent),
                f"Analyzing {configuration_name}",
                f"File {number} of {len(files)}: {filename}"
            )
        csv_path = os.path.join(folder_path, filename)
        print(f"[{configuration_name}] Processing {number}/{len(files)}: {filename}")
        try:
            info = extract_file_info(filename)
            time_values, voltage_values, raw_count, invalid_count = read_scope_csv(csv_path)
            calculation = analyze_waveform(time_values, voltage_values, info)
            edge_time = calculation["Edge Time (s)"]
            row = {
                "Filename": filename,
                "CSV Path": csv_path,
                **info,
                **calculation_diagnostics(calculation),
                "Baseline Voltage (V)": calculation["Baseline Voltage (V)"],
                "Max Voltage (V)": calculation["Max Voltage (V)"],
                "Signal Span (V)": calculation["Signal Span (V)"],
            "Pulse Off Noise Floor Max (V)": calculation["Pulse Off Noise Floor Max (V)"],
            "Low Power Threshold Rule Used": calculation["Low Power Threshold Rule Used"],
                "10% Voltage (V)": calculation["10% Voltage (V)"],
                "90% Voltage (V)": calculation["90% Voltage (V)"],
                "t10 (s)": calculation["t10 (s)"],
                "t90 (s)": calculation["t90 (s)"],
                "Edge Time (s)": edge_time,
                "Edge Time (us)": edge_time * 1e6,
                "Edge Time (ns)": edge_time * 1e9,
                "Valid Samples": len(time_values),
                "Raw Data Rows": raw_count,
                "Invalid Samples Removed": invalid_count,
                "Linear Interpolation Used": USE_LINEAR_INTERPOLATION,
                "Baseline Referenced Thresholds": USE_BASELINE_REFERENCED_THRESHOLDS,
                "Configuration": configuration_name,
            }
            if info["Type"] == "RISE":
                row["Rise Time (s)"] = edge_time
                row["Rise Time (us)"] = edge_time * 1e6
                row["Rise Time (ns)"] = edge_time * 1e9
                rise_rows.append(row)
            elif info["Type"] == "FALL":
                row["Fall Time (s)"] = edge_time
                row["Fall Time (us)"] = edge_time * 1e6
                row["Fall Time (ns)"] = edge_time * 1e9
                fall_rows.append(row)
            else:
                raise ValueError(f"Unsupported edge type: {info['Type']}")
        except Exception as error:
            failures.append({"Configuration": configuration_name,
                             "Filename": filename, "Error": str(error)})
    return pd.DataFrame(rise_rows), pd.DataFrame(fall_rows), pd.DataFrame(failures)


def create_configuration_comparison(summary_a, summary_b, edge_type, name_a, name_b):
    """Match complete test conditions and calculate signed non-transparency."""
    keys = ["Pulse Frequency (Hz)", "Duty Cycle (%)",
            "RF Frequency (kHz)", "Power Setpoint (W)"]
    avg_col = f"Average_{'Rise' if edge_type == 'RISE' else 'Fall'}_Time_us"
    std_col = f"Std_Dev_{'Rise' if edge_type == 'RISE' else 'Fall'}_Time_us"
    count_col = "Included_Sample_Count"
    wanted = keys + [avg_col, std_col, count_col]
    if summary_a.empty or summary_b.empty:
        return pd.DataFrame()
    a = summary_a[wanted].rename(columns={
        avg_col: f"{name_a} Average (us)", std_col: f"{name_a} Std Dev (us)",
        count_col: f"{name_a} Samples Used"})
    b = summary_b[wanted].rename(columns={
        avg_col: f"{name_b} Average (us)", std_col: f"{name_b} Std Dev (us)",
        count_col: f"{name_b} Samples Used"})
    merged = a.merge(b, on=keys, how="outer", indicator=True)
    avga, avgb = f"{name_a} Average (us)", f"{name_b} Average (us)"
    merged["Matched Condition"] = merged["_merge"].eq("both")
    merged["Difference B-A (us)"] = merged[avgb] - merged[avga]
    merged["Absolute Difference (us)"] = merged["Difference B-A (us)"].abs()
    merged["Percent Difference vs A (%)"] = np.where(
        merged[avga].notna() & (merged[avga] != 0),
        100.0 * merged["Difference B-A (us)"] / merged[avga], np.nan)
    merged["Absolute Percent Difference (%)"] = merged["Percent Difference vs A (%)"].abs()
    merged["B/A Ratio"] = np.where(
        merged[avga].notna() & (merged[avga] != 0), merged[avgb] / merged[avga], np.nan)
    pooled = np.sqrt(merged[f"{name_a} Std Dev (us)"].fillna(0) ** 2 +
                     merged[f"{name_b} Std Dev (us)"].fillna(0) ** 2)
    merged["Difference / Combined Std Dev"] = np.where(
        pooled > 0, merged["Absolute Difference (us)"] / pooled, np.nan)
    merged["Edge"] = edge_type
    merged["Availability"] = merged["_merge"].map({
        "both": "Matched", "left_only": f"Only {name_a}", "right_only": f"Only {name_b}"})
    return merged.drop(columns=["_merge"]).sort_values(keys).reset_index(drop=True)


def create_comparison_plots(comparison_df, edge_type, name_a, name_b, folder):
    """Overlay both configurations and plot B-A using common, data-driven scales."""
    if comparison_df.empty:
        return
    os.makedirs(folder, exist_ok=True)
    avg_a, avg_b = f"{name_a} Average (us)", f"{name_b} Average (us)"
    matched = comparison_df[comparison_df["Matched Condition"]].copy()
    if matched.empty:
        return
    y_values = pd.concat([matched[avg_a], matched[avg_b]]).dropna()
    y_min, y_max = float(y_values.min()), float(y_values.max())
    y_pad = max((y_max - y_min) * 0.08, abs(y_max) * 0.02, 0.001)
    dmax = matched["Absolute Difference (us)"].max()
    dlim = max(float(dmax) * 1.10, 0.001)
    for (pulse_hz, duty), group in matched.groupby(
            ["Pulse Frequency (Hz)", "Duty Cycle (%)"], dropna=False):
        fig, (ax_top, ax_delta) = plt.subplots(
            2, 1, figsize=(11, 8), sharex=True,
            gridspec_kw={"height_ratios": [2.2, 1.0]})
        for rf_khz, rf_group in group.groupby("RF Frequency (kHz)"):
            rf_group = rf_group.sort_values("Power Setpoint (W)")
            x = rf_group["Power Setpoint (W)"]
            ax_top.plot(x, rf_group[avg_a], marker="o", linestyle="-",
                        label=f"{name_a}, {rf_khz:g} kHz")
            ax_top.plot(x, rf_group[avg_b], marker="s", linestyle="--",
                        label=f"{name_b}, {rf_khz:g} kHz")
            ax_delta.plot(x, rf_group["Difference B-A (us)"], marker="o",
                          label=f"{rf_khz:g} kHz")
        ax_top.set_ylim(y_min - y_pad, y_max + y_pad)
        apply_manual_edge_time_ylim(ax_top, edge_type)
        ax_delta.set_ylim(-dlim, dlim)
        ax_top.set_ylabel(f"Average {edge_type.lower()} time (us)")
        ax_delta.set_ylabel("B - A (us)")
        ax_delta.set_xlabel("Power setpoint (W)")
        ax_delta.axhline(0, color="black", linewidth=1)
        ax_top.grid(True, alpha=0.3)
        ax_delta.grid(True, alpha=0.3)
        ax_top.legend(loc="best")
        ax_delta.legend(loc="best")
        fig.suptitle(f"{edge_type} comparison: {pulse_hz:g} Hz, {duty:g}% duty")
        fig.tight_layout()
        filename = (f"Comparison_{edge_type}_{safe_filename_value(pulse_hz)}Hz_"
                    f"{safe_filename_value(duty)}Pct.png")
        safe_save_figure(fig, os.path.join(folder, filename), dpi=200, bbox_inches="tight")
        plt.close(fig)


# ==========================================================
# PROCESS CSV FILES
# ==========================================================

progress_window = AnalysisProgressWindow(root)
progress_window.update(1, "Discovering input files", f"Scanning {configuration_a_name}")
rise_results = []
fall_results = []
failed_files = []

csv_files = sorted(
    filename for filename in os.listdir(input_folder)
    if filename.lower().endswith(".csv")
)

if not csv_files:
    messagebox.showwarning(
        "No CSV files found",
        "No CSV files were found in the selected folder.",
        parent=root
    )
    progress_window.close()
    root.destroy()
    raise SystemExit("No CSV files found.")

print(f"Found {len(csv_files)} CSV file(s).")

for file_number, filename in enumerate(csv_files, start=1):
    primary_end = 42.0 if ANALYSIS_MODE == "COMPARE" else 58.0
    progress_window.update(
        3.0 + (file_number - 1) / max(1, len(csv_files)) * (primary_end - 3.0),
        f"Analyzing {configuration_a_name}",
        f"File {file_number} of {len(csv_files)}: {filename}"
    )
    csv_path = os.path.join(input_folder, filename)

    print("\n==================================================")
    print(f"Processing file {file_number}/{len(csv_files)}")
    print(filename)
    print("==================================================")

    try:
        file_info = extract_file_info(filename)
        edge_type = file_info["Type"]

        (
            time_values,
            voltage_values,
            raw_sample_count,
            invalid_samples_removed
        ) = read_scope_csv(csv_path)

        if edge_type not in ("RISE", "FALL"):
            raise ValueError(f"Unsupported edge type: {edge_type}")
        calculation = analyze_waveform(time_values, voltage_values, file_info)

        edge_time = calculation["Edge Time (s)"]

        result = {
            "Filename": filename,
            "CSV Path": csv_path,
            **file_info,
            **calculation_diagnostics(calculation),
            "Baseline Voltage (V)": calculation["Baseline Voltage (V)"],
            "Max Voltage (V)": calculation["Max Voltage (V)"],
            "Signal Span (V)": calculation["Signal Span (V)"],
            "Pulse Off Noise Floor Max (V)": calculation["Pulse Off Noise Floor Max (V)"],
            "Low Power Threshold Rule Used": calculation["Low Power Threshold Rule Used"],
            "10% Voltage (V)": calculation["10% Voltage (V)"],
            "90% Voltage (V)": calculation["90% Voltage (V)"],
            "t10 (s)": calculation["t10 (s)"],
            "t90 (s)": calculation["t90 (s)"],
            "Edge Time (s)": edge_time,
            "Edge Time (us)": edge_time * 1e6,
            "Edge Time (ns)": edge_time * 1e9,
            "Valid Samples": len(time_values),
            "Raw Data Rows": raw_sample_count,
            "Invalid Samples Removed": invalid_samples_removed,
            "Linear Interpolation Used": USE_LINEAR_INTERPOLATION,
            "Baseline Referenced Thresholds": USE_BASELINE_REFERENCED_THRESHOLDS
        }

        if edge_type == "RISE":
            result["Rise Time (s)"] = edge_time
            result["Rise Time (us)"] = edge_time * 1e6
            result["Rise Time (ns)"] = edge_time * 1e9
            rise_results.append(result)
        else:
            result["Fall Time (s)"] = edge_time
            result["Fall Time (us)"] = edge_time * 1e6
            result["Fall Time (ns)"] = edge_time * 1e9
            fall_results.append(result)

        print(f"Type             = {edge_type}")
        print(f"RF frequency     = {file_info['RF Frequency (kHz)']} kHz")
        print(f"Power            = {file_info['Power Setpoint (W)']} W")
        print(f"Pulse frequency  = {file_info['Pulse Frequency (Hz)']} Hz")
        print(f"Duty cycle       = {file_info['Duty Cycle (%)']} percent")
        print(f"Baseline voltage = {calculation['Baseline Voltage (V)']:.6f} V")
        print(f"Maximum voltage  = {calculation['Max Voltage (V)']:.6f} V")
        print(f"Edge time        = {edge_time:.9e} s")

        if GENERATE_PLOTS:
            plot_path = save_analysis_plot(
                filename,
                edge_type,
                time_values,
                (voltage_values if WAVEFORM_ANALYSIS_METHOD == ANALYSIS_METHOD_RMS_COMPLETE_PULSE else calculation["Analysis Signal"]),
                calculation
            )
            print(f"Saved plot: {plot_path}")

    except Exception as error:
        print(f"ERROR processing {filename}: {error}")
        failed_files.append({
            "Filename": filename,
            "Error": str(error)
        })

# ==========================================================
# CREATE DATAFRAMES
# ==========================================================
progress_window.update(60 if ANALYSIS_MODE == "SINGLE" else 40, "Calculating summaries", "Applying IQR filtering and building summary tables")

rise_df = pd.DataFrame(rise_results)
fall_df = pd.DataFrame(fall_results)
failed_df = pd.DataFrame(failed_files)

sort_columns = [
    "Pulse Frequency (Hz)",
    "Duty Cycle (%)",
    "RF Frequency (kHz)",
    "Power Setpoint (W)",
    "Acquisition Number",
    "Filename"
]

if not rise_df.empty:
    rise_df = rise_df.sort_values(by=sort_columns, ascending=True)

if not fall_df.empty:
    fall_df = fall_df.sort_values(by=sort_columns, ascending=True)

rise_summary_df, rise_df, rise_outliers_df = create_summary(
    rise_df,
    "RISE"
)

fall_summary_df, fall_df, fall_outliers_df = create_summary(
    fall_df,
    "FALL"
)

frequency_min_max_df = create_frequency_min_max_summary(
    rise_df,
    fall_df
)

if not frequency_min_max_df.empty:
    frequency_min_max_df.to_csv(
        frequency_min_max_csv_file,
        index=False
    )
    print(
        "Frequency min/max CSV saved to: "
        f"{frequency_min_max_csv_file}"
    )

rise_setpoint_average_df = create_cross_rf_frequency_setpoint_average(
    rise_summary_df,
    "RISE"
)
fall_setpoint_average_df = create_cross_rf_frequency_setpoint_average(
    fall_summary_df,
    "FALL"
)
setpoint_averages_df = combine_setpoint_averages(
    rise_setpoint_average_df,
    fall_setpoint_average_df
)


comparison_excel_file = None
comparison_output_folder = None
comparison_rise_df = pd.DataFrame()
comparison_fall_df = pd.DataFrame()
comparison_failed_df = pd.DataFrame()
comparison_rise_summary_df = pd.DataFrame()
comparison_fall_summary_df = pd.DataFrame()
rise_comparison_df = pd.DataFrame()
fall_comparison_df = pd.DataFrame()

if ANALYSIS_MODE == "COMPARE":
    print("\nAnalyzing Configuration B for comparison...", flush=True)
    comparison_rise_df, comparison_fall_df, comparison_failed_df = process_comparison_folder(
        comparison_folder, configuration_b_name, progress_window, 42.0, 65.0)
    if not comparison_rise_df.empty:
        comparison_rise_summary_df, comparison_rise_df, comparison_rise_outliers_df = create_summary(
            comparison_rise_df, "RISE")
    else:
        comparison_rise_outliers_df = pd.DataFrame()
    if not comparison_fall_df.empty:
        comparison_fall_summary_df, comparison_fall_df, comparison_fall_outliers_df = create_summary(
            comparison_fall_df, "FALL")
    else:
        comparison_fall_outliers_df = pd.DataFrame()

    rise_comparison_df = create_configuration_comparison(
        rise_summary_df, comparison_rise_summary_df, "RISE",
        configuration_a_name, configuration_b_name)
    fall_comparison_df = create_configuration_comparison(
        fall_summary_df, comparison_fall_summary_df, "FALL",
        configuration_a_name, configuration_b_name)

    comparison_output_folder = os.path.join(input_folder, "Hardware Configuration Comparison")
    comparison_plot_folder = os.path.join(comparison_output_folder, "Comparison Plots")
    os.makedirs(comparison_output_folder, exist_ok=True)
    create_comparison_plots(rise_comparison_df, "RISE", configuration_a_name,
                            configuration_b_name, comparison_plot_folder)
    create_comparison_plots(fall_comparison_df, "FALL", configuration_a_name,
                            configuration_b_name, comparison_plot_folder)

    comparison_excel_file = os.path.join(
        comparison_output_folder, "Pulse_Mode_Hardware_Comparison.xlsx")
    with pd.ExcelWriter(comparison_excel_file, engine="openpyxl") as comparison_writer:
        if not rise_comparison_df.empty:
            rise_comparison_df.to_excel(comparison_writer, sheet_name="RISE Comparison", index=False)
        if not fall_comparison_df.empty:
            fall_comparison_df.to_excel(comparison_writer, sheet_name="FALL Comparison", index=False)
        combined_nontransparency = pd.concat(
            [rise_comparison_df, fall_comparison_df], ignore_index=True)
        if not combined_nontransparency.empty:
            combined_nontransparency = combined_nontransparency.sort_values(
                ["Absolute Percent Difference (%)", "Absolute Difference (us)"],
                ascending=[False, False], na_position="last")
            combined_nontransparency.to_excel(
                comparison_writer, sheet_name="Non-Transparency Ranking", index=False)
        if not comparison_rise_summary_df.empty:
            comparison_rise_summary_df.to_excel(
                comparison_writer, sheet_name="B RISE Summary", index=False)
        if not comparison_fall_summary_df.empty:
            comparison_fall_summary_df.to_excel(
                comparison_writer, sheet_name="B FALL Summary", index=False)
        if not comparison_failed_df.empty:
            comparison_failed_df.to_excel(
                comparison_writer, sheet_name="B Failed Files", index=False)
        pd.DataFrame({
            "Item": ["Configuration A", "Configuration A Folder",
                     "Configuration B", "Configuration B Folder",
                     "Difference Convention", "Percent Difference Convention"],
            "Value": [configuration_a_name, input_folder,
                      configuration_b_name, comparison_folder,
                      "Configuration B minus Configuration A",
                      "100 * (B - A) / A"]
        }).to_excel(comparison_writer, sheet_name="Comparison Settings", index=False)
        format_excel_workbook(comparison_writer)
    print(f"Comparison workbook saved to: {comparison_excel_file}")

if GENERATE_SUMMARY_PLOTS:
    progress_window.update(70 if ANALYSIS_MODE == "SINGLE" else 76, "Generating summary waveform plots", configuration_a_name)
    print(f"Generating group summary plots for {configuration_a_name}...", flush=True)
    run_plot_step_nonfatal(
        f"RISE summary plots for {configuration_a_name}",
        create_group_summary_plots, rise_df, "RISE", SHOW_SUMMARY_SAMPLE_INFO, output_folder
    )
    run_plot_step_nonfatal(
        f"FALL summary plots for {configuration_a_name}",
        create_group_summary_plots, fall_df, "FALL", SHOW_SUMMARY_SAMPLE_INFO, output_folder
    )

progress_window.update(76 if ANALYSIS_MODE == "SINGLE" else 81, "Generating trend plots", configuration_a_name)
run_plot_step_nonfatal(
    f"RISE trend plots for {configuration_a_name}",
    create_edge_time_trend_plots, rise_summary_df, "RISE", output_folder
)
run_plot_step_nonfatal(
    f"FALL trend plots for {configuration_a_name}",
    create_edge_time_trend_plots, fall_summary_df, "FALL", output_folder
)

if ANALYSIS_MODE == "COMPARE":
    comparison_dataset_output_folder = os.path.join(comparison_folder, "Modified Plots")
    os.makedirs(comparison_dataset_output_folder, exist_ok=True)
    if GENERATE_SUMMARY_PLOTS:
        print(f"Generating group summary plots for {configuration_b_name}...", flush=True)
        run_plot_step_nonfatal(
            f"RISE summary plots for {configuration_b_name}",
            create_group_summary_plots, comparison_rise_df, "RISE",
            SHOW_SUMMARY_SAMPLE_INFO, comparison_dataset_output_folder
        )
        run_plot_step_nonfatal(
            f"FALL summary plots for {configuration_b_name}",
            create_group_summary_plots, comparison_fall_df, "FALL",
            SHOW_SUMMARY_SAMPLE_INFO, comparison_dataset_output_folder
        )
    print(f"Generating trend plots for {configuration_b_name}...", flush=True)
    run_plot_step_nonfatal(
        f"RISE trend plots for {configuration_b_name}",
        create_edge_time_trend_plots, comparison_rise_summary_df, "RISE",
        comparison_dataset_output_folder
    )
    run_plot_step_nonfatal(
        f"FALL trend plots for {configuration_b_name}",
        create_edge_time_trend_plots, comparison_fall_summary_df, "FALL",
        comparison_dataset_output_folder
    )

# ==========================================================
# WRITE EXCEL WORKBOOK
# ==========================================================
progress_window.update(86, "Writing Excel workbook", excel_output_file)

if rise_df.empty and fall_df.empty:
    if not failed_df.empty:
        failed_output_file = os.path.join(
            input_folder,
            "Pulse_Mode_Analysis_Failed_Files.xlsx"
        )

        with pd.ExcelWriter(
            failed_output_file,
            engine="openpyxl"
        ) as writer:
            failed_df.to_excel(
                writer,
                sheet_name="Failed Files",
                index=False
            )
            format_excel_workbook(writer)

        print("\nNo valid waveform files were analyzed.")
        print(f"Failure log saved to: {failed_output_file}")
        open_output_file(failed_output_file)

        messagebox.showwarning(
            "No valid files analyzed",
            "No valid waveform files were analyzed.\n\n"
            f"A failure log was saved to:\n{failed_output_file}",
            parent=root
        )
    else:
        messagebox.showwarning(
            "No valid files analyzed",
            "No valid RISE or FALL files were analyzed.",
            parent=root
        )

    progress_window.close()
    root.destroy()
    raise SystemExit("No valid waveform files were analyzed.")

with pd.ExcelWriter(
    excel_output_file,
    engine="openpyxl"
) as writer:
    if not setpoint_averages_df.empty:
        setpoint_averages_df.to_excel(
            writer,
            sheet_name="Setpoint Averages",
            index=False
        )

    if not frequency_min_max_df.empty:
        frequency_min_max_df.to_excel(
            writer,
            sheet_name="Frequency Min Max",
            index=False
        )

    if not rise_summary_df.empty:
        rise_summary_df.to_excel(
            writer,
            sheet_name="RISE Summary",
            index=False
        )

    if not fall_summary_df.empty:
        fall_summary_df.to_excel(
            writer,
            sheet_name="FALL Summary",
            index=False
        )

    if not rise_df.empty:
        rise_df.to_excel(
            writer,
            sheet_name="RISE Individual Results",
            index=False
        )

    if not fall_df.empty:
        fall_df.to_excel(
            writer,
            sheet_name="FALL Individual Results",
            index=False
        )

    if not rise_outliers_df.empty:
        rise_outliers_df.to_excel(
            writer,
            sheet_name="RISE Excluded Outliers",
            index=False
        )

    if not fall_outliers_df.empty:
        fall_outliers_df.to_excel(
            writer,
            sheet_name="FALL Excluded Outliers",
            index=False
        )

    if not failed_df.empty:
        failed_df.to_excel(
            writer,
            sheet_name="Failed Files",
            index=False
        )

    analysis_settings_df = pd.DataFrame({
        "Setting": [
            "Waveform Analysis Method",
            "Data Start Excel Row",
            "Low Threshold Fraction",
            "High Threshold Fraction",
            "Linear Interpolation",
            "Subtract Baseline",
            "Pulse Off Baseline Sample Count",
            "Low Power Threshold (W)",
            "Use Low Power Noise Floor Threshold",
            "Baseline Referenced Thresholds",
            "Minimum Samples for IQR",
            "IQR Multiplier",
            "RMS Envelope Cycles",
            "Minimum Complete Pulse Duration Ratio",
            "Maximum Complete Pulse Duration Ratio",
            "Plot Mode",
            "RISE Manual Vertical Minimum (us)",
            "RISE Manual Vertical Maximum (us)",
            "FALL Manual Vertical Minimum (us)",
            "FALL Manual Vertical Maximum (us)",
            "Summary Plot Sample Information",
            "PowerPoint Table Decimal Places",
            "PowerPoint Table Time Unit"
        ],
        "Value": [
            WAVEFORM_ANALYSIS_METHOD,
            DATA_START_ROW_INDEX + 1,
            LOW_THRESHOLD_FRACTION,
            HIGH_THRESHOLD_FRACTION,
            USE_LINEAR_INTERPOLATION,
            SUBTRACT_BASELINE,
            BASELINE_OFF_STATE_SAMPLE_COUNT,
            LOW_POWER_THRESHOLD_W,
            USE_LOW_POWER_NOISE_FLOOR_THRESHOLD,
            USE_BASELINE_REFERENCED_THRESHOLDS,
            MIN_SAMPLES_FOR_IQR,
            IQR_MULTIPLIER,
            ENVELOPE_RMS_CYCLES,
            MIN_COMPLETE_PULSE_DURATION_RATIO,
            MAX_COMPLETE_PULSE_DURATION_RATIO,
            PLOT_MODE,
            EDGE_TIME_VERTICAL_LIMITS_US["RISE"][0] if EDGE_TIME_VERTICAL_LIMITS_US["RISE"] else "Automatic",
            EDGE_TIME_VERTICAL_LIMITS_US["RISE"][1] if EDGE_TIME_VERTICAL_LIMITS_US["RISE"] else "Automatic",
            EDGE_TIME_VERTICAL_LIMITS_US["FALL"][0] if EDGE_TIME_VERTICAL_LIMITS_US["FALL"] else "Automatic",
            EDGE_TIME_VERTICAL_LIMITS_US["FALL"][1] if EDGE_TIME_VERTICAL_LIMITS_US["FALL"] else "Automatic",
            "Shown" if SHOW_SUMMARY_SAMPLE_INFO else "Hidden",
            POWERPOINT_TABLE_DECIMAL_PLACES,
            "us"
        ]
    })

    analysis_settings_df.to_excel(
        writer,
        sheet_name="Analysis Settings",
        index=False
    )

    format_excel_workbook(writer)

progress_window.update(93, "Creating PowerPoint summary", powerpoint_output_file)
powerpoint_result = create_powerpoint_summary(
    rise_summary_df,
    fall_summary_df,
    setpoint_averages_df,
    powerpoint_output_file,
)

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
        manifest_path=export_powerpoint_package(generated_images,rise_summary_df,fall_summary_df,comparison_rise_summary_df,comparison_fall_summary_df,rise_comparison_df,fall_comparison_df,comparison_excel_file)
        if manifest_path:print(f"PowerPoint package manifest saved to: {manifest_path}")
    except Exception as error:
        package_warning=f"PowerPoint package export warning: {error}";print(package_warning,file=sys.stderr)

# ==========================================================
# COMPLETION
# ==========================================================
progress_window.update(100, "Analysis complete", "All requested outputs have been generated")

print("\nFinished processing all CSV files.")
print(f"Valid RISE files: {len(rise_df)}")
print(f"Valid FALL files: {len(fall_df)}")
print(f"Failed files: {len(failed_df)}")
print(f"Excel workbook saved to: {excel_output_file}")
if comparison_excel_file:
    print(f"Hardware comparison workbook saved to: {comparison_excel_file}")
if powerpoint_result:
    print(f"PowerPoint summary saved to: {powerpoint_result}")
if not frequency_min_max_df.empty:
    print(f"Frequency min/max CSV saved to: {frequency_min_max_csv_file}")

open_output_file(excel_output_file)
progress_window.close()

messagebox.showinfo(
    "Analysis complete",
    "Pulse-mode waveform analysis is complete.\n\n"
    f"Valid RISE files: {len(rise_df)}\n"
    f"Valid FALL files: {len(fall_df)}\n"
    f"Failed files: {len(failed_df)}\n\n"
    f"Excel workbook:\n{excel_output_file}" +
    (f"\n\nHardware comparison workbook:\n{comparison_excel_file}" if comparison_excel_file else "") +
    (f"\n\n{package_warning}\nNormal analysis outputs were still completed." if package_warning else ""),
    parent=root
)

try:
    root.destroy()
except Exception:
    pass

print("Script finished.")
