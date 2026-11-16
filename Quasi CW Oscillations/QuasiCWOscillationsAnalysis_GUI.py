"""
PMNT_Power_Regulation_Analysis_GUI.py

Analyzes:
    Delivered_Power_Test.csv
    Power_Regulation_Run_Settings.csv

Outputs:
    Power_Regulation.png
    Delivered_Power_Histogram.png
    Power_Regulation_Analysis.xlsx
"""

from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

WORKBOOK_NAME = 'Power_Regulation_Analysis.xlsx'


def read_settings(folder):
    path = Path(folder) / 'Power_Regulation_Run_Settings.csv'
    df = pd.read_csv(path)
    return dict(zip(df['setting'], df['value']))


def load_data(folder):
    path = Path(folder) / 'Delivered_Power_Test.csv'
    return pd.read_csv(path)


def analyze_dataset(folder):
    settings = read_settings(folder)
    data = load_data(folder)

    power_limit = float(settings['power_limit'])

    upper = power_limit * 1.005
    lower = power_limit * 0.995

    data['OutOfBounds'] = (
        (data['DeliveredPower'] > upper) |
        (data['DeliveredPower'] < lower)
    )

    failures = data[data['OutOfBounds']].copy()

    mean_power = data['DeliveredPower'].mean()
    min_power = data['DeliveredPower'].min()
    max_power = data['DeliveredPower'].max()
    std_power = data['DeliveredPower'].std()
    pkpk = max_power - min_power

    summary = pd.DataFrame([
        ['Serial Number', settings.get('serial_number','')],
        ['Power Limit (W)', power_limit],
        ['Mean Delivered Power (W)', mean_power],
        ['Minimum Delivered Power (W)', min_power],
        ['Maximum Delivered Power (W)', max_power],
        ['Peak-To-Peak Variation (W)', pkpk],
        ['Std Dev (W)', std_power],
        ['Out Of Bounds Samples', int(len(failures))],
        ['Pass/Fail', 'PASS' if len(failures)==0 else 'FAIL']
    ], columns=['Metric','Value'])

    create_plots(folder, data, upper, lower)

    workbook = Path(folder) / WORKBOOK_NAME

    with pd.ExcelWriter(workbook, engine='openpyxl') as writer:
        summary.to_excel(writer, sheet_name='Summary', index=False)
        data.to_excel(writer, sheet_name='Raw Data', index=False)
        failures.to_excel(writer, sheet_name='Failures', index=False)

    return workbook, summary



def create_plots(folder, data, upper, lower):

    t_ms = data['TimeSec'] * 1000

    plt.figure(figsize=(10,5))
    plt.plot(t_ms, data['DeliveredPower'], label='Delivered Power')
    plt.axhline(upper, color='red', linestyle='--', label='Upper Limit')
    plt.axhline(lower, color='blue', linestyle='--', label='Lower Limit')
    plt.xlabel('Time (ms)')
    plt.ylabel('Power (W)')
    plt.title('Delivered Power vs Time')
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(Path(folder)/'Power_Regulation.png', dpi=200)
    plt.close()

    plt.figure(figsize=(8,5))
    plt.hist(data['DeliveredPower'], bins=50)
    plt.xlabel('Delivered Power (W)')
    plt.ylabel('Count')
    plt.title('Delivered Power Histogram')
    plt.tight_layout()
    plt.savefig(Path(folder)/'Delivered_Power_Histogram.png', dpi=200)
    plt.close()


def compare_datasets(folder_a, folder_b):

    sa = pd.read_excel(Path(folder_a)/WORKBOOK_NAME, sheet_name='Summary')
    sb = pd.read_excel(Path(folder_b)/WORKBOOK_NAME, sheet_name='Summary')

    comp = pd.merge(sa, sb, on='Metric', suffixes=('_A','_B'))

    out = Path(folder_a) / 'Power_Regulation_Comparison.xlsx'

    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        comp.to_excel(writer, sheet_name='Comparison', index=False)

    return out


def browse(var):
    d = filedialog.askdirectory(mustexist=True)
    if d:
        var.set(d)


root = tk.Tk()
root.title('Power Regulation Analysis')
root.geometry('750x300')

folder_a = tk.StringVar()
folder_b = tk.StringVar()
compare = tk.BooleanVar(value=False)

frm = ttk.Frame(root, padding=10)
frm.pack(fill='both', expand=True)


ttk.Label(frm,text='Dataset A').grid(row=0,column=0,sticky='w')
ttk.Entry(frm,textvariable=folder_a,width=65).grid(row=0,column=1)
ttk.Button(frm,text='Browse',command=lambda:browse(folder_a)).grid(row=0,column=2)

ttk.Checkbutton(frm,text='Compare Datasets',variable=compare).grid(row=1,column=1,sticky='w')

ttk.Label(frm,text='Dataset B').grid(row=2,column=0,sticky='w')
ttk.Entry(frm,textvariable=folder_b,width=65).grid(row=2,column=1)
ttk.Button(frm,text='Browse',command=lambda:browse(folder_b)).grid(row=2,column=2)


def run():
    try:
        wb,_ = analyze_dataset(folder_a.get())
        msg = f'Analysis Complete\n\nWorkbook:\n{wb}'

        if compare.get() and folder_b.get().strip():
            analyze_dataset(folder_b.get())
            comp = compare_datasets(folder_a.get(), folder_b.get())
            msg += f'\n\nComparison Workbook:\n{comp}'

        messagebox.showinfo('Complete', msg)

    except Exception as e:
        messagebox.showerror('Error', str(e))


ttk.Button(frm,text='Analyze',command=run).grid(row=3,column=1,pady=20)

root.mainloop()
