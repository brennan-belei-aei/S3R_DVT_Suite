"""RF Enable Timing waveform analysis GUI.

Analyzes the three local waveform CSV files produced for each RF ON/OFF capture:
CH1 RF Enable, CH2 RF Monitor, and CH3 RF Detector. Measurements preserve the
validated timing structure: baseline-referenced, direction-aware thresholds,
persistent crossings, and linear crossing interpolation. CH2 is converted to a
Hilbert envelope before threshold measurement; CH3 is measured directly.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import hilbert

ANALYSIS_ID = "rf_enable_timing"
ANALYSIS_NAME = "RF Enable Timing"
EXPECTED_CONTRACT = "Paramount.RFEnableTiming"
SUPPORTED_CONTRACT_MAJOR = 1

# Validated measurement definitions. Keep these independent for each signal.
COMMAND_THRESHOLD_FRACTION = 0.50
RESPONSE_THRESHOLD_FRACTION = 0.10
COMMAND_PRE_WINDOW_S = (-500e-6, -50e-6)
COMMAND_POST_WINDOW_S = (50e-6, 500e-6)
RESPONSE_PRE_WINDOW_S = (-500e-6, -50e-6)
RESPONSE_POST_WINDOW_S = (500e-6, 2.0e-3)
CROSSING_PERSISTENCE_S = 1.0e-6
PLOT_DPI = 150

REQUIRED_CAPTURE_COLUMNS = {
    "Requested_Power_W", "Cycle", "Edge", "Triggered",
    "CH1_File", "CH2_File", "CH3_File",
}


def bool_value(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def safe_name(value: object, fallback: str = "Dataset") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "").strip())
    return re.sub(r"\s+", " ", text).strip(" .") or fallback


def unique_output(parent: Path, label: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = parent / f"{stamp} - {ANALYSIS_NAME} Analysis - {safe_name(label)}"
    candidate = base; run = 1
    while candidate.exists():
        run += 1; candidate = parent / f"{base.name} - Run {run}"
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


def read_waveform_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read both new local two-column CSVs and legacy Tektronix CSV exports."""
    if not path.is_file():
        raise FileNotFoundError(path)
    # First try the new Ethernet-transfer format.
    try:
        data = np.genfromtxt(path, delimiter=",", names=True, dtype=float,
                             encoding="utf-8-sig", invalid_raise=False)
        if data.dtype.names and len(data.dtype.names) >= 2:
            time_values = np.asarray(data[data.dtype.names[0]], dtype=float)
            signal_values = np.asarray(data[data.dtype.names[1]], dtype=float)
            valid = np.isfinite(time_values) & np.isfinite(signal_values)
            time_values, signal_values = time_values[valid], signal_values[valid]
            if time_values.size >= 3:
                order = np.argsort(time_values); time_values = time_values[order]; signal_values = signal_values[order]
                keep = np.concatenate(([True], np.diff(time_values) > 0))
                return time_values[keep], signal_values[keep]
    except Exception:
        pass
    # Legacy scope spreadsheet export: find the first two consistently numeric columns.
    rows = []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            values = []
            for item in row:
                try: values.append(float(item.strip()))
                except Exception: values.append(float("nan"))
            rows.append(values)
    width = max((len(row) for row in rows), default=0)
    if width < 2:
        raise ValueError(f"No usable waveform columns in {path.name}.")
    matrix = np.full((len(rows), width), np.nan)
    for index, row in enumerate(rows): matrix[index, :len(row)] = row
    best = None
    for first in range(width):
        for second in range(first + 1, width):
            valid = np.isfinite(matrix[:, first]) & np.isfinite(matrix[:, second])
            count = int(valid.sum())
            if count >= 3 and (best is None or count > best[0]): best = (count, first, second, valid)
    if best is None:
        raise ValueError(f"Could not identify time and voltage columns in {path.name}.")
    _, first, second, valid = best
    time_values, signal_values = matrix[valid, first], matrix[valid, second]
    order = np.argsort(time_values); time_values, signal_values = time_values[order], signal_values[order]
    keep = np.concatenate(([True], np.diff(time_values) > 0))
    time_values, signal_values = time_values[keep], signal_values[keep]
    if time_values.size < 3: raise ValueError(f"Too few strictly increasing samples in {path.name}.")
    return time_values, signal_values


def window_median(time_values: np.ndarray, signal: np.ndarray, center: float,
                  relative_window: tuple[float, float]) -> float:
    mask = (time_values >= center + relative_window[0]) & (time_values <= center + relative_window[1])
    if not np.any(mask):
        raise ValueError(f"No samples in window {relative_window} relative to {center:g} s.")
    return float(np.median(signal[mask]))


def interpolated_crossing(t0: float, y0: float, t1: float, y1: float, threshold: float) -> float:
    if y1 == y0: return float(t1)
    fraction = (threshold - y0) / (y1 - y0)
    return float(t0 + np.clip(fraction, 0.0, 1.0) * (t1 - t0))


def persistent_crossing(time_values: np.ndarray, signal: np.ndarray, threshold: float,
                        direction: str, start_time: Optional[float] = None) -> float:
    dt = float(np.median(np.diff(time_values)))
    persistence_samples = max(1, int(math.ceil(CROSSING_PERSISTENCE_S / dt)))
    start_index = 1 if start_time is None else max(1, int(np.searchsorted(time_values, start_time)))
    rising = direction.upper() in {"ON", "RISE"}
    for index in range(start_index, signal.size):
        crossed = signal[index - 1] < threshold <= signal[index] if rising else signal[index - 1] > threshold >= signal[index]
        if not crossed: continue
        stop = min(signal.size, index + persistence_samples)
        persistent = np.all(signal[index:stop] >= threshold) if rising else np.all(signal[index:stop] <= threshold)
        if persistent and stop - index >= persistence_samples:
            return interpolated_crossing(time_values[index-1], signal[index-1], time_values[index], signal[index], threshold)
    raise ValueError(f"No persistent {direction} crossing at {threshold:g}.")


def measured_levels(time_values: np.ndarray, signal: np.ndarray, approximate_center: float,
                    pre_window: tuple[float, float], post_window: tuple[float, float]) -> tuple[float, float]:
    return (window_median(time_values, signal, approximate_center, pre_window),
            window_median(time_values, signal, approximate_center, post_window))


def measure_command(time_values: np.ndarray, signal: np.ndarray, edge: str) -> dict[str, float]:
    # Scope trigger places the event near t=0; use it only to select baseline windows.
    initial, final = measured_levels(time_values, signal, 0.0, COMMAND_PRE_WINDOW_S, COMMAND_POST_WINDOW_S)
    threshold = initial + COMMAND_THRESHOLD_FRACTION * (final - initial)
    crossing = persistent_crossing(time_values, signal, threshold, edge)
    return {"Initial": initial, "Final": final, "Threshold": threshold, "Crossing_s": crossing}


def measure_response(time_values: np.ndarray, signal: np.ndarray, command_time: float,
                     edge: str, envelope: bool) -> tuple[dict[str, float], np.ndarray]:
    processed = np.abs(hilbert(signal)) if envelope else signal.copy()
    initial, final = measured_levels(time_values, processed, command_time,
                                     RESPONSE_PRE_WINDOW_S, RESPONSE_POST_WINDOW_S)
    threshold = initial + RESPONSE_THRESHOLD_FRACTION * (final - initial)
    crossing = persistent_crossing(time_values, processed, threshold, edge, start_time=command_time)
    return ({"Initial": initial, "Final": final, "Threshold": threshold,
             "Crossing_s": crossing, "Latency_s": crossing - command_time}, processed)


def resolve_file(run_folder: Path, stored: Any) -> Path:
    text = str(stored or "").strip()
    if not text: raise ValueError("Waveform file path is blank.")
    path = Path(text)
    if path.is_file(): return path
    local = run_folder / path.name
    if local.is_file(): return local
    matches = list(run_folder.rglob(path.name))
    if len(matches) == 1: return matches[0]
    raise FileNotFoundError(f"Could not resolve waveform {text!r} under {run_folder}.")


def validate_contract(run_folder: Path) -> dict[str, Any]:
    path = run_folder / "Output_Contract.json"
    if not path.is_file(): return {"warning": "Output_Contract.json not present; CSV validation used."}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("contract_name") != EXPECTED_CONTRACT:
        raise ValueError(f"Expected contract {EXPECTED_CONTRACT!r}; found {data.get('contract_name')!r}.")
    version = str(data.get("contract_version", "0"))
    if int(version.split(".")[0]) != SUPPORTED_CONTRACT_MAJOR:
        raise ValueError(f"Unsupported contract version {version}.")
    return data


def save_capture_plot(path: Path, time_1, ch1, time_2, ch2_env, time_3, ch3, result):
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    datasets = [(time_1, ch1, "CH1 RF Enable", result["Command_Threshold_V"], result["Command_Crossing_s"]),
                (time_2, ch2_env, "CH2 RF Monitor Envelope", result["RF_Monitor_Threshold_V"], result["RF_Monitor_Crossing_s"]),
                (time_3, ch3, "CH3 RF Detector", result["RF_Detector_Threshold_V"], result["RF_Detector_Crossing_s"])]
    for axis, (times, values, label, threshold, crossing) in zip(axes, datasets):
        axis.plot(times * 1e6, values, linewidth=1); axis.axhline(threshold, color="tab:orange", linestyle="--")
        axis.axvline(crossing * 1e6, color="tab:red", linestyle=":"); axis.set_ylabel(label + " (V)"); axis.grid(True, alpha=.3)
    axes[-1].set_xlabel("Time (us)")
    fig.suptitle(f"{result['Requested_Power_W']:g} W, Cycle {result['Cycle']}, RF {result['Edge']}")
    fig.tight_layout(); fig.savefig(path, dpi=PLOT_DPI); plt.close(fig)


def analyze_capture(run_folder: Path, row: dict[str, Any], output: Path) -> dict[str, Any]:
    edge = str(row["Edge"]).strip().upper()
    if edge not in {"ON", "OFF"}: raise ValueError(f"Unsupported edge {edge!r}.")
    paths = [resolve_file(run_folder, row[f"CH{channel}_File"]) for channel in (1,2,3)]
    waveforms = [read_waveform_csv(path) for path in paths]
    command = measure_command(*waveforms[0], edge)
    monitor, monitor_envelope = measure_response(*waveforms[1], command["Crossing_s"], edge, envelope=True)
    detector, _ = measure_response(*waveforms[2], command["Crossing_s"], edge, envelope=False)
    result = {
        "Requested_Power_W": float(row["Requested_Power_W"]), "Cycle": int(float(row["Cycle"])), "Edge": edge,
        "Command_Initial_V": command["Initial"], "Command_Final_V": command["Final"],
        "Command_Threshold_V": command["Threshold"], "Command_Crossing_s": command["Crossing_s"],
        "RF_Monitor_Initial_V": monitor["Initial"], "RF_Monitor_Final_V": monitor["Final"],
        "RF_Monitor_Threshold_V": monitor["Threshold"], "RF_Monitor_Crossing_s": monitor["Crossing_s"],
        "RF_Monitor_Latency_s": monitor["Latency_s"], "RF_Monitor_Latency_us": monitor["Latency_s"]*1e6,
        "RF_Detector_Initial_V": detector["Initial"], "RF_Detector_Final_V": detector["Final"],
        "RF_Detector_Threshold_V": detector["Threshold"], "RF_Detector_Crossing_s": detector["Crossing_s"],
        "RF_Detector_Latency_s": detector["Latency_s"], "RF_Detector_Latency_us": detector["Latency_s"]*1e6,
        "CH1_File": str(paths[0]), "CH2_File": str(paths[1]), "CH3_File": str(paths[2]),
        "Analysis_Valid": True, "Failure_Reason": "",
    }
    plot_name = safe_name(f"{result['Requested_Power_W']:g}W_Cycle{result['Cycle']}_RF_{edge}") + ".png"
    save_capture_plot(output/plot_name, *waveforms[0], waveforms[1][0], monitor_envelope,
                      *waveforms[2], result)
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows: return
    fields=[]
    for row in rows:
        for key in row:
            if key not in fields: fields.append(key)
    pd.DataFrame(rows).reindex(columns=fields).to_csv(path,index=False,encoding="utf-8-sig")


def summarize(valid_frame: pd.DataFrame) -> pd.DataFrame:
    return valid_frame.groupby(["Requested_Power_W","Edge"],as_index=False).agg(
        Capture_Count=("Cycle","count"),
        RF_Monitor_Average_Latency_us=("RF_Monitor_Latency_us","mean"),
        RF_Monitor_Minimum_Latency_us=("RF_Monitor_Latency_us","min"),
        RF_Monitor_Maximum_Latency_us=("RF_Monitor_Latency_us","max"),
        RF_Monitor_StdDev_Latency_us=("RF_Monitor_Latency_us","std"),
        RF_Detector_Average_Latency_us=("RF_Detector_Latency_us","mean"),
        RF_Detector_Minimum_Latency_us=("RF_Detector_Latency_us","min"),
        RF_Detector_Maximum_Latency_us=("RF_Detector_Latency_us","max"),
        RF_Detector_StdDev_Latency_us=("RF_Detector_Latency_us","std"),
    ).fillna(0.0)


def save_summary_plot(path: Path, summary: pd.DataFrame, label: str) -> None:
    fig, axes = plt.subplots(1,2,figsize=(13,5),sharey=True)
    for axis, edge in zip(axes,("ON","OFF")):
        part=summary.loc[summary["Edge"]==edge]
        for prefix,name,marker in (("RF_Monitor","RF Monitor","o"),("RF_Detector","RF Detector","s")):
            axis.errorbar(part["Requested_Power_W"],part[f"{prefix}_Average_Latency_us"],
                          yerr=part[f"{prefix}_StdDev_Latency_us"],marker=marker,capsize=3,label=name)
        axis.set_title(f"RF {edge}");axis.set_xlabel("Requested Power (W)");axis.grid(True,alpha=.3);axis.legend()
    axes[0].set_ylabel("Latency (us)");fig.suptitle(f"{label}: RF Enable Timing");fig.tight_layout();fig.savefig(path,dpi=PLOT_DPI);plt.close(fig)


def analyze_dataset(folder: Path, output_parent: Path, label: str, progress) -> dict[str,Any]:
    capture_path=folder/"Capture_Results.csv"
    if not capture_path.is_file():
        matches=list(folder.rglob("Capture_Results.csv"))
        if len(matches)!=1: raise FileNotFoundError("Select one RF Enable Timing run folder containing Capture_Results.csv.")
        capture_path=matches[0]
    run_folder=capture_path.parent;contract=validate_contract(run_folder)
    captures=pd.read_csv(capture_path,encoding="utf-8-sig")
    missing=sorted(REQUIRED_CAPTURE_COLUMNS-set(captures.columns))
    if missing: raise ValueError("Capture_Results.csv is missing: "+", ".join(missing))
    output=unique_output(output_parent,label);results=[]
    eligible=captures.loc[captures["Triggered"].map(bool_value)].copy()
    for index,(_,row) in enumerate(eligible.iterrows(),1):
        progress(f"{label}: {index}/{len(eligible)} {row['Requested_Power_W']} W RF {row['Edge']}")
        base={"Requested_Power_W":row["Requested_Power_W"],"Cycle":row["Cycle"],"Edge":row["Edge"]}
        try: result=analyze_capture(run_folder,row.to_dict(),output)
        except Exception as exc: result={**base,"Analysis_Valid":False,"Failure_Reason":str(exc),"CH1_File":row.get("CH1_File"),"CH2_File":row.get("CH2_File"),"CH3_File":row.get("CH3_File")}
        results.append(result);write_csv(output/"RF_Enable_Timing_Results.csv",results)
    valid=pd.DataFrame([row for row in results if row.get("Analysis_Valid")])
    if valid.empty: raise RuntimeError("No capture produced valid timing measurements.")
    summary=summarize(valid);summary.insert(0,"Dataset_Label",label)
    write_csv(output/"Averages_By_Power_And_Edge.csv",summary.to_dict("records"))
    save_summary_plot(output/"Average_RF_Enable_Timing.png",summary,label)
    manifest={"analysis_id":ANALYSIS_ID,"analysis_name":ANALYSIS_NAME,"schema_version":"1.0",
              "dataset_label":label,"input_folder":str(run_folder),"output_folder":str(output),
              "measurement_contract":{"command":"CH1 baseline-referenced 50% persistent crossing",
              "rf_monitor":"CH2 Hilbert-envelope baseline-referenced 10% persistent crossing",
              "rf_detector":"CH3 baseline-referenced 10% persistent crossing",
              "persistence_s":CROSSING_PERSISTENCE_S,"interpolation":"linear"},
              "valid_count":int(valid.shape[0]),"invalid_count":len(results)-int(valid.shape[0]),
              "source_contract":contract,"files":sorted(path.name for path in output.iterdir())}
    (output/"Analysis_Manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return {"label":label,"output":output,"summary":summary,"results":results}


def write_comparison(results,output_parent):
    if len(results)!=2:return None
    output=unique_output(output_parent,f"{results[0]['label']} vs {results[1]['label']}")
    combined=pd.concat([r["summary"] for r in results],ignore_index=True);combined.to_csv(output/"Dataset_Comparison.csv",index=False,encoding="utf-8-sig")
    return output


class AnalysisGUI:
    def __init__(self,root):
        import tkinter as tk
        from tkinter import ttk
        self.root=root;self.tk=tk;self.ttk=ttk;self.running=False;self.exit_code=2
        mode=os.environ.get("PARAMOUNT_ANALYSIS_MODE","single").lower();self.mode=tk.StringVar(value="comparison" if mode=="comparison" else "single")
        self.af=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_FOLDER",""));self.bf=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_FOLDER",""))
        self.al=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_LABEL","Dataset A") or "Dataset A");self.bl=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_LABEL","Dataset B") or "Dataset B")
        self.out=tk.StringVar(value=os.environ.get("PARAMOUNT_ANALYSIS_OUTPUT_FOLDER","") or self.af.get() or str(Path.home()));self.status=tk.StringVar(value="Ready");self._build()
    def _build(self):
        t=self.ttk;r=self.root;r.title("RF Enable Timing Analysis");r.geometry("920x690");r.minsize(800,600)
        o=t.Frame(r,padding=14);o.pack(fill="both",expand=True);t.Label(o,text="RF Enable Timing Analysis",font=("Segoe UI",16,"bold")).pack(anchor="w")
        t.Label(o,text="Measure RF ON/OFF latency independently for the RF Monitor and RF Detector.").pack(anchor="w",pady=(2,10))
        m=t.LabelFrame(o,text="Analysis Mode",padding=10);m.pack(fill="x");t.Radiobutton(m,text="Single dataset",variable=self.mode,value="single",command=self._mode).pack(side="left");t.Radiobutton(m,text="Compare two datasets",variable=self.mode,value="comparison",command=self._mode).pack(side="left",padx=18)
        d=t.LabelFrame(o,text="Datasets",padding=10);d.pack(fill="x",pady=10);d.columnconfigure(2,weight=1)
        t.Label(d,text="Dataset A").grid(row=0,column=0,sticky="w");t.Entry(d,textvariable=self.al,width=18).grid(row=0,column=1,padx=6);t.Entry(d,textvariable=self.af).grid(row=0,column=2,sticky="ew");t.Button(d,text="Browse...",command=lambda:self._browse(self.af)).grid(row=0,column=3,padx=6)
        t.Label(d,text="Dataset B").grid(row=1,column=0,sticky="w");self.ble=t.Entry(d,textvariable=self.bl,width=18);self.ble.grid(row=1,column=1,padx=6);self.bfe=t.Entry(d,textvariable=self.bf);self.bfe.grid(row=1,column=2,sticky="ew");self.bfb=t.Button(d,text="Browse...",command=lambda:self._browse(self.bf));self.bfb.grid(row=1,column=3,padx=6)
        t.Label(d,text="Results parent").grid(row=2,column=0,sticky="w");t.Entry(d,textvariable=self.out).grid(row=2,column=1,columnspan=2,sticky="ew",padx=6);t.Button(d,text="Browse...",command=lambda:self._browse(self.out)).grid(row=2,column=3,padx=6)
        self.start=t.Button(o,text="START RF ENABLE TIMING ANALYSIS",command=self._start);self.start.pack(fill="x",ipady=10,pady=10)
        p=t.LabelFrame(o,text="Progress",padding=10);p.pack(fill="both",expand=True);self.bar=t.Progressbar(p,mode="indeterminate");self.bar.pack(fill="x");self.log=self.tk.Text(p,height=13,state="disabled",wrap="word");self.log.pack(fill="both",expand=True,pady=(8,0))
        f=t.Frame(o);f.pack(fill="x",pady=(10,0));t.Label(f,textvariable=self.status).pack(side="left");self.close=t.Button(f,text="Close",command=self._close);self.close.pack(side="right");r.protocol("WM_DELETE_WINDOW",self._close);self._mode()
    def _mode(self):
        state="normal" if self.mode.get()=="comparison" else "disabled"
        for widget in (self.ble,self.bfe,self.bfb):widget.configure(state=state)
    def _browse(self,var):
        from tkinter import filedialog
        value=filedialog.askdirectory(parent=self.root,initialdir=var.get() or str(Path.home()))
        if value:var.set(value)
    def _say(self,message):
        self.log.configure(state="normal");self.log.insert("end",message+"\n");self.log.see("end");self.log.configure(state="disabled")
    def _start(self):
        from tkinter import messagebox
        datasets=[(self.af.get().strip(),self.al.get().strip() or "Dataset A")]
        if self.mode.get()=="comparison":datasets.append((self.bf.get().strip(),self.bl.get().strip() or "Dataset B"))
        out=Path(self.out.get().strip()).expanduser()
        if not out.is_dir() or any(not Path(folder).expanduser().is_dir() for folder,_ in datasets):messagebox.showerror("Invalid folder","Select existing dataset and results-parent folders.",parent=self.root);return
        self.running=True;self.start.configure(state="disabled");self.close.configure(state="disabled");self.bar.start(10);self.status.set("Analyzing")
        def worker():
            try:
                results=[analyze_dataset(Path(folder).expanduser(),out,label,lambda msg:self.root.after(0,lambda x=msg:self._say(x))) for folder,label in datasets]
                comparison=write_comparison(results,out);self.root.after(0,lambda:self._finish(0,results,comparison,None))
            except Exception as exc:
                traceback.print_exc();self.root.after(0,lambda error=exc:self._finish(1,[],None,error))
        threading.Thread(target=worker,daemon=True).start()
    def _finish(self,code,results,comparison,error):
        from tkinter import messagebox
        self.running=False;self.exit_code=code;self.bar.stop();self.start.configure(state="normal");self.close.configure(state="normal")
        if code==0:
            self.status.set("Completed successfully");folders="\n".join(str(result["output"]) for result in results)+((f"\n{comparison}") if comparison else "");messagebox.showinfo("Analysis complete",f"Results saved under:\n{folders}",parent=self.root)
        else:self.status.set("Analysis failed");self._say(f"ERROR: {error}");messagebox.showerror("Analysis failed",str(error),parent=self.root)
    def _close(self):
        from tkinter import messagebox
        if self.running:messagebox.showwarning("Analysis running","Wait for analysis to finish before closing.",parent=self.root);return
        self.root.destroy()


def main()->int:
    import tkinter as tk
    root=tk.Tk();app=AnalysisGUI(root);root.mainloop();return app.exit_code

if __name__=="__main__":raise SystemExit(main())
