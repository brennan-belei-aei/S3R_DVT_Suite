import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

APP_TITLE = "Rise/Fall Time Analysis"
SETTINGS_FILE = "Rise_Fall_Run_Settings.csv"
WORKBOOK_NAME = "Rise_Fall_Analysis.xlsx"


def read_settings(folder):
    p = Path(folder)/SETTINGS_FILE
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    return dict(zip(df['setting'], df['value']))


def moving_average(signal, samples):
    samples = max(1, int(samples))
    k = np.ones(samples)/samples
    return np.convolve(signal, k, mode='same')


def sustained_crossing(signal, threshold, direction, sustain_samples):
    cond = signal >= threshold if direction=='rising' else signal <= threshold
    count = 0
    for i,v in enumerate(cond):
        if v:
            count += 1
            if count >= sustain_samples:
                return i-sustain_samples+1
        else:
            count = 0
    return None


def analyze_transition(df,start_power,end_power,filter_ms,sustain_ms):
    t = pd.to_numeric(df['TimeSec']).to_numpy()
    p = pd.to_numeric(df['DeliveredPower']).to_numpy()
    sample_period = np.mean(np.diff(t))
    filt_samples = max(5,int((filter_ms/1000)/sample_period))
    sustain_samples = max(5,int((sustain_ms/1000)/sample_period))
    f = moving_average(p,filt_samples)
    span = end_power-start_power
    level10 = start_power + 0.10*span
    level90 = start_power + 0.90*span
    if end_power > start_power:
        idx10 = sustained_crossing(f,level10,'rising',sustain_samples)
        idx90 = sustained_crossing(f,level90,'rising',sustain_samples)
        mode='Rise'
    else:
        idx90 = sustained_crossing(f,level90,'falling',sustain_samples)
        idx10 = sustained_crossing(f,level10,'falling',sustain_samples)
        mode='Fall'
    if idx10 is None or idx90 is None:
        raise RuntimeError(f'Could not locate {mode} thresholds')
    return {
        'mode':mode,'time':abs(t[idx90]-t[idx10])*1000,'t':t,'raw':p,
        'filtered':f,'level10':level10,'level90':level90,
        't10':t[idx10],'t90':t[idx90]
    }


def save_plot(result,outfile,title):
    plt.figure(figsize=(10,5))
    plt.plot(result['t']*1000,result['raw'],alpha=.4,label='Raw')
    plt.plot(result['t']*1000,result['filtered'],label='Filtered')
    plt.axhline(result['level10'],ls='--',color='orange')
    plt.axhline(result['level90'],ls='--',color='red')
    plt.axvline(result['t10']*1000,color='orange')
    plt.axvline(result['t90']*1000,color='red')
    plt.title(title)
    plt.xlabel('Time (ms)')
    plt.ylabel('Power (W)')
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(outfile,dpi=200)
    plt.close()


def analyze_dataset(folder,filter_ms,sustain_ms):
    folder = Path(folder)
    settings = read_settings(folder)
    power_limit = float(settings['power_limit'])
    initial_power = float(settings['initial_power'])

    rise = pd.read_csv(folder/'Rise_Test.csv')
    fall = pd.read_csv(folder/'Fall_Test.csv')

    rise_result = analyze_transition(rise,initial_power,power_limit,filter_ms,sustain_ms)
    fall_result = analyze_transition(fall,power_limit,initial_power,filter_ms,sustain_ms)

    save_plot(rise_result,folder/'Rise_Time.png',f"Rise Time = {rise_result['time']:.3f} ms")
    save_plot(fall_result,folder/'Fall_Time.png',f"Fall Time = {fall_result['time']:.3f} ms")

    summary = pd.DataFrame([
        ['Serial Number',settings.get('serial_number','')],
        ['Power Limit',power_limit],
        ['Rise Time ms',rise_result['time']],
        ['Fall Time ms',fall_result['time']]
    ],columns=['Metric','Value'])

    workbook = folder/WORKBOOK_NAME
    with pd.ExcelWriter(workbook,engine='openpyxl') as w:
        summary.to_excel(w,sheet_name='Summary',index=False)
        rise.to_excel(w,sheet_name='Rise Raw Data',index=False)
        fall.to_excel(w,sheet_name='Fall Raw Data',index=False)

    return workbook,rise_result,fall_result


def compare_datasets(folder_a,folder_b):
    sa = pd.read_excel(Path(folder_a)/WORKBOOK_NAME,sheet_name='Summary')
    sb = pd.read_excel(Path(folder_b)/WORKBOOK_NAME,sheet_name='Summary')
    return sa,sb


def browse(var):
    p = filedialog.askdirectory(mustexist=True)
    if p:
        var.set(p)


def run_gui():
    root=tk.Tk()
    root.title(APP_TITLE)
    root.geometry('700x350')

    folder_a=tk.StringVar()
    folder_b=tk.StringVar()
    compare=tk.BooleanVar(value=False)
    filter_ms=tk.StringVar(value='1.0')
    sustain_ms=tk.StringVar(value='0.5')

    frm=ttk.Frame(root,padding=10)
    frm.pack(fill='both',expand=True)

    ttk.Checkbutton(frm,text='Compare Two Datasets',variable=compare).grid(row=0,column=0,columnspan=3,sticky='w')
    ttk.Label(frm,text='Dataset A').grid(row=1,column=0,sticky='w')
    ttk.Entry(frm,textvariable=folder_a,width=60).grid(row=1,column=1)
    ttk.Button(frm,text='Browse',command=lambda:browse(folder_a)).grid(row=1,column=2)

    ttk.Label(frm,text='Dataset B').grid(row=2,column=0,sticky='w')
    ttk.Entry(frm,textvariable=folder_b,width=60).grid(row=2,column=1)
    ttk.Button(frm,text='Browse',command=lambda:browse(folder_b)).grid(row=2,column=2)

    ttk.Label(frm,text='Filter Window (ms)').grid(row=3,column=0,sticky='w')
    ttk.Entry(frm,textvariable=filter_ms).grid(row=3,column=1,sticky='w')

    ttk.Label(frm,text='Sustain Time (ms)').grid(row=4,column=0,sticky='w')
    ttk.Entry(frm,textvariable=sustain_ms).grid(row=4,column=1,sticky='w')

    def start():
        try:
            wb, rise, fall = analyze_dataset(folder_a.get(),float(filter_ms.get()),float(sustain_ms.get()))
            msg=f'Workbook:\n{wb}\n\nRise Time = {rise["time"]:.3f} ms\nFall Time = {fall["time"]:.3f} ms'
            if compare.get() and folder_b.get().strip():
                analyze_dataset(folder_b.get(),float(filter_ms.get()),float(sustain_ms.get()))
                msg += '\n\nDataset B analyzed.'
            messagebox.showinfo('Complete',msg)
        except Exception as e:
            messagebox.showerror('Error',str(e))

    ttk.Button(frm,text='Analyze',command=start).grid(row=5,column=1,pady=20)
    root.mainloop()


if __name__ == '__main__':
    run_gui()
