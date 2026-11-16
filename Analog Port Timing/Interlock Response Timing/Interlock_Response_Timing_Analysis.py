"""Interlock Response Timing waveform analysis GUI.

Measures delay from the falling interlock edge on CH1 to RF shutdown on CH2.
CH1 uses a baseline-referenced 50% falling crossing. CH2 is converted to a
Hilbert envelope and uses a baseline-referenced persistent 10% transition-
progress crossing in the falling direction. Crossings use linear interpolation.
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

ANALYSIS_ID = "interlock_response_timing"
ANALYSIS_NAME = "Interlock Response Timing"
EXPECTED_CONTRACT = "Paramount.InterlockResponseTiming"
SUPPORTED_CONTRACT_MAJOR = 1

INTERLOCK_THRESHOLD_FRACTION = 0.50
RF_SHUTDOWN_THRESHOLD_FRACTION = 0.10
INTERLOCK_PRE_WINDOW_S = (-40e-3, -5e-3)
INTERLOCK_POST_WINDOW_S = (5e-3, 40e-3)
RF_PRE_WINDOW_S = (-40e-3, -5e-3)
RF_POST_WINDOW_S = (20e-3, 80e-3)
CROSSING_PERSISTENCE_S = 1.0e-3
PLOT_DPI = 150

REQUIRED_CAPTURE_COLUMNS = {"Requested_Power_W", "Cycle", "Triggered", "CH1_File", "CH2_File"}


def bool_value(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def safe_name(value: object, fallback: str = "Dataset") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "").strip())
    return re.sub(r"\s+", " ", text).strip(" .") or fallback


def unique_output(parent: Path, label: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = parent / f"{stamp} - {ANALYSIS_NAME} Analysis - {safe_name(label)}"
    candidate = base; run = 1
    while candidate.exists(): run += 1; candidate = parent / f"{base.name} - Run {run}"
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


def read_waveform_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read local Ethernet two-column CSVs and legacy scope spreadsheet CSVs."""
    if not path.is_file(): raise FileNotFoundError(path)
    try:
        data = np.genfromtxt(path, delimiter=",", names=True, dtype=float,
                             encoding="utf-8-sig", invalid_raise=False)
        if data.dtype.names and len(data.dtype.names) >= 2:
            t = np.asarray(data[data.dtype.names[0]], float); y = np.asarray(data[data.dtype.names[1]], float)
            good = np.isfinite(t) & np.isfinite(y); t, y = t[good], y[good]
            if t.size >= 20:
                order = np.argsort(t); t, y = t[order], y[order]
                keep = np.concatenate(([True], np.diff(t) > 0)); t, y = t[keep], y[keep]
                if t.size >= 20: return t, y
    except Exception: pass
    rows=[]
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            numeric=[]
            for cell in row:
                try: numeric.append(float(cell))
                except ValueError: pass
            if len(numeric) >= 2: rows.append((numeric[-2], numeric[-1]))
    if len(rows) < 20: raise ValueError(f"Insufficient waveform samples in {path.name}.")
    array=np.asarray(rows,float); good=np.isfinite(array[:,0]) & np.isfinite(array[:,1]); t,y=array[good,0],array[good,1]
    order=np.argsort(t);t,y=t[order],y[order];keep=np.concatenate(([True],np.diff(t)>0));t,y=t[keep],y[keep]
    if t.size < 20: raise ValueError(f"Invalid or non-increasing time data in {path.name}.")
    return t,y


def window_median(t: np.ndarray, y: np.ndarray, center: float, window: tuple[float,float]) -> float:
    mask=(t>=center+window[0]) & (t<=center+window[1])
    if not np.any(mask): raise ValueError(f"No samples in analysis window {window}.")
    return float(np.median(y[mask]))


def interpolate(t0,y0,t1,y1,threshold):
    if y1==y0:return float(t1)
    return float(t0+np.clip((threshold-y0)/(y1-y0),0,1)*(t1-t0))


def persistent_falling_crossing(t,y,threshold,start_time=None):
    dt=float(np.median(np.diff(t))); required=max(1,int(math.ceil(CROSSING_PERSISTENCE_S/dt)))
    start=1 if start_time is None else max(1,int(np.searchsorted(t,start_time)))
    for index in range(start,len(y)):
        if not (y[index-1] > threshold >= y[index]): continue
        stop=min(len(y),index+required)
        if stop-index>=required and np.all(y[index:stop] <= threshold):
            return interpolate(t[index-1],y[index-1],t[index],y[index],threshold)
    raise ValueError(f"No persistent falling crossing at {threshold:g}.")


def measure_capture(t1,ch1,t2,ch2):
    interlock_initial=window_median(t1,ch1,0.0,INTERLOCK_PRE_WINDOW_S)
    interlock_final=window_median(t1,ch1,0.0,INTERLOCK_POST_WINDOW_S)
    interlock_threshold=interlock_initial+INTERLOCK_THRESHOLD_FRACTION*(interlock_final-interlock_initial)
    interlock_crossing=persistent_falling_crossing(t1,ch1,interlock_threshold)
    # Remove the RF carrier's DC baseline before extracting the analytic envelope.
    rf_baseline=window_median(t2,ch2,interlock_crossing,RF_POST_WINDOW_S)
    envelope=np.abs(hilbert(ch2-rf_baseline))
    rf_initial=window_median(t2,envelope,interlock_crossing,RF_PRE_WINDOW_S)
    rf_final=window_median(t2,envelope,interlock_crossing,RF_POST_WINDOW_S)
    rf_threshold=rf_initial+RF_SHUTDOWN_THRESHOLD_FRACTION*(rf_final-rf_initial)
    rf_crossing=persistent_falling_crossing(t2,envelope,rf_threshold,start_time=interlock_crossing)
    return {
        "Interlock_Initial_V":interlock_initial,"Interlock_Final_V":interlock_final,
        "Interlock_Threshold_V":interlock_threshold,"Interlock_Crossing_s":interlock_crossing,
        "RF_Envelope_Initial_V":rf_initial,"RF_Envelope_Final_V":rf_final,
        "RF_Envelope_Threshold_V":rf_threshold,"RF_Shutdown_Crossing_s":rf_crossing,
        "RF_Shutdown_Latency_s":rf_crossing-interlock_crossing,
        "RF_Shutdown_Latency_us":(rf_crossing-interlock_crossing)*1e6,
        "Sample_Interval_CH1_s":float(np.median(np.diff(t1))),"Sample_Interval_CH2_s":float(np.median(np.diff(t2))),
    },envelope


def resolve_file(folder: Path, stored: Any) -> Path:
    text=str(stored or "").strip()
    if not text: raise ValueError("Waveform file path is blank.")
    path=Path(text)
    if path.is_file():return path
    local=folder/path.name
    if local.is_file():return local
    matches=list(folder.rglob(path.name))
    if len(matches)==1:return matches[0]
    raise FileNotFoundError(f"Could not resolve waveform {text!r} under {folder}.")


def validate_contract(folder: Path):
    path=folder/"Output_Contract.json"
    if not path.is_file():return {"warning":"Output_Contract.json not present; CSV validation used."}
    data=json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("contract_name")!=EXPECTED_CONTRACT:raise ValueError(f"Expected {EXPECTED_CONTRACT!r}; found {data.get('contract_name')!r}.")
    version=str(data.get("contract_version","0"))
    if int(version.split(".")[0])!=SUPPORTED_CONTRACT_MAJOR:raise ValueError(f"Unsupported contract version {version}.")
    return data


def save_capture_plot(path,t1,ch1,t2,envelope,result):
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True)
    axes[0].plot(t1*1e3,ch1);axes[0].axhline(result["Interlock_Threshold_V"],color="tab:orange",linestyle="--");axes[0].axvline(result["Interlock_Crossing_s"]*1e3,color="tab:red",linestyle=":");axes[0].set_ylabel("Interlock (V)");axes[0].grid(True,alpha=.3)
    axes[1].plot(t2*1e3,envelope);axes[1].axhline(result["RF_Envelope_Threshold_V"],color="tab:orange",linestyle="--");axes[1].axvline(result["RF_Shutdown_Crossing_s"]*1e3,color="tab:red",linestyle=":");axes[1].set_ylabel("RF envelope (V)");axes[1].set_xlabel("Time (ms)");axes[1].grid(True,alpha=.3)
    fig.suptitle(f"{result['Requested_Power_W']:g} W, Cycle {result['Cycle']}: Interlock Response");fig.tight_layout();fig.savefig(path,dpi=PLOT_DPI);plt.close(fig)


def write_csv(path,rows):
    if rows:pd.DataFrame(rows).to_csv(path,index=False,encoding="utf-8-sig")


def summarize(valid):
    return valid.groupby("Requested_Power_W",as_index=False).agg(
        Valid_Capture_Count=("Cycle","count"),Average_Latency_us=("RF_Shutdown_Latency_us","mean"),
        Minimum_Latency_us=("RF_Shutdown_Latency_us","min"),Maximum_Latency_us=("RF_Shutdown_Latency_us","max"),
        Standard_Deviation_us=("RF_Shutdown_Latency_us","std"),
    ).fillna(0.0)


def save_summary_plot(path,summary,label):
    fig,ax=plt.subplots(figsize=(10,6));ax.errorbar(summary["Requested_Power_W"],summary["Average_Latency_us"],yerr=summary["Standard_Deviation_us"],marker="o",capsize=4)
    ax.set(title=f"{label}: Interlock Response Timing",xlabel="Requested Power (W)",ylabel="RF Shutdown Latency (us)");ax.grid(True,alpha=.3);fig.tight_layout();fig.savefig(path,dpi=PLOT_DPI);plt.close(fig)


def analyze_dataset(folder:Path,output_parent:Path,label:str,progress):
    capture=folder/"Capture_Results.csv"
    if not capture.is_file():
        matches=list(folder.rglob("Capture_Results.csv"))
        if len(matches)!=1:raise FileNotFoundError("Select one Interlock Response Timing run folder containing Capture_Results.csv.")
        capture=matches[0]
    run_folder=capture.parent;contract=validate_contract(run_folder);frame=pd.read_csv(capture,encoding="utf-8-sig")
    missing=sorted(REQUIRED_CAPTURE_COLUMNS-set(frame.columns))
    if missing:raise ValueError("Capture_Results.csv is missing: "+", ".join(missing))
    verification_path=run_folder/"RF_Off_Verification.csv";verification=pd.read_csv(verification_path,encoding="utf-8-sig") if verification_path.is_file() else pd.DataFrame()
    output=unique_output(output_parent,label);results=[];eligible=frame.loc[frame["Triggered"].map(bool_value)]
    for index,(_,row) in enumerate(eligible.iterrows(),1):
        progress(f"{label}: {index}/{len(eligible)} {row['Requested_Power_W']} W cycle {row['Cycle']}")
        base={"Requested_Power_W":float(row["Requested_Power_W"]),"Cycle":int(float(row["Cycle"]))}
        try:
            path1=resolve_file(run_folder,row["CH1_File"]);path2=resolve_file(run_folder,row["CH2_File"]);t1,ch1=read_waveform_csv(path1);t2,ch2=read_waveform_csv(path2)
            measured,envelope=measure_capture(t1,ch1,t2,ch2);result={**base,**measured,"CH1_File":str(path1),"CH2_File":str(path2),"Analysis_Valid":True,"Failure_Reason":""}
            if not verification.empty:
                match=verification[(pd.to_numeric(verification.get("Requested_Power_W"),errors="coerce")==base["Requested_Power_W"]) & (pd.to_numeric(verification.get("Cycle"),errors="coerce")==base["Cycle"])]
                if not match.empty:result["RF_Off_Verified"]=bool_value(match.iloc[-1].get("RF_Off_Verified"))
            save_capture_plot(output/(safe_name(f"{base['Requested_Power_W']:g}W_Cycle{base['Cycle']}_Interlock_FALL")+".png"),t1,ch1,t2,envelope,result)
        except Exception as exc:result={**base,"CH1_File":row.get("CH1_File"),"CH2_File":row.get("CH2_File"),"Analysis_Valid":False,"Failure_Reason":str(exc)}
        results.append(result);write_csv(output/"Interlock_Response_Timing_Results.csv",results)
    valid=pd.DataFrame([row for row in results if row.get("Analysis_Valid")])
    if valid.empty:raise RuntimeError("No capture produced a valid interlock-response measurement.")
    summary=summarize(valid);summary.insert(0,"Dataset_Label",label);write_csv(output/"Averages_By_Power.csv",summary.to_dict("records"));save_summary_plot(output/"Average_Interlock_Response_Timing.png",summary,label)
    manifest={"analysis_id":ANALYSIS_ID,"analysis_name":ANALYSIS_NAME,"schema_version":"1.0","dataset_label":label,"input_folder":str(run_folder),"output_folder":str(output),
              "measurement_contract":{"interlock":"CH1 baseline-referenced 50% falling persistent crossing","rf_shutdown":"CH2 Hilbert-envelope baseline-referenced 10% falling persistent crossing","persistence_s":CROSSING_PERSISTENCE_S,"interpolation":"linear"},
              "valid_count":int(valid.shape[0]),"invalid_count":len(results)-int(valid.shape[0]),"source_contract":contract,"files":sorted(path.name for path in output.iterdir())}
    (output/"Analysis_Manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return {"label":label,"output":output,"summary":summary,"results":results}


def write_comparison(results,parent):
    if len(results)!=2:return None
    output=unique_output(parent,f"{results[0]['label']} vs {results[1]['label']}");pd.concat([r["summary"] for r in results],ignore_index=True).to_csv(output/"Dataset_Comparison.csv",index=False,encoding="utf-8-sig");return output


class AnalysisGUI:
    def __init__(self,root):
        import tkinter as tk
        from tkinter import ttk
        self.root=root;self.tk=tk;self.ttk=ttk;self.running=False;self.exit_code=2
        mode=os.environ.get("PARAMOUNT_ANALYSIS_MODE","single").lower();self.mode=tk.StringVar(value="comparison" if mode=="comparison" else "single")
        self.af=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_FOLDER",""));self.bf=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_FOLDER",""));self.al=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_LABEL","Dataset A") or "Dataset A");self.bl=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_LABEL","Dataset B") or "Dataset B");self.out=tk.StringVar(value=os.environ.get("PARAMOUNT_ANALYSIS_OUTPUT_FOLDER","") or self.af.get() or str(Path.home()));self.status=tk.StringVar(value="Ready");self._build()
    def _build(self):
        t=self.ttk;r=self.root;r.title("Interlock Response Timing Analysis");r.geometry("920x690");r.minsize(800,600);o=t.Frame(r,padding=14);o.pack(fill="both",expand=True)
        t.Label(o,text="Interlock Response Timing Analysis",font=("Segoe UI",16,"bold")).pack(anchor="w");t.Label(o,text="Measure delay from the falling interlock edge to RF-envelope shutdown.").pack(anchor="w",pady=(2,10))
        m=t.LabelFrame(o,text="Analysis Mode",padding=10);m.pack(fill="x");t.Radiobutton(m,text="Single dataset",variable=self.mode,value="single",command=self._mode).pack(side="left");t.Radiobutton(m,text="Compare two datasets",variable=self.mode,value="comparison",command=self._mode).pack(side="left",padx=18)
        d=t.LabelFrame(o,text="Datasets",padding=10);d.pack(fill="x",pady=10);d.columnconfigure(2,weight=1)
        t.Label(d,text="Dataset A").grid(row=0,column=0,sticky="w");t.Entry(d,textvariable=self.al,width=18).grid(row=0,column=1,padx=6);t.Entry(d,textvariable=self.af).grid(row=0,column=2,sticky="ew");t.Button(d,text="Browse...",command=lambda:self._browse(self.af)).grid(row=0,column=3,padx=6)
        t.Label(d,text="Dataset B").grid(row=1,column=0,sticky="w");self.ble=t.Entry(d,textvariable=self.bl,width=18);self.ble.grid(row=1,column=1,padx=6);self.bfe=t.Entry(d,textvariable=self.bf);self.bfe.grid(row=1,column=2,sticky="ew");self.bfb=t.Button(d,text="Browse...",command=lambda:self._browse(self.bf));self.bfb.grid(row=1,column=3,padx=6)
        t.Label(d,text="Results parent").grid(row=2,column=0,sticky="w");t.Entry(d,textvariable=self.out).grid(row=2,column=1,columnspan=2,sticky="ew",padx=6);t.Button(d,text="Browse...",command=lambda:self._browse(self.out)).grid(row=2,column=3,padx=6)
        self.start=t.Button(o,text="START INTERLOCK RESPONSE TIMING ANALYSIS",command=self._start);self.start.pack(fill="x",ipady=10,pady=10)
        p=t.LabelFrame(o,text="Progress",padding=10);p.pack(fill="both",expand=True);self.bar=t.Progressbar(p,mode="indeterminate");self.bar.pack(fill="x");self.log=self.tk.Text(p,height=13,state="disabled",wrap="word");self.log.pack(fill="both",expand=True,pady=(8,0))
        f=t.Frame(o);f.pack(fill="x",pady=(10,0));t.Label(f,textvariable=self.status).pack(side="left");self.close=t.Button(f,text="Close",command=self._close);self.close.pack(side="right");r.protocol("WM_DELETE_WINDOW",self._close);self._mode()
    def _mode(self):
        state="normal" if self.mode.get()=="comparison" else "disabled"
        for widget in (self.ble,self.bfe,self.bfb):widget.configure(state=state)
    def _browse(self,var):
        from tkinter import filedialog
        value=filedialog.askdirectory(parent=self.root,initialdir=var.get() or str(Path.home()))
        if value:var.set(value)
    def _say(self,msg):self.log.configure(state="normal");self.log.insert("end",msg+"\n");self.log.see("end");self.log.configure(state="disabled")
    def _start(self):
        from tkinter import messagebox
        datasets=[(self.af.get().strip(),self.al.get().strip() or "Dataset A")]
        if self.mode.get()=="comparison":datasets.append((self.bf.get().strip(),self.bl.get().strip() or "Dataset B"))
        out=Path(self.out.get().strip()).expanduser()
        if not out.is_dir() or any(not Path(folder).expanduser().is_dir() for folder,_ in datasets):messagebox.showerror("Invalid folder","Select existing dataset and results-parent folders.",parent=self.root);return
        self.running=True;self.start.configure(state="disabled");self.close.configure(state="disabled");self.bar.start(10);self.status.set("Analyzing")
        def worker():
            try:
                results=[analyze_dataset(Path(folder).expanduser(),out,label,lambda msg:self.root.after(0,lambda x=msg:self._say(x))) for folder,label in datasets];comparison=write_comparison(results,out);self.root.after(0,lambda:self._finish(0,results,comparison,None))
            except Exception as exc:traceback.print_exc();self.root.after(0,lambda error=exc:self._finish(1,[],None,error))
        threading.Thread(target=worker,daemon=True).start()
    def _finish(self,code,results,comparison,error):
        from tkinter import messagebox
        self.running=False;self.exit_code=code;self.bar.stop();self.start.configure(state="normal");self.close.configure(state="normal")
        if code==0:self.status.set("Completed successfully");folders="\n".join(str(result["output"]) for result in results)+((f"\n{comparison}") if comparison else "");messagebox.showinfo("Analysis complete",f"Results saved under:\n{folders}",parent=self.root)
        else:self.status.set("Analysis failed");self._say(f"ERROR: {error}");messagebox.showerror("Analysis failed",str(error),parent=self.root)
    def _close(self):
        from tkinter import messagebox
        if self.running:messagebox.showwarning("Analysis running","Wait for analysis to finish before closing.",parent=self.root);return
        self.root.destroy()


def main():
    import tkinter as tk
    root=tk.Tk();app=AnalysisGUI(root);root.mainloop();return app.exit_code

if __name__=="__main__":raise SystemExit(main())
