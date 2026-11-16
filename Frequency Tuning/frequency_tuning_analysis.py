"""RF Tuning analysis for RF_Tuning_Collection.py and Paramount unified analysis.

Analyzes accepted frequency-counter CSV files from one or two RF Tuning run folders.
Failed attempts remain represented from attempt_log.csv but are never used in averages.
No pass/fail specification is invented; results report measured behavior and exclusions.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ANALYSIS_ID = "rf_tuning"
APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "RF_Tuning_Analysis_Settings.json"

ENV_MODE = "PARAMOUNT_ANALYSIS_MODE"
ENV_A = "PARAMOUNT_DATASET_A_FOLDER"
ENV_B = "PARAMOUNT_DATASET_B_FOLDER"
ENV_A_LABEL = "PARAMOUNT_DATASET_A_LABEL"
ENV_B_LABEL = "PARAMOUNT_DATASET_B_LABEL"
ENV_OUTPUT = "PARAMOUNT_ANALYSIS_OUTPUT_FOLDER"
ENV_PPT_ENABLED = "PARAMOUNT_POWERPOINT_EXPORT_ENABLED"
ENV_PPT_FOLDER = "PARAMOUNT_POWERPOINT_PACKAGE_FOLDER"
ENV_PPT_ID = "PARAMOUNT_POWERPOINT_ANALYSIS_ID"
ENV_PPT_SCHEMA = "PARAMOUNT_POWERPOINT_SCHEMA_VERSION"

DEFAULT_SETTLE_TOLERANCE_PERCENT = 5.0
DEFAULT_PERSISTENCE_MS = 20.0
DEFAULT_MIN_POINTS = 20
DEFAULT_SEARCH_DELAY_SECONDS = 0.50

FILENAME_RE = re.compile(
    r"sample_(?P<sample>\d+)_attempt_(?P<attempt>\d+)_"
    r"(?P<start>\d+p\d+)_to_(?P<target>\d+p\d+)MHz_"
    r"match_L(?P<load>-?\d+)_T(?P<tune>-?\d+)_", re.IGNORECASE)


@dataclass
class AnalysisSettings:
    dataset_a: str
    dataset_b: str = ""
    label_a: str = "Dataset A"
    label_b: str = "Dataset B"
    output_parent: str = ""
    settle_tolerance_percent: float = DEFAULT_SETTLE_TOLERANCE_PERCENT
    persistence_ms: float = DEFAULT_PERSISTENCE_MS
    min_points: int = DEFAULT_MIN_POINTS
    search_delay_seconds: float = DEFAULT_SEARCH_DELAY_SECONDS


def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", text).strip().rstrip(".") or "RF_Tuning_Analysis"


def unique_output_folder(parent: Path) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    base = parent / f"{stamp} - RF Tuning Analysis"
    candidate = base
    run = 2
    while candidate.exists():
        candidate = parent / f"{base.name} - Run {run}"
        run += 1
    candidate.mkdir(parents=True)
    return candidate


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_frequency(token: str) -> int:
    return int(round(float(token.replace("p", ".")) * 1000))


def parse_metadata(path: Path, row: dict[str, Any] | None = None) -> dict[str, Any]:
    row = row or {}
    m = FILENAME_RE.search(path.name)
    if m:
        return {
            "Logical Sample": int(m.group("sample")), "Attempt": int(m.group("attempt")),
            "Start Frequency kHz": parse_frequency(m.group("start")),
            "Target Frequency kHz": parse_frequency(m.group("target")),
            "Actual Load Position": int(m.group("load")),
            "Actual Tune Position": int(m.group("tune")),
        }
    return {
        "Logical Sample": int(float(row.get("logical_sample", 0) or 0)),
        "Attempt": int(float(row.get("attempt", 1) or 1)),
        "Start Frequency kHz": int(float(row.get("start_frequency_khz", 0) or 0)),
        "Target Frequency kHz": int(float(row.get("target_frequency_khz", 0) or 0)),
        "Actual Load Position": int(float(row.get("actual_load", 0) or 0)),
        "Actual Tune Position": int(float(row.get("actual_tune", 0) or 0)),
    }


def load_waveform(path: Path) -> tuple[np.ndarray, np.ndarray]:
    frame = pd.read_csv(path)
    lowered = {str(c).strip().lower(): c for c in frame.columns}
    if "time" not in lowered or "frequency_hz" not in lowered:
        raise ValueError("CSV must contain time and frequency_hz columns")
    raw_t = frame[lowered["time"]]
    frequencies = pd.to_numeric(frame[lowered["frequency_hz"]], errors="coerce").to_numpy(float)
    numeric_time = pd.to_numeric(raw_t, errors="coerce")
    if numeric_time.notna().sum() == len(raw_t):
        times = numeric_time.to_numpy(float)
    else:
        timestamps = pd.to_datetime(raw_t, errors="coerce", utc=True)
        if timestamps.isna().all():
            raise ValueError("Time column is neither numeric nor a readable timestamp")
        times = (timestamps - timestamps.dropna().iloc[0]).dt.total_seconds().to_numpy(float)
    valid = np.isfinite(times) & np.isfinite(frequencies) & (frequencies > 0)
    times, frequencies = times[valid], frequencies[valid]
    order = np.argsort(times, kind="stable")
    times, frequencies = times[order], frequencies[order]
    keep = np.r_[True, np.diff(times) > 0] if len(times) else np.array([], dtype=bool)
    return times[keep], frequencies[keep]


def interpolate_crossing(t0, y0, t1, y1, level) -> float:
    if y1 == y0:
        return float(t1)
    return float(t0 + (level - y0) * (t1 - t0) / (y1 - y0))


def first_directional_crossing(times, values, level, direction, start_index) -> float | None:
    for i in range(max(1, start_index), len(values)):
        if direction > 0 and values[i - 1] < level <= values[i]:
            return interpolate_crossing(times[i-1], values[i-1], times[i], values[i], level)
        if direction < 0 and values[i - 1] > level >= values[i]:
            return interpolate_crossing(times[i-1], values[i-1], times[i], values[i], level)
    return None


def settling_time(times, values, target, tolerance_hz, persistence_s, after_time) -> float | None:
    start = int(np.searchsorted(times, after_time, side="left"))
    for i in range(start, len(values)):
        if abs(values[i] - target) > tolerance_hz:
            continue
        end_time = times[i] + persistence_s
        j = int(np.searchsorted(times, end_time, side="left"))
        if j < len(values) and np.all(np.abs(values[i:j+1] - target) <= tolerance_hz):
            return float(times[i])
    return None


def analyze_waveform(path: Path, meta: dict, settings: AnalysisSettings) -> tuple[dict, tuple[np.ndarray, np.ndarray]]:
    base = dict(meta)
    base.update({"CSV Path": str(path), "Valid Analysis": False, "Use In Average": False,
                 "Exclusion Reason": "", "Outlier Reason": ""})
    try:
        t, f = load_waveform(path)
        if len(t) < settings.min_points:
            raise ValueError(f"Fewer than {settings.min_points} valid, strictly increasing samples")
        start_hz = meta["Start Frequency kHz"] * 1000.0
        target_hz = meta["Target Frequency kHz"] * 1000.0
        span = target_hz - start_hz
        if not span:
            raise ValueError("Start and target frequencies are equal")
        search_index = int(np.searchsorted(t, t[0] + settings.search_delay_seconds))
        level10 = start_hz + 0.10 * span
        level90 = start_hz + 0.90 * span
        direction = 1 if span > 0 else -1
        t10 = first_directional_crossing(t, f, level10, direction, search_index)
        t90 = first_directional_crossing(t, f, level90, direction, search_index)
        if t10 is None:
            raise ValueError("No 10% directional crossing found")
        tolerance_hz = abs(span) * settings.settle_tolerance_percent / 100.0
        settled = settling_time(t, f, target_hz, tolerance_hz, settings.persistence_ms / 1000.0, t10)
        if settled is None:
            raise ValueError("Signal did not remain inside the target settling band")
        final_window = f[t >= max(t[0], t[-1] - settings.persistence_ms / 1000.0)]
        final_mean = float(np.mean(final_window)) if len(final_window) else float(f[-1])
        base.update({
            "Valid Analysis": True, "Use In Average": True,
            "10 Percent Crossing s": t10,
            "90 Percent Crossing s": t90 if t90 is not None else np.nan,
            "10 to 90 Time ms": (t90 - t10) * 1000.0 if t90 is not None else np.nan,
            "Settled Time s": settled,
            "Tuning Time ms": (settled - t10) * 1000.0,
            "Final Mean Frequency Hz": final_mean,
            "Final Frequency Error Hz": final_mean - target_hz,
            "Final Frequency Error Percent of Target": (final_mean - target_hz) / target_hz * 100.0,
            "Peak Overshoot Hz": (float(np.max(f[t >= t10])) - target_hz) if direction > 0 else (target_hz - float(np.min(f[t >= t10]))),
            "Point Count": len(t), "Duration s": float(t[-1] - t[0]),
            "Settle Tolerance Hz": tolerance_hz,
        })
        return base, (t, f)
    except Exception as exc:
        base["Exclusion Reason"] = str(exc)
        return base, (np.array([]), np.array([]))


def accepted_attempts(run_folder: Path) -> list[tuple[Path, dict]]:
    log = run_folder / "attempt_log.csv"
    found = []
    if log.is_file():
        with log.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                rel = row.get("csv_path", "").strip()
                if not rel:
                    continue
                path = run_folder / Path(rel)
                status = row.get("status", "").strip().lower()
                use = row.get("use_in_analysis", "").strip().lower() in {"true", "1", "yes"}
                if status == "passed" and use and path.is_file():
                    found.append((path, row))
    if not found:
        for path in sorted(run_folder.rglob("*.csv")):
            if path.name.lower() == "attempt_log.csv" or "Failed Attempts" in path.parts:
                continue
            if FILENAME_RE.search(path.name):
                found.append((path, {}))
    return found


def failed_attempt_rows(run_folder: Path, dataset: str) -> list[dict]:
    log = run_folder / "attempt_log.csv"
    if not log.is_file():
        return []
    frame = pd.read_csv(log, dtype=str).fillna("")
    rows = []
    for _, row in frame.iterrows():
        if row.get("status", "").lower() == "passed":
            continue
        meta = parse_metadata(run_folder / row.get("csv_path", "failed.csv"), row.to_dict())
        rows.append({"Dataset": dataset, **meta, "CSV Path": row.get("csv_path", ""),
                     "Valid Analysis": False, "Use In Average": False,
                     "Exclusion Reason": row.get("exclusion_reason", "Collection attempt failed"),
                     "Fault Codes": row.get("fault_codes", ""),
                     "Fault Descriptions": row.get("fault_descriptions", "")})
    return rows


def apply_iqr(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for (_, condition), indices in result.groupby(["Dataset", "Condition"]).groups.items():
        valid = result.loc[indices]
        valid = valid[(valid["Valid Analysis"] == True) & pd.to_numeric(valid["Tuning Time ms"], errors="coerce").notna()]
        if len(valid) < 4:
            continue
        values = pd.to_numeric(valid["Tuning Time ms"])
        q1, q3 = values.quantile([0.25, 0.75])
        low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
        excluded = valid.index[(values < low) | (values > high)]
        result.loc[excluded, "Use In Average"] = False
        result.loc[excluded, "Outlier Reason"] = f"1.5 x IQR outside {low:.6g} to {high:.6g} ms"
        result.loc[excluded, "Exclusion Reason"] = result.loc[excluded, "Outlier Reason"]
    return result


def summarize(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (dataset, condition), group in frame.groupby(["Dataset", "Condition"], dropna=False):
        accepted = group[group["Use In Average"] == True]
        values = pd.to_numeric(accepted.get("Tuning Time ms"), errors="coerce").dropna()
        errors = pd.to_numeric(accepted.get("Final Frequency Error Hz"), errors="coerce").dropna()
        rows.append({
            "Dataset": dataset, "Condition": condition,
            "Start Frequency kHz": group["Start Frequency kHz"].iloc[0],
            "Target Frequency kHz": group["Target Frequency kHz"].iloc[0],
            "Total Attempts Represented": len(group), "Accepted Count": len(values),
            "Excluded Count": len(group) - len(values),
            "Average Tuning Time ms": values.mean() if len(values) else np.nan,
            "Minimum Tuning Time ms": values.min() if len(values) else np.nan,
            "Maximum Tuning Time ms": values.max() if len(values) else np.nan,
            "Tuning Time Standard Deviation ms": values.std(ddof=1) if len(values) > 1 else (0.0 if len(values) == 1 else np.nan),
            "Average Final Frequency Error Hz": errors.mean() if len(errors) else np.nan,
            "Maximum Absolute Final Frequency Error Hz": errors.abs().max() if len(errors) else np.nan,
        })
    return pd.DataFrame(rows)


def plot_condition(output: Path, dataset: str, condition: str, waveforms: list[tuple[dict, np.ndarray, np.ndarray]], settings: AnalysisSettings) -> Path | None:
    usable = [(r, t, f) for r, t, f in waveforms if len(t)]
    if not usable:
        return None
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for row, t, f in usable:
        ax.plot((t - t[0]) * 1000.0, f / 1e6, alpha=.35, linewidth=.8)
    target = usable[0][0]["Target Frequency kHz"] / 1000.0
    span = abs(usable[0][0]["Target Frequency kHz"] - usable[0][0]["Start Frequency kHz"]) / 1000.0
    tol = span * settings.settle_tolerance_percent / 100.0
    ax.axhline(target, color="black", linewidth=1, label="Target")
    ax.axhspan(target-tol, target+tol, color="green", alpha=.12, label="Settling band")
    ax.set(title=f"{dataset}: {condition}", xlabel="Time from first valid reading (ms)", ylabel="Frequency (MHz)")
    ax.grid(True, alpha=.25); ax.legend(loc="best"); fig.tight_layout()
    path = output / "plots" / f"{safe_name(dataset)}_{safe_name(condition)}.png"
    path.parent.mkdir(parents=True, exist_ok=True); fig.savefig(path, dpi=160); plt.close(fig)
    return path


def analyze_dataset(folder: Path, label: str, settings: AnalysisSettings):
    manifest = read_json(folder / "run_manifest.json")
    attempts = accepted_attempts(folder)
    if not attempts:
        raise ValueError(f"No accepted RF Tuning sample CSV files found in {folder}")
    rows, waves = [], []
    for path, log_row in attempts:
        meta = parse_metadata(path, log_row)
        meta["Dataset"] = label
        meta["Condition"] = f"{meta['Start Frequency kHz']/1000:.3f} to {meta['Target Frequency kHz']/1000:.3f} MHz"
        row, (t, f) = analyze_waveform(path, meta, settings)
        rows.append(row); waves.append((row, t, f))
    rows.extend(failed_attempt_rows(folder, label))
    return rows, waves, manifest


def export_package(folder: Path, output: Path, summary: Path, details: Path, plots: list[Path]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": int(os.environ.get(ENV_PPT_SCHEMA, "1") or 1),
        "analysis_id": ANALYSIS_ID, "display_name": "RF Tuning",
        "generated_at": datetime.now().astimezone().isoformat(),
        "summary_csv": str(summary), "details_csv": str(details),
        "plots": [str(p) for p in plots], "analysis_output_folder": str(output),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def run_analysis(settings: AnalysisSettings, status=lambda _s: None) -> Path:
    datasets = [(Path(settings.dataset_a).resolve(), settings.label_a or "Dataset A")]
    if settings.dataset_b.strip():
        datasets.append((Path(settings.dataset_b).resolve(), settings.label_b or "Dataset B"))
    for folder, _ in datasets:
        if not folder.is_dir():
            raise ValueError(f"Dataset folder does not exist: {folder}")
    parent = Path(settings.output_parent).expanduser().resolve() if settings.output_parent else datasets[0][0]
    parent.mkdir(parents=True, exist_ok=True)
    output = unique_output_folder(parent)
    all_rows, all_waves, source_manifests = [], [], []
    for folder, label in datasets:
        status(f"Analyzing {label}...")
        rows, waves, manifest = analyze_dataset(folder, label, settings)
        all_rows.extend(rows); all_waves.extend(waves); source_manifests.append(manifest)
    details = apply_iqr(pd.DataFrame(all_rows))
    details_path = output / "RF_Tuning_Detailed_Results.csv"
    details.to_csv(details_path, index=False)
    summary = summarize(details)
    summary_path = output / "RF_Tuning_Summary.csv"
    summary.to_csv(summary_path, index=False)
    plot_paths = []
    for (dataset, condition), group in pd.DataFrame([r for r, _, _ in all_waves]).groupby(["Dataset", "Condition"]):
        selected = [(r, t, f) for r, t, f in all_waves if r["Dataset"] == dataset and r["Condition"] == condition]
        p = plot_condition(output, dataset, condition, selected, settings)
        if p: plot_paths.append(p)
    metadata = {"analysis_id": ANALYSIS_ID, "generated_at": datetime.now().astimezone().isoformat(),
                "settings": asdict(settings), "source_manifests": source_manifests,
                "method": {"start": "10% directional crossing after configured search delay",
                           "settled": "first entry into target band maintained for persistence window",
                           "outliers": "1.5 x IQR separately by dataset and directed transition",
                           "pass_fail": "Not assigned because no product acceptance limits were supplied"}}
    (output / "analysis_manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    if os.environ.get(ENV_PPT_ENABLED, "").lower() in {"1", "true", "yes"}:
        package = Path(os.environ.get(ENV_PPT_FOLDER, str(output / "powerpoint_package")))
        export_package(package, output, summary_path, details_path, plot_paths)
    return output


def load_settings() -> dict:
    data = {}
    try: data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception: pass
    data["dataset_a"] = os.environ.get(ENV_A, data.get("dataset_a", ""))
    data["dataset_b"] = os.environ.get(ENV_B, data.get("dataset_b", ""))
    data["label_a"] = os.environ.get(ENV_A_LABEL, data.get("label_a", "Dataset A"))
    data["label_b"] = os.environ.get(ENV_B_LABEL, data.get("label_b", "Dataset B"))
    data["output_parent"] = os.environ.get(ENV_OUTPUT, data.get("output_parent", ""))
    return data


def launch_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk(); root.title("RF Tuning Data Analysis"); root.geometry("840x620")
    saved = load_settings()
    vars = {k: tk.StringVar(value=str(saved.get(k, default))) for k, default in {
        "dataset_a":"", "dataset_b":"", "label_a":"Dataset A", "label_b":"Dataset B", "output_parent":"",
        "settle_tolerance_percent":DEFAULT_SETTLE_TOLERANCE_PERCENT, "persistence_ms":DEFAULT_PERSISTENCE_MS,
        "min_points":DEFAULT_MIN_POINTS, "search_delay_seconds":DEFAULT_SEARCH_DELAY_SECONDS}.items()}
    status_var = tk.StringVar(value="Ready")
    main = ttk.Frame(root, padding=16); main.pack(fill="both", expand=True)
    ttk.Label(main, text="RF Tuning Data Analysis", font=("Segoe UI", 16, "bold")).pack(anchor="w")
    ttk.Label(main, text="Analyzes collection CSVs, retains failed and excluded attempts, applies condition-specific IQR filtering, and produces summaries and plots.", wraplength=790).pack(anchor="w", pady=(2,12))
    inputs = ttk.LabelFrame(main, text="Datasets and output", padding=10); inputs.pack(fill="x"); inputs.columnconfigure(1, weight=1)
    fields = [("Dataset A folder","dataset_a",True),("Dataset A label","label_a",False),("Dataset B folder (optional)","dataset_b",True),("Dataset B label","label_b",False),("Analysis output parent","output_parent",True)]
    def browse(key):
        value=filedialog.askdirectory(parent=root, initialdir=vars[key].get() or str(Path.home()))
        if value: vars[key].set(value)
    for i,(label,key,is_dir) in enumerate(fields):
        ttk.Label(inputs,text=label).grid(row=i,column=0,sticky="w",pady=4)
        ttk.Entry(inputs,textvariable=vars[key]).grid(row=i,column=1,sticky="ew",padx=8,pady=4)
        if is_dir: ttk.Button(inputs,text="Browse...",command=lambda k=key:browse(k)).grid(row=i,column=2,pady=4)
    method=ttk.LabelFrame(main,text="Measurement method",padding=10); method.pack(fill="x",pady=10)
    settings_fields=[("Settling tolerance (% of tuning span)","settle_tolerance_percent"),("Persistence window (ms)","persistence_ms"),("Minimum valid points","min_points"),("Earliest transition search time (s)","search_delay_seconds")]
    for i,(label,key) in enumerate(settings_fields):
        ttk.Label(method,text=label).grid(row=i,column=0,sticky="w",pady=3); ttk.Entry(method,textvariable=vars[key],width=12).grid(row=i,column=1,sticky="w",padx=8)
    ttk.Label(main,text="No pass/fail result is assigned because the supplied collection scripts contain no acceptance limit.",foreground="#8a5200").pack(anchor="w",pady=8)
    ttk.Label(main,textvariable=status_var,wraplength=790).pack(anchor="w",pady=5)
    progress=ttk.Progressbar(main,mode="indeterminate"); progress.pack(fill="x")
    button=ttk.Button(main,text="Run Analysis"); button.pack(anchor="e",pady=12)
    import threading
    def start():
        try:
            settings=AnalysisSettings(dataset_a=vars["dataset_a"].get().strip(),dataset_b=vars["dataset_b"].get().strip(),label_a=vars["label_a"].get().strip(),label_b=vars["label_b"].get().strip(),output_parent=vars["output_parent"].get().strip(),settle_tolerance_percent=float(vars["settle_tolerance_percent"].get()),persistence_ms=float(vars["persistence_ms"].get()),min_points=int(vars["min_points"].get()),search_delay_seconds=float(vars["search_delay_seconds"].get()))
            if not settings.dataset_a: raise ValueError("Select Dataset A.")
            if settings.settle_tolerance_percent <= 0 or settings.persistence_ms <= 0 or settings.min_points < 3 or settings.search_delay_seconds < 0: raise ValueError("Analysis settings must be positive; search delay may be zero.")
            SETTINGS_FILE.write_text(json.dumps(asdict(settings),indent=2),encoding="utf-8")
        except Exception as exc: messagebox.showerror("Invalid settings",str(exc),parent=root); return
        button.configure(state="disabled"); progress.start(10); status_var.set("Starting analysis...")
        def worker():
            try:
                out=run_analysis(settings,lambda s:root.after(0,status_var.set,s))
                root.after(0,finish,True,f"Analysis complete.\n\n{out}")
            except Exception as exc:
                traceback.print_exc(); root.after(0,finish,False,str(exc))
        threading.Thread(target=worker,daemon=True).start()
    def finish(ok,text):
        progress.stop(); button.configure(state="normal"); status_var.set(text)
        (messagebox.showinfo if ok else messagebox.showerror)("RF Tuning Analysis" if ok else "Analysis failed",text,parent=root)
    button.configure(command=start); root.mainloop(); return 0


def self_test() -> int:
    assert parse_frequency("13p560") == 13560
    m=parse_metadata(Path("sample_0001_attempt_01_13p560_to_12p882MHz_match_L5795_T6277_x.csv"))
    assert m["Start Frequency kHz"] == 13560 and m["Target Frequency kHz"] == 12882
    t=np.linspace(0,2,2001); f=np.where(t<1,13.56e6,13.56e6+(12.882e6-13.56e6)*np.minimum((t-1)/.1,1))
    assert first_directional_crossing(t,f,13.56e6+.1*(12.882e6-13.56e6),-1,500) is not None
    print("RF Tuning analysis self-test passed."); return 0


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--self-test",action="store_true"); parser.add_argument("--dataset-a"); parser.add_argument("--dataset-b",default=""); parser.add_argument("--output"); args=parser.parse_args()
    if args.self_test: return self_test()
    if args.dataset_a:
        settings=AnalysisSettings(args.dataset_a,args.dataset_b,output_parent=args.output or "")
        print(run_analysis(settings)); return 0
    return launch_gui()

if __name__ == "__main__": raise SystemExit(main())
