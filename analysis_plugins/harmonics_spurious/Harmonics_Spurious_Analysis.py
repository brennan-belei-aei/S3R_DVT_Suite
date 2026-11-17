"""Standalone Harmonics and Spurious spectrum analysis and comparison GUI.

Consumes the explicit long-form CSV files produced by
PMNT_Harmonics_Spurious_Collection_GUI_AnalysisReady.py.
No instrument-control packages or live equipment connections are used.
"""

import json
import os
import shutil
import sys
import csv
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.powerpoint import powerpoint_export_requested  # noqa: E402
from suite_common.reports import open_with_default_app, size_workbook_columns  # noqa: E402
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import find_peaks


APP_TITLE = "Paramount Harmonics and Spurious Data Analysis"
HARMONICS_FILE_NAME = "Harmonics_Spectrum_Data.csv"
SPURIOUS_FILE_NAME = "Spurious_Spectrum_Data.csv"
SETTINGS_FILE_NAME = "Harmonics_Spurious_Run_Settings.csv"
COUPLER_FILE_NAME = "Coupler_Correction_Map_Used.csv"
ANALYSIS_WORKBOOK_NAME = "Harmonics_Spurious_Analysis.xlsx"
COMPARISON_WORKBOOK_NAME = "Harmonics_Spurious_Hardware_Comparison.xlsx"
RESULTS_TEXT_NAME = "Results_Harmonics_Spurious.txt"
DETAILS_TEXT_NAME = "Setpoint_Fail_Details.txt"
PLOT_FOLDER_NAME = "Harmonics Spurious Analysis Plots"
COMPARISON_FOLDER_NAME = "Hardware Configuration Comparison"

REQUIRED_COLUMNS = [
    "acquisition_id", "test_type", "rf_frequency_Hz", "power_setpoint_W",
    "analyzer_start_frequency_Hz", "analyzer_stop_frequency_Hz",
    "analyzer_resolution_bandwidth_Hz", "active_spec_dBc",
    "spectrum_point_index", "spectrum_frequency_Hz", "spectrum_amplitude_dBm",
]

ACQUISITION_KEYS = ["test_type", "rf_frequency_Hz", "power_setpoint_W"]
COMPARISON_KEYS = [
    "test_type", "rf_frequency_Hz", "power_setpoint_W",
    "analyzer_start_frequency_Hz", "analyzer_stop_frequency_Hz",
    "analyzer_resolution_bandwidth_Hz",
]


class AnalysisProgress:
    def __init__(self):
        self.root=tk.Tk();self.root.title("Harmonics and Spurious Analysis Progress");self.root.geometry("720x235");self.root.resizable(True,False);self.root.protocol("WM_DELETE_WINDOW",lambda:None)
        frame=ttk.Frame(self.root,padding=16);frame.pack(fill="both",expand=True)
        self.heading=tk.StringVar(value="Preparing analysis");self.detail=tk.StringVar(value="");self.percent=tk.StringVar(value="0%")
        ttk.Label(frame,textvariable=self.heading,font=("Segoe UI",12,"bold")).pack(anchor="w")
        ttk.Label(frame,textvariable=self.detail,wraplength=675).pack(anchor="w",pady=(8,12))
        self.bar=ttk.Progressbar(frame,maximum=100,mode="determinate");self.bar.pack(fill="x")
        ttk.Label(frame,textvariable=self.percent).pack(anchor="e",pady=(6,0))
        ttk.Label(frame,text="This window closes automatically when analysis finishes.",foreground="#555555").pack(anchor="w",pady=(10,0))
        self.root.update_idletasks();self.root.update()
    def update(self,value,heading=None,detail=None):
        value=max(0.0,min(100.0,float(value)));self.bar["value"]=value;self.percent.set(f"{value:.0f}%")
        if heading is not None:self.heading.set(str(heading))
        if detail is not None:self.detail.set(str(detail))
        self.root.update_idletasks();self.root.update()
    def close(self):
        try:self.root.destroy()
        except tk.TclError:pass

def _scaled_progress(progress,start,end,fraction,heading=None,detail=None):
    if progress is not None:progress.update(start+(end-start)*max(0,min(1,float(fraction))),heading,detail)


def launcher_default(*names, fallback=""):
    """Read the first non-empty unified-launcher environment value."""
    for name in names:
        value = os.environ.get(name, "").strip().strip('"')
        if value:
            return value
    return fallback


def dataset_folder_default(role):
    role = role.upper()
    return launcher_default(
        f"PARAMOUNT_DATASET_{role}_FOLDER",
        f"PARAMOUNT_DATASET_{role}_PATH",
        fallback="",
    )


def bool_value(value):
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def read_settings(folder):
    path = Path(folder) / SETTINGS_FILE_NAME
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str)
    if not {"setting", "value"}.issubset(frame.columns):
        return {}
    return dict(zip(frame["setting"], frame["value"]))


def read_coupler_map(path):
    path = Path(path) if path else None
    if path is None or not path.exists():
        return pd.DataFrame(columns=["frequency_Hz", "correction_dB"])
    rows = []
    with open(path, newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 2:
                continue
            try:
                rows.append((float(row[0]), float(row[1])))
            except (ValueError, TypeError):
                continue
    if not rows:
        return pd.DataFrame(columns=["frequency_Hz", "correction_dB"])
    return pd.DataFrame(rows, columns=["frequency_Hz", "correction_dB"]).sort_values("frequency_Hz")


def discover_spectrum_files(folder):
    folder = Path(folder)
    paths = []
    for file_name in (HARMONICS_FILE_NAME, SPURIOUS_FILE_NAME):
        path = folder / file_name
        if path.exists():
            paths.append(path)
    return paths


def load_dataset(folder, dataset_name):
    folder = Path(folder)
    files = discover_spectrum_files(folder)
    if not files:
        raise ValueError(
            f"{dataset_name} does not contain {HARMONICS_FILE_NAME} or {SPURIOUS_FILE_NAME}."
        )
    frames, failures = [], []
    for path in files:
        try:
            frame = pd.read_csv(path)
            missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
            if missing:
                raise ValueError("Missing required columns: " + ", ".join(missing))
            frame.insert(0, "dataset_name", dataset_name)
            frame.insert(1, "source_csv", path.name)
            frames.append(frame)
        except Exception as error:
            failures.append({"dataset_name": dataset_name, "source_csv": str(path), "error": str(error)})
    if not frames:
        raise ValueError(f"No readable analysis-ready spectrum CSV was found for {dataset_name}.")
    data = pd.concat(frames, ignore_index=True, sort=False)
    numeric_columns = [
        "rf_frequency_Hz", "rf_frequency_kHz", "power_setpoint_W",
        "analyzer_start_frequency_Hz", "analyzer_stop_frequency_Hz",
        "analyzer_center_frequency_Hz", "analyzer_span_Hz",
        "analyzer_resolution_bandwidth_Hz", "analyzer_trace_point_count_saved",
        "low_power_boundary_W", "harmonics_low_power_spec_dBc",
        "spurious_low_power_spec_dBc", "high_power_spec_dBc", "active_spec_dBc",
        "coupler_reference_spec_dB", "spectrum_point_index",
        "spectrum_frequency_Hz", "spectrum_amplitude_dBm", "cycle_number", "total_cycles",
    ]
    for column in numeric_columns:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["spectrum_frequency_Hz", "spectrum_amplitude_dBm"])
    if "cycle_number" not in data.columns:
        data["cycle_number"] = 1
    if "total_cycles" not in data.columns:
        data["total_cycles"] = int(data["cycle_number"].max()) if not data.empty else 1
    data["cycle_number"] = pd.to_numeric(data["cycle_number"], errors="coerce").fillna(1).astype(int)
    data["total_cycles"] = pd.to_numeric(data["total_cycles"], errors="coerce").fillna(1).astype(int)
    return data, pd.DataFrame(failures), read_settings(folder)


def determine_active_spec(group, use_recorded_specs, harmonic_lp, spurious_lp, high_power, boundary_w):
    test_type = str(group["test_type"].iloc[0])
    setpoint = float(group["power_setpoint_W"].iloc[0])
    if use_recorded_specs and "active_spec_dBc" in group:
        recorded = pd.to_numeric(group["active_spec_dBc"], errors="coerce").dropna()
        if not recorded.empty:
            return float(recorded.iloc[0])
    if setpoint <= boundary_w:
        return float(harmonic_lp if test_type == "Harmonics" else spurious_lp)
    return float(high_power)


def coupler_correction_at(coupler_data, frequency_hz):
    if coupler_data.empty:
        return np.nan
    frequencies = coupler_data["frequency_Hz"].to_numpy(dtype=float)
    corrections = coupler_data["correction_dB"].to_numpy(dtype=float)
    index = int(np.searchsorted(frequencies, frequency_hz, side="left"))
    index = min(max(index, 0), len(frequencies) - 1)
    return float(corrections[index])


def analyze_acquisition(
    group,
    use_recorded_specs=True,
    harmonic_lp=-50.0,
    spurious_lp=-50.0,
    high_power=-50.0,
    low_power_boundary_w=50.0,
    failing_peak_distance=5,
    passing_peak_distance=20,
    harmonic_multiplier=0.95,
    apply_coupler=True,
    coupler_data=None,
    coupler_reference_spec=-60.0,
):
    group = group.sort_values("spectrum_point_index").copy()
    frequencies_hz = group["spectrum_frequency_Hz"].to_numpy(dtype=float)
    amplitudes_dbm = group["spectrum_amplitude_dBm"].to_numpy(dtype=float)
    if len(amplitudes_dbm) < 3:
        raise ValueError("Spectrum contains fewer than three usable points.")

    carrier_amplitude_dbm = float(np.max(amplitudes_dbm))
    dbc = np.round(amplitudes_dbm - carrier_amplitude_dbm, 2)
    active_spec = determine_active_spec(
        group, use_recorded_specs, harmonic_lp, spurious_lp, high_power, low_power_boundary_w
    )
    fail_indices, _ = find_peaks(
        dbc, height=(active_spec + 0.01, 0), distance=max(int(failing_peak_distance), 1)
    )
    pass_indices, _ = find_peaks(
        dbc, height=(-100, active_spec), distance=max(int(passing_peak_distance), 1)
    )

    candidate_carriers = list(fail_indices)
    if candidate_carriers:
        carrier_index = min(candidate_carriers, key=lambda index: abs(float(dbc[index])))
    else:
        carrier_index = int(np.argmax(amplitudes_dbm))
    carrier_frequency_hz = float(frequencies_hz[carrier_index])

    test_type = str(group["test_type"].iloc[0])
    rf_frequency_hz = float(group["rf_frequency_Hz"].iloc[0])
    setpoint_w = float(group["power_setpoint_W"].iloc[0])
    failures = []
    detected = []
    for index in fail_indices:
        if index == carrier_index:
            classification = "CARRIER"
            is_failure = False
            corrected_dbc = float(dbc[index])
            correction_db = np.nan
        else:
            delta_hz = float(frequencies_hz[index] - carrier_frequency_hz)
            qualifies = test_type == "Spurious" or abs(delta_hz) >= rf_frequency_hz * harmonic_multiplier
            if not qualifies:
                classification = "NON-HARMONIC PEAK"
                is_failure = False
                corrected_dbc = float(dbc[index])
                correction_db = np.nan
            else:
                classification = "HARMONIC" if test_type == "Harmonics" else "SPURIOUS"
                correction_db = np.nan
                corrected_dbc = float(dbc[index])
                if test_type == "Harmonics" and apply_coupler and coupler_data is not None and not coupler_data.empty:
                    correction_db = coupler_correction_at(coupler_data, frequencies_hz[index])
                    corrected_dbc = (float(coupler_reference_spec) - correction_db) + float(dbc[index])
                is_failure = corrected_dbc >= active_spec
                if is_failure:
                    failures.append(index)
        detected.append({
            "spectrum_point_index": int(index),
            "frequency_Hz": float(frequencies_hz[index]),
            "frequency_MHz": float(frequencies_hz[index] / 1e6),
            "amplitude_dBm": float(amplitudes_dbm[index]),
            "amplitude_dBc": float(dbc[index]),
            "delta_from_carrier_Hz": float(frequencies_hz[index] - carrier_frequency_hz),
            "delta_from_carrier_MHz": float((frequencies_hz[index] - carrier_frequency_hz) / 1e6),
            "classification": classification,
            "coupler_correction_dB": correction_db,
            "coupler_corrected_dBc": corrected_dbc,
            "active_spec_dBc": active_spec,
            "margin_to_spec_dB": active_spec - corrected_dbc,
            "is_failure": bool(is_failure),
        })

    non_carrier_dbc = [float(dbc[index]) for index in fail_indices if index != carrier_index]
    worst_non_carrier = max(non_carrier_dbc) if non_carrier_dbc else np.nan
    failing_rows = [row for row in detected if row["is_failure"]]
    worst_corrected = max((row["coupler_corrected_dBc"] for row in failing_rows), default=np.nan)
    worst_row = max(failing_rows, key=lambda row: row["coupler_corrected_dBc"], default=None)

    acquisition = {
        "acquisition_id": str(group["acquisition_id"].iloc[0]),
        "analysis_scope": str(group["analysis_scope"].iloc[0]) if "analysis_scope" in group else "Individual Cycle",
        "cycle_number": int(group["cycle_number"].iloc[0]) if "cycle_number" in group else 1,
        "cycles_in_average": int(group["cycles_in_average"].iloc[0]) if "cycles_in_average" in group else 1,
        "test_type": test_type,
        "rf_frequency_Hz": rf_frequency_hz,
        "rf_frequency_MHz": rf_frequency_hz / 1e6,
        "power_setpoint_W": setpoint_w,
        "analyzer_start_frequency_Hz": float(group["analyzer_start_frequency_Hz"].iloc[0]),
        "analyzer_stop_frequency_Hz": float(group["analyzer_stop_frequency_Hz"].iloc[0]),
        "analyzer_resolution_bandwidth_Hz": float(group["analyzer_resolution_bandwidth_Hz"].iloc[0]),
        "trace_point_count": len(group),
        "carrier_point_index": int(carrier_index),
        "carrier_frequency_Hz": carrier_frequency_hz,
        "carrier_frequency_MHz": carrier_frequency_hz / 1e6,
        "carrier_amplitude_dBm": carrier_amplitude_dbm,
        "active_spec_dBc": active_spec,
        "failing_peak_count": len(failing_rows),
        "passing_peak_count": len(pass_indices),
        "worst_non_carrier_peak_dBc": worst_non_carrier,
        "worst_corrected_peak_dBc": worst_corrected,
        "worst_failure_frequency_Hz": worst_row["frequency_Hz"] if worst_row else np.nan,
        "worst_failure_delta_MHz": worst_row["delta_from_carrier_MHz"] if worst_row else np.nan,
        "worst_margin_to_spec_dB": active_spec - worst_corrected if failing_rows else np.nan,
        "result": "FAIL" if failing_rows else "PASS",
    }
    points = group[["spectrum_point_index", "spectrum_frequency_Hz", "spectrum_amplitude_dBm"]].copy()
    points.insert(0, "analysis_scope", acquisition["analysis_scope"])
    points.insert(1, "cycle_number", acquisition["cycle_number"])
    points.insert(2, "cycles_in_average", acquisition["cycles_in_average"])
    points["spectrum_amplitude_dBc"] = dbc
    points["is_carrier"] = points["spectrum_point_index"].astype(int).eq(carrier_index)
    points["active_spec_dBc"] = active_spec
    return acquisition, pd.DataFrame(detected), points


def build_cycle_average_spectra(data):
    """Create point-by-point mean spectra across matching cycles from raw acquisitions."""
    condition_keys = COMPARISON_KEYS
    rows = []
    for condition, group in data.groupby(condition_keys, dropna=False, sort=False):
        cycle_count = int(group["cycle_number"].nunique())
        # Average by saved spectrum point so every original cycle remains independently analyzable.
        averaged = group.groupby("spectrum_point_index", sort=True, as_index=False).agg(
            spectrum_frequency_Hz=("spectrum_frequency_Hz", "mean"),
            spectrum_amplitude_dBm=("spectrum_amplitude_dBm", "mean"),
            point_cycle_count=("cycle_number", "nunique"),
        )
        template = group.iloc[0].copy()
        condition_token = "_".join(str(value) for value in condition)
        for _, point in averaged.iterrows():
            row = template.copy()
            row["acquisition_id"] = f"CycleAverage_{condition_token}"
            row["analysis_scope"] = "Cycle Average"
            row["cycle_number"] = 0
            row["cycles_in_average"] = cycle_count
            row["spectrum_point_index"] = int(point["spectrum_point_index"])
            row["spectrum_frequency_Hz"] = float(point["spectrum_frequency_Hz"])
            row["spectrum_amplitude_dBm"] = float(point["spectrum_amplitude_dBm"])
            row["point_cycle_count"] = int(point["point_cycle_count"])
            rows.append(row)
    return pd.DataFrame(rows)

def analyze_dataset(data, options, coupler_data, progress_callback=None):
    acquisitions, peaks, points, failures = [], [], [], []
    grouped_acquisitions=list(data.groupby("acquisition_id",sort=False));total_acquisitions=max(len(grouped_acquisitions),1)
    for acquisition_index,(acquisition_id,group) in enumerate(grouped_acquisitions,start=1):
        if progress_callback is not None:
            progress_callback((acquisition_index-1)/total_acquisitions,f"Analyzing acquisition {acquisition_index} of {total_acquisitions}",str(acquisition_id))
        try:
            acquisition, peak_frame, point_frame = analyze_acquisition(
                group,
                use_recorded_specs=options["use_recorded_specs"],
                harmonic_lp=options["harmonic_lp"],
                spurious_lp=options["spurious_lp"],
                high_power=options["high_power"],
                low_power_boundary_w=options["low_power_boundary_w"],
                failing_peak_distance=options["failing_peak_distance"],
                passing_peak_distance=options["passing_peak_distance"],
                harmonic_multiplier=options["harmonic_multiplier"],
                apply_coupler=options["apply_coupler"],
                coupler_data=coupler_data,
                coupler_reference_spec=options["coupler_reference_spec"],
            )
            acquisitions.append(acquisition)
            if not peak_frame.empty:
                peak_frame.insert(0, "acquisition_id", acquisition_id)
                peaks.append(peak_frame)
            point_frame.insert(0, "acquisition_id", acquisition_id)
            points.append(point_frame)
        except Exception as error:
            failures.append({"acquisition_id": acquisition_id, "error": str(error)})
    if progress_callback is not None:
        progress_callback(1.0,"Acquisition analysis complete",f"Processed {len(grouped_acquisitions)} acquisitions")
    return (
        pd.DataFrame(acquisitions),
        pd.concat(peaks, ignore_index=True) if peaks else pd.DataFrame(),
        pd.concat(points, ignore_index=True) if points else pd.DataFrame(),
        pd.DataFrame(failures),
    )


def generate_acquisition_plots(data, acquisition_summary, peak_details, output_folder, make_dbm, make_dbc, coupler_data):
    output_folder = Path(output_folder)
    dbm_folder = output_folder / "dBm"
    dbc_folder = output_folder / "dBc"
    paths = []
    summary_by_id = acquisition_summary.set_index("acquisition_id")
    for acquisition_id, group in data.groupby("acquisition_id", sort=False):
        if acquisition_id not in summary_by_id.index:
            continue
        summary = summary_by_id.loc[acquisition_id]
        group = group.sort_values("spectrum_point_index")
        frequencies_mhz = group["spectrum_frequency_Hz"].to_numpy(float) / 1e6
        dbm = group["spectrum_amplitude_dBm"].to_numpy(float)
        dbc = np.round(dbm - np.max(dbm), 2)
        test = summary["test_type"]
        rf_mhz = summary["rf_frequency_MHz"]
        setpoint = summary["power_setpoint_W"]
        spec = summary["active_spec_dBc"]
        scope = str(summary.get("analysis_scope", "Individual Cycle"))
        cycle = int(summary.get("cycle_number", 1))
        cycle_suffix = "CycleAverage" if scope == "Cycle Average" else f"Cycle{cycle}"
        base = f"{test}_{rf_mhz:g}MHz_{setpoint:g}W_{cycle_suffix}"

        if make_dbm:
            dbm_folder.mkdir(parents=True, exist_ok=True)
            fig, ax = plt.subplots(figsize=(14, 8))
            ax.plot(frequencies_mhz, dbm, "b-")
            ax.axhline(np.max(dbm) - abs(spec), linestyle="--", color="black", label=f"Spec ({spec:g} dBc)")
            ax.set_xlabel("Frequency (MHz)"); ax.set_ylabel("dBm")
            ax.set_title(f"{rf_mhz:g} MHz {test} at {setpoint:g} W Setpoint - {cycle_suffix}\nParamount")
            ax.grid(True); ax.legend(); fig.tight_layout()
            path = dbm_folder / f"{base}_dBm.png"; fig.savefig(path, dpi=250, bbox_inches="tight"); plt.close(fig); paths.append(path)

        if make_dbc:
            dbc_folder.mkdir(parents=True, exist_ok=True)
            fig, ax = plt.subplots(figsize=(18, 10))
            ax.plot(frequencies_mhz, dbc, "b-")
            ax.axhline(spec, linestyle="--", color="black", linewidth=1.5, label=f"Spec ({spec:g} dBc)")
            detail = peak_details[peak_details["acquisition_id"] == acquisition_id]
            for _, peak in detail.iterrows():
                if peak["classification"] == "CARRIER":
                    ax.plot(peak["frequency_MHz"], peak["amplitude_dBc"], "o", color="red", label="CARRIER")
                elif peak["is_failure"]:
                    ax.plot(peak["frequency_MHz"], peak["amplitude_dBc"], "x", color="red", label="FAIL")
            pass_peaks = detail[(detail["classification"] != "CARRIER") & (~detail["is_failure"])]
            if not pass_peaks.empty:
                ax.plot(pass_peaks["frequency_MHz"], pass_peaks["amplitude_dBc"], "x", color="green", label="PASS")
            if not coupler_data.empty:
                visible = coupler_data[
                    (coupler_data["frequency_Hz"] >= group["spectrum_frequency_Hz"].min()) &
                    (coupler_data["frequency_Hz"] <= group["spectrum_frequency_Hz"].max())
                ]
                if not visible.empty:
                    twin = ax.twinx()
                    twin.plot(visible["frequency_Hz"] / 1e6, visible["correction_dB"], "r-", alpha=0.6)
                    twin.set_ylabel("Coupler correction (dB)")
            ax.set_ylim(-100, 0); ax.set_xlabel("Frequency (MHz)"); ax.set_ylabel("Amplitude (dBc)")
            ax.set_title(f"{rf_mhz:g} MHz {test} at {setpoint:g} W Setpoint - {cycle_suffix}\nParamount - {summary['result']}")
            handles, labels = ax.get_legend_handles_labels()
            unique = dict(zip(labels, handles)); ax.legend(unique.values(), unique.keys(), loc="upper right")
            ax.grid(True); fig.tight_layout()
            path = dbc_folder / f"{base}_dBc.png"; fig.savefig(path, dpi=250, bbox_inches="tight"); plt.close(fig); paths.append(path)
    return paths


def generate_summary_plots(summary, output_folder, dataset_name):
    output_folder = Path(output_folder) / "Summary"
    output_folder.mkdir(parents=True, exist_ok=True)
    paths = []
    plot_summary = summary[summary["analysis_scope"] == "Cycle Average"] if "analysis_scope" in summary and (summary["analysis_scope"] == "Cycle Average").any() else summary
    for test_type, test_data in plot_summary.groupby("test_type"):
        for metric, label in [
            ("worst_non_carrier_peak_dBc", "Worst Non-Carrier Peak (dBc)"),
            ("worst_margin_to_spec_dB", "Worst Margin to Specification (dB)"),
            ("failing_peak_count", "Failing Peak Count"),
            ("carrier_amplitude_dBm", "Carrier Amplitude (dBm)"),
        ]:
            fig, ax = plt.subplots(figsize=(10, 6)); plotted = False
            for frequency, group in test_data.groupby("rf_frequency_Hz"):
                group = group.sort_values("power_setpoint_W")
                values = pd.to_numeric(group[metric], errors="coerce")
                if values.notna().any():
                    ax.plot(group["power_setpoint_W"], values, marker="o", linewidth=2,
                            label=f"{frequency / 1e6:g} MHz")
                    plotted = True
            if not plotted:
                plt.close(fig); continue
            ax.set_title(f"{dataset_name} - {test_type} - {label}")
            ax.set_xlabel("Power Setpoint (W)"); ax.set_ylabel(label); ax.grid(True, alpha=0.3); ax.legend()
            fig.tight_layout(); path = output_folder / f"{test_type}_{metric}.png"
            fig.savefig(path, dpi=250, bbox_inches="tight"); plt.close(fig); paths.append(path)
    return paths


def create_comparison(summary_a, summary_b, name_a, name_b):
    if "analysis_scope" in summary_a and (summary_a["analysis_scope"] == "Cycle Average").any():
        summary_a = summary_a[summary_a["analysis_scope"] == "Cycle Average"].copy()
    if "analysis_scope" in summary_b and (summary_b["analysis_scope"] == "Cycle Average").any():
        summary_b = summary_b[summary_b["analysis_scope"] == "Cycle Average"].copy()
    metrics = [
        "result", "carrier_amplitude_dBm", "worst_non_carrier_peak_dBc",
        "worst_corrected_peak_dBc", "worst_margin_to_spec_dB",
        "failing_peak_count", "passing_peak_count",
    ]
    a = summary_a[COMPARISON_KEYS + metrics].rename(
        columns={metric: f"{name_a} {metric}" for metric in metrics}
    )
    b = summary_b[COMPARISON_KEYS + metrics].rename(
        columns={metric: f"{name_b} {metric}" for metric in metrics}
    )
    merged = a.merge(b, on=COMPARISON_KEYS, how="outer", indicator=True)
    merged["matched_condition"] = merged["_merge"].eq("both")
    merged["availability"] = merged["_merge"].map({
        "both": "Matched", "left_only": f"Only {name_a}", "right_only": f"Only {name_b}"
    })
    merged["pass_fail_changed"] = np.where(
        merged["matched_condition"], merged[f"{name_a} result"] != merged[f"{name_b} result"], False
    )
    for metric in metrics[1:]:
        column_a, column_b = f"{name_a} {metric}", f"{name_b} {metric}"
        difference = merged[column_b] - merged[column_a]
        merged[f"{metric} Difference B-A"] = difference
        merged[f"{metric} Absolute Difference"] = difference.abs()
    return merged.drop(columns=["_merge"]).sort_values(COMPARISON_KEYS).reset_index(drop=True)


def generate_comparison_plots(comparison, name_a, name_b, output_folder):
    output_folder = Path(output_folder) / "Comparison Plots"
    output_folder.mkdir(parents=True, exist_ok=True)
    matched = comparison[comparison["matched_condition"]]
    paths = []
    metrics = [
        ("worst_non_carrier_peak_dBc", "Worst Non-Carrier Peak (dBc)"),
        ("worst_margin_to_spec_dB", "Margin to Specification (dB)"),
        ("failing_peak_count", "Failing Peak Count"),
        ("carrier_amplitude_dBm", "Carrier Amplitude (dBm)"),
    ]
    for (test_type, rf_frequency), condition in matched.groupby(["test_type", "rf_frequency_Hz"]):
        for metric, label in metrics:
            a_col, b_col = f"{name_a} {metric}", f"{name_b} {metric}"
            d_col = f"{metric} Difference B-A"
            group = condition.sort_values("power_setpoint_W")
            if group[a_col].isna().all() and group[b_col].isna().all():
                continue
            fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 8), sharex=True,
                                               gridspec_kw={"height_ratios": [2.2, 1.0]})
            x = group["power_setpoint_W"]
            top.plot(x, group[a_col], marker="o", linewidth=2, label=name_a)
            top.plot(x, group[b_col], marker="s", linestyle="--", linewidth=2, label=name_b)
            bottom.plot(x, group[d_col], marker="o", color="#C00000")
            bottom.axhline(0, color="black", linewidth=1)
            top.set_ylabel(label); bottom.set_ylabel("B - A"); bottom.set_xlabel("Power Setpoint (W)")
            top.grid(True, alpha=0.3); bottom.grid(True, alpha=0.3); top.legend()
            fig.suptitle(f"{test_type} Comparison - {rf_frequency / 1e6:g} MHz")
            fig.tight_layout(); path = output_folder / f"{test_type}_{rf_frequency / 1e6:g}MHz_{metric}.png"
            fig.savefig(path, dpi=250, bbox_inches="tight"); plt.close(fig); paths.append(path)
    return paths


def auto_size_workbook(writer):
    size_workbook_columns(writer, max_width=55)


def write_analysis_outputs(folder, dataset_name, summary, peaks, points, failures, settings, options):
    folder = Path(folder)
    workbook = folder / ANALYSIS_WORKBOOK_NAME
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Acquisition Summary", index=False)
        if "analysis_scope" in summary:
            individual = summary[summary["analysis_scope"] == "Individual Cycle"]
            averages = summary[summary["analysis_scope"] == "Cycle Average"]
            if not individual.empty:
                individual.to_excel(writer, sheet_name="Per-Cycle Summary", index=False)
            if not averages.empty:
                averages.to_excel(writer, sheet_name="Cycle Average Summary", index=False)
        if not peaks.empty:
            peaks.to_excel(writer, sheet_name="Peak Details", index=False)
        if not points.empty:
            points.to_excel(writer, sheet_name="Spectrum Points", index=False)
        if not failures.empty:
            failures.to_excel(writer, sheet_name="Failed Acquisitions", index=False)
        pd.DataFrame({"Setting": list(settings.keys()), "Value": list(settings.values())}).to_excel(
            writer, sheet_name="Run Settings", index=False
        )
        pd.DataFrame({"Setting": list(options.keys()), "Value": list(options.values())}).to_excel(
            writer, sheet_name="Analysis Settings", index=False
        )
        auto_size_workbook(writer)

    report_summary = summary[summary["analysis_scope"] == "Cycle Average"] if "analysis_scope" in summary and (summary["analysis_scope"] == "Cycle Average").any() else summary
    with open(folder / RESULTS_TEXT_NAME, "w") as handle:
        for test_type in ("Harmonics", "Spurious"):
            rows = report_summary[report_summary["test_type"] == test_type]
            if rows.empty:
                continue
            status = "FAIL" if (rows["result"] == "FAIL").any() else "PASS"
            handle.write(f"{test_type} Results: {status}\n\n")
            for _, row in rows[rows["result"] == "FAIL"].iterrows():
                handle.write(f"{row['rf_frequency_MHz']:g}MHz @ {row['power_setpoint_W']:g}W - FAIL\n")

    with open(folder / DETAILS_TEXT_NAME, "w") as handle:
        failure_peaks = peaks[peaks["is_failure"]] if not peaks.empty else pd.DataFrame()
        for acquisition_id, rows in failure_peaks.groupby("acquisition_id") if not failure_peaks.empty else []:
            summary_row = summary[summary["acquisition_id"] == acquisition_id].iloc[0]
            handle.write(
                f"{summary_row['test_type']} {summary_row['rf_frequency_MHz']:g}MHz @ "
                f"{summary_row['power_setpoint_W']:g}W - FAIL Details "
            )
            for _, peak in rows.iterrows():
                handle.write(
                    f":{peak['amplitude_dBc']:.2f}dBc Delta {peak['delta_from_carrier_MHz']:.2f}MHz "
                )
            handle.write("\n")
    return workbook


def write_comparison_workbook(path, comparison, summary_a, summary_b, failures_a, failures_b, settings):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        comparison.to_excel(writer, sheet_name="Condition Comparison", index=False)
        ranking = comparison.copy()
        ranking["ranking_score"] = (
            ranking.filter(like="Absolute Difference").apply(pd.to_numeric, errors="coerce").abs().max(axis=1)
        )
        ranking.sort_values(["pass_fail_changed", "ranking_score"], ascending=[False, False]).to_excel(
            writer, sheet_name="Difference Ranking", index=False
        )
        summary_a.to_excel(writer, sheet_name="A Acquisition Summary", index=False)
        summary_b.to_excel(writer, sheet_name="B Acquisition Summary", index=False)
        failures = pd.concat([failures_a, failures_b], ignore_index=True)
        if not failures.empty:
            failures.to_excel(writer, sheet_name="Failed Acquisitions", index=False)
        pd.DataFrame({"Setting": list(settings.keys()), "Value": list(settings.values())}).to_excel(
            writer, sheet_name="Comparison Settings", index=False
        )
        auto_size_workbook(writer)


def export_powerpoint_package(config, generated_images, summary_a, summary_b=None, comparison=None,
                              workbook_a=None, workbook_b=None, comparison_workbook=None):
    if not powerpoint_export_requested(): return None
    package_text=os.environ.get("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER","").strip()
    analysis_id=os.environ.get("PARAMOUNT_POWERPOINT_ANALYSIS_ID","harmonics_spurious").strip()
    schema=int(os.environ.get("PARAMOUNT_POWERPOINT_SCHEMA_VERSION","1").strip())
    if not package_text: raise ValueError("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER is not set.")
    package=Path(package_text).expanduser().resolve();package.mkdir(parents=True,exist_ok=True)
    manifest_path=package/"manifest.json"
    if manifest_path.exists():manifest_path.unlink()
    images=package/"images";tables=package/"tables"
    if images.exists():shutil.rmtree(images)
    if tables.exists():shutil.rmtree(tables)
    tables.mkdir(parents=True,exist_ok=True)
    summary_a.to_csv(tables/"dataset_a_summary.csv",index=False)
    if summary_b is not None:summary_b.to_csv(tables/"dataset_b_summary.csv",index=False)
    if comparison is not None:comparison.to_csv(tables/"comparison_b_minus_a.csv",index=False)
    slides=[];copied=[]
    for role,source_path in generated_images:
        source=Path(source_path)
        if not source.is_file():continue
        destination_folder=images/role;destination_folder.mkdir(parents=True,exist_ok=True)
        destination=destination_folder/source.name;shutil.copy2(source,destination)
        relative=destination.relative_to(package).as_posix()
        copied.append({"dataset_role":role.upper(),"source":str(source),"package_file":relative})
        slide={"slide_type":"image","title":source.name,"image_file":relative,"dataset_role":role.upper()}
        if role=="comparison":slide["difference_convention"]="B_MINUS_A"
        slides.append(slide)
    metrics={"Analysis mode":config["mode"],"Dataset A":config["name_a"],"Dataset A acquisitions":len(summary_a),"Generated image slides":len(slides)}
    highlights=[f"Dataset A: {config['name_a']}",f"Generated {len(slides)} image slide(s) from this analysis run."]
    dataset_b=None;comparison_info={"completed":False,"difference_convention":"B_MINUS_A"}
    if config["mode"]=="COMPARE":
        dataset_b={"label":config["name_b"],"source_folder":config["path_b"],"workbook":str(workbook_b) if workbook_b else ""}
        metrics.update({"Dataset B":config["name_b"],"Dataset B acquisitions":len(summary_b) if summary_b is not None else 0})
        highlights.append(f"Comparison convention: {config['name_b']} minus {config['name_a']}.")
        comparison_info={"completed":comparison is not None,"difference_convention":"B_MINUS_A","workbook":str(comparison_workbook) if comparison_workbook else ""}
    manifest={"schema_version":schema,"analysis_id":analysis_id,"display_name":"Harmonics and Spurious","status":"completed",
              "analysis_mode":config["mode"],"dataset_label":config["name_a"] if config["mode"]=="SINGLE" else f"{config['name_b']} vs {config['name_a']}",
              "source_folder":config["path_a"],"dataset_a":{"label":config["name_a"],"source_folder":config["path_a"],"workbook":str(workbook_a) if workbook_a else ""},
              "dataset_b":dataset_b,"comparison":comparison_info,"highlights":highlights,"metrics":metrics,"generated_images":copied,"slides":slides}
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return manifest_path


def open_file(path):
    open_with_default_app(path)


def configure_gui():
    root = tk.Tk(); root.title(APP_TITLE); root.geometry("1080x820"); root.minsize(950, 700)
    style = ttk.Style(root)
    try: style.theme_use("vista")
    except tk.TclError: pass
    style.configure("Header.TLabel", font=("Segoe UI", 15, "bold"))
    style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Start.TButton", font=("Segoe UI", 11, "bold"), padding=(14, 8))
    outer = ttk.Frame(root, padding=14); outer.pack(fill="both", expand=True)
    ttk.Label(outer, text=APP_TITLE, style="Header.TLabel").pack(anchor="w")
    ttk.Label(outer, text="Generate the original dBm/dBc analysis, peak classification, PASS/FAIL reports, summaries, and optional two-dataset comparison from saved spectrum data.", wraplength=1000).pack(anchor="w", pady=(2,10))
    notebook=ttk.Notebook(outer); notebook.pack(fill="both",expand=True)
    input_tab=ttk.Frame(notebook,padding=12); specs_tab=ttk.Frame(notebook,padding=12)
    peaks_tab=ttk.Frame(notebook,padding=12); plots_tab=ttk.Frame(notebook,padding=12)
    comparison_tab=ttk.Frame(notebook,padding=12); output_tab=ttk.Frame(notebook,padding=12)
    for tab,label in [(input_tab,"Input Data"),(specs_tab,"Specifications"),(peaks_tab,"Peak Analysis"),(plots_tab,"Plots"),(comparison_tab,"Comparison"),(output_tab,"Output")]: notebook.add(tab,text=label)

    launcher_mode=launcher_default("PARAMOUNT_ANALYSIS_MODE",fallback="INDIVIDUAL").upper()
    initial_mode=launcher_mode if launcher_mode in {"SINGLE","COMPARE"} else "SINGLE"
    initial_path_a=dataset_folder_default("A")
    initial_path_b=dataset_folder_default("B")
    mode=tk.StringVar(value=initial_mode)
    path_a=tk.StringVar(value=initial_path_a)
    path_b=tk.StringVar(value=initial_path_b)
    name_a=tk.StringVar(value=launcher_default("PARAMOUNT_DATASET_A_LABEL",fallback="Configuration A"))
    name_b=tk.StringVar(value=launcher_default("PARAMOUNT_DATASET_B_LABEL",fallback="Configuration B"))
    print(f"Unified Dataset A default received: {initial_path_a or '[none]'}")
    print(f"Unified Dataset B default received: {initial_path_b or '[none]'}")
    preview=tk.StringVar(value="Select an analysis-ready Harmonics and Spurious collection folder.")
    use_recorded=tk.BooleanVar(value=True); apply_coupler=tk.BooleanVar(value=True)
    harmonic_lp=tk.StringVar(value="-50");spurious_lp=tk.StringVar(value="-50");high_power=tk.StringVar(value="-50")
    boundary=tk.StringVar(value="50");coupler_spec=tk.StringVar(value="-60")
    fail_distance=tk.StringVar(value="5");pass_distance=tk.StringVar(value="20");harm_multiplier=tk.StringVar(value="0.95")
    make_dbm=tk.BooleanVar(value=True);make_dbc=tk.BooleanVar(value=True);make_summary=tk.BooleanVar(value=True);open_workbook=tk.BooleanVar(value=True)

    input_tab.columnconfigure(1,weight=1)
    ttk.Label(input_tab,text="Analysis mode",style="Section.TLabel").grid(row=0,column=0,sticky="w")
    mf=ttk.Frame(input_tab);mf.grid(row=1,column=0,columnspan=3,sticky="w",pady=(5,12))
    ttk.Radiobutton(mf,text="Single dataset",variable=mode,value="SINGLE").pack(side="left",padx=(0,20))
    ttk.Radiobutton(mf,text="Compare two hardware configurations",variable=mode,value="COMPARE").pack(side="left")
    ttk.Label(input_tab,text="Configuration A folder").grid(row=2,column=0,sticky="w",pady=5)
    ttk.Entry(input_tab,textvariable=path_a,width=68).grid(row=2,column=1,sticky="ew",pady=5)
    ttk.Label(input_tab,text="Display name").grid(row=3,column=0,sticky="w",pady=5);ttk.Entry(input_tab,textvariable=name_a,width=35).grid(row=3,column=1,sticky="w")
    ttk.Label(input_tab,text="Configuration B folder").grid(row=4,column=0,sticky="w",pady=5)
    b_entry=ttk.Entry(input_tab,textvariable=path_b,width=68);b_entry.grid(row=4,column=1,sticky="ew",pady=5)
    ttk.Label(input_tab,text="Display name").grid(row=5,column=0,sticky="w",pady=5);b_name=ttk.Entry(input_tab,textvariable=name_b,width=35);b_name.grid(row=5,column=1,sticky="w")
    def browse(variable,label,show_preview=False):
        current=variable.get().strip()
        selected=filedialog.askdirectory(title="Select Harmonics and Spurious collection folder",parent=root,mustexist=True,initialdir=current if current and Path(current).is_dir() else None)
        if selected:
            variable.set(selected);label.set(Path(selected).name or label.get())
            if show_preview:
                files=discover_spectrum_files(selected); acquisitions=0;tests=[];freqs=set();powers=set()
                for file in files:
                    try:
                        frame=pd.read_csv(file)
                        acquisitions += frame["acquisition_id"].nunique();tests.extend(frame["test_type"].dropna().unique())
                        freqs.update(pd.to_numeric(frame["rf_frequency_Hz"],errors="coerce").dropna());powers.update(pd.to_numeric(frame["power_setpoint_W"],errors="coerce").dropna())
                    except Exception:pass
                preview.set(f"Spectrum files: {len(files)} | Acquisitions: {acquisitions} | Tests: {', '.join(sorted(set(tests))) or 'None'}\nRF frequencies (MHz): {', '.join(f'{v/1e6:g}' for v in sorted(freqs)) or 'None'}\nPower setpoints (W): {', '.join(f'{v:g}' for v in sorted(powers)) or 'None'}")
    ttk.Button(input_tab,text="Browse...",command=lambda:browse(path_a,name_a,True)).grid(row=2,column=2,padx=(10,0))
    b_button=ttk.Button(input_tab,text="Browse...",command=lambda:browse(path_b,name_b));b_button.grid(row=4,column=2,padx=(10,0))
    if initial_path_a and Path(initial_path_a).is_dir():
        files=discover_spectrum_files(initial_path_a)
        preview.set(
            f"Unified Dataset A default loaded: {initial_path_a}\n"
            f"Spectrum files currently detected: {len(files)}"
        )
    elif initial_path_a:
        preview.set(f"Unified Dataset A default loaded, but the folder does not currently exist:\n{initial_path_a}")
    ttk.Label(input_tab,textvariable=preview,wraplength=920,justify="left",foreground="#444444").grid(row=6,column=0,columnspan=3,sticky="w",pady=(16,0))

    def entry(parent,row,label,var):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky="w",pady=5,padx=(0,10));ttk.Entry(parent,textvariable=var,width=20).grid(row=row,column=1,sticky="w",pady=5)
    ttk.Checkbutton(specs_tab,text="Use active specification values recorded during collection",variable=use_recorded).grid(row=0,column=0,columnspan=3,sticky="w",pady=(0,10))
    entry(specs_tab,1,"Harmonics low-power spec (dBc)",harmonic_lp);entry(specs_tab,2,"Spurious low-power spec (dBc)",spurious_lp)
    entry(specs_tab,3,"High-power spec (dBc)",high_power);entry(specs_tab,4,"Low-power boundary (W)",boundary)
    ttk.Checkbutton(specs_tab,text="Apply copied coupler correction map when available",variable=apply_coupler).grid(row=5,column=0,columnspan=3,sticky="w",pady=(12,4))
    entry(specs_tab,6,"Coupler reference/spec value (dB)",coupler_spec)

    entry(peaks_tab,0,"Failing peak minimum distance (points)",fail_distance);entry(peaks_tab,1,"Passing peak minimum distance (points)",pass_distance);entry(peaks_tab,2,"Harmonic frequency-separation multiplier",harm_multiplier)
    ttk.Label(peaks_tab,text="Defaults reproduce the original script: fail distance 5, pass distance 20, harmonic multiplier 0.95.",foreground="#555555",wraplength=820).grid(row=3,column=0,columnspan=3,sticky="w",pady=(12,0))

    ttk.Checkbutton(plots_tab,text="Generate dBm spectrum plots",variable=make_dbm).pack(anchor="w",pady=5)
    ttk.Checkbutton(plots_tab,text="Generate dBc PASS/FAIL spectrum plots",variable=make_dbc).pack(anchor="w",pady=5)
    ttk.Checkbutton(plots_tab,text="Generate summary trend plots",variable=make_summary).pack(anchor="w",pady=5)

    ttk.Label(comparison_tab,text="Comparison metrics",style="Section.TLabel").pack(anchor="w",pady=(0,8))
    ttk.Label(comparison_tab,text="Matched conditions use test type, RF frequency, setpoint, analyzer span, and RBW. Outputs include PASS/FAIL changes, carrier amplitude, worst non-carrier dBc, corrected worst peak, margin to spec, failing/pass peak counts, B-minus-A metrics, ranking, and frequency-specific overlay/difference plots.",wraplength=870,justify="left").pack(anchor="w")
    ttk.Checkbutton(output_tab,text="Open Configuration A workbook when complete",variable=open_workbook).pack(anchor="w",pady=5)
    ttk.Label(output_tab,text=f"Outputs: {ANALYSIS_WORKBOOK_NAME}, {RESULTS_TEXT_NAME}, {DETAILS_TEXT_NAME}, {PLOT_FOLDER_NAME}, and optional {COMPARISON_WORKBOOK_NAME}.",wraplength=880,justify="left").pack(anchor="w",pady=(14,0))

    result={"accepted":False}
    def number(var,label,minimum=None,integer=False):
        try:value=float(var.get().strip())
        except ValueError as error:raise ValueError(f"{label} must be numeric.") from error
        if not np.isfinite(value):raise ValueError(f"{label} must be finite.")
        if integer:
            if not value.is_integer():raise ValueError(f"{label} must be a whole number.")
            value=int(value)
        if minimum is not None and value<minimum:raise ValueError(f"{label} must be at least {minimum}.")
        return value
    def refresh(*_):
        state="normal" if mode.get()=="COMPARE" else "disabled";b_entry.configure(state=state);b_name.configure(state=state);b_button.configure(state=state)
    mode.trace_add("write",refresh)
    def start():
        try:
            a=Path(path_a.get().strip()).resolve()
            if not discover_spectrum_files(a):raise ValueError("Select a valid Configuration A collection folder.")
            b=None
            if mode.get()=="COMPARE":
                b=Path(path_b.get().strip()).resolve()
                if not discover_spectrum_files(b):raise ValueError("Select a valid Configuration B collection folder.")
                if os.path.normcase(str(a))==os.path.normcase(str(b)):raise ValueError("Select two different datasets.")
            result.update({"accepted":True,"mode":mode.get(),"path_a":str(a),"path_b":str(b) if b else None,"name_a":name_a.get().strip() or a.name,"name_b":name_b.get().strip() or (b.name if b else "Configuration B"),
                "use_recorded_specs":bool(use_recorded.get()),"harmonic_lp":number(harmonic_lp,"Harmonics low-power spec"),"spurious_lp":number(spurious_lp,"Spurious low-power spec"),"high_power":number(high_power,"High-power spec"),"low_power_boundary_w":number(boundary,"Low-power boundary",0),"apply_coupler":bool(apply_coupler.get()),"coupler_reference_spec":number(coupler_spec,"Coupler reference"),"failing_peak_distance":number(fail_distance,"Fail distance",1,True),"passing_peak_distance":number(pass_distance,"Pass distance",1,True),"harmonic_multiplier":number(harm_multiplier,"Harmonic multiplier",0),"make_dbm":bool(make_dbm.get()),"make_dbc":bool(make_dbc.get()),"make_summary":bool(make_summary.get()),"open_workbook":bool(open_workbook.get())})
            root.destroy()
        except Exception as error:messagebox.showerror("Invalid analysis settings",str(error),parent=root)
    buttons=ttk.Frame(outer);buttons.pack(fill="x",pady=(12,0));ttk.Button(buttons,text="Cancel",command=root.destroy).pack(side="right",padx=(8,0));ttk.Button(buttons,text="Start Analysis",style="Start.TButton",command=start).pack(side="right")
    root.protocol("WM_DELETE_WINDOW",root.destroy);refresh();root.mainloop();return result


def process_dataset(folder, dataset_name, options, progress=None, phase_start=0.0, phase_end=100.0):
    _scaled_progress(progress,phase_start,phase_end,0.0,f"Loading {dataset_name}",str(folder))
    data, load_failures, settings = load_dataset(folder, dataset_name)
    _scaled_progress(progress,phase_start,phase_end,0.08,f"Preparing {dataset_name}","Building cycle-average spectra")
    coupler_path = Path(folder) / COUPLER_FILE_NAME
    coupler_data = read_coupler_map(coupler_path) if options["apply_coupler"] else pd.DataFrame()
    data = data.copy()
    data["analysis_scope"] = "Individual Cycle"
    data["cycles_in_average"] = 1
    average_data = build_cycle_average_spectra(data)
    analysis_data = pd.concat([data, average_data], ignore_index=True, sort=False)
    summary, peaks, points, analysis_failures = analyze_dataset(
        analysis_data,options,coupler_data,
        progress_callback=lambda fraction,heading,detail:_scaled_progress(progress,phase_start,phase_end,0.12+0.48*fraction,f"{dataset_name}: {heading}",detail),
    )
    failures = pd.concat([load_failures, analysis_failures], ignore_index=True, sort=False)
    plot_folder = Path(folder) / PLOT_FOLDER_NAME
    _scaled_progress(progress,phase_start,phase_end,0.62,f"Generating {dataset_name} acquisition plots","Creating selected dBm and dBc plots")
    generated_images = generate_acquisition_plots(
        analysis_data, summary, peaks, plot_folder, options["make_dbm"], options["make_dbc"], coupler_data
    )
    if options["make_summary"]:
        _scaled_progress(progress,phase_start,phase_end,0.82,f"Generating {dataset_name} summary plots","Creating trend plots")
        generated_images.extend(generate_summary_plots(summary, plot_folder, dataset_name))
    _scaled_progress(progress,phase_start,phase_end,0.93,f"Writing {dataset_name} workbook",ANALYSIS_WORKBOOK_NAME)
    workbook = write_analysis_outputs(folder, dataset_name, summary, peaks, points, failures, settings, options)
    _scaled_progress(progress,phase_start,phase_end,1.0,f"{dataset_name} complete",str(workbook))
    return analysis_data, summary, peaks, points, failures, settings, workbook, generated_images


def run_analysis():
    config=configure_gui()
    if not config.get("accepted"):return
    progress=AnalysisProgress()
    try:
        options={key:config[key] for key in ["use_recorded_specs","harmonic_lp","spurious_lp","high_power","low_power_boundary_w","apply_coupler","coupler_reference_spec","failing_peak_distance","passing_peak_distance","harmonic_multiplier","make_dbm","make_dbc","make_summary"]}
        if config["mode"]=="COMPARE":a_start,a_end,b_start,b_end=0,42,42,84
        else:a_start,a_end,b_start,b_end=0,90,0,0
        _,summary_a,_,_,fail_a,settings_a,workbook_a,images_a=process_dataset(config["path_a"],config["name_a"],options,progress,a_start,a_end)
        generated_images=[("dataset_a",path) for path in images_a]
        summary_b=None;comparison=None;workbook_b=None;comparison_workbook=None
        if config["mode"]=="COMPARE":
            _,summary_b,_,_,fail_b,_,workbook_b,images_b=process_dataset(config["path_b"],config["name_b"],options,progress,b_start,b_end)
            generated_images.extend(("dataset_b",path) for path in images_b)
            progress.update(86,"Comparing datasets","Matching conditions and calculating B minus A")
            comparison=create_comparison(summary_a,summary_b,config["name_a"],config["name_b"])
            folder=Path(config["path_a"])/COMPARISON_FOLDER_NAME;folder.mkdir(parents=True,exist_ok=True)
            comparison_workbook=folder/COMPARISON_WORKBOOK_NAME
            settings={"Configuration A":config["name_a"],"Configuration A Folder":config["path_a"],"Configuration B":config["name_b"],"Configuration B Folder":config["path_b"],"Difference Convention":"Configuration B minus Configuration A","Matching Conditions":", ".join(COMPARISON_KEYS)}
            progress.update(89,"Writing comparison workbook",str(comparison_workbook))
            write_comparison_workbook(comparison_workbook,comparison,summary_a,summary_b,fail_a,fail_b,settings)
            progress.update(92,"Generating comparison plots","Creating overlays and B-minus-A plots")
            generated_images.extend(("comparison",path) for path in generate_comparison_plots(comparison,config["name_a"],config["name_b"],folder))
        package_warning=None
        if powerpoint_export_requested():
            try:
                progress.update(97,"Packaging PowerPoint assets","Copying current-run images and tables")
                manifest_path=export_powerpoint_package(config,generated_images,summary_a,summary_b,comparison,workbook_a,workbook_b,comparison_workbook)
                if manifest_path:print(f"PowerPoint package manifest saved to: {manifest_path}")
            except Exception as error:
                package_warning=f"PowerPoint package export warning: {error}";print(package_warning,file=sys.stderr)
        progress.update(100,"Analysis complete","All requested outputs have been generated")
        if config["open_workbook"]:open_file(workbook_a)
        progress.close()
        root=tk.Tk();root.withdraw();message=f"Harmonics and Spurious analysis is complete.\n\nConfiguration A workbook:\n{workbook_a}"
        if comparison_workbook:message+=f"\n\nComparison workbook:\n{comparison_workbook}"
        if package_warning:message+=f"\n\n{package_warning}\nNormal analysis outputs were still completed."
        messagebox.showinfo("Analysis complete",message,parent=root);root.destroy()
    except Exception:
        progress.close();raise


if __name__ == "__main__":
    run_analysis()
