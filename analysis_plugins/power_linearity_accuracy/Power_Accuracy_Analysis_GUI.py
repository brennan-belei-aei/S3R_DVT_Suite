"""Standalone Power Accuracy analysis and hardware-comparison GUI.

Consumes measurement CSVs and optional Power_Accuracy_Run_Settings.csv files created by
PMNT_Power_Accuracy_Collection_GUI.py. This file contains no instrument dependencies.
"""

import json
import os
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.powerpoint import powerpoint_export_requested  # noqa: E402
from suite_common.reports import open_with_default_app, size_workbook_columns  # noqa: E402
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


APP_TITLE = "Paramount Power Accuracy Data Analysis"
SETTINGS_FILE_NAME = "Power_Accuracy_Run_Settings.csv"
ANALYSIS_WORKBOOK_NAME = "Power_Accuracy_Analysis.xlsx"
ANALYSIS_PLOT_NAME = "Power_Accuracy.png"
AVERAGE_PLOT_NAME = "Power_Accuracy_Frequency_Averages.png"
AVERAGE_ERROR_PLOT_NAME = "Power_Accuracy_Frequency_Average_Percent_Error.png"
CYCLE_PLOT_NAME = "Power_Accuracy_Individual_Cycles.png"
CYCLE_ERROR_PLOT_NAME = "Power_Accuracy_Individual_Cycles_Percent_Error.png"
COMPARISON_FOLDER_NAME = "Hardware Configuration Comparison"
COMPARISON_WORKBOOK_NAME = "Power_Accuracy_Hardware_Comparison.xlsx"
REQUIRED_COLUMNS = [
    'Test Frequency', 'pFWDGen', 'pREFGen', 'pDELGen',
    'pFWDLynx', 'pREFLynx', 'pDELLynx', 'rLynx', 'iLynx',
    'volLynx', 'curLynx'
]


class AnalysisProgress:
    def __init__(self):
        self.root=tk.Tk();self.root.title("Power Accuracy Analysis Progress");self.root.geometry("720x235");self.root.resizable(True,False);self.root.protocol("WM_DELETE_WINDOW",lambda:None)
        f=ttk.Frame(self.root,padding=16);f.pack(fill="both",expand=True);self.h=tk.StringVar(value="Preparing analysis");self.d=tk.StringVar(value="");self.p=tk.StringVar(value="0%")
        ttk.Label(f,textvariable=self.h,font=("Segoe UI",12,"bold")).pack(anchor="w");ttk.Label(f,textvariable=self.d,wraplength=675).pack(anchor="w",pady=(8,12));self.bar=ttk.Progressbar(f,maximum=100,mode="determinate");self.bar.pack(fill="x");ttk.Label(f,textvariable=self.p).pack(anchor="e");self.root.update()
    def update(self,v,h=None,d=None):
        v=max(0,min(100,float(v)));self.bar["value"]=v;self.p.set(f"{v:.0f}%");self.h.set(h or self.h.get());self.d.set(d or self.d.get());self.root.update_idletasks();self.root.update()
    def close(self):
        try:self.root.destroy()
        except tk.TclError:pass

def read_settings(folder):
    path = Path(folder) / SETTINGS_FILE_NAME
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str)
    if not {'setting', 'value'}.issubset(frame.columns):
        return {}
    return dict(zip(frame['setting'], frame['value']))


def find_measurement_csvs(folder):
    folder = Path(folder)
    return sorted(
        path for path in folder.glob('*.csv')
        if path.name != SETTINGS_FILE_NAME
    )


def load_dataset(folder, dataset_name, remove_duplicate_rows=True):
    files = find_measurement_csvs(folder)
    if not files:
        raise ValueError(f'{dataset_name} contains no measurement CSV files.')
    frames, failures = [], []
    for path in files:
        try:
            frame = pd.read_csv(path)
            missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
            if missing:
                raise ValueError('Missing required columns: ' + ', '.join(missing))
            frame.insert(0, 'Dataset', dataset_name)
            frame.insert(1, 'Source CSV', path.name)
            frames.append(frame)
        except Exception as error:
            failures.append({'Dataset': dataset_name, 'CSV Path': str(path), 'Error': str(error)})
    if not frames:
        raise ValueError(f'No readable Power Accuracy CSV was found for {dataset_name}.')
    data = pd.concat(frames, ignore_index=True, sort=False)
    for column in REQUIRED_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors='coerce')
    data = data.dropna(subset=['Test Frequency', 'pFWDGen', 'pFWDLynx']).copy()
    if remove_duplicate_rows:
        data['Duplicate Row Removed'] = data.duplicated(subset=REQUIRED_COLUMNS, keep='first')
        data = data[~data['Duplicate Row Removed']].copy()
    else:
        data['Duplicate Row Removed'] = False
    return data.reset_index(drop=True), pd.DataFrame(failures), read_settings(folder)


def setting_float(settings, key, default):
    try:
        return float(settings.get(key, default))
    except (ValueError, TypeError):
        return float(default)


def prepare_analysis(data, settings, nominal_power_override=None, spec_fraction=0.01, test_fraction=0.005):
    result = data.copy()
    nominal_power = (
        float(nominal_power_override)
        if nominal_power_override is not None
        else setting_float(settings, 'nominal_plot_power_W', np.nan)
    )
    if not np.isfinite(nominal_power):
        nominal_power = float(pd.to_numeric(result['pFWDLynx'], errors='coerce').median())
    result['Frequency (kHz)'] = pd.to_numeric(result['Test Frequency'], errors='coerce')
    result['Generator Forward Error (W)'] = result['pFWDGen'] - nominal_power
    result['Generator Forward Error (%)'] = result['Generator Forward Error (W)'] / nominal_power * 100.0
    result['Lynx Forward Error (W)'] = result['pFWDLynx'] - nominal_power
    result['Lynx Forward Error (%)'] = result['Lynx Forward Error (W)'] / nominal_power * 100.0
    result['Generator-Lynx Difference (W)'] = result['pFWDGen'] - result['pFWDLynx']
    result['Generator-Lynx Difference (%)'] = np.where(
        result['pFWDLynx'] != 0,
        result['Generator-Lynx Difference (W)'] / result['pFWDLynx'] * 100.0,
        np.nan,
    )
    result['Upper Spec Limit (W)'] = nominal_power * (1 + spec_fraction)
    result['Lower Spec Limit (W)'] = nominal_power * (1 - spec_fraction)
    result['Upper Test Limit (W)'] = nominal_power * (1 + test_fraction)
    result['Lower Test Limit (W)'] = nominal_power * (1 - test_fraction)
    result['Generator Within Spec'] = result['pFWDGen'].between(
        result['Lower Spec Limit (W)'], result['Upper Spec Limit (W)']
    )
    result['Lynx Within Spec'] = result['pFWDLynx'].between(
        result['Lower Spec Limit (W)'], result['Upper Spec Limit (W)']
    )
    result['Generator Within Test Limit'] = result['pFWDGen'].between(
        result['Lower Test Limit (W)'], result['Upper Test Limit (W)']
    )
    result['Lynx Within Test Limit'] = result['pFWDLynx'].between(
        result['Lower Test Limit (W)'], result['Upper Test Limit (W)']
    )
    return result, nominal_power


def create_frequency_averages(data):
    """Summarize repeated collection cycles at each test frequency."""
    numeric_columns = [
        column for column in data.select_dtypes(include=[np.number]).columns
        if column != 'Frequency (kHz)'
    ]
    grouped = data.groupby('Frequency (kHz)', dropna=False, sort=True)
    averages = grouped[numeric_columns].mean().reset_index()
    averages.insert(1, 'Sample Count', grouped.size().to_numpy())
    for metric in ['pFWDGen', 'pFWDLynx', 'Generator Forward Error (%)',
                   'Lynx Forward Error (%)', 'Generator-Lynx Difference (%)']:
        if metric not in numeric_columns:
            continue
        statistics = grouped[metric].agg(['std', 'min', 'max']).reset_index(drop=True)
        averages[f'{metric} Std Dev'] = statistics['std'].fillna(0.0).to_numpy()
        averages[f'{metric} Minimum'] = statistics['min'].to_numpy()
        averages[f'{metric} Maximum'] = statistics['max'].to_numpy()
    return averages


def add_cycle_numbers(data,settings):
    result=data.copy()
    try:recorded=max(1,int(float(settings.get("number_of_collection_cycles",1))))
    except (TypeError,ValueError):recorded=1
    result["Cycle Number"]=result.groupby("Frequency (kHz)",sort=False).cumcount()+1
    detected=int(result["Cycle Number"].max()) if not result.empty else 0
    return result,recorded,detected

def create_cycle_power_plots(data,nominal,output_folder,name):
    output_folder=Path(output_folder);output_folder.mkdir(parents=True,exist_ok=True);paths=[]
    for cycle,g in data.groupby("Cycle Number",sort=True):
        cycle_number=int(cycle);g=g.sort_values("Frequency (kHz)");fig,ax=plt.subplots(figsize=(10,6))
        ax.plot(g["Frequency (kHz)"],g["pFWDGen"],marker="o",label="Generator Forward Power")
        ax.plot(g["Frequency (kHz)"],g["pFWDLynx"],marker="s",linestyle="--",label="Lynx Forward Power")
        for value,color,style,label in [(nominal*1.01,"red","dashed","Spec Limits"),(nominal*.99,"red","dashed",None),(nominal*1.005,"blue","dashdot","Test Limits"),(nominal*.995,"blue","dashdot",None)]:ax.axhline(value,color=color,linestyle=style,label=label)
        ax.set(title=f"{name} - Collection Cycle {cycle_number}",xlabel="Frequency [kHz]",ylabel="Reported Forward Power [W]");ax.grid(True,alpha=.3);ax.legend();fig.tight_layout()
        path=output_folder/f"Power_Accuracy_Cycle_{cycle_number:02d}.png";fig.savefig(path,dpi=250,bbox_inches="tight");plt.close(fig);paths.append(path)
    return paths

def create_cycle_error_plots(data,output_folder,name):
    output_folder=Path(output_folder);output_folder.mkdir(parents=True,exist_ok=True);paths=[]
    for cycle,g in data.groupby("Cycle Number",sort=True):
        cycle_number=int(cycle);g=g.sort_values("Frequency (kHz)");fig,ax=plt.subplots(figsize=(10,6))
        ax.plot(g["Frequency (kHz)"],g["Generator Forward Error (%)"],marker="o",label="Generator Forward Error")
        ax.plot(g["Frequency (kHz)"],g["Lynx Forward Error (%)"],marker="s",linestyle="--",label="Lynx Forward Error")
        for value,color,style,label in [(1,"red","dashed","Spec Limits"),(-1,"red","dashed",None),(.5,"blue","dashdot","Test Limits"),(-.5,"blue","dashdot",None)]:ax.axhline(value,color=color,linestyle=style,label=label)
        ax.set(title=f"{name} - Collection Cycle {cycle_number} Forward Power Error",xlabel="Frequency [kHz]",ylabel="Error vs Nominal Power (%)");ax.grid(True,alpha=.3);ax.legend();fig.tight_layout()
        path=output_folder/f"Power_Accuracy_Cycle_{cycle_number:02d}_Percent_Error.png";fig.savefig(path,dpi=250,bbox_inches="tight");plt.close(fig);paths.append(path)
    return paths

def create_average_plot(averages, nominal_power, output_path, dataset_name):
    data = averages.sort_values('Frequency (kHz)')
    figure, axis = plt.subplots(figsize=(10, 6))
    x = data['Frequency (kHz)']
    axis.plot(x, data['pFWDGen'], color='green', marker='o', label='Generator Average')
    axis.plot(x, data['pFWDLynx'], color='black', marker='s', label='Lynx Average')
    if 'pFWDGen Std Dev' in data:
        axis.fill_between(x, data['pFWDGen'] - data['pFWDGen Std Dev'],
                         data['pFWDGen'] + data['pFWDGen Std Dev'],
                         color='green', alpha=0.12, label='Generator ±1 Std Dev')
    if 'pFWDLynx Std Dev' in data:
        axis.fill_between(x, data['pFWDLynx'] - data['pFWDLynx Std Dev'],
                         data['pFWDLynx'] + data['pFWDLynx Std Dev'],
                         color='black', alpha=0.10, label='Lynx ±1 Std Dev')
    axis.axhline(nominal_power * 1.01, color='red', linestyle='dashed', label='Spec Limits')
    axis.axhline(nominal_power * 1.005, color='blue', linestyle='dashdot', label='Test Limits')
    axis.axhline(nominal_power * 0.99, color='red', linestyle='dashed')
    axis.axhline(nominal_power * 0.995, color='blue', linestyle='dashdot')
    axis.set_title(f'{dataset_name} - Frequency-Level Average Power Accuracy')
    axis.set_xlabel('Frequency [kHz]'); axis.set_ylabel('Average Reported Forward Power [W]')
    axis.grid(True, alpha=0.3); axis.legend(); figure.tight_layout()
    figure.savefig(output_path, dpi=250, bbox_inches='tight'); plt.close(figure)


def create_average_error_plot(averages, output_path, dataset_name):
    data = averages.sort_values('Frequency (kHz)')
    figure, axis = plt.subplots(figsize=(10, 6))
    x = data['Frequency (kHz)']
    axis.plot(x, data['Generator Forward Error (%)'], marker='o', label='Generator Average')
    axis.plot(x, data['Lynx Forward Error (%)'], marker='s', label='Lynx Average')
    axis.axhline(1.0, color='red', linestyle='dashed', label='Spec Limits')
    axis.axhline(0.5, color='blue', linestyle='dashdot', label='Test Limits')
    axis.axhline(-1.0, color='red', linestyle='dashed')
    axis.axhline(-0.5, color='blue', linestyle='dashdot')
    axis.set_title(f'{dataset_name} - Frequency-Level Average Forward Power Error')
    axis.set_xlabel('Frequency [kHz]'); axis.set_ylabel('Average Error vs Nominal Power (%)')
    axis.grid(True, alpha=0.3); axis.legend(); figure.tight_layout()
    figure.savefig(output_path, dpi=250, bbox_inches='tight'); plt.close(figure)


def create_summary(data, nominal_power):
    metrics = [
        'pFWDGen', 'pREFGen', 'pDELGen', 'pFWDLynx', 'pREFLynx', 'pDELLynx',
        'rLynx', 'iLynx', 'volLynx', 'curLynx',
        'Generator Forward Error (%)', 'Lynx Forward Error (%)',
        'Generator-Lynx Difference (%)',
    ]
    row = {
        'Nominal Power (W)': nominal_power,
        'Frequency Point Count': len(data),
        'Minimum Frequency (kHz)': data['Frequency (kHz)'].min(),
        'Maximum Frequency (kHz)': data['Frequency (kHz)'].max(),
        'Generator Spec Fail Count': int((~data['Generator Within Spec']).sum()),
        'Lynx Spec Fail Count': int((~data['Lynx Within Spec']).sum()),
        'Generator Test-Limit Fail Count': int((~data['Generator Within Test Limit']).sum()),
        'Lynx Test-Limit Fail Count': int((~data['Lynx Within Test Limit']).sum()),
        'Overall Result': 'PASS' if data['Generator Within Spec'].all() and data['Lynx Within Spec'].all() else 'FAIL',
    }
    for metric in metrics:
        values = pd.to_numeric(data[metric], errors='coerce').dropna()
        row[f'{metric} Average'] = values.mean() if not values.empty else np.nan
        row[f'{metric} Minimum'] = values.min() if not values.empty else np.nan
        row[f'{metric} Maximum'] = values.max() if not values.empty else np.nan
        row[f'{metric} Std Dev'] = values.std(ddof=1) if len(values) > 1 else 0.0 if len(values) == 1 else np.nan
    return pd.DataFrame([row])


def create_original_plot(data, nominal_power, output_path, serial_label, show=False):
    data = data.sort_values('Frequency (kHz)')
    figure, axis = plt.subplots(figsize=(10, 6))
    axis.set_xlim(data['Frequency (kHz)'].iloc[0], data['Frequency (kHz)'].iloc[-1])
    axis.plot(data['Frequency (kHz)'], data['pFWDGen'], color='green', label='Generator')
    axis.plot(data['Frequency (kHz)'], data['pFWDLynx'], color='black', label='Lynx')
    axis.axhline(nominal_power * 1.01, color='red', linestyle='dashed', label='Spec Limits')
    axis.axhline(nominal_power * 1.005, color='blue', linestyle='dashdot', label='Test Limits')
    axis.axhline(nominal_power * 0.99, color='red', linestyle='dashed')
    axis.axhline(nominal_power * 0.995, color='blue', linestyle='dashdot')
    axis.set_title(f'Power Accuracy of SN: {serial_label}')
    axis.set_xlabel('Frequency [kHz]')
    axis.set_ylabel('Reported Forward Power [W]')
    axis.legend(loc=4)
    axis.grid(True, alpha=0.25)
    figure.tight_layout()
    figure.savefig(output_path, dpi=250, bbox_inches='tight')
    if show:
        plt.show()
    else:
        plt.close(figure)


def create_error_plot(data, output_path, dataset_name):
    data = data.sort_values('Frequency (kHz)')
    figure, axis = plt.subplots(figsize=(10, 6))
    axis.plot(data['Frequency (kHz)'], data['Generator Forward Error (%)'], marker='o', label='Generator')
    axis.plot(data['Frequency (kHz)'], data['Lynx Forward Error (%)'], marker='s', label='Lynx')
    axis.axhline(1.0, color='red', linestyle='dashed', label='Spec Limits')
    axis.axhline(0.5, color='blue', linestyle='dashdot', label='Test Limits')
    axis.axhline(-1.0, color='red', linestyle='dashed')
    axis.axhline(-0.5, color='blue', linestyle='dashdot')
    axis.set_title(f'{dataset_name} - Forward Power Error')
    axis.set_xlabel('Frequency [kHz]'); axis.set_ylabel('Error vs Nominal Power (%)')
    axis.grid(True, alpha=0.3); axis.legend(); figure.tight_layout()
    figure.savefig(output_path, dpi=250, bbox_inches='tight'); plt.close(figure)


def create_comparison(data_a, data_b, name_a, name_b):
    keys = ['Frequency (kHz)']
    metrics = ['pFWDGen', 'pFWDLynx', 'Generator Forward Error (%)', 'Lynx Forward Error (%)', 'Generator-Lynx Difference (%)']
    a = data_a[keys + metrics].rename(columns={metric: f'{name_a} {metric}' for metric in metrics})
    b = data_b[keys + metrics].rename(columns={metric: f'{name_b} {metric}' for metric in metrics})
    merged = a.merge(b, on=keys, how='outer', indicator=True)
    merged['Matched Frequency'] = merged['_merge'].eq('both')
    merged['Availability'] = merged['_merge'].map({
        'both': 'Matched', 'left_only': f'Only {name_a}', 'right_only': f'Only {name_b}'
    })
    for metric in metrics:
        difference = merged[f'{name_b} {metric}'] - merged[f'{name_a} {metric}']
        merged[f'{metric} Difference B-A'] = difference
        merged[f'{metric} Absolute Difference'] = difference.abs()
        merged[f'{metric} Percent Difference vs A'] = np.where(
            merged[f'{name_a} {metric}'] != 0,
            difference / merged[f'{name_a} {metric}'] * 100.0,
            np.nan,
        )
    return merged.drop(columns=['_merge']).sort_values('Frequency (kHz)').reset_index(drop=True)


def create_comparison_plots(comparison, name_a, name_b, output_folder):
    output_folder = Path(output_folder); output_folder.mkdir(parents=True, exist_ok=True)
    matched = comparison[comparison['Matched Frequency']]
    paths = []
    for metric, label in [('pFWDGen', 'Generator Forward Power (W)'), ('pFWDLynx', 'Lynx Forward Power (W)'), ('Generator Forward Error (%)', 'Generator Error (%)'), ('Lynx Forward Error (%)', 'Lynx Error (%)')]:
        if matched.empty:
            continue
        figure, (top, bottom) = plt.subplots(2, 1, figsize=(10, 8), sharex=True, gridspec_kw={'height_ratios': [2.2, 1]})
        x = matched['Frequency (kHz)']
        top.plot(x, matched[f'{name_a} {metric}'], marker='o', label=name_a)
        top.plot(x, matched[f'{name_b} {metric}'], marker='s', linestyle='--', label=name_b)
        bottom.plot(x, matched[f'{metric} Difference B-A'], marker='o', color='#C00000')
        bottom.axhline(0, color='black', linewidth=1)
        top.set_ylabel(label); bottom.set_ylabel('B - A'); bottom.set_xlabel('Frequency (kHz)')
        top.grid(True, alpha=0.3); bottom.grid(True, alpha=0.3); top.legend()
        figure.suptitle(f'Power Accuracy Comparison - {label}')
        figure.tight_layout(); path = output_folder / f"comparison_{metric.replace(' ', '_').replace('%','pct')}.png"
        figure.savefig(path, dpi=250, bbox_inches='tight'); plt.close(figure); paths.append(path)
    return paths


def auto_size_workbook(writer):
    size_workbook_columns(writer, max_width=55)


def write_analysis_workbook(path, data, averages, summary, failures, settings, analysis_settings):
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        data.to_excel(writer, sheet_name='Analyzed Data', index=False)
        averages.to_excel(writer, sheet_name='Frequency Averages', index=False)
        summary.to_excel(writer, sheet_name='Summary', index=False)
        failed = data[(~data['Generator Within Spec']) | (~data['Lynx Within Spec'])]
        if not failed.empty:
            failed.to_excel(writer, sheet_name='Spec Failures', index=False)
        if not failures.empty:
            failures.to_excel(writer, sheet_name='Failed Files', index=False)
        pd.DataFrame({'Setting': list(settings.keys()), 'Value': list(settings.values())}).to_excel(writer, sheet_name='Run Settings', index=False)
        pd.DataFrame({'Setting': list(analysis_settings.keys()), 'Value': list(analysis_settings.values())}).to_excel(writer, sheet_name='Analysis Settings', index=False)
        auto_size_workbook(writer)


def write_comparison_workbook(path, comparison, summary_a, summary_b, settings):
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        comparison.to_excel(writer, sheet_name='Frequency Comparison', index=False)
        ranking = comparison.copy()
        ranking['Maximum Absolute Difference'] = ranking.filter(like='Absolute Difference').max(axis=1)
        ranking.sort_values('Maximum Absolute Difference', ascending=False).to_excel(writer, sheet_name='Difference Ranking', index=False)
        summary_a.to_excel(writer, sheet_name='A Summary', index=False)
        summary_b.to_excel(writer, sheet_name='B Summary', index=False)
        pd.DataFrame({'Setting': list(settings.keys()), 'Value': list(settings.values())}).to_excel(writer, sheet_name='Comparison Settings', index=False)
        auto_size_workbook(writer)


def export_powerpoint_package(config,generated_images,averages_a,averages_b=None,comparison=None,workbook_a=None,workbook_b=None,comparison_workbook=None):
    if not powerpoint_export_requested():return None
    package_text=os.environ.get('PARAMOUNT_POWERPOINT_PACKAGE_FOLDER','').strip()
    if not package_text:raise ValueError('PARAMOUNT_POWERPOINT_PACKAGE_FOLDER is not set.')
    package=Path(package_text).expanduser().resolve();package.mkdir(parents=True,exist_ok=True)
    manifest_path=package/'manifest.json'
    if manifest_path.exists():manifest_path.unlink()
    images=package/'images';tables=package/'tables'
    if images.exists():shutil.rmtree(images)
    if tables.exists():shutil.rmtree(tables)
    tables.mkdir(parents=True,exist_ok=True);averages_a.to_csv(tables/'dataset_a_frequency_averages.csv',index=False)
    if averages_b is not None:averages_b.to_csv(tables/'dataset_b_frequency_averages.csv',index=False)
    if comparison is not None:comparison.to_csv(tables/'comparison_b_minus_a.csv',index=False)
    slides=[];copied=[]
    for role,source_path in generated_images:
        source=Path(source_path)
        if not source.is_file():continue
        dest_dir=images/role;dest_dir.mkdir(parents=True,exist_ok=True);dest=dest_dir/source.name;shutil.copy2(source,dest)
        relative=dest.relative_to(package).as_posix();copied.append({'dataset_role':role.upper(),'source':str(source),'package_file':relative})
        slide={'slide_type':'image','title':source.name,'image_file':relative,'dataset_role':role.upper()}
        if role=='comparison':slide['difference_convention']='B_MINUS_A'
        slides.append(slide)
    dataset_b=None;comparison_info={'completed':False,'difference_convention':'B_MINUS_A'}
    metrics={'Analysis mode':config['mode'],'Dataset A':config['name_a'],'Dataset A averaged frequencies':len(averages_a),'Generated image slides':len(slides)}
    highlights=[f"Dataset A: {config['name_a']}",f'Generated {len(slides)} image slide(s) from this analysis run.']
    if config['mode']=='COMPARE':
        dataset_b={'label':config['name_b'],'source_folder':config['path_b'],'workbook':str(workbook_b) if workbook_b else ''}
        metrics.update({'Dataset B':config['name_b'],'Dataset B averaged frequencies':len(averages_b) if averages_b is not None else 0})
        highlights.append(f"Comparison convention: {config['name_b']} minus {config['name_a']}.")
        comparison_info={'completed':comparison is not None,'difference_convention':'B_MINUS_A','workbook':str(comparison_workbook) if comparison_workbook else ''}
    manifest={'schema_version':int(os.environ.get('PARAMOUNT_POWERPOINT_SCHEMA_VERSION','1')),
      'analysis_id':os.environ.get('PARAMOUNT_POWERPOINT_ANALYSIS_ID','power_accuracy').strip(),'display_name':'Power Accuracy','status':'completed','analysis_mode':config['mode'],
      'dataset_label':config['name_a'] if config['mode']=='SINGLE' else f"{config['name_b']} vs {config['name_a']}",'source_folder':config['path_a'],
      'dataset_a':{'label':config['name_a'],'source_folder':config['path_a'],'workbook':str(workbook_a) if workbook_a else ''},'dataset_b':dataset_b,
      'comparison':comparison_info,'highlights':highlights,'metrics':metrics,'generated_images':copied,'slides':slides}
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8');return manifest_path

def open_file(path):
    open_with_default_app(path)


def configure_gui():
    root = tk.Tk(); root.title(APP_TITLE); root.geometry('1000x750'); root.minsize(880, 650)
    style = ttk.Style(root)
    try: style.theme_use('vista')
    except tk.TclError: pass
    style.configure('Header.TLabel', font=('Segoe UI', 15, 'bold'))
    style.configure('Section.TLabel', font=('Segoe UI', 10, 'bold'))
    style.configure('Start.TButton', font=('Segoe UI', 11, 'bold'), padding=(14, 8))
    outer = ttk.Frame(root, padding=14); outer.pack(fill='both', expand=True)
    ttk.Label(outer, text=APP_TITLE, style='Header.TLabel').pack(anchor='w')
    ttk.Label(outer, text='Reproduce the original Power Accuracy graph from saved CSV data, generate summary/error outputs, or compare two hardware configurations.', wraplength=940).pack(anchor='w', pady=(2,10))
    notebook = ttk.Notebook(outer); notebook.pack(fill='both', expand=True)
    input_tab=ttk.Frame(notebook,padding=12);limits_tab=ttk.Frame(notebook,padding=12);plots_tab=ttk.Frame(notebook,padding=12);comparison_tab=ttk.Frame(notebook,padding=12);output_tab=ttk.Frame(notebook,padding=12)
    for tab,label in [(input_tab,'Input Data'),(limits_tab,'Limits'),(plots_tab,'Plots'),(comparison_tab,'Comparison'),(output_tab,'Output')]: notebook.add(tab,text=label)
    launcher_mode=os.environ.get('PARAMOUNT_ANALYSIS_MODE','INDIVIDUAL').strip().upper()
    initial_mode=launcher_mode if launcher_mode in {'SINGLE','COMPARE'} else 'SINGLE'
    mode=tk.StringVar(value=initial_mode)
    path_a=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_A_FOLDER','').strip())
    path_b=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_B_FOLDER','').strip())
    name_a=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_A_LABEL','').strip() or 'Configuration A')
    name_b=tk.StringVar(value=os.environ.get('PARAMOUNT_DATASET_B_LABEL','').strip() or 'Configuration B')
    nominal=tk.StringVar(value='');spec=tk.StringVar(value='0.01');test=tk.StringVar(value='0.005')
    use_recorded=tk.BooleanVar(value=True);dedupe=tk.BooleanVar(value=True);make_original=tk.BooleanVar(value=True);make_error=tk.BooleanVar(value=True);make_averages=tk.BooleanVar(value=True);show_plot=tk.BooleanVar(value=False);open_workbook=tk.BooleanVar(value=True)
    preview=tk.StringVar(value='Select a Power Accuracy collection folder.')
    input_tab.columnconfigure(1,weight=1);ttk.Label(input_tab,text='Analysis mode',style='Section.TLabel').grid(row=0,column=0,sticky='w')
    modes=ttk.Frame(input_tab);modes.grid(row=1,column=0,columnspan=3,sticky='w',pady=(5,12));ttk.Radiobutton(modes,text='Single dataset',variable=mode,value='SINGLE').pack(side='left',padx=(0,20));ttk.Radiobutton(modes,text='Compare two hardware configurations',variable=mode,value='COMPARE').pack(side='left')
    ttk.Label(input_tab,text='Configuration A folder').grid(row=2,column=0,sticky='w',pady=5);ttk.Entry(input_tab,textvariable=path_a,width=65).grid(row=2,column=1,sticky='ew');ttk.Label(input_tab,text='Display name').grid(row=3,column=0,sticky='w');ttk.Entry(input_tab,textvariable=name_a,width=35).grid(row=3,column=1,sticky='w')
    ttk.Label(input_tab,text='Configuration B folder').grid(row=4,column=0,sticky='w',pady=5);entry_b=ttk.Entry(input_tab,textvariable=path_b,width=65);entry_b.grid(row=4,column=1,sticky='ew');ttk.Label(input_tab,text='Display name').grid(row=5,column=0,sticky='w');name_b_e=ttk.Entry(input_tab,textvariable=name_b,width=35);name_b_e.grid(row=5,column=1,sticky='w')
    def browse(var,label,preview_it=False):
        current=var.get().strip()
        selected=filedialog.askdirectory(title='Select Power Accuracy collection folder',parent=root,mustexist=True,initialdir=current if current and Path(current).is_dir() else None)
        if selected:
            var.set(selected);label.set(Path(selected).name or label.get())
            if preview_it:
                files=find_measurement_csvs(selected);rows=0;freqs=set()
                for file in files:
                    try:
                        frame=pd.read_csv(file);rows+=len(frame);freqs.update(pd.to_numeric(frame.get('Test Frequency'),errors='coerce').dropna())
                    except Exception:pass
                settings=read_settings(selected);preview.set(f"Measurement CSV files: {len(files)} | Rows: {rows}\nFrequency range (kHz): {min(freqs) if freqs else 'N/A'} to {max(freqs) if freqs else 'N/A'}\nRecorded nominal power (W): {settings.get('nominal_plot_power_W','Not available')}")
    ttk.Button(input_tab,text='Browse...',command=lambda:browse(path_a,name_a,True)).grid(row=2,column=2,padx=(10,0));button_b=ttk.Button(input_tab,text='Browse...',command=lambda:browse(path_b,name_b));button_b.grid(row=4,column=2,padx=(10,0));ttk.Label(input_tab,textvariable=preview,wraplength=840,justify='left',foreground='#444444').grid(row=6,column=0,columnspan=3,sticky='w',pady=(16,0))
    ttk.Checkbutton(input_tab,text='Remove exact duplicate measurement rows',variable=dedupe).grid(row=7,column=0,columnspan=3,sticky='w',pady=(12,0))
    ttk.Checkbutton(limits_tab,text='Use nominal power and limits recorded during collection',variable=use_recorded).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,10))
    for row,label,var in [(1,'Nominal power override (W)',nominal),(2,'Spec limit fraction',spec),(3,'Test limit fraction',test)]:ttk.Label(limits_tab,text=label).grid(row=row,column=0,sticky='w',pady=5);ttk.Entry(limits_tab,textvariable=var,width=20).grid(row=row,column=1,sticky='w')
    ttk.Label(limits_tab,text='Original defaults: ±1% specification limits and ±0.5% test limits.',foreground='#555555').grid(row=4,column=0,columnspan=3,sticky='w',pady=(12,0))
    ttk.Checkbutton(plots_tab,text='Generate original Power Accuracy plot',variable=make_original).pack(anchor='w',pady=5);ttk.Checkbutton(plots_tab,text='Generate percent-error plot',variable=make_error).pack(anchor='w',pady=5);ttk.Checkbutton(plots_tab,text='Generate frequency-level average plots with standard-deviation bands',variable=make_averages).pack(anchor='w',pady=5);ttk.Checkbutton(plots_tab,text='Show original plot window',variable=show_plot).pack(anchor='w',pady=5)
    ttk.Label(comparison_tab,text='Matched conditions use test frequency. Comparison outputs include A/B generator and Lynx values, B-minus-A differences, absolute differences, percent differences versus A, unmatched-frequency availability, ranking, and overlay/difference plots.',wraplength=840,justify='left').pack(anchor='w')
    ttk.Checkbutton(output_tab,text='Open Configuration A workbook when complete',variable=open_workbook).pack(anchor='w',pady=5);ttk.Label(output_tab,text=f'Outputs: {ANALYSIS_WORKBOOK_NAME}, {ANALYSIS_PLOT_NAME}, percent-error plot, and optional {COMPARISON_WORKBOOK_NAME}.',wraplength=840).pack(anchor='w',pady=(14,0))
    result={'accepted':False}
    def number(var,label,minimum=None,allow_blank=False):
        text=var.get().strip()
        if allow_blank and not text:return None
        try:value=float(text)
        except ValueError as error:raise ValueError(f'{label} must be numeric.') from error
        if not np.isfinite(value):raise ValueError(f'{label} must be finite.')
        if minimum is not None and value<minimum:raise ValueError(f'{label} must be at least {minimum}.')
        return value
    def refresh(*_):
        state='normal' if mode.get()=='COMPARE' else 'disabled';entry_b.configure(state=state);name_b_e.configure(state=state);button_b.configure(state=state)
    mode.trace_add('write',refresh)
    def start():
        try:
            a=Path(path_a.get().strip()).resolve()
            if not find_measurement_csvs(a):raise ValueError('Select a valid Configuration A collection folder.')
            b=None
            if mode.get()=='COMPARE':
                b=Path(path_b.get().strip()).resolve()
                if not find_measurement_csvs(b):raise ValueError('Select a valid Configuration B collection folder.')
                if os.path.normcase(str(a))==os.path.normcase(str(b)):raise ValueError('Select two different datasets.')
            result.update({'accepted':True,'mode':mode.get(),'path_a':str(a),'path_b':str(b) if b else None,'name_a':name_a.get().strip() or a.name,'name_b':name_b.get().strip() or (b.name if b else 'Configuration B'),'use_recorded':bool(use_recorded.get()),'nominal_override':number(nominal,'Nominal power',0,True),'spec_fraction':number(spec,'Spec fraction',0),'test_fraction':number(test,'Test fraction',0),'dedupe':bool(dedupe.get()),'make_original':bool(make_original.get()),'make_error':bool(make_error.get()),'make_averages':bool(make_averages.get()),'show_plot':bool(show_plot.get()),'open_workbook':bool(open_workbook.get())});root.destroy()
        except Exception as error:messagebox.showerror('Invalid Power Accuracy analysis settings',str(error),parent=root)
    buttons=ttk.Frame(outer);buttons.pack(fill='x',pady=(12,0));ttk.Button(buttons,text='Cancel',command=root.destroy).pack(side='right',padx=(8,0));ttk.Button(buttons,text='Start Analysis',style='Start.TButton',command=start).pack(side='right');root.protocol('WM_DELETE_WINDOW',root.destroy);refresh();root.mainloop();return result


def process_dataset(folder,name,config,progress=None,start=0,end=100):
    if progress:progress.update(start,f"Loading {name}",str(folder))
    data,failures,settings=load_dataset(folder,name,config['dedupe'])
    spec_fraction=setting_float(settings,'spec_limit_fraction',config['spec_fraction']) if config['use_recorded'] else config['spec_fraction']
    test_fraction=setting_float(settings,'test_limit_fraction',config['test_fraction']) if config['use_recorded'] else config['test_fraction']
    nominal_override=None if config['use_recorded'] and config['nominal_override'] is None else config['nominal_override']
    if progress:progress.update(start+(end-start)*.15,f"Preparing {name}","Calculating errors, limits, and cycle assignments")
    data,nominal=prepare_analysis(data,settings,nominal_override,spec_fraction,test_fraction);data,recorded_cycles,detected_cycles=add_cycle_numbers(data,settings);summary=create_summary(data,nominal)
    averages=create_frequency_averages(data)
    serial=settings.get('generator_serial_override',settings.get('generator_unit_number',name))
    folder=Path(folder);generated_images=[]
    if progress:progress.update(start+(end-start)*.35,f"Generating {name} plots",f"Detected cycles: {detected_cycles}")
    if config['make_original']:
        create_original_plot(data,nominal,folder/ANALYSIS_PLOT_NAME,serial,config['show_plot'])
        generated_images.append(folder/ANALYSIS_PLOT_NAME)
        generated_images.extend(create_cycle_power_plots(
            data, nominal, folder / "Power Accuracy Cycle Plots", name
        ))
    if config['make_error']:
        create_error_plot(data,folder/'Power_Accuracy_Percent_Error.png',name)
        generated_images.append(folder/'Power_Accuracy_Percent_Error.png')
        generated_images.extend(create_cycle_error_plots(data,folder/"Power Accuracy Cycle Plots",name))
    if config['make_averages']:
        create_average_plot(averages,nominal,folder/AVERAGE_PLOT_NAME,name)
        create_average_error_plot(averages,folder/AVERAGE_ERROR_PLOT_NAME,name)
        generated_images.extend([folder/AVERAGE_PLOT_NAME,folder/AVERAGE_ERROR_PLOT_NAME])
    analysis_settings={'Nominal Power (W)':nominal,'Spec Limit Fraction':spec_fraction,'Test Limit Fraction':test_fraction,'Duplicate Rows Removed':config['dedupe'],'Raw Measurement Row Count':len(data),'Frequency Average Row Count':len(averages),'Recorded Collection Cycles':recorded_cycles,'Detected Collection Cycles':detected_cycles}
    if progress:progress.update(start+(end-start)*.85,f"Writing {name} workbook",str(folder/ANALYSIS_WORKBOOK_NAME))
    workbook=folder/ANALYSIS_WORKBOOK_NAME;write_analysis_workbook(workbook,data,averages,summary,failures,settings,analysis_settings)
    if progress:progress.update(end,f"{name} complete",str(workbook))
    return data,averages,summary,failures,workbook,generated_images


def run_analysis():
    config=configure_gui()
    if not config.get('accepted'):return
    progress=AnalysisProgress()
    try:
        end_a=42 if config['mode']=='COMPARE' else 90
        data_a,averages_a,summary_a,fail_a,workbook_a,images_a=process_dataset(config['path_a'],config['name_a'],config,progress,0,end_a);generated_images=[('dataset_a',p) for p in images_a]
        averages_b=None;comparison=None;workbook_b=None;comparison_workbook=None
        if config['mode']=='COMPARE':
            data_b,averages_b,summary_b,fail_b,workbook_b,images_b=process_dataset(config['path_b'],config['name_b'],config,progress,42,84);generated_images.extend(('dataset_b',p) for p in images_b);progress.update(87,'Comparing datasets','Calculating B minus A frequency averages');comparison=create_comparison(averages_a,averages_b,config['name_a'],config['name_b']);folder=Path(config['path_a'])/COMPARISON_FOLDER_NAME;folder.mkdir(parents=True,exist_ok=True);comparison_workbook=folder/COMPARISON_WORKBOOK_NAME;settings={'Configuration A':config['name_a'],'Configuration A Folder':config['path_a'],'Configuration B':config['name_b'],'Configuration B Folder':config['path_b'],'Difference Convention':'Configuration B minus Configuration A','Matching Condition':'Frequency-level averages by Test Frequency','Configuration A Raw Rows':len(data_a),'Configuration A Averaged Frequencies':len(averages_a),'Configuration B Raw Rows':len(data_b),'Configuration B Averaged Frequencies':len(averages_b)};progress.update(90,'Writing comparison workbook',str(comparison_workbook));write_comparison_workbook(comparison_workbook,comparison,summary_a,summary_b,settings);progress.update(93,'Generating comparison plots','Creating overlays and B-minus-A plots');generated_images.extend(('comparison',p) for p in create_comparison_plots(comparison,config['name_a'],config['name_b'],folder/'Comparison Plots'))
        package_warning=None
        if powerpoint_export_requested():
            try:progress.update(97,'Packaging PowerPoint assets','Copying cycle, average, and comparison plots');manifest_path=export_powerpoint_package(config,generated_images,averages_a,averages_b,comparison,workbook_a,workbook_b,comparison_workbook);print(f'PowerPoint package manifest saved to: {manifest_path}') if manifest_path else None
            except Exception as error:package_warning=f'PowerPoint package export warning: {error}';print(package_warning,file=sys.stderr)
        progress.update(100,'Analysis complete','All outputs generated')
        if config['open_workbook']:open_file(workbook_a)
        progress.close();root=tk.Tk();root.withdraw();message=f'Power Accuracy analysis is complete.\n\nConfiguration A workbook:\n{workbook_a}';message+=f'\n\nComparison workbook:\n{comparison_workbook}' if comparison_workbook else '';message+=f'\n\n{package_warning}\nNormal analysis outputs were still completed.' if package_warning else '';messagebox.showinfo('Analysis complete',message,parent=root);root.destroy()
    except Exception:progress.close();raise


if __name__=='__main__':
    run_analysis()
