"""GUI analysis for Setpoint Transfer Function collection results.

Reads the versioned Raw_Samples.csv produced by
Setpoint_Transfer_Function_Collection.py. Computes sample-level, cycle-level,
and equally weighted across-cycle statistics, transfer-function regression,
error metrics, repeatability, CSV outputs, and plots. Supports standalone,
single-dataset, and two-dataset unified-analysis launch modes.
"""
from __future__ import annotations

import csv
import json
import math
import os
import threading
import traceback
from dataclasses import dataclass
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
import pandas as pd

ANALYSIS_NAME = "Setpoint Transfer Function"
ANALYSIS_ID = "setpoint_transfer_function"
EXPECTED_CONTRACT = "Paramount.SetpointTransferFunction"
SUPPORTED_CONTRACT_MAJOR = 1
RAW_FILENAME = "Raw_Samples.csv"
OUTPUT_FOLDER_NAME = "Setpoint Transfer Function Analysis"

REQUIRED_COLUMNS = {
    "Run_ID", "Cycle", "Point", "Sample", "Input_Voltage_V",
    "Expected_Power_W", "Measured_Delivered_Power_W", "Valid",
}


def safe_name(value: str, fallback: str = "Dataset") -> str:
    return path_component_strict(value, fallback)


def bool_value(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "valid"}


def unique_output(parent: Path, label: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return create_unique_folder(parent, f"{stamp} - {OUTPUT_FOLDER_NAME} - {safe_name(label)}")


def find_dataset_file(folder: Path) -> Path:
    direct = folder / RAW_FILENAME
    if direct.is_file():
        return direct
    matches = list(folder.rglob(RAW_FILENAME))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"No {RAW_FILENAME} was found under {folder}.")
    raise RuntimeError(f"Multiple {RAW_FILENAME} files were found under {folder}; select one run folder.")


def validate_contract(run_folder: Path) -> dict[str, Any]:
    path = run_folder / "Output_Contract.json"
    if not path.is_file():
        return {"warning": "Output_Contract.json was not present; column validation was used."}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    name = data.get("contract_name")
    version = str(data.get("contract_version", "0"))
    if name != EXPECTED_CONTRACT:
        raise ValueError(f"Expected contract {EXPECTED_CONTRACT!r}; found {name!r}.")
    try:
        major = int(version.split(".")[0])
    except ValueError as exc:
        raise ValueError(f"Invalid contract version {version!r}.") from exc
    if major != SUPPORTED_CONTRACT_MAJOR:
        raise ValueError(f"Unsupported contract version {version}; expected major version 1.")
    return data


@dataclass
class AnalysisResult:
    label: str
    input_folder: Path
    output_folder: Path
    cycle_points: pd.DataFrame
    summary: pd.DataFrame
    metrics: dict[str, Any]
    figures: list[Path]


def load_raw(folder: Path) -> tuple[pd.DataFrame, Path, dict[str, Any]]:
    raw_path = find_dataset_file(folder)
    run_folder = raw_path.parent
    contract = validate_contract(run_folder)
    frame = pd.read_csv(raw_path, encoding="utf-8-sig")
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"{raw_path.name} is missing required columns: {', '.join(missing)}")
    numeric = ["Cycle", "Point", "Sample", "Input_Voltage_V", "Expected_Power_W", "Measured_Delivered_Power_W"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["Valid"] = frame["Valid"].map(bool_value)
    frame = frame.loc[frame["Valid"]].copy()
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=numeric)
    if frame.empty:
        raise ValueError("No valid finite measurements remain after validation.")
    duplicate_key = ["Run_ID", "Cycle", "Point", "Sample"]
    if frame.duplicated(duplicate_key).any():
        raise ValueError("Duplicate Run_ID/Cycle/Point/Sample rows were found.")
    return frame, run_folder, contract


def analyze_dataset(folder: Path, output_parent: Path, label: str, progress) -> AnalysisResult:
    progress(f"Loading {label}...")
    raw, run_folder, contract = load_raw(folder)
    output = unique_output(output_parent, label)

    progress(f"Calculating cycle statistics for {label}...")
    grouped = raw.groupby(["Run_ID", "Cycle", "Point", "Input_Voltage_V", "Expected_Power_W"], as_index=False)
    cycle = grouped["Measured_Delivered_Power_W"].agg(
        Sample_Count="count", Cycle_Mean_W="mean", Cycle_Min_W="min",
        Cycle_Max_W="max", Cycle_StdDev_W="std",
    )
    cycle["Cycle_StdDev_W"] = cycle["Cycle_StdDev_W"].fillna(0.0)
    cycle["Cycle_Error_W"] = cycle["Cycle_Mean_W"] - cycle["Expected_Power_W"]
    cycle["Cycle_Error_Percent_Expected"] = np.where(
        cycle["Expected_Power_W"].abs() > 0,
        100.0 * cycle["Cycle_Error_W"] / cycle["Expected_Power_W"], np.nan,
    )

    progress(f"Calculating across-cycle statistics for {label}...")
    summary = cycle.groupby(["Point", "Input_Voltage_V", "Expected_Power_W"], as_index=False).agg(
        Cycle_Count=("Cycle", "nunique"),
        Average_Measured_Power_W=("Cycle_Mean_W", "mean"),
        Minimum_Cycle_Mean_W=("Cycle_Mean_W", "min"),
        Maximum_Cycle_Mean_W=("Cycle_Mean_W", "max"),
        Cycle_to_Cycle_StdDev_W=("Cycle_Mean_W", "std"),
        Total_Raw_Samples=("Sample_Count", "sum"),
    )
    summary["Cycle_to_Cycle_StdDev_W"] = summary["Cycle_to_Cycle_StdDev_W"].fillna(0.0)
    summary["Average_Error_W"] = summary["Average_Measured_Power_W"] - summary["Expected_Power_W"]
    summary["Average_Error_Percent_Expected"] = np.where(
        summary["Expected_Power_W"].abs() > 0,
        100.0 * summary["Average_Error_W"] / summary["Expected_Power_W"], np.nan,
    )

    x = summary["Input_Voltage_V"].to_numpy(float)
    y = summary["Average_Measured_Power_W"].to_numpy(float)
    if len(np.unique(x)) < 2:
        raise ValueError("At least two distinct input voltages are required for regression.")
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    residual = y - fitted
    ss_res = float(np.sum(residual ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    full_scale = float(summary["Expected_Power_W"].max() - summary["Expected_Power_W"].min())
    summary["Best_Fit_Power_W"] = fitted
    summary["Linearity_Error_W"] = residual
    summary["Linearity_Error_Percent_Full_Scale"] = np.where(
        full_scale > 0, 100.0 * residual / full_scale, np.nan,
    )
    metrics = {
        "Dataset_Label": label,
        "Input_Folder": str(run_folder),
        "Run_Count": int(raw["Run_ID"].nunique()),
        "Cycle_Count": int(cycle[["Run_ID", "Cycle"]].drop_duplicates().shape[0]),
        "Voltage_Point_Count": int(summary.shape[0]),
        "Valid_Raw_Sample_Count": int(raw.shape[0]),
        "Regression_Slope_W_per_V": float(slope),
        "Regression_Intercept_W": float(intercept),
        "Regression_R_squared": float(r_squared),
        "Maximum_Absolute_Point_Error_W": float(summary["Average_Error_W"].abs().max()),
        "Maximum_Absolute_Linearity_Error_W": float(summary["Linearity_Error_W"].abs().max()),
        "Maximum_Absolute_Linearity_Error_Percent_Full_Scale": float(summary["Linearity_Error_Percent_Full_Scale"].abs().max()),
        "Maximum_Cycle_to_Cycle_StdDev_W": float(summary["Cycle_to_Cycle_StdDev_W"].max()),
    }

    raw.to_csv(output / "Validated_Raw_Samples.csv", index=False, encoding="utf-8-sig")
    cycle.to_csv(output / "Cycle_Point_Statistics.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(output / "Across_Cycle_Summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([metrics]).to_csv(output / "Transfer_Function_Metrics.csv", index=False, encoding="utf-8-sig")

    progress(f"Generating plots for {label}...")
    figures: list[Path] = []
    fig, ax = plt.subplots(figsize=(10, 6))
    for cycle_id, part in cycle.groupby("Cycle"):
        ax.plot(part["Input_Voltage_V"], part["Cycle_Mean_W"], color="0.75", linewidth=1, alpha=.8)
    ax.errorbar(x, y, yerr=summary["Cycle_to_Cycle_StdDev_W"], fmt="o", capsize=3,
                label="Across-cycle mean ± 1σ")
    ax.plot(x, summary["Expected_Power_W"], linestyle="--", label="Expected transfer function")
    ax.plot(x, fitted, label=f"Best fit: {slope:.4g} W/V, intercept {intercept:.4g} W")
    ax.set(title=f"{label}: Setpoint Transfer Function", xlabel="Analog Setpoint Input (V)", ylabel="Delivered Power (W)")
    ax.grid(True, alpha=.3); ax.legend(); fig.tight_layout()
    path = output / "Transfer_Function.png"; fig.savefig(path, dpi=160); plt.close(fig); figures.append(path)

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.axhline(0, color="black", linewidth=1)
    ax.plot(x, summary["Average_Error_W"], marker="o", label="Point error vs expected")
    ax.plot(x, summary["Linearity_Error_W"], marker="s", label="Linearity error vs best fit")
    ax.set(title=f"{label}: Transfer-Function Error", xlabel="Analog Setpoint Input (V)", ylabel="Error (W)")
    ax.grid(True, alpha=.3); ax.legend(); fig.tight_layout()
    path = output / "Transfer_Function_Error.png"; fig.savefig(path, dpi=160); plt.close(fig); figures.append(path)

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(x, summary["Cycle_to_Cycle_StdDev_W"], marker="o")
    ax.set(title=f"{label}: Cycle-to-Cycle Repeatability", xlabel="Analog Setpoint Input (V)", ylabel="Cycle Mean Standard Deviation (W)")
    ax.grid(True, alpha=.3); fig.tight_layout()
    path = output / "Cycle_Repeatability.png"; fig.savefig(path, dpi=160); plt.close(fig); figures.append(path)

    manifest = {
        "analysis_id": ANALYSIS_ID, "analysis_name": ANALYSIS_NAME,
        "schema_version": "1.0", "dataset_label": label,
        "input_folder": str(run_folder), "output_folder": str(output),
        "completed": datetime.now().isoformat(timespec="seconds"),
        "metrics": metrics, "figures": [p.name for p in figures],
        "tables": ["Cycle_Point_Statistics.csv", "Across_Cycle_Summary.csv", "Transfer_Function_Metrics.csv"],
        "contract": contract,
    }
    (output / "Analysis_Manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    return AnalysisResult(label, run_folder, output, cycle, summary, metrics, figures)


def write_comparison(results: list[AnalysisResult], parent: Path) -> Optional[Path]:
    if len(results) != 2:
        return None
    output = unique_output(parent, f"{results[0].label} vs {results[1].label}")
    rows = []
    for result in results:
        temp = result.summary.copy(); temp.insert(0, "Dataset", result.label); rows.append(temp)
    combined = pd.concat(rows, ignore_index=True)
    combined.to_csv(output / "Dataset_Comparison.csv", index=False, encoding="utf-8-sig")
    fig, ax = plt.subplots(figsize=(10, 6))
    for result in results:
        ax.plot(result.summary["Input_Voltage_V"], result.summary["Average_Measured_Power_W"], marker="o", label=result.label)
    ax.set(title="Setpoint Transfer Function Comparison", xlabel="Analog Setpoint Input (V)", ylabel="Delivered Power (W)")
    ax.grid(True, alpha=.3); ax.legend(); fig.tight_layout(); fig.savefig(output / "Dataset_Comparison.png", dpi=160); plt.close(fig)
    return output


class AnalysisGUI:
    def __init__(self, root) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.root, self.tk, self.ttk = root, tk, ttk
        self.exit_code = 2; self.running = False
        mode = os.environ.get("PARAMOUNT_ANALYSIS_MODE", "single").strip().lower()
        self.mode_var = tk.StringVar(value="comparison" if mode == "comparison" else "single")
        self.a_folder = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_FOLDER", ""))
        self.b_folder = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_FOLDER", ""))
        self.a_label = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_LABEL", "Dataset A") or "Dataset A")
        self.b_label = tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_LABEL", "Dataset B") or "Dataset B")
        default_output = os.environ.get("PARAMOUNT_ANALYSIS_OUTPUT_FOLDER", "")
        self.output = tk.StringVar(value=default_output or self.a_folder.get() or str(Path.home()))
        self.status = tk.StringVar(value="Ready")
        self._build()

    def _build(self):
        ttk=self.ttk; root=self.root
        root.title("Setpoint Transfer Function Analysis"); root.geometry("900x690"); root.minsize(780,600)
        outer=ttk.Frame(root,padding=14); outer.pack(fill="both",expand=True)
        ttk.Label(outer,text="Setpoint Transfer Function Analysis",font=("Segoe UI",16,"bold")).pack(anchor="w")
        ttk.Label(outer,text="Analyze cycle averages, across-cycle repeatability, transfer-function fit, error, and linearity.").pack(anchor="w",pady=(2,12))
        mode=ttk.LabelFrame(outer,text="Analysis Mode",padding=10); mode.pack(fill="x")
        ttk.Radiobutton(mode,text="Single dataset",variable=self.mode_var,value="single",command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(mode,text="Compare two datasets",variable=self.mode_var,value="comparison",command=self._mode_changed).pack(side="left",padx=18)
        data=ttk.LabelFrame(outer,text="Datasets",padding=10); data.pack(fill="x",pady=10); data.columnconfigure(2,weight=1)
        ttk.Label(data,text="Dataset A label").grid(row=0,column=0,sticky="w",pady=4); ttk.Entry(data,textvariable=self.a_label,width=18).grid(row=0,column=1,padx=6); ttk.Entry(data,textvariable=self.a_folder).grid(row=0,column=2,sticky="ew")
        ttk.Button(data,text="Browse...",command=lambda:self._browse(self.a_folder)).grid(row=0,column=3,padx=6)
        ttk.Label(data,text="Dataset B label").grid(row=1,column=0,sticky="w",pady=4); self.b_label_entry=ttk.Entry(data,textvariable=self.b_label,width=18); self.b_label_entry.grid(row=1,column=1,padx=6); self.b_entry=ttk.Entry(data,textvariable=self.b_folder); self.b_entry.grid(row=1,column=2,sticky="ew"); self.b_browse=ttk.Button(data,text="Browse...",command=lambda:self._browse(self.b_folder)); self.b_browse.grid(row=1,column=3,padx=6)
        ttk.Label(data,text="Results parent folder").grid(row=2,column=0,sticky="w",pady=4); ttk.Entry(data,textvariable=self.output).grid(row=2,column=1,columnspan=2,sticky="ew",padx=6); ttk.Button(data,text="Browse...",command=lambda:self._browse(self.output)).grid(row=2,column=3,padx=6)
        self.start=ttk.Button(outer,text="START SETPOINT TRANSFER FUNCTION ANALYSIS",command=self._start); self.start.pack(fill="x",ipady=10,pady=8)
        box=ttk.LabelFrame(outer,text="Progress",padding=10); box.pack(fill="both",expand=True)
        self.bar=ttk.Progressbar(box,mode="indeterminate"); self.bar.pack(fill="x")
        self.log=self.tk.Text(box,height=14,state="disabled",wrap="word"); self.log.pack(fill="both",expand=True,pady=(8,0))
        foot=ttk.Frame(outer); foot.pack(fill="x",pady=(10,0)); ttk.Label(foot,textvariable=self.status).pack(side="left"); self.close=ttk.Button(foot,text="Close",command=self._close); self.close.pack(side="right")
        root.protocol("WM_DELETE_WINDOW",self._close); self._mode_changed()

    def _mode_changed(self):
        state="normal" if self.mode_var.get()=="comparison" else "disabled"
        self.b_label_entry.configure(state=state); self.b_entry.configure(state=state); self.b_browse.configure(state=state)

    def _browse(self,var):
        from tkinter import filedialog
        value=filedialog.askdirectory(parent=self.root,initialdir=var.get() or str(Path.home()))
        if value: var.set(value)

    def _say(self,text):
        self.log.configure(state="normal"); self.log.insert("end",text+"\n"); self.log.see("end"); self.log.configure(state="disabled")

    def _start(self):
        from tkinter import messagebox
        datasets=[(self.a_folder.get().strip(),self.a_label.get().strip() or "Dataset A")]
        if self.mode_var.get()=="comparison": datasets.append((self.b_folder.get().strip(),self.b_label.get().strip() or "Dataset B"))
        output=Path(self.output.get().strip()).expanduser()
        if any(not Path(f).expanduser().is_dir() for f,_ in datasets) or not output.is_dir():
            messagebox.showerror("Invalid folder","Select existing dataset and results-parent folders.",parent=self.root); return
        self.running=True; self.start.configure(state="disabled"); self.close.configure(state="disabled"); self.bar.start(10); self.status.set("Analyzing")
        def worker():
            try:
                results=[]
                for folder,label in datasets:
                    results.append(analyze_dataset(Path(folder).expanduser(),output,label,lambda m:self.root.after(0,lambda x=m:self._say(x))))
                comparison=write_comparison(results,output)
                self.root.after(0,lambda:self._finish(0,results,comparison,None))
            except Exception as exc:
                traceback.print_exc(); self.root.after(0,lambda:self._finish(1,[],None,exc))
        threading.Thread(target=worker,daemon=True).start()

    def _finish(self,code,results,comparison,error):
        from tkinter import messagebox
        self.running=False; self.exit_code=code; self.bar.stop(); self.start.configure(state="normal"); self.close.configure(state="normal")
        if code==0:
            folders="\n".join(str(r.output_folder) for r in results)
            if comparison: folders += f"\n{comparison}"
            self.status.set("Completed successfully"); self._say("Analysis completed successfully."); messagebox.showinfo("Analysis complete",f"Results saved under:\n{folders}",parent=self.root)
        else:
            self.status.set("Analysis failed"); self._say(f"ERROR: {error}"); messagebox.showerror("Analysis failed",str(error),parent=self.root)

    def _close(self):
        from tkinter import messagebox
        if self.running: messagebox.showwarning("Analysis running","Wait for analysis to finish before closing.",parent=self.root); return
        self.root.destroy()


def main() -> int:
    import tkinter as tk
    root=tk.Tk(); app=AnalysisGUI(root); root.mainloop(); return app.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
