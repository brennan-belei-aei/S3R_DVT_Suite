#!/usr/bin/env python3
"""Analysis GUI for AEBus transaction, RF ON/OFF, and IF Tools latency data."""
from __future__ import annotations
import json, os, re, shutil, sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ANALYSIS_WORKBOOK="AEBus_Command_Latency_Analysis.xlsx"
COMPARISON_FOLDER="Hardware Configuration Comparison"
COMPARISON_WORKBOOK="AEBus_Command_Latency_Hardware_Comparison.xlsx"
PLOT_FOLDER="AEBus Command Latency Analysis Plots"
MAX_SAMPLES_FOR_STATS=1000

# Preserved from statistics-new.py, including its existing event pairing and units.
def parse_if_tools_raw(in_file,out_file,max_samples=None):
    df=pd.read_csv(in_file)
    required={'Input','Data','dt last(us)'}
    missing=required-set(df.columns)
    if missing:raise ValueError(f"IF Tools CSV is missing columns: {sorted(missing)}")
    transitions=pd.DataFrame(columns=['row','command_raw','subcommand_raw','lat (ms)'])
    prior=df['Input'].shift(1);b_to_a=df.index[(prior=='B')&(df['Input']=='A')].tolist();a_to_b=df.index[(prior=='A')&(df['Input']=='B')].tolist()
    rows=[]
    for start in b_to_a:
        latency_index=next((idx for idx in a_to_b if idx>start),None)
        if latency_index is None:continue
        command=df.loc[start+2,'Data'] if start+2 in df.index else np.nan
        subcommand=df.loc[start+3,'Data'] if start+3 in df.index else np.nan
        rows.append({'row':start,'command_raw':command,'subcommand_raw':subcommand,'lat (ms)':df.loc[latency_index,'dt last(us)']*1000.0})
    transitions=pd.DataFrame(rows)
    def fmt(row):
        cmd=row['command_raw'];sub=row['subcommand_raw']
        if pd.isna(cmd):return np.nan
        cmd_int=int(cmd)
        return f"{cmd_int}-{int(sub)}" if cmd_int in [71,118,204,248] and pd.notna(sub) else str(cmd_int)
    if transitions.empty:output=pd.DataFrame(columns=['command','lat (ms)'])
    else:
        transitions['command']=transitions.apply(fmt,axis=1);output=transitions[['command','lat (ms)']].dropna()
        if max_samples is not None:output=output.groupby('command',group_keys=False).head(max_samples)
    output.to_csv(out_file,index=False)
    return output, analyze_if_tools_latency(output)

def analyze_if_tools_latency(data):
    df=pd.read_csv(data) if isinstance(data,(str,Path)) else data.copy()
    if df.empty:return pd.DataFrame(columns=['command','best_case_ms','worst_case_ms','average_ms','std_dev_ms','samples'])
    stats=df.groupby('command')['lat (ms)'].agg([('min','min'),('max','max'),('mean','mean'),('std','std'),('count','count')]).reset_index()
    stats.columns=['command','best_case_ms','worst_case_ms','average_ms','std_dev_ms','samples']
    def key(value):
        if isinstance(value,str) and '-' in value:
            base,sub=value.split('-',1);return int(base),int(sub)
        return int(value),0
    return stats.sort_values('command',key=lambda x:x.map(key)).reset_index(drop=True).round(6)

def find_csv(folder,predicate):
    return [p for p in Path(folder).rglob('*.csv') if predicate(p)]

def command_processing(folder):
    files=[]
    for p in Path(folder).rglob('*.csv'):
        try:
            columns=set(pd.read_csv(p,nrows=0).columns)
            if 'command_processing_latency' in p.name.lower() or {'CommandName','Latency_ms'}.issubset(columns):files.append(p)
        except Exception:pass
    if not files:return None,None
    raw=pd.concat([pd.read_csv(p) for p in files],ignore_index=True)
    summary=raw.groupby(['CommandName','Command'],dropna=False)['Latency_ms'].agg(Average_ms='mean',Minimum_ms='min',Maximum_ms='max',Std_Dev_ms='std',Sample_Rows='count').reset_index()
    return raw,summary

def rf_scope(folder):
    files=[]
    for p in Path(folder).rglob('*.csv'):
        if p.name.endswith('_details.csv'):continue
        try:
            cols=set(pd.read_csv(p,nrows=0).columns)
            if {'setpoint_W','command','iteration','delta_t_ms'}.issubset(cols):files.append(p)
        except Exception:pass
    if not files:return None,None
    raw=pd.concat([pd.read_csv(p) for p in files],ignore_index=True)
    summary=raw.groupby(['setpoint_W','command'],dropna=False)['delta_t_ms'].agg(Average_ms='mean',Minimum_ms='min',Maximum_ms='max',Std_Dev_ms='std',Samples='count').reset_index()
    return raw,summary

def if_tools(folder,out_folder):
    candidates=[]
    for p in (Path(folder)/'if_tools').glob('*.csv') if (Path(folder)/'if_tools').is_dir() else Path(folder).rglob('*.csv'):
        try:
            if {'Input','Data','dt last(us)'}.issubset(pd.read_csv(p,nrows=0).columns):candidates.append(p)
        except Exception:pass
    if not candidates:return None,None
    out_folder.mkdir(parents=True,exist_ok=True)
    return parse_if_tools_raw(candidates[0],out_folder/'IF_Tools_Intermediate.csv',MAX_SAMPLES_FOR_STATS)

def plots(name,summaries,folder):
    folder.mkdir(parents=True,exist_ok=True);images=[]
    command= summaries.get('command')
    if command is not None and not command.empty:
        fig,ax=plt.subplots(figsize=(11,6));x=np.arange(len(command));ax.bar(x,command['Average_ms']);ax.set_xticks(x,command['CommandName'].astype(str),rotation=75,ha='right');ax.set_ylabel('Average latency (ms)');ax.set_title(f'{name} - AEBus Transaction Latency');ax.grid(axis='y',alpha=.3);fig.tight_layout();p=folder/'Command_Processing_Latency.png';fig.savefig(p,dpi=180,bbox_inches='tight');plt.close(fig);images.append(p)
    rf=summaries.get('rf')
    if rf is not None and not rf.empty:
        fig,ax=plt.subplots(figsize=(10,6))
        for cmd,g in rf.groupby('command'):ax.plot(g['setpoint_W'],g['Average_ms'],marker='o',label=cmd)
        ax.set(xlabel='Power setpoint (W)',ylabel='Average physical response latency (ms)',title=f'{name} - RF ON/OFF Latency');ax.grid(alpha=.3);ax.legend();fig.tight_layout();p=folder/'RF_ON_OFF_Latency.png';fig.savefig(p,dpi=180,bbox_inches='tight');plt.close(fig);images.append(p)
    stats=summaries.get('if')
    if stats is not None and not stats.empty:
        fig,ax=plt.subplots(figsize=(11,6));x=np.arange(len(stats));ax.bar(x,stats['average_ms']);ax.set_xticks(x,stats['command'].astype(str),rotation=75,ha='right');ax.set_ylabel('Average latency (ms)');ax.set_title(f'{name} - IF Tools Statistics');ax.grid(axis='y',alpha=.3);fig.tight_layout();p=folder/'IF_Tools_Command_Statistics.png';fig.savefig(p,dpi=180,bbox_inches='tight');plt.close(fig);images.append(p)
    return images

def process(folder,name,selected):
    folder=Path(folder);out={};summaries={};images=[]
    if selected['command']:
        raw,summary=command_processing(folder);out['Command Processing Raw']=raw;summaries['command']=summary
    if selected['rf']:
        raw,summary=rf_scope(folder);out['RF ON-OFF Raw']=raw;summaries['rf']=summary
    if selected['if']:
        raw,summary=if_tools(folder,folder);out['IF Tools Parsed']=raw;summaries['if']=summary
    if not any(v is not None for v in summaries.values()):raise ValueError(f'No selected AEBus latency result files found in {folder}')
    workbook=folder/ANALYSIS_WORKBOOK
    with pd.ExcelWriter(workbook,engine='openpyxl') as writer:
        for sheet,frame in out.items():
            if frame is not None:frame.to_excel(writer,sheet_name=sheet[:31],index=False)
        for key,frame in summaries.items():
            if frame is not None:frame.to_excel(writer,sheet_name={'command':'Command Processing Summary','rf':'RF ON-OFF Summary','if':'IF Tools Statistics'}[key][:31],index=False)
        pd.DataFrame([('Dataset',name),('Source Folder',str(folder)),('IF Tools unit treatment','Preserved from statistics-new.py')],columns=['Setting','Value']).to_excel(writer,sheet_name='Analysis Settings',index=False)
    images=plots(name,summaries,folder/PLOT_FOLDER)
    return {'folder':folder,'name':name,'workbook':workbook,'summaries':summaries,'images':images}

def comparisons(a,b):
    results={}
    specs={'command':(['CommandName','Command'],'Average_ms'),'rf':(['setpoint_W','command'],'Average_ms'),'if':(['command'],'average_ms')}
    for key,(keys,value) in specs.items():
        x=a['summaries'].get(key);y=b['summaries'].get(key)
        if x is None or y is None:continue
        merged=x.merge(y,on=keys,how='outer',suffixes=('_A','_B'),indicator=True);merged['Difference B-A']=merged[f'{value}_B']-merged[f'{value}_A'];merged['Absolute Difference']=merged['Difference B-A'].abs();results[key]=merged
    return results

def comparison_plots(results,folder):
    folder.mkdir(parents=True,exist_ok=True);images=[]
    for key,frame in results.items():
        matched=frame[frame['_merge']=='both']
        if matched.empty:continue
        fig,ax=plt.subplots(figsize=(11,6));x=np.arange(len(matched));ax.bar(x,matched['Difference B-A']);labels=matched.iloc[:,:1].astype(str).iloc[:,0];ax.set_xticks(x,labels,rotation=75,ha='right');ax.axhline(0,color='black',lw=.8);ax.set_ylabel('B - A (ms)');ax.set_title(f'{key.title()} Comparison');fig.tight_layout();p=folder/f'{key}_Comparison_B_minus_A.png';fig.savefig(p,dpi=180,bbox_inches='tight');plt.close(fig);images.append(p)
    return images

def ppt_export(config,images,a,b,results,comparison_workbook):
    if os.environ.get('PARAMOUNT_POWERPOINT_EXPORT_ENABLED','')!='1':return None
    package=Path(os.environ['PARAMOUNT_POWERPOINT_PACKAGE_FOLDER']);package.mkdir(parents=True,exist_ok=True);manifest=package/'manifest.json'
    if manifest.exists():manifest.unlink()
    ir=package/'images';tr=package/'tables'
    if ir.exists():shutil.rmtree(ir)
    if tr.exists():shutil.rmtree(tr)
    tr.mkdir(parents=True)
    for role,data in [('dataset_a',a),('dataset_b',b)]:
        if not data:continue
        for key,frame in data['summaries'].items():
            if frame is not None:frame.to_csv(tr/f'{role}_{key}_summary.csv',index=False)
    for key,frame in results.items():frame.to_csv(tr/f'{key}_comparison_b_minus_a.csv',index=False)
    slides=[]
    for role,path in images:
        destdir=ir/role;destdir.mkdir(parents=True,exist_ok=True);dest=destdir/Path(path).name;shutil.copy2(path,dest);rel=dest.relative_to(package).as_posix();slide={'slide_type':'image','title':Path(path).name,'image_file':rel,'dataset_role':role.upper()}
        if role=='comparison':slide['difference_convention']='B_MINUS_A'
        slides.append(slide)
    payload={'schema_version':int(os.environ.get('PARAMOUNT_POWERPOINT_SCHEMA_VERSION','1')),'analysis_id':os.environ.get('PARAMOUNT_POWERPOINT_ANALYSIS_ID','aebus_command_latency'),'display_name':'AEBus Command Latency','status':'completed','analysis_mode':config['mode'],'source_folder':config['a'],'dataset_a':{'label':config['na'],'source_folder':config['a'],'workbook':str(a['workbook'])},'dataset_b':({'label':config['nb'],'source_folder':config['b'],'workbook':str(b['workbook'])} if b else None),'comparison':{'completed':bool(b),'difference_convention':'B_MINUS_A','workbook':str(comparison_workbook) if comparison_workbook else ''},'slides':slides,'metrics':{'Generated image slides':len(slides)}}
    manifest.write_text(json.dumps(payload,indent=2),encoding='utf-8');return manifest

def gui():
    root=tk.Tk();root.title('AEBus Command Latency Analysis');root.geometry('900x640');outer=ttk.Frame(root,padding=14);outer.pack(fill='both',expand=True);nb=ttk.Notebook(outer);nb.pack(fill='both',expand=True)
    data=ttk.Frame(nb,padding=12);sources=ttk.Frame(nb,padding=12);outputs=ttk.Frame(nb,padding=12);nb.add(data,text='Datasets');nb.add(sources,text='Analysis Sources');nb.add(outputs,text='Outputs')
    launcher=os.environ.get('PARAMOUNT_ANALYSIS_MODE','INDIVIDUAL').upper();mode=tk.StringVar(value=launcher if launcher in {'SINGLE','COMPARE'} else 'SINGLE');a=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_A_FOLDER',''));b=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_B_FOLDER',''));na=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_A_LABEL','') or 'Configuration A');nbv=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_B_LABEL','') or 'Configuration B')
    ttk.Radiobutton(data,text='Single dataset',variable=mode,value='SINGLE').grid(row=0,column=0,sticky='w');ttk.Radiobutton(data,text='Compare two datasets',variable=mode,value='COMPARE').grid(row=0,column=1,sticky='w')
    def browse(v):
        x=filedialog.askdirectory(parent=root,mustexist=True,initialdir=v.get() or None)
        if x:v.set(x)
    entries=[]
    for row,(label,var) in enumerate([('Dataset A folder',a),('Dataset A label',na),('Dataset B folder',b),('Dataset B label',nbv)],1):
        ttk.Label(data,text=label).grid(row=row,column=0,sticky='w',pady=6);e=ttk.Entry(data,textvariable=var);e.grid(row=row,column=1,sticky='ew');entries.append(e)
        if 'folder' in label:ttk.Button(data,text='Browse...',command=lambda v=var:browse(v)).grid(row=row,column=2)
    data.columnconfigure(1,weight=1)
    cv=tk.BooleanVar(value=True);rv=tk.BooleanVar(value=True);iv=tk.BooleanVar(value=True)
    ttk.Checkbutton(sources,text='Command-processing latency',variable=cv).pack(anchor='w',pady=6);ttk.Checkbutton(sources,text='RF ON/OFF physical-response latency',variable=rv).pack(anchor='w',pady=6);ttk.Checkbutton(sources,text='IF Tools statistics',variable=iv).pack(anchor='w',pady=6)
    openv=tk.BooleanVar(value=True);ttk.Checkbutton(outputs,text='Open Dataset A workbook after analysis',variable=openv).pack(anchor='w')
    result={}
    def start():
        try:
            pa=Path(a.get()).resolve();pb=Path(b.get()).resolve() if mode.get()=='COMPARE' else None
            if not pa.is_dir() or (pb and not pb.is_dir()):raise ValueError('Select valid dataset folders.')
            if pb==pa:raise ValueError('Dataset A and B must differ.')
            if not any((cv.get(),rv.get(),iv.get())):raise ValueError('Select at least one analysis source.')
            result.update(mode=mode.get(),a=str(pa),b=str(pb) if pb else '',na=na.get() or 'Configuration A',nb=nbv.get() or 'Configuration B',selected={'command':cv.get(),'rf':rv.get(),'if':iv.get()},open=openv.get());root.destroy()
        except Exception as error:messagebox.showerror('Invalid settings',str(error),parent=root)
    buttons=ttk.Frame(outer);buttons.pack(fill='x',pady=(12,0));ttk.Button(buttons,text='Cancel',command=root.destroy).pack(side='right',padx=8);ttk.Button(buttons,text='Start Analysis',command=start).pack(side='right');root.mainloop()
    if not result:raise SystemExit(0)
    return result

def main():
    c=gui();a=process(c['a'],c['na'],c['selected']);images=[('dataset_a',p) for p in a['images']];b=None;results={};cw=None
    if c['mode']=='COMPARE':
        b=process(c['b'],c['nb'],c['selected']);images.extend(('dataset_b',p) for p in b['images']);results=comparisons(a,b);folder=Path(c['a'])/COMPARISON_FOLDER;folder.mkdir(exist_ok=True);cw=folder/COMPARISON_WORKBOOK
        with pd.ExcelWriter(cw,engine='openpyxl') as writer:
            for key,frame in results.items():frame.to_excel(writer,sheet_name=f'{key} B minus A'[:31],index=False)
            pd.DataFrame([('Difference convention','Dataset B minus Dataset A')],columns=['Setting','Value']).to_excel(writer,sheet_name='Settings',index=False)
        images.extend(('comparison',p) for p in comparison_plots(results,folder/'Comparison Plots'))
    warning=None
    try:ppt_export(c,images,a,b,results,cw)
    except Exception as error:warning=str(error)
    if c['open']:
        try:os.startfile(a['workbook'])
        except Exception:pass
    root=tk.Tk();root.withdraw();message=f"Dataset A workbook:\n{a['workbook']}"+(f"\n\nComparison workbook:\n{cw}" if cw else '')+(f"\n\nPowerPoint package warning: {warning}" if warning else '');messagebox.showinfo('Analysis complete',message,parent=root);root.destroy()
if __name__=='__main__':main()
