"""Analog Processing Timing waveform analysis GUI.

Measurement definitions are preserved from the validated timing analysis:
CH1 command timing is the 50% setpoint crossing. CH2 uses a Hilbert envelope.
Latency is CH1 50% to the first CH2 10% transition-progress crossing. RF rise
and fall times use persistent 10% to 90% transition-progress crossings.
"""
from __future__ import annotations
import csv, json, math, os, re, threading, traceback
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.output_folders import create_unique_folder, path_component_strict  # noqa: E402
from typing import Any, Optional
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from scipy.signal import hilbert

CH1_CURSOR_FRACTION = 0.50
CH1_PRE_EDGE_WINDOW = (-500e-6, -50e-6)
CH1_POST_EDGE_WINDOW = (50e-6, 500e-6)
ENVELOPE_INITIAL_WINDOW = (-500e-6, -50e-6)
ENVELOPE_FINAL_WINDOW = (500e-6, 2.0e-3)
ENVELOPE_SMOOTHING_S = 0.0
OPTIONAL_SMOOTHING_S = 2.0e-6
CROSSING_PERSISTENCE_S = 1.0e-6
COMMAND_LEVEL_TOLERANCE_V = 0.35
COMMAND_TRANSIENT_TOLERANCE_V = 0.35
PLOT_DPI = 150
ANALYSIS_ID = "analog_processing_timing"
ANALYSIS_NAME = "Analog Processing Timing"


def pair_files(folder: Path):
    # Accept both the descriptive collection filenames and legacy combined-suite
    # filenames without changing descriptor, direction, or channel pairing.
    pattern = re.compile(
        r"^(?P<prefix>.*(?:ANALOG_TIMING|T5).*?_(?P<direction>RISE|FALL))_CH(?P<channel>[12])"
        r"(?:_\d{8}_\d{6}(?:_\d+)?)?\.csv$", re.I)
    groups, directions = {}, {}
    for path in folder.rglob("*.csv"):
        if any("Analysis" in part for part in path.parts):
            continue
        match = pattern.match(path.name)
        if not match:
            continue
        key = match.group("prefix")
        groups.setdefault(key, {})[int(match.group("channel"))] = path
        directions[key] = match.group("direction").upper()
    incomplete = {key: sorted(files) for key, files in groups.items() if not {1,2} <= set(files)}
    pairs = [(key, directions[key], files[1], files[2]) for key, files in sorted(groups.items()) if 1 in files and 2 in files]
    if not pairs:
        raise RuntimeError("No matching analog-processing CH1/CH2 waveform pairs found.")
    return pairs, incomplete

def read_scope_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read local Ethernet-transfer CSVs and legacy scope spreadsheet CSVs.

    This function changes only file ingestion. The validated timing measurement
    functions and thresholds downstream are intentionally unchanged.
    """
    # New local format written by the collection script:
    # Time (s),CH1 (V) or Time (s),CH2 (V)
    try:
        local = np.genfromtxt(path, delimiter=",", names=True, dtype=float,
                              encoding="utf-8-sig", invalid_raise=False)
        if local.dtype.names and len(local.dtype.names) >= 2:
            t = np.asarray(local[local.dtype.names[0]], dtype=float)
            y = np.asarray(local[local.dtype.names[1]], dtype=float)
            good = np.isfinite(t) & np.isfinite(y)
            t, y = t[good], y[good]
            if len(t) >= 20:
                keep = np.concatenate(([True], np.diff(t) > 0))
                t, y = t[keep], y[keep]
                if len(t) >= 20:
                    return t, y
    except Exception:
        pass

    # Legacy Tektronix spreadsheet export path retained for compatibility.
    rows = list(csv.reader(path.open("r", newline="", encoding="utf-8-sig", errors="replace")))
    if not rows:
        raise RuntimeError(f"Empty CSV: {path.name}")
    header = tx = vy = None
    for i, row in enumerate(rows[:100]):
        lower = [cell.strip().lower() for cell in row]
        tc = [j for j, value in enumerate(lower) if "time" in value or value in {"x", "seconds", "second"}]
        vc = [j for j, value in enumerate(lower) if "volt" in value or value in {"y", "amplitude"}]
        if tc and vc:
            header, tx, vy = i, tc[-1], vc[-1]
            break
    samples = []
    if header is not None:
        for row in rows[header + 1:]:
            try:
                samples.append((float(row[tx]), float(row[vy])))
            except (ValueError, IndexError):
                pass
    else:
        for row in rows:
            nums = []
            for cell in row:
                try:
                    nums.append(float(cell))
                except ValueError:
                    pass
            if len(nums) >= 2:
                samples.append((nums[-2], nums[-1]))
    if len(samples) < 20:
        raise RuntimeError(f"Insufficient waveform samples in {path.name}")
    data = np.asarray(samples, float)
    good = np.isfinite(data[:, 0]) & np.isfinite(data[:, 1])
    t, y = data[good, 0], data[good, 1]
    keep = np.concatenate(([True], np.diff(t) > 0))
    t, y = t[keep], y[keep]
    if len(t) < 20:
        raise RuntimeError(f"Invalid or non-increasing time data in {path.name}")
    return t, y

def parse_transition(descriptor: str) -> tuple[float, float]:
    match = re.search(r"_(-?\d+(?:\.\d+)?)V_to_(-?\d+(?:\.\d+)?)V_", descriptor, re.I)
    if not match:
        raise RuntimeError(f"Could not parse transition from {descriptor!r}")
    return float(match.group(1)), float(match.group(2))

def window_median(t, y, reference, window, label):
    mask = (t >= reference + window[0]) & (t <= reference + window[1])
    if np.count_nonzero(mask) < 10:
        raise RuntimeError(f"{label} contains fewer than 10 samples")
    return float(np.median(y[mask]))

def crossings(t, y, level, direction, persistence):
    found = []
    for i in range(1, len(y)):
        crossed = y[i-1] < level <= y[i] if direction == "rising" else y[i-1] > level >= y[i]
        if not crossed: continue
        stop = min(len(y), i + max(1, persistence))
        stable = np.all(y[i:stop] >= level) if direction == "rising" else np.all(y[i:stop] <= level)
        if not stable: continue
        fraction = 0.0 if y[i] == y[i-1] else (level - y[i-1]) / (y[i] - y[i-1])
        found.append((float(t[i-1] + fraction * (t[i] - t[i-1])), i))
    return found

def first_crossing_after(t, y, level, direction, start_index, persistence):
    for crossing_time, index in crossings(t[start_index-1:], y[start_index-1:], level, direction, persistence):
        return crossing_time, start_index - 1 + index
    raise RuntimeError(f"No persistent {direction} crossing at {level:.6g}")

def select_t1(t, ch1, start_v, end_v, direction, persistence):
    level = start_v + CH1_CURSOR_FRACTION * (end_v - start_v)
    edge_direction = "rising" if direction == "RISE" else "falling"
    candidates = []
    for tc, idx in crossings(t, ch1, level, edge_direction, persistence):
        try:
            pre = window_median(t, ch1, tc, CH1_PRE_EDGE_WINDOW, "CH1 pre-edge window")
            post = window_median(t, ch1, tc, CH1_POST_EDGE_WINDOW, "CH1 post-edge window")
        except RuntimeError:
            continue
        start_error, end_error = abs(pre-start_v), abs(post-end_v)
        if start_error <= COMMAND_LEVEL_TOLERANCE_V and end_error <= COMMAND_LEVEL_TOLERANCE_V:
            candidates.append((start_error + end_error, tc, idx, pre, post))
    if not candidates:
        raise RuntimeError(f"No CH1 edge had local plateaus matching {start_v:g} V to {end_v:g} V")
    _, tc, idx, pre, post = min(candidates)
    local = (t >= tc + CH1_PRE_EDGE_WINDOW[0]) & (t <= tc + CH1_POST_EDGE_WINDOW[1])
    local_min, local_max = float(np.min(ch1[local])), float(np.max(ch1[local]))
    if local_min < min(start_v, end_v) - COMMAND_TRANSIENT_TOLERANCE_V:
        raise RuntimeError(f"CH1 local transition dipped to {local_min:.3f} V")
    if local_max > max(start_v, end_v) + COMMAND_TRANSIENT_TOLERANCE_V:
        raise RuntimeError(f"CH1 local transition rose to {local_max:.3f} V")
    return tc, idx, level, pre, post, len(candidates), local_min, local_max

def analyze_pair(descriptor, direction, t1_axis, ch1, t2_axis, ch2):
    start_v, end_v = parse_transition(descriptor)
    if len(t1_axis) != len(t2_axis) or not np.allclose(t1_axis, t2_axis, rtol=1e-8, atol=1e-15):
        ch2 = np.interp(t1_axis, t2_axis, ch2); t2_axis = t1_axis.copy()
    dt = float(np.median(np.diff(t1_axis)))
    persistence = max(1, int(round(CROSSING_PERSISTENCE_S / dt)))
    t1, t1_idx, ch1_level, ch1_pre, ch1_post, candidate_count, local_min, local_max = select_t1(
        t1_axis, ch1, start_v, end_v, direction, persistence)

    rf_dc = window_median(t2_axis, ch2, t1, ENVELOPE_INITIAL_WINDOW, "CH2 baseline")
    raw_envelope = np.abs(hilbert(ch2 - rf_dc))
    smoothing_samples = max(1, int(round(ENVELOPE_SMOOTHING_S / dt)))
    if smoothing_samples > 1:
        envelope = np.convolve(raw_envelope, np.ones(smoothing_samples)/smoothing_samples, mode="same")
        compensation = (smoothing_samples - 1) * dt / 2.0
    else:
        envelope, compensation = raw_envelope.copy(), 0.0

    env_initial = window_median(t2_axis, envelope, t1, ENVELOPE_INITIAL_WINDOW, "Initial envelope")
    env_final = window_median(t2_axis, envelope, t1, ENVELOPE_FINAL_WINDOW, "Final envelope")
    step = env_final - env_initial
    level_10 = env_initial + 0.10 * step
    level_90 = env_initial + 0.90 * step

    # level_10 and level_90 are fractions of transition progress from the
    # measured initial envelope to the measured final envelope. Therefore:
    #   RISE: level_10 is 10% amplitude progress; level_90 is 90% progress.
    #   FALL: level_10 is the high threshold after the first 10% of the drop
    #         (about 90% of the original amplitude), and level_90 is the low
    #         threshold after 90% of the drop (about 10% amplitude remaining).
    # Command latency always ends at the first 10% of transition progress.
    if direction == "RISE":
        t_start_raw, start_idx = first_crossing_after(
            t2_axis, envelope, level_10, "rising", t1_idx, persistence
        )
        transition_name = "Rise"
        end_level = level_90
        end_direction = "rising"
    else:
        t_start_raw, start_idx = first_crossing_after(
            t2_axis, envelope, level_10, "falling", t1_idx, persistence
        )
        transition_name = "Fall"
        end_level = level_90
        end_direction = "falling"

    t_start = t_start_raw + compensation
    latency_s = t_start - t1

    # Transition time is optional. Failure to find the ending cursor does not
    # invalidate the already calculated command latency.
    t_end = None
    transition_time = None
    transition_time_valid = False
    transition_time_failure_reason = ""
    try:
        t_end_raw, _ = first_crossing_after(
            t2_axis, envelope, end_level, end_direction, start_idx, persistence
        )
        t_end = t_end_raw + compensation
        transition_time = t_end - t_start
        if transition_time < 0:
            raise RuntimeError(
                f"Calculated {transition_name.lower()} time is negative"
            )
        transition_time_valid = True
    except Exception as exc:
        transition_time_failure_reason = str(exc)

    result = {
        "Descriptor": descriptor, "Direction": direction,
        "Expected_Start_V": start_v, "Expected_End_V": end_v,
        "CH1_Initial_V": ch1_pre, "CH1_Final_V": ch1_post,
        "CH1_Local_Minimum_V": local_min, "CH1_Local_Maximum_V": local_max,
        "CH1_Threshold_V": ch1_level, "CH1_Valid_Candidate_Count": candidate_count,
        "T1_s": t1, "Envelope_Initial_V": env_initial, "Envelope_Final_V": env_final,
        "Envelope_10_Percent_V": level_10, "Envelope_90_Percent_V": level_90,
        "Envelope_10_Percent_Transition_Progress_V": level_10,
        "Envelope_90_Percent_Transition_Progress_V": level_90,
        "Latency_Envelope_Threshold_V": level_10,
        "T2_s": t_start, "Command_Latency_s": latency_s,
        "Command_Latency_us": latency_s*1e6,
        "Latency_Valid": True, "Latency_Failure_Reason": "",
        "Transition_Start_s": t_start, "Transition_End_s": t_end,
        "Rise_Time_s": transition_time if direction == "RISE" and transition_time_valid else None,
        "Rise_Time_us": transition_time*1e6 if direction == "RISE" and transition_time_valid else None,
        "Fall_Time_s": transition_time if direction == "FALL" and transition_time_valid else None,
        "Fall_Time_us": transition_time*1e6 if direction == "FALL" and transition_time_valid else None,
        "Transition_Time_s": transition_time,
        "Transition_Time_us": transition_time*1e6 if transition_time_valid else None,
        "Transition_Time_Valid": transition_time_valid,
        "Transition_Time_Failure_Reason": transition_time_failure_reason,
        "Any_Metric_Valid": True,
        "Sample_Interval_s": dt, "Smoothing_Samples": smoothing_samples,
        "Smoothing_Compensation_s": compensation,
    }
    return result, raw_envelope, envelope

def save_capture_plot(path, t, ch1, raw_env, env, result):
    x = t*1e6
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    axes[0].plot(x, ch1, label="CH1 analog setpoint")
    axes[0].axhline(result["CH1_Threshold_V"], label="CH1 50% cursor")
    axes[0].axvline(result["T1_s"]*1e6, label="T1")
    axes[0].set_ylabel("Voltage (V)"); axes[0].legend()
    axes[1].plot(x, raw_env, alpha=0.6, label="Raw Hilbert envelope")
    if result["Smoothing_Samples"] > 1: axes[1].plot(x, env, label="Processed envelope")
    axes[1].axhline(result["Envelope_10_Percent_V"], label="10% transition-progress cursor")
    axes[1].axhline(result["Envelope_90_Percent_V"], label="90% transition-progress cursor")
    axes[1].axvline(result["Transition_Start_s"]*1e6, label="Transition start")
    if result["Transition_End_s"] is not None:
        axes[1].axvline(result["Transition_End_s"]*1e6, label="Transition end")
    axes[1].set_xlabel("Time (us)"); axes[1].set_ylabel("Envelope (V)"); axes[1].legend()
    kind = "rise" if result["Direction"] == "RISE" else "fall"
    transition_text = (
        f"{kind} time {result['Transition_Time_us']:.3f} us"
        if result["Transition_Time_Valid"]
        else f"{kind} time unavailable"
    )
    fig.suptitle(
        f"{result['Descriptor']} | latency {result['Command_Latency_us']:.3f} us | "
        f"{transition_text}"
    )
    fig.tight_layout(); fig.savefig(path, dpi=PLOT_DPI); plt.close(fig)

def write_csv(path, rows):
    fields = []
    for row in rows:
        for key in row:
            if key not in fields: fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)

def summarize(rows):
    groups = {}
    for row in rows:
        if not row.get("Analysis_Valid"):
            continue
        key = (float(row["Expected_Start_V"]), float(row["Expected_End_V"]), row["Direction"])
        group = groups.setdefault(key, {"latency": [], "transition": []})
        if row.get("Latency_Valid") and row.get("Command_Latency_us") is not None:
            group["latency"].append(float(row["Command_Latency_us"]))
        if row.get("Transition_Time_Valid") and row.get("Transition_Time_us") is not None:
            group["transition"].append(float(row["Transition_Time_us"]))
    summaries=[]
    for (start,end,direction), values in sorted(groups.items()):
        lat=np.asarray(values["latency"],float); tr=np.asarray(values["transition"],float)
        summaries.append({
            "State_Change":f"{start:g}V_to_{end:g}V","Direction":direction,"Start_V":start,"End_V":end,
            "Average_Latency_us":float(np.mean(lat)) if lat.size else None,
            "Minimum_Latency_us":float(np.min(lat)) if lat.size else None,
            "Maximum_Latency_us":float(np.max(lat)) if lat.size else None,
            "Latency_Standard_Deviation_us":float(np.std(lat,ddof=1)) if lat.size>1 else (0.0 if lat.size==1 else None),
            "Valid_Latency_Count":int(lat.size),
            "Average_Rise_Time_us":float(np.mean(tr)) if direction=="RISE" and tr.size else None,
            "Average_Fall_Time_us":float(np.mean(tr)) if direction=="FALL" and tr.size else None,
            "Average_Transition_Time_us":float(np.mean(tr)) if tr.size else None,
            "Minimum_Transition_Time_us":float(np.min(tr)) if tr.size else None,
            "Maximum_Transition_Time_us":float(np.max(tr)) if tr.size else None,
            "Transition_Time_Standard_Deviation_us":float(np.std(tr,ddof=1)) if tr.size>1 else (0.0 if tr.size==1 else None),
            "Valid_Transition_Time_Count":int(tr.size),
        })
    return summaries

def save_summary_plot(path, summaries):
    labels = [f"{r['Start_V']:g} V to {r['End_V']:g} V" for r in summaries]
    latency = np.asarray([r["Average_Latency_us"] for r in summaries])
    transition = np.asarray([
        np.nan if r["Average_Transition_Time_us"] is None else r["Average_Transition_Time_us"]
        for r in summaries
    ])
    x = np.arange(len(labels)); width = 0.36
    fig, ax = plt.subplots(figsize=(12, 7))
    b1 = ax.bar(x-width/2, latency, width, label="Average command latency")
    b2 = ax.bar(x+width/2, np.nan_to_num(transition), width, label="Average RF rise/fall time")
    ax.set_title("Analog Processing Timing: Average Latency and RF Transition Time by State Change")
    ax.set_ylabel("Time (us)"); ax.set_xlabel("Analog setpoint state change")
    ax.set_xticks(x); ax.set_xticklabels(labels, rotation=30, ha="right"); ax.legend()
    for bar in b1:
        ax.annotate(f"{bar.get_height():.3f}", (bar.get_x()+bar.get_width()/2, bar.get_height()),
                    xytext=(0,3), textcoords="offset points", ha="center", va="bottom")
    for bar, value in zip(b2, transition):
        if np.isnan(value):
            bar.set_alpha(0.15)
            ax.annotate("N/A", (bar.get_x()+bar.get_width()/2, 0), xytext=(0,3), textcoords="offset points", ha="center")
        else:
            ax.annotate(f"{value:.3f}", (bar.get_x()+bar.get_width()/2, bar.get_height()),
                        xytext=(0,3), textcoords="offset points", ha="center", va="bottom")
    fig.tight_layout(); fig.savefig(path, dpi=PLOT_DPI); plt.close(fig)

def safe_name(value, fallback="Dataset"):
    return path_component_strict(value, fallback)


def unique_output(parent: Path, label: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return create_unique_folder(parent, f"{stamp} - Analog Processing Timing Analysis - {safe_name(label)}")


def analyze_dataset(folder: Path, output_parent: Path, label: str, smoothing_s: float, progress) -> dict:
    global ENVELOPE_SMOOTHING_S
    ENVELOPE_SMOOTHING_S=float(smoothing_s)
    pairs,incomplete=pair_files(folder)
    output=unique_output(output_parent,label)
    rows=[]
    total=len(pairs)
    for index,(descriptor,direction,ch1_path,ch2_path) in enumerate(pairs,1):
        progress(f"{label}: {index}/{total} {descriptor}")
        try:
            t1,ch1=read_scope_csv(ch1_path); t2,ch2=read_scope_csv(ch2_path)
            result,raw_env,env=analyze_pair(descriptor,direction,t1,ch1,t2,ch2)
            result.update({"Dataset_Label":label,"CH1_File":str(ch1_path),"CH2_File":str(ch2_path),"Analysis_Valid":True,"Failure_Reason":""})
            save_capture_plot(output/f"{safe_name(descriptor)}_Latency_And_Transition_Time.png",t1,ch1,raw_env,env,result)
        except Exception as exc:
            result={"Dataset_Label":label,"Descriptor":descriptor,"Direction":direction,"CH1_File":str(ch1_path),"CH2_File":str(ch2_path),"Analysis_Valid":False,"Failure_Reason":str(exc)}
        rows.append(result)
        write_csv(output/'Latency_And_Transition_Time_Results.csv',rows)
    summaries=summarize(rows)
    if summaries:
        for r in summaries:r["Dataset_Label"]=label
        write_csv(output/'Averages_By_State_Change.csv',summaries)
        save_summary_plot(output/'Average_Latency_And_Transition_Time.png',summaries)
    incomplete_rows=[{"Descriptor":k,"Channels_Present":str(v)} for k,v in incomplete.items()]
    if incomplete_rows: write_csv(output/'Incomplete_Waveform_Pairs.csv',incomplete_rows)
    manifest={"analysis_id":ANALYSIS_ID,"analysis_name":ANALYSIS_NAME,"schema_version":"1.0",
      "dataset_label":label,"input_folder":str(folder),"output_folder":str(output),
      "measurement_contract":{"ch1_cursor_fraction":CH1_CURSOR_FRACTION,"envelope_method":"Hilbert magnitude",
      "latency":"CH1 50% to first CH2 10% transition-progress crossing","transition_time":"CH2 10% to 90% transition progress",
      "crossing_persistence_s":CROSSING_PERSISTENCE_S,"smoothing_s":ENVELOPE_SMOOTHING_S},
      "pair_count":len(pairs),"valid_count":sum(bool(r.get('Analysis_Valid')) for r in rows),
      "invalid_count":sum(not bool(r.get('Analysis_Valid')) for r in rows),"incomplete_pair_count":len(incomplete),
      "files":[p.name for p in output.iterdir()]}
    (output/'Analysis_Manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return {"label":label,"output":output,"rows":rows,"summaries":summaries,"manifest":manifest}


def write_comparison(results,output_parent):
    if len(results)!=2:return None
    output=unique_output(output_parent,f"{results[0]['label']} vs {results[1]['label']}")
    combined=[]
    for result in results:combined.extend(result['summaries'])
    if combined:write_csv(output/'Dataset_Comparison.csv',combined)
    fig,ax=plt.subplots(figsize=(12,7))
    for result in results:
        labels=[f"{r['Start_V']:g} to {r['End_V']:g} V" for r in result['summaries']]
        values=[r['Average_Latency_us'] for r in result['summaries']]
        ax.plot(labels,values,marker='o',label=result['label'])
    ax.set(title='Analog Processing Timing Latency Comparison',xlabel='Setpoint state change',ylabel='Average latency (us)')
    ax.tick_params(axis='x',rotation=30); ax.grid(True,alpha=.3); ax.legend(); fig.tight_layout(); fig.savefig(output/'Latency_Comparison.png',dpi=PLOT_DPI); plt.close(fig)
    return output


class AnalysisGUI:
    def __init__(self,root):
        import tkinter as tk
        from tkinter import ttk
        self.root=root; self.tk=tk; self.ttk=ttk; self.running=False; self.exit_code=2
        mode=os.environ.get('PARAMOUNT_ANALYSIS_MODE','single').lower()
        self.mode=tk.StringVar(value='comparison' if mode=='comparison' else 'single')
        self.af=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_A_FOLDER',''))
        self.bf=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_B_FOLDER',''))
        self.al=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_A_LABEL','Dataset A') or 'Dataset A')
        self.bl=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_B_LABEL','Dataset B') or 'Dataset B')
        self.out=tk.StringVar(value=os.environ.get('PARAMOUNT_ANALYSIS_OUTPUT_FOLDER','') or self.af.get() or str(Path.home()))
        self.smooth=tk.BooleanVar(value=False); self.status=tk.StringVar(value='Ready'); self._build()
    def _build(self):
        t=self.ttk;r=self.root;r.title('Analog Processing Timing Analysis');r.geometry('920x720');r.minsize(800,620)
        o=t.Frame(r,padding=14);o.pack(fill='both',expand=True)
        t.Label(o,text='Analog Processing Timing Analysis',font=('Segoe UI',16,'bold')).pack(anchor='w')
        t.Label(o,text='Validated measurement method: CH1 50% command crossing; CH2 Hilbert-envelope 10% latency and 10%-90% transition time.').pack(anchor='w',pady=(2,10))
        m=t.LabelFrame(o,text='Analysis Mode',padding=10);m.pack(fill='x');t.Radiobutton(m,text='Single dataset',variable=self.mode,value='single',command=self._mode).pack(side='left');t.Radiobutton(m,text='Compare two datasets',variable=self.mode,value='comparison',command=self._mode).pack(side='left',padx=18)
        d=t.LabelFrame(o,text='Datasets',padding=10);d.pack(fill='x',pady=10);d.columnconfigure(2,weight=1)
        t.Label(d,text='Dataset A').grid(row=0,column=0,sticky='w');t.Entry(d,textvariable=self.al,width=18).grid(row=0,column=1,padx=6);t.Entry(d,textvariable=self.af).grid(row=0,column=2,sticky='ew');t.Button(d,text='Browse...',command=lambda:self._browse(self.af)).grid(row=0,column=3,padx=6)
        t.Label(d,text='Dataset B').grid(row=1,column=0,sticky='w');self.ble=t.Entry(d,textvariable=self.bl,width=18);self.ble.grid(row=1,column=1,padx=6);self.bfe=t.Entry(d,textvariable=self.bf);self.bfe.grid(row=1,column=2,sticky='ew');self.bfb=t.Button(d,text='Browse...',command=lambda:self._browse(self.bf));self.bfb.grid(row=1,column=3,padx=6)
        t.Label(d,text='Results parent').grid(row=2,column=0,sticky='w');t.Entry(d,textvariable=self.out).grid(row=2,column=1,columnspan=2,sticky='ew',padx=6);t.Button(d,text='Browse...',command=lambda:self._browse(self.out)).grid(row=2,column=3,padx=6)
        opts=t.LabelFrame(o,text='Measurement Processing',padding=10);opts.pack(fill='x')
        t.Checkbutton(opts,text='Apply optional 2.0 us moving-average smoothing to the RF envelope',variable=self.smooth).pack(anchor='w')
        t.Label(opts,text='Unchecked preserves the validated unsmoothed default. Crossing thresholds, persistence, windows, and interpolation are unchanged.',foreground='#555555').pack(anchor='w')
        self.start=t.Button(o,text='START ANALOG PROCESSING TIMING ANALYSIS',command=self._start);self.start.pack(fill='x',ipady=10,pady=10)
        p=t.LabelFrame(o,text='Progress',padding=10);p.pack(fill='both',expand=True);self.bar=t.Progressbar(p,mode='indeterminate');self.bar.pack(fill='x');self.log=self.tk.Text(p,height=12,state='disabled',wrap='word');self.log.pack(fill='both',expand=True,pady=(8,0))
        f=t.Frame(o);f.pack(fill='x',pady=(10,0));t.Label(f,textvariable=self.status).pack(side='left');self.close=t.Button(f,text='Close',command=self._close);self.close.pack(side='right');r.protocol('WM_DELETE_WINDOW',self._close);self._mode()
    def _mode(self):
        state='normal' if self.mode.get()=='comparison' else 'disabled'
        for w in (self.ble,self.bfe,self.bfb):w.configure(state=state)
    def _browse(self,var):
        from tkinter import filedialog
        v=filedialog.askdirectory(parent=self.root,initialdir=var.get() or str(Path.home()))
        if v:var.set(v)
    def _say(self,msg):
        self.log.configure(state='normal');self.log.insert('end',msg+'\n');self.log.see('end');self.log.configure(state='disabled')
    def _start(self):
        from tkinter import messagebox
        data=[(self.af.get().strip(),self.al.get().strip() or 'Dataset A')]
        if self.mode.get()=='comparison':data.append((self.bf.get().strip(),self.bl.get().strip() or 'Dataset B'))
        out=Path(self.out.get().strip()).expanduser()
        if not out.is_dir() or any(not Path(f).expanduser().is_dir() for f,_ in data):messagebox.showerror('Invalid folder','Select existing dataset and results-parent folders.',parent=self.root);return
        self.running=True;self.start.configure(state='disabled');self.close.configure(state='disabled');self.bar.start(10);self.status.set('Analyzing')
        def worker():
            try:
                results=[analyze_dataset(Path(f).expanduser(),out,l,OPTIONAL_SMOOTHING_S if self.smooth.get() else 0.0,lambda m:self.root.after(0,lambda x=m:self._say(x))) for f,l in data]
                comp=write_comparison(results,out);self.root.after(0,lambda:self._finish(0,results,comp,None))
            except Exception as e:traceback.print_exc();self.root.after(0,lambda error=e:self._finish(1,[],None,error))
        threading.Thread(target=worker,daemon=True).start()
    def _finish(self,code,results,comp,error):
        from tkinter import messagebox
        self.running=False;self.exit_code=code;self.bar.stop();self.start.configure(state='normal');self.close.configure(state='normal')
        if code==0:
            self.status.set('Completed successfully');paths='\n'.join(str(r['output']) for r in results)+((f'\n{comp}') if comp else '');messagebox.showinfo('Analysis complete',f'Results saved under:\n{paths}',parent=self.root)
        else:self.status.set('Analysis failed');self._say(f'ERROR: {error}');messagebox.showerror('Analysis failed',str(error),parent=self.root)
    def _close(self):
        from tkinter import messagebox
        if self.running:messagebox.showwarning('Analysis running','Wait for analysis to finish before closing.',parent=self.root);return
        self.root.destroy()


def main():
    import tkinter as tk
    root=tk.Tk();app=AnalysisGUI(root);root.mainloop();return app.exit_code
if __name__=='__main__':raise SystemExit(main())
