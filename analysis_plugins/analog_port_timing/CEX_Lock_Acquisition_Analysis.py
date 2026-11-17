"""CEX Lock Acquisition analysis GUI.

T1 is the CH1 rolling-RMS activity rising 90% crossing. T2 is the CH3
active-low CEX LOCK first 10% of the falling transition toward 0 V. Acquisition
time is T2 minus T1. CH2 independently verifies RF waveform activity.
"""
from __future__ import annotations
import csv,json,math,os,threading,traceback
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.output_folders import create_unique_folder, path_component_strict  # noqa: E402
from typing import Any
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import hilbert
ANALYSIS_ID="cex_lock_acquisition";ANALYSIS_NAME="CEX Lock Acquisition";EXPECTED_CONTRACT="Paramount.CEXLockAcquisition"
RMS_WINDOW_S=2.0e-6;PERSISTENCE_S=2.0e-6
PRE_WINDOW=(-400e-3,-50e-3);POST_WINDOW=(50e-3,400e-3)
T1_FRACTION=.90;T2_FRACTION=.10;RF_ACTIVITY_MIN_RATIO=2.0;PLOT_DPI=150
REQUIRED={"Requested_Power_W","Acquisition","Triggered","CH1_File","CH2_File","CH3_File"}
def b(v):return str(v).strip().lower() in {"1","true","yes","y"}
def safe(v,f="Dataset"):
    return path_component_strict(v, f)
def unique(parent,label):
 stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
 return create_unique_folder(parent, f"{stamp} - {ANALYSIS_NAME} Analysis - {safe(label)}")
def read(path):
 try:
  d=np.genfromtxt(path,delimiter=",",names=True,dtype=float,encoding="utf-8-sig",invalid_raise=False)
  if d.dtype.names and len(d.dtype.names)>=2:t=np.asarray(d[d.dtype.names[0]],float);y=np.asarray(d[d.dtype.names[1]],float)
  else:raise ValueError
 except Exception:
  rows=[]
  for row in csv.reader(Path(path).open("r",encoding="utf-8-sig",errors="replace")):
   nums=[]
   for x in row:
    try:nums.append(float(x))
    except:pass
   if len(nums)>=2:rows.append(nums[-2:])
  t,y=np.asarray(rows,float).T
 good=np.isfinite(t)&np.isfinite(y);t,y=t[good],y[good];order=np.argsort(t);t,y=t[order],y[order];keep=np.r_[True,np.diff(t)>0];t,y=t[keep],y[keep]
 if len(t)<20:raise ValueError(f"Insufficient waveform samples in {Path(path).name}")
 return t,y
def med(t,y,c,w):
 m=(t>=c+w[0])&(t<=c+w[1])
 if not np.any(m):raise ValueError(f"No samples in window {w}")
 return float(np.median(y[m]))
def cross(t,y,thr,rising,start=None):
 dt=float(np.median(np.diff(t)));n=max(1,int(math.ceil(PERSISTENCE_S/dt)));i0=1 if start is None else max(1,int(np.searchsorted(t,start)))
 for i in range(i0,len(y)):
  hit=y[i-1]<thr<=y[i] if rising else y[i-1]>thr>=y[i]
  if hit and i+n<=len(y) and (np.all(y[i:i+n]>=thr) if rising else np.all(y[i:i+n]<=thr)):
   return float(t[i-1]+(thr-y[i-1])/(y[i]-y[i-1])*(t[i]-t[i-1]))
 raise ValueError("Persistent threshold crossing not found")
def rolling_rms(y,n):
 n=max(1,n);kernel=np.ones(n)/n;return np.sqrt(np.convolve(y*y,kernel,mode="same"))
def measure(t1,ch1,t2,ch2,t3,ch3):
 dt=float(np.median(np.diff(t1)));rms=rolling_rms(ch1-med(t1,ch1,0,PRE_WINDOW),int(round(RMS_WINDOW_S/dt)))
 r0=med(t1,rms,0,PRE_WINDOW);r1=med(t1,rms,0,POST_WINDOW);thr1=r0+T1_FRACTION*(r1-r0);T1=cross(t1,rms,thr1,True)
 l0=med(t3,ch3,T1,PRE_WINDOW);l1=med(t3,ch3,T1,POST_WINDOW);thr2=l0+T2_FRACTION*(l1-l0);T2=cross(t3,ch3,thr2,False,T1)
 env=np.abs(hilbert(ch2-med(t2,ch2,T1,PRE_WINDOW)));rf0=med(t2,env,T1,PRE_WINDOW);rf1=med(t2,env,T1,POST_WINDOW);ratio=rf1/max(rf0,1e-15)
 return {"T1_s":T1,"T2_s":T2,"Acquisition_Time_s":T2-T1,"Acquisition_Time_us":(T2-T1)*1e6,"CH1_RMS_Initial_V":r0,"CH1_RMS_Final_V":r1,"CH1_RMS_90_Threshold_V":thr1,"CH3_Initial_V":l0,"CH3_Final_V":l1,"CH3_10_Percent_Drop_Threshold_V":thr2,"RF_Envelope_Pre_V":rf0,"RF_Envelope_Post_V":rf1,"RF_Activity_Ratio":ratio,"RF_Active_Verified":ratio>=RF_ACTIVITY_MIN_RATIO},rms,env
def resolve(folder,value):
 p=Path(str(value));
 if p.is_file():return p
 p2=folder/p.name
 if p2.is_file():return p2
 x=list(folder.rglob(p.name))
 if len(x)==1:return x[0]
 raise FileNotFoundError(value)
def plot(path,t1,ch1,rms,t2,env,t3,ch3,r):
 fig,ax=plt.subplots(3,1,figsize=(11,9),sharex=True);ax[0].plot(t1,rms);ax[0].axhline(r["CH1_RMS_90_Threshold_V"]);ax[0].axvline(r["T1_s"]);ax[0].set_ylabel("CH1 RMS (V)");ax[1].plot(t2,env);ax[1].set_ylabel("RF envelope (V)");ax[2].plot(t3,ch3);ax[2].axhline(r["CH3_10_Percent_Drop_Threshold_V"]);ax[2].axvline(r["T2_s"]);ax[2].set_ylabel("CEX LOCK (V)");ax[2].set_xlabel("Time (s)");[a.grid(True,alpha=.3) for a in ax];fig.tight_layout();fig.savefig(path,dpi=PLOT_DPI);plt.close(fig)
def analyze(folder,parent,label,progress):
 cap=folder/"Capture_Results.csv"
 if not cap.is_file():
  x=list(folder.rglob("Capture_Results.csv"));cap=x[0] if len(x)==1 else (_ for _ in()).throw(FileNotFoundError("Select one CEX Lock Acquisition run folder"))
 run=cap.parent;contract=json.loads((run/"Output_Contract.json").read_text()) if (run/"Output_Contract.json").is_file() else {}
 if contract and contract.get("contract_name")!=EXPECTED_CONTRACT:raise ValueError("Wrong collection contract")
 frame=pd.read_csv(cap,encoding="utf-8-sig");missing=REQUIRED-set(frame.columns)
 if missing:raise ValueError("Missing columns: "+", ".join(sorted(missing)))
 states=pd.read_csv(run/"Lock_State_Verification.csv",encoding="utf-8-sig") if (run/"Lock_State_Verification.csv").is_file() else pd.DataFrame();out=unique(parent,label);rows=[]
 eligible=frame[frame.Triggered.map(b)]
 for k,(_,row) in enumerate(eligible.iterrows(),1):
  progress(f"{label}: {k}/{len(eligible)} {row.Requested_Power_W:g} W acquisition {row.Acquisition}")
  base={"Requested_Power_W":float(row.Requested_Power_W),"Acquisition":int(row.Acquisition)}
  try:
   p=[resolve(run,row[f"CH{i}_File"]) for i in (1,2,3)];w=[read(x) for x in p];r,rms,env=measure(*w[0],*w[1],*w[2]);result={**base,**r,"CH1_File":str(p[0]),"CH2_File":str(p[1]),"CH3_File":str(p[2]),"Analysis_Valid":True,"Failure_Reason":""}
   if not states.empty:
    m=states[(pd.to_numeric(states.Requested_Power_W,errors="coerce")==base["Requested_Power_W"])&(pd.to_numeric(states.Acquisition,errors="coerce")==base["Acquisition"])]
    if not m.empty:result.update(Unlocked_Before=b(m.iloc[-1].Unlocked_Before),Locked_After=b(m.iloc[-1].Locked_After))
   plot(out/(safe(f"{base['Requested_Power_W']:g}W_Acquisition{base['Acquisition']}")+".png"),*w[0],rms,*w[1],env,*w[2],result)
  except Exception as e:result={**base,"Analysis_Valid":False,"Failure_Reason":str(e)}
  rows.append(result);pd.DataFrame(rows).to_csv(out/"CEX_Lock_Acquisition_Results.csv",index=False,encoding="utf-8-sig")
 valid=pd.DataFrame([x for x in rows if x.get("Analysis_Valid")])
 if valid.empty:raise RuntimeError("No valid measurements")
 summary=valid.groupby("Requested_Power_W",as_index=False).agg(Valid_Count=("Acquisition","count"),Average_Acquisition_Time_us=("Acquisition_Time_us","mean"),Minimum_Acquisition_Time_us=("Acquisition_Time_us","min"),Maximum_Acquisition_Time_us=("Acquisition_Time_us","max"),Standard_Deviation_us=("Acquisition_Time_us","std"),RF_Active_Verified_Count=("RF_Active_Verified","sum")).fillna(0);summary.insert(0,"Dataset_Label",label);summary.to_csv(out/"Averages_By_Power.csv",index=False,encoding="utf-8-sig")
 fig,ax=plt.subplots(figsize=(10,6));ax.errorbar(summary.Requested_Power_W,summary.Average_Acquisition_Time_us,yerr=summary.Standard_Deviation_us,marker="o",capsize=4);ax.set(xlabel="Requested Power (W)",ylabel="CEX Lock Acquisition Time (us)",title=f"{label}: CEX Lock Acquisition");ax.grid(True,alpha=.3);fig.tight_layout();fig.savefig(out/"Average_CEX_Lock_Acquisition.png",dpi=PLOT_DPI);plt.close(fig)
 manifest={"analysis_id":ANALYSIS_ID,"analysis_name":ANALYSIS_NAME,"schema_version":"1.0","dataset_label":label,"input_folder":str(run),"output_folder":str(out),"measurement_contract":{"T1":"CH1 rolling-RMS rising 90% crossing","T2":"CH3 active-low falling first 10% of drop","acquisition_time":"T2-T1","crossing_persistence_s":PERSISTENCE_S,"interpolation":"linear"},"valid_count":len(valid),"invalid_count":len(rows)-len(valid),"files":sorted(p.name for p in out.iterdir())};(out/"Analysis_Manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
 return {"label":label,"output":out,"summary":summary}
class GUI:
 def __init__(self,root):
  import tkinter as tk
  from tkinter import ttk
  self.root=root;self.tk=tk;self.ttk=ttk;self.running=False;self.exit_code=2;mode=os.environ.get("PARAMOUNT_ANALYSIS_MODE","single").lower();self.mode=tk.StringVar(value="comparison" if mode=="comparison" else "single");self.af=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_FOLDER",""));self.bf=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_FOLDER",""));self.al=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_LABEL","Dataset A") or "Dataset A");self.bl=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_LABEL","Dataset B") or "Dataset B");self.out=tk.StringVar(value=os.environ.get("PARAMOUNT_ANALYSIS_OUTPUT_FOLDER","") or self.af.get() or str(Path.home()));self.status=tk.StringVar(value="Ready");self.build()
 def build(self):
  t=self.ttk;r=self.root;r.title("CEX Lock Acquisition Analysis");r.geometry("900x680");o=t.Frame(r,padding=14);o.pack(fill="both",expand=True);t.Label(o,text="CEX Lock Acquisition Analysis",font=("Segoe UI",16,"bold")).pack(anchor="w");m=t.Frame(o);m.pack(fill="x",pady=8);t.Radiobutton(m,text="Single dataset",variable=self.mode,value="single",command=self.toggle).pack(side="left");t.Radiobutton(m,text="Compare two datasets",variable=self.mode,value="comparison",command=self.toggle).pack(side="left",padx=15);d=t.LabelFrame(o,text="Datasets",padding=10);d.pack(fill="x");d.columnconfigure(2,weight=1)
  for row,(name,label,folder) in enumerate((("Dataset A",self.al,self.af),("Dataset B",self.bl,self.bf))):t.Label(d,text=name).grid(row=row,column=0);e=t.Entry(d,textvariable=label,width=18);e.grid(row=row,column=1,padx=5);f=t.Entry(d,textvariable=folder);f.grid(row=row,column=2,sticky="ew");bt=t.Button(d,text="Browse",command=lambda v=folder:self.browse(v));bt.grid(row=row,column=3);(setattr(self,"be",e),setattr(self,"bfentry",f),setattr(self,"bb",bt)) if row==1 else None
  t.Label(d,text="Results parent").grid(row=2,column=0);t.Entry(d,textvariable=self.out).grid(row=2,column=1,columnspan=2,sticky="ew");t.Button(d,text="Browse",command=lambda:self.browse(self.out)).grid(row=2,column=3);self.start=t.Button(o,text="START CEX LOCK ACQUISITION ANALYSIS",command=self.start_run);self.start.pack(fill="x",ipady=10,pady=10);p=t.LabelFrame(o,text="Progress",padding=10);p.pack(fill="both",expand=True);self.bar=t.Progressbar(p,mode="indeterminate");self.bar.pack(fill="x");self.log=self.tk.Text(p,state="disabled");self.log.pack(fill="both",expand=True);self.close=t.Button(o,text="Close",command=self.close);self.close.pack(anchor="e",pady=8);self.toggle()
 def toggle(self):
  state="normal" if self.mode.get()=="comparison" else "disabled"
  for x in (self.be,self.bfentry,self.bb):x.configure(state=state)
 def browse(self,var):
  from tkinter import filedialog
  x=filedialog.askdirectory(parent=self.root,initialdir=var.get() or str(Path.home()));var.set(x or var.get())
 def say(self,x):self.log.configure(state="normal");self.log.insert("end",x+"\n");self.log.configure(state="disabled")
 def start_run(self):
  from tkinter import messagebox
  data=[(self.af.get(),self.al.get() or "Dataset A")]+([(self.bf.get(),self.bl.get() or "Dataset B")] if self.mode.get()=="comparison" else []);out=Path(self.out.get())
  if not out.is_dir() or any(not Path(f).is_dir() for f,_ in data):messagebox.showerror("Invalid folder","Select existing folders",parent=self.root);return
  self.running=True;self.start.configure(state="disabled");self.bar.start(10)
  def worker():
   try:r=[analyze(Path(f),out,l,lambda x:self.root.after(0,lambda y=x:self.say(y))) for f,l in data];self.root.after(0,lambda:self.finish(0,r,None))
   except Exception as e:traceback.print_exc();self.root.after(0,lambda e=e:self.finish(1,[],e))
  threading.Thread(target=worker,daemon=True).start()
 def finish(self,code,results,error):
  from tkinter import messagebox
  self.running=False;self.exit_code=code;self.bar.stop();self.start.configure(state="normal");messagebox.showinfo("Complete","\n".join(str(x["output"]) for x in results),parent=self.root) if code==0 else messagebox.showerror("Failed",str(error),parent=self.root)
 def close(self):
  if not self.running:self.root.destroy()
def main():
 import tkinter as tk
 root=tk.Tk();app=GUI(root);root.mainloop();return app.exit_code
if __name__=="__main__":raise SystemExit(main())
