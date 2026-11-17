"""Arc Management waveform analysis for Arc_Management_Collection.py.

Compatible with standalone use and suite_apps/analysis_gui.py.
The physical meanings of CH1-CH4 are intentionally not assumed. The analysis
reports trigger-referenced electrical behavior per channel and does not assign
product pass/fail limits until the channel map and acceptance criteria are known.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ANALYSIS_ID = "arc_management"
APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.output_folders import create_unique_folder, path_component  # noqa: E402
from suite_common.config import plugin_settings_path  # noqa: E402
SETTINGS_FILE = plugin_settings_path("arc_management", "analysis")
CSV_PATTERN = re.compile(r"arc_sample_(?P<sample>\d+)_(?P<stamp>.+)\.csv$", re.I)
CHANNELS = ("ch1_v", "ch2_v", "ch3_v", "ch4_v")

ENV_A = "PARAMOUNT_DATASET_A_FOLDER"
ENV_B = "PARAMOUNT_DATASET_B_FOLDER"
ENV_A_LABEL = "PARAMOUNT_DATASET_A_LABEL"
ENV_B_LABEL = "PARAMOUNT_DATASET_B_LABEL"
ENV_OUTPUT = "PARAMOUNT_ANALYSIS_OUTPUT_FOLDER"
ENV_PPT_ENABLED = "PARAMOUNT_POWERPOINT_EXPORT_ENABLED"
ENV_PPT_FOLDER = "PARAMOUNT_POWERPOINT_PACKAGE_FOLDER"
ENV_PPT_SCHEMA = "PARAMOUNT_POWERPOINT_SCHEMA_VERSION"


@dataclass
class Settings:
    dataset_a: str
    dataset_b: str = ""
    label_a: str = "Dataset A"
    label_b: str = "Dataset B"
    output_parent: str = ""
    trigger_level_v: float = 1.0
    baseline_fraction: float = 0.10
    baseline_noise_multiplier: float = 6.0
    minimum_deviation_v: float = 0.02
    recovery_band_percent_of_excursion: float = 5.0
    persistence_us: float = 20.0
    minimum_points: int = 50


def safe_name(text: str) -> str:
    return path_component(text, "Arc_Management")


def unique_output(parent: Path) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    return create_unique_folder(parent, f"{stamp} - Arc Management Analysis", parents=True)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def discover_samples(folder: Path) -> list[Path]:
    files = [p for p in folder.rglob("*.csv") if CSV_PATTERN.search(p.name)]
    return sorted(files, key=lambda p: int(CSV_PATTERN.search(p.name).group("sample")))


def load_waveform(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    lower = {str(c).strip().lower(): c for c in frame.columns}
    required = ("time_s", *CHANNELS)
    missing = [name for name in required if name not in lower]
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing))
    clean = pd.DataFrame({name: pd.to_numeric(frame[lower[name]], errors="coerce") for name in required})
    clean = clean.replace([np.inf, -np.inf], np.nan).dropna()
    clean = clean.sort_values("time_s", kind="stable")
    clean = clean.loc[clean["time_s"].diff().fillna(1) > 0].reset_index(drop=True)
    return clean


def linear_crossing(t0: float, y0: float, t1: float, y1: float, level: float) -> float:
    if y1 == y0:
        return t1
    return t0 + (level-y0)*(t1-t0)/(y1-y0)


def rising_crossing(t: np.ndarray, y: np.ndarray, level: float) -> float | None:
    indices = np.where((y[:-1] < level) & (y[1:] >= level))[0]
    if not len(indices):
        return None
    i = int(indices[0])
    return float(linear_crossing(t[i], y[i], t[i+1], y[i+1], level))


def robust_noise(values: np.ndarray) -> float:
    if len(values) < 2:
        return 0.0
    median = np.median(values)
    return float(1.4826 * np.median(np.abs(values-median)))


def first_sustained(mask: np.ndarray, t: np.ndarray, start: int, persistence_s: float) -> int | None:
    i = max(0, start)
    while i < len(mask):
        if not mask[i]:
            i += 1
            continue
        end = int(np.searchsorted(t, t[i] + persistence_s, side="left"))
        if end < len(mask) and np.all(mask[i:end+1]):
            return i
        i += 1
    return None


def channel_metrics(t: np.ndarray, y: np.ndarray, trigger_time: float, settings: Settings) -> dict[str, Any]:
    pre = y[t < trigger_time]
    if len(pre) < max(5, int(settings.minimum_points * settings.baseline_fraction)):
        count = max(5, min(len(y)//4, int(len(y)*settings.baseline_fraction)))
        pre = y[:count]
    baseline = float(np.median(pre))
    noise = robust_noise(pre)
    deviation = np.abs(y-baseline)
    peak_index = int(np.argmax(deviation))
    peak_signed = float(y[peak_index]-baseline)
    threshold = max(settings.minimum_deviation_v, settings.baseline_noise_multiplier*noise)
    start_index = int(np.searchsorted(t, trigger_time, side="left"))
    onset_candidates = np.where(deviation[start_index:] >= threshold)[0]
    onset_index = start_index + int(onset_candidates[0]) if len(onset_candidates) else None
    onset_time = float(t[onset_index]) if onset_index is not None else np.nan
    excursion = abs(peak_signed)
    recovery_band = max(settings.minimum_deviation_v, excursion*settings.recovery_band_percent_of_excursion/100.0)
    persistence_s = settings.persistence_us*1e-6
    after_peak = first_sustained(deviation <= recovery_band, t, peak_index, persistence_s)
    recovery_time = float(t[after_peak]) if after_peak is not None else np.nan
    return {
        "Baseline V": baseline,
        "Baseline Noise Robust Sigma V": noise,
        "Minimum Detectable Deviation V": threshold,
        "Minimum V": float(np.min(y)),
        "Maximum V": float(np.max(y)),
        "Peak-to-Peak V": float(np.ptp(y)),
        "Peak Deviation V": peak_signed,
        "Peak Absolute Deviation V": excursion,
        "Peak Time Relative to Trigger us": (float(t[peak_index])-trigger_time)*1e6,
        "Response Onset Relative to Trigger us": (onset_time-trigger_time)*1e6 if np.isfinite(onset_time) else np.nan,
        "Recovery Relative to Trigger us": (recovery_time-trigger_time)*1e6 if np.isfinite(recovery_time) else np.nan,
        "Recovery From Peak us": (recovery_time-float(t[peak_index]))*1e6 if np.isfinite(recovery_time) else np.nan,
        "Recovery Band V": recovery_band,
        "Recovered in Record": bool(np.isfinite(recovery_time)),
    }


def analyze_sample(path: Path, dataset: str, settings: Settings) -> tuple[dict, list[dict], pd.DataFrame | None]:
    match = CSV_PATTERN.search(path.name)
    sample = int(match.group("sample")) if match else 0
    base = {"Dataset": dataset, "Sample": sample, "CSV Path": str(path),
            "Valid Analysis": False, "Use In Average": False, "Exclusion Reason": ""}
    try:
        frame = load_waveform(path)
        if len(frame) < settings.minimum_points:
            raise ValueError(f"Only {len(frame)} valid points; minimum is {settings.minimum_points}")
        t = frame["time_s"].to_numpy(float)
        trigger = frame["ch3_v"].to_numpy(float)
        trigger_time = rising_crossing(t, trigger, settings.trigger_level_v)
        if trigger_time is None:
            raise ValueError(f"CH3 does not have a rising {settings.trigger_level_v:g} V crossing")
        sample_row = {**base, "Valid Analysis": True, "Use In Average": True,
                      "Trigger Time s": trigger_time, "Point Count": len(frame),
                      "Record Start s": float(t[0]), "Record End s": float(t[-1]),
                      "Record Duration us": float(t[-1]-t[0])*1e6,
                      "Sample Interval Median ns": float(np.median(np.diff(t)))*1e9}
        channel_rows = []
        for channel in CHANNELS:
            row = {**base, "Valid Analysis": True, "Use In Average": True,
                   "Channel": channel.upper().replace("_V", ""), "Trigger Time s": trigger_time}
            row.update(channel_metrics(t, frame[channel].to_numpy(float), trigger_time, settings))
            channel_rows.append(row)
        return sample_row, channel_rows, frame
    except Exception as exc:
        base["Exclusion Reason"] = str(exc)
        return base, [{**base, "Channel": ch.upper().replace("_V", "")} for ch in CHANNELS], None


def apply_iqr(frame: pd.DataFrame, metric: str) -> pd.DataFrame:
    result = frame.copy()
    if metric not in result:
        return result
    for (_, channel), idx in result.groupby(["Dataset", "Channel"]).groups.items():
        group = result.loc[idx]
        values = pd.to_numeric(group.loc[group["Valid Analysis"] == True, metric], errors="coerce").dropna()
        if len(values) < 4:
            continue
        q1, q3 = values.quantile([.25, .75]); iqr = q3-q1
        low, high = q1-1.5*iqr, q3+1.5*iqr
        out = values.index[(values < low) | (values > high)]
        result.loc[out, "Use In Average"] = False
        result.loc[out, "Exclusion Reason"] = f"1.5 x IQR {metric}: outside {low:.6g} to {high:.6g}"
    return result


def summarize_channels(frame: pd.DataFrame) -> pd.DataFrame:
    metrics = ["Peak Absolute Deviation V", "Peak Time Relative to Trigger us",
               "Response Onset Relative to Trigger us", "Recovery Relative to Trigger us"]
    rows = []
    for (dataset, channel), group in frame.groupby(["Dataset", "Channel"]):
        accepted = group[group["Use In Average"] == True]
        row = {"Dataset": dataset, "Channel": channel, "Total Count": len(group),
               "Accepted Count": len(accepted), "Excluded Count": len(group)-len(accepted)}
        for metric in metrics:
            values = pd.to_numeric(accepted.get(metric), errors="coerce").dropna()
            row[f"{metric} Average"] = values.mean() if len(values) else np.nan
            row[f"{metric} Minimum"] = values.min() if len(values) else np.nan
            row[f"{metric} Maximum"] = values.max() if len(values) else np.nan
            row[f"{metric} Standard Deviation"] = values.std(ddof=1) if len(values)>1 else (0.0 if len(values)==1 else np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


def plot_sample(output: Path, dataset: str, sample: int, frame: pd.DataFrame, trigger_time: float) -> Path:
    fig, axes = plt.subplots(4, 1, figsize=(11, 8), sharex=True)
    x = (frame["time_s"].to_numpy(float)-trigger_time)*1e6
    for ax, channel in zip(axes, CHANNELS):
        ax.plot(x, frame[channel].to_numpy(float), linewidth=.8)
        ax.axvline(0, color="black", linewidth=.8, linestyle="--")
        ax.set_ylabel(channel.upper().replace("_V", "") + " (V)")
        ax.grid(True, alpha=.25)
    axes[-1].set_xlabel("Time relative to CH3 trigger crossing (us)")
    fig.suptitle(f"{dataset}: Arc sample {sample}")
    fig.tight_layout()
    path = output/"plots"/f"{safe_name(dataset)}_sample_{sample:04d}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160); plt.close(fig)
    return path


def analyze_dataset(folder: Path, label: str, output: Path, settings: Settings):
    files = discover_samples(folder)
    if not files:
        raise ValueError(f"No arc_sample_*.csv files found in {folder}")
    sample_rows, channel_rows, plot_paths = [], [], []
    for path in files:
        sample_row, channels, frame = analyze_sample(path, label, settings)
        sample_rows.append(sample_row); channel_rows.extend(channels)
        if frame is not None:
            plot_paths.append(plot_sample(output, label, sample_row["Sample"], frame, sample_row["Trigger Time s"]))
    return sample_rows, channel_rows, plot_paths, read_json(folder/"run_manifest.json")


def export_package(folder: Path, output: Path, files: dict[str, Path], plots: list[Path]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": int(os.environ.get(ENV_PPT_SCHEMA, "1") or 1),
               "analysis_id": ANALYSIS_ID, "display_name": "Arc Management",
               "generated_at": datetime.now().astimezone().isoformat(),
               "analysis_output_folder": str(output),
               "files": {k: str(v) for k,v in files.items()}, "plots": [str(p) for p in plots]}
    (folder/"manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_analysis(settings: Settings, status=lambda _s: None) -> Path:
    datasets = [(Path(settings.dataset_a).resolve(), settings.label_a or "Dataset A")]
    if settings.dataset_b.strip(): datasets.append((Path(settings.dataset_b).resolve(), settings.label_b or "Dataset B"))
    for folder,_ in datasets:
        if not folder.is_dir(): raise ValueError(f"Dataset folder does not exist: {folder}")
    parent = Path(settings.output_parent).expanduser().resolve() if settings.output_parent else datasets[0][0]
    parent.mkdir(parents=True, exist_ok=True); output = unique_output(parent)
    all_samples, all_channels, plots, source_manifests = [], [], [], []
    for folder,label in datasets:
        status(f"Analyzing {label}...")
        samples, channels, images, manifest = analyze_dataset(folder,label,output,settings)
        all_samples.extend(samples); all_channels.extend(channels); plots.extend(images); source_manifests.append(manifest)
    sample_frame = pd.DataFrame(all_samples)
    channel_frame = apply_iqr(pd.DataFrame(all_channels), "Recovery Relative to Trigger us")
    summary = summarize_channels(channel_frame)
    paths = {"sample_results": output/"Arc_Management_Sample_Results.csv",
             "channel_results": output/"Arc_Management_Channel_Results.csv",
             "summary": output/"Arc_Management_Summary.csv"}
    sample_frame.to_csv(paths["sample_results"], index=False)
    channel_frame.to_csv(paths["channel_results"], index=False)
    summary.to_csv(paths["summary"], index=False)
    manifest = {"analysis_id": ANALYSIS_ID, "generated_at": datetime.now().astimezone().isoformat(),
                "settings": asdict(settings), "source_manifests": source_manifests,
                "method": {"trigger_reference": "First interpolated CH3 rising crossing at configured level",
                           "baseline": "Median pre-trigger level; robust noise from 1.4826 x median absolute deviation",
                           "response_onset": "First post-trigger deviation exceeding maximum of minimum voltage and noise multiplier",
                           "recovery": "First return to baseline band maintained for persistence window",
                           "outliers": "1.5 x IQR on recovery time, separately per dataset and channel",
                           "pass_fail": "Not assigned; channel meanings and acceptance limits were not supplied"}}
    (output/"analysis_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    if os.environ.get(ENV_PPT_ENABLED, "").lower() in {"1","true","yes"}:
        export_package(Path(os.environ.get(ENV_PPT_FOLDER, str(output/"powerpoint_package"))), output, paths, plots)
    return output


def load_defaults() -> dict:
    try: data=json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception: data={}
    overrides={"dataset_a":os.environ.get(ENV_A,""),"dataset_b":os.environ.get(ENV_B,""),
               "label_a":os.environ.get(ENV_A_LABEL,""),"label_b":os.environ.get(ENV_B_LABEL,""),
               "output_parent":os.environ.get(ENV_OUTPUT,"")}
    for key,value in overrides.items():
        if value: data[key]=value
    return data


def launch_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root=tk.Tk(); root.title("Arc Management Data Analysis"); root.geometry("900x720")
    saved=load_defaults(); defaults=asdict(Settings(dataset_a="")); variables={k:tk.StringVar(value=str(saved.get(k,v))) for k,v in defaults.items() if k not in {"dataset_a","dataset_b","label_a","label_b","output_parent"}}
    for key,default in (("dataset_a",""),("dataset_b",""),("label_a","Dataset A"),("label_b","Dataset B"),("output_parent","")): variables[key]=tk.StringVar(value=str(saved.get(key,default)))
    status=tk.StringVar(value="Ready")
    main=ttk.Frame(root,padding=16); main.pack(fill="both",expand=True)
    ttk.Label(main,text="Arc Management Data Analysis",font=("Segoe UI",16,"bold")).pack(anchor="w")
    ttk.Label(main,text="Trigger-referenced CH1-CH4 waveform analysis. Channel meanings and product pass/fail limits are intentionally not assumed.",wraplength=850).pack(anchor="w",pady=(2,10))
    inputs=ttk.LabelFrame(main,text="Datasets and output",padding=10); inputs.pack(fill="x"); inputs.columnconfigure(1,weight=1)
    fields=(("Dataset A folder","dataset_a",True),("Dataset A label","label_a",False),("Dataset B folder (optional)","dataset_b",True),("Dataset B label","label_b",False),("Output parent folder","output_parent",True))
    def browse(key):
        selected=filedialog.askdirectory(parent=root,initialdir=variables[key].get() or str(Path.home()))
        if selected: variables[key].set(selected)
    for i,(label,key,is_folder) in enumerate(fields):
        ttk.Label(inputs,text=label).grid(row=i,column=0,sticky="w",pady=4); ttk.Entry(inputs,textvariable=variables[key]).grid(row=i,column=1,sticky="ew",padx=8,pady=4)
        if is_folder: ttk.Button(inputs,text="Browse...",command=lambda k=key:browse(k)).grid(row=i,column=2)
    method=ttk.LabelFrame(main,text="Analysis settings",padding=10); method.pack(fill="x",pady=10)
    options=(("CH3 rising trigger level (V)","trigger_level_v"),("Baseline fraction","baseline_fraction"),("Baseline noise multiplier","baseline_noise_multiplier"),("Minimum deviation (V)","minimum_deviation_v"),("Recovery band (% of excursion)","recovery_band_percent_of_excursion"),("Persistence (us)","persistence_us"),("Minimum valid points","minimum_points"))
    for i,(label,key) in enumerate(options):
        row=i//2; col=(i%2)*2; ttk.Label(method,text=label).grid(row=row,column=col,sticky="w",pady=4); ttk.Entry(method,textvariable=variables[key],width=12).grid(row=row,column=col+1,sticky="w",padx=(8,25))
    ttk.Label(main,text="No product Pass/Fail is assigned until CH1-CH4 meanings and formal limits are confirmed.",foreground="#8a5200").pack(anchor="w",pady=7)
    ttk.Label(main,textvariable=status,wraplength=850).pack(anchor="w",pady=5); progress=ttk.Progressbar(main,mode="indeterminate"); progress.pack(fill="x")
    button=ttk.Button(main,text="Run Analysis"); button.pack(anchor="e",pady=12)
    def start():
        try:
            settings=Settings(dataset_a=variables["dataset_a"].get().strip(),dataset_b=variables["dataset_b"].get().strip(),label_a=variables["label_a"].get().strip(),label_b=variables["label_b"].get().strip(),output_parent=variables["output_parent"].get().strip(),trigger_level_v=float(variables["trigger_level_v"].get()),baseline_fraction=float(variables["baseline_fraction"].get()),baseline_noise_multiplier=float(variables["baseline_noise_multiplier"].get()),minimum_deviation_v=float(variables["minimum_deviation_v"].get()),recovery_band_percent_of_excursion=float(variables["recovery_band_percent_of_excursion"].get()),persistence_us=float(variables["persistence_us"].get()),minimum_points=int(variables["minimum_points"].get()))
            if not settings.dataset_a: raise ValueError("Select Dataset A.")
            if not 0 < settings.baseline_fraction < 0.5: raise ValueError("Baseline fraction must be greater than 0 and less than 0.5.")
            if min(settings.baseline_noise_multiplier,settings.minimum_deviation_v,settings.recovery_band_percent_of_excursion,settings.persistence_us) <= 0 or settings.minimum_points < 10: raise ValueError("Analysis thresholds must be positive and minimum points must be at least 10.")
            SETTINGS_FILE.write_text(json.dumps(asdict(settings),indent=2),encoding="utf-8")
        except Exception as exc: messagebox.showerror("Invalid settings",str(exc),parent=root); return
        button.configure(state="disabled"); progress.start(10); status.set("Starting analysis...")
        def worker():
            try: root.after(0,finish,True,f"Analysis complete.\n\n{run_analysis(settings,lambda text:root.after(0,status.set,text))}")
            except Exception as exc: traceback.print_exc(); root.after(0,finish,False,str(exc))
        threading.Thread(target=worker,daemon=True).start()
    def finish(ok,text):
        progress.stop(); button.configure(state="normal"); status.set(text); (messagebox.showinfo if ok else messagebox.showerror)("Arc Management Analysis" if ok else "Analysis failed",text,parent=root)
    button.configure(command=start); root.mainloop(); return 0


def self_test() -> int:
    t=np.linspace(-1e-3,1e-3,2001); y=np.where(t<0,0,2)
    crossing=rising_crossing(t,y,1.0)
    assert crossing is not None and abs(crossing) < 2e-6
    assert CSV_PATTERN.search("arc_sample_0001_20261110_120000_000.csv")
    print("Arc Management analysis self-test passed."); return 0


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--self-test",action="store_true"); parser.add_argument("--dataset-a"); parser.add_argument("--dataset-b",default=""); parser.add_argument("--output",default=""); args=parser.parse_args()
    if args.self_test: return self_test()
    if args.dataset_a: print(run_analysis(Settings(args.dataset_a,args.dataset_b,output_parent=args.output))); return 0
    return launch_gui()

if __name__ == "__main__": raise SystemExit(main())
