"""Standalone DC Operating Point analysis and comparison application.

Consumes CSV files produced by PMNT_DC_Operating_Point_Collection_GUI_AnalysisReady.py.
No instrument connections or control commands are used by this application.
"""

import json
import os
import shutil
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


APP_TITLE = "Paramount DC Operating Point Data Analysis"
DEFAULT_WORKBOOK_NAME = "DC_Operating_Point_Analysis.xlsx"
DEFAULT_PLOT_FOLDER_NAME = "DC Operating Point Analysis Plots"
COMPARISON_WORKBOOK_NAME = "DC_Operating_Point_Hardware_Comparison.xlsx"
COMPARISON_FOLDER_NAME = "Hardware Configuration Comparison"

LEGACY_REQUIRED_COLUMNS = [
    "freq_Hz",
    "power_setpoint_W",
    "DCVoltage_V",
    "DCCurrent_A",
    "pDelivered_W",
    "pDissipation_W",
    "stabilize_time_s",
]

ANALYSIS_READY_COLUMNS = [
    "dc_voltage_adc_counts",
    "dc_current_adc_counts",
    "dc_voltage_full_scale_V",
    "dc_current_full_scale_A",
    "adc_full_scale_counts",
    "dc_input_power_W",
    "lynx_forward_power_W",
    "lynx_reflected_power_W",
    "lynx_delivered_power_W",
    "calculated_power_loss_W",
    "calculated_efficiency_percent",
    "stabilization_timed_out",
    "measurement_status",
]

PLOT_DEFINITIONS = {
    "dc_voltage": ("DC Voltage", "DCVoltage_V", "DC Voltage (V)"),
    "dc_current": ("DC Current", "DCCurrent_A", "DC Current (A)"),
    "dc_input_power": ("DC Input Power", "analysis_dc_input_power_W", "DC Input Power (W)"),
    "delivered_power": ("Delivered RF Power", "analysis_delivered_power_W", "Delivered Power (W)"),
    "dissipation": ("DUT-Reported Dissipation", "pDissipation_W", "Power Dissipation (W)"),
    "calculated_loss": ("Calculated Power Loss", "analysis_calculated_power_loss_W", "Power Loss (W)"),
    "efficiency": ("Conversion Efficiency", "analysis_efficiency_percent", "Efficiency (%)"),
    "power_error": ("Delivered Power Error", "analysis_power_error_percent", "Error vs Setpoint (%)"),
    "stabilization": ("Stabilization Time", "stabilize_time_s", "Stabilization Time (s)"),
    "dissipation_difference": (
        "Dissipation Cross-Check Difference",
        "analysis_dissipation_difference_W",
        "DUT Dissipation - Calculated Loss (W)",
    ),
}

SUMMARY_GROUP_COLUMNS = ["freq_Hz", "power_setpoint_W"]
SUMMARY_METRICS = [
    "DCVoltage_V",
    "DCCurrent_A",
    "analysis_dc_input_power_W",
    "analysis_delivered_power_W",
    "pDissipation_W",
    "analysis_calculated_power_loss_W",
    "analysis_efficiency_percent",
    "analysis_power_error_percent",
    "stabilize_time_s",
    "analysis_dissipation_difference_W",
]


class AnalysisProgress:
    """Determinate progress window for DC Operating Point analysis stages."""
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("DC Operating Point Analysis Progress")
        self.root.geometry("720x235")
        self.root.resizable(True, False)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        self.heading = tk.StringVar(value="Preparing analysis")
        self.detail = tk.StringVar(value="")
        self.percent = tk.StringVar(value="0%")
        ttk.Label(frame, textvariable=self.heading, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.detail, wraplength=675).pack(anchor="w", pady=(8, 12))
        self.bar = ttk.Progressbar(frame, maximum=100, mode="determinate")
        self.bar.pack(fill="x")
        ttk.Label(frame, textvariable=self.percent).pack(anchor="e", pady=(6, 0))
        ttk.Label(
            frame,
            text="The progress window closes automatically when analysis finishes.",
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 0))
        self.root.update_idletasks()
        self.root.update()

    def update(self, value, heading=None, detail=None):
        value = max(0.0, min(100.0, float(value)))
        self.bar["value"] = value
        self.percent.set(f"{value:.0f}%")
        if heading is not None:
            self.heading.set(str(heading))
        if detail is not None:
            self.detail.set(str(detail))
        self.root.update_idletasks()
        self.root.update()

    def close(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def safe_sheet_name(name):
    invalid = '[]:*?/\\'
    cleaned = "".join("_" if character in invalid else character for character in str(name))
    return cleaned[:31] or "Sheet"


def auto_size_workbook(writer):
    for worksheet in writer.book.worksheets:
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(length + 2, 10), 50)


def collect_csv_files(path):
    selected = Path(path)
    settings_filename = "DC_Operating_Point_Run_Settings.csv"
    if selected.is_file():
        return (
            [selected]
            if selected.suffix.lower() == ".csv" and selected.name != settings_filename
            else []
        )
    if selected.is_dir():
        return sorted(
            csv_path
            for csv_path in selected.glob("*.csv")
            if csv_path.name != settings_filename
        )
    return []


def read_dataset(path, dataset_name):
    files = collect_csv_files(path)
    if not files:
        raise ValueError(f"{dataset_name} contains no CSV files.")

    frames = []
    failures = []
    for csv_path in files:
        try:
            frame = pd.read_csv(csv_path)
            missing = [column for column in LEGACY_REQUIRED_COLUMNS if column not in frame.columns]
            if missing:
                raise ValueError("Missing required columns: " + ", ".join(missing))
            frame.insert(0, "Source CSV", csv_path.name)
            frame.insert(0, "Dataset", dataset_name)
            frames.append(frame)
        except Exception as error:
            failures.append({"Dataset": dataset_name, "CSV Path": str(csv_path), "Error": str(error)})

    if not frames:
        raise ValueError(f"No readable DC Operating Point CSV files were found for {dataset_name}.")
    combined = pd.concat(frames, ignore_index=True, sort=False)
    return combined, pd.DataFrame(failures), files


def bool_series(series):
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def numeric_series(frame, column, default=np.nan):
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def prepare_analysis_data(frame, recalculate_from_raw, exclude_timeout_rows, valid_only):
    data = frame.copy()
    numeric_candidates = set(LEGACY_REQUIRED_COLUMNS + ANALYSIS_READY_COLUMNS + [
        "freq_kHz", "generator_command_setpoint", "lynx_resistance_ohm",
        "lynx_reactance_ohm", "lynx_voltage_V", "lynx_current_A",
        "dissipation_difference_W", "stabilization_sample_count",
        "previous_stabilization_power_W", "final_stabilization_power_W",
        "final_relative_change_percent", "measurement_valid",
    ])
    for column in numeric_candidates:
        if column in data.columns and column not in {"stabilization_timed_out", "measurement_valid"}:
            data[column] = pd.to_numeric(data[column], errors="coerce")

    data["analysis_row_included"] = True
    data["analysis_exclusion_reason"] = ""

    if valid_only and "measurement_valid" in data.columns:
        explicit_validity = data["measurement_valid"].notna()
        valid_mask = bool_series(data["measurement_valid"])
        invalid_mask = explicit_validity & ~valid_mask
        data.loc[invalid_mask, "analysis_row_included"] = False
        data.loc[invalid_mask, "analysis_exclusion_reason"] = "Measurement marked invalid"

    if exclude_timeout_rows:
        timeout_mask = pd.Series(False, index=data.index)
        if "stabilization_timed_out" in data.columns:
            timeout_mask |= bool_series(data["stabilization_timed_out"])
        if "measurement_status" in data.columns:
            timeout_mask |= data["measurement_status"].astype(str).str.contains("timeout", case=False, na=False)
        data.loc[timeout_mask, "analysis_row_included"] = False
        existing = data.loc[timeout_mask, "analysis_exclusion_reason"].astype(str)
        data.loc[timeout_mask, "analysis_exclusion_reason"] = np.where(
            existing.str.len() > 0, existing + "; Stabilization timeout", "Stabilization timeout"
        )

    voltage_recorded = numeric_series(data, "DCVoltage_V")
    current_recorded = numeric_series(data, "DCCurrent_A")
    voltage_raw = numeric_series(data, "dc_voltage_adc_counts")
    current_raw = numeric_series(data, "dc_current_adc_counts")
    voltage_scale = numeric_series(data, "dc_voltage_full_scale_V", 201.4).fillna(201.4)
    current_scale = numeric_series(data, "dc_current_full_scale_A", 40.8).fillna(40.8)
    adc_counts = numeric_series(data, "adc_full_scale_counts", 4096.0).replace(0, np.nan).fillna(4096.0)

    voltage_recalculated = voltage_raw * voltage_scale / adc_counts
    current_recalculated = current_raw * current_scale / adc_counts
    use_raw = recalculate_from_raw & voltage_recalculated.notna() & current_recalculated.notna()

    data["analysis_dc_voltage_V"] = np.where(use_raw, voltage_recalculated, voltage_recorded)
    data["analysis_dc_current_A"] = np.where(use_raw, current_recalculated, current_recorded)
    data["analysis_dc_input_power_W"] = data["analysis_dc_voltage_V"] * data["analysis_dc_current_A"]

    delivered = numeric_series(data, "lynx_delivered_power_W")
    delivered = delivered.where(delivered.notna(), numeric_series(data, "pDelivered_W"))
    data["analysis_delivered_power_W"] = delivered
    data["analysis_calculated_power_loss_W"] = data["analysis_dc_input_power_W"] - delivered
    data["analysis_efficiency_percent"] = np.where(
        data["analysis_dc_input_power_W"] != 0,
        delivered / data["analysis_dc_input_power_W"] * 100.0,
        np.nan,
    )
    setpoint = numeric_series(data, "power_setpoint_W")
    data["analysis_power_error_W"] = delivered - setpoint
    data["analysis_power_error_percent"] = np.where(
        setpoint != 0, data["analysis_power_error_W"] / setpoint * 100.0, np.nan
    )
    data["analysis_dissipation_difference_W"] = (
        numeric_series(data, "pDissipation_W") - data["analysis_calculated_power_loss_W"]
    )
    data["analysis_recalculated_from_raw"] = use_raw
    data["analysis_schema"] = np.where(
        data[[column for column in ANALYSIS_READY_COLUMNS if column in data.columns]].notna().any(axis=1)
        if any(column in data.columns for column in ANALYSIS_READY_COLUMNS)
        else False,
        "Analysis-ready",
        "Legacy",
    )
    return data


def create_summary(data):
    included = data[data["analysis_row_included"]].copy()
    if included.empty:
        return pd.DataFrame()
    grouped = included.groupby(SUMMARY_GROUP_COLUMNS, dropna=False)
    rows = []
    for keys, group in grouped:
        row = {
            "freq_Hz": keys[0],
            "freq_kHz": keys[0] / 1000.0,
            "power_setpoint_W": keys[1],
            "Included Row Count": len(group),
            "Timeout Row Count": int(
                group.get("measurement_status", pd.Series("", index=group.index))
                .astype(str).str.contains("timeout", case=False, na=False).sum()
            ),
        }
        for metric in SUMMARY_METRICS:
            values = pd.to_numeric(group.get(metric), errors="coerce").dropna()
            title = metric.removeprefix("analysis_")
            row[f"{title} Average"] = values.mean() if not values.empty else np.nan
            row[f"{title} Minimum"] = values.min() if not values.empty else np.nan
            row[f"{title} Maximum"] = values.max() if not values.empty else np.nan
            row[f"{title} Std Dev"] = values.std(ddof=1) if len(values) > 1 else 0.0 if len(values) == 1 else np.nan
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["freq_Hz", "power_setpoint_W"]).reset_index(drop=True)


def create_frequency_summary(data):
    included = data[data["analysis_row_included"]].copy()
    if included.empty:
        return pd.DataFrame()
    rows = []
    for frequency, group in included.groupby("freq_Hz", dropna=False):
        row = {"freq_Hz": frequency, "freq_kHz": frequency / 1000.0, "Row Count": len(group)}
        for metric in SUMMARY_METRICS:
            values = pd.to_numeric(group.get(metric), errors="coerce").dropna()
            title = metric.removeprefix("analysis_")
            row[f"{title} Overall Average"] = values.mean() if not values.empty else np.nan
            row[f"{title} Overall Minimum"] = values.min() if not values.empty else np.nan
            row[f"{title} Overall Maximum"] = values.max() if not values.empty else np.nan
        rows.append(row)
    return pd.DataFrame(rows).sort_values("freq_Hz").reset_index(drop=True)


def create_plots(data, output_folder, selected_plots, dataset_name):
    included = data[data["analysis_row_included"]].copy()
    if included.empty:
        return []
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for plot_key in selected_plots:
        title, metric, ylabel = PLOT_DEFINITIONS[plot_key]
        if metric not in included.columns:
            continue
        figure, axis = plt.subplots(figsize=(10, 6))
        plotted = False
        for frequency, group in included.groupby("freq_Hz", dropna=False):
            group = group.sort_values("power_setpoint_W")
            x = pd.to_numeric(group["power_setpoint_W"], errors="coerce")
            y = pd.to_numeric(group[metric], errors="coerce")
            mask = x.notna() & y.notna()
            if mask.any():
                axis.plot(x[mask], y[mask], marker="o", linewidth=2, label=f"{frequency / 1000:g} kHz")
                plotted = True
        if not plotted:
            plt.close(figure)
            continue
        axis.set_title(f"{dataset_name} - {title}")
        axis.set_xlabel("Power Setpoint (W)")
        axis.set_ylabel(ylabel)
        axis.grid(True, alpha=0.3)
        axis.legend(title="RF Frequency")
        figure.tight_layout()
        path = output_folder / f"{plot_key}.png"
        figure.savefig(path, dpi=250, bbox_inches="tight")
        plt.close(figure)
        paths.append(path)
    return paths


def create_comparison(summary_a, summary_b, name_a, name_b):
    keys = ["freq_Hz", "freq_kHz", "power_setpoint_W"]
    metric_bases = [
        "dc_voltage_V Average", "dc_current_A Average", "dc_input_power_W Average",
        "delivered_power_W Average", "pDissipation_W Average",
        "calculated_power_loss_W Average", "efficiency_percent Average",
        "power_error_percent Average", "stabilize_time_s Average",
        "dissipation_difference_W Average",
    ]
    available = [metric for metric in metric_bases if metric in summary_a.columns or metric in summary_b.columns]
    a_columns = keys + [metric for metric in available if metric in summary_a.columns]
    b_columns = keys + [metric for metric in available if metric in summary_b.columns]
    a = summary_a[a_columns].rename(columns={metric: f"{name_a} {metric}" for metric in available if metric in summary_a.columns})
    b = summary_b[b_columns].rename(columns={metric: f"{name_b} {metric}" for metric in available if metric in summary_b.columns})
    merged = a.merge(b, on=keys, how="outer", indicator=True)
    merged["Matched Condition"] = merged["_merge"].eq("both")
    merged["Availability"] = merged["_merge"].map({
        "both": "Matched", "left_only": f"Only {name_a}", "right_only": f"Only {name_b}"
    })
    for metric in available:
        column_a = f"{name_a} {metric}"
        column_b = f"{name_b} {metric}"
        if column_a not in merged.columns:
            merged[column_a] = np.nan
        if column_b not in merged.columns:
            merged[column_b] = np.nan
        label = metric.replace(" Average", "")
        difference = merged[column_b] - merged[column_a]
        merged[f"{label} Difference B-A"] = difference
        merged[f"{label} Absolute Difference"] = difference.abs()
        merged[f"{label} Percent Difference vs A"] = np.where(
            merged[column_a].notna() & (merged[column_a] != 0),
            100.0 * difference / merged[column_a],
            np.nan,
        )
    return merged.drop(columns=["_merge"]).sort_values(keys).reset_index(drop=True)


def create_comparison_plots(comparison, name_a, name_b, output_folder):
    if comparison.empty:
        return []
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    mappings = {
        "analysis_efficiency_percent": "efficiency_percent Average",
        "analysis_power_error_percent": "power_error_percent Average",
        "analysis_dc_input_power_W": "dc_input_power_W Average",
        "analysis_delivered_power_W": "delivered_power_W Average",
        "pDissipation_W": "pDissipation_W Average",
        "stabilize_time_s": "stabilize_time_s Average",
    }
    paths = []
    matched = comparison[comparison["Matched Condition"]].copy()
    for plot_key, metric_label in mappings.items():
        column_a = f"{name_a} {metric_label}"
        column_b = f"{name_b} {metric_label}"
        difference_column = f"{metric_label.replace(' Average', '')} Difference B-A"
        if column_a not in matched or column_b not in matched:
            continue
        for frequency, group in matched.groupby("freq_Hz", dropna=False):
            group = group.sort_values("power_setpoint_W")
            if group.empty:
                continue
            figure, (top, bottom) = plt.subplots(
                2, 1, figsize=(10, 8), sharex=True, gridspec_kw={"height_ratios": [2.2, 1.0]}
            )
            x = group["power_setpoint_W"]
            top.plot(x, group[column_a], marker="o", linewidth=2, label=name_a)
            top.plot(x, group[column_b], marker="s", linestyle="--", linewidth=2, label=name_b)
            bottom.plot(x, group[difference_column], marker="o", color="#C00000")
            bottom.axhline(0, color="black", linewidth=1)
            top.set_ylabel(metric_label.replace(" Average", ""))
            bottom.set_ylabel("B - A")
            bottom.set_xlabel("Power Setpoint (W)")
            top.grid(True, alpha=0.3); bottom.grid(True, alpha=0.3); top.legend()
            figure.suptitle(f"DC Operating Point Comparison - {frequency / 1000:g} kHz")
            figure.tight_layout()
            path = output_folder / f"comparison_{plot_key}_{frequency / 1000:g}kHz.png"
            figure.savefig(path, dpi=250, bbox_inches="tight")
            plt.close(figure)
            paths.append(path)
    return paths


def write_analysis_workbook(path, data, summary, frequency_summary, failures, settings):
    excluded = data[~data["analysis_row_included"]].copy()
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        data.to_excel(writer, sheet_name="Analyzed Data", index=False)
        if not summary.empty:
            summary.to_excel(writer, sheet_name="Condition Summary", index=False)
        if not frequency_summary.empty:
            frequency_summary.to_excel(writer, sheet_name="Frequency Summary", index=False)
        if not excluded.empty:
            excluded.to_excel(writer, sheet_name="Excluded Rows", index=False)
        if not failures.empty:
            failures.to_excel(writer, sheet_name="Failed Files", index=False)
        pd.DataFrame({"Setting": list(settings.keys()), "Value": list(settings.values())}).to_excel(
            writer, sheet_name="Analysis Settings", index=False
        )
        auto_size_workbook(writer)


def write_comparison_workbook(path, comparison, summary_a, summary_b, failures_a, failures_b, settings):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        comparison.to_excel(writer, sheet_name="Condition Comparison", index=False)
        if not summary_a.empty:
            summary_a.to_excel(writer, sheet_name="A Condition Summary", index=False)
        if not summary_b.empty:
            summary_b.to_excel(writer, sheet_name="B Condition Summary", index=False)
        ranking_columns = [column for column in comparison.columns if "Absolute Difference" in column]
        if ranking_columns:
            ranking = comparison.copy()
            ranking["Maximum Normalized Difference"] = ranking[ranking_columns].abs().max(axis=1)
            ranking.sort_values("Maximum Normalized Difference", ascending=False, na_position="last").to_excel(
                writer, sheet_name="Difference Ranking", index=False
            )
        failures = pd.concat([failures_a, failures_b], ignore_index=True)
        if not failures.empty:
            failures.to_excel(writer, sheet_name="Failed Files", index=False)
        pd.DataFrame({"Setting": list(settings.keys()), "Value": list(settings.values())}).to_excel(
            writer, sheet_name="Comparison Settings", index=False
        )
        auto_size_workbook(writer)


def configure_analysis_gui():
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1040x790")
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
    ttk.Label(outer, text=APP_TITLE, style="Header.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "Analyze one DC Operating Point dataset or compare two hardware configurations. "
            "The application supports both the expanded analysis-ready schema and the legacy seven-column schema."
        ),
        wraplength=980,
    ).pack(anchor="w", pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    input_tab = ttk.Frame(notebook, padding=12)
    calculations_tab = ttk.Frame(notebook, padding=12)
    plots_tab = ttk.Frame(notebook, padding=12)
    comparison_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(input_tab, text="Input Data")
    notebook.add(calculations_tab, text="Calculations")
    notebook.add(plots_tab, text="Plots")
    notebook.add(comparison_tab, text="Comparison")
    notebook.add(output_tab, text="Output")

    launcher_mode = os.environ.get("PARAMOUNT_ANALYSIS_MODE", "INDIVIDUAL").strip().upper()
    initial_mode = launcher_mode if launcher_mode in {"SINGLE", "COMPARE"} else "SINGLE"
    initial_path_a = os.environ.get("PARAMOUNT_DATASET_A_FOLDER", "").strip()
    initial_path_b = os.environ.get("PARAMOUNT_DATASET_B_FOLDER", "").strip()
    initial_name_a = os.environ.get("PARAMOUNT_DATASET_A_LABEL", "").strip() or "Configuration A"
    initial_name_b = os.environ.get("PARAMOUNT_DATASET_B_LABEL", "").strip() or "Configuration B"
    mode = tk.StringVar(value=initial_mode)
    path_a = tk.StringVar(value=initial_path_a); path_b = tk.StringVar(value=initial_path_b)
    name_a = tk.StringVar(value=initial_name_a); name_b = tk.StringVar(value=initial_name_b)
    recalculate = tk.BooleanVar(value=True)
    exclude_timeouts = tk.BooleanVar(value=True)
    valid_only = tk.BooleanVar(value=True)
    open_workbook = tk.BooleanVar(value=True)
    workbook_name = tk.StringVar(value=DEFAULT_WORKBOOK_NAME)
    plot_folder_name = tk.StringVar(value=DEFAULT_PLOT_FOLDER_NAME)
    preview_text = tk.StringVar(value="Select a collection folder or CSV file.")
    output_preview = tk.StringVar()
    plot_vars = {key: tk.BooleanVar(value=True) for key in PLOT_DEFINITIONS}

    input_tab.columnconfigure(1, weight=1)
    ttk.Label(input_tab, text="Analysis mode", style="Section.TLabel").grid(row=0, column=0, sticky="w")
    mode_frame = ttk.Frame(input_tab); mode_frame.grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 12))
    ttk.Radiobutton(mode_frame, text="Single dataset", variable=mode, value="SINGLE").pack(side="left", padx=(0, 20))
    ttk.Radiobutton(mode_frame, text="Compare two hardware configurations", variable=mode, value="COMPARE").pack(side="left")

    ttk.Label(input_tab, text="Configuration A folder or CSV").grid(row=2, column=0, sticky="w", pady=5)
    ttk.Entry(input_tab, textvariable=path_a, width=65).grid(row=2, column=1, sticky="ew", pady=5)
    ttk.Label(input_tab, text="Display name").grid(row=3, column=0, sticky="w", pady=5)
    ttk.Entry(input_tab, textvariable=name_a, width=35).grid(row=3, column=1, sticky="w", pady=5)
    ttk.Label(input_tab, text="Configuration B folder or CSV").grid(row=4, column=0, sticky="w", pady=5)
    entry_b = ttk.Entry(input_tab, textvariable=path_b, width=65); entry_b.grid(row=4, column=1, sticky="ew", pady=5)
    ttk.Label(input_tab, text="Display name").grid(row=5, column=0, sticky="w", pady=5)
    name_b_entry = ttk.Entry(input_tab, textvariable=name_b, width=35); name_b_entry.grid(row=5, column=1, sticky="w", pady=5)

    def select_path(variable, label_variable, preview=False):
        current = variable.get().strip()
        selected = filedialog.askdirectory(
            title="Select DC Operating Point collection folder",
            parent=root,
            mustexist=True,
            initialdir=current if current and Path(current).is_dir() else None,
        )
        if selected:
            variable.set(selected)
            label_variable.set(Path(selected).name or label_variable.get())
            if preview:
                files = collect_csv_files(selected)
                schemas = []
                rows = 0
                frequencies = set(); setpoints = set()
                for file_path in files:
                    try:
                        frame = pd.read_csv(file_path)
                        rows += len(frame)
                        schemas.append("Analysis-ready" if all(c in frame for c in ANALYSIS_READY_COLUMNS) else "Legacy")
                        frequencies.update(pd.to_numeric(frame.get("freq_Hz"), errors="coerce").dropna())
                        setpoints.update(pd.to_numeric(frame.get("power_setpoint_W"), errors="coerce").dropna())
                    except Exception:
                        pass
                preview_text.set(
                    f"CSV files: {len(files)} | Rows: {rows} | Schemas: {', '.join(sorted(set(schemas))) or 'Unknown'}\n"
                    f"RF frequencies (kHz): {', '.join(f'{v / 1000:g}' for v in sorted(frequencies)) or 'None'}\n"
                    f"Power setpoints (W): {', '.join(f'{v:g}' for v in sorted(setpoints)) or 'None'}"
                )

    ttk.Button(input_tab, text="Browse...", command=lambda: select_path(path_a, name_a, True)).grid(row=2, column=2, padx=(10, 0))
    button_b = ttk.Button(input_tab, text="Browse...", command=lambda: select_path(path_b, name_b, False)); button_b.grid(row=4, column=2, padx=(10, 0))
    ttk.Label(input_tab, textvariable=preview_text, wraplength=900, foreground="#444444", justify="left").grid(
        row=6, column=0, columnspan=3, sticky="w", pady=(16, 0)
    )

    ttk.Label(calculations_tab, text="Row selection and recalculation", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
    ttk.Checkbutton(calculations_tab, text="Recalculate DC voltage/current from raw ADC counts when available", variable=recalculate).pack(anchor="w", pady=5)
    ttk.Checkbutton(calculations_tab, text="Exclude stabilization-timeout rows from summaries and plots", variable=exclude_timeouts).pack(anchor="w", pady=5)
    ttk.Checkbutton(calculations_tab, text="Exclude rows marked measurement_valid = False", variable=valid_only).pack(anchor="w", pady=5)
    ttk.Label(
        calculations_tab,
        text=(
            "Legacy files remain supported using their recorded scaled values. Analysis-ready files retain both recorded "
            "and recalculated engineering values for traceability."
        ),
        wraplength=850, foreground="#555555",
    ).pack(anchor="w", pady=(14, 0))

    ttk.Label(plots_tab, text="Select generated plots", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
    for index, (key, definition) in enumerate(PLOT_DEFINITIONS.items(), start=1):
        ttk.Checkbutton(plots_tab, text=definition[0], variable=plot_vars[key]).grid(
            row=(index + 1) // 2, column=(index - 1) % 2, sticky="w", padx=(0, 40), pady=5
        )

    ttk.Label(comparison_tab, text="DC Operating Point comparison", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
    ttk.Label(
        comparison_tab,
        text=(
            "Matched conditions use RF frequency and requested power setpoint. The comparison workbook includes "
            "A and B averages, signed B-minus-A differences, absolute differences, percent differences versus A, "
            "availability, difference ranking, and overlaid frequency-specific plots with a B-minus-A panel."
        ),
        wraplength=860, justify="left",
    ).pack(anchor="w")

    output_tab.columnconfigure(1, weight=1)
    ttk.Label(output_tab, text="Analysis workbook filename").grid(row=0, column=0, sticky="w", pady=5)
    ttk.Entry(output_tab, textvariable=workbook_name, width=45).grid(row=0, column=1, sticky="w", pady=5)
    ttk.Label(output_tab, text="Plot folder name").grid(row=1, column=0, sticky="w", pady=5)
    ttk.Entry(output_tab, textvariable=plot_folder_name, width=45).grid(row=1, column=1, sticky="w", pady=5)
    ttk.Checkbutton(output_tab, text="Open Configuration A workbook when complete", variable=open_workbook).grid(
        row=2, column=0, columnspan=2, sticky="w", pady=(10, 4)
    )
    ttk.Label(output_tab, textvariable=output_preview, wraplength=850, justify="left", foreground="#333333").grid(
        row=3, column=0, columnspan=3, sticky="w", pady=(16, 0)
    )

    result = {"accepted": False}

    def refresh(*_):
        compare = mode.get() == "COMPARE"
        state = "normal" if compare else "disabled"
        entry_b.configure(state=state); name_b_entry.configure(state=state); button_b.configure(state=state)
        output_preview.set(
            f"Mode: {'Two-dataset comparison' if compare else 'Single dataset'}\n"
            f"Configuration A outputs are saved beside its selected CSV/folder.\n"
            f"Workbook: {workbook_name.get()} | Plot folder: {plot_folder_name.get()}\n"
            f"Comparison workbook: {COMPARISON_WORKBOOK_NAME if compare else 'Not generated'}"
        )
    mode.trace_add("write", refresh); workbook_name.trace_add("write", refresh); plot_folder_name.trace_add("write", refresh)
    refresh()

    def validate_output_name(name, suffix=None):
        invalid = '<>:"/\\|?*'
        if not name or any(character in name for character in invalid):
            raise ValueError("Output names cannot be blank or contain Windows-invalid characters.")
        if suffix and not name.lower().endswith(suffix):
            raise ValueError(f"Workbook filename must end in {suffix}.")

    def start_analysis():
        try:
            a = Path(path_a.get().strip()).resolve()
            if not collect_csv_files(a):
                raise ValueError("Select a Configuration A folder containing CSV files.")
            b = None
            if mode.get() == "COMPARE":
                b = Path(path_b.get().strip()).resolve()
                if not collect_csv_files(b):
                    raise ValueError("Select a Configuration B folder containing CSV files.")
                if os.path.normcase(str(a)) == os.path.normcase(str(b)):
                    raise ValueError("Select two different datasets.")
            validate_output_name(workbook_name.get().strip(), ".xlsx")
            validate_output_name(plot_folder_name.get().strip())
            selected_plots = [key for key, variable in plot_vars.items() if variable.get()]
            result.update({
                "accepted": True,
                "root": root,
                "mode": mode.get(),
                "path_a": str(a),
                "path_b": str(b) if b else None,
                "name_a": name_a.get().strip() or a.name or "Configuration A",
                "name_b": name_b.get().strip() or (b.name if b else "Configuration B"),
                "recalculate": bool(recalculate.get()),
                "exclude_timeouts": bool(exclude_timeouts.get()),
                "valid_only": bool(valid_only.get()),
                "selected_plots": selected_plots,
                "workbook_name": workbook_name.get().strip(),
                "plot_folder_name": plot_folder_name.get().strip(),
                "open_workbook": bool(open_workbook.get()),
            })
            root.destroy()
        except Exception as error:
            messagebox.showerror("Invalid DC Operating Point analysis settings", str(error), parent=root)

    buttons = ttk.Frame(outer); buttons.pack(fill="x", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text="Start Analysis", style="Start.TButton", command=start_analysis).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    refresh(); root.mainloop()
    return result


def dataset_output_directory(selected_path):
    path = Path(selected_path)
    return path if path.is_dir() else path.parent


def powerpoint_export_requested():
    return os.environ.get("PARAMOUNT_POWERPOINT_EXPORT_ENABLED", "").strip() == "1"


def export_powerpoint_package(configuration, generated_images, summary_a, summary_b=None,
                              comparison=None, workbook_a=None, workbook_b=None,
                              comparison_workbook=None):
    if not powerpoint_export_requested():
        return None
    package_text = os.environ.get("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER", "").strip()
    analysis_id = os.environ.get("PARAMOUNT_POWERPOINT_ANALYSIS_ID", "dc_operating_point").strip()
    schema_text = os.environ.get("PARAMOUNT_POWERPOINT_SCHEMA_VERSION", "1").strip()
    if not package_text:
        raise ValueError("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER is not set.")
    package_folder = Path(package_text).expanduser().resolve()
    package_folder.mkdir(parents=True, exist_ok=True)
    manifest_path = package_folder / "manifest.json"
    if manifest_path.exists(): manifest_path.unlink()
    images_root = package_folder / "images"
    tables_root = package_folder / "tables"
    if images_root.exists(): shutil.rmtree(images_root)
    if tables_root.exists(): shutil.rmtree(tables_root)
    tables_root.mkdir(parents=True, exist_ok=True)
    summary_a.to_csv(tables_root / "dataset_a_summary.csv", index=False)
    if summary_b is not None: summary_b.to_csv(tables_root / "dataset_b_summary.csv", index=False)
    if comparison is not None: comparison.to_csv(tables_root / "comparison_b_minus_a.csv", index=False)
    slides=[]; copied_images=[]
    for role, source_path in generated_images:
        source=Path(source_path)
        if not source.is_file(): continue
        destination_folder=images_root/role; destination_folder.mkdir(parents=True, exist_ok=True)
        destination=destination_folder/source.name; shutil.copy2(source,destination)
        relative=destination.relative_to(package_folder).as_posix()
        copied_images.append({"dataset_role":role.upper(),"source":str(source),"package_file":relative})
        slide={"slide_type":"image","title":source.name,"image_file":relative,"dataset_role":role.upper()}
        if role=="comparison": slide["difference_convention"]="B_MINUS_A"
        slides.append(slide)
    metrics={"Analysis mode":configuration["mode"],"Dataset A":configuration["name_a"],
             "Dataset A summary rows":len(summary_a),"Generated image slides":len(slides)}
    highlights=[f"Dataset A: {configuration['name_a']}",f"Generated {len(slides)} image slide(s) from this analysis run."]
    dataset_b=None; comparison_info={"completed":False,"difference_convention":"B_MINUS_A"}
    if configuration["mode"]=="COMPARE":
        dataset_b={"label":configuration["name_b"],"source_folder":configuration["path_b"],"workbook":str(workbook_b) if workbook_b else ""}
        metrics.update({"Dataset B":configuration["name_b"],"Dataset B summary rows":len(summary_b) if summary_b is not None else 0})
        highlights.append(f"Comparison convention: {configuration['name_b']} minus {configuration['name_a']}.")
        comparison_info={"completed":comparison is not None,"difference_convention":"B_MINUS_A","workbook":str(comparison_workbook) if comparison_workbook else ""}
    manifest={"schema_version":int(schema_text),"analysis_id":analysis_id,"display_name":"DC Operating Point","status":"completed",
              "analysis_mode":configuration["mode"],"dataset_label":configuration["name_a"] if configuration["mode"]=="SINGLE" else f"{configuration['name_b']} vs {configuration['name_a']}",
              "source_folder":configuration["path_a"],"dataset_a":{"label":configuration["name_a"],"source_folder":configuration["path_a"],"workbook":str(workbook_a) if workbook_a else ""},
              "dataset_b":dataset_b,"comparison":comparison_info,"highlights":highlights,"metrics":metrics,
              "generated_images":copied_images,"slides":slides}
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return manifest_path


def open_file(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def run_analysis():
    configuration = configure_analysis_gui()
    if not configuration.get("accepted"):
        return
    progress = AnalysisProgress()
    try:
        generated_images = []
        settings = {
            "Recalculate from raw ADC counts": configuration["recalculate"],
            "Exclude stabilization timeouts": configuration["exclude_timeouts"],
            "Exclude invalid measurements": configuration["valid_only"],
            "Selected plots": ", ".join(configuration["selected_plots"]),
            "Comparison convention": "Configuration B minus Configuration A",
            "Comparison match keys": "RF frequency and power setpoint",
        }

        if configuration["mode"] == "COMPARE":
            a_end, b_start, b_end = 42, 42, 84
        else:
            a_end, b_start, b_end = 88, 0, 0

        progress.update(3, "Loading Configuration A", configuration["path_a"])
        raw_a, failures_a, _ = read_dataset(configuration["path_a"], configuration["name_a"])
        progress.update(10, "Preparing Configuration A", "Applying row selection and engineering calculations")
        data_a = prepare_analysis_data(
            raw_a, configuration["recalculate"], configuration["exclude_timeouts"], configuration["valid_only"]
        )
        progress.update(18, "Summarizing Configuration A", "Calculating condition and frequency summaries")
        summary_a = create_summary(data_a)
        frequency_a = create_frequency_summary(data_a)
        output_a = dataset_output_directory(configuration["path_a"])
        workbook_a = output_a / configuration["workbook_name"]
        plot_folder_a = output_a / configuration["plot_folder_name"]
        progress.update(27, "Writing Configuration A workbook", str(workbook_a))
        write_analysis_workbook(workbook_a, data_a, summary_a, frequency_a, failures_a, settings)
        progress.update(35, "Generating Configuration A plots", f"Selected plots: {len(configuration['selected_plots'])}")
        generated_images.extend(("dataset_a", path) for path in create_plots(
            data_a, plot_folder_a, configuration["selected_plots"], configuration["name_a"]
        ))
        progress.update(a_end, "Configuration A complete", str(workbook_a))

        summary_b = None
        comparison = None
        workbook_b = None
        comparison_workbook = None
        if configuration["mode"] == "COMPARE":
            progress.update(b_start + 2, "Loading Configuration B", configuration["path_b"])
            raw_b, failures_b, _ = read_dataset(configuration["path_b"], configuration["name_b"])
            progress.update(b_start + 8, "Preparing Configuration B", "Applying row selection and engineering calculations")
            data_b = prepare_analysis_data(
                raw_b, configuration["recalculate"], configuration["exclude_timeouts"], configuration["valid_only"]
            )
            progress.update(b_start + 15, "Summarizing Configuration B", "Calculating condition and frequency summaries")
            summary_b = create_summary(data_b)
            frequency_b = create_frequency_summary(data_b)
            output_b = dataset_output_directory(configuration["path_b"])
            workbook_b = output_b / configuration["workbook_name"]
            plot_folder_b = output_b / configuration["plot_folder_name"]
            progress.update(b_start + 23, "Writing Configuration B workbook", str(workbook_b))
            write_analysis_workbook(workbook_b, data_b, summary_b, frequency_b, failures_b, settings)
            progress.update(b_start + 31, "Generating Configuration B plots", f"Selected plots: {len(configuration['selected_plots'])}")
            generated_images.extend(("dataset_b", path) for path in create_plots(
                data_b, plot_folder_b, configuration["selected_plots"], configuration["name_b"]
            ))
            progress.update(b_end, "Configuration B complete", str(workbook_b))

            progress.update(87, "Comparing configurations", "Matching RF frequency and power-setpoint conditions")
            comparison = create_comparison(summary_a, summary_b, configuration["name_a"], configuration["name_b"])
            comparison_folder = output_a / COMPARISON_FOLDER_NAME
            comparison_folder.mkdir(parents=True, exist_ok=True)
            comparison_workbook = comparison_folder / COMPARISON_WORKBOOK_NAME
            compare_settings = {
                **settings,
                "Configuration A": configuration["name_a"],
                "Configuration A Path": configuration["path_a"],
                "Configuration B": configuration["name_b"],
                "Configuration B Path": configuration["path_b"],
            }
            progress.update(90, "Writing comparison workbook", str(comparison_workbook))
            write_comparison_workbook(
                comparison_workbook, comparison, summary_a, summary_b, failures_a, failures_b, compare_settings
            )
            progress.update(93, "Generating comparison plots", "Creating overlays and B-minus-A panels")
            generated_images.extend(("comparison", path) for path in create_comparison_plots(
                comparison, configuration["name_a"], configuration["name_b"], comparison_folder / "Comparison Plots"
            ))

        package_warning = None
        if powerpoint_export_requested():
            try:
                progress.update(97, "Packaging PowerPoint assets", "Copying current-run images and summary tables")
                manifest_path = export_powerpoint_package(configuration, generated_images, summary_a, summary_b,
                                                           comparison, workbook_a, workbook_b, comparison_workbook)
                if manifest_path:
                    print(f"PowerPoint package manifest saved to: {manifest_path}")
            except Exception as error:
                package_warning = f"PowerPoint package export warning: {error}"
                print(package_warning, file=sys.stderr)

        progress.update(100, "Analysis complete", "All requested outputs have been generated")
        if configuration["open_workbook"]:
            open_file(workbook_a)
        progress.close()

        completion_root = tk.Tk(); completion_root.withdraw()
        message = f"DC Operating Point analysis is complete.\n\nConfiguration A workbook:\n{workbook_a}"
        if comparison_workbook:
            message += f"\n\nComparison workbook:\n{comparison_workbook}"
        if package_warning:
            message += f"\n\n{package_warning}\nNormal analysis outputs were still completed."
        messagebox.showinfo("Analysis complete", message, parent=completion_root)
        completion_root.destroy()
    except Exception:
        progress.close()
        raise

if __name__ == "__main__":
    run_analysis()
