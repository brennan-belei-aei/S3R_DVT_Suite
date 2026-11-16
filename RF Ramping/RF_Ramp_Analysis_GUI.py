"""RF ramp verification analysis GUI.

Consumes RF_Ramp_Collection_GUI.py run folders and supports standalone use or
launching from the Paramount unified analysis GUI through PARAMOUNT_* variables.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
import traceback
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import matplotlib
matplotlib.use("Agg")

APP_TITLE = "RF Ramp Verification - Analysis"
ANALYSIS_ID = "rf_ramp_verification"


def safe_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", str(value)).strip().rstrip(".") or "Dataset"


def read_waveform(path: Path):
    data = np.genfromtxt(path, delimiter=",", names=True, encoding="utf-8-sig")
    if data.size == 0 or not data.dtype.names or len(data.dtype.names) < 2:
        raise ValueError("Waveform CSV is empty or does not contain time and voltage columns.")
    names = data.dtype.names
    t = np.atleast_1d(np.asarray(data[names[0]], dtype=float))
    y = np.atleast_1d(np.asarray(data[names[1]], dtype=float))
    good = np.isfinite(t) & np.isfinite(y)
    t, y = t[good], y[good]
    if len(t) < 20:
        raise ValueError("Waveform contains fewer than 20 valid samples.")
    order = np.argsort(t)
    t, y = t[order], y[order]
    keep = np.concatenate(([True], np.diff(t) > 0))
    t, y = t[keep], y[keep]
    if len(t) < 20:
        raise ValueError("Waveform has too few strictly increasing time samples.")
    return t, y


def moving_average(y, points):
    points = max(1, min(int(points), len(y) // 5))
    if points <= 1:
        return y.copy()
    kernel = np.ones(points, dtype=float) / points
    return np.convolve(y, kernel, mode="same")


def crossing_candidates(t, y, level, direction):
    if direction == "RISE":
        idx = np.flatnonzero((y[:-1] < level) & (y[1:] >= level))
    else:
        idx = np.flatnonzero((y[:-1] > level) & (y[1:] <= level))
    out = []
    for i in idx:
        dy = y[i + 1] - y[i]
        tc = t[i] if dy == 0 else t[i] + (level - y[i]) * (t[i + 1] - t[i]) / dy
        out.append((int(i), float(tc)))
    return out


def select_transition(t, y, direction, start_v, end_v):
    """Locate the 10% and 90% crossings belonging to one monotonic transition."""
    delta = end_v - start_v
    level10 = start_v + 0.10 * delta
    level90 = start_v + 0.90 * delta
    c10 = crossing_candidates(t, y, level10, direction)
    c90 = crossing_candidates(t, y, level90, direction)
    pairs = []
    for i10, t10 in c10:
        for i90, t90 in c90:
            if t90 <= t10:
                continue
            duration = t90 - t10
            if duration <= 0:
                continue
            segment = y[i10:i90 + 2]
            if len(segment) < 3:
                continue
            diffs = np.diff(segment)
            monotonic_fraction = float(np.mean(diffs >= 0)) if direction == "RISE" else float(np.mean(diffs <= 0))
            pairs.append((monotonic_fraction, -duration, i10, i90, t10, t90))
    if not pairs:
        raise ValueError("Could not find a valid ordered 10% to 90% transition.")
    pairs.sort(reverse=True)
    _, _, i10, i90, t10, t90 = pairs[0]
    return i10, i90, t10, t90, level10, level90


def correct_legacy_byte_order(y):
    """Repair CSVs collected with the former hard-coded MSB binary decoding."""
    unique = np.unique(y)
    differences = np.diff(unique)
    positive = differences[differences > 1e-12]
    if len(positive) == 0:
        return y.copy(), False
    quantization = float(np.min(positive))
    # Wrong-endian Tektronix int16 data typically spans nearly all 65536 codes,
    # while the corrected signal occupies a small physical voltage range.
    code_span = (float(np.max(y)) - float(np.min(y))) / quantization
    if code_span < 60000:
        return y.copy(), False
    codes = np.rint(y / quantization)
    if np.max(np.abs(codes)) > 32768.5:
        return y.copy(), False
    raw = codes.astype(np.int16)
    repaired = raw.byteswap().astype(np.int16).astype(float) * quantization
    if np.ptp(repaired) >= 0.5 * np.ptp(y):
        return y.copy(), False
    return repaired, True


def prepare_measurement_signal(t, y):
    """Convert carrier-like CH1 data to a local RMS envelope; leave DC monitor data unchanged."""
    edge_n = max(20, int(len(y) * 0.10))
    raw_delta = abs(float(np.median(y[-edge_n:]) - np.median(y[:edge_n])))
    raw_noise = max(float(np.std(y[:edge_n])), float(np.std(y[-edge_n:])))
    if raw_noise <= max(0.01, 2.0 * raw_delta):
        return y.copy(), "DIRECT_VOLTAGE", 1
    dt = float(np.median(np.diff(t)))
    # A 20 ms RMS window rejects the RF carrier while preserving 200 ms to 5 s ramps.
    points = max(5, int(round(0.020 / dt))) if dt > 0 else max(5, len(y) // 500)
    points = min(points, max(5, len(y) // 20))
    kernel = np.ones(points, dtype=float) / points
    envelope = np.sqrt(np.convolve(np.square(y), kernel, mode="same"))
    return envelope, "RMS_ENVELOPE", points


def analyze_waveform(t, y, direction, smoothing_pct=0.05):
    direction = direction.upper()
    if direction not in {"RISE", "FALL"}:
        raise ValueError(f"Unsupported direction: {direction}")

    corrected_y, legacy_byte_order_corrected = correct_legacy_byte_order(y)
    measurement, signal_method, envelope_points = prepare_measurement_signal(t, corrected_y)
    smooth_points = max(1, int(round(len(measurement) * smoothing_pct / 100.0)))
    ys = moving_average(measurement, smooth_points)
    edge_n = max(10, int(len(y) * 0.10))
    first = float(np.median(ys[:edge_n]))
    last = float(np.median(ys[-edge_n:]))
    start_v, end_v = (first, last)
    delta = end_v - start_v
    noise_start = float(np.std(ys[:edge_n], ddof=1)) if edge_n > 1 else 0.0
    noise_end = float(np.std(ys[-edge_n:], ddof=1)) if edge_n > 1 else 0.0
    minimum_delta = max(0.005, 6.0 * max(noise_start, noise_end))
    if direction == "RISE" and delta <= minimum_delta:
        raise ValueError(f"No valid rising transition. Endpoint delta is {delta:.6g} V.")
    if direction == "FALL" and delta >= -minimum_delta:
        raise ValueError(f"No valid falling transition. Endpoint delta is {delta:.6g} V.")

    i10, i90, t10, t90, level10, level90 = select_transition(t, ys, direction, start_v, end_v)
    measured_10_90_s = t90 - t10

    lo, hi = sorted((level10, level90))
    fit_mask = (ys >= lo) & (ys <= hi) & (np.arange(len(ys)) >= i10) & (np.arange(len(ys)) <= i90 + 1)
    if int(fit_mask.sum()) < 3:
        raise ValueError("Too few samples in the central 10% to 90% ramp region.")
    slope, intercept = np.polyfit(t[fit_mask], ys[fit_mask], 1)
    if slope == 0:
        raise ValueError("Central ramp fit has zero slope.")
    t0 = (start_v - intercept) / slope
    t100 = (end_v - intercept) / slope
    measured_0_100_s = abs(float(t100 - t0))
    fitted = slope * t[fit_mask] + intercept
    ss_res = float(np.sum((ys[fit_mask] - fitted) ** 2))
    ss_tot = float(np.sum((ys[fit_mask] - np.mean(ys[fit_mask])) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot else math.nan

    expected_peak = max(start_v, end_v)
    if direction == "RISE":
        peak = float(np.max(ys[i90:]))
        overshoot_pct = 100.0 * (peak - end_v) / abs(delta)
    else:
        trough = float(np.min(ys[i90:]))
        overshoot_pct = 100.0 * (end_v - trough) / abs(delta)

    dt = float(np.median(np.diff(t)))
    return {
        "baseline_start_v": start_v,
        "baseline_end_v": end_v,
        "transition_delta_v": delta,
        "start_noise_rms_v": noise_start,
        "end_noise_rms_v": noise_end,
        "level_10_v": level10,
        "level_90_v": level90,
        "t10_s": t10,
        "t90_s": t90,
        "fit_slope_v_per_s": float(slope),
        "fit_intercept_v": float(intercept),
        "fit_t0_s": float(t0),
        "fit_t100_s": float(t100),
        "measured_10_90_s": measured_10_90_s,
        "estimated_0_100_s": measured_0_100_s,
        "fit_r_squared": r_squared,
        "samples_across_10_90": int(round(measured_10_90_s / dt)) if dt > 0 else 0,
        "sample_interval_s": dt,
        "overshoot_pct": overshoot_pct,
        "legacy_byte_order_corrected": legacy_byte_order_corrected,
        "signal_method": signal_method,
        "envelope_points": envelope_points,
        "smoothing_points": smooth_points,
        "expected_peak_v_from_waveform": expected_peak,
    }


def load_index(folder: Path):
    path = folder / "capture_index.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path.name} in {folder}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{path.name} contains no captures.")
    return rows


def write_csv(path, rows):
    if not rows:
        return
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_waveform_plot(t, y, source, metrics, result_row, output_path, error_text=None):
    """Save one annotated PNG for every analyzed waveform, including invalid captures."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 6.5))
    signal_method = metrics.get("signal_method") if metrics else None
    if signal_method == "RMS_ENVELOPE":
        corrected_y, _legacy_fixed = correct_legacy_byte_order(y)
        plotted, _method, _points = prepare_measurement_signal(t, corrected_y)
        decimation = max(1, len(t) // 12000)
        ax.plot(t[::decimation], corrected_y[::decimation], color="#9ecae1", linewidth=0.35, alpha=0.45, label="Raw CH1 carrier")
        ax.plot(t, plotted, color="#1f77b4", linewidth=1.2, label="20 ms RMS envelope used for analysis")
    else:
        ax.plot(t, y, color="#1f77b4", linewidth=1.0, label="Captured CH1 waveform")
    direction = source.get("direction", "UNKNOWN")
    high_w = float(source.get("high_w", 0) or 0)
    ramp_ms = float(source.get("ramp_ms", 0) or 0)
    repetition = source.get("repetition", "")

    if metrics:
        start_v = float(metrics["baseline_start_v"])
        end_v = float(metrics["baseline_end_v"])
        level10 = float(metrics["level_10_v"])
        level90 = float(metrics["level_90_v"])
        t10 = float(metrics["t10_s"])
        t90 = float(metrics["t90_s"])
        definition = result_row.get("selected_definition", "10_TO_90")
        ax.axhline(start_v, color="#777777", linestyle=":", linewidth=1.0, label="Measured 0% / 100% endpoint levels")
        ax.axhline(end_v, color="#777777", linestyle=":", linewidth=1.0)
        if definition == "10_TO_90":
            ax.axhline(level10, color="#ff7f0e", linestyle="--", linewidth=1.0, label="10% / 90% thresholds")
            ax.axhline(level90, color="#ff7f0e", linestyle="--", linewidth=1.0)
            ax.axvline(t10, color="#2ca02c", linestyle="--", linewidth=1.1, label="Measured t10 / t90 crossings")
            ax.axvline(t90, color="#2ca02c", linestyle="--", linewidth=1.1)
            ax.axvspan(t10, t90, color="#2ca02c", alpha=0.08, label="Reported 10% to 90% interval")
        else:
            t0 = float(metrics["fit_t0_s"])
            t100 = float(metrics["fit_t100_s"])
            slope = float(metrics["fit_slope_v_per_s"])
            intercept = float(metrics["fit_intercept_v"])
            fit_left, fit_right = sorted((t0, t100))
            fit_time = np.linspace(fit_left, fit_right, 250)
            ax.plot(fit_time, slope * fit_time + intercept, color="#9467bd", linewidth=2.0,
                    label="Linear fit extrapolated from 0% to 100%")
            ax.axvline(t0, color="#d62728", linestyle="--", linewidth=1.2, label="Extrapolated t0 / t100")
            ax.axvline(t100, color="#d62728", linestyle="--", linewidth=1.2)
            ax.axvspan(fit_left, fit_right, color="#d62728", alpha=0.07, label="Reported 0% to 100% interval")
            # Keep the internal fit-region boundaries visible but visually secondary.
            ax.axvline(t10, color="#ff7f0e", linestyle=":", linewidth=0.9, alpha=0.65,
                       label="10% / 90% fit-region boundaries")
            ax.axvline(t90, color="#ff7f0e", linestyle=":", linewidth=0.9, alpha=0.65)
        measured_ms = float(result_row.get("measured_selected_ms", float("nan")))
        error_pct = float(result_row.get("error_pct", float("nan")))
        status = result_row.get("pass_fail", "")
        annotation = (
            f"Definition: {result_row.get('selected_definition', '')}\n"
            f"Commanded full ramp: {ramp_ms:g} ms\n"
            f"Expected by definition: {float(result_row.get('expected_selected_ms', float('nan'))):.3f} ms\n"
            f"Measured by definition: {measured_ms:.3f} ms\n"
            f"Error: {error_pct:+.2f}%\n"
            f"Signal: {metrics.get('signal_method', '')}\n"
            f"Fit R²: {float(metrics['fit_r_squared']):.4f}\n"
            f"Result: {status}"
        )
        ax.text(0.985, 0.97, annotation, transform=ax.transAxes, ha="right", va="top",
                bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.88}, fontsize=9)
    else:
        ax.text(0.985, 0.97, f"INVALID\n{error_text or 'Analysis failed'}", transform=ax.transAxes,
                ha="right", va="top", wrap=True,
                bbox={"boxstyle": "round", "facecolor": "#ffe6e6", "alpha": 0.9}, fontsize=9)

    transition = f"0 to {high_w:g} W" if direction == "RISE" else f"{high_w:g} to 0 W"
    selected_definition = result_row.get("selected_definition", "")
    method_label = "10% to 90% threshold crossings" if selected_definition == "10_TO_90" else "0% to 100% linear-fit extrapolation"
    ax.set_title(f"RF Ramp {direction}: {transition}, {ramp_ms:g} ms, Rep {repetition}\nMethod: {method_label}")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("CH1 Voltage (V), 50 ohm input")
    ax.grid(True, alpha=0.35)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def analyze_dataset(folder, label, definition, tolerance_pct, fixed_tolerance_ms, min_samples, smoothing_pct, output, progress_callback=None):
    results = []
    sources = load_index(folder)
    waveform_plot_dir = output / 'Analyzed_Waveforms'
    waveform_plot_dir.mkdir(parents=True, exist_ok=True)
    for item_index, source in enumerate(sources, 1):
        if progress_callback:
            progress_callback(item_index - 1, len(sources), source.get('waveform_file', ''))
        row = dict(source)
        row["dataset_label"] = label
        t = y = metrics = None
        analysis_error = None
        row["selected_definition"] = definition
        try:
            waveform = folder / source["waveform_file"]
            t, y = read_waveform(waveform)
            metrics = analyze_waveform(t, y, source["direction"], smoothing_pct)
            commanded_ms = float(source["ramp_ms"])
            expected_ms = commanded_ms * 0.8 if definition == "10_TO_90" else commanded_ms
            measured_s = metrics["measured_10_90_s"] if definition == "10_TO_90" else metrics["estimated_0_100_s"]
            measured_ms = 1000.0 * measured_s
            error_ms = measured_ms - expected_ms
            error_pct = 100.0 * error_ms / expected_ms
            limit_ms = max(expected_ms * tolerance_pct / 100.0, fixed_tolerance_ms)
            resolution_ok = metrics["samples_across_10_90"] >= min_samples
            fit_ok = not math.isnan(metrics["fit_r_squared"]) and metrics["fit_r_squared"] >= 0.90
            passed = abs(error_ms) <= limit_ms and resolution_ok and fit_ok
            row.update(metrics)
            row.update({
                "commanded_ramp_ms": commanded_ms,
                "expected_selected_ms": expected_ms,
                "measured_selected_ms": measured_ms,
                "error_ms": error_ms,
                "error_pct": error_pct,
                "tolerance_limit_ms": limit_ms,
                "resolution_adequate": resolution_ok,
                "fit_adequate": fit_ok,
                "pass_fail": "PASS" if passed else "FAIL",
                "analysis_status": "OK",
            })
        except Exception as exc:
            analysis_error = str(exc)
            row.update({"pass_fail": "INVALID", "analysis_status": analysis_error})
        plot_stem = safe_name(Path(source.get('waveform_file', f'waveform_{item_index:04d}')).stem)
        plot_path = waveform_plot_dir / f'{plot_stem}_Analyzed.png'
        try:
            if t is None or y is None:
                t, y = read_waveform(folder / source['waveform_file'])
            create_waveform_plot(t, y, source, metrics, row, plot_path, analysis_error)
            row['analyzed_waveform_plot'] = str(plot_path.relative_to(output))
        except Exception as plot_exc:
            row['plot_status'] = f'Plot failed: {plot_exc}'
        results.append(row)
        if progress_callback:
            progress_callback(item_index, len(sources), source.get('waveform_file', ''))

    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "RF_Ramp_Results.csv", results)

    valid = [r for r in results if r.get("pass_fail") in {"PASS", "FAIL"}]
    groups = {}
    for row in valid:
        key = (row["dataset_label"], row["direction"], float(row["high_w"]), float(row["ramp_ms"]))
        groups.setdefault(key, []).append(row)
    summary = []
    for key, items in sorted(groups.items()):
        values = np.asarray([float(x["measured_selected_ms"]) for x in items], dtype=float)
        errors = np.asarray([float(x["error_pct"]) for x in items], dtype=float)
        summary.append({
            "dataset_label": key[0], "direction": key[1], "low_w": 0.0,
            "high_w": key[2], "commanded_ramp_ms": key[3], "definition": definition,
            "average_measured_ms": float(values.mean()), "min_measured_ms": float(values.min()),
            "max_measured_ms": float(values.max()),
            "std_dev_ms": float(values.std(ddof=1)) if len(values) > 1 else 0.0,
            "average_error_pct": float(errors.mean()), "count": len(values),
            "failures": sum(x["pass_fail"] == "FAIL" for x in items),
        })
    write_csv(output / "RF_Ramp_Summary.csv", summary)
    create_plots(valid, output, definition)
    create_report(output, folder, label, definition, tolerance_pct, fixed_tolerance_ms, min_samples, results, summary)
    return results, summary


def create_plots(rows, output, definition):
    if not rows:
        return
    import matplotlib.pyplot as plt
    for direction in ("RISE", "FALL"):
        subset = [r for r in rows if r["direction"] == direction]
        if not subset:
            continue
        fig, ax = plt.subplots(figsize=(10, 6))
        for power in sorted({float(r["high_w"]) for r in subset}):
            p = [r for r in subset if float(r["high_w"]) == power]
            x = np.asarray([float(r["expected_selected_ms"]) for r in p])
            y = np.asarray([float(r["measured_selected_ms"]) for r in p])
            order = np.argsort(x)
            ax.plot(x[order], y[order], "o-", label=f"0 to {power:g} W" if direction == "RISE" else f"{power:g} to 0 W")
        all_x = np.asarray([float(r["expected_selected_ms"]) for r in subset])
        lo, hi = float(all_x.min()), float(all_x.max())
        ax.plot([lo, hi], [lo, hi], "k--", label="Ideal")
        ax.set_title(f"RF Ramp {direction} - {definition.replace('_', ' to ')}")
        ax.set_xlabel("Commanded absolute ramp time (ms)")
        ax.set_ylabel("Measured ramp time (ms)")
        ax.grid(True)
        ax.legend()
        fig.tight_layout()
        fig.savefig(output / f"RF_Ramp_{direction}.png", dpi=160)
        plt.close(fig)


def create_report(output, source, label, definition, tolerance_pct, fixed_ms, min_samples, rows, summary):
    report = {
        "schema_version": 2,
        "analysis_id": ANALYSIS_ID,
        "generated": datetime.now().isoformat(),
        "source_folder": str(source),
        "dataset_label": label,
        "definition": definition,
        "tolerance_pct": tolerance_pct,
        "fixed_tolerance_ms": fixed_ms,
        "minimum_samples_across_10_90": min_samples,
        "captures": len(rows),
        "passes": sum(r.get("pass_fail") == "PASS" for r in rows),
        "failures": sum(r.get("pass_fail") == "FAIL" for r in rows),
        "invalid": sum(r.get("pass_fail") == "INVALID" for r in rows),
        "summary_rows": len(summary),
    }
    (output / "analysis_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def export_package(output, all_rows):
    if os.getenv("PARAMOUNT_POWERPOINT_EXPORT_ENABLED", "0") != "1":
        return
    package = Path(os.getenv("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER", str(output)))
    package.mkdir(parents=True, exist_ok=True)
    assets = []
    for source in output.rglob("*.png"):
        destination = package / f"{safe_name(source.parent.name)}_{source.name}"
        destination.write_bytes(source.read_bytes())
        assets.append({"type": "image", "path": destination.name, "title": source.stem})
    manifest = {
        "schema_version": int(os.getenv("PARAMOUNT_POWERPOINT_SCHEMA_VERSION", "1")),
        "analysis_id": os.getenv("PARAMOUNT_POWERPOINT_ANALYSIS_ID", ANALYSIS_ID),
        "display_name": "RF Ramp Verification",
        "generated": datetime.now().isoformat(),
        "summary": {
            "captures": len(all_rows),
            "passes": sum(r.get("pass_fail") == "PASS" for r in all_rows),
            "failures": sum(r.get("pass_fail") == "FAIL" for r in all_rows),
            "invalid": sum(r.get("pass_fail") == "INVALID" for r in all_rows),
        },
        "assets": assets,
    }
    (package / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("900x620")
        mode = os.getenv("PARAMOUNT_ANALYSIS_MODE", "INDIVIDUAL").upper()
        mode = "COMPARE" if mode == "COMPARE" else "SINGLE"
        self.v = {
            "mode": tk.StringVar(value=mode),
            "a": tk.StringVar(value=os.getenv("PARAMOUNT_DATASET_A_FOLDER", "")),
            "b": tk.StringVar(value=os.getenv("PARAMOUNT_DATASET_B_FOLDER", "")),
            "label_a": tk.StringVar(value=os.getenv("PARAMOUNT_DATASET_A_LABEL", "Dataset A")),
            "label_b": tk.StringVar(value=os.getenv("PARAMOUNT_DATASET_B_LABEL", "Dataset B")),
            "definition": tk.StringVar(value="10_TO_90"),
            "tolerance_pct": tk.StringVar(value="5"),
            "fixed_tolerance_ms": tk.StringVar(value="10"),
            "min_samples": tk.StringVar(value="100"),
            "smoothing_pct": tk.StringVar(value="0.05"),
            "output": tk.StringVar(value=""),
        }
        self.status = tk.StringVar(value="Ready.")
        self.progress = tk.DoubleVar(value=0.0)
        self.progress_text = tk.StringVar(value="0 of 0 (0%)")
        self.build()

    def build(self):
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=APP_TITLE, font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        fields = [
            ("mode", "Mode"), ("a", "Dataset A run folder"), ("label_a", "Dataset A label"),
            ("b", "Dataset B run folder"), ("label_b", "Dataset B label"),
            ("definition", "Official measured ramp definition"),
            ("tolerance_pct", "Tolerance (%)"),
            ("fixed_tolerance_ms", "Minimum absolute tolerance (ms)"),
            ("min_samples", "Minimum samples across 10% to 90%"),
            ("smoothing_pct", "Waveform smoothing window (% of record)"),
            ("output", "Output folder (blank = Dataset A/RF_Ramp_Analysis)"),
        ]
        for row, (key, label) in enumerate(fields, 1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5)
            if key == "mode":
                widget = ttk.Combobox(frame, textvariable=self.v[key], values=("SINGLE", "COMPARE"), state="readonly")
            elif key == "definition":
                widget = ttk.Combobox(frame, textvariable=self.v[key], values=("10_TO_90", "0_TO_100"), state="readonly")
            else:
                widget = ttk.Entry(frame, textvariable=self.v[key])
            widget.grid(row=row, column=1, sticky="ew", padx=8, pady=5)
            if key in {"a", "b", "output"}:
                ttk.Button(frame, text="Browse...", command=lambda k=key: self.browse(k)).grid(row=row, column=2)
        ttk.Label(frame, text="10_TO_90 uses interpolated threshold crossings. 0_TO_100 fits the central 10% to 90% region and extrapolates to both stable endpoints. An annotated PNG is saved for every analyzed waveform.", wraplength=820).grid(row=12, column=0, columnspan=3, sticky="w", pady=10)
        progress_frame = ttk.Frame(frame); progress_frame.grid(row=13, column=0, columnspan=3, sticky="ew", pady=(4, 2)); progress_frame.columnconfigure(0, weight=1)
        ttk.Progressbar(progress_frame, variable=self.progress, maximum=100, mode="determinate").grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_frame, textvariable=self.progress_text, width=18, anchor="e").grid(row=0, column=1, padx=(8, 0))
        ttk.Label(frame, textvariable=self.status, wraplength=820).grid(row=14, column=0, columnspan=3, sticky="w", pady=8)
        self.run_button = ttk.Button(frame, text="Run Analysis", command=self.run); self.run_button.grid(row=15, column=2, sticky="e")

    def browse(self, key):
        initial = self.v[key].get() or str(Path.home())
        selected = filedialog.askdirectory(parent=self.root, initialdir=initial)
        if selected:
            self.v[key].set(selected)

    def run(self):
        """Validate GUI input on the UI thread, then analyze in a worker thread."""
        try:
            config = {key: variable.get() for key, variable in self.v.items()}
            folder_a = Path(config["a"])
            if not folder_a.is_dir():
                raise ValueError("Select a valid Dataset A collection run folder.")
            if config["mode"] == "COMPARE":
                folder_b = Path(config["b"])
                if not folder_b.is_dir() or folder_b.resolve() == folder_a.resolve():
                    raise ValueError("Select a different valid Dataset B collection run folder.")
            float(config["tolerance_pct"]); float(config["fixed_tolerance_ms"])
            int(config["min_samples"]); float(config["smoothing_pct"])
        except Exception as exc:
            messagebox.showerror("Invalid analysis settings", str(exc), parent=self.root)
            return
        self.run_button.configure(state="disabled")
        self.progress.set(0.0); self.progress_text.set("Preparing...")
        self.status.set("Starting analysis worker...")
        threading.Thread(target=self._run_worker, args=(config,), daemon=True).start()

    def _post_progress(self, overall, total, dataset_label, filename):
        pct = 100.0 * overall / max(1, total)
        displayed = Path(filename).name if filename else "Preparing dataset"
        def update():
            self.progress.set(pct)
            self.progress_text.set(f"{overall} of {total} ({pct:.0f}%)")
            self.status.set(f"Analyzing {dataset_label}: {displayed}")
        self.root.after(0, update)

    def _run_worker(self, config):
        try:
            folder_a = Path(config["a"])
            datasets = [(folder_a, config["label_a"] or "Dataset A")]
            if config["mode"] == "COMPARE":
                datasets.append((Path(config["b"]), config["label_b"] or "Dataset B"))
            output = Path(config["output"]) if config["output"] else folder_a / "RF_Ramp_Analysis"
            dataset_counts = [(folder, label, len(load_index(folder))) for folder, label in datasets]
            total_waveforms = sum(count for _folder, _label, count in dataset_counts)
            tolerance = float(config["tolerance_pct"])
            fixed_ms = float(config["fixed_tolerance_ms"])
            min_samples = int(config["min_samples"])
            smoothing = float(config["smoothing_pct"])
            if tolerance < 0 or fixed_ms < 0 or min_samples < 3 or not 0 <= smoothing <= 2:
                raise ValueError("Check tolerance, minimum samples, and smoothing values.")

            all_rows = []
            completed_before = 0
            for folder, label, dataset_count in dataset_counts:
                base = completed_before
                def update_progress(done, _count, filename, base_count=base, dataset_label=label):
                    self._post_progress(base_count + done, total_waveforms, dataset_label, filename)
                rows, _ = analyze_dataset(
                    folder, label, config["definition"], tolerance, fixed_ms,
                    min_samples, smoothing, output / safe_name(label), update_progress,
                )
                all_rows.extend(rows)
                completed_before += dataset_count
            export_package(output, all_rows)
            message = (
                f"Analysis complete: {output}\n"
                f"PASS: {sum(r.get('pass_fail') == 'PASS' for r in all_rows)}  "
                f"FAIL: {sum(r.get('pass_fail') == 'FAIL' for r in all_rows)}  "
                f"INVALID: {sum(r.get('pass_fail') == 'INVALID' for r in all_rows)}"
            )
            def complete():
                self.progress.set(100.0)
                self.progress_text.set(f"{total_waveforms} of {total_waveforms} (100%)")
                self.status.set(message)
                self.run_button.configure(state="normal")
                messagebox.showinfo("RF ramp analysis", message, parent=self.root)
            self.root.after(0, complete)
        except Exception as exc:
            traceback.print_exc()
            error_text = str(exc)
            def failed():
                self.status.set(f"Analysis failed: {error_text}")
                self.run_button.configure(state="normal")
                messagebox.showerror("Analysis failed", error_text, parent=self.root)
            self.root.after(0, failed)


def self_test():
    t = np.linspace(0, 4, 4001)
    rise = np.clip((t - 1) / 1.0, 0, 1) * 0.3
    result = analyze_waveform(t, rise, "RISE", 0)
    assert abs(result["measured_10_90_s"] - 0.8) < 0.005
    assert abs(result["estimated_0_100_s"] - 1.0) < 0.005
    fall = 0.3 - rise
    result = analyze_waveform(t, fall, "FALL", 0)
    assert abs(result["measured_10_90_s"] - 0.8) < 0.005
    print("RF ramp analysis self-test passed.")
    return 0


def main():
    if "--self-test" in sys.argv:
        return self_test()
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
