#!/usr/bin/env python3
"""Pulse Sync 10%-90% transient analysis for the current collection-folder format.

Supports single-dataset and Dataset B minus Dataset A comparison workflows,
unified analysis GUI defaults, and optional consolidated-PowerPoint packaging.
The coworker's original 50%-anchored threshold algorithm is preserved.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.powerpoint import powerpoint_export_requested  # noqa: E402
from suite_common.reports import open_with_default_app  # noqa: E402
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ANALYSIS_WORKBOOK = "Psync_Analysis.xlsx"
PLOT_FOLDER = "Psync Analysis Plots"
COMPARISON_FOLDER = "Hardware Configuration Comparison"
COMPARISON_WORKBOOK = "Psync_Hardware_Comparison.xlsx"
CAPTURE_PATTERN = re.compile(
    r"rf_(?P<rf>[0-9.]+)MHz__pwr_(?P<power>\d+)W__pulse_(?P<pulse>\d+)Hz"
    r"__duty_(?P<duty>\d+)pct__(?P<view>rise|fall|full_pulse)__sample_(?P<sample>\d+)",
    re.IGNORECASE,
)


def norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def read_capture(path: Path, channel: str):
    suffix = path.suffix.lower()
    if suffix == ".csv":
        data = pd.read_csv(path, sep=None, engine="python")
    elif suffix == ".xlsx":
        data = pd.read_excel(path, engine="openpyxl")
    elif suffix == ".xls":
        data = pd.read_excel(path, engine="xlrd")
    else:
        raise ValueError(f"Unsupported capture type: {path.suffix}")
    data = data.dropna(how="all").dropna(axis=1, how="all")
    if data.empty or len(data.columns) < 2:
        raise ValueError("Waveform file has fewer than two populated columns.")
    names = {column: norm(column) for column in data.columns}
    time_column = next((column for column, name in names.items() if name.startswith("time")), data.columns[0])
    channel_norm = norm(channel)
    voltage_column = next(
        (column for column, name in names.items()
         if name.startswith(channel_norm) or name.startswith(channel_norm.replace("ch", "channel"))),
        None,
    )
    if voltage_column is None:
        voltage_column = next(column for column in data.columns if column != time_column)
    result = pd.DataFrame({
        "Time_s": pd.to_numeric(data[time_column], errors="coerce"),
        "Signal_V": pd.to_numeric(data[voltage_column], errors="coerce"),
    }).replace([np.inf, -np.inf], np.nan).dropna()
    result = result.sort_values("Time_s").drop_duplicates("Time_s").reset_index(drop=True)
    if len(result) < 20:
        raise ValueError("Fewer than 20 valid numeric samples.")
    if not np.all(np.diff(result["Time_s"].to_numpy(float)) > 0):
        raise ValueError("Time samples are not strictly increasing.")
    return result


def interp(t1, y1, t2, y2, threshold):
    return float(t2 if y2 == y1 else t1 + (threshold - y1) / (y2 - y1) * (t2 - t1))


def first_sustained(time, signal, threshold, rising, start, consecutive):
    crossed = signal >= threshold if rising else signal <= threshold
    directional = (lambda a, b: a < threshold <= b) if rising else (lambda a, b: a > threshold >= b)
    for index in range(max(1, start), len(signal) - consecutive + 1):
        if directional(signal[index - 1], signal[index]) and np.all(crossed[index:index + consecutive]):
            return interp(time[index - 1], signal[index - 1], time[index], signal[index], threshold), index
    raise ValueError(f"No sustained crossing at {threshold:.6g} V")


def last_before(time, signal, threshold, rising, end):
    directional = (lambda a, b: a < threshold <= b) if rising else (lambda a, b: a > threshold >= b)
    for index in range(min(end, len(signal) - 1), 0, -1):
        if directional(signal[index - 1], signal[index]):
            return interp(time[index - 1], signal[index - 1], time[index], signal[index], threshold), index
    raise ValueError(f"No directional crossing before t50 at {threshold:.6g} V")


def analyze_waveform(path, plateau_fraction=0.10, smooth=5, consecutive=3, channel="CH1"):
    data = read_capture(path, channel)
    time = data["Time_s"].to_numpy(float)
    raw = data["Signal_V"].to_numpy(float)
    signal = (
        pd.Series(raw).rolling(smooth, center=True, min_periods=1).median().to_numpy()
        if smooth > 1 else raw.copy()
    )
    count = len(signal)
    plateau_count = min(max(10, round(count * plateau_fraction)), max(10, count // 3))
    initial = float(np.median(signal[:plateau_count]))
    final = float(np.median(signal[-plateau_count:]))
    delta = final - initial
    initial_noise = float(np.std(signal[:plateau_count], ddof=1))
    final_noise = float(np.std(signal[-plateau_count:], ddof=1))
    noise = max(initial_noise, final_noise)
    if abs(delta) <= max(1e-12, 3 * noise):
        raise ValueError(f"Step {delta:.6g} V is too small versus noise {noise:.6g} V")
    rising = delta > 0
    threshold_10 = initial + 0.1 * delta
    threshold_50 = initial + 0.5 * delta
    threshold_90 = initial + 0.9 * delta
    t50, index_50 = first_sustained(time, signal, threshold_50, rising, 1, consecutive)
    t10, _ = last_before(time, signal, threshold_10, rising, index_50)
    t90, _ = first_sustained(time, signal, threshold_90, rising, index_50, consecutive)
    if not t10 <= t50 <= t90:
        raise ValueError("Invalid t10/t50/t90 order")
    transition_time = t90 - t10
    result = {
        "Status": "OK", "Direction": "Rising" if rising else "Falling", "Samples": count,
        "Initial_Level_V": initial, "Final_Level_V": final, "Step_Amplitude_V": delta,
        "Threshold_10pct_V": threshold_10, "Threshold_50pct_V": threshold_50,
        "Threshold_90pct_V": threshold_90, "t10_s": t10, "t50_s": t50, "t90_s": t90,
        "Transition_Time_s": transition_time, "Transition_Time_ms": transition_time * 1e3,
        "Transition_Time_us": transition_time * 1e6,
        "Initial_Plateau_Std_V": initial_noise, "Final_Plateau_Std_V": final_noise,
        "Plateau_Noise_Std_V": noise, "Error": "",
    }
    data["Signal_Used_V"] = signal
    return result, data


def parse_capture_metadata(path: Path):
    for candidate in (path.parent.name, path.stem):
        match = CAPTURE_PATTERN.search(candidate)
        if match:
            values = match.groupdict()
            return {
                "RF_Frequency_MHz": float(values["rf"]),
                "Power_Setpoint_W": int(values["power"]),
                "Pulse_Frequency_Hz": int(values["pulse"]),
                "Duty_Cycle_Percent": int(values["duty"]),
                "View": values["view"].upper(),
                "Sample": int(values["sample"]),
                "Capture_Name": match.group(0),
            }
    raise ValueError(f"Could not parse Pulse Sync test conditions from {path.parent.name!r}")


def discover_capture_files(folder: Path, channel: str):
    channel_upper = channel.upper()
    files = []
    for capture_info in folder.rglob("capture_info.txt"):
        capture_folder = capture_info.parent
        candidates = [
            item for item in capture_folder.iterdir()
            if item.is_file() and item.suffix.lower() in {".csv", ".xlsx", ".xls"}
            and channel_upper.lower() in item.stem.lower()
        ]
        if candidates:
            files.append(sorted(candidates)[0])
    if files:
        return sorted(files)
    return sorted(
        item for item in folder.rglob("*")
        if item.is_file() and item.suffix.lower() in {".csv", ".xlsx", ".xls"}
        and channel_upper.lower() in item.stem.lower()
        and CAPTURE_PATTERN.search(item.parent.name)
    )


def plot_one(path, data, result, output_folder):
    output_folder.mkdir(parents=True, exist_ok=True)
    time_ms = data["Time_s"] * 1e3
    signal = data["Signal_Used_V"]
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.plot(time_ms, signal, linewidth=1, label="Signal")
    for percent in (10, 50, 90):
        axis.axhline(result[f"Threshold_{percent}pct_V"], linestyle="--", label=f"{percent}% threshold")
        axis.axvline(result[f"t{percent}_s"] * 1e3, linestyle=":", label=f"t{percent}")
    axis.set(
        title=f"{result['Capture_Name']} | {result['Direction']} | {result['Transition_Time_us']:.3f} us",
        xlabel="Time (ms)", ylabel="Signal (V)",
    )
    axis.grid(alpha=0.3)
    axis.legend(fontsize=8)
    figure.tight_layout()
    output = output_folder / f"{result['Capture_Name']}_{path.stem}_analysis.png"
    figure.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return output


def create_summary(results):
    valid = results[results["Status"] == "OK"].copy()
    if valid.empty:
        raise RuntimeError("No captures successfully analyzed.")
    values = valid["Transition_Time_us"].astype(float)
    return pd.DataFrame([
        ("Files Analyzed", len(results)), ("Valid Captures (N)", len(valid)),
        ("Failed Captures", len(results) - len(valid)),
        ("Directions", ", ".join(sorted(valid["Direction"].dropna().unique()))),
        ("Mean Transition Time (us)", values.mean()),
        ("Sample Std. Dev. (us)", values.std(ddof=1)),
        ("Minimum Transition Time (us)", values.min()),
        ("Maximum Transition Time (us)", values.max()),
        ("Peak-to-Peak Variation (us)", values.max() - values.min()),
        ("Median Transition Time (us)", values.median()),
    ], columns=["Metric", "Value"])


def create_condition_summary(results):
    valid = results[results["Status"] == "OK"].copy()
    keys = ["RF_Frequency_MHz", "Power_Setpoint_W", "Pulse_Frequency_Hz",
            "Duty_Cycle_Percent", "View", "Direction"]
    return valid.groupby(keys, dropna=False)["Transition_Time_us"].agg(
        Average_us="mean", Minimum_us="min", Maximum_us="max", Std_Dev_us="std", Sample_Count="count"
    ).reset_index()


def process_dataset(folder, name, settings):
    folder = Path(folder).resolve()
    capture_files = discover_capture_files(folder, settings["channel"])
    if not capture_files:
        raise ValueError(f"No {settings['channel']} Pulse Sync capture files were found in {folder}")
    plot_folder = folder / PLOT_FOLDER
    rows, images = [], []
    for index, path in enumerate(capture_files, 1):
        try:
            metadata = parse_capture_metadata(path)
            if metadata["View"] not in settings["views"]:
                continue
            result, waveform = analyze_waveform(
                path, settings["plateau_fraction"], settings["smooth"],
                settings["consecutive"], settings["channel"],
            )
            result.update(metadata)
            result.update({"File": path.name, "Source_Path": str(path)})
            rows.append(result)
            if settings["plots"]:
                images.append(plot_one(path, waveform, result, plot_folder))
        except Exception as error:
            try:
                metadata = parse_capture_metadata(path)
            except Exception:
                metadata = {}
            rows.append({"File": path.name, "Source_Path": str(path), "Status": "FAILED",
                         "Error": str(error), "Transition_Time_us": np.nan, **metadata})
    results = pd.DataFrame(rows)
    if results.empty:
        raise RuntimeError("No selected Pulse Sync capture views were found.")
    summary = create_summary(results)
    condition_summary = create_condition_summary(results)
    configuration = pd.DataFrame([
        ("Dataset", name), ("Source Folder", str(folder)),
        ("Channel", settings["channel"]),
        ("Views", ", ".join(settings["views"])),
        ("Method", "50% anchor; last directional 10% crossing before t50; first sustained 90% crossing after t50"),
        ("Smoothing", f"{settings['smooth']}-point centered median"),
        ("Consecutive samples", settings["consecutive"]),
        ("Plateau fraction", settings["plateau_fraction"]),
        ("Standard deviation", "Sample, N-1"),
    ], columns=["Setting", "Value"])
    workbook = folder / ANALYSIS_WORKBOOK
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        condition_summary.to_excel(writer, sheet_name="Condition Summary", index=False)
        results.to_excel(writer, sheet_name="Individual Results", index=False)
        configuration.to_excel(writer, sheet_name="Configuration", index=False)
    return {"results": results, "summary": summary, "condition_summary": condition_summary,
            "workbook": workbook, "images": images, "folder": folder, "name": name}


def create_comparison(data_a, data_b, name_a, name_b):
    keys = ["RF_Frequency_MHz", "Power_Setpoint_W", "Pulse_Frequency_Hz",
            "Duty_Cycle_Percent", "View", "Direction"]
    left = data_a["condition_summary"].rename(columns={
        "Average_us": f"Average {name_a} (us)", "Std_Dev_us": f"Std Dev {name_a} (us)",
        "Sample_Count": f"Sample Count {name_a}",
    })
    right = data_b["condition_summary"].rename(columns={
        "Average_us": f"Average {name_b} (us)", "Std_Dev_us": f"Std Dev {name_b} (us)",
        "Sample_Count": f"Sample Count {name_b}",
    })
    comparison = left.merge(right, on=keys, how="outer", indicator=True, suffixes=("_A", "_B"))
    comparison["Difference B-A (us)"] = comparison[f"Average {name_b} (us)"] - comparison[f"Average {name_a} (us)"]
    comparison["Absolute Difference (us)"] = comparison["Difference B-A (us)"].abs()
    comparison["Percent Difference vs A"] = np.where(
        comparison[f"Average {name_a} (us)"].abs() > 0,
        100 * comparison["Difference B-A (us)"] / comparison[f"Average {name_a} (us)"], np.nan,
    )
    return comparison


def create_comparison_plots(comparison, name_a, name_b, folder):
    folder.mkdir(parents=True, exist_ok=True)
    images = []
    matched = comparison[comparison["_merge"] == "both"].copy()
    group_keys = ["RF_Frequency_MHz", "Pulse_Frequency_Hz", "Duty_Cycle_Percent", "View", "Direction"]
    for values, group in matched.groupby(group_keys, dropna=False):
        group = group.sort_values("Power_Setpoint_W")
        figure, (top, bottom) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
        top.plot(group["Power_Setpoint_W"], group[f"Average {name_a} (us)"], marker="o", label=name_a)
        top.plot(group["Power_Setpoint_W"], group[f"Average {name_b} (us)"], marker="s", label=name_b)
        top.set_ylabel("Average transition time (us)")
        top.grid(alpha=0.3); top.legend()
        bottom.plot(group["Power_Setpoint_W"], group["Difference B-A (us)"], marker="o", color="purple")
        bottom.axhline(0, color="black", linewidth=0.8)
        bottom.set_xlabel("Power setpoint (W)"); bottom.set_ylabel("B - A (us)"); bottom.grid(alpha=0.3)
        rf, pulse, duty, view, direction = values
        figure.suptitle(f"{rf:g} MHz | {pulse:g} Hz | {duty:g}% | {view} | {direction}")
        figure.tight_layout()
        filename = f"comparison_{rf:g}MHz_{pulse:g}Hz_{duty:g}pct_{view}_{direction}.png"
        path = folder / re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)
        figure.savefig(path, dpi=180, bbox_inches="tight")
        plt.close(figure)
        images.append(path)
    return images


def export_powerpoint_package(configuration, images, data_a, data_b=None, comparison=None, comparison_workbook=None):
    if not powerpoint_export_requested():
        return None
    package_text = os.environ.get("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER", "").strip()
    if not package_text:
        raise ValueError("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER is not set.")
    package = Path(package_text).expanduser().resolve()
    package.mkdir(parents=True, exist_ok=True)
    manifest_path = package / "manifest.json"
    if manifest_path.exists(): manifest_path.unlink()
    image_root, table_root = package / "images", package / "tables"
    if image_root.exists(): shutil.rmtree(image_root)
    if table_root.exists(): shutil.rmtree(table_root)
    table_root.mkdir(parents=True, exist_ok=True)
    data_a["condition_summary"].to_csv(table_root / "dataset_a_condition_summary.csv", index=False)
    if data_b is not None:
        data_b["condition_summary"].to_csv(table_root / "dataset_b_condition_summary.csv", index=False)
    if comparison is not None:
        comparison.to_csv(table_root / "comparison_b_minus_a.csv", index=False)
    slides, copied = [], []
    for role, source_path in images:
        source = Path(source_path)
        if not source.is_file(): continue
        destination_folder = image_root / role
        destination_folder.mkdir(parents=True, exist_ok=True)
        destination = destination_folder / source.name
        if destination.exists(): destination = destination_folder / f"{source.parent.name}_{source.name}"
        shutil.copy2(source, destination)
        relative = destination.relative_to(package).as_posix()
        copied.append({"dataset_role": role.upper(), "source": str(source), "package_file": relative})
        slide = {"slide_type": "image", "title": source.name,
                 "image_file": relative, "dataset_role": role.upper()}
        if role == "comparison": slide["difference_convention"] = "B_MINUS_A"
        slides.append(slide)
    mode = configuration["mode"]
    dataset_b = None
    comparison_info = {"completed": False, "difference_convention": "B_MINUS_A"}
    if mode == "COMPARE":
        dataset_b = {"label": configuration["name_b"], "source_folder": configuration["folder_b"],
                     "workbook": str(data_b["workbook"]) if data_b else ""}
        comparison_info = {"completed": comparison is not None, "difference_convention": "B_MINUS_A",
                           "workbook": str(comparison_workbook) if comparison_workbook else ""}
    payload = {
        "schema_version": int(os.environ.get("PARAMOUNT_POWERPOINT_SCHEMA_VERSION", "1")),
        "analysis_id": os.environ.get("PARAMOUNT_POWERPOINT_ANALYSIS_ID", "psync").strip(),
        "display_name": "Pulse Sync", "status": "completed", "analysis_mode": mode,
        "dataset_label": configuration["name_a"] if mode == "SINGLE" else f"{configuration['name_b']} vs {configuration['name_a']}",
        "source_folder": configuration["folder_a"],
        "dataset_a": {"label": configuration["name_a"], "source_folder": configuration["folder_a"],
                      "workbook": str(data_a["workbook"])},
        "dataset_b": dataset_b, "comparison": comparison_info,
        "highlights": [f"Dataset A: {configuration['name_a']}",
                       f"Generated {len(slides)} image slide(s) from this analysis run."],
        "metrics": {"Analysis mode": mode, "Generated image slides": len(slides)},
        "generated_images": copied, "slides": slides,
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest_path


def configure_gui():
    root = tk.Tk()
    root.title("Pulse Sync Data Analysis")
    root.geometry("920x650")
    root.minsize(820, 590)
    style = ttk.Style(root)
    try: style.theme_use("vista")
    except tk.TclError: pass
    style.configure("Header.TLabel", font=("Segoe UI", 15, "bold"))
    outer = ttk.Frame(root, padding=14); outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="Pulse Sync Data Analysis", style="Header.TLabel").pack(anchor="w")
    ttk.Label(outer, text="Analyze the current Pulse Sync collection-folder format using the original 50%-anchored 10%-90% algorithm.", wraplength=860).pack(anchor="w", pady=(2, 10))
    notebook = ttk.Notebook(outer); notebook.pack(fill="both", expand=True)
    data_tab = ttk.Frame(notebook, padding=12); settings_tab = ttk.Frame(notebook, padding=12); output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(data_tab, text="Datasets"); notebook.add(settings_tab, text="Analysis Method"); notebook.add(output_tab, text="Outputs")
    launcher_mode = os.environ.get("PARAMOUNT_ANALYSIS_MODE", "INDIVIDUAL").strip().upper()
    initial_mode = launcher_mode if launcher_mode in {"SINGLE", "COMPARE"} else "SINGLE"
    mode = tk.StringVar(value=initial_mode)
    folder_a = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_FOLDER", "").strip())
    folder_b = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_FOLDER", "").strip())
    name_a = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_LABEL", "").strip() or "Configuration A")
    name_b = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_LABEL", "").strip() or "Configuration B")
    ttk.Radiobutton(data_tab, text="Single dataset", variable=mode, value="SINGLE").grid(row=0,column=0,sticky="w")
    ttk.Radiobutton(data_tab, text="Compare two datasets", variable=mode, value="COMPARE").grid(row=0,column=1,sticky="w")
    def browse(variable):
        current=variable.get().strip(); selected=filedialog.askdirectory(parent=root,mustexist=True,initialdir=current if current and Path(current).is_dir() else None)
        if selected: variable.set(selected)
    ttk.Label(data_tab,text="Dataset A folder").grid(row=1,column=0,sticky="w",pady=7); ttk.Entry(data_tab,textvariable=folder_a,width=70).grid(row=1,column=1,sticky="ew"); ttk.Button(data_tab,text="Browse...",command=lambda:browse(folder_a)).grid(row=1,column=2)
    ttk.Label(data_tab,text="Dataset A label").grid(row=2,column=0,sticky="w",pady=7); ttk.Entry(data_tab,textvariable=name_a).grid(row=2,column=1,sticky="ew")
    ttk.Label(data_tab,text="Dataset B folder").grid(row=3,column=0,sticky="w",pady=7); entry_b=ttk.Entry(data_tab,textvariable=folder_b);entry_b.grid(row=3,column=1,sticky="ew");button_b=ttk.Button(data_tab,text="Browse...",command=lambda:browse(folder_b));button_b.grid(row=3,column=2)
    ttk.Label(data_tab,text="Dataset B label").grid(row=4,column=0,sticky="w",pady=7);name_b_entry=ttk.Entry(data_tab,textvariable=name_b);name_b_entry.grid(row=4,column=1,sticky="ew");data_tab.columnconfigure(1,weight=1)
    channel=tk.StringVar(value="CH1")
    rise_view=tk.BooleanVar(value=True)
    fall_view=tk.BooleanVar(value=True)
    smooth=tk.StringVar(value="5");consecutive=tk.StringVar(value="3");plateau=tk.StringVar(value="0.10")

    ttk.Label(settings_tab,text="Analysis channel").grid(row=0,column=0,sticky="nw",pady=6)
    channel_frame=ttk.Frame(settings_tab)
    channel_frame.grid(row=0,column=1,sticky="w",pady=6)
    for column,channel_name in enumerate(("CH1","CH2","CH3","CH4")):
        ttk.Radiobutton(
            channel_frame,text=channel_name,variable=channel,value=channel_name
        ).grid(row=0,column=column,sticky="w",padx=(0,14))

    ttk.Label(settings_tab,text="Capture views").grid(row=1,column=0,sticky="nw",pady=6)
    view_frame=ttk.Frame(settings_tab)
    view_frame.grid(row=1,column=1,sticky="w",pady=6)
    ttk.Checkbutton(view_frame,text="RISE",variable=rise_view).grid(row=0,column=0,sticky="w",padx=(0,16))
    ttk.Checkbutton(view_frame,text="FALL",variable=fall_view).grid(row=0,column=1,sticky="w")

    for row,(label,var) in enumerate((("Median smoothing points",smooth),("Consecutive samples",consecutive),("Plateau fraction",plateau)),start=2):
        ttk.Label(settings_tab,text=label).grid(row=row,column=0,sticky="w",pady=6)
        ttk.Entry(settings_tab,textvariable=var).grid(row=row,column=1,sticky="ew",pady=6)
    settings_tab.columnconfigure(1,weight=1)
    plots=tk.BooleanVar(value=True);open_workbook=tk.BooleanVar(value=True)
    ttk.Checkbutton(output_tab,text="Generate individual waveform analysis plots",variable=plots).pack(anchor="w",pady=5)
    ttk.Checkbutton(output_tab,text="Open Dataset A workbook after analysis",variable=open_workbook).pack(anchor="w",pady=5)
    result={}
    def update_mode(*_):
        state="normal" if mode.get()=="COMPARE" else "disabled";entry_b.configure(state=state);button_b.configure(state=state);name_b_entry.configure(state=state)
    mode.trace_add("write",update_mode);update_mode()
    def start():
        try:
            fa=Path(folder_a.get()).expanduser().resolve()
            if not fa.is_dir(): raise ValueError("Select a valid Dataset A folder.")
            fb=None
            if mode.get()=="COMPARE":
                fb=Path(folder_b.get()).expanduser().resolve()
                if not fb.is_dir(): raise ValueError("Select a valid Dataset B folder.")
                if fb==fa: raise ValueError("Dataset A and Dataset B must be different folders.")
            selected_views=[]
            if rise_view.get(): selected_views.append("RISE")
            if fall_view.get(): selected_views.append("FALL")
            if not selected_views: raise ValueError("Select at least one capture view: RISE or FALL.")
            selected_channel=channel.get().strip().upper()
            if selected_channel not in {"CH1","CH2","CH3","CH4"}: raise ValueError("Select one analysis channel: CH1, CH2, CH3, or CH4.")
            result.update(mode=mode.get(),folder_a=str(fa),folder_b=str(fb) if fb else "",name_a=name_a.get().strip() or "Configuration A",name_b=name_b.get().strip() or "Configuration B",channel=selected_channel,views=selected_views,smooth=int(smooth.get()),consecutive=int(consecutive.get()),plateau_fraction=float(plateau.get()),plots=plots.get(),open_workbook=open_workbook.get())
            if result["smooth"]<1 or result["consecutive"]<1 or not 0<result["plateau_fraction"]<=0.33: raise ValueError("Smoothing and consecutive samples must be positive; plateau fraction must be greater than 0 and no more than 0.33.")
            root.destroy()
        except Exception as error: messagebox.showerror("Invalid Pulse Sync analysis settings",str(error),parent=root)
    buttons=ttk.Frame(outer);buttons.pack(fill="x",pady=(12,0));ttk.Button(buttons,text="Cancel",command=root.destroy).pack(side="right",padx=(8,0));ttk.Button(buttons,text="Start Analysis",command=start).pack(side="right")
    root.mainloop()
    if not result: raise SystemExit(0)
    return result


def open_file(path):
    open_with_default_app(path)


def main():
    configuration=configure_gui()
    data_a=process_dataset(configuration["folder_a"],configuration["name_a"],configuration)
    images=[("dataset_a",path) for path in data_a["images"]]
    data_b=None;comparison=None;comparison_workbook=None
    if configuration["mode"]=="COMPARE":
        data_b=process_dataset(configuration["folder_b"],configuration["name_b"],configuration)
        images.extend(("dataset_b",path) for path in data_b["images"])
        comparison=create_comparison(data_a,data_b,configuration["name_a"],configuration["name_b"])
        comparison_folder=Path(configuration["folder_a"])/COMPARISON_FOLDER;comparison_folder.mkdir(parents=True,exist_ok=True)
        comparison_workbook=comparison_folder/COMPARISON_WORKBOOK
        with pd.ExcelWriter(comparison_workbook,engine="openpyxl") as writer:
            comparison.to_excel(writer,sheet_name="B minus A Comparison",index=False)
            comparison.sort_values("Absolute Difference (us)",ascending=False).to_excel(writer,sheet_name="Difference Ranking",index=False)
            pd.DataFrame([("Difference convention","Dataset B minus Dataset A"),("Dataset A",configuration["name_a"]),("Dataset B",configuration["name_b"])],columns=["Setting","Value"]).to_excel(writer,sheet_name="Settings",index=False)
        images.extend(("comparison",path) for path in create_comparison_plots(comparison,configuration["name_a"],configuration["name_b"],comparison_folder/"Comparison Plots"))
    warning=None
    if powerpoint_export_requested():
        try:
            manifest=export_powerpoint_package(configuration,images,data_a,data_b,comparison,comparison_workbook)
            if manifest: print(f"PowerPoint package manifest saved to: {manifest}")
        except Exception as error:
            warning=f"PowerPoint package export warning: {error}";print(warning,file=sys.stderr)
    if configuration["open_workbook"]: open_file(data_a["workbook"])
    root=tk.Tk();root.withdraw();message=f"Dataset A workbook:\n{data_a['workbook']}"
    if comparison_workbook: message+=f"\n\nComparison workbook:\n{comparison_workbook}"
    if warning: message+=f"\n\n{warning}\nNormal analysis outputs were still completed."
    messagebox.showinfo("Pulse Sync analysis complete",message,parent=root);root.destroy()


if __name__ == "__main__":
    try: main()
    except Exception as error:
        print(f"ERROR: {error}",file=sys.stderr)
        try:
            root=tk.Tk();root.withdraw();messagebox.showerror("Pulse Sync analysis failed",str(error),parent=root);root.destroy()
        except Exception: pass
        raise SystemExit(1)
