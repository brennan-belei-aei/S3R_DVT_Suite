"""Combined GUI for original Power Profile Smith-chart plots and B-minus-A delta plots."""
import json, os, re, shutil, sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.powerpoint import powerpoint_export_requested  # noqa: E402
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.interpolate import griddata
from matplotlib import cm, patheffects
from matplotlib.colors import Normalize
try:
    import skrf as rf
except ImportError as error:
    raise ImportError("Power Profile analysis requires scikit-rf. Install it with: pip install scikit-rf") from error

APP_TITLE="Paramount Power Profile Combined Analysis"
REQUIRED={"freq","zReal","zImag"}
METRICS=["DCVoltage","DCCurrent","Setpoint","pDelLynx","pDelGen","pError(%)","pFwdGen","pRflGen","zGenReal","zGenImag","pDiss","dwellTime","DCTherm","RFTherm","pInput"]
ORIGINAL_PLOTS=[("pDissGen","Dissipated Power"),("pDiss","Dissipated Power"),("DCVoltage","DC Voltage"),("DCCurrent","DC Current"),("pDelGen","Delivered Power"),("pRflGen","Reflected Power"),("DCTherm","DC Thermistor Temperature"),("RFTherm","RF Thermistor Temperature"),("delta_forward_power","Forward Power Delta")]

def sanitize(s):return re.sub(r"[^\w\-. ]+","_",str(s)).strip().replace(" ","_")
def newest(folder):
    paths=[]
    for p in Path(folder).glob("*.csv"):
        try:
            if REQUIRED.issubset(pd.read_csv(p,nrows=0).columns):paths.append(p)
        except Exception:pass
    if not paths:raise ValueError(f"No compatible Power Profile CSV found in {folder}")
    return max(paths,key=lambda p:p.stat().st_mtime)
def numeric(frame,col):return pd.to_numeric(frame[col],errors="coerce").to_numpy(float)
def compatible(frame):return [m for m in METRICS if m in frame and pd.to_numeric(frame[m],errors="coerce").notna().any()]
def shared(a,b):return [m for m in METRICS if m in compatible(a) and m in compatible(b)]
def gamma_xy(frame,mask):
    z=numeric(frame.loc[mask],"zReal")+1j*numeric(frame.loc[mask],"zImag")
    with np.errstate(divide="ignore",invalid="ignore"):g=(z-50)/(z+50)
    return np.real(g),np.imag(g)
def method(n):return "linear" if n>=3 else "nearest"
def levels(vmin,vmax,n=20):
    if not np.isfinite(vmin) or not np.isfinite(vmax):vmin,vmax=0,1
    if vmin==vmax:vmax=vmin+1e-6
    return np.linspace(vmin,vmax,n)
def smith_base(ax,vswrs,label_style="standard"):
    rf.plotting.smith(ax=ax)
    for value in vswrs:
        radius=(value-1)/(value+1);ax.add_patch(plt.Circle((0,0),radius,fill=False,color="black",linewidth=1.5,zorder=5))
        text=ax.text(0,radius+(0.015 if label_style=="delta" else 0.020),f"{value:g}",ha="center",va="bottom",fontsize=7 if label_style=="delta" else 10,weight="normal" if label_style=="delta" else "bold",color="black",zorder=6)
        if label_style=="delta":text.set_path_effects([patheffects.withStroke(linewidth=2,foreground="white")])
    ax.set_xlim(-1,1);ax.set_ylim(-1,1)

def validate_pair(a,b):
    fa=pd.unique(a.freq);fb=pd.unique(b.freq)
    if not np.array_equal(fa,fb):raise ValueError("Frequency mismatch between Dataset A and Dataset B")
    for f in fa:
        ma=a.freq==f;mb=b.freq==f
        if ma.sum()!=mb.sum() or not np.allclose(numeric(a.loc[ma],"zReal"),numeric(b.loc[mb],"zReal"),equal_nan=True) or not np.allclose(numeric(a.loc[ma],"zImag"),numeric(b.loc[mb],"zImag"),equal_nan=True):raise ValueError(f"Impedance mismatch at {f:g} Hz")
    return fa

def original_plots(frame,label,out,opts):
    out=Path(out)/f"Dataset {label} Original Plots";out.mkdir(parents=True,exist_ok=True);paths=[]
    freqs=pd.unique(frame.freq);x=np.linspace(-1,1,150);y=np.linspace(-1,1,150);X,Y=np.meshgrid(x,y)
    selections=[];seen=set()
    for col,title in ORIGINAL_PLOTS:
        if col in frame and title not in seen and opts["original_metrics"].get(title,False):selections.append((col,title));seen.add(title)
    for f in freqs:
        mask=frame.freq==f;gr,gi=gamma_xy(frame,mask);finite_gamma=np.isfinite(gr)&np.isfinite(gi)
        for col,title in selections:
            vals=numeric(frame.loc[mask],col);valid=finite_gamma&np.isfinite(vals)
            if not valid.any():continue
            vmin=float(np.nanmin(vals));vmax=float(np.nanmax(vals));lev=levels(vmin,vmax,20)
            try:Z=griddata((gr[valid],gi[valid]),vals[valid],(X,Y),method=method(valid.sum()))
            except Exception:Z=griddata((gr[valid],gi[valid]),vals[valid],(X,Y),method="nearest")
            fig,ax=plt.subplots(figsize=(7,7));smith_base(ax,opts["original_vswr"],"standard")
            try:cont=ax.contourf(X,Y,Z,cmap=cm.jet,levels=lev)
            except Exception:
                Z=griddata((gr[valid],gi[valid]),vals[valid],(X,Y),method="nearest");cont=ax.contourf(X,Y,Z,cmap=cm.jet,levels=lev)
            ax.set_title(f"Dataset {label} - {title}");ax.text(-0.3,1.4,f"{f/1e6:.2f} MHz");bar=fig.colorbar(cont,ax=ax,ticks=np.linspace(vmin,vmax,8));bar.ax.set_ylabel(title)
            p=out/f"Dataset_{label}_{sanitize(title)}_{f/1e6:.2f}_MHz.png";fig.savefig(p,dpi=opts["dpi"],bbox_inches="tight");plt.close(fig);paths.append(p)
        if opts["impedance"] and "zGenReal" in frame and "zGenImag" in frame:
            zgr=numeric(frame.loc[mask],"zGenReal");zgi=numeric(frame.loc[mask],"zGenImag");zg=zgr+1j*zgi
            with np.errstate(divide="ignore",invalid="ignore"):gg=(zg-50)/(zg+50)
            fig,ax=plt.subplots(figsize=(7,7));rf.plotting.smith(ax=ax);ax.plot(gr,gi,'bo',ms=4,label='Match Impedance');ax.plot(np.real(gg),np.imag(gg),'rx',ms=5,label='Generator Measured Impedance');ax.text(-.9,.9,f"Dataset {label} - {f/1e6:.2f} MHz");ax.legend();ax.set_xlim(-1,1);ax.set_ylim(-1,1)
            p=out/f"Dataset_{label}_Impedance_{f/1e6:.2f}_MHz.png";fig.savefig(p,dpi=opts["dpi"],bbox_inches="tight");plt.close(fig);paths.append(p)
    return paths

def fill_nan(x,y,v,method_name):
    good=np.isfinite(v)
    if good.sum()<3:return v
    out=v.copy();out[~good]=griddata((x[good],y[good]),v[good],(x[~good],y[~good]),method=method_name);return out

def delta_plots(a,b,out,opts):
    out=Path(out)/"Delta Plots";out.mkdir(parents=True,exist_ok=True);paths=[];rows=[];freqs=validate_pair(a,b);x=np.linspace(-1,1,150);y=np.linspace(-1,1,150);X,Y=np.meshgrid(x,y)
    for f in freqs:
        ma=a.freq==f;mb=b.freq==f;zr=numeric(a.loc[ma],"zReal");zi=numeric(a.loc[ma],"zImag");av=numeric(a.loc[ma],opts["metric"]);bv=numeric(b.loc[mb],opts["metric"]);raw=bv-av;final=raw.copy();filtered=np.zeros(len(raw),bool);interpolated=np.zeros(len(raw),bool)
        if opts["filter"]:
            filtered=np.abs(raw)>opts["limit"];final=np.where(filtered,np.nan,raw)
            if opts["interpolate"]:
                before=~np.isfinite(final);filled=fill_nan(zr,zi,final,opts["interpolation"]);interpolated=before&np.isfinite(filled);final=filled
        for i in range(len(raw)):rows.append({"freq":f,"zReal":zr[i],"zImag":zi[i],"A":av[i],"B":bv[i],"Raw Delta B-A":raw[i],"Filtered":filtered[i],"Interpolated":interpolated[i],"Final Delta B-A":final[i]})
        z=zr+1j*zi
        with np.errstate(divide="ignore",invalid="ignore"):g=(z-50)/(z+50)
        gr,gi=np.real(g),np.imag(g);valid=np.isfinite(gr)&np.isfinite(gi)&np.isfinite(final)
        if not valid.any():continue
        try:Z=griddata((gr[valid],gi[valid]),final[valid],(X,Y),method=method(valid.sum()))
        except Exception:Z=griddata((gr[valid],gi[valid]),final[valid],(X,Y),method="nearest")
        Z=np.clip(Z,opts["delta_min"],opts["delta_max"]);lev=np.linspace(opts["delta_min"],opts["delta_max"],21);norm=Normalize(opts["delta_min"],opts["delta_max"])
        fig,ax=plt.subplots(figsize=(7,7));smith_base(ax,opts["delta_vswr"],"delta");cont=ax.contourf(X,Y,Z,levels=lev,cmap=cm.jet,norm=norm,extend="both");bar=fig.colorbar(cont,ax=ax);bar.set_ticks(np.linspace(opts["delta_min"],opts["delta_max"],7));ax.set_title(opts["delta_title"]);ax.text(-.3,1.4,f"{f/1e6:.2f} MHz")
        p=out/f"{sanitize(opts['delta_title'])}_{f/1e6:.2f}.png";fig.savefig(p,dpi=opts["dpi"],bbox_inches="tight");plt.close(fig);paths.append(p)
    return paths,pd.DataFrame(rows)

def write_audit(path,audit,settings):
    with pd.ExcelWriter(path,engine="openpyxl") as w:
        audit.to_excel(w,sheet_name="Delta Audit",index=False);pd.DataFrame({"Setting":list(settings),"Value":list(settings.values())}).to_excel(w,sheet_name="Settings",index=False)

def gui():
    root=tk.Tk();root.title(APP_TITLE);root.geometry("1080x820");outer=ttk.Frame(root,padding=14);outer.pack(fill="both",expand=True);ttk.Label(outer,text=APP_TITLE,font=("Segoe UI",15,"bold")).pack(anchor="w");book=ttk.Notebook(outer);book.pack(fill="both",expand=True,pady=10)
    inp=ttk.Frame(book,padding=12);orig=ttk.Frame(book,padding=12);delta=ttk.Frame(book,padding=12);outtab=ttk.Frame(book,padding=12);book.add(inp,text="Input Data");book.add(orig,text="Original Plots");book.add(delta,text="Delta Plot");book.add(outtab,text="Output")
    launcher_mode=os.environ.get("PARAMOUNT_ANALYSIS_MODE","INDIVIDUAL").strip().upper()
    prefer_compare=launcher_mode=="COMPARE"
    pa=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_A_FOLDER","").strip());pb=tk.StringVar(value=os.environ.get("PARAMOUNT_DATASET_B_FOLDER","").strip());metric=tk.StringVar(value="pDelGen");preview=tk.StringVar(value="Select Dataset A. Dataset B is optional unless B plots or delta plots are requested.");run_a=tk.BooleanVar(value=True);run_b=tk.BooleanVar(value=prefer_compare);run_delta=tk.BooleanVar(value=prefer_compare);imp=tk.BooleanVar(value=True)
    dataset_a_label=os.environ.get("PARAMOUNT_DATASET_A_LABEL","").strip() or "Dataset A"
    dataset_b_label=os.environ.get("PARAMOUNT_DATASET_B_LABEL","").strip() or "Dataset B"
    original_defs={title:tk.BooleanVar(value=True) for _,title in ORIGINAL_PLOTS if title not in {"Forward Power Delta"}};dtitle=tk.StringVar(value="Delivered Power Delta: New − Baseline");lim=tk.StringVar(value="40");dmin=tk.StringVar(value="-30");dmax=tk.StringVar(value="30");filt=tk.BooleanVar(value=True);interp=tk.BooleanVar(value=True);imethod=tk.StringVar(value="nearest");dpi=tk.StringVar(value="200");delta_outdir=tk.StringVar();audit=tk.BooleanVar(value=True)
    inp.columnconfigure(1,weight=1);combo=ttk.Combobox(inp,textvariable=metric,values=METRICS,state="readonly",width=30);combo.current(METRICS.index("pDelGen"))
    def browse(v):
        current=v.get().strip()
        x=filedialog.askdirectory(parent=root,mustexist=True,initialdir=current if current and Path(current).is_dir() else None)
        if x:v.set(x);refresh()
    def refresh():
        text=[]
        for label,v in (("A",pa),("B",pb)):
            if v.get():
                try:p=newest(v.get());d=pd.read_csv(p);text.append(f"{label}: {p.name} | {len(d)} rows | {d.freq.nunique()} frequencies | metrics: {', '.join(compatible(d))}")
                except Exception as e:text.append(f"{label}: {e}")
        if pa.get() and not pb.get():
            text.append("Dataset B is optional. Dataset A original plots can run by themselves.")
        preview.set("\n".join(text) if text else "Select Dataset A. Dataset B is optional unless B plots or delta plots are requested.")
    for row,label,v in [(0,"Dataset A folder",pa),(1,"Dataset B folder (optional)",pb)]:ttk.Label(inp,text=label).grid(row=row,column=0,sticky="w",pady=5);ttk.Entry(inp,textvariable=v).grid(row=row,column=1,sticky="ew");ttk.Button(inp,text="Browse...",command=lambda v=v:browse(v)).grid(row=row,column=2,padx=8)
    ttk.Label(inp,text="Delta metric (A minus B)").grid(row=2,column=0,sticky="w");combo.grid(row=2,column=1,sticky="w");ttk.Label(inp,textvariable=preview,wraplength=950,justify="left").grid(row=3,column=0,columnspan=3,sticky="w",pady=12)
    ttk.Checkbutton(orig,text="Generate Dataset A original plots",variable=run_a).grid(row=0,column=0,columnspan=2,sticky="w");ttk.Checkbutton(orig,text="Generate Dataset B original plots",variable=run_b).grid(row=1,column=0,columnspan=2,sticky="w");ttk.Checkbutton(orig,text="Generate impedance overlay plots",variable=imp).grid(row=2,column=0,columnspan=2,sticky="w",pady=(0,12))
    r=3
    for title,var in original_defs.items():ttk.Checkbutton(orig,text=title,variable=var).grid(row=r,column=0,sticky="w");r+=1
    ttk.Checkbutton(delta,text="Generate B-minus-A delta plots",variable=run_delta).grid(row=0,column=0,columnspan=2,sticky="w")
    for row,label,var in [(1,"Delta plot title",dtitle),(2,"Absolute outlier limit",lim),(3,"Minimum color tick",dmin),(4,"Maximum color tick",dmax)]:ttk.Label(delta,text=label).grid(row=row,column=0,sticky="w",pady=5);ttk.Entry(delta,textvariable=var,width=40).grid(row=row,column=1,sticky="w")
    ttk.Checkbutton(delta,text="Enable outlier filter",variable=filt).grid(row=5,column=0,columnspan=2,sticky="w");ttk.Checkbutton(delta,text="Enable local interpolation",variable=interp).grid(row=6,column=0,columnspan=2,sticky="w");ttk.Label(delta,text="Interpolation method").grid(row=7,column=0,sticky="w");ttk.Combobox(delta,textvariable=imethod,values=["nearest","linear"],state="readonly").grid(row=7,column=1,sticky="w")
    outtab.columnconfigure(1,weight=1);ttk.Label(outtab,text="Delta/comparison output folder (blank = Dataset A folder)").grid(row=0,column=0,sticky="w");ttk.Entry(outtab,textvariable=delta_outdir).grid(row=0,column=1,sticky="ew");ttk.Label(outtab,text="Image DPI").grid(row=1,column=0,sticky="w");ttk.Entry(outtab,textvariable=dpi,width=15).grid(row=1,column=1,sticky="w");ttk.Checkbutton(outtab,text="Write delta audit workbook",variable=audit).grid(row=2,column=0,columnspan=2,sticky="w")
    result={"ok":False}
    def start():
        try:
            ca=newest(pa.get());fa=pd.read_csv(ca);m=metric.get()
            needs_b=run_b.get() or run_delta.get()
            cb=None;fb=None
            if needs_b:
                if not pb.get().strip():
                    raise ValueError("Select Dataset B or disable Dataset B original plots and delta plots.")
                cb=newest(pb.get());fb=pd.read_csv(cb)
            if run_delta.get():
                validate_pair(fa,fb)
                if m not in fa or m not in fb:
                    raise ValueError(f"Metric {m} is not present in both datasets")
            if not run_a.get() and not run_b.get() and not run_delta.get():
                raise ValueError("Select at least one plot output.")
            dataset_a_output=ca.parent
            dataset_b_output=cb.parent if cb else None
            delta_output=Path(delta_outdir.get()).resolve() if delta_outdir.get().strip() else ca.parent
            if not dataset_a_output.is_dir():raise ValueError("Dataset A output folder does not exist")
            if dataset_b_output is not None and not dataset_b_output.is_dir():raise ValueError("Dataset B output folder does not exist")
            if not delta_output.is_dir():raise ValueError("Delta/comparison output folder does not exist")
            lo=float(dmin.get());hi=float(dmax.get())
            if lo>=hi:raise ValueError("Minimum delta tick must be less than maximum")
            result.update(ok=True,ca=str(ca),cb=str(cb) if cb else None,dataset_a_label=dataset_a_label,dataset_b_label=dataset_b_label,out_a=str(dataset_a_output),out_b=str(dataset_b_output) if dataset_b_output else None,out_delta=str(delta_output),run_a=run_a.get(),run_b=run_b.get(),run_delta=run_delta.get(),impedance=imp.get(),original_metrics={k:v.get() for k,v in original_defs.items()},metric=m,delta_title=dtitle.get(),limit=float(lim.get()),delta_min=lo,delta_max=hi,filter=filt.get(),interpolate=interp.get(),interpolation=imethod.get(),dpi=int(float(dpi.get())),audit=audit.get(),difference_convention="B_MINUS_A",original_vswr=[1.1,1.6,2,2.5],delta_vswr=[1.1,1.6,2,2.5,3]);root.destroy()
        except Exception as e:messagebox.showerror("Invalid settings",str(e),parent=root)
    buttons=ttk.Frame(outer);buttons.pack(fill="x");ttk.Button(buttons,text="Cancel",command=root.destroy).pack(side="right",padx=8);ttk.Button(buttons,text="Start Analysis",command=start).pack(side="right");root.mainloop();return result

def export_powerpoint_package(opts, generated_images, audit=None, audit_workbook=None):
    if not powerpoint_export_requested():return None
    folder_text=os.environ.get("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER","").strip()
    if not folder_text:raise ValueError("PARAMOUNT_POWERPOINT_PACKAGE_FOLDER is not set.")
    package=Path(folder_text).expanduser().resolve();package.mkdir(parents=True,exist_ok=True);manifest_path=package/"manifest.json"
    if manifest_path.exists():manifest_path.unlink()
    images_root=package/"images";tables_root=package/"tables"
    if images_root.exists():shutil.rmtree(images_root)
    if tables_root.exists():shutil.rmtree(tables_root)
    tables_root.mkdir(parents=True,exist_ok=True)
    if audit is not None and not audit.empty:audit.to_csv(tables_root/"comparison_b_minus_a.csv",index=False)
    slides=[];copied=[]
    for role,source_path in generated_images:
        source=Path(source_path)
        if not source.is_file():continue
        dest_dir=images_root/role;dest_dir.mkdir(parents=True,exist_ok=True);dest=dest_dir/source.name;shutil.copy2(source,dest);relative=dest.relative_to(package).as_posix()
        copied.append({"dataset_role":role.upper(),"source":str(source),"package_file":relative})
        slide={"slide_type":"image","title":source.name,"image_file":relative,"dataset_role":role.upper()}
        if role=="comparison":slide["difference_convention"]="B_MINUS_A"
        slides.append(slide)
    comparison_used=bool(opts["run_delta"]);a_label=opts.get("dataset_a_label") or "Dataset A";b_label=opts.get("dataset_b_label") or "Dataset B"
    dataset_b={"label":b_label,"source_folder":str(Path(opts["cb"]).parent)} if opts.get("cb") else None
    manifest={"schema_version":int(os.environ.get("PARAMOUNT_POWERPOINT_SCHEMA_VERSION","1")),"analysis_id":os.environ.get("PARAMOUNT_POWERPOINT_ANALYSIS_ID","power_profile").strip(),"display_name":"Power Profile","status":"completed","analysis_mode":"COMPARE" if (opts["run_b"] or opts["run_delta"]) else "SINGLE","dataset_label":a_label if not dataset_b else f"{b_label} vs {a_label}","source_folder":str(Path(opts["ca"]).parent),"dataset_a":{"label":a_label,"source_folder":str(Path(opts["ca"]).parent)},"dataset_b":dataset_b,"comparison":{"completed":comparison_used,"difference_convention":"B_MINUS_A","workbook":str(audit_workbook) if audit_workbook else ""},"highlights":[f"Dataset A: {a_label}",f"Generated {len(slides)} image slide(s) from this analysis run."],"metrics":{"Generated image slides":len(slides),"Delta convention":"B_MINUS_A"},"generated_images":copied,"slides":slides}
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding="utf-8");return manifest_path


def main():
    opts=gui()
    if not opts.get("ok"):return
    a=pd.read_csv(opts["ca"])
    b=pd.read_csv(opts["cb"]) if opts.get("cb") else None
    count=0;generated_images=[];audit=None;audit_workbook=None
    print("Starting Power Profile combined analysis...",flush=True)
    if opts["run_a"]:
        print("Generating Dataset A original plots...",flush=True);paths=original_plots(a,"A",opts["out_a"],opts);count+=len(paths);generated_images.extend(("dataset_a",p) for p in paths)
    if opts["run_b"]:
        print("Generating Dataset B original plots...",flush=True);paths=original_plots(b,"B",opts["out_b"],opts);count+=len(paths);generated_images.extend(("dataset_b",p) for p in paths)
    if opts["run_delta"]:
        print("Generating B-minus-A delta plots...",flush=True);paths,audit=delta_plots(a,b,opts["out_delta"],opts);count+=len(paths);generated_images.extend(("comparison",p) for p in paths)
        if opts["audit"]:audit_workbook=Path(opts["out_delta"])/"Power_Profile_Delta_Analysis.xlsx";write_audit(audit_workbook,audit,{k:v for k,v in opts.items() if k not in {"original_metrics"}})
    warning=None
    if powerpoint_export_requested():
        try:
            manifest=export_powerpoint_package(opts,generated_images,audit,audit_workbook)
            if manifest:print(f"PowerPoint package manifest saved to: {manifest}")
        except Exception as error:warning=f"PowerPoint package export warning: {error}";print(warning,file=sys.stderr)
    root=tk.Tk();root.withdraw();message=f"Generated {count} plots.\n\nDataset A plot folder:\n{opts['out_a']}"
    if opts.get("out_b") and opts["run_b"]:message+=f"\n\nDataset B plot folder:\n{opts['out_b']}"
    if opts["run_delta"]:message+=f"\n\nDelta/comparison output folder:\n{opts['out_delta']}"
    if warning:message+=f"\n\n{warning}\nNormal analysis outputs were still completed."
    messagebox.showinfo("Complete",message,parent=root);root.destroy()
if __name__=="__main__":main()
