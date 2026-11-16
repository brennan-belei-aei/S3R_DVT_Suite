#!/usr/bin/env python3
"""Unified collection wrapper for AEBus command-latency workflows.

The wrapper intentionally preserves the coworker-provided measurement engines.
It creates temporary runtime copies that change only configured constants and
output paths, then executes those copies as independent Python processes.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.bench import bench_default
from suite_sdk.registry import load_registry

APP_DIR = Path(__file__).resolve().parent
TEST_NAME = "AEBus Command Latency"
# Both collectors belong to the rf_on_off_timing plugin (Digital Timing); look them up by test key.
COLLECTION_SCRIPT_KEYS = {
    "command_processing": "rf_onoff_command_processing",
    "rf_scope": "rf_onoff_scope_capture",
}


def find_script(mode):
    return load_registry().test("collection", COLLECTION_SCRIPT_KEYS[mode]).script


def safe_component(value):
    return re.sub(r'[<>:"/\\|?*]+', "_", str(value)).strip().rstrip(".") or "Unknown"


def unique_run_folder(parent, generator):
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = f"{stamp} - {TEST_NAME} - {safe_component(generator)}"
    candidate = parent / base
    number = 2
    while candidate.exists():
        candidate = parent / f"{base} - Run {number}"; number += 1
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


def replace_one(source, pattern, replacement, label):
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.MULTILINE)
    if count != 1: raise RuntimeError(f"Could not configure {label}; expected one source match, found {count}.")
    return updated


def runtime_copy(original, output_folder, replacements):
    source = original.read_text(encoding="utf-8")
    for pattern, replacement, label in replacements:
        source = replace_one(source, pattern, replacement, label)
    runtime = original.parent / f".__aebus_runtime_{original.stem}_{os.getpid()}_{int(time.time()*1000)}.py"
    runtime.write_text(source, encoding="utf-8")
    return runtime


def write_if_tools_readme(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.txt").write_text(
        "IF Tools capture was selected. Use the coworker-provided IF Tools workflow, export the raw CSV, "
        "and place it in this folder. The analysis expects columns named Input, Data, and dt last(us).\n",
        encoding="utf-8",
    )


class App:
    def __init__(self, root):
        self.root=root; self.root.title("AEBus Command Latency Collection"); self.root.geometry("1020x770"); self.root.minsize(900,680)
        self.stop_requested=False; self.process=None; self.result={}
        scope_address=bench_default("oscilloscope")
        scope_address=scope_address.split("::")[1] if "::" in scope_address else scope_address
        self.vars={
            "generator":tk.StringVar(value=bench_default("generator")),
            "scope":tk.StringVar(value=scope_address),
            "parent":tk.StringVar(value=os.environ.get("PARAMOUNT_COLLECTION_PARENT_FOLDER",str(Path.home()))),
            "comm":tk.StringVar(value="serial"), "com":tk.StringVar(value=bench_default("generator_com_port")), "unit":tk.StringVar(value="refreshed 6013"),
            "command_iterations":tk.StringVar(value="25"), "rf_iterations":tk.StringVar(value="10"),
            "setpoints":tk.StringVar(value="1000 2000 3000 4000 5000 6000"), "measurement":tk.StringVar(value="1"),
            "if_file":tk.StringVar(),
        }
        self.command_var=tk.BooleanVar(value=True); self.rf_var=tk.BooleanVar(value=True); self.if_var=tk.BooleanVar(value=False)
        self.status=tk.StringVar(value="Ready"); self.detail=tk.StringVar(value="")
        self.build()

    def build(self):
        style=ttk.Style(self.root)
        try: style.theme_use("vista")
        except tk.TclError: pass
        style.configure("Header.TLabel",font=("Segoe UI",15,"bold")); style.configure("Start.TButton",font=("Segoe UI",11,"bold"),padding=(14,8))
        outer=ttk.Frame(self.root,padding=14);outer.pack(fill="both",expand=True)
        ttk.Label(outer,text=TEST_NAME,style="Header.TLabel").pack(anchor="w")
        ttk.Label(outer,text="Collect communication latency, RF ON/OFF physical-response latency, and/or import an IF Tools trace. Existing measurement algorithms are executed without correction.",wraplength=950).pack(anchor="w",pady=(2,10))
        nb=ttk.Notebook(outer);nb.pack(fill="both",expand=True)
        modes=ttk.Frame(nb,padding=12);inst=ttk.Frame(nb,padding=12);cmd=ttk.Frame(nb,padding=12);rf=ttk.Frame(nb,padding=12);ift=ttk.Frame(nb,padding=12);out=ttk.Frame(nb,padding=12)
        for frame,title in [(modes,"Collection Modes"),(inst,"Instruments"),(cmd,"Command Processing"),(rf,"RF ON/OFF Scope"),(ift,"IF Tools"),(out,"Output")]:nb.add(frame,text=title)
        ttk.Checkbutton(modes,text="AEBus transaction latency (CommandProcessing.py)",variable=self.command_var).pack(anchor="w",pady=7)
        ttk.Checkbutton(modes,text="RF ON/OFF physical-response latency (rf_onoff_scope_capture_GUI.py)",variable=self.rf_var).pack(anchor="w",pady=7)
        ttk.Checkbutton(modes,text="IF Tools trace workflow/import",variable=self.if_var).pack(anchor="w",pady=7)
        self.entry(inst,0,"Generator hostname / IP","generator","Used for Ethernet; defaults from unified GUI.")
        self.entry(inst,1,"Oscilloscope hostname / IP","scope","Defaults from unified GUI.")
        self.entry(inst,2,"Communication type","comm","serial or ethernet")
        self.entry(inst,3,"Serial COM port","com","")
        self.entry(inst,4,"RF scope unit/model selection","unit","Must match the original RF script choices.")
        self.entry(cmd,0,"Iterations per report command","command_iterations","")
        ttk.Label(cmd,text="The original set-command sequence, report-command list, latency_ms endpoints, retries, and CSV columns are preserved.",wraplength=800,foreground="#555555").grid(row=1,column=0,columnspan=3,sticky="w",pady=(14,0))
        self.entry(rf,0,"Iterations","rf_iterations","")
        self.entry(rf,1,"Setpoints [tenths of watts]","setpoints","Space-separated values passed through the original CLI.")
        self.entry(rf,2,"Measurement method","measurement","1 or 2, using the original cursor algorithms.")
        ttk.Label(rf,text="The original RF ON/OFF scope acquisition, cursor calculations, screenshots, troubleshooting CSV, and result CSV remain in the coworker script.",wraplength=800,foreground="#555555").grid(row=3,column=0,columnspan=3,sticky="w",pady=(14,0))
        ttk.Label(ift,text="Optional existing IF Tools CSV export").grid(row=0,column=0,sticky="w",pady=6)
        ttk.Entry(ift,textvariable=self.vars["if_file"]).grid(row=0,column=1,sticky="ew",padx=8);ift.columnconfigure(1,weight=1)
        ttk.Button(ift,text="Browse...",command=self.browse_if).grid(row=0,column=2)
        ttk.Label(ift,text="If no file is selected, the run folder receives instructions and you can copy the export there later. No IF Tools event logic or units are changed by collection.",wraplength=820,foreground="#555555").grid(row=1,column=0,columnspan=3,sticky="w",pady=(12,0))
        ttk.Label(out,text="Parent output folder").grid(row=0,column=0,sticky="w",pady=6)
        ttk.Entry(out,textvariable=self.vars["parent"]).grid(row=0,column=1,sticky="ew",padx=8);out.columnconfigure(1,weight=1)
        ttk.Button(out,text="Browse...",command=self.browse_parent).grid(row=0,column=2)
        ttk.Label(out,text="The timestamped campaign folder is created only after Start Collection validates all settings.",foreground="#555555").grid(row=1,column=0,columnspan=3,sticky="w",pady=(12,0))
        progress=ttk.LabelFrame(outer,text="Progress",padding=10);progress.pack(fill="x",pady=(12,0))
        ttk.Label(progress,textvariable=self.status,font=("Segoe UI",10,"bold")).pack(anchor="w");ttk.Label(progress,textvariable=self.detail,wraplength=920).pack(anchor="w",pady=(4,8))
        self.bar=ttk.Progressbar(progress,mode="determinate",maximum=3);self.bar.pack(fill="x")
        buttons=ttk.Frame(outer);buttons.pack(fill="x",pady=(12,0))
        self.start_btn=ttk.Button(buttons,text="Start Collection",style="Start.TButton",command=self.start);self.start_btn.pack(side="right")
        self.stop_btn=ttk.Button(buttons,text="Stop After Current Mode",command=self.stop,state="disabled");self.stop_btn.pack(side="right",padx=8)
        ttk.Button(buttons,text="Close",command=self.root.destroy).pack(side="left")

    def entry(self,parent,row,label,key,help_text):
        parent.columnconfigure(1,weight=1);ttk.Label(parent,text=label).grid(row=row,column=0,sticky="w",pady=6);ttk.Entry(parent,textvariable=self.vars[key]).grid(row=row,column=1,sticky="ew",padx=8,pady=6)
        if help_text:ttk.Label(parent,text=help_text,foreground="#555555").grid(row=row,column=2,sticky="w")
    def browse_parent(self):
        value=filedialog.askdirectory(parent=self.root,mustexist=True,initialdir=self.vars["parent"].get() or str(Path.home()))
        if value:self.vars["parent"].set(value)
    def browse_if(self):
        value=filedialog.askopenfilename(parent=self.root,filetypes=[("CSV files","*.csv"),("All files","*.*")])
        if value:self.vars["if_file"].set(value)
    def stop(self):self.stop_requested=True;self.status.set("Stop requested");self.detail.set("The current subprocess will finish; remaining modes will not start.")

    def validate(self):
        modes=[]
        if self.command_var.get():modes.append("command_processing")
        if self.rf_var.get():modes.append("rf_scope")
        if self.if_var.get():modes.append("if_tools")
        if not modes:raise ValueError("Select at least one collection mode.")
        parent=Path(self.vars["parent"].get()).expanduser().resolve()
        if not parent.is_dir():raise ValueError("Select an existing parent output folder.")
        comm=self.vars["comm"].get().strip().lower()
        if comm not in {"serial","ethernet"}:raise ValueError("Communication type must be serial or ethernet.")
        ci=int(self.vars["command_iterations"].get());ri=int(self.vars["rf_iterations"].get());measurement=int(self.vars["measurement"].get())
        if ci<1 or ri<1 or measurement not in {1,2}:raise ValueError("Iterations must be positive and measurement must be 1 or 2.")
        setpoints=[int(x) for x in self.vars["setpoints"].get().split()]
        if not setpoints:raise ValueError("Enter at least one RF scope setpoint.")
        for key in modes:
            if key!="if_tools" and not find_script(key).is_file():raise ValueError(f"Missing required script: {find_script(key)}")
        return modes,parent,comm,ci,ri,measurement,setpoints

    def start(self):
        try:self.config=self.validate()
        except Exception as error:messagebox.showerror("Invalid settings",str(error),parent=self.root);return
        self.start_btn.configure(state="disabled");self.stop_btn.configure(state="normal");self.stop_requested=False
        threading.Thread(target=self.worker,daemon=True).start()

    def run_subprocess(self,args,cwd,env):
        self.process=subprocess.Popen(args,cwd=str(cwd),env=env)
        code=self.process.wait();self.process=None
        if code!=0:raise RuntimeError(f"Child process exited with code {code}: {args[1]}")

    def worker(self):
        modes,parent,comm,ci,ri,measurement,setpoints=self.config
        run=None;runtime_files=[]
        try:
            run=unique_run_folder(parent,self.vars["generator"].get().strip() or "Generator")
            settings={k:v.get() for k,v in self.vars.items()};settings.update({"modes":modes,"created":datetime.now().isoformat()})
            (run/"AEBus_Latency_Run_Settings.json").write_text(json.dumps(settings,indent=2),encoding="utf-8")
            env=os.environ.copy();env["AEBUS_LATENCY_RUN_FOLDER"]=str(run)
            completed=0
            if "command_processing" in modes and not self.stop_requested:
                self.status.set("Running AEBus transaction-latency collection");folder=run/"command_processing";folder.mkdir()
                original=find_script("command_processing")
                csv_path=(folder/"Command_Processing_Latency.csv").resolve()
                replacements=[
                    (r'^COMM_TYPE\s*=.*$',f'COMM_TYPE = {comm!r}',"communication type"),
                    (r'^IP_ADDRESS\s*=.*$',f'IP_ADDRESS = {self.vars["generator"].get().strip()!r}',"generator address"),
                    (r'^COM_PORT\s*=.*$',f'COM_PORT = {self.vars["com"].get().strip()!r}',"COM port"),
                    (r'^ITERATION\s*=.*$',f'ITERATION = {ci}',"iterations"),
                    (r'^CSV_PATH\s*=.*$',f'CSV_PATH = {str(csv_path)!r}',"CSV path"),
                ]
                runtime=runtime_copy(original,folder,replacements);runtime_files.append(runtime)
                self.run_subprocess([sys.executable,str(runtime)],folder,env);completed+=1;self.bar["value"]=completed
            if "rf_scope" in modes and not self.stop_requested:
                self.status.set("Running RF ON/OFF scope collection");folder=run/"rf_onoff_scope";folder.mkdir()
                original=find_script("rf_scope")
                replacements=[
                    (r'^COMM_TYPE\s*=.*$',f'COMM_TYPE = {comm!r}',"communication type"),
                    (r'^IP_ADDRESS\s*=.*$',f'IP_ADDRESS = {self.vars["generator"].get().strip()!r}',"generator address"),
                    (r'^COM_PORT\s*=.*$',f'COM_PORT = {self.vars["com"].get().strip()!r}',"COM port"),
                    (r'^REPORT_ROOT\s*=.*$',f'REPORT_ROOT = {str(folder.resolve())!r}',"report root"),
                ]
                runtime=runtime_copy(original,folder,replacements);runtime_files.append(runtime)
                # --no-gui: this GUI already collected the settings; --output-parent keeps data in this run folder.
                args=[sys.executable,str(runtime),"--no-gui","--output-parent",str(folder.resolve()),"--dut-name",self.vars["generator"].get().strip() or "Generator","--unit",self.vars["unit"].get().strip(),"--scope-ip",self.vars["scope"].get().strip(),"--com-port",self.vars["com"].get().strip(),"--iterations",str(ri),"--measurement",str(measurement),"--setpoints",*[str(x) for x in setpoints]]
                self.run_subprocess(args,folder,env);completed+=1;self.bar["value"]=completed
            if "if_tools" in modes and not self.stop_requested:
                self.status.set("Preparing IF Tools trace folder");folder=run/"if_tools";write_if_tools_readme(folder)
                selected=self.vars["if_file"].get().strip()
                if selected:
                    source=Path(selected)
                    if not source.is_file():raise ValueError(f"Selected IF Tools CSV does not exist: {source}")
                    shutil.copy2(source,folder/source.name)
                    (folder/"source_path.txt").write_text(str(source.resolve()),encoding="utf-8")
                completed+=1;self.bar["value"]=completed
            (run/"collection_manifest.json").write_text(json.dumps({"status":"stopped" if self.stop_requested else "complete","selected_modes":modes,"completed_modes":completed,"run_folder":str(run)},indent=2),encoding="utf-8")
            self.status.set("Collection stopped" if self.stop_requested else "Collection complete");self.detail.set(str(run))
            self.root.after(0,lambda:messagebox.showinfo("AEBus collection",f"Collection {'stopped' if self.stop_requested else 'complete'}.\n\nRun folder:\n{run}",parent=self.root))
        except Exception as error:
            if run:(run/"collection_error.txt").write_text(traceback.format_exc(),encoding="utf-8")
            self.status.set("Collection failed");self.detail.set(str(error));self.root.after(0,lambda e=str(error):messagebox.showerror("Collection failed",e,parent=self.root))
        finally:
            for path in runtime_files:
                try:path.unlink()
                except OSError:pass
            self.root.after(0,lambda:(self.start_btn.configure(state="normal"),self.stop_btn.configure(state="disabled")))


def main():
    root=tk.Tk();App(root);root.mainloop()
if __name__=="__main__":main()
