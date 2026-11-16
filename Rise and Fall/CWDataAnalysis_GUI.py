# Note: this script has been verified as functional on 9/9/26

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from suite_common.powerpoint import powerpoint_export_requested, snapshot_png_files, changed_png_files
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Split very large waveform paths into renderer-sized chunks. CW scope files
# can contain millions of vertices across the overlaid summary traces; leaving
# this at Matplotlib's default of 0 can exceed the Agg renderer cell limit.
# This changes only how paths are rendered, not the waveform data or analysis.
plt.rcParams["agg.path.chunksize"] = 10000
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from scipy.signal import hilbert, butter, filtfilt

# TODO:
# Add feature to automatically detect whether the capture has a "ghost pulse" present at the end. And appropriately exclude the pulse from calculation of noise floor.

# ==========================================================
# SETPOINT-DEPENDENT WAVEFORM PROCESSING SETTINGS
# ==========================================================
# NOTE: SMALL_SETPOINT_THRESHOLD value no longer controls the setpoint for which hilbert is applied. Now all setpoints receive hilbert.
# this variable for now, only controls the range for which the median and not the average is plotted on trend plot.
# above this value, trend plots are of average fall time not median fall time.
SMALL_SETPOINT_THRESHOLD = 500
MIN_SAMPLES_FOR_IQR = 4
IQR_MULTIPLIER = 1.5

# Selectable envelope-analysis methods. Hilbert remains the default so existing
# results and workflows are unchanged unless RMS Envelope is explicitly selected.
ANALYSIS_METHOD_HILBERT = "HILBERT"
ANALYSIS_METHOD_RMS = "RMS"
ENVELOPE_RMS_CYCLES = 8.0
MIN_ENVELOPE_WINDOW_SAMPLES = 5
MAX_ENVELOPE_WINDOW_SAMPLES = 5001
RMS_PLATEAU_FRACTION = 0.10
RMS_MIN_PLATEAU_SAMPLES = 20

# BELOW IS THE RISE TIME CUTOFF FREQ FOR HILBERT TRANSFORM

RISE_FILTER_CUTOFF_HZ = 600

# These defaults are retained for unexpected setpoints. The helper function
# below applies the requested double-digit and triple-digit behavior.

# BELOW IS THE FALL TIME CUTOFF FREQ FOR HILBERT TRANSFORM

DEFAULT_FILTER_CUTOFF_HZ = 2000.0
DOUBLE_DIGIT_FILTER_CUTOFF_HZ = 35000.0
TRIPLE_DIGIT_FILTER_CUTOFF_HZ = 2000.0

# FALL-only analysis region. Fractions are expressed from 0.0 to 1.0.
FALL_LEFT_EXCLUSION_FRACTION = 0.15
DEFAULT_FALL_RIGHT_EXCLUSION_FRACTION = 0.0
DOUBLE_DIGIT_FALL_RIGHT_EXCLUSION_FRACTION = 0.15
TRIPLE_DIGIT_FALL_RIGHT_EXCLUSION_FRACTION = 0.03

# Estimate the FALL noise floor from the lower portion of the retained middle
# region. Thresholds are referenced from this floor to the retained-region max.
FALL_NOISE_FLOOR_PERCENTILE = 3.0


def get_setpoint_processing_settings(power_setpoint_w):
    """Return filter cutoff and FALL right exclusion for one power setpoint.

    Double-digit setpoints (10 W through 99.999... W) use a 35 kHz Hilbert
    low-pass cutoff and exclude the final 10 percent of FALL samples.

    Triple-digit and higher setpoints (100 W and above) use a 2 kHz Hilbert
    low-pass cutoff. All RISE and FALL setpoints now use Hilbert-envelope timing.
    Double-digit FALL captures retain the 10 percent right-exclusion setting;
    triple-digit and higher FALL captures retain zero right-side exclusion.
    """
    power = float(power_setpoint_w)
    if not np.isfinite(power) or power < 0:
        raise ValueError("Power setpoint must be a finite, nonnegative value.")

    is_double_digit = 10.0 <= power < 100.0
    is_triple_digit_or_higher = power >= 100.0

    if is_double_digit:
        filter_cutoff_hz = DOUBLE_DIGIT_FILTER_CUTOFF_HZ
    elif is_triple_digit_or_higher:
        filter_cutoff_hz = TRIPLE_DIGIT_FILTER_CUTOFF_HZ
    else:
        filter_cutoff_hz = DEFAULT_FILTER_CUTOFF_HZ

    right_exclusion_fraction = (
        DOUBLE_DIGIT_FALL_RIGHT_EXCLUSION_FRACTION
        if is_double_digit
        else TRIPLE_DIGIT_FALL_RIGHT_EXCLUSION_FRACTION
    )

    return {
        "filter_cutoff_hz": float(filter_cutoff_hz),
        "fall_right_exclusion_fraction": float(right_exclusion_fraction),
    }


POWERPOINT_FILENAME = "CW_Rise_Fall_Summary.pptx"
# Controls Avg, Min, Max, and Std Dev formatting in the PowerPoint table.
# PowerPoint timing values always use microseconds (us).
POWERPOINT_TABLE_DECIMAL_PLACES = 3

# ==========================================================
# TREND GRAPH VERTICAL-SCALE SETTINGS
# ==========================================================
# Values are in microseconds. Leave both values as None to retain the
# existing automatic vertical scaling.
RISE_TREND_Y_MIN_US = None
RISE_TREND_Y_MAX_US = None
FALL_TREND_Y_MIN_US = None
FALL_TREND_Y_MAX_US = None

PLOT_MODE_ALL = "ALL"
PLOT_MODE_SUMMARY = "SUMMARY"
PLOT_MODE_NONE = "NONE"
COMPARISON_PLOT_MODE_BOX_WHISKER = "BOX_WHISKER"
COMPARISON_PLOT_MODE_ORIGINAL = "ORIGINAL"
FREQUENCY_LAYOUT_COMBINED = "COMBINED"
FREQUENCY_LAYOUT_SEPARATE = "SEPARATE"
FREQUENCY_PLOT_COLORS = ["#4472C4", "#ED7D31", "#70AD47", "#A5A5A5", "#5B9BD5", "#FFC000"]
FREQUENCY_PLOT_COLOR_OVERRIDE = None
TREND_RANGE_MODE_NONE = "NONE"
TREND_RANGE_MODE_BOX = "BOX"
TREND_RANGE_MODE_WHISKERS = "WHISKERS"
TREND_RANGE_MODE_BOX_AND_WHISKERS = "BOX_AND_WHISKERS"


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
        font=("Segoe UI", 11, "bold"),
    ).pack(pady=(0, 14))

    tk.Label(
        frame,
        text=(
            "Choose whether to save every waveform, one representative "
            "summary plot per group, or no plots."
        ),
        wraplength=390,
        justify="left",
    ).pack(pady=(0, 14))

    def choose(mode):
        selected["mode"] = mode
        dialog.grab_release()
        dialog.destroy()

    tk.Button(
        frame,
        text="Plot every CSV",
        width=46,
        command=lambda: choose(PLOT_MODE_ALL),
    ).pack(pady=4)

    tk.Button(
        frame,
        text="One summary plot per frequency/power group",
        width=46,
        command=lambda: choose(PLOT_MODE_SUMMARY),
        default="active",
    ).pack(pady=4)

    tk.Button(
        frame,
        text="No plots (Excel summary only)",
        width=46,
        command=lambda: choose(PLOT_MODE_NONE),
    ).pack(pady=4)

    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(PLOT_MODE_NONE))
    dialog.update_idletasks()

    width = dialog.winfo_reqwidth()
    height = dialog.winfo_reqheight()
    x = max(0, (dialog.winfo_screenwidth() - width) // 2)
    y = max(0, (dialog.winfo_screenheight() - height) // 2)
    dialog.geometry(f"{width}x{height}+{x}+{y}")

    # Show only after sizing. Reassert topmost after mapping so Windows
    # cannot leave the dialog hidden behind another application.
    dialog.deiconify()
    dialog.lift()
    dialog.attributes("-topmost", True)
    dialog.focus_force()
    dialog.grab_set()
    dialog.after(500, lambda: dialog.attributes("-topmost", False))

    parent.wait_window(dialog)
    return selected["mode"]


def select_summary_sample_info(parent):
    """Choose whether summary plots include both count lines or neither."""
    selected = {"include_both": True}
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title("Summary Plot Information")
    dialog.resizable(False, False)

    frame = tk.Frame(dialog, padx=24, pady=18)
    frame.pack(fill="both", expand=True)
    tk.Label(
        frame,
        text="Include sample and outlier counts?",
        font=("Segoe UI", 11, "bold"),
    ).pack(pady=(0, 12))
    tk.Label(
        frame,
        text=(
            "Choose whether every summary plot includes both the "
            "'Samples used' and 'Outliers excluded' lines, or includes neither."
        ),
        wraplength=430,
        justify="left",
    ).pack(pady=(0, 14))

    def choose(include_both):
        selected["include_both"] = bool(include_both)
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    tk.Button(
        frame,
        text="Include both count lines",
        width=46,
        command=lambda: choose(True),
        default="active",
    ).pack(pady=4)
    tk.Button(
        frame,
        text="Include neither count line",
        width=46,
        command=lambda: choose(False),
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
    return selected["include_both"]


def select_trend_range_display_option(parent):
    """Choose the optional distribution display for both trend-plot versions."""
    selected = {"mode": TREND_RANGE_MODE_NONE}
    dialog = tk.Toplevel(parent);
    dialog.withdraw()
    dialog.title("Trend Plot Range Display");
    dialog.resizable(False, False)
    frame = tk.Frame(dialog, padx=24, pady=18);
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="Select the trend-plot range display:",
             font=("Segoe UI", 11, "bold")).pack(pady=(0, 12))
    tk.Label(frame, text=("The choice applies to both the original average plots and the "
                          "additional median-at-low-power plots. All ranges use accepted "
                          "measurements only."), wraplength=470, justify="left").pack(pady=(0, 14))

    def choose(mode):
        selected["mode"] = mode
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    for text, mode, default in (
            ("No additional box or whiskers", TREND_RANGE_MODE_NONE, None),
            ("Show IQR box only (Q1, median, Q3)", TREND_RANGE_MODE_BOX, "active"),
            ("Show accepted min/max whiskers only", TREND_RANGE_MODE_WHISKERS, None),
            ("Show IQR box with accepted min/max whiskers", TREND_RANGE_MODE_BOX_AND_WHISKERS, None),
    ):
        tk.Button(frame, text=text, width=50, command=lambda m=mode: choose(m),
                  default=default).pack(pady=4)
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(TREND_RANGE_MODE_NONE))
    dialog.update_idletasks();
    width = dialog.winfo_reqwidth();
    height = dialog.winfo_reqheight()
    x = max(0, (dialog.winfo_screenwidth() - width) // 2);
    y = max(0, (dialog.winfo_screenheight() - height) // 2)
    dialog.geometry(f"{width}x{height}+{x}+{y}");
    dialog.deiconify();
    dialog.lift()
    dialog.attributes("-topmost", True);
    dialog.focus_force();
    dialog.grab_set()
    dialog.after(500, lambda: dialog.attributes("-topmost", False));
    parent.wait_window(dialog)
    return selected["mode"]


def select_analysis_mode(parent):
    """Choose original single-data-set mode or two-data-set comparison mode."""
    choice = messagebox.askyesnocancel(
        "Analysis Mode",
        "Analyze two CW hardware configurations?\n\n"
        "Yes: analyze and compare two folders.\n"
        "No: run the original single-folder workflow.\n"
        "Cancel: stop the script.",
        parent=parent,
    )
    if choice is None:
        return None
    return "COMPARE" if choice else "SINGLE"


def select_comparison_plot_mode(parent):
    """Choose which comparison graph style is generated."""
    selected = {"mode": None}
    dialog = tk.Toplevel(parent);
    dialog.withdraw()
    dialog.title("Comparison Chart Style");
    dialog.resizable(False, False)
    frame = tk.Frame(dialog, padx=24, pady=18);
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="Select the comparison chart style:",
             font=("Segoe UI", 11, "bold")).pack(pady=(0, 12))
    tk.Label(frame, text=("Choose the frequency-specific side-by-side box-and-whisker "
                          "charts or the original average-line chart with its B-minus-A panel."),
             wraplength=480, justify="left").pack(pady=(0, 14))

    def choose(mode):
        selected["mode"] = mode
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    tk.Button(frame, text="Frequency-specific box-and-whisker charts", width=54,
              command=lambda: choose(COMPARISON_PLOT_MODE_BOX_WHISKER),
              default="active").pack(pady=4)
    tk.Button(frame, text="Original average-line and B-minus-A charts", width=54,
              command=lambda: choose(COMPARISON_PLOT_MODE_ORIGINAL)).pack(pady=4)
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(None))
    dialog.update_idletasks();
    width = dialog.winfo_reqwidth();
    height = dialog.winfo_reqheight()
    x = max(0, (dialog.winfo_screenwidth() - width) // 2);
    y = max(0, (dialog.winfo_screenheight() - height) // 2)
    dialog.geometry(f"{width}x{height}+{x}+{y}");
    dialog.deiconify();
    dialog.lift()
    dialog.attributes("-topmost", True);
    dialog.focus_force();
    dialog.grab_set()
    dialog.after(500, lambda: dialog.attributes("-topmost", False));
    parent.wait_window(dialog)
    return selected["mode"]


def select_frequency_curve_layout(parent):
    """Choose whether frequency curves share one graph or use separate files."""
    selected = {"mode": None}
    dialog = tk.Toplevel(parent);
    dialog.withdraw()
    dialog.title("Frequency Curve Layout");
    dialog.resizable(False, False)
    frame = tk.Frame(dialog, padx=24, pady=18);
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="How should the frequency curves be plotted?",
             font=("Segoe UI", 11, "bold")).pack(pady=(0, 12))
    tk.Label(frame, text=("This applies to each dataset's RISE and FALL trend plots. "
                          "For original comparison charts, it also controls their layout."),
             wraplength=500, justify="left").pack(pady=(0, 14))

    def choose(mode):
        selected["mode"] = mode
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    tk.Button(frame, text="All frequency curves on the same graph", width=52,
              command=lambda: choose(FREQUENCY_LAYOUT_COMBINED), default="active").pack(pady=4)
    tk.Button(frame, text="Separate graph for each frequency", width=52,
              command=lambda: choose(FREQUENCY_LAYOUT_SEPARATE)).pack(pady=4)
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(None))
    dialog.update_idletasks();
    w = dialog.winfo_reqwidth();
    h = dialog.winfo_reqheight()
    x = max(0, (dialog.winfo_screenwidth() - w) // 2);
    y = max(0, (dialog.winfo_screenheight() - h) // 2)
    dialog.geometry(f"{w}x{h}+{x}+{y}");
    dialog.deiconify();
    dialog.lift()
    dialog.attributes("-topmost", True);
    dialog.focus_force();
    dialog.grab_set()
    dialog.after(500, lambda: dialog.attributes("-topmost", False));
    parent.wait_window(dialog)
    return selected["mode"]


def select_manual_vertical_scales(parent):
    """Optionally choose common RISE/FALL trend limits in microseconds."""
    defaults = {
        "RISE": (RISE_TREND_Y_MIN_US, RISE_TREND_Y_MAX_US),
        "FALL": (FALL_TREND_Y_MIN_US, FALL_TREND_Y_MAX_US),
    }
    if not messagebox.askyesno(
            "CW Trend Vertical Scales",
            "Would you like to manually choose the RISE and FALL trend-plot "
            "vertical limits?\n\nChoose No to use the script's existing scale settings.",
            parent=parent,
    ):
        return defaults

    result = {"RISE": None, "FALL": None}
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title("CW Trend Vertical Scales")
    dialog.resizable(False, False)
    frame = tk.Frame(dialog, padx=22, pady=18)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="Vertical limits in microseconds (us)",
             font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=3, pady=(0, 6))
    tk.Label(frame, text="Leave both entries blank for automatic scaling.").grid(
        row=1, column=0, columnspan=3, pady=(0, 14))
    tk.Label(frame, text="Edge").grid(row=2, column=0)
    tk.Label(frame, text="Minimum (us)").grid(row=2, column=1)
    tk.Label(frame, text="Maximum (us)").grid(row=2, column=2)
    entries = {}
    for row, edge in enumerate(("RISE", "FALL"), start=3):
        tk.Label(frame, text=edge).grid(row=row, column=0, padx=6, pady=4)
        low, high = tk.Entry(frame, width=16), tk.Entry(frame, width=16)
        current = defaults[edge]
        if current[0] is not None: low.insert(0, str(current[0]))
        if current[1] is not None: high.insert(0, str(current[1]))
        low.grid(row=row, column=1, padx=6, pady=4)
        high.grid(row=row, column=2, padx=6, pady=4)
        entries[edge] = (low, high)

    def apply_values():
        parsed = {}
        try:
            for edge, (low, high) in entries.items():
                low_text, high_text = low.get().strip(), high.get().strip()
                if not low_text and not high_text:
                    parsed[edge] = (None, None)
                    continue
                if not low_text or not high_text:
                    raise ValueError(f"Enter both {edge} limits or leave both blank.")
                low_value, high_value = float(low_text), float(high_text)
                if not np.isfinite(low_value) or not np.isfinite(high_value):
                    raise ValueError(f"{edge} limits must be finite.")
                if low_value >= high_value:
                    raise ValueError(f"{edge} minimum must be less than maximum.")
                parsed[edge] = (low_value, high_value)
        except ValueError as error:
            messagebox.showerror("Invalid Vertical Scale", str(error), parent=dialog)
            return
        result.update(parsed)
        dialog.destroy()

    tk.Button(frame, text="Apply", width=18, command=apply_values).grid(
        row=5, column=0, columnspan=3, pady=(14, 0))
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.update_idletasks()
    x = max(0, (dialog.winfo_screenwidth() - dialog.winfo_reqwidth()) // 2)
    y = max(0, (dialog.winfo_screenheight() - dialog.winfo_reqheight()) // 2)
    dialog.geometry(f"+{x}+{y}")
    dialog.deiconify();
    dialog.lift();
    dialog.grab_set()
    parent.wait_window(dialog)
    return result


def extract_file_info(filename):
    name = os.path.splitext(filename)[0]
    match = re.search(
        r"RF_Freq_(RISE|FALL)(\d+(?:\.\d+)?)_Power(\d+(?:\.\d+)?)W",
        name,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError(
            "Filename does not match RF_Freq_RISE{freq}_Power{power}W_timestamp.csv "
            "or RF_Freq_FALL{freq}_Power{power}W_timestamp.csv"
        )
    edge_type = match.group(1).upper()
    frequency_khz = float(match.group(2))
    power_setpoint_w = float(match.group(3))
    return edge_type, power_setpoint_w, frequency_khz, frequency_khz * 1000


def read_waveform(csv_path):
    """Read time/voltage from columns A/B beginning at Excel row 15."""
    df = pd.read_csv(csv_path)
    if df.shape[1] < 2:
        raise ValueError("CSV must contain at least two columns.")
    time_raw = pd.to_numeric(df.iloc[14:, 0], errors="coerce").to_numpy()
    voltage_raw = pd.to_numeric(df.iloc[14:, 1], errors="coerce").to_numpy()
    valid = np.isfinite(time_raw) & np.isfinite(voltage_raw)
    time = time_raw[valid]
    voltage = voltage_raw[valid]
    if len(time) < 2:
        raise ValueError("Fewer than two valid time/voltage samples were found.")
    if not np.all(np.diff(time) > 0):
        order = np.argsort(time)
        time = time[order]
        voltage = voltage[order]
        unique = np.concatenate(([True], np.diff(time) > 0))
        time = time[unique]
        voltage = voltage[unique]
    return time, voltage, len(time_raw) - len(time)


def calculate_rise_time(time, voltage):
    if len(time) == 0 or len(voltage) == 0:
        raise ValueError("No valid time/voltage data found.")
    v_max = np.max(voltage)
    v10 = 0.10 * v_max
    v90 = 0.90 * v_max
    crossing_10 = np.where(voltage >= v10)[0]
    crossing_90 = np.where(voltage >= v90)[0]
    if len(crossing_10) == 0:
        raise ValueError("Signal never crosses 10% threshold.")
    if len(crossing_90) == 0:
        raise ValueError("Signal never crosses 90% threshold.")
    idx10 = crossing_10[0]
    idx90 = crossing_90[0]
    t10 = time[idx10]
    t90 = time[idx90]
    rise_time = t90 - t10
    if rise_time < 0:
        raise ValueError("Calculated rise time is negative.")
    return v_max, v10, v90, idx10, idx90, t10, t90, rise_time


def calculate_fall_time(time, voltage, right_exclusion_fraction=None):
    """Calculate raw-waveform FALL time inside the retained middle region.

    The configured left and setpoint-dependent right regions are ignored. The
    thresholds are referenced to a measured noise floor so the 10 percent
    threshold remains above a nonzero floor.
    """
    if len(time) == 0 or len(voltage) == 0:
        raise ValueError("No valid time/voltage data found.")
    if len(time) != len(voltage):
        raise ValueError("Time and voltage arrays must have the same length.")
    if not 0.0 <= FALL_LEFT_EXCLUSION_FRACTION < 1.0:
        raise ValueError("FALL_LEFT_EXCLUSION_FRACTION must be in [0, 1).")
    if right_exclusion_fraction is None:
        right_exclusion_fraction = DEFAULT_FALL_RIGHT_EXCLUSION_FRACTION
    right_exclusion_fraction = float(right_exclusion_fraction)
    if not 0.0 <= right_exclusion_fraction < 1.0:
        raise ValueError("FALL right exclusion fraction must be in [0, 1).")
    if FALL_LEFT_EXCLUSION_FRACTION + right_exclusion_fraction >= 1.0:
        raise ValueError("Combined FALL exclusions must be less than 100 percent.")
    if not 0.0 <= FALL_NOISE_FLOOR_PERCENTILE <= 100.0:
        raise ValueError("FALL_NOISE_FLOOR_PERCENTILE must be from 0 to 100.")

    search_start = int(np.floor(len(voltage) * FALL_LEFT_EXCLUSION_FRACTION))
    search_end = int(np.ceil(len(voltage) * (1.0 - right_exclusion_fraction)))
    if search_end - search_start < 2:
        raise ValueError("FALL exclusions leave too few waveform samples.")

    analysis_voltage = voltage[search_start:search_end]
    v_max = float(np.max(analysis_voltage))
    noise_floor_v = float(np.percentile(
        analysis_voltage,
        FALL_NOISE_FLOOR_PERCENTILE,
    ))
    signal_span_v = v_max - noise_floor_v
    if not np.isfinite(signal_span_v) or signal_span_v <= 0:
        raise ValueError(
            "FALL retained region has no positive maximum-to-noise-floor span."
        )

    v90 = noise_floor_v + 0.90 * signal_span_v
    v10 = noise_floor_v + 0.10 * signal_span_v

    crossing_90 = np.where(
        (analysis_voltage[:-1] > v90)
        & (analysis_voltage[1:] <= v90)
    )[0]
    if len(crossing_90) == 0:
        raise ValueError("Signal never crosses downward through 90% in the retained FALL region.")

    selected_idx90 = None
    selected_idx10 = None
    for relative_idx90 in crossing_90:
        candidate_idx90 = search_start + int(relative_idx90) + 1
        crossing_10 = np.where(
            (voltage[candidate_idx90:search_end - 1] > v10)
            & (voltage[candidate_idx90 + 1:search_end] <= v10)
        )[0]
        if len(crossing_10) > 0:
            selected_idx90 = candidate_idx90
            selected_idx10 = candidate_idx90 + int(crossing_10[0]) + 1
            break

    if selected_idx90 is None or selected_idx10 is None:
        raise ValueError(
            "No complete downward 90%-to-10% pair was found in the retained FALL region."
        )

    t90 = time[selected_idx90]
    t10 = time[selected_idx10]
    fall_time = t10 - t90
    if not np.isfinite(fall_time) or fall_time < 0:
        raise ValueError(f"Calculated fall time is invalid: {fall_time}")
    return v_max, v90, v10, selected_idx90, selected_idx10, t90, t10, fall_time


def calculate_low_power_rise_time(time, voltage):
    """Calculate RISE time from the filtered Hilbert envelope."""
    if len(time) == 0 or len(voltage) == 0:
        raise ValueError("No valid time/voltage data found.")
    if len(time) != len(voltage):
        raise ValueError("Time and voltage arrays must have the same length.")

    start_idx = int(len(voltage) * 0.05)
    averaging_points = min(500, max(1, len(voltage) - start_idx))
    initial_slice = voltage[start_idx:start_idx + averaging_points]
    final_slice = voltage[-averaging_points:]
    if len(initial_slice) == 0 or len(final_slice) == 0:
        raise ValueError("Not enough points to calculate low-power thresholds.")

    v_initial = float(np.mean(initial_slice))
    v_final = float(np.mean(final_slice))
    signal_span = v_final - v_initial
    if not np.isfinite(signal_span) or signal_span <= 0:
        raise ValueError(
            "Low-power RISE Hilbert envelope has no positive initial-to-final span."
        )

    v10 = v_initial + 0.10 * signal_span
    v90 = v_initial + 0.90 * signal_span
    v_max = float(np.max(voltage))
    crossing_10 = np.where(
        (voltage[:-1] < v10) & (voltage[1:] >= v10)
    )[0]

    for idx10_left in crossing_10:
        crossing_90 = np.where(
            (voltage[idx10_left:-1] < v90)
            & (voltage[idx10_left + 1:] >= v90)
        )[0]
        if len(crossing_90) > 0:
            idx10 = int(idx10_left + 1)
            idx90 = int(idx10_left + crossing_90[0] + 1)
            t10 = float(time[idx10])
            t90 = float(time[idx90])
            rise_time = t90 - t10
            if np.isfinite(rise_time) and rise_time >= 0:
                return v_max, v10, v90, idx10, idx90, t10, t90, rise_time

    raise ValueError(
        "No valid upward 10% to 90% Hilbert-envelope crossing pair was found."
    )


def calculate_low_power_fall_time(time, voltage, right_exclusion_fraction=None):
    """Calculate Hilbert-envelope FALL time inside the retained middle region.

    The configured left and setpoint-dependent right regions are ignored. A robust
    low-percentile envelope floor is measured in the retained region, and the
    10% and 90% thresholds are referenced from that floor to the envelope max.
    """
    if len(time) == 0 or len(voltage) == 0:
        raise ValueError("No valid time/voltage data found.")
    if len(time) != len(voltage):
        raise ValueError("Time and voltage arrays must have the same length.")
    if not 0.0 <= FALL_LEFT_EXCLUSION_FRACTION < 1.0:
        raise ValueError("FALL_LEFT_EXCLUSION_FRACTION must be in [0, 1).")
    if right_exclusion_fraction is None:
        right_exclusion_fraction = DEFAULT_FALL_RIGHT_EXCLUSION_FRACTION
    right_exclusion_fraction = float(right_exclusion_fraction)
    if not 0.0 <= right_exclusion_fraction < 1.0:
        raise ValueError("FALL right exclusion fraction must be in [0, 1).")
    if FALL_LEFT_EXCLUSION_FRACTION + right_exclusion_fraction >= 1.0:
        raise ValueError("Combined FALL exclusions must be less than 100 percent.")
    if not 0.0 <= FALL_NOISE_FLOOR_PERCENTILE <= 100.0:
        raise ValueError("FALL_NOISE_FLOOR_PERCENTILE must be from 0 to 100.")

    search_start = int(np.floor(len(voltage) * FALL_LEFT_EXCLUSION_FRACTION))
    search_end = int(np.ceil(len(voltage) * (1.0 - right_exclusion_fraction)))
    if search_end - search_start < 2:
        raise ValueError("FALL exclusions leave too few Hilbert-envelope samples.")

    analysis_envelope = voltage[search_start:search_end]
    v_max = float(np.max(analysis_envelope))
    noise_floor_v = float(np.percentile(
        analysis_envelope,
        FALL_NOISE_FLOOR_PERCENTILE,
    ))
    signal_span_v = v_max - noise_floor_v
    if not np.isfinite(signal_span_v) or signal_span_v <= 0:
        raise ValueError(
            "FALL Hilbert envelope has no positive maximum-to-noise-floor span "
            "inside the retained region."
        )

    v90 = noise_floor_v + 0.90 * signal_span_v
    v10 = noise_floor_v + 0.10 * signal_span_v

    crossing_90 = np.where(
        (analysis_envelope[:-1] > v90)
        & (analysis_envelope[1:] <= v90)
    )[0]
    if len(crossing_90) == 0:
        raise ValueError(
            "Hilbert envelope never crosses downward through 90% in the retained FALL region."
        )

    selected_idx90 = None
    selected_idx10 = None
    for relative_idx90 in crossing_90:
        candidate_idx90 = search_start + int(relative_idx90) + 1
        crossing_10 = np.where(
            (voltage[candidate_idx90:search_end - 1] > v10)
            & (voltage[candidate_idx90 + 1:search_end] <= v10)
        )[0]
        if len(crossing_10) > 0:
            selected_idx90 = candidate_idx90
            selected_idx10 = candidate_idx90 + int(crossing_10[0]) + 1
            break

    if selected_idx90 is None or selected_idx10 is None:
        raise ValueError(
            "No complete Hilbert-envelope downward 90%-to-10% pair was found "
            "in the retained FALL region."
        )

    t90 = time[selected_idx90]
    t10 = time[selected_idx10]
    fall_time = t10 - t90
    if not np.isfinite(fall_time) or fall_time < 0:
        raise ValueError(f"Calculated fall time is invalid: {fall_time}")
    return v_max, v90, v10, selected_idx90, selected_idx10, t90, t10, fall_time


def apply_hilbert(voltage, time, filter_cutoff_hz=None):
    fs = 1.0 / np.mean(np.diff(time))
    if filter_cutoff_hz is None:
        filter_cutoff_hz = DEFAULT_FILTER_CUTOFF_HZ
    filter_cutoff_hz = float(filter_cutoff_hz)
    if not np.isfinite(filter_cutoff_hz) or filter_cutoff_hz <= 0:
        raise ValueError("Hilbert filter cutoff must be a finite positive value.")
    if filter_cutoff_hz >= fs / 2:
        raise ValueError(
            f"Filter cutoff ({filter_cutoff_hz} Hz) must be below Nyquist ({fs / 2:.3f} Hz)."
        )
    b, a = butter(4, filter_cutoff_hz, btype="low", fs=fs)
    envelope = np.abs(hilbert(voltage))
    min_length = 3 * max(len(a), len(b))
    if len(envelope) <= min_length:
        raise ValueError("Waveform is too short for zero-phase Hilbert-envelope filtering.")
    return filtfilt(b, a, envelope)


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


def extract_rms_peak_envelope(time, voltage, rf_frequency_hz):
    """Create the SLP-style peak-equivalent centered moving-RMS envelope."""
    time = np.asarray(time, dtype=float)
    voltage = np.asarray(voltage, dtype=float)
    dt = float(np.median(np.diff(time)))
    if not np.isfinite(dt) or dt <= 0:
        raise ValueError("Invalid waveform sample interval for RMS envelope.")
    rf_hz = float(rf_frequency_hz)
    if not np.isfinite(rf_hz) or rf_hz <= 0:
        raise ValueError("RF frequency must be positive for RMS envelope extraction.")
    sample_rate_hz = 1.0 / dt
    requested_window = int(round((sample_rate_hz / rf_hz) * ENVELOPE_RMS_CYCLES))
    window = max(MIN_ENVELOPE_WINDOW_SAMPLES, requested_window)
    window = min(MAX_ENVELOPE_WINDOW_SAMPLES, window)
    if window % 2 == 0:
        window += 1
    if window >= len(voltage):
        raise ValueError("RMS envelope window is longer than the waveform.")
    rms = np.sqrt(np.maximum(centered_moving_average(voltage ** 2, window), 0.0))
    envelope = rms * np.sqrt(2.0)
    applied_cycles = window * rf_hz / sample_rate_hz
    return envelope, requested_window, window, applied_cycles, sample_rate_hz


def interpolate_crossing_time(time, signal, left_index, threshold):
    """Linearly interpolate a threshold crossing between adjacent samples."""
    right_index = int(left_index) + 1
    if left_index < 0 or right_index >= len(time):
        raise IndexError("Crossing interpolation index is outside the waveform.")
    t0, t1 = float(time[left_index]), float(time[right_index])
    y0, y1 = float(signal[left_index]), float(signal[right_index])
    if y1 == y0:
        return t1
    fraction = np.clip((float(threshold) - y0) / (y1 - y0), 0.0, 1.0)
    return t0 + float(fraction) * (t1 - t0)


def calculate_rms_cw_edge(time, envelope, edge_type):
    """Measure a CW edge using settled endpoint plateaus and interpolated cursors."""
    time = np.asarray(time, dtype=float)
    envelope = np.asarray(envelope, dtype=float)
    if len(time) != len(envelope) or len(time) < 2:
        raise ValueError("Time and RMS-envelope arrays must have equal usable lengths.")
    plateau_points = max(RMS_MIN_PLATEAU_SAMPLES, int(round(len(envelope) * RMS_PLATEAU_FRACTION)))
    plateau_points = min(plateau_points, max(1, len(envelope) // 3))
    initial_level = float(np.median(envelope[:plateau_points]))
    final_level = float(np.median(envelope[-plateau_points:]))
    if edge_type == "RISE":
        low_level, high_level = initial_level, final_level
    elif edge_type == "FALL":
        high_level, low_level = initial_level, final_level
    else:
        raise ValueError(f"Unsupported edge type: {edge_type}")
    span = high_level - low_level
    if not np.isfinite(span) or span <= 0:
        raise ValueError(f"RMS {edge_type} envelope has no positive settled-state span.")
    v10 = low_level + 0.10 * span
    v90 = low_level + 0.90 * span
    indices = np.arange(len(envelope) - 1)
    midpoint = low_level + 0.50 * span
    if edge_type == "RISE":
        midpoint_crossings = indices[(envelope[:-1] < midpoint) & (envelope[1:] >= midpoint)]
        if len(midpoint_crossings) == 0:
            raise ValueError("No upward RMS-envelope midpoint crossing was found.")
        mid = int(midpoint_crossings[0])
        c10 = indices[(indices <= mid) & (envelope[:-1] < v10) & (envelope[1:] >= v10)]
        c90 = indices[(indices >= mid) & (envelope[:-1] < v90) & (envelope[1:] >= v90)]
        if len(c10) == 0 or len(c90) == 0:
            raise ValueError("No valid upward RMS-envelope 10%-to-90% pair was found.")
        idx10, idx90 = int(c10[-1]), int(c90[0])
    else:
        midpoint_crossings = indices[(envelope[:-1] > midpoint) & (envelope[1:] <= midpoint)]
        if len(midpoint_crossings) == 0:
            raise ValueError("No downward RMS-envelope midpoint crossing was found.")
        mid = int(midpoint_crossings[0])
        c90 = indices[(indices <= mid) & (envelope[:-1] > v90) & (envelope[1:] <= v90)]
        c10 = indices[(indices >= mid) & (envelope[:-1] > v10) & (envelope[1:] <= v10)]
        if len(c90) == 0 or len(c10) == 0:
            raise ValueError("No valid downward RMS-envelope 90%-to-10% pair was found.")
        idx90, idx10 = int(c90[-1]), int(c10[0])
    t10 = interpolate_crossing_time(time, envelope, idx10, v10)
    t90 = interpolate_crossing_time(time, envelope, idx90, v90)
    measured = t90 - t10 if edge_type == "RISE" else t10 - t90
    if not np.isfinite(measured) or measured <= 0:
        raise ValueError(f"Calculated RMS {edge_type} time is invalid: {measured}")
    return float(np.max(envelope)), v10, v90, idx10, idx90, t10, t90, measured, initial_level, final_level


def add_iqr_outlier_flags(df, value_column, group_columns):
    df = df.copy()
    df["Use In Average"] = True
    df["Outlier Reason"] = ""
    df["IQR Lower Bound"] = np.nan
    df["IQR Upper Bound"] = np.nan
    for _, group_df in df.groupby(group_columns, dropna=False):
        values = group_df[value_column].dropna()
        if len(values) < MIN_SAMPLES_FOR_IQR:
            df.loc[group_df.index, "Outlier Reason"] = (
                f"No IQR filter applied: fewer than {MIN_SAMPLES_FOR_IQR} samples in group"
            )
            continue
        q1 = values.quantile(0.25)
        q3 = values.quantile(0.75)
        iqr = q3 - q1
        lower_bound = q1 - IQR_MULTIPLIER * iqr
        upper_bound = q3 + IQR_MULTIPLIER * iqr
        df.loc[group_df.index, "IQR Lower Bound"] = lower_bound
        df.loc[group_df.index, "IQR Upper Bound"] = upper_bound
        outlier_indices = group_df[
            (group_df[value_column] < lower_bound)
            | (group_df[value_column] > upper_bound)
            ].index
        df.loc[outlier_indices, "Use In Average"] = False
        df.loc[outlier_indices, "Outlier Reason"] = (
            f"IQR outlier: outside {lower_bound:.3e} to {upper_bound:.3e} s"
        )
        included = group_df.index.difference(outlier_indices)
        df.loc[included, "Outlier Reason"] = (
            f"Included: within IQR bounds {lower_bound:.3e} to {upper_bound:.3e} s"
        )
    return df


def create_summary_dataframe(individual_df, value_column, value_label):
    if individual_df.empty:
        return pd.DataFrame(), individual_df.copy()
    individual_df = add_iqr_outlier_flags(
        individual_df, value_column, ["Frequency (kHz)", "Power Setpoint (W)"]
    )
    filtered = individual_df[individual_df["Use In Average"]].copy()
    group_cols = ["Frequency (kHz)", "Frequency (Hz)", "Power Setpoint (W)"]
    totals = (
        individual_df.groupby(group_cols, dropna=False)
        .agg(
            Total_Sample_Count=(value_column, "count"),
            Excluded_Outlier_Count=("Use In Average", lambda x: (~x).sum()),
        )
        .reset_index()
    )
    if filtered.empty:
        return pd.DataFrame(), individual_df
    prefix = value_label
    us_col = f"{prefix} Time (us)"
    ns_col = f"{prefix} Time (ns)"
    summary = (
        filtered.groupby(group_cols, dropna=False)
        .agg(
            Included_Sample_Count=(value_column, "count"),
            **{
                f"Average_{prefix}_Time_s": (value_column, "mean"),
                f"Std_Dev_{prefix}_Time_s": (value_column, "std"),
                f"Average_{prefix}_Time_us": (us_col, "mean"),
                f"Minimum_{prefix}_Time_us": (us_col, "min"),
                f"Q1_{prefix}_Time_us": (us_col, lambda values: values.quantile(0.25)),
                f"Median_{prefix}_Time_us": (us_col, "median"),
                f"Q3_{prefix}_Time_us": (us_col, lambda values: values.quantile(0.75)),
                f"Maximum_{prefix}_Time_us": (us_col, "max"),
                f"Std_Dev_{prefix}_Time_us": (us_col, "std"),
                f"Average_{prefix}_Time_ns": (ns_col, "mean"),
                f"Std_Dev_{prefix}_Time_ns": (ns_col, "std"),
                "Average_Max_Voltage_V": ("Max Voltage (V)", "mean"),
                "Std_Dev_Max_Voltage_V": ("Max Voltage (V)", "std"),
            },
        )
        .reset_index()
        .merge(totals, on=group_cols, how="left")
        .sort_values(["Frequency (kHz)", "Power Setpoint (W)"])
    )
    return summary, individual_df


def create_frequency_min_max_summary(rise_df, fall_df):
    """Create accepted rise/fall time ranges for each RF frequency.

    RISE and FALL classification is inherited from extract_file_info(), which
    reads RF_Freq_RISE... or RF_Freq_FALL... from each source CSV filename.
    Values excluded by the existing IQR filter are omitted from these ranges.
    """
    group_columns = ["Frequency (kHz)", "Frequency (Hz)"]

    def summarize_edge(dataframe, edge_label):
        if dataframe.empty:
            return pd.DataFrame(columns=group_columns)

        required_columns = {
            *group_columns,
            "Power Setpoint (W)",
            "Use In Average",
            f"{edge_label} Time (s)",
            f"{edge_label} Time (us)",
            f"{edge_label} Time (ns)",
        }
        missing_columns = required_columns.difference(dataframe.columns)
        if missing_columns:
            raise ValueError(
                f"Cannot create {edge_label.lower()} frequency ranges. "
                "Missing columns: " + ", ".join(sorted(missing_columns))
            )

        included = dataframe[dataframe["Use In Average"] == True].copy()
        if included.empty:
            return pd.DataFrame(columns=group_columns)

        return (
            included.groupby(group_columns, dropna=False)
            .agg(**{
                f"{edge_label} Included Sample Count": (f"{edge_label} Time (s)", "count"),
                f"Minimum {edge_label} Time (s)": (f"{edge_label} Time (s)", "min"),
                f"Maximum {edge_label} Time (s)": (f"{edge_label} Time (s)", "max"),
                f"Minimum {edge_label} Time (us)": (f"{edge_label} Time (us)", "min"),
                f"Maximum {edge_label} Time (us)": (f"{edge_label} Time (us)", "max"),
                f"Minimum {edge_label} Time (ns)": (f"{edge_label} Time (ns)", "min"),
                f"Maximum {edge_label} Time (ns)": (f"{edge_label} Time (ns)", "max"),
                f"Minimum {edge_label} Power Setpoint (W)": ("Power Setpoint (W)", "min"),
                f"Maximum {edge_label} Power Setpoint (W)": ("Power Setpoint (W)", "max"),
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
        combined = rise_ranges.merge(fall_ranges, on=group_columns, how="outer")

    if combined.empty:
        return combined

    return combined.sort_values("Frequency (kHz)").reset_index(drop=True)


def create_cross_frequency_setpoint_average(summary_df, value_label):
    """Create one equally weighted time average per setpoint across frequencies.

    Each frequency contributes its outlier-filtered frequency/setpoint average,
    so frequencies remain equally weighted if their CSV capture counts differ.
    """
    if summary_df.empty:
        return pd.DataFrame()

    prefix = value_label
    average_s_column = f"Average_{prefix}_Time_s"
    average_us_column = f"Average_{prefix}_Time_us"
    average_ns_column = f"Average_{prefix}_Time_ns"
    required_columns = {
        "Power Setpoint (W)", "Frequency (kHz)", average_s_column,
        average_us_column, average_ns_column, "Included_Sample_Count",
        "Total_Sample_Count", "Excluded_Outlier_Count",
    }
    missing_columns = required_columns.difference(summary_df.columns)
    if missing_columns:
        raise ValueError(
            "Cannot create cross-frequency setpoint averages. Missing columns: "
            + ", ".join(sorted(missing_columns))
        )

    grouped = summary_df.groupby("Power Setpoint (W)", dropna=False)
    result = grouped.agg(**{
        "Frequency Count": ("Frequency (kHz)", "nunique"),
        f"Average {prefix} Time Across Frequencies (s)": (average_s_column, "mean"),
        f"Average {prefix} Time Across Frequencies (us)": (average_us_column, "mean"),
        f"Average {prefix} Time Across Frequencies (ns)": (average_ns_column, "mean"),
        f"Std Dev of Frequency {prefix} Averages (s)": (average_s_column, "std"),
        f"Std Dev of Frequency {prefix} Averages (us)": (average_us_column, "std"),
        f"Std Dev of Frequency {prefix} Averages (ns)": (average_ns_column, "std"),
        f"Included {prefix} Samples Across Frequencies": ("Included_Sample_Count", "sum"),
        f"Total {prefix} Samples Across Frequencies": ("Total_Sample_Count", "sum"),
        f"Excluded {prefix} Outliers Across Frequencies": ("Excluded_Outlier_Count", "sum"),
    }).reset_index()

    frequency_lists = grouped["Frequency (kHz)"].apply(
        lambda values: ", ".join(
            f"{frequency:g}" for frequency in sorted(values.dropna().unique())
        )
    ).rename("Frequencies Included (kHz)").reset_index()
    result = result.merge(frequency_lists, on="Power Setpoint (W)", how="left")
    first_columns = [
        "Power Setpoint (W)", "Frequency Count", "Frequencies Included (kHz)"
    ]
    remaining_columns = [c for c in result.columns if c not in first_columns]
    return result[first_columns + remaining_columns].sort_values("Power Setpoint (W)")


def combine_setpoint_averages(rise_average, fall_average):
    """Combine rise and fall cross-frequency results into one Excel table."""
    if rise_average.empty:
        return fall_average.copy()
    if fall_average.empty:
        return rise_average.copy()

    shared = ["Power Setpoint (W)", "Frequency Count", "Frequencies Included (kHz)"]
    fall_only = [column for column in fall_average.columns if column not in shared]
    return rise_average.merge(
        fall_average[["Power Setpoint (W)"] + fall_only],
        on="Power Setpoint (W)", how="outer",
    ).sort_values("Power Setpoint (W)")


def safe_value_for_filename(value):
    value = float(value)
    return str(int(value)) if value.is_integer() else str(value).replace(".", "p")


def get_frequency_plot_color(index):
    """Return a consistent color for each frequency series."""
    if FREQUENCY_PLOT_COLOR_OVERRIDE is not None:
        return FREQUENCY_PLOT_COLOR_OVERRIDE
    return FREQUENCY_PLOT_COLORS[index % len(FREQUENCY_PLOT_COLORS)]


def save_separate_legend_image(axes, graph_path, title=None):
    """Save the unique legend entries separately and remove them from graphs."""
    if not isinstance(axes, (list, tuple)):
        axes = [axes]
    handles_by_label = {}
    for axis in axes:
        handles, labels = axis.get_legend_handles_labels()
        for handle, label in zip(handles, labels):
            if label and not label.startswith("_") and label not in handles_by_label:
                handles_by_label[label] = handle
        existing = axis.get_legend()
        if existing is not None:
            existing.remove()
    if not handles_by_label:
        return None
    legend_figure = plt.figure(figsize=(8.5, max(1.5, 0.36 * len(handles_by_label) + 0.7)))
    legend_axis = legend_figure.add_subplot(111);
    legend_axis.set_axis_off()
    legend_axis.legend(list(handles_by_label.values()), list(handles_by_label.keys()),
                       title=title, loc="center", frameon=True, ncol=1)
    stem, extension = os.path.splitext(graph_path)
    legend_path = f"{stem}_Legend{extension}"
    legend_figure.savefig(legend_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(legend_figure)
    print(f"Saved separate legend image: {legend_path}")
    return legend_path


def plot_individual(
        time,
        analysis_signal,
        row,
        output_folder,
        raw_voltage=None,
        show_raw_and_envelope=True,
        analysis_method=ANALYSIS_METHOD_HILBERT,
):
    """Save one individual waveform plot with the existing calculated cursors.

    For every RISE and FALL file, both the filtered Hilbert envelope used for
    the calculation and the original CSV waveform are displayed. Cursor voltages,
    cursor times, and measured edge time are not recalculated here.
    """
    edge = row["Type"]
    t10, t90 = row["t10 (s)"], row["t90 (s)"]
    v10, v90 = row["10% Voltage (V)"], row["90% Voltage (V)"]
    idx10 = int(np.argmin(np.abs(time - t10)))
    idx90 = int(np.argmin(np.abs(time - t90)))
    time_value = row[f"{edge.title()} Time (s)"]

    figure, axis = plt.subplots(figsize=(12, 6))

    if show_raw_and_envelope:
        if raw_voltage is None:
            raise ValueError(
                "raw_voltage is required when plotting both raw and analysis-envelope waveforms."
            )
        if len(raw_voltage) != len(time):
            raise ValueError("Raw waveform and time arrays must have the same length.")

        axis.plot(
            time,
            raw_voltage,
            color="#7F7F7F",
            linewidth=0.8,
            alpha=0.72,
            label="Original waveform",
        )
        axis.plot(
            time,
            analysis_signal,
            color="blue",
            linewidth=1.5,
              label=(f"Filtered Hilbert envelope used for {edge} calculation" if analysis_method == ANALYSIS_METHOD_HILBERT else f"RMS peak-equivalent envelope used for {edge} calculation"),
        )
        threshold_prefix = "Hilbert-envelope" if analysis_method == ANALYSIS_METHOD_HILBERT else "RMS-envelope"
    else:
        axis.plot(
            time,
            analysis_signal,
            color="blue",
            linewidth=1.2,
            label="Measured signal",
        )
        threshold_prefix = "Waveform"

    axis.axhline(
        v10,
        color="green",
        linestyle=":",
        label=f"{threshold_prefix} 10% = {v10:.3f} V",
    )
    axis.axhline(
        v90,
        color="red",
        linestyle=":",
        label=f"{threshold_prefix} 90% = {v90:.3f} V",
    )
    axis.axvline(t10, color="green", linestyle="--", label=f"10% @ {t10:.3e} s")
    axis.axvline(t90, color="red", linestyle="--", label=f"90% @ {t90:.3e} s")

    # Markers are placed on the analysis signal because that is the signal from
    # which the saved threshold times were calculated.
    axis.plot(t10, analysis_signal[idx10], "go", markersize=8)
    axis.plot(t90, analysis_signal[idx90], "ro", markersize=8)

    processing_text = (
          (" | Raw waveform and filtered Hilbert envelope" if analysis_method == ANALYSIS_METHOD_HILBERT else " | Raw waveform and RMS envelope")
        if show_raw_and_envelope
        else ""
    )
    axis.set_title(
        f"{row['Filename']}\n"
        f"{edge.title()} Time = {time_value:.3e} s{processing_text}"
    )
    axis.set_xlabel("Time (s)")
    axis.set_ylabel("Voltage (V)")
    axis.grid(True)
    figure.tight_layout()

    suffix = "risetime" if edge == "RISE" else "falltime"
    output_path = os.path.join(
        output_folder,
        f"{os.path.splitext(row['Filename'])[0]}_{suffix}.png",
    )
    save_separate_legend_image(axis, output_path)
    figure.savefig(output_path, dpi=300)
    plt.close(figure)
    print(f"Saved individual waveform plot: {output_path}")
    return output_path


def create_group_summary_plots_raw(results_df, edge_type, output_folder, include_count_lines=True):
    """Create one representative waveform plot per frequency/power group."""
    if results_df.empty:
        return
    included_df = results_df[results_df["Use In Average"]].copy()
    if included_df.empty:
        return
    summary_folder = os.path.join(output_folder, f"{edge_type} Summary Plots")
    os.makedirs(summary_folder, exist_ok=True)
    total_lookup = results_df.groupby(["Frequency (kHz)", "Power Setpoint (W)"]).size()

    for (freq_khz, power_w), group in included_df.groupby(
            ["Frequency (kHz)", "Power Setpoint (W)"], sort=True
    ):
        representative = group.iloc[0]
        try:
            time, raw_voltage, _ = read_waveform(representative["CSV Path"])
            uses_hilbert_envelope = edge_type in {"RISE", "FALL"}
            processing_settings = get_setpoint_processing_settings(power_w)
            display_signal = (
                apply_hilbert(
                    raw_voltage,
                    time,
                    processing_settings["filter_cutoff_hz"],
                )
                if is_low_power_fall
                else raw_voltage
            )
            signal_label = "Hilbert envelope" if uses_hilbert_envelope else "Representative waveform"

            avg_t10 = group["t10 (s)"].mean()
            avg_t90 = group["t90 (s)"].mean()
            avg_v10 = group["10% Voltage (V)"].mean()
            avg_v90 = group["90% Voltage (V)"].mean()
            time_col = f"{edge_type.title()} Time (us)"
            avg_time_us = group[time_col].mean()
            std_time_us = group[time_col].std()
            std_text = "N/A" if pd.isna(std_time_us) else f"{std_time_us:.3f} us"
            total_count = int(total_lookup.loc[(freq_khz, power_w)])
            included_count = len(group)
            excluded_count = total_count - included_count

            y10 = np.interp(avg_t10, time, display_signal)
            y90 = np.interp(avg_t90, time, display_signal)

            fig, ax = plt.subplots(figsize=(12, 7))
            ax.plot(time, display_signal, color="blue", linewidth=1.2, label=signal_label)
            ax.axvline(avg_t10, color="green", linestyle="--", label=f"Average t10 = {avg_t10:.3e} s")
            ax.axvline(avg_t90, color="red", linestyle="--", label=f"Average t90 = {avg_t90:.3e} s")
            ax.axhline(avg_v10, color="green", linestyle=":", label=f"Average 10% = {avg_v10:.3f} V")
            ax.axhline(avg_v90, color="red", linestyle=":", label=f"Average 90% = {avg_v90:.3f} V")
            ax.plot(avg_t10, y10, "go", markersize=8)
            ax.plot(avg_t90, y90, "ro", markersize=8)
            ax.annotate("Average 10% cursor", (avg_t10, y10), xytext=(10, 12), textcoords="offset points")
            ax.annotate("Average 90% cursor", (avg_t90, y90), xytext=(10, -20), textcoords="offset points")

            count_text = ""
            if include_count_lines:
                count_text = (
                    f"Samples used: {included_count}\n"
                    f"Outliers excluded: {excluded_count}\n"
                )
            stats = (
                    count_text
                    + f"Average t10: {avg_t10:.3e} s\n"
                      f"Average t90: {avg_t90:.3e} s\n"
                      f"Average {edge_type.title()} Time: {avg_time_us:.3f} us\n"
                      f"Std. Dev.: {std_text}\n"
                      f"Displayed waveform:\n{representative['Filename']}"
            )
            ax.text(
                0.98, 0.98, stats, transform=ax.transAxes,
                ha="right", va="top", fontsize=9,
                bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.85},
            )
            ax.set_title(
                f"{edge_type} SUMMARY | {freq_khz:g} kHz | {power_w:g} W\n"
                f"Average {edge_type.title()} Time = {avg_time_us:.3f} us"
            )
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Voltage (V)")
            ax.grid(True)
            fig.tight_layout()
            # filename = (
            #     f"{edge_type}_{safe_value_for_filename(freq_khz)}kHz_"
            #     f"{safe_value_for_filename(power_w)}W_Summary.png"
            # )
            view_name = (
                "Hilbert_Envelope"
                if uses_hilbert_envelope
                else "Raw_Waveform"
            )
            filename = (
                f"{edge_type}_"
                f"{safe_value_for_filename(freq_khz)}kHz_"
                f"{safe_value_for_filename(power_w)}W_"
                f"{view_name}_Summary.png"
            )
            summary_path = os.path.join(summary_folder, filename)
            save_separate_legend_image(ax, summary_path)
            fig.savefig(summary_path, dpi=300)
            plt.close(fig)
            print(f"Saved summary plot: {os.path.join(summary_folder, filename)}")

        except Exception as exc:
            plt.close("all")
            print(f"Summary plot failed for {edge_type} {freq_khz:g} kHz, {power_w:g} W: {exc}")


# ======================================================
# Testing revision of create_group_summary_plots (will plot on unfiltered waveform using filtered cursors)
# ======================================================

def create_group_summary_plots_filter_view(results_df, edge_type, output_folder, include_count_lines=True):
    """
    Creates one summary plot for each frequency/power combination.

    The representative trace displayed on every plot is the original,
    unfiltered waveform from the CSV.

    Important:
        Low-power FALL cursor times and voltage thresholds are still based
        on the Hilbert-envelope calculations performed during the original
        CSV analysis. The Hilbert envelope is not displayed on this plot.

    Summary statistics and cursor averages use only rows where
    "Use In Average" is True.
    """

    if results_df.empty:
        return

    # Use only non-outlier measurements in the summary calculations.
    included_df = results_df[
        results_df["Use In Average"] == True
        ].copy()

    if included_df.empty:
        print(
            f"No included {edge_type} measurements were available "
            "for summary plotting."
        )
        return

    summary_folder = os.path.join(
        output_folder,
        f"{edge_type} Summary Plots"
    )

    os.makedirs(summary_folder, exist_ok=True)

    group_columns = [
        "Frequency (kHz)",
        "Power Setpoint (W)"
    ]

    # Used to report the number of excluded measurements in each group.
    total_counts = (
        results_df
        .groupby(group_columns, dropna=False)
        .size()
    )

    for group_key, group in included_df.groupby(
            group_columns,
            dropna=False,
            sort=True
    ):
        frequency_khz, power_setpoint_w = group_key

        try:
            # Use the included waveform whose measured edge time is
            # closest to the group-average edge time.
            if edge_type == "RISE":
                time_column = "Rise Time (s)"
                time_us_column = "Rise Time (us)"
                timing_label = "Rise"
            else:
                time_column = "Fall Time (s)"
                time_us_column = "Fall Time (us)"
                timing_label = "Fall"

            average_edge_time_s = float(
                group[time_column].mean()
            )

            representative_index = (
                    group[time_column] - average_edge_time_s
            ).abs().idxmin()

            representative = group.loc[
                representative_index
            ]

            # Reload the original CSV. read_waveform() returns the raw,
            # unfiltered voltage data.
            time, raw_voltage, _ = read_waveform(
                representative["CSV Path"]
            )

            # Always display the original waveform, including low-power
            # FALL measurements that were analyzed with a Hilbert envelope.
            display_signal = raw_voltage
            signal_label = "Representative raw waveform"

            # Group-average absolute cursor times.
            average_absolute_t10 = float(
                group["t10 (s)"].mean()
            )

            average_absolute_t90 = float(
                group["t90 (s)"].mean()
            )

            # Calculate cursor positions relative to the center of each
            # measured edge. This removes trigger-position variation
            # between acquisitions.
            edge_centers = (
                                   group["t10 (s)"] +
                                   group["t90 (s)"]
                           ) / 2.0

            relative_t10 = (
                    group["t10 (s)"] -
                    edge_centers
            )

            relative_t90 = (
                    group["t90 (s)"] -
                    edge_centers
            )

            average_relative_t10 = float(
                relative_t10.mean()
            )

            average_relative_t90 = float(
                relative_t90.mean()
            )

            # Anchor the average cursor spacing to the selected
            # representative waveform's actual edge center.
            representative_edge_center = (
                                                 float(representative["t10 (s)"]) +
                                                 float(representative["t90 (s)"])
                                         ) / 2.0

            plot_t10 = (
                    representative_edge_center +
                    average_relative_t10
            )

            plot_t90 = (
                    representative_edge_center +
                    average_relative_t90
            )

            # Make sure the displayed cursor separation exactly matches
            # the group-average edge time.
            displayed_edge_time_s = abs(
                plot_t90 - plot_t10
            )

            if not np.isclose(
                    displayed_edge_time_s,
                    average_edge_time_s,
                    rtol=1e-9,
                    atol=max(
                        1e-15,
                        abs(average_edge_time_s) * 1e-12
                    )
            ):
                half_average_edge_time = (
                        average_edge_time_s / 2.0
                )

                plot_t10 = (
                        representative_edge_center -
                        half_average_edge_time
                )

                plot_t90 = (
                        representative_edge_center +
                        half_average_edge_time
                )

            # Use the representative measurement's voltage thresholds.
            #
            # For low-power FALL data, these values were calculated from
            # the Hilbert envelope. They are retained because they are the
            # actual thresholds used in the measurement, even though the
            # displayed trace is now the raw waveform.
            plot_v10 = float(
                representative["10% Voltage (V)"]
            )

            plot_v90 = float(
                representative["90% Voltage (V)"]
            )

            # Group-average thresholds are included in the statistics box.
            average_v10 = float(
                group["10% Voltage (V)"].mean()
            )

            average_v90 = float(
                group["90% Voltage (V)"].mean()
            )

            average_time_us = float(
                group[time_us_column].mean()
            )

            standard_deviation_us = (
                group[time_us_column].std()
            )

            if pd.isna(standard_deviation_us):
                standard_deviation_text = "N/A"
            else:
                standard_deviation_text = (
                    f"{standard_deviation_us:.3f} us"
                )

            included_count = len(group)

            total_count = int(
                total_counts.loc[group_key]
            )

            excluded_count = (
                    total_count -
                    included_count
            )

            # Find the raw waveform voltage at the aligned cursor times.
            #
            # These marker voltages are not expected to equal the Hilbert
            # thresholds for low-power FALL measurements. Their purpose is
            # to show where the measured cursor times occur on the original
            # waveform.
            # Locate the aligned cursors on the displayed raw waveform.
            marker_y10 = float(np.interp(plot_t10, time, raw_voltage))
            marker_y90 = float(np.interp(plot_t90, time, raw_voltage))

            uses_hilbert_envelope = edge_type in {"RISE", "FALL"}

            figure, axis = plt.subplots(
                figsize=(12, 7)
            )

            axis.plot(
                time,
                display_signal,
                color="blue",
                linewidth=0.9,
                label=signal_label
            )

            axis.axvline(
                plot_t10,
                color="green",
                linestyle="--",
                linewidth=1.4,
                label=(
                    "Edge-aligned average t10 "
                    f"= {plot_t10:.3e} s"
                )
            )

            axis.axvline(
                plot_t90,
                color="red",
                linestyle="--",
                linewidth=1.4,
                label=(
                    "Edge-aligned average t90 "
                    f"= {plot_t90:.3e} s"
                )
            )

            # Keep the horizontal lines, but identify Hilbert-derived
            # thresholds clearly on low-power FALL plots.
            if uses_hilbert_envelope:
                threshold_10_label = (
                    "Hilbert-envelope 10% threshold "
                    f"= {plot_v10:.3f} V"
                )

                threshold_90_label = (
                    "Hilbert-envelope 90% threshold "
                    f"= {plot_v90:.3f} V"
                )
            else:
                threshold_10_label = (
                    "Displayed waveform 10% threshold "
                    f"= {plot_v10:.3f} V"
                )

                threshold_90_label = (
                    "Displayed waveform 90% threshold "
                    f"= {plot_v90:.3f} V"
                )

            axis.axhline(
                plot_v10,
                color="green",
                linestyle=":",
                linewidth=1.2,
                label=threshold_10_label
            )

            axis.axhline(
                plot_v90,
                color="red",
                linestyle=":",
                linewidth=1.2,
                label=threshold_90_label
            )

            # Mark the cursor locations directly on the raw waveform.
            # axis.plot(
            #     plot_t10,
            #     marker_y10,
            #     "go",
            #     markersize=8
            # )
            #
            # axis.plot(
            #     plot_t90,
            #     marker_y90,
            #     "ro",
            #     markersize=8
            # )

            axis.annotate(
                "Edge-aligned average 10% cursor",
                (plot_t10, marker_y10),
                textcoords="offset points",
                xytext=(10, 12)
            )

            axis.annotate(
                "Edge-aligned average 90% cursor",
                (plot_t90, marker_y90),
                textcoords="offset points",
                xytext=(10, -20)
            )

            if uses_hilbert_envelope:
                processing_description = (
                    "Raw waveform displayed; "
                    "Hilbert envelope used for measurement"
                )
            else:
                processing_description = (
                    "Raw waveform displayed and used "
                    "for measurement"
                )

            count_text = ""
            if include_count_lines:
                count_text = (
                    f"Samples used: {included_count}\n"
                    f"Outliers excluded: {excluded_count}\n"
                )
            statistics_text = (
                    count_text
                    + f"Average {timing_label} Time: "
                      f"{average_time_us:.3f} us\n"
                      f"Std. Dev.: {standard_deviation_text}\n"
                      f"Group mean absolute t10: "
                      f"{average_absolute_t10:.3e} s\n"
                      f"Group mean absolute t90: "
                      f"{average_absolute_t90:.3e} s\n"
                      f"Group mean 10% threshold: "
                      f"{average_v10:.3f} V\n"
                      f"Group mean 90% threshold: "
                      f"{average_v90:.3f} V\n"
                      f"{processing_description}\n"
                      f"Displayed waveform:\n"
                      f"{representative['Filename']}"
            )

            axis.text(
                0.98,
                0.98,
                statistics_text,
                transform=axis.transAxes,
                horizontalalignment="right",
                verticalalignment="top",
                fontsize=9,
                bbox={
                    "boxstyle": "round",
                    "facecolor": "white",
                    "alpha": 0.85
                }
            )

            axis.set_title(
                f"{edge_type} SUMMARY | "
                f"{frequency_khz:g} kHz | "
                f"{power_setpoint_w:g} W\n"
                f"Edge-Aligned Average "
                f"{timing_label} Time "
                f"= {average_time_us:.3f} us"
            )

            axis.set_xlabel("Time (s)")
            axis.set_ylabel("Voltage (V)")
            axis.grid(True)

            figure.tight_layout()

            # output_filename = (
            #     f"{edge_type}_"
            #     f"{safe_value_for_filename(frequency_khz)}kHz_"
            #     f"{safe_value_for_filename(power_setpoint_w)}W_"
            #     f"Summary.png"
            # )
            output_filename = (
                f"{edge_type}_"
                f"{safe_value_for_filename(frequency_khz)}kHz_"
                f"{safe_value_for_filename(power_setpoint_w)}W_"
                f"Raw_Waveform_Summary.png"
            )

            output_path = os.path.join(
                summary_folder,
                output_filename
            )

            save_separate_legend_image(axis, output_path)
            figure.savefig(
                output_path,
                dpi=300
            )

            plt.close(figure)

            print(
                "Saved filtered-waveform summary plot: "
                f"{output_path}"
            )

        except Exception as error:
            plt.close("all")

            print(
                f"Summary plot failed for "
                f"{edge_type}, "
                f"{frequency_khz:g} kHz, "
                f"{power_setpoint_w:g} W: "
                f"{error}"
            )


def apply_manual_trend_y_limits(axis, y_min_us, y_max_us, edge_label):
    """Apply optional manual trend-graph limits in microseconds.

    Either limit may remain None. When a FALL limit is specified, the manual
    scale takes priority over the existing automatic log/symlog selection and
    the graph is displayed on a linear vertical axis.
    """
    if y_min_us is None and y_max_us is None:
        return False

    for setting_name, setting_value in (
            (f"{edge_label.upper()}_TREND_Y_MIN_US", y_min_us),
            (f"{edge_label.upper()}_TREND_Y_MAX_US", y_max_us),
    ):
        if setting_value is not None and not np.isfinite(setting_value):
            raise ValueError(f"{setting_name} must be a finite number or None.")

    if (
            y_min_us is not None
            and y_max_us is not None
            and y_max_us <= y_min_us
    ):
        raise ValueError(
            f"{edge_label} trend Y-axis maximum must be greater than its minimum."
        )

    from matplotlib.ticker import AutoMinorLocator, MaxNLocator, ScalarFormatter

    axis.set_yscale("linear")
    current_bottom, current_top = axis.get_ylim()
    bottom = current_bottom if y_min_us is None else float(y_min_us)
    top = current_top if y_max_us is None else float(y_max_us)

    if top <= bottom:
        raise ValueError(
            f"The resolved {edge_label.lower()} trend Y-axis maximum must be "
            "greater than its minimum."
        )

    axis.set_ylim(bottom, top)
    axis.yaxis.set_major_locator(
        MaxNLocator(nbins=10, steps=[1, 2, 2.5, 5, 10])
    )
    axis.yaxis.set_minor_locator(AutoMinorLocator(2))
    axis.yaxis.set_major_formatter(ScalarFormatter(useOffset=False))
    axis.set_ylabel(f"Average {edge_label} Time (us)", fontsize=12)
    return True


def add_iqr_boxes(axis, group, edge_label, color, positions, box_width, include_min_max):
    """Draw exact accepted Q1/median/Q3 boxes, optionally with min/max whiskers."""
    stats = []
    for _, row in group.iterrows():
        q1 = row[f"Q1_{edge_label}_Time_us"];
        med = row[f"Median_{edge_label}_Time_us"]
        q3 = row[f"Q3_{edge_label}_Time_us"]
        stats.append({"q1": q1, "med": med, "q3": q3,
                      "whislo": row[f"Minimum_{edge_label}_Time_us"] if include_min_max else q1,
                      "whishi": row[f"Maximum_{edge_label}_Time_us"] if include_min_max else q3,
                      "fliers": []})
    axis.bxp(stats, positions=positions, widths=box_width, showfliers=False,
             patch_artist=True, manage_ticks=False,
             boxprops={"facecolor": color, "edgecolor": color, "alpha": 0.24, "linewidth": 1.1},
             medianprops={"color": color, "linewidth": 2.0},
             whiskerprops={"color": color if include_min_max else "none", "linewidth": 1.0 if include_min_max else 0},
             capprops={"color": color if include_min_max else "none", "linewidth": 1.0 if include_min_max else 0},
             zorder=2)


def create_median_low_power_trend_plot(summary, edge_label, output_folder, trend_range_mode):
    """Create an additional plot using median at/below threshold and average above it."""
    if summary.empty: return None
    cols = ["Power Setpoint (W)", "Frequency (kHz)", f"Average_{edge_label}_Time_us",
            f"Median_{edge_label}_Time_us", f"Minimum_{edge_label}_Time_us", f"Maximum_{edge_label}_Time_us",
            f"Q1_{edge_label}_Time_us", f"Q3_{edge_label}_Time_us"]
    data = summary[cols].dropna().sort_values(["Frequency (kHz)", "Power Setpoint (W)"])
    if data.empty: return None
    fig, ax = plt.subplots(figsize=(12, 7.5));
    colors = ["#4472C4", "#ED7D31", "#70AD47", "#A5A5A5", "#5B9BD5", "#FFC000"]
    markers = ["o", "s", "^", "D", "v", "P"];
    frequency_count = data["Frequency (kHz)"].nunique()
    for i, (freq, g) in enumerate(data.groupby("Frequency (kHz)", sort=True)):
        g = g.sort_values("Power Setpoint (W)");
        x = g["Power Setpoint (W)"].to_numpy(float)
        avg = g[f"Average_{edge_label}_Time_us"].to_numpy(float);
        med = g[f"Median_{edge_label}_Time_us"].to_numpy(float)
        center = np.where(x <= SMALL_SETPOINT_THRESHOLD, med, avg);
        color = get_frequency_plot_color(i);
        marker = markers[i % len(markers)]
        if trend_range_mode == TREND_RANGE_MODE_WHISKERS:
            lo = center - g[f"Minimum_{edge_label}_Time_us"].to_numpy(float);
            hi = g[f"Maximum_{edge_label}_Time_us"].to_numpy(float) - center
            ax.errorbar(x, center, yerr=np.vstack([np.maximum(0, lo), np.maximum(0, hi)]), color=color, marker=marker,
                        markersize=7, linewidth=2, elinewidth=1.1, capsize=4, capthick=1.1,
                        markeredgecolor="white", markeredgewidth=.8, label=f"{freq:g} kHz")
        else:
            ax.plot(x, center, color=color, marker=marker, markersize=7, linewidth=2,
                    markeredgecolor="white", markeredgewidth=.8, label=f"{freq:g} kHz", zorder=4)
            if trend_range_mode in {TREND_RANGE_MODE_BOX, TREND_RANGE_MODE_BOX_AND_WHISKERS}:
                spac = np.diff(np.sort(np.unique(x)));
                spac = spac[spac > 0];
                width = float(np.min(spac)) * .16 if len(spac) else 20
                offset = (i - (frequency_count - 1) / 2) * width * 1.35
                add_iqr_boxes(ax, g, edge_label, color, x + offset, width,
                              trend_range_mode == TREND_RANGE_MODE_BOX_AND_WHISKERS)
    ax.set_title(f"Median {edge_label} Time at Low Power; Average Above {SMALL_SETPOINT_THRESHOLD:g} W", fontsize=15,
                 fontweight="bold", pad=14)
    ax.set_xlabel("Power Setpoint (W)", fontsize=12);
    ax.set_ylabel(f"{edge_label} Time (us)", fontsize=12)
    ax.grid(True, which="major", color="#BFBFBF", linewidth=.8, alpha=.75);
    ax.grid(True, which="minor", color="#E2E2E2", linewidth=.5, alpha=.55)
    ax.minorticks_on()
    all_y = []
    for _, r in data.iterrows():
        if trend_range_mode in {TREND_RANGE_MODE_WHISKERS, TREND_RANGE_MODE_BOX_AND_WHISKERS}:
            all_y += [r[f"Minimum_{edge_label}_Time_us"], r[f"Maximum_{edge_label}_Time_us"]]
        elif trend_range_mode == TREND_RANGE_MODE_BOX:
            all_y += [r[f"Q1_{edge_label}_Time_us"], r[f"Q3_{edge_label}_Time_us"]]
        else:
            all_y.append(
                r[f"Median_{edge_label}_Time_us"] if r["Power Setpoint (W)"] <= SMALL_SETPOINT_THRESHOLD else r[
                    f"Average_{edge_label}_Time_us"])
    ymin, ymax = float(np.min(all_y)), float(np.max(all_y));
    pad = max((ymax - ymin) * .08, abs(ymax) * .01, .001);
    ax.set_ylim(max(0, ymin - pad), ymax + pad)
    if edge_label == "Rise":
        apply_manual_trend_y_limits(ax, RISE_TREND_Y_MIN_US, RISE_TREND_Y_MAX_US, "Rise")
    else:
        apply_manual_trend_y_limits(ax, FALL_TREND_Y_MIN_US, FALL_TREND_Y_MAX_US, "Fall")
    fig.tight_layout();
    os.makedirs(output_folder, exist_ok=True)
    path = os.path.join(output_folder, f"{edge_label}_Time_vs_Power_Setpoint_Median_Low_Power_by_Frequency.png")
    save_separate_legend_image(ax, path, title="Frequency")
    fig.savefig(path, dpi=300, bbox_inches="tight");
    plt.close(fig);
    print(f"Saved median-at-low-power trend plot: {path}");
    return path


def create_rise_time_trend_plot(rise_summary, output_folder, trend_range_mode=TREND_RANGE_MODE_NONE):
    """Plot average rise time versus setpoint, connecting measured points.

    A separate point-to-point series is created for each frequency. No fitted
    trend model is applied. Values come from the outlier-filtered averages in
    the RISE Summary dataframe.
    """
    if rise_summary.empty:
        print("No rise-time summary data were available for trend plotting.")
        return None

    required_columns = {
        "Power Setpoint (W)",
        "Frequency (kHz)",
        "Average_Rise_Time_us",
        "Minimum_Rise_Time_us",
        "Q1_Rise_Time_us",
        "Median_Rise_Time_us",
        "Q3_Rise_Time_us",
        "Maximum_Rise_Time_us",
    }
    missing_columns = required_columns.difference(rise_summary.columns)
    if missing_columns:
        raise ValueError(
            "Cannot create the rise-time trend plot. Missing columns: "
            + ", ".join(sorted(missing_columns))
        )

    from matplotlib.ticker import AutoMinorLocator, MaxNLocator, ScalarFormatter

    plot_data = (
        rise_summary[
            [
                "Power Setpoint (W)",
                "Frequency (kHz)",
                "Average_Rise_Time_us",
                "Minimum_Rise_Time_us",
                "Q1_Rise_Time_us",
                "Median_Rise_Time_us",
                "Q3_Rise_Time_us",
                "Maximum_Rise_Time_us",
            ]
        ]
        .dropna()
        .sort_values(["Frequency (kHz)", "Power Setpoint (W)"])
    )
    if plot_data.empty:
        print("No complete rise-time rows were available for trend plotting.")
        return None

    figure, axis = plt.subplots(figsize=(12, 7.5))
    colors = ["#4472C4", "#ED7D31", "#70AD47", "#A5A5A5", "#5B9BD5", "#FFC000"]
    markers = ["o", "s", "^", "D", "v", "P"]

    for index, (frequency_khz, group) in enumerate(
            plot_data.groupby("Frequency (kHz)", sort=True)
    ):
        group = group.sort_values("Power Setpoint (W)")
        if trend_range_mode in {TREND_RANGE_MODE_WHISKERS, TREND_RANGE_MODE_BOX_AND_WHISKERS}:
            average_values = group["Average_Rise_Time_us"].to_numpy(dtype=float)
            minimum_values = group["Minimum_Rise_Time_us"].to_numpy(dtype=float)
            maximum_values = group["Maximum_Rise_Time_us"].to_numpy(dtype=float)
            lower_errors = np.maximum(0.0, average_values - minimum_values)
            upper_errors = np.maximum(0.0, maximum_values - average_values)
            axis.errorbar(
                group["Power Setpoint (W)"],
                average_values,
                yerr=np.vstack([lower_errors, upper_errors]),
                color=get_frequency_plot_color(index),
                marker=markers[index % len(markers)],
                markersize=7,
                linewidth=2.0,
                elinewidth=1.1,
                capsize=4,
                capthick=1.1,
                markeredgecolor="white",
                markeredgewidth=0.8,
                label=f"{frequency_khz:g} kHz",
            )
        else:
            axis.plot(
                group["Power Setpoint (W)"],
                group["Average_Rise_Time_us"],
                color=get_frequency_plot_color(index),
                marker=markers[index % len(markers)],
                markersize=7,
                linewidth=2.0,
                markeredgecolor="white",
                markeredgewidth=0.8,
                label=f"{frequency_khz:g} kHz",
            )
            if trend_range_mode in {TREND_RANGE_MODE_BOX, TREND_RANGE_MODE_BOX_AND_WHISKERS}:
                x_values = group["Power Setpoint (W)"].to_numpy(dtype=float)
                spacing = np.diff(np.sort(np.unique(x_values)));
                spacing = spacing[spacing > 0]
                box_width = float(np.min(spacing)) * 0.16 if len(spacing) else 20.0
                frequency_count = max(1, plot_data["Frequency (kHz)"].nunique())
                offset = (index - (frequency_count - 1) / 2.0) * box_width * 1.35
                add_iqr_boxes(axis, group, "Rise", get_frequency_plot_color(index),
                              x_values + offset, box_width,
                              trend_range_mode == TREND_RANGE_MODE_BOX_AND_WHISKERS)
    axis.set_title(
        "Average Rise Time vs. Power Setpoint by Frequency",
        fontsize=15,
        fontweight="bold",
        pad=14,
    )
    axis.set_xlabel("Power Setpoint (W)", fontsize=12)
    axis.set_ylabel("Average Rise Time (us)", fontsize=12)

    minimum_average_index = plot_data["Average_Rise_Time_us"].idxmin()
    maximum_average_index = plot_data["Average_Rise_Time_us"].idxmax()
    minimum_average_row = plot_data.loc[minimum_average_index]
    maximum_average_row = plot_data.loc[maximum_average_index]
    average_range_text = (
        "Across all plotted frequencies:\n"
        f"Minimum average: {minimum_average_row['Average_Rise_Time_us']:.3f} us\n"
        f"  {minimum_average_row['Frequency (kHz)']:g} kHz, "
        f"{minimum_average_row['Power Setpoint (W)']:g} W\n"
        f"Maximum average: {maximum_average_row['Average_Rise_Time_us']:.3f} us\n"
        f"  {maximum_average_row['Frequency (kHz)']:g} kHz, "
        f"{maximum_average_row['Power Setpoint (W)']:g} W"
    )
    info_figure, info_axis = plt.subplots(figsize=(5.2, 1.75))
    info_axis.set_axis_off()
    info_axis.text(
        0.5, 0.5, average_range_text,
        transform=info_axis.transAxes,
        horizontalalignment="center",
        verticalalignment="center",
        fontsize=9,
        bbox={
            "boxstyle": "round",
            "facecolor": "white",
            "edgecolor": "#7F7F7F",
            "alpha": 0.90,
        },
    )

    # Show readable numeric scales and tick marks on both axes.
    axis.xaxis.set_major_locator(MaxNLocator(nbins=10, steps=[1, 2, 2.5, 5, 10]))
    axis.yaxis.set_major_locator(MaxNLocator(nbins=10, steps=[1, 2, 2.5, 5, 10]))
    axis.xaxis.set_minor_locator(AutoMinorLocator(2))
    axis.yaxis.set_minor_locator(AutoMinorLocator(2))
    axis.xaxis.set_major_formatter(ScalarFormatter(useOffset=False))
    axis.yaxis.set_major_formatter(ScalarFormatter(useOffset=False))
    axis.ticklabel_format(axis="both", style="plain", useOffset=False)
    axis.tick_params(axis="both", which="major", direction="out", length=6, width=1.0)
    axis.tick_params(axis="both", which="minor", direction="out", length=3, width=0.8)

    x_min = float(plot_data["Power Setpoint (W)"].min())
    x_max = float(plot_data["Power Setpoint (W)"].max())
    if trend_range_mode == TREND_RANGE_MODE_WHISKERS:
        y_min = float(plot_data["Minimum_Rise_Time_us"].min())
        y_max = float(plot_data["Maximum_Rise_Time_us"].max())
    else:
        y_min = float(plot_data["Average_Rise_Time_us"].min())
        y_max = float(plot_data["Average_Rise_Time_us"].max())
    x_padding = max((x_max - x_min) * 0.04, 1.0)
    y_padding = max((y_max - y_min) * 0.08, abs(y_max) * 0.01, 0.001)
    axis.set_xlim(x_min - x_padding, x_max + x_padding)
    axis.set_ylim(max(0.0, y_min - y_padding), y_max + y_padding)

    apply_manual_trend_y_limits(
        axis,
        RISE_TREND_Y_MIN_US,
        RISE_TREND_Y_MAX_US,
        "Rise",
    )

    axis.grid(True, which="major", color="#BFBFBF", linewidth=0.8, alpha=0.75)
    axis.grid(True, which="minor", color="#E2E2E2", linewidth=0.5, alpha=0.55)
    figure.tight_layout()

    os.makedirs(output_folder, exist_ok=True)
    output_path = os.path.join(output_folder, "Rise_Time_vs_Power_Setpoint_by_Frequency.png")
    info_output_path = os.path.join(
        output_folder, "Rise_Time_vs_Power_Setpoint_by_Frequency_Min_Max_Averages.png"
    )
    info_figure.savefig(info_output_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(info_figure)
    save_separate_legend_image(axis, output_path, title="Frequency")
    figure.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(figure)
    print(f"Saved rise-time trend plot: {output_path}")
    return output_path


def create_fall_time_trend_plot(
        fall_summary,
        output_folder,
        trend_range_mode=TREND_RANGE_MODE_NONE,
        shared_auto_scale_values=None,
):
    """Plot average fall time versus setpoint, connecting measured points.

    A separate point-to-point series is created for each frequency. No fitted
    trend model is applied. Values come from the outlier-filtered averages in
    the FALL Summary dataframe.
    """
    if fall_summary.empty:
        print("No fall-time summary data were available for trend plotting.")
        return None

    required_columns = {
        "Power Setpoint (W)",
        "Frequency (kHz)",
        "Average_Fall_Time_us",
        "Minimum_Fall_Time_us",
        "Q1_Fall_Time_us",
        "Median_Fall_Time_us",
        "Q3_Fall_Time_us",
        "Maximum_Fall_Time_us",
    }
    missing_columns = required_columns.difference(fall_summary.columns)
    if missing_columns:
        raise ValueError(
            "Cannot create the fall-time trend plot. Missing columns: "
            + ", ".join(sorted(missing_columns))
        )

    from matplotlib.ticker import (
        AutoMinorLocator,
        LogLocator,
        MaxNLocator,
        NullFormatter,
        ScalarFormatter,
    )

    plot_data = (
        fall_summary[
            [
                "Power Setpoint (W)",
                "Frequency (kHz)",
                "Average_Fall_Time_us",
                "Minimum_Fall_Time_us",
                "Q1_Fall_Time_us",
                "Median_Fall_Time_us",
                "Q3_Fall_Time_us",
                "Maximum_Fall_Time_us",
            ]
        ]
        .dropna()
        .sort_values(["Frequency (kHz)", "Power Setpoint (W)"])
    )
    if plot_data.empty:
        print("No complete fall-time rows were available for trend plotting.")
        return None

    figure, axis = plt.subplots(figsize=(12, 7.5))
    colors = ["#4472C4", "#ED7D31", "#70AD47", "#A5A5A5", "#5B9BD5", "#FFC000"]
    markers = ["o", "s", "^", "D", "v", "P"]

    for index, (frequency_khz, group) in enumerate(
            plot_data.groupby("Frequency (kHz)", sort=True)
    ):
        group = group.sort_values("Power Setpoint (W)")
        if trend_range_mode in {TREND_RANGE_MODE_WHISKERS, TREND_RANGE_MODE_BOX_AND_WHISKERS}:
            average_values = group["Average_Fall_Time_us"].to_numpy(dtype=float)
            minimum_values = group["Minimum_Fall_Time_us"].to_numpy(dtype=float)
            maximum_values = group["Maximum_Fall_Time_us"].to_numpy(dtype=float)
            lower_errors = np.maximum(0.0, average_values - minimum_values)
            upper_errors = np.maximum(0.0, maximum_values - average_values)
            axis.errorbar(
                group["Power Setpoint (W)"],
                average_values,
                yerr=np.vstack([lower_errors, upper_errors]),
                color=get_frequency_plot_color(index),
                marker=markers[index % len(markers)],
                markersize=7,
                linewidth=2.0,
                elinewidth=1.1,
                capsize=4,
                capthick=1.1,
                markeredgecolor="white",
                markeredgewidth=0.8,
                label=f"{frequency_khz:g} kHz",
            )
        else:
            axis.plot(
                group["Power Setpoint (W)"],
                group["Average_Fall_Time_us"],
                color=get_frequency_plot_color(index),
                marker=markers[index % len(markers)],
                markersize=7,
                linewidth=2.0,
                markeredgecolor="white",
                markeredgewidth=0.8,
                label=f"{frequency_khz:g} kHz",
            )
            if trend_range_mode in {TREND_RANGE_MODE_BOX, TREND_RANGE_MODE_BOX_AND_WHISKERS}:
                x_values = group["Power Setpoint (W)"].to_numpy(dtype=float)
                spacing = np.diff(np.sort(np.unique(x_values)));
                spacing = spacing[spacing > 0]
                box_width = float(np.min(spacing)) * 0.16 if len(spacing) else 20.0
                frequency_count = max(1, plot_data["Frequency (kHz)"].nunique())
                offset = (index - (frequency_count - 1) / 2.0) * box_width * 1.35
                add_iqr_boxes(axis, group, "Fall", get_frequency_plot_color(index),
                              x_values + offset, box_width,
                              trend_range_mode == TREND_RANGE_MODE_BOX_AND_WHISKERS)
    axis.set_title(
        "Average Fall Time vs. Power Setpoint by Frequency",
        fontsize=15,
        fontweight="bold",
        pad=14,
    )
    axis.set_xlabel("Power Setpoint (W)", fontsize=12)
    axis.set_ylabel("Average Fall Time (us)", fontsize=12)

    minimum_average_index = plot_data["Average_Fall_Time_us"].idxmin()
    maximum_average_index = plot_data["Average_Fall_Time_us"].idxmax()
    minimum_average_row = plot_data.loc[minimum_average_index]
    maximum_average_row = plot_data.loc[maximum_average_index]
    average_range_text = (
        "Across all plotted frequencies:\n"
        f"Minimum average: {minimum_average_row['Average_Fall_Time_us']:.3f} us\n"
        f"  {minimum_average_row['Frequency (kHz)']:g} kHz, "
        f"{minimum_average_row['Power Setpoint (W)']:g} W\n"
        f"Maximum average: {maximum_average_row['Average_Fall_Time_us']:.3f} us\n"
        f"  {maximum_average_row['Frequency (kHz)']:g} kHz, "
        f"{maximum_average_row['Power Setpoint (W)']:g} W"
    )
    info_figure, info_axis = plt.subplots(figsize=(5.2, 1.75))
    info_axis.set_axis_off()
    info_axis.text(
        0.5, 0.5, average_range_text,
        transform=info_axis.transAxes,
        horizontalalignment="center",
        verticalalignment="center",
        fontsize=9,
        bbox={
            "boxstyle": "round",
            "facecolor": "white",
            "edgecolor": "#7F7F7F",
            "alpha": 0.90,
        },
    )

    # Show readable numeric scales and tick marks on both axes.
    axis.xaxis.set_major_locator(MaxNLocator(nbins=10, steps=[1, 2, 2.5, 5, 10]))
    axis.xaxis.set_minor_locator(AutoMinorLocator(2))
    axis.xaxis.set_major_formatter(ScalarFormatter(useOffset=False))
    axis.tick_params(axis="both", which="major", direction="out", length=6, width=1.0)
    axis.tick_params(axis="both", which="minor", direction="out", length=3, width=0.8)

    x_min = float(plot_data["Power Setpoint (W)"].min())
    x_max = float(plot_data["Power Setpoint (W)"].max())
    average_y_values = plot_data["Average_Fall_Time_us"].to_numpy(dtype=float)
    if trend_range_mode in {TREND_RANGE_MODE_WHISKERS, TREND_RANGE_MODE_BOX_AND_WHISKERS}:
        minimum_y_values = plot_data["Minimum_Fall_Time_us"].to_numpy(dtype=float)
        maximum_y_values = plot_data["Maximum_Fall_Time_us"].to_numpy(dtype=float)
        local_y_values = np.concatenate([minimum_y_values, maximum_y_values])
    else:
        local_y_values = average_y_values

    # In comparison mode, both dataset plots receive the same combined values.
    # This forces the same linear/log/symlog decision, Y limits, and log ticks.
    if shared_auto_scale_values is None:
        y_values = local_y_values
    else:
        y_values = np.asarray(shared_auto_scale_values, dtype=float)
        y_values = y_values[np.isfinite(y_values)]
        if len(y_values) == 0:
            y_values = local_y_values
    positive_y_values = y_values[y_values > 0]
    y_max = float(np.max(y_values))
    x_padding = max((x_max - x_min) * 0.04, 1.0)
    axis.set_xlim(x_min - x_padding, x_max + x_padding)

    if len(positive_y_values) > 0:
        smallest_positive_fall_time = float(np.min(positive_y_values))
        largest_positive_fall_time = float(np.max(positive_y_values))
        dynamic_range = largest_positive_fall_time / smallest_positive_fall_time
    else:
        smallest_positive_fall_time = 0.0
        largest_positive_fall_time = 0.0
        dynamic_range = 1.0

    # Automatically use a log scale when large fall times would otherwise
    # flatten the smallest measurements against the bottom of the graph.
    if dynamic_range >= 20.0 and np.all(y_values > 0):
        axis.set_yscale("log")
        axis.yaxis.set_major_locator(LogLocator(base=10.0))
        axis.yaxis.set_minor_locator(LogLocator(base=10.0, subs=np.arange(2, 10) * 0.1))
        axis.yaxis.set_major_formatter(ScalarFormatter())
        axis.yaxis.set_minor_formatter(NullFormatter())
        axis.set_ylim(
            smallest_positive_fall_time / 1.25,
            largest_positive_fall_time * 1.25,
        )
        axis.set_ylabel("Average Fall Time (us, logarithmic scale)", fontsize=12)
    elif dynamic_range >= 20.0 and smallest_positive_fall_time > 0:
        # Symmetric-log preserves any true zero values while expanding the
        # small positive measurements into a visible linear region.
        axis.set_yscale(
            "symlog",
            linthresh=smallest_positive_fall_time,
            linscale=1.0,
            base=10,
        )
        axis.set_ylim(
            min(0.0, float(np.min(y_values))),
            largest_positive_fall_time * 1.25,
        )
        axis.set_ylabel("Average Fall Time (us, hybrid log scale)", fontsize=12)
    else:
        y_min = float(np.min(y_values))
        y_padding = max((y_max - y_min) * 0.08, abs(y_max) * 0.01, 0.001)
        axis.yaxis.set_major_locator(MaxNLocator(nbins=10, steps=[1, 2, 2.5, 5, 10]))
        axis.yaxis.set_minor_locator(AutoMinorLocator(2))
        axis.yaxis.set_major_formatter(ScalarFormatter(useOffset=False))
        axis.set_ylim(max(0.0, y_min - y_padding), y_max + y_padding)

    apply_manual_trend_y_limits(
        axis,
        FALL_TREND_Y_MIN_US,
        FALL_TREND_Y_MAX_US,
        "Fall",
    )

    axis.ticklabel_format(axis="x", style="plain", useOffset=False)

    axis.grid(True, which="major", color="#BFBFBF", linewidth=0.8, alpha=0.75)
    axis.grid(True, which="minor", color="#E2E2E2", linewidth=0.5, alpha=0.55)
    figure.tight_layout()

    os.makedirs(output_folder, exist_ok=True)
    output_path = os.path.join(output_folder, "Fall_Time_vs_Power_Setpoint_by_Frequency.png")
    info_output_path = os.path.join(
        output_folder, "Fall_Time_vs_Power_Setpoint_by_Frequency_Min_Max_Averages.png"
    )
    info_figure.savefig(info_output_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(info_figure)
    save_separate_legend_image(axis, output_path, title="Frequency")
    figure.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(figure)
    print(f"Saved fall-time trend plot: {output_path}")
    return output_path


def collect_shared_fall_trend_scale_values(
        fall_summary_a,
        fall_summary_b,
        trend_range_mode,
):
    """Return common FALL trend values for two-dataset automatic scaling.

    The returned values use the same columns that each FALL trend plot would
    use independently. Average-only plots use the frequency/setpoint averages;
    whisker displays use accepted minimum and maximum values. Both datasets
    therefore receive an identical scale type, limits, and logarithmic ticks.
    """
    value_arrays = []
    for summary in (fall_summary_a, fall_summary_b):
        if summary is None or summary.empty:
            continue
        if trend_range_mode in {
            TREND_RANGE_MODE_WHISKERS,
            TREND_RANGE_MODE_BOX_AND_WHISKERS,
        }:
            columns = [
                "Minimum_Fall_Time_us",
                "Maximum_Fall_Time_us",
            ]
        else:
            columns = ["Average_Fall_Time_us"]
        missing = set(columns).difference(summary.columns)
        if missing:
            raise ValueError(
                "Cannot calculate the shared FALL trend scale. Missing columns: "
                + ", ".join(sorted(missing))
            )
        for column in columns:
            values = pd.to_numeric(summary[column], errors="coerce").to_numpy(dtype=float)
            values = values[np.isfinite(values)]
            if len(values) > 0:
                value_arrays.append(values)
    if not value_arrays:
        return None
    return np.concatenate(value_arrays)


def create_powerpoint_summary(
        rise_summary,
        fall_summary,
        setpoint_averages,
        output_path,
):
    """Create CW RISE/FALL slides with Avg/Min/Max/Std Dev per frequency.

    Standard deviation values come directly from the RISE Summary or FALL
    Summary dataframe for each frequency and power-setpoint combination.
    """
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
        from pptx.util import Inches, Pt
    except ImportError as error:
        raise ImportError(
            "PowerPoint generation requires python-pptx. "
            "Install it with: pip install python-pptx"
        ) from error

    presentation = Presentation()
    presentation.slide_width = Inches(13.333333)
    presentation.slide_height = Inches(7.5)
    navy = RGBColor(31, 105, 139)
    light_gray = RGBColor(226, 230, 235)
    medium_gray = RGBColor(202, 208, 215)
    white = RGBColor(255, 255, 255)
    black = RGBColor(20, 20, 20)

    def set_fill(cell, color):
        cell.fill.solid()
        cell.fill.fore_color.rgb = color

    def set_text(cell, value, size=10.5, bold=False, color=black):
        cell.text = str(value)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        paragraph = cell.text_frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.CENTER
        paragraph.space_before = Pt(0)
        paragraph.space_after = Pt(0)
        run = paragraph.runs[0]
        run.font.name = "Arial"
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color

    def format_time_us(value):
        if pd.isna(value):
            return "N/A"
        return f"{float(value):.{POWERPOINT_TABLE_DECIMAL_PLACES}f}"

    def add_edge_slide(summary, edge_type):
        if summary.empty:
            return
        edge_label = "Rise" if edge_type == "RISE" else "Fall"
        average_column = f"Average_{edge_label}_Time_us"
        minimum_column = f"Minimum_{edge_label}_Time_us"
        maximum_column = f"Maximum_{edge_label}_Time_us"
        standard_deviation_column = f"Std_Dev_{edge_label}_Time_us"
        frequencies = sorted(summary["Frequency (kHz)"].dropna().unique())
        setpoints = sorted(summary["Power Setpoint (W)"].dropna().unique())
        if not frequencies or not setpoints:
            return

        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        title = slide.shapes.add_textbox(
            Inches(0.35), Inches(0.12), Inches(12.63), Inches(0.46)
        )
        paragraph = title.text_frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.CENTER
        run = paragraph.add_run()
        run.text = f"Continuous-Wave {edge_type} Summary"
        run.font.name = "Arial"
        run.font.size = Pt(22)
        run.font.bold = True
        run.font.color.rgb = navy

        row_count = 2 + len(setpoints)
        column_count = 1 + 4 * len(frequencies)
        table = slide.shapes.add_table(
            row_count,
            column_count,
            Inches(0.3),
            Inches(0.72),
            Inches(12.73),
            Inches(6.42),
        ).table
        setpoint_width = 1.35
        metric_width = (
                               12.73 - setpoint_width
                       ) / (4 * len(frequencies))
        table.columns[0].width = Inches(setpoint_width)
        for column_index in range(1, 1 + 4 * len(frequencies)):
            table.columns[column_index].width = Inches(metric_width)
        table.rows[0].height = Inches(0.68)
        table.rows[1].height = Inches(0.48)
        for row_index in range(2, row_count):
            table.rows[row_index].height = Inches(
                max(0.30, min(0.54, 5.2 / max(1, len(setpoints))))
            )

        table.cell(0, 0).merge(table.cell(1, 0))
        set_text(table.cell(0, 0), "Power\nSetpoint", 13, True, white)
        set_fill(table.cell(0, 0), navy)

        for frequency_index, frequency_khz in enumerate(frequencies):
            first_column = 1 + 4 * frequency_index
            table.cell(0, first_column).merge(table.cell(0, first_column + 3))
            set_text(
                table.cell(0, first_column),
                f"{float(frequency_khz) / 1000.0:.3f} MHz "
                f"{edge_label} (us)",
                13,
                True,
                white,
            )
            set_fill(table.cell(0, first_column), navy)
            for offset, heading in enumerate(("Avg", "Min", "Max", "Std Dev")):
                set_text(table.cell(1, first_column + offset), heading, 12, True)
                set_fill(table.cell(1, first_column + offset), medium_gray)

        for row_index, power_setpoint in enumerate(setpoints, start=2):
            row_color = light_gray if row_index % 2 == 0 else medium_gray
            set_text(table.cell(row_index, 0), f"{power_setpoint:g}", 11)
            set_fill(table.cell(row_index, 0), row_color)

            for frequency_index, frequency_khz in enumerate(frequencies):
                first_column = 1 + 4 * frequency_index
                match = summary[
                    np.isclose(summary["Power Setpoint (W)"], power_setpoint)
                    & np.isclose(summary["Frequency (kHz)"], frequency_khz)
                    ]
                values = (
                    (np.nan, np.nan, np.nan, np.nan)
                    if match.empty
                    else tuple(
                        match.iloc[0][column]
                        for column in (
                            average_column,
                            minimum_column,
                            maximum_column,
                            standard_deviation_column,
                        )
                    )
                )
                for offset, value in enumerate(values):
                    set_text(
                        table.cell(row_index, first_column + offset),
                        format_time_us(value),
                    )
                    set_fill(
                        table.cell(row_index, first_column + offset), row_color
                    )

    add_edge_slide(rise_summary, "RISE")
    add_edge_slide(fall_summary, "FALL")
    if len(presentation.slides) == 0:
        print("No CW summary data were available for PowerPoint generation.")
        return None
    presentation.save(output_path)
    print(f"PowerPoint summary saved to: {output_path}")
    return output_path


def auto_size_excel(excel_path):
    """Auto-size worksheet columns without failing on merged cells."""
    from openpyxl import load_workbook
    from openpyxl.cell.cell import MergedCell
    from openpyxl.utils import get_column_letter

    workbook = load_workbook(excel_path)
    for worksheet in workbook.worksheets:
        # Preserve the chart sheet's custom layout. Apply the standard table
        # settings only to worksheets that contain tabular output.
        if worksheet.title != "RISE Trend Chart":
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

        for column_index in range(1, worksheet.max_column + 1):
            max_length = 0
            for row_index in range(1, worksheet.max_row + 1):
                cell = worksheet.cell(row=row_index, column=column_index)
                if isinstance(cell, MergedCell) or cell.value is None:
                    continue
                max_length = max(max_length, len(str(cell.value)))

            if max_length > 0:
                column_letter = get_column_letter(column_index)
                worksheet.column_dimensions[column_letter].width = min(
                    max_length + 2,
                    60,
                )

    workbook.save(excel_path)


def open_file(path):
    try:
        if os.name == "nt":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        print(f"Could not automatically open Excel file: {exc}")


def export_powerpoint_package(config,images,data_a,data_b,rise_compare,fall_compare,comparison_excel):
    if not powerpoint_export_requested(): return None
    text=os.environ.get("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER","").strip()
    if not text: raise ValueError("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER is not set.")
    package=Path(text).expanduser().resolve();package.mkdir(parents=True,exist_ok=True);manifest=package/"manifest.json"
    if manifest.exists():manifest.unlink()
    ir=package/"images";tr=package/"tables"
    if ir.exists():shutil.rmtree(ir)
    if tr.exists():shutil.rmtree(tr)
    tr.mkdir(parents=True,exist_ok=True)
    for data,prefix in ((data_a,"dataset_a"),(data_b,"dataset_b")):
        if data:
            for key in ("rise_summary","fall_summary"):
                frame=data.get(key)
                if frame is not None and not frame.empty: frame.to_csv(tr/f"{prefix}_{key}.csv",index=False)
    if rise_compare is not None and not rise_compare.empty:rise_compare.to_csv(tr/"rise_comparison_b_minus_a.csv",index=False)
    if fall_compare is not None and not fall_compare.empty:fall_compare.to_csv(tr/"fall_comparison_b_minus_a.csv",index=False)
    slides=[];copied=[]
    for role,path in images:
        source=Path(path)
        if not source.is_file():continue
        destdir=ir/role;destdir.mkdir(parents=True,exist_ok=True);dest=destdir/source.name
        if dest.exists():dest=destdir/f"{source.parent.name}_{source.name}"
        shutil.copy2(source,dest);rel=dest.relative_to(package).as_posix();copied.append({"dataset_role":role.upper(),"source":str(source),"package_file":rel})
        slide={"slide_type":"image","title":source.name,"image_file":rel,"dataset_role":role.upper()}
        if role=="comparison":slide["difference_convention"]="B_MINUS_A"
        slides.append(slide)
    mode=config["mode"];na=config["name_a"];nb=config["name_b"]
    db={"label":nb,"source_folder":config["folder_b"],"workbook":data_b.get("excel","")} if mode=="COMPARE" else None
    comp={"completed":mode=="COMPARE","difference_convention":"B_MINUS_A","workbook":str(comparison_excel) if comparison_excel else ""}
    payload={"schema_version":int(os.environ.get("PARAMOUNT_POWERPOINT_SCHEMA_VERSION","1")),"analysis_id":os.environ.get("PARAMOUNT_POWERPOINT_ANALYSIS_ID","rise_fall_cw").strip(),"display_name":"CW Rise/Fall Timing","status":"completed","analysis_mode":mode,"dataset_label":na if mode=="SINGLE" else f"{nb} vs {na}","source_folder":config["folder_a"],"dataset_a":{"label":na,"source_folder":config["folder_a"],"workbook":data_a.get("excel","")},"dataset_b":db,"comparison":comp,"highlights":[f"Dataset A: {na}",f"Generated {len(slides)} image slide(s) from this analysis run."],"metrics":{"Analysis mode":mode,"Generated image slides":len(slides)},"generated_images":copied,"slides":slides}
    manifest.write_text(json.dumps(payload,indent=2),encoding="utf-8");return manifest

class ExecutionProgress:
    """Display execution progress in the existing Tk root window."""

    def __init__(self, parent, total_steps):
        self.total = max(1, int(total_steps))
        self.done = 0
        self.window = parent

        # The setup GUI previously withdrew this root and created a child
        # Toplevel. On Windows that child can remain hidden with its owner.
        # Reuse the root itself so the progress window is always mapped.
        for child in self.window.winfo_children():
            child.destroy()

        self.window.title("CW Analysis Progress")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)

        frame = ttk.Frame(self.window, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Processing CW data",
            font=("Segoe UI", 12, "bold"),
        ).pack(pady=(0, 10))

        self.status = tk.StringVar(master=self.window, value="Preparing analysis...")
        ttk.Label(
            frame,
            textvariable=self.status,
            width=70,
            anchor="w",
        ).pack(pady=(0, 8))

        self.percent = tk.StringVar(master=self.window, value="0% complete")
        ttk.Label(
            frame,
            textvariable=self.percent,
            font=("Segoe UI", 10, "bold"),
        ).pack(pady=(0, 6))

        self.value = tk.DoubleVar(master=self.window, value=0.0)
        ttk.Progressbar(
            frame,
            maximum=100.0,
            length=540,
            variable=self.value,
            mode="determinate",
        ).pack()

        self.window.deiconify()
        self.window.update_idletasks()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        x = max(0, (self.window.winfo_screenwidth() - width) // 2)
        y = max(0, (self.window.winfo_screenheight() - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.lift()
        try:
            self.window.attributes("-topmost", True)
            self.window.after(1000, self._release_topmost)
        except tk.TclError:
            pass
        self.window.focus_force()
        self._refresh()

    def _release_topmost(self):
        try:
            if self.window.winfo_exists():
                self.window.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _refresh(self):
        if self.window.winfo_exists():
            self.window.update_idletasks()
            self.window.update()

    def set_status(self, text):
        self.status.set(str(text))
        self._refresh()

    def advance(self, text):
        self.done = min(self.total, self.done + 1)
        value = 100.0 * self.done / self.total
        self.status.set(str(text))
        self.value.set(value)
        self.percent.set(f"{value:.0f}% complete")
        self._refresh()

    def finish(self):
        self.done = self.total
        self.status.set("Analysis complete")
        self.value.set(100.0)
        self.percent.set("100% complete")
        self._refresh()

    def close(self):
        # Keep the root available for the existing completion message box.
        if self.window.winfo_exists():
            self.window.withdraw()


def count_csv_files(folder):
    return sum(1 for name in os.listdir(folder) if name.lower().endswith(".csv"))


def create_dataset_frequency_trend_plots(rise_summary, fall_summary, output_folder,
                                         trend_range_mode, frequency_layout):
    """Use existing trend functions in combined or frequency-filtered mode."""
    if frequency_layout == FREQUENCY_LAYOUT_COMBINED:
        create_rise_time_trend_plot(rise_summary, output_folder, trend_range_mode)
        create_fall_time_trend_plot(fall_summary, output_folder, trend_range_mode)
        create_median_low_power_trend_plot(rise_summary, "Rise", output_folder, trend_range_mode)
        create_median_low_power_trend_plot(fall_summary, "Fall", output_folder, trend_range_mode)
        return
    if frequency_layout != FREQUENCY_LAYOUT_SEPARATE:
        raise ValueError(f"Unsupported frequency layout: {frequency_layout}")
    frequencies = sorted(set(rise_summary.get("Frequency (kHz)", pd.Series(dtype=float)).dropna().unique()) |
                         set(fall_summary.get("Frequency (kHz)", pd.Series(dtype=float)).dropna().unique()))
    shared_fall = collect_shared_fall_trend_scale_values(fall_summary, None, trend_range_mode)

    def rename(name, frequency):
        old = os.path.join(output_folder, name)
        if os.path.exists(old):
            stem, ext = os.path.splitext(name)
            new = os.path.join(output_folder, f"{stem}_{safe_value_for_filename(frequency)}kHz{ext}")
            os.replace(old, new)
            old_legend = os.path.splitext(old)[0] + "_Legend" + ext
            if os.path.exists(old_legend):
                new_legend = os.path.splitext(new)[0] + "_Legend" + ext
                os.replace(old_legend, new_legend)

    global FREQUENCY_PLOT_COLOR_OVERRIDE
    for frequency_index, frequency in enumerate(frequencies):
        rise = rise_summary[
            np.isclose(rise_summary["Frequency (kHz)"], frequency)].copy() if not rise_summary.empty else pd.DataFrame()
        fall = fall_summary[
            np.isclose(fall_summary["Frequency (kHz)"], frequency)].copy() if not fall_summary.empty else pd.DataFrame()
        FREQUENCY_PLOT_COLOR_OVERRIDE = FREQUENCY_PLOT_COLORS[frequency_index % len(FREQUENCY_PLOT_COLORS)]
        try:
            if not rise.empty:
                create_rise_time_trend_plot(rise, output_folder, trend_range_mode)
                rename("Rise_Time_vs_Power_Setpoint_by_Frequency.png", frequency)
                rename("Rise_Time_vs_Power_Setpoint_by_Frequency_Min_Max_Averages.png", frequency)
                create_median_low_power_trend_plot(rise, "Rise", output_folder, trend_range_mode)
                rename("Rise_Time_vs_Power_Setpoint_Median_Low_Power_by_Frequency.png", frequency)
            if not fall.empty:
                create_fall_time_trend_plot(fall, output_folder, trend_range_mode, shared_fall)
                rename("Fall_Time_vs_Power_Setpoint_by_Frequency.png", frequency)
                rename("Fall_Time_vs_Power_Setpoint_by_Frequency_Min_Max_Averages.png", frequency)
                create_median_low_power_trend_plot(fall, "Fall", output_folder, trend_range_mode)
                rename("Fall_Time_vs_Power_Setpoint_Median_Low_Power_by_Frequency.png", frequency)
        finally:
            FREQUENCY_PLOT_COLOR_OVERRIDE = None


def process_cw_dataset(folder, configuration_name, plot_mode, include_count_lines,
                       trend_range_mode=TREND_RANGE_MODE_NONE, progress=None,
                       frequency_layout=FREQUENCY_LAYOUT_COMBINED,
                       analysis_method=ANALYSIS_METHOD_HILBERT):
    """Run the existing CW calculations and outputs for one folder."""
    output_folder = os.path.join(folder, "Modified Plots")
    individual_folder = os.path.join(output_folder, "Individual Plots")
    os.makedirs(output_folder, exist_ok=True)
    if plot_mode == PLOT_MODE_ALL:
        os.makedirs(individual_folder, exist_ok=True)

    csv_files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".csv"))
    rise_results, fall_results, failures = [], [], []
    for filename in csv_files:
        csv_path = os.path.join(folder, filename)
        print(f"\n[{configuration_name}] Processing: {filename}")
        if progress is not None:
            progress.set_status(f"{configuration_name}: analyzing {filename}")
        try:
            edge, power_w, freq_khz, freq_hz = extract_file_info(filename)
            time, voltage, invalid_removed = read_waveform(csv_path)
            processing_settings = get_setpoint_processing_settings(power_w)
            filter_cutoff_hz = processing_settings["filter_cutoff_hz"]
            fall_right_exclusion_fraction = processing_settings[
                "fall_right_exclusion_fraction"
            ]
            rms_diagnostics = {}
            if analysis_method == ANALYSIS_METHOD_RMS:
                plot_signal, requested_window, applied_window, applied_cycles, sample_rate_hz = extract_rms_peak_envelope(
                    time, voltage, freq_hz
                )
                values = calculate_rms_cw_edge(time, plot_signal, edge)
                v_max, v10, v90, idx10, idx90, t10, t90, measured_time, initial_level, final_level = values
                rms_diagnostics = {
                    "Requested RMS Window Samples": requested_window,
                    "Applied RMS Window Samples": applied_window,
                    "Applied RMS Window Cycles": applied_cycles,
                    "RMS Window Duration (s)": applied_window / sample_rate_hz,
                    "Sample Rate (Sa/s)": sample_rate_hz,
                    "Initial Settled Envelope Level (V)": initial_level,
                    "Final Settled Envelope Level (V)": final_level,
                }
                filter_cutoff_hz = np.nan
            elif analysis_method == ANALYSIS_METHOD_HILBERT:
                if edge == "RISE":
                    filter_cutoff_hz = RISE_FILTER_CUTOFF_HZ
                plot_signal = apply_hilbert(voltage, time, filter_cutoff_hz)
                if edge == "RISE":
                    values = calculate_low_power_rise_time(time, plot_signal)
                    v_max, v10, v90, idx10, idx90, t10, t90, measured_time = values
                else:
                    values = calculate_low_power_fall_time(
                        time, plot_signal, fall_right_exclusion_fraction
                    )
                    v_max, v90, v10, idx90, idx10, t90, t10, measured_time = values
            else:
                raise ValueError(f"Unsupported analysis method: {analysis_method}")
            if edge == "RISE":
                result = {
                    "Filename": filename, "CSV Path": csv_path, "Type": edge,
                    "Power Setpoint (W)": power_w, "Frequency (kHz)": freq_khz,
                    "Frequency (Hz)": freq_hz, "Max Voltage (V)": v_max,
                    "10% Voltage (V)": v10, "90% Voltage (V)": v90,
                    "t10 (s)": t10, "t90 (s)": t90,
                    "Rise Time (s)": measured_time, "Rise Time (us)": measured_time * 1e6,
                    "Rise Time (ns)": measured_time * 1e9, "Valid Samples": len(time),
                    "Invalid Samples Removed": invalid_removed,
                    "Applied Hilbert Filter Cutoff (Hz)": filter_cutoff_hz,
                    "Applied FALL Right Exclusion Fraction": (
                        fall_right_exclusion_fraction if edge == "FALL" else np.nan
                    ),
                    "Analysis Method": analysis_method,
                    **rms_diagnostics,
                    "Configuration": configuration_name,
                }
                rise_results.append(result)
            else:
                result = {
                    "Filename": filename, "CSV Path": csv_path, "Type": edge,
                    "Power Setpoint (W)": power_w, "Frequency (kHz)": freq_khz,
                    "Frequency (Hz)": freq_hz, "Max Voltage (V)": v_max,
                    "10% Voltage (V)": v10, "90% Voltage (V)": v90,
                    "t10 (s)": t10, "t90 (s)": t90,
                    "Fall Time (s)": measured_time, "Fall Time (us)": measured_time * 1e6,
                    "Fall Time (ns)": measured_time * 1e9, "Valid Samples": len(time),
                    "Invalid Samples Removed": invalid_removed,
                    "Applied Hilbert Filter Cutoff (Hz)": filter_cutoff_hz,
                    "Applied FALL Right Exclusion Fraction": (
                        fall_right_exclusion_fraction if edge == "FALL" else np.nan
                    ),
                    "Analysis Method": analysis_method,
                    **rms_diagnostics,
                    "Configuration": configuration_name,
                }
                fall_results.append(result)
            print(f"{edge} | {freq_khz:g} kHz | {power_w:g} W | Time = {measured_time:.9e} s")
            if plot_mode == PLOT_MODE_ALL:
                uses_hilbert_envelope = edge in {"RISE", "FALL"}
                plot_individual(
                    time=time,
                    analysis_signal=plot_signal,
                    row=result,
                    output_folder=individual_folder,
                    raw_voltage=voltage,
                    show_raw_and_envelope=True,
                    analysis_method=analysis_method,
                )
        except Exception as error:
            print(f"ERROR processing {filename}: {error}")
            failures.append({"Configuration": configuration_name,
                             "Filename": filename, "Error": str(error)})
        finally:
            if progress is not None:
                progress.advance(f"{configuration_name}: completed {filename}")

    rise_df, fall_df = pd.DataFrame(rise_results), pd.DataFrame(fall_results)
    failed_df = pd.DataFrame(failures)
    sort_cols = ["Frequency (kHz)", "Power Setpoint (W)", "Filename"]
    if not rise_df.empty: rise_df = rise_df.sort_values(sort_cols)
    if not fall_df.empty: fall_df = fall_df.sort_values(sort_cols)
    rise_summary, fall_summary = pd.DataFrame(), pd.DataFrame()
    if not rise_df.empty:
        rise_summary, rise_df = create_summary_dataframe(rise_df, "Rise Time (s)", "Rise")
    if not fall_df.empty:
        fall_summary, fall_df = create_summary_dataframe(fall_df, "Fall Time (s)", "Fall")
    frequency_min_max = create_frequency_min_max_summary(rise_df, fall_df)
    rise_average = create_cross_frequency_setpoint_average(rise_summary, "Rise")
    fall_average = create_cross_frequency_setpoint_average(fall_summary, "Fall")
    setpoint_averages = combine_setpoint_averages(rise_average, fall_average)

    if plot_mode == PLOT_MODE_SUMMARY:
        create_group_summary_plots_filter_view(
            rise_df, "RISE", output_folder, include_count_lines)
        create_group_summary_plots_filter_view(
            fall_df, "FALL", output_folder, include_count_lines)
    create_dataset_frequency_trend_plots(
        rise_summary, fall_summary, output_folder, trend_range_mode, frequency_layout
    )

    excel_path = os.path.join(folder, "Rise_Fall_Time_Summary.xlsx")
    rise_outliers = rise_df[~rise_df["Use In Average"]].copy() if not rise_df.empty else pd.DataFrame()
    fall_outliers = fall_df[~fall_df["Use In Average"]].copy() if not fall_df.empty else pd.DataFrame()
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        for frame, sheet in [
            (setpoint_averages, "Setpoint Averages"), (frequency_min_max, "Frequency Min Max"),
            (rise_summary, "RISE Summary"), (fall_summary, "FALL Summary"),
            (rise_df, "RISE Individual Results"), (fall_df, "FALL Individual Results"),
            (rise_outliers, "RISE Excluded Outliers"), (fall_outliers, "FALL Excluded Outliers"),
            (failed_df, "Failed Files")]:
            if not frame.empty: frame.to_excel(writer, sheet_name=sheet, index=False)
    auto_size_excel(excel_path)
    frequency_csv = os.path.join(folder, "CW_Frequency_Min_Max_Rise_Fall_Times.csv")
    if not frequency_min_max.empty: frequency_min_max.to_csv(frequency_csv, index=False)
    powerpoint_path = os.path.join(folder, POWERPOINT_FILENAME)
    powerpoint_result = create_powerpoint_summary(
        rise_summary, fall_summary, setpoint_averages, powerpoint_path)
    if progress is not None:
        progress.advance(f"{configuration_name}: all dataset outputs complete")
    return {"name": configuration_name, "folder": folder, "output": output_folder,
            "excel": excel_path, "powerpoint": powerpoint_result,
            "rise_df": rise_df, "fall_df": fall_df, "failed": failed_df,
            "rise_summary": rise_summary, "fall_summary": fall_summary}


def create_cw_comparison(summary_a, summary_b, edge, name_a, name_b):
    keys = ["Frequency (kHz)", "Frequency (Hz)", "Power Setpoint (W)"]
    avg = f"Average_{edge.title()}_Time_us"
    std = f"Std_Dev_{edge.title()}_Time_us"
    count = "Included_Sample_Count"
    if summary_a.empty or summary_b.empty:
        return pd.DataFrame()
    a = summary_a[keys + [avg, std, count]].rename(columns={
        avg: f"{name_a} Average (us)", std: f"{name_a} Std Dev (us)",
        count: f"{name_a} Samples Used"})
    b = summary_b[keys + [avg, std, count]].rename(columns={
        avg: f"{name_b} Average (us)", std: f"{name_b} Std Dev (us)",
        count: f"{name_b} Samples Used"})
    result = a.merge(b, on=keys, how="outer", indicator=True)
    avga, avgb = f"{name_a} Average (us)", f"{name_b} Average (us)"
    result["Matched Condition"] = result["_merge"].eq("both")
    result["Difference B-A (us)"] = result[avgb] - result[avga]
    result["Absolute Difference (us)"] = result["Difference B-A (us)"].abs()
    result["Percent Difference vs A (%)"] = np.where(
        result[avga].notna() & (result[avga] != 0),
        100.0 * result["Difference B-A (us)"] / result[avga], np.nan)
    result["Absolute Percent Difference (%)"] = result["Percent Difference vs A (%)"].abs()
    result["B/A Ratio"] = np.where(
        result[avga].notna() & (result[avga] != 0), result[avgb] / result[avga], np.nan)
    pooled = np.sqrt(result[f"{name_a} Std Dev (us)"].fillna(0) ** 2 +
                     result[f"{name_b} Std Dev (us)"].fillna(0) ** 2)
    result["Difference / Combined Std Dev"] = np.where(
        pooled > 0, result["Absolute Difference (us)"] / pooled, np.nan)
    result["Edge"] = edge.upper()
    result["Availability"] = result["_merge"].map({
        "both": "Matched", "left_only": f"Only {name_a}", "right_only": f"Only {name_b}"})
    return result.drop(columns="_merge").sort_values(keys).reset_index(drop=True)


def create_cw_comparison_plot(comparison, edge, name_a, name_b, destination,
                              frequency_layout=FREQUENCY_LAYOUT_COMBINED):
    if comparison.empty: return None
    matched = comparison[comparison["Matched Condition"]].copy()
    if matched.empty: return None
    if frequency_layout == FREQUENCY_LAYOUT_SEPARATE:
        paths = []
        global FREQUENCY_PLOT_COLOR_OVERRIDE
        for frequency_index, (frequency, group) in enumerate(matched.groupby("Frequency (kHz)", sort=True)):
            FREQUENCY_PLOT_COLOR_OVERRIDE = FREQUENCY_PLOT_COLORS[frequency_index % len(FREQUENCY_PLOT_COLORS)]
            try:
                path = create_cw_comparison_plot(group, edge, name_a, name_b, destination, FREQUENCY_LAYOUT_COMBINED)
            finally:
                FREQUENCY_PLOT_COLOR_OVERRIDE = None
            if path:
                stem, ext = os.path.splitext(path);
                new_path = f"{stem}_{safe_value_for_filename(frequency)}kHz{ext}"
                os.replace(path, new_path)
                old_legend = f"{stem}_Legend{ext}"
                if os.path.exists(old_legend): os.replace(old_legend, f"{os.path.splitext(new_path)[0]}_Legend{ext}")
                paths.append(new_path)
        return paths
    if frequency_layout != FREQUENCY_LAYOUT_COMBINED:
        raise ValueError(f"Unsupported frequency layout: {frequency_layout}")
    avga, avgb = f"{name_a} Average (us)", f"{name_b} Average (us)"
    fig, (top, delta) = plt.subplots(2, 1, figsize=(12, 9), sharex=True,
                                     gridspec_kw={"height_ratios": [2.2, 1.0]})
    colors = ["#4472C4", "#ED7D31", "#70AD47", "#A5A5A5", "#5B9BD5", "#FFC000"]
    for index, (frequency, group) in enumerate(matched.groupby("Frequency (kHz)", sort=True)):
        group = group.sort_values("Power Setpoint (W)")
        x = group["Power Setpoint (W)"]
        color = get_frequency_plot_color(index)
        top.plot(x, group[avga], color=color, marker="o", linestyle="-",
                 label=f"{name_a}, {frequency:g} kHz")
        top.plot(x, group[avgb], color=color, marker="s", linestyle="--",
                 label=f"{name_b}, {frequency:g} kHz")
        delta.plot(x, group["Difference B-A (us)"], color=color, marker="o",
                   label=f"{frequency:g} kHz")
    limits = MANUAL_VERTICAL_SCALES[edge.upper()]
    if limits[0] is not None and limits[1] is not None:
        top.set_ylim(*limits)
    else:
        values = pd.concat([matched[avga], matched[avgb]]).dropna()
        low, high = float(values.min()), float(values.max())
        pad = max((high - low) * 0.08, abs(high) * 0.01, 0.001)
        top.set_ylim(max(0.0, low - pad), high + pad)
    dmax = float(matched["Absolute Difference (us)"].max())
    delta.set_ylim(-max(dmax * 1.1, 0.001), max(dmax * 1.1, 0.001))
    top.set_ylabel(f"Average {edge.title()} Time (us)")
    delta.set_ylabel("B - A (us)");
    delta.set_xlabel("Power Setpoint (W)")
    delta.axhline(0, color="black", linewidth=1)
    top.grid(True, alpha=0.3);
    delta.grid(True, alpha=0.3)
    fig.tight_layout();
    os.makedirs(destination, exist_ok=True)
    path = os.path.join(destination, f"CW_{edge.upper()}_Hardware_Comparison.png")
    save_separate_legend_image(
        [top, delta], path, title="Configuration / Frequency"
    )
    fig.savefig(path, dpi=300, bbox_inches="tight");
    plt.close(fig)
    return path


def create_cw_frequency_boxplot_comparisons(individual_a, individual_b, edge, name_a, name_b, destination):
    """Create one narrow, unconnected side-by-side A/B boxplot per frequency.

    Only rows accepted by the existing IQR filtering are used. Boxes show Q1,
    median, and Q3; whiskers show accepted minimum and maximum. No points or
    lines are drawn between power setpoints.
    """
    edge_label = edge.title()
    value_column = f"{edge_label} Time (us)"
    required = {"Frequency (kHz)", "Power Setpoint (W)", "Use In Average", value_column}
    prepared = []
    for dataframe, configuration_name in ((individual_a, name_a), (individual_b, name_b)):
        if dataframe is None or dataframe.empty:
            prepared.append(pd.DataFrame(columns=list(required)))
            continue
        missing = required.difference(dataframe.columns)
        if missing:
            raise ValueError(
                f"Cannot create {edge_label.lower()} comparison boxplots for {configuration_name}. "
                "Missing columns: " + ", ".join(sorted(missing))
            )
        accepted = dataframe.loc[dataframe["Use In Average"].eq(True), list(required)].copy()
        accepted[value_column] = pd.to_numeric(accepted[value_column], errors="coerce")
        accepted = accepted[
            np.isfinite(accepted[value_column])
            & np.isfinite(accepted["Frequency (kHz)"])
            & np.isfinite(accepted["Power Setpoint (W)"])
            ]
        prepared.append(accepted)

    data_a, data_b = prepared
    if data_a.empty and data_b.empty:
        print(f"No accepted {edge_label.lower()} measurements were available for comparison boxplots.")
        return []

    frequencies = sorted(set(data_a["Frequency (kHz)"].unique()) | set(data_b["Frequency (kHz)"].unique()))
    os.makedirs(destination, exist_ok=True)
    paths = []
    configuration_styles = (
        (name_a, -0.16, "#4472C4"),
        (name_b, 0.16, "#ED7D31"),
    )

    for frequency_khz in frequencies:
        freq_a = data_a[np.isclose(data_a["Frequency (kHz)"], frequency_khz)].copy()
        freq_b = data_b[np.isclose(data_b["Frequency (kHz)"], frequency_khz)].copy()
        frequency_configurations = (
            (name_a, freq_a, -0.16, "#4472C4"),
            (name_b, freq_b, 0.16, "#ED7D31"),
        )
        for configuration_name, frequency_data, _, _ in frequency_configurations:
            if not frequency_data.empty and not np.all(
                    np.isclose(frequency_data["Frequency (kHz)"], frequency_khz)
            ):
                raise RuntimeError(
                    f"Frequency filtering failed for {configuration_name} at "
                    f"{frequency_khz:g} kHz."
                )
        setpoints = sorted(set(freq_a["Power Setpoint (W)"].unique()) | set(freq_b["Power Setpoint (W)"].unique()))
        if not setpoints:
            continue
        figure, axis = plt.subplots(figsize=(max(12.0, 1.05 * len(setpoints) + 4.0), 7.5))
        base_positions = np.arange(len(setpoints), dtype=float)
        plotted_values = []

        # Draw isolated boxes only. No plot or errorbar call connects setpoints.
        for configuration_name, frequency_data, offset, color in frequency_configurations:
            stats, positions = [], []
            for base_position, setpoint in zip(base_positions, setpoints):
                values = frequency_data.loc[
                    np.isclose(frequency_data["Power Setpoint (W)"], setpoint), value_column
                ].dropna()
                if values.empty:
                    continue
                plotted_values.extend(values.astype(float).tolist())
                stats.append({
                    "label": "", "whislo": float(values.min()),
                    "q1": float(values.quantile(0.25)), "med": float(values.median()),
                    "q3": float(values.quantile(0.75)), "whishi": float(values.max()),
                    "fliers": [],
                })
                positions.append(float(base_position + offset))
            if stats:
                axis.bxp(
                    stats, positions=positions, widths=0.20, showfliers=False,
                    patch_artist=True, manage_ticks=False,
                    boxprops={"facecolor": color, "edgecolor": color, "alpha": 0.32, "linewidth": 1.4},
                    medianprops={"color": color, "linewidth": 2.2},
                    whiskerprops={"color": color, "linewidth": 1.2},
                    capprops={"color": color, "linewidth": 1.2}, zorder=3,
                )

        from matplotlib.patches import Patch
        axis.set_title(f"{edge_label} Time Distribution Comparison | {frequency_khz:g} kHz",
                       fontsize=15, fontweight="bold", pad=14)
        axis.set_xlabel("Power Setpoint (W)", fontsize=12)
        axis.set_ylabel(f"{edge_label} Time (us)", fontsize=12)
        axis.set_xticks(base_positions)
        axis.set_xticklabels([f"{setpoint:g}" for setpoint in setpoints])
        axis.set_xlim(-0.65, len(setpoints) - 0.35)
        axis.grid(False, axis="x")
        axis.grid(True, axis="y", which="major", color="#BFBFBF", linewidth=0.8, alpha=0.75)
        axis.grid(True, axis="y", which="minor", color="#E2E2E2", linewidth=0.5, alpha=0.55)
        axis.minorticks_on()

        limits = MANUAL_VERTICAL_SCALES[edge.upper()]
        if limits[0] is not None or limits[1] is not None:
            apply_manual_trend_y_limits(axis, limits[0], limits[1], edge_label)
            axis.set_ylabel(f"{edge_label} Time (us)", fontsize=12)
        elif plotted_values:
            low, high = float(np.min(plotted_values)), float(np.max(plotted_values))
            padding = max((high - low) * 0.08, abs(high) * 0.01, 0.001)
            axis.set_ylim(max(0.0, low - padding), high + padding)

        figure.tight_layout()
        filename = (f"CW_{edge.upper()}_{safe_value_for_filename(frequency_khz)}kHz_"
                    "A_B_Box_Whisker_Comparison.png")
        path = os.path.join(destination, filename)
        from matplotlib.patches import Patch
        legend_handles = [
            Patch(facecolor=color, edgecolor=color, alpha=0.32, label=name)
            for name, _, color in configuration_styles
        ]
        for handle in legend_handles:
            axis.add_patch(Patch(facecolor="none", edgecolor="none", label=handle.get_label()))
        # Use explicit handles because box artists do not create standard labels.
        legend_figure = plt.figure(figsize=(6.5, 1.8))
        legend_axis = legend_figure.add_subplot(111);
        legend_axis.set_axis_off()
        legend_axis.legend(handles=legend_handles, title="Configuration", loc="center")
        stem, extension = os.path.splitext(path)
        legend_path = f"{stem}_Legend{extension}"
        legend_figure.savefig(legend_path, dpi=300, bbox_inches="tight", facecolor="white")
        plt.close(legend_figure)
        print(f"Saved separate legend image: {legend_path}")
        figure.savefig(path, dpi=300, bbox_inches="tight")
        plt.close(figure)
        paths.append(path)
        print(f"Saved {edge_label.lower()} comparison boxplot: {path}")
    return paths


def configure_cw_analysis_with_gui():
    """Collect all pre-analysis choices in one tabbed CW analysis window."""
    root = tk.Tk()
    root.title("CW Statistical Timing Data Analysis")
    root.geometry("1020x760")
    root.minsize(900, 660)

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
    ttk.Label(outer, text="CW Statistical Timing Data Analysis", style="Header.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Select the CW data folders and configure waveform plots, trend plots, "
            "comparison charts, vertical scales, and report outputs before analysis begins."
        ),
        wraplength=960,
    ).pack(anchor="w", pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    input_tab = ttk.Frame(notebook, padding=12)
    method_tab = ttk.Frame(notebook, padding=12)
    waveform_tab = ttk.Frame(notebook, padding=12)
    trend_tab = ttk.Frame(notebook, padding=12)
    comparison_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(input_tab, text="Input Data")
    notebook.add(method_tab, text="Analysis Method")
    notebook.add(waveform_tab, text="Waveform Plots")
    notebook.add(trend_tab, text="Trend Plots")
    notebook.add(comparison_tab, text="Comparison")
    notebook.add(output_tab, text="Output Summary")

    launcher_mode = os.environ.get("PARAMOUNT_ANALYSIS_MODE", "INDIVIDUAL").strip().upper()
    initial_mode = launcher_mode if launcher_mode in {"SINGLE", "COMPARE"} else "SINGLE"
    mode = tk.StringVar(value=initial_mode)
    folder_a = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_FOLDER", "").strip())
    folder_b = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_FOLDER", "").strip())
    name_a = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_LABEL", "").strip() or "Configuration A")
    name_b = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_LABEL", "").strip() or "Configuration B")
    analysis_method = tk.StringVar(value=ANALYSIS_METHOD_HILBERT)
    plot_mode = tk.StringVar(value=PLOT_MODE_SUMMARY)
    include_counts = tk.BooleanVar(value=True)
    frequency_layout = tk.StringVar(value=FREQUENCY_LAYOUT_COMBINED)
    trend_range_mode = tk.StringVar(value=TREND_RANGE_MODE_NONE)
    comparison_plot_mode = tk.StringVar(value=COMPARISON_PLOT_MODE_BOX_WHISKER)
    use_manual_scales = tk.BooleanVar(value=False)
    rise_min = tk.StringVar(value="" if RISE_TREND_Y_MIN_US is None else str(RISE_TREND_Y_MIN_US))
    rise_max = tk.StringVar(value="" if RISE_TREND_Y_MAX_US is None else str(RISE_TREND_Y_MAX_US))
    fall_min = tk.StringVar(value="" if FALL_TREND_Y_MIN_US is None else str(FALL_TREND_Y_MIN_US))
    fall_max = tk.StringVar(value="" if FALL_TREND_Y_MAX_US is None else str(FALL_TREND_Y_MAX_US))
    discovery_text = tk.StringVar(value="Select a CW collection folder to preview discovered CSV files.")

    ttk.Label(input_tab, text="Analysis mode", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
    mode_frame = ttk.Frame(input_tab)
    mode_frame.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 12))
    ttk.Radiobutton(mode_frame, text="Single dataset", variable=mode, value="SINGLE").pack(side="left", padx=(0, 20))
    ttk.Radiobutton(mode_frame, text="Compare two hardware configurations", variable=mode, value="COMPARE").pack(side="left")

    input_tab.columnconfigure(1, weight=1)
    ttk.Label(input_tab, text="Configuration A folder").grid(row=2, column=0, sticky="w", pady=5)
    ttk.Entry(input_tab, textvariable=folder_a, width=65).grid(row=2, column=1, sticky="ew", pady=5)
    ttk.Label(input_tab, text="Display name").grid(row=3, column=0, sticky="w", pady=5)
    ttk.Entry(input_tab, textvariable=name_a, width=35).grid(row=3, column=1, sticky="w", pady=5)
    ttk.Label(input_tab, text="Configuration B folder").grid(row=4, column=0, sticky="w", pady=5)
    folder_b_entry = ttk.Entry(input_tab, textvariable=folder_b, width=65)
    folder_b_entry.grid(row=4, column=1, sticky="ew", pady=5)
    ttk.Label(input_tab, text="Display name").grid(row=5, column=0, sticky="w", pady=5)
    name_b_entry = ttk.Entry(input_tab, textvariable=name_b, width=35)
    name_b_entry.grid(row=5, column=1, sticky="w", pady=5)

    def preview_folder(path_text):
        path = Path(path_text.strip()) if path_text.strip() else None
        if path is None or not path.is_dir():
            return "Folder not selected or does not exist."
        csv_files = sorted(path.glob("*.csv"))
        rise = fall = invalid = 0
        frequencies = set(); setpoints = set()
        for item in csv_files:
            try:
                edge, power, frequency, _ = extract_file_info(item.name)
                rise += edge == "RISE"; fall += edge == "FALL"
                frequencies.add(frequency); setpoints.add(power)
            except Exception:
                invalid += 1
        return (
            f"CSV files: {len(csv_files)} | RISE: {rise} | FALL: {fall} | "
            f"Unrecognized names: {invalid}\n"
            f"Frequencies (kHz): {', '.join(f'{v:g}' for v in sorted(frequencies)) or 'None'}\n"
            f"Setpoints (W): {', '.join(f'{v:g}' for v in sorted(setpoints)) or 'None'}"
        )

    def browse_folder(variable, label_variable):
        current = variable.get().strip()
        selected = filedialog.askdirectory(title="Select CW CSV collection folder", parent=root, mustexist=True,
                                           initialdir=current if current and Path(current).is_dir() else None)
        if selected:
            variable.set(selected)
            label_variable.set(os.path.basename(os.path.normpath(selected)) or label_variable.get())
            discovery_text.set(preview_folder(folder_a.get()))

    ttk.Button(input_tab, text="Browse...", command=lambda: browse_folder(folder_a, name_a)).grid(row=2, column=2, padx=(10, 0))
    folder_b_button = ttk.Button(input_tab, text="Browse...", command=lambda: browse_folder(folder_b, name_b))
    folder_b_button.grid(row=4, column=2, padx=(10, 0))
    ttk.Label(input_tab, textvariable=discovery_text, foreground="#444444", wraplength=850, justify="left").grid(
        row=6, column=0, columnspan=3, sticky="w", pady=(16, 0))

    ttk.Label(method_tab, text="Waveform timing algorithm", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
    hilbert_frame = ttk.LabelFrame(method_tab, text="Hilbert Transform Envelope", padding=12)
    hilbert_frame.pack(fill="x", pady=(0, 10))
    ttk.Radiobutton(hilbert_frame, text="Use the existing filtered Hilbert-envelope method", variable=analysis_method, value=ANALYSIS_METHOD_HILBERT).pack(anchor="w")
    ttk.Label(hilbert_frame, text="Default. Preserves the existing RISE/FALL calculations and setpoint-dependent filter behavior.", foreground="#555555", wraplength=820).pack(anchor="w", pady=(6, 0))
    rms_frame = ttk.LabelFrame(method_tab, text="RMS Envelope", padding=12)
    rms_frame.pack(fill="x")
    ttk.Radiobutton(rms_frame, text="Use an 8-RF-cycle centered moving-RMS peak-equivalent envelope", variable=analysis_method, value=ANALYSIS_METHOD_RMS).pack(anchor="w")
    ttk.Label(rms_frame, text="Uses settled beginning/end plateaus, baseline-referenced 10%/90% thresholds, and linear crossing interpolation for both RISE and FALL.", foreground="#555555", wraplength=820).pack(anchor="w", pady=(6, 0))
    ttk.Label(waveform_tab, text="Waveform plot mode", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
    for label, value in (
        ("Plot every CSV", PLOT_MODE_ALL),
        ("One representative summary plot per frequency/power group", PLOT_MODE_SUMMARY),
        ("No waveform plots; Excel/report outputs only", PLOT_MODE_NONE),
    ):
        ttk.Radiobutton(waveform_tab, text=label, variable=plot_mode, value=value).pack(anchor="w", pady=4)
    counts_check = ttk.Checkbutton(
        waveform_tab,
        text="Include both 'Samples used' and 'Outliers excluded' lines on summary plots",
        variable=include_counts,
    )
    counts_check.pack(anchor="w", pady=(18, 5))
    ttk.Label(
        waveform_tab,
        text="This paired option is used only when summary waveform plots are selected.",
        foreground="#555555",
    ).pack(anchor="w")

    ttk.Label(trend_tab, text="Frequency curve layout", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
    ttk.Radiobutton(trend_tab, text="All frequency curves on the same graph", variable=frequency_layout,
                    value=FREQUENCY_LAYOUT_COMBINED).grid(row=1, column=0, columnspan=3, sticky="w", pady=3)
    ttk.Radiobutton(trend_tab, text="Separate graph for each frequency", variable=frequency_layout,
                    value=FREQUENCY_LAYOUT_SEPARATE).grid(row=2, column=0, columnspan=3, sticky="w", pady=3)
    ttk.Label(trend_tab, text="Range display", style="Section.TLabel").grid(row=3, column=0, sticky="w", pady=(16, 6))
    for row, (label, value) in enumerate((
        ("No additional box or whiskers", TREND_RANGE_MODE_NONE),
        ("IQR box only", TREND_RANGE_MODE_BOX),
        ("Accepted minimum/maximum whiskers only", TREND_RANGE_MODE_WHISKERS),
        ("IQR box with accepted minimum/maximum whiskers", TREND_RANGE_MODE_BOX_AND_WHISKERS),
    ), start=4):
        ttk.Radiobutton(trend_tab, text=label, variable=trend_range_mode, value=value).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=3)

    manual_check = ttk.Checkbutton(trend_tab, text="Use manual RISE/FALL vertical limits (microseconds)", variable=use_manual_scales)
    manual_check.grid(row=8, column=0, columnspan=3, sticky="w", pady=(18, 8))
    ttk.Label(trend_tab, text="Edge").grid(row=9, column=0, sticky="w")
    ttk.Label(trend_tab, text="Minimum (us)").grid(row=9, column=1, sticky="w")
    ttk.Label(trend_tab, text="Maximum (us)").grid(row=9, column=2, sticky="w")
    scale_entries=[]
    for row, edge, lo, hi in ((10,"RISE",rise_min,rise_max),(11,"FALL",fall_min,fall_max)):
        ttk.Label(trend_tab, text=edge).grid(row=row,column=0,sticky="w",pady=4)
        e1=ttk.Entry(trend_tab,textvariable=lo,width=18); e1.grid(row=row,column=1,sticky="w",pady=4)
        e2=ttk.Entry(trend_tab,textvariable=hi,width=18); e2.grid(row=row,column=2,sticky="w",pady=4)
        scale_entries.extend([e1,e2])

    ttk.Label(comparison_tab, text="Comparison chart style", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
    comparison_controls=[]
    for label,value in (
        ("Frequency-specific side-by-side box-and-whisker charts", COMPARISON_PLOT_MODE_BOX_WHISKER),
        ("Original average-line chart with B-minus-A panel", COMPARISON_PLOT_MODE_ORIGINAL),
    ):
        control=ttk.Radiobutton(comparison_tab,text=label,variable=comparison_plot_mode,value=value)
        control.pack(anchor="w",pady=4); comparison_controls.append(control)
    ttk.Label(
        comparison_tab,
        text="Comparison outputs use Configuration B minus Configuration A. These controls are enabled only in comparison mode.",
        foreground="#555555", wraplength=780,
    ).pack(anchor="w", pady=(16,0))

    ttk.Label(output_tab, text="Outputs generated by the existing analysis engine", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        output_tab,
        text=(
            "Each selected dataset retains its current output behavior: Rise_Fall_Time_Summary.xlsx, "
            "frequency min/max CSV, CW_Rise_Fall_Summary.pptx, trend plots, waveform plots based on "
            "the selected mode, excluded-outlier sheets, failed-file details, and comparison outputs "
            "when two datasets are selected. Outputs remain in the selected collection folders."
        ),
        wraplength=850, justify="left",
    ).pack(anchor="w", pady=(8,16))
    output_preview=tk.StringVar()
    ttk.Label(output_tab,textvariable=output_preview,foreground="#333333",wraplength=850,justify="left").pack(anchor="w")

    result={"accepted":False}

    def parse_optional_pair(low_var, high_var, edge):
        low_text=low_var.get().strip(); high_text=high_var.get().strip()
        if not use_manual_scales.get(): return (None,None)
        if not low_text or not high_text: raise ValueError(f"Enter both {edge} limits when manual scaling is enabled.")
        low=float(low_text); high=float(high_text)
        if not np.isfinite(low) or not np.isfinite(high): raise ValueError(f"{edge} limits must be finite.")
        if low>=high: raise ValueError(f"{edge} minimum must be less than maximum.")
        return (low,high)

    def refresh_states(*_):
        compare=mode.get()=="COMPARE"
        state="normal" if compare else "disabled"
        folder_b_entry.configure(state=state); name_b_entry.configure(state=state); folder_b_button.configure(state=state)
        for control in comparison_controls: control.configure(state=state)
        counts_check.configure(state="normal" if plot_mode.get()==PLOT_MODE_SUMMARY else "disabled")
        scale_state="normal" if use_manual_scales.get() else "disabled"
        for entry in scale_entries: entry.configure(state=scale_state)
        output_preview.set(
            f"Mode: {'Two-dataset comparison' if compare else 'Single dataset'}\n"
            f"Analysis method: {analysis_method.get()} | Waveform plots: {plot_mode.get()} | Frequency layout: {frequency_layout.get()}\n"
            f"Trend range display: {trend_range_mode.get()} | PowerPoint: Always generated when summary data exist"
        )
    mode.trace_add("write",refresh_states); analysis_method.trace_add("write",refresh_states); plot_mode.trace_add("write",refresh_states); use_manual_scales.trace_add("write",refresh_states)
    frequency_layout.trace_add("write",refresh_states); trend_range_mode.trace_add("write",refresh_states)

    def start_analysis():
        try:
            a=Path(folder_a.get().strip()).resolve()
            if not a.is_dir(): raise ValueError("Select an existing Configuration A CW folder.")
            if count_csv_files(a)==0: raise ValueError("Configuration A contains no CSV files.")
            b=None
            if mode.get()=="COMPARE":
                b=Path(folder_b.get().strip()).resolve()
                if not b.is_dir(): raise ValueError("Select an existing Configuration B CW folder.")
                if count_csv_files(b)==0: raise ValueError("Configuration B contains no CSV files.")
                if os.path.normcase(str(a))==os.path.normcase(str(b)): raise ValueError("Select two different folders.")
            scales={"RISE":parse_optional_pair(rise_min,rise_max,"RISE"),"FALL":parse_optional_pair(fall_min,fall_max,"FALL")}
            result.update({
                "accepted":True,"root":root,"mode":mode.get(),"folder_a":str(a),"folder_b":str(b) if b else None,
                "name_a":name_a.get().strip() or a.name or "Configuration A",
                "name_b":name_b.get().strip() or (b.name if b else "Configuration B"),
                "analysis_method":analysis_method.get(),
    "plot_mode":plot_mode.get(),"include_counts":bool(include_counts.get()),
                "frequency_layout":frequency_layout.get(),"trend_range_mode":trend_range_mode.get(),
                "comparison_plot_mode":comparison_plot_mode.get() if mode.get()=="COMPARE" else None,
                "manual_scales":scales,
            })
            # End the setup GUI event loop so main() can immediately create
            # and display the progress meter using this same Tk root. Merely
            # withdrawing the window leaves mainloop() running and prevents
            # configure_cw_analysis_with_gui() from returning.
            root.withdraw()
            root.update_idletasks()
            root.quit()
        except Exception as error:
            messagebox.showerror("Invalid CW analysis settings",str(error),parent=root)

    buttons=ttk.Frame(outer); buttons.pack(fill="x",pady=(12,0))
    ttk.Button(buttons,text="Cancel",command=root.destroy).pack(side="right",padx=(8,0))
    ttk.Button(buttons,text="Start Analysis",style="Start.TButton",command=start_analysis).pack(side="right")
    root.protocol("WM_DELETE_WINDOW",root.destroy)
    refresh_states(); root.mainloop()
    return result


def main():
    global RISE_TREND_Y_MIN_US, RISE_TREND_Y_MAX_US
    global FALL_TREND_Y_MIN_US, FALL_TREND_Y_MAX_US
    global MANUAL_VERTICAL_SCALES

    print("Script started.", flush=True)
    configuration = configure_cw_analysis_with_gui()
    if not configuration.get("accepted"):
        return

    root = configuration["root"]
    mode = configuration["mode"]
    folder_a = configuration["folder_a"]
    folder_b = configuration["folder_b"]
    name_a = configuration["name_a"]
    name_b = configuration["name_b"]
    analysis_method = configuration["analysis_method"]
    plot_mode = configuration["plot_mode"]
    include_counts = configuration["include_counts"]
    frequency_layout = configuration["frequency_layout"]
    comparison_plot_mode = configuration["comparison_plot_mode"]
    trend_range_mode = configuration["trend_range_mode"]
    MANUAL_VERTICAL_SCALES = configuration["manual_scales"]
    RISE_TREND_Y_MIN_US, RISE_TREND_Y_MAX_US = MANUAL_VERTICAL_SCALES["RISE"]
    FALL_TREND_Y_MIN_US, FALL_TREND_Y_MAX_US = MANUAL_VERTICAL_SCALES["FALL"]

    image_state_a = snapshot_png_files(folder_a)
    image_state_b = snapshot_png_files(folder_b) if mode == "COMPARE" else {}
    range_mode_descriptions = {
        TREND_RANGE_MODE_NONE: "no additional box or whiskers",
        TREND_RANGE_MODE_BOX: "IQR boxes only",
        TREND_RANGE_MODE_WHISKERS: "accepted min/max whiskers only",
        TREND_RANGE_MODE_BOX_AND_WHISKERS: "IQR boxes with accepted min/max whiskers",
    }
    print(f"Waveform analysis method: {analysis_method}", flush=True)
    print("Trend plot range display: " + range_mode_descriptions[trend_range_mode], flush=True)

    dataset_count = 2 if mode == "COMPARE" else 1
    input_count = count_csv_files(folder_a)
    if mode == "COMPARE": input_count += count_csv_files(folder_b)
    comparison_steps = 1 if mode == "COMPARE" else 0
    progress = ExecutionProgress(root, input_count + dataset_count + comparison_steps)

    data_a = process_cw_dataset(
        folder_a,
        name_a,
        plot_mode,
        include_counts,
        trend_range_mode,
        progress,
        frequency_layout,
    analysis_method,
    )
    data_b = None
    rise_compare = pd.DataFrame()
    fall_compare = pd.DataFrame()
    comparison_folder = None
    comparison_excel = None
    if mode == "COMPARE":
        data_b = process_cw_dataset(
            folder_b,
            name_b,
            plot_mode,
            include_counts,
            trend_range_mode,
            progress,
            frequency_layout,
        )
        # Regenerate both standalone FALL trend plots with one shared automatic
        # scale. The files overwrite the independently scaled versions created
        # during each dataset's initial processing.
        if frequency_layout == FREQUENCY_LAYOUT_COMBINED:
            shared_fall_scale_values = collect_shared_fall_trend_scale_values(
                data_a["fall_summary"],
                data_b["fall_summary"],
                trend_range_mode,
            )
            create_fall_time_trend_plot(
                data_a["fall_summary"],
                data_a["output"],
                trend_range_mode,
                shared_fall_scale_values,
            )
            create_fall_time_trend_plot(
                data_b["fall_summary"],
                data_b["output"],
                trend_range_mode,
                shared_fall_scale_values,
            )

        rise_compare = create_cw_comparison(
            data_a["rise_summary"], data_b["rise_summary"], "Rise", name_a, name_b)
        fall_compare = create_cw_comparison(
            data_a["fall_summary"], data_b["fall_summary"], "Fall", name_a, name_b)
        comparison_folder = os.path.join(folder_a, "CW Hardware Configuration Comparison")
        os.makedirs(comparison_folder, exist_ok=True)
        if comparison_plot_mode == COMPARISON_PLOT_MODE_BOX_WHISKER:
            create_cw_frequency_boxplot_comparisons(
                data_a["rise_df"], data_b["rise_df"], "Rise", name_a, name_b, comparison_folder)
            create_cw_frequency_boxplot_comparisons(
                data_a["fall_df"], data_b["fall_df"], "Fall", name_a, name_b, comparison_folder)
        elif comparison_plot_mode == COMPARISON_PLOT_MODE_ORIGINAL:
            create_cw_comparison_plot(
                rise_compare, "Rise", name_a, name_b, comparison_folder, frequency_layout)
            create_cw_comparison_plot(
                fall_compare, "Fall", name_a, name_b, comparison_folder, frequency_layout)
        else:
            raise ValueError(f"Unsupported comparison plot mode: {comparison_plot_mode}")
        comparison_excel = os.path.join(comparison_folder, "CW_Hardware_Comparison.xlsx")
        with pd.ExcelWriter(comparison_excel, engine="openpyxl") as writer:
            if not rise_compare.empty: rise_compare.to_excel(writer, sheet_name="RISE Comparison", index=False)
            if not fall_compare.empty: fall_compare.to_excel(writer, sheet_name="FALL Comparison", index=False)
            ranking = pd.concat([rise_compare, fall_compare], ignore_index=True)
            if not ranking.empty:
                ranking.sort_values(["Absolute Percent Difference (%)", "Absolute Difference (us)"],
                                    ascending=False, na_position="last").to_excel(
                    writer, sheet_name="Non-Transparency Ranking", index=False)
            pd.DataFrame({"Item": ["Configuration A", "Configuration A Folder",
                                   "Configuration B", "Configuration B Folder",
                                   "Difference Convention", "RISE Vertical Limits (us)",
                                   "FALL Vertical Limits (us)",
                                   "Trend Range Display",
                                   "Comparison Chart Style",
                                   "Frequency Curve Layout"],
                          "Value": [name_a, folder_a, name_b, folder_b,
                                    "Configuration B minus Configuration A",
                                    str(MANUAL_VERTICAL_SCALES["RISE"]),
                                    str(MANUAL_VERTICAL_SCALES["FALL"]),
                                    range_mode_descriptions[trend_range_mode],
                                    {COMPARISON_PLOT_MODE_BOX_WHISKER: "Frequency-specific box-and-whisker charts",
                                     COMPARISON_PLOT_MODE_ORIGINAL: "Original average-line and B-minus-A charts"}[
                                        comparison_plot_mode],
                                    {FREQUENCY_LAYOUT_COMBINED: "All frequency curves on one graph",
                                     FREQUENCY_LAYOUT_SEPARATE: "Separate graph per frequency"}[
                                        frequency_layout]]}).to_excel(
                writer, sheet_name="Comparison Settings", index=False)
        auto_size_excel(comparison_excel)
        progress.advance("All comparison outputs complete")
    changed_a = changed_png_files(image_state_a, folder_a)
    if comparison_folder:
        comparison_root = Path(comparison_folder).resolve()
        comparison_images = [x for x in changed_a if comparison_root in x.resolve().parents]
        dataset_a_images = [x for x in changed_a if comparison_root not in x.resolve().parents]
    else:
        comparison_images, dataset_a_images = [], changed_a
    generated_images = [("dataset_a", x) for x in dataset_a_images]
    if mode == "COMPARE":
        generated_images.extend(("dataset_b", x) for x in changed_png_files(image_state_b, folder_b))
        generated_images.extend(("comparison", x) for x in comparison_images)
    package_warning = None
    if powerpoint_export_requested():
        try:
            manifest_path = export_powerpoint_package(configuration, generated_images, data_a, data_b,
                                                       rise_compare, fall_compare, comparison_excel)
            if manifest_path: print(f"PowerPoint package manifest saved to: {manifest_path}")
        except Exception as error:
            package_warning = f"PowerPoint package export warning: {error}"
            print(package_warning, file=sys.stderr)
    progress.finish()
    print(f"\nConfiguration A Excel: {data_a['excel']}")
    if comparison_excel: print(f"Comparison Excel: {comparison_excel}")
    open_file(data_a["excel"])
    progress.close()
    messagebox.showinfo(
        "Script complete",
        "CW processing is complete.\n\n"
        f"Configuration A workbook:\n{data_a['excel']}" +
        (f"\n\nComparison workbook:\n{comparison_excel}" if comparison_excel else "") +
        (f"\n\n{package_warning}\nNormal analysis outputs were still completed." if package_warning else ""),
        parent=root)
    root.destroy();
    print("Script finished.")


if __name__ == "__main__":
    main()
