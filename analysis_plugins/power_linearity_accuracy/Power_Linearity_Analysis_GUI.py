"""Standalone Power Linearity analysis and two-dataset comparison GUI."""
import json, os, shutil, sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.powerpoint import powerpoint_export_requested  # noqa: E402
from suite_common.reports import open_with_default_app  # noqa: E402
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

APP_TITLE="Paramount Power Linearity Data Analysis"
SETTINGS_FILE_NAME="Power_Linearity_Run_Settings.csv"
WORKBOOK_NAME="Power_Linearity_Analysis.xlsx"
COMPARISON_FOLDER="Hardware Configuration Comparison"
COMPARISON_WORKBOOK="Power_Linearity_Hardware_Comparison.xlsx"
REQUIRED=['Measure_Frequency','Gen_Forward_Power','Gen_Reflected_Power','Gen_Delivered_Power','Lynx_Forward_Power','Lynx_Reflected_Power','Lynx_Delivered_Power','Measured_R','Measured_X','Lynx_V','Lynx_I']

class AnalysisProgress:
 def __init__(self):
  self.root=tk.Tk();self.root.title('Power Linearity Analysis Progress');self.root.geometry('720x235');self.root.resizable(True,False);self.root.protocol('WM_DELETE_WINDOW',lambda:None);f=ttk.Frame(self.root,padding=16);f.pack(fill='both',expand=True);self.h=tk.StringVar(value='Preparing analysis');self.d=tk.StringVar(value='');self.p=tk.StringVar(value='0%');ttk.Label(f,textvariable=self.h,font=('Segoe UI',12,'bold')).pack(anchor='w');ttk.Label(f,textvariable=self.d,wraplength=675).pack(anchor='w',pady=(8,12));self.bar=ttk.Progressbar(f,maximum=100,mode='determinate');self.bar.pack(fill='x');ttk.Label(f,textvariable=self.p).pack(anchor='e');self.root.update()
 def update(self,v,h=None,d=None):
  v=max(0,min(100,float(v)));self.bar['value']=v;self.p.set(f'{v:.0f}%');self.h.set(h or self.h.get());self.d.set(d or self.d.get());self.root.update_idletasks();self.root.update()
 def close(self):
  try:self.root.destroy()
  except tk.TclError:pass

def read_settings(folder):
 p=Path(folder)/SETTINGS_FILE_NAME
 if not p.exists():return {}
 d=pd.read_csv(p,dtype=str)
 return dict(zip(d.setting,d.value)) if {'setting','value'}.issubset(d.columns) else {}

def measurement_files(folder):
 return sorted(p for p in Path(folder).glob('*.csv') if p.name!=SETTINGS_FILE_NAME)

def load_dataset(folder,name):
 frames=[];fail=[]
 for p in measurement_files(folder):
  try:
   d=pd.read_csv(p);missing=[c for c in REQUIRED if c not in d]
   if missing:raise ValueError('Missing columns: '+', '.join(missing))
   d.insert(0,'Dataset',name);d.insert(1,'Source CSV',p.name);frames.append(d)
  except Exception as e:fail.append({'Dataset':name,'CSV Path':str(p),'Error':str(e)})
 if not frames:raise ValueError(f'No valid Power Linearity CSV found for {name}.')
 d=pd.concat(frames,ignore_index=True)
 for c in REQUIRED:d[c]=pd.to_numeric(d[c],errors='coerce')
 d=d.dropna(subset=['Measure_Frequency','Gen_Forward_Power','Lynx_Delivered_Power']).copy()
 return d,pd.DataFrame(fail),read_settings(folder)

def tolerance_percent(setpoint,transition=300,absolute_w=3,high_percent=1):
 p=np.asarray(setpoint,float)
 with np.errstate(divide='ignore',invalid='ignore'):
  upper=np.where(p<transition,absolute_w/p*100,high_percent)
 upper[~np.isfinite(upper)]=np.nan
 return upper

def analyze(data,transition=300,absolute_w=3,high_percent=1):
 # Preserve collection-file row order here. Cycle reconstruction depends on the
 # original acquisition order and must occur before any setpoint-based sorting.
 d=data.copy().reset_index(drop=True);d['Frequency (kHz)']=d.Measure_Frequency.round().astype(int)
 d['Setpoint (W)']=d.Gen_Forward_Power
 d['Measured Delivered Power (W)']=d.Lynx_Delivered_Power
 d['Percent Error (%)']=np.where(d['Setpoint (W)']!=0,(d['Measured Delivered Power (W)']-d['Setpoint (W)'])/d['Setpoint (W)']*100,np.nan)
 d['Upper Bound (%)']=tolerance_percent(d['Setpoint (W)'],transition,absolute_w,high_percent);d['Lower Bound (%)']=-d['Upper Bound (%)']
 d['Within Linearity Limit']=d['Percent Error (%)'].between(d['Lower Bound (%)'],d['Upper Bound (%)'])
 return d


def multi_cycle_averages(data,settings):
 """Average repeated sweeps by frequency and sweep-point position."""
 try:cycles=max(1,int(float(settings.get('number_of_collection_cycles',1))))
 except (TypeError,ValueError):cycles=1
 d=data.copy().reset_index(drop=True);d['Cycle Number']=1;d['Sweep Point']=d.groupby('Frequency (kHz)',sort=False).cumcount()+1
 incomplete_frequencies=[]
 for frequency,indexes in d.groupby('Frequency (kHz)',sort=False).groups.items():
  indexes=list(indexes);count=len(indexes)
  if cycles>1 and count%cycles==0:
   points=count//cycles
   # Rows are still in original collection order, so each consecutive block is
   # one complete low-to-high sweep for this RF frequency.
   d.loc[indexes,'Cycle Number']=np.repeat(np.arange(1,cycles+1),points)
   d.loc[indexes,'Sweep Point']=np.tile(np.arange(1,points+1),cycles)
  elif cycles>1:
   incomplete_frequencies.append((frequency,count))
 if incomplete_frequencies:
  details=', '.join(f'{frequency} kHz: {count} rows' for frequency,count in incomplete_frequencies)
  raise ValueError(f'Cannot reconstruct {cycles} complete collection cycles because row counts are not divisible by the recorded cycle count. {details}')
 keys=['Frequency (kHz)','Sweep Point']
 numeric=[c for c in d.select_dtypes(include=[np.number]).columns if c not in keys+['Cycle Number']]
 avg=d.groupby(keys,sort=True)[numeric].mean().reset_index()
 counts=d.groupby(keys).size().rename('Sample Count').reset_index();avg=counts.merge(avg,on=keys)
 for metric in ['Setpoint (W)','Measured Delivered Power (W)','Percent Error (%)']:
  stats=d.groupby(keys)[metric].agg(['std','min','max']).reset_index()
  stats=stats.rename(columns={'std':f'{metric} Std Dev','min':f'{metric} Minimum','max':f'{metric} Maximum'})
  stats[f'{metric} Std Dev']=stats[f'{metric} Std Dev'].fillna(0.0);avg=avg.merge(stats,on=keys)
 avg['Upper Bound (%)']=tolerance_percent(avg['Setpoint (W)'])
 avg['Lower Bound (%)']=-avg['Upper Bound (%)']
 avg['Within Linearity Limit']=avg['Percent Error (%)'].between(avg['Lower Bound (%)'],avg['Upper Bound (%)'])
 return d,avg,cycles

def plot_cycles_all(data,folder,title_prefix):
 folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);paths=[]
 for cycle,gc in data.groupby('Cycle Number',sort=True):
  cycle_number=int(cycle);fig,ax=plt.subplots(figsize=(10,6));allsp=np.sort(gc['Setpoint (W)'].dropna().unique());bounds=tolerance_percent(allsp);ax.plot(allsp,bounds,'r--',label='Upper Bound');ax.plot(allsp,-bounds,'r--',label='Lower Bound')
  for f,g in gc.groupby('Frequency (kHz)',sort=True):
   g=g.sort_values('Sweep Point');ax.plot(g['Setpoint (W)'],g['Percent Error (%)'],marker='o',ms=3,lw=1.2,label=f'{f} kHz')
  ax.set(title=f'{title_prefix} - Collection Cycle {cycle_number}',xlabel='Setpoint (W)',ylabel='Percent Error [%]');ax.set_ylim(-2.5,2.5);ax.grid(True,alpha=.3);ax.legend();fig.tight_layout();path=folder/f'Power_Linearity_Cycle_{cycle_number:02d}_All_Frequencies.png';fig.savefig(path,dpi=200,bbox_inches='tight');plt.close(fig);paths.append(path)
 return paths

def plot_cycles_per_frequency(data,folder,title_prefix):
 folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);paths=[]
 for (f,cycle),g in data.groupby(['Frequency (kHz)','Cycle Number'],sort=True):
  cycle_number=int(cycle);g=g.sort_values('Sweep Point');fig,ax=plt.subplots(figsize=(9,6));ax.plot(g['Setpoint (W)'],g['Percent Error (%)'],marker='o',lw=1.2,label=f'{f} kHz')
  allsp=np.sort(g['Setpoint (W)'].dropna().unique());bounds=tolerance_percent(allsp);ax.plot(allsp,bounds,'r--',label='Upper Bound');ax.plot(allsp,-bounds,'r--',label='Lower Bound');ax.set(title=f'{title_prefix} - {f} kHz - Cycle {cycle_number}',xlabel='Setpoint (W)',ylabel='Percent Error [%]');ax.set_ylim(-2.5,2.5);ax.grid(True,alpha=.3);ax.legend();fig.tight_layout();path=folder/f'Power_Linearity_{f}kHz_Cycle_{cycle_number:02d}.png';fig.savefig(path,dpi=200,bbox_inches='tight');plt.close(fig);paths.append(path)
 return paths

def plot_averages(data,path,title):
 fig,ax=plt.subplots(figsize=(10,6));allsp=np.sort(data['Setpoint (W)'].dropna().unique());bounds=tolerance_percent(allsp)
 ax.plot(allsp,bounds,'r--',label='Upper Bound');ax.plot(allsp,-bounds,'r--',label='Lower Bound')
 for f,g in data.groupby('Frequency (kHz)'):
  g=g.sort_values('Sweep Point');ax.plot(g['Setpoint (W)'],g['Percent Error (%)'],marker='o',ms=3,lw=1.5,label=f'{f} kHz average')
  if 'Percent Error (%) Std Dev' in g:ax.fill_between(g['Setpoint (W)'],g['Percent Error (%)']-g['Percent Error (%) Std Dev'],g['Percent Error (%)']+g['Percent Error (%) Std Dev'],alpha=.10)
 ax.set_title(title);ax.set_xlabel('Average Generator Forward Power (W)');ax.set_ylabel('Average Percent Error [%]');ax.set_ylim(-2.5,2.5);ax.grid(True,alpha=.3);ax.legend(fontsize=8);fig.tight_layout();fig.savefig(path,dpi=250,bbox_inches='tight');plt.close(fig)

def summary(data):
 rows=[]
 for f,g in data.groupby('Frequency (kHz)'):
  err=g['Percent Error (%)'].dropna();rows.append({'Frequency (kHz)':f,'Point Count':len(g),'Minimum Setpoint (W)':g['Setpoint (W)'].min(),'Maximum Setpoint (W)':g['Setpoint (W)'].max(),'Average Error (%)':err.mean(),'Minimum Error (%)':err.min(),'Maximum Error (%)':err.max(),'Std Dev Error (%)':err.std(ddof=1) if len(err)>1 else 0,'Fail Count':int((~g['Within Linearity Limit']).sum()),'Result':'PASS' if g['Within Linearity Limit'].all() else 'FAIL'})
 return pd.DataFrame(rows)

def plot_all(data,path,title,ylim=(-2.5,2.5)):
 fig,ax=plt.subplots(figsize=(10,6));allsp=np.sort(data['Setpoint (W)'].dropna().unique());bounds=tolerance_percent(allsp)
 ax.plot(allsp,bounds,'r--',label='Upper Bound');ax.plot(allsp,-bounds,'r--',label='Lower Bound')
 for f,g in data.groupby('Frequency (kHz)'):ax.plot(g['Setpoint (W)'],g['Percent Error (%)'],marker='o',ms=3,lw=1.5,label=f'{f} kHz')
 ax.set_title(title);ax.set_xlabel('Setpoint (W)');ax.set_ylabel('Percent Error [%]');ax.set_ylim(*ylim);ax.grid(True,alpha=.3);ax.legend(ncol=2);fig.tight_layout();fig.savefig(path,dpi=150,bbox_inches='tight');plt.close(fig)

def plot_per_frequency(data,folder,title_prefix):
 folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);paths=[]
 for f,g in data.groupby('Frequency (kHz)'):
  fig,ax=plt.subplots(figsize=(9,6));ax.plot(g['Setpoint (W)'],g['Percent Error (%)'],'g-',label='Measured');ax.plot(g['Setpoint (W)'],g['Upper Bound (%)'],'r-',label='Upper Bound');ax.plot(g['Setpoint (W)'],g['Lower Bound (%)'],'r-',label='Lower Bound');ax.set_title(f'{title_prefix} - {f} kHz');ax.set_xlabel('Setpoint (W)');ax.set_ylabel('Percent Error');ax.set_ylim(-2.5,2.5);ax.grid(True,alpha=.3);ax.legend();fig.tight_layout();p=folder/f'Power_Linearity_{f}kHz.png';fig.savefig(p,dpi=150,bbox_inches='tight');plt.close(fig);paths.append(p)
 return paths

def compare(a,b,na,nb):
 # Compare corresponding averaged sweep positions. Exact measured generator-forward
 # powers naturally vary between hardware configurations and are retained as metrics.
 keys=['Frequency (kHz)','Sweep Point'];metrics=['Setpoint (W)','Measured Delivered Power (W)','Percent Error (%)']
 aa=a[keys+metrics].rename(columns={metric:f'{na} {metric}' for metric in metrics});bb=b[keys+metrics].rename(columns={metric:f'{nb} {metric}' for metric in metrics})
 d=aa.merge(bb,on=keys,how='outer',indicator=True);d['Matched Condition']=d._merge.eq('both');d['Availability']=d._merge.map({'both':'Matched','left_only':f'Only {na}','right_only':f'Only {nb}'})
 for metric in metrics:
  diff=d[f'{nb} {metric}']-d[f'{na} {metric}'];d[f'{metric} Difference B-A']=diff;d[f'{metric} Absolute Difference']=diff.abs()
 d['Comparison Setpoint (W)']=d[[f'{na} Setpoint (W)',f'{nb} Setpoint (W)']].mean(axis=1,skipna=True)
 return d.drop(columns='_merge').sort_values(keys).reset_index(drop=True)

def comparison_plots(d,na,nb,folder):
 folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);paths=[];d=d[d['Matched Condition']]
 for f,g in d.groupby('Frequency (kHz)'):
  g=g.sort_values('Sweep Point');fig,(top,bot)=plt.subplots(2,1,figsize=(10,8),sharex=True,gridspec_kw={'height_ratios':[2.2,1]});x=g['Comparison Setpoint (W)'];top.plot(x,g[f'{na} Percent Error (%)'],marker='o',label=na);top.plot(x,g[f'{nb} Percent Error (%)'],marker='s',linestyle='--',label=nb);bot.plot(x,g['Percent Error (%) Difference B-A'],marker='o',color='#C00000');bot.axhline(0,color='black',lw=1);top.set_ylabel('Percent Error (%)');bot.set_ylabel('B - A (%)');bot.set_xlabel('Average of A/B measured generator forward power (W)');top.grid(True,alpha=.3);bot.grid(True,alpha=.3);top.legend();fig.suptitle(f'Power Linearity Comparison - {f} kHz');fig.tight_layout();p=folder/f'Comparison_{f}kHz.png';fig.savefig(p,dpi=200,bbox_inches='tight');plt.close(fig);paths.append(p)
 return paths

def autosize(w):
 for s in w.book.worksheets:
  s.freeze_panes='A2';s.auto_filter.ref=s.dimensions
  for cells in s.columns:s.column_dimensions[cells[0].column_letter].width=min(max(max(len(str(c.value)) if c.value is not None else 0 for c in cells)+2,10),55)

def write_book(path,data,averages,summ,fail,settings,analysis):
 with pd.ExcelWriter(path,engine='openpyxl') as w:
  data.to_excel(w,sheet_name='Analyzed Data',index=False);averages.to_excel(w,sheet_name='Sweep Point Averages',index=False);summ.to_excel(w,sheet_name='Frequency Summary',index=False)
  bad=data[~data['Within Linearity Limit']]
  if not bad.empty:bad.to_excel(w,sheet_name='Limit Failures',index=False)
  if not fail.empty:fail.to_excel(w,sheet_name='Failed Files',index=False)
  pd.DataFrame({'Setting':list(settings),'Value':list(settings.values())}).to_excel(w,sheet_name='Run Settings',index=False);pd.DataFrame({'Setting':list(analysis),'Value':list(analysis.values())}).to_excel(w,sheet_name='Analysis Settings',index=False);autosize(w)

def write_compare(path,d,sa,sb,settings):
 with pd.ExcelWriter(path,engine='openpyxl') as w:
  d.to_excel(w,sheet_name='Condition Comparison',index=False);r=d.copy();r['Ranking']=r.filter(like='Absolute Difference').max(axis=1);r.sort_values('Ranking',ascending=False).to_excel(w,sheet_name='Difference Ranking',index=False);sa.to_excel(w,sheet_name='A Frequency Summary',index=False);sb.to_excel(w,sheet_name='B Frequency Summary',index=False);pd.DataFrame({'Setting':list(settings),'Value':list(settings.values())}).to_excel(w,sheet_name='Comparison Settings',index=False);autosize(w)

def export_powerpoint_package(c,images,avg_a,avg_b=None,comparison=None,wa=None,wb=None,cw=None):
 if not powerpoint_export_requested():return None
 text=os.environ.get('PARAMOUNT_POWERPOINT_PACKAGE_FOLDER','').strip()
 if not text:raise ValueError('PARAMOUNT_POWERPOINT_PACKAGE_FOLDER is not set.')
 package=Path(text).expanduser().resolve();package.mkdir(parents=True,exist_ok=True);manifest=package/'manifest.json'
 if manifest.exists():manifest.unlink()
 image_root=package/'images';table_root=package/'tables'
 if image_root.exists():shutil.rmtree(image_root)
 if table_root.exists():shutil.rmtree(table_root)
 table_root.mkdir(parents=True,exist_ok=True);avg_a.to_csv(table_root/'dataset_a_sweep_point_averages.csv',index=False)
 if avg_b is not None:avg_b.to_csv(table_root/'dataset_b_sweep_point_averages.csv',index=False)
 if comparison is not None:comparison.to_csv(table_root/'comparison_b_minus_a.csv',index=False)
 slides=[];copied=[]
 # Build a de-duplicated image inventory. In comparison mode, explicitly discover
 # comparison PNGs from the authoritative comparison folder as a safeguard against
 # a stale generated_images list or a caller omitting the returned plot paths.
 image_inventory=list(images)
 if c['mode']=='COMPARE':
  comparison_plot_folder=Path(c['a'])/COMPARISON_FOLDER/'Comparison Plots'
  existing_sources={str(Path(path).resolve()) for _,path in image_inventory}
  if comparison_plot_folder.is_dir():
   for comparison_image in sorted(comparison_plot_folder.glob('*.png')):
    resolved=str(comparison_image.resolve())
    if resolved not in existing_sources:
     image_inventory.append(('comparison',comparison_image));existing_sources.add(resolved)
  if not any(role=='comparison' and Path(path).is_file() for role,path in image_inventory):
   raise ValueError(f'Comparison mode completed, but no comparison plot images were found in {comparison_plot_folder}.')
 for role,path in image_inventory:
  source=Path(path)
  if not source.is_file():continue
  dest_dir=image_root/role;dest_dir.mkdir(parents=True,exist_ok=True);dest=dest_dir/source.name;shutil.copy2(source,dest);rel=dest.relative_to(package).as_posix()
  copied.append({'dataset_role':role.upper(),'source':str(source),'package_file':rel});slide={'slide_type':'image','title':source.name,'image_file':rel,'dataset_role':role.upper()}
  if role=='comparison':slide['difference_convention']='B_MINUS_A'
  slides.append(slide)
 comparison_slide_count=sum(1 for slide in slides if slide.get('dataset_role')=='COMPARISON')
 dataset_b=None;comp={'completed':False,'difference_convention':'B_MINUS_A'};metrics={'Analysis mode':c['mode'],'Dataset A':c['na'],'Dataset A averaged sweep points':len(avg_a),'Generated image slides':len(slides),'Comparison image slides':comparison_slide_count};highlights=[f"Dataset A: {c['na']}",f'Generated {len(slides)} image slide(s) from this analysis run.',f'Packaged {comparison_slide_count} comparison image slide(s).']
 if c['mode']=='COMPARE':
  dataset_b={'label':c['nb'],'source_folder':c['b'],'workbook':str(wb) if wb else ''};metrics.update({'Dataset B':c['nb'],'Dataset B averaged sweep points':len(avg_b) if avg_b is not None else 0});highlights.append(f"Comparison convention: {c['nb']} minus {c['na']}.");comp={'completed':comparison is not None,'difference_convention':'B_MINUS_A','workbook':str(cw) if cw else ''}
 data={'schema_version':int(os.environ.get('PARAMOUNT_POWERPOINT_SCHEMA_VERSION','1')),'analysis_id':os.environ.get('PARAMOUNT_POWERPOINT_ANALYSIS_ID','power_linearity').strip(),'display_name':'Power Linearity','status':'completed','analysis_mode':c['mode'],'dataset_label':c['na'] if c['mode']=='SINGLE' else f"{c['nb']} vs {c['na']}",'source_folder':c['a'],'dataset_a':{'label':c['na'],'source_folder':c['a'],'workbook':str(wa) if wa else ''},'dataset_b':dataset_b,'comparison':comp,'highlights':highlights,'metrics':metrics,'generated_images':copied,'slides':slides}
 manifest.write_text(json.dumps(data,indent=2),encoding='utf-8');return manifest

def open_file(p):
 open_with_default_app(p)

def gui():
 r=tk.Tk();r.title(APP_TITLE);r.geometry('1000x760');o=ttk.Frame(r,padding=14);o.pack(fill='both',expand=True);ttk.Label(o,text=APP_TITLE,font=('Segoe UI',15,'bold')).pack(anchor='w');n=ttk.Notebook(o);n.pack(fill='both',expand=True,pady=10);tabs={x:ttk.Frame(n,padding=12) for x in ['Input Data','Limits','Plots','Comparison','Output']}
 for x,t in tabs.items():n.add(t,text=x)
 launcher_mode=os.environ.get('PARAMOUNT_ANALYSIS_MODE','INDIVIDUAL').strip().upper();initial_mode=launcher_mode if launcher_mode in {'SINGLE','COMPARE'} else 'SINGLE'
 mode=tk.StringVar(value=initial_mode);pa=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_A_FOLDER','').strip());pb=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_B_FOLDER','').strip());na=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_A_LABEL','').strip() or 'Configuration A');nb=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_B_LABEL','').strip() or 'Configuration B');transition=tk.StringVar(value='300');absolute=tk.StringVar(value='3');high=tk.StringVar(value='1');allplot=tk.BooleanVar(value=True);perplot=tk.BooleanVar(value=True);avgplot=tk.BooleanVar(value=True);openwb=tk.BooleanVar(value=True);preview=tk.StringVar(value='Select a Power Linearity collection folder.')
 t=tabs['Input Data'];t.columnconfigure(1,weight=1);ttk.Radiobutton(t,text='Single dataset',variable=mode,value='SINGLE').grid(row=0,column=0,sticky='w');ttk.Radiobutton(t,text='Compare two hardware configurations',variable=mode,value='COMPARE').grid(row=0,column=1,sticky='w')
 def browse(v,name,show=False):
  current=v.get().strip();x=filedialog.askdirectory(parent=r,mustexist=True,initialdir=current if current and Path(current).is_dir() else None)
  if x:v.set(x);name.set(Path(x).name or name.get());preview.set(f'CSV files: {len(measurement_files(x))}') if show else None
 for row,label,v,name in [(1,'Configuration A folder',pa,na),(3,'Configuration B folder',pb,nb)]:ttk.Label(t,text=label).grid(row=row,column=0,sticky='w');ttk.Entry(t,textvariable=v).grid(row=row,column=1,sticky='ew');ttk.Button(t,text='Browse...',command=lambda v=v,name=name,show=row==1:browse(v,name,show)).grid(row=row,column=2);ttk.Label(t,text='Display name').grid(row=row+1,column=0,sticky='w');ttk.Entry(t,textvariable=name).grid(row=row+1,column=1,sticky='w')
 ttk.Label(t,textvariable=preview).grid(row=5,column=0,columnspan=3,sticky='w',pady=12)
 for row,label,v in [(0,'Low-power transition (W)',transition),(1,'Low-power absolute tolerance (W)',absolute),(2,'High-power tolerance (%)',high)]:ttk.Label(tabs['Limits'],text=label).grid(row=row,column=0,sticky='w');ttk.Entry(tabs['Limits'],textvariable=v).grid(row=row,column=1,sticky='w')
 ttk.Checkbutton(tabs['Plots'],text='Generate combined all-frequency percent-error plot',variable=allplot).pack(anchor='w');ttk.Checkbutton(tabs['Plots'],text='Generate separate plot for each frequency',variable=perplot).pack(anchor='w');ttk.Checkbutton(tabs['Plots'],text='Generate multi-cycle sweep-point average plot with standard-deviation bands',variable=avgplot).pack(anchor='w');ttk.Label(tabs['Plots'],text='Selected raw plot options also generate a separate image for each collection cycle; cycles are not overlaid.',foreground='#555555').pack(anchor='w',pady=(8,0))
 ttk.Label(tabs['Comparison'],text='Matches on frequency and generator-forward-power setpoint; reports B-minus-A delivered-power and percent-error differences with comparison plots.',wraplength=800).pack(anchor='w')
 ttk.Checkbutton(tabs['Output'],text='Open Configuration A workbook when complete',variable=openwb).pack(anchor='w')
 res={'ok':False}
 def start():
  try:
   a=Path(pa.get()).resolve();b=Path(pb.get()).resolve() if mode.get()=='COMPARE' else None
   if not measurement_files(a):raise ValueError('Select Configuration A data.')
   if b and (not measurement_files(b) or a==b):raise ValueError('Select a different Configuration B dataset.')
   vals=[float(transition.get()),float(absolute.get()),float(high.get())]
   res.update(ok=True,mode=mode.get(),a=str(a),b=str(b) if b else None,na=na.get() or a.name,nb=nb.get() or (b.name if b else 'Configuration B'),transition=vals[0],absolute=vals[1],high=vals[2],allplot=allplot.get(),perplot=perplot.get(),avgplot=avgplot.get(),open=openwb.get());r.destroy()
  except Exception as e:messagebox.showerror('Invalid settings',str(e),parent=r)
 btm=ttk.Frame(o);btm.pack(fill='x');ttk.Button(btm,text='Start Analysis',command=start).pack(side='right');ttk.Button(btm,text='Cancel',command=r.destroy).pack(side='right',padx=8);r.mainloop();return res

def process(folder,name,c,progress=None,start=0,end=100):
 if progress:progress.update(start,f'Loading {name}',str(folder))
 raw,fail,settings=load_dataset(folder,name);d=analyze(raw,c['transition'],c['absolute'],c['high']);d,averages,cycles=multi_cycle_averages(d,settings);summ=summary(averages);folder=Path(folder);images=[]
 if progress:progress.update(start+(end-start)*.25,f'Generating {name} plots',f'Collection cycles: {cycles}')
 if c['allplot']:
  p=folder/'Power_Linearity_Percent_Error_All_Frequencies.png';plot_all(d,p,f'Power Linearity Percent Error (All Frequencies) - {name}');images.append(p)
  images.extend(plot_cycles_all(d,folder/'Power Linearity Cycle Plots',name))
 if c['perplot']:
  images.extend(plot_per_frequency(d,folder/'Power Linearity Plots',name));images.extend(plot_cycles_per_frequency(d,folder/'Power Linearity Cycle Plots',name))
 if c['avgplot']:
  p=folder/'Power_Linearity_Sweep_Point_Averages.png';plot_averages(averages,p,f'Power Linearity Multi-Cycle Averages - {name}');images.append(p)
 if progress:progress.update(start+(end-start)*.85,f'Writing {name} workbook',str(folder/WORKBOOK_NAME))
 wb=folder/WORKBOOK_NAME;write_book(wb,d,averages,summ,fail,settings,{'Low-power transition W':c['transition'],'Absolute tolerance W':c['absolute'],'High-power tolerance %':c['high'],'Recorded Collection Cycles':cycles,'Raw Measurement Rows':len(d),'Averaged Sweep Points':len(averages)});progress.update(end,f'{name} complete',str(wb)) if progress else None;return d,averages,summ,wb,images

def main():
 c=gui()
 if not c.get('ok'):return
 progress=AnalysisProgress()
 try:
  end_a=42 if c['mode']=='COMPARE' else 90
  a,aa,sa,wa,ia=process(c['a'],c['na'],c,progress,0,end_a);images=[('dataset_a',p) for p in ia];ab=None;d=None;wb=None;cw=None
  if c['mode']=='COMPARE':
   b,ab,sb,wb,ib=process(c['b'],c['nb'],c,progress,42,84);images.extend(('dataset_b',p) for p in ib);progress.update(87,'Comparing datasets','Calculating B minus A averaged sweep points');d=compare(aa,ab,c['na'],c['nb']);folder=Path(c['a'])/COMPARISON_FOLDER;folder.mkdir(exist_ok=True);cw=folder/COMPARISON_WORKBOOK;progress.update(90,'Writing comparison workbook',str(cw));write_compare(cw,d,sa,sb,{'A':c['a'],'B':c['b'],'Difference':'B-A','Matching':'RF frequency and averaged sweep-point position'});progress.update(93,'Generating comparison plots','Creating averaged comparison plots');images.extend(('comparison',p) for p in comparison_plots(d,c['na'],c['nb'],folder/'Comparison Plots'))
  warning=None
  if powerpoint_export_requested():
   try:progress.update(97,'Packaging PowerPoint assets','Copying individual-cycle, average, and comparison plots');m=export_powerpoint_package(c,images,aa,ab,d,wa,wb,cw);print(f'PowerPoint package manifest saved to: {m}') if m else None
   except Exception as e:warning=f'PowerPoint package export warning: {e}';print(warning,file=sys.stderr)
  progress.update(100,'Analysis complete','All outputs generated')
  if c['open']:open_file(wa)
  progress.close();root=tk.Tk();root.withdraw();messagebox.showinfo('Complete',f'Power Linearity analysis complete.\n\nWorkbook: {wa}'+(f'\n\nComparison: {cw}' if cw else '')+(f'\n\n{warning}\nNormal analysis outputs were still completed.' if warning else ''),parent=root);root.destroy()
 except Exception:progress.close();raise

if __name__=='__main__':main()
