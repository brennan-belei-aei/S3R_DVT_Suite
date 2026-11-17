"""
PMNT_Power_Regulation_Collection_GUI.py

Collects a FastDAQ delivered-power waveform and saves:

    Delivered_Power_Test.csv
    Power_Regulation_Run_Settings.csv

Analysis is intentionally performed by a separate script.
"""

import csv
import sys
import datetime
import os
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
import suite_common.bootstrap  # noqa: E402,F401

from suite_instruments.factory import InstrumentFactory  # noqa: E402

GENERATOR_IP = "PMNT0451C5"
OUTPUT_FOLDER = str(Path.home())

STANDARD_DELAY = 2
HALO_MULTIPLIER = 10
FIXED_FREQUENCY_MODE = 0
GENERATOR_FREQUENCY = 13560
REGULATION_MODE = 7

RF_ON_DELAY = 0.5
ACQUISITION_DELAY = 0.5
SAMPLE_PERIOD = 5015e-9

FASTDAQ_SAMPLE_INTERVAL_CMD = [118,'8:1','1:1',55,0,4,0,64,13,3,0]
FASTDAQ_START_CMD = [118,'4:1','1:1',56,0,1,0]
FASTDAQ_TRIGGER_CMD = [118,'4:1','1:1',56,0,2,0]
FASTDAQ_STOP_CMD = [118,'4:1','1:1',56,0,4,0]


def build_fastdaq_read_cmd(block_number):
    return [248,'6:1','1:680',57,0,1,0,block_number,0]


def get_user_settings():
    root=tk.Tk()
    root.title('Power Regulation Collection')
    result={'accepted':False}

    gen=tk.StringVar(value=GENERATOR_IP)
    out=tk.StringVar(value=OUTPUT_FOLDER)

    frm=ttk.Frame(root,padding=10)
    frm.pack(fill='both',expand=True)

    ttk.Label(frm,text='Generator Hostname/IP').grid(row=0,column=0,sticky='w')
    ttk.Entry(frm,textvariable=gen,width=40).grid(row=0,column=1,sticky='ew')

    ttk.Label(frm,text='Output Folder').grid(row=1,column=0,sticky='w')
    ttk.Entry(frm,textvariable=out,width=50).grid(row=1,column=1,sticky='ew')

    def browse():
        p=filedialog.askdirectory(mustexist=True,parent=root)
        if p:
            out.set(p)

    ttk.Button(frm,text='Browse',command=browse).grid(row=1,column=2)

    def start():
        result['accepted']=True
        result['generator']=gen.get().strip()
        result['output']=out.get().strip()
        root.destroy()

    ttk.Button(frm,text='Start Collection',command=start).grid(row=2,column=1,pady=15)
    root.mainloop()

    if not result['accepted']:
        raise SystemExit()

    return result


def create_output_folder(parent,generator):
    stamp=datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    folder=Path(parent)/f'{stamp}_Power_Regulation_{generator}'
    folder.mkdir(parents=True,exist_ok=True)
    return folder


def read_fastdaq(Egen):
    combined=[]

    for block in range(24):
        response=Egen.genericCMD(build_fastdaq_read_cmd(block))
        combined.extend(response[2])

    power=[x/HALO_MULTIPLIER for x in combined]
    times=[i*SAMPLE_PERIOD for i in range(len(power))]

    return times,power


def save_waveform_csv(path,times,power):
    with open(path,'w',newline='') as fh:
        writer=csv.writer(fh)
        writer.writerow(['SampleIndex','TimeSec','DeliveredPower'])

        for i,(t,p) in enumerate(zip(times,power)):
            writer.writerow([i,t,p])


def save_settings(path,data):
    with open(path,'w',newline='') as fh:
        writer=csv.writer(fh)
        writer.writerow(['setting','value'])

        for k,v in data.items():
            writer.writerow([k,v])


def main():

    cfg=get_user_settings()
    egen=None

    try:
        egen=InstrumentFactory({'generator':cfg['generator']},{'generator':'ae.fastdaq'}).create('generator')

        time.sleep(STANDARD_DELAY)

        egen.setFixedFrequencyMode(FIXED_FREQUENCY_MODE)
        egen.setStartFreq(GENERATOR_FREQUENCY)
        egen.setGenFreq(GENERATOR_FREQUENCY)
        egen.setRegulationMode(REGULATION_MODE)

        power_limit=int(egen.reportUserPowerLimit())
        serial=egen.serialNumber()

        egen.DLP_setpoint(power_limit*HALO_MULTIPLIER,
                          power_limit*HALO_MULTIPLIER)

        out_folder=create_output_folder(cfg['output'],cfg['generator'])

        settings={
            'generator_ip':cfg['generator'],
            'serial_number':serial,
            'power_limit':power_limit,
            'frequency_khz':GENERATOR_FREQUENCY,
            'sample_period_s':SAMPLE_PERIOD,
            'timestamp':datetime.datetime.now().isoformat()
        }

        save_settings(out_folder/'Power_Regulation_Run_Settings.csv',settings)

        egen.RF_ON()
        time.sleep(RF_ON_DELAY)

        egen.genericCMD(FASTDAQ_SAMPLE_INTERVAL_CMD)
        egen.genericCMD(FASTDAQ_START_CMD)
        egen.genericCMD(FASTDAQ_TRIGGER_CMD)

        time.sleep(ACQUISITION_DELAY)

        times,power=read_fastdaq(egen)

        egen.genericCMD(FASTDAQ_STOP_CMD)
        egen.RF_OFF()

        save_waveform_csv(out_folder/'Delivered_Power_Test.csv',times,power)

        messagebox.showinfo(
            'Collection Complete',
            f'Samples: {len(power)}\n\nSaved to:\n{out_folder}'
        )

    finally:
        if egen is not None:
            try:
                egen.RF_OFF()
            except Exception:
                pass
            try:
                egen.close()
            except Exception:
                pass


if __name__=='__main__':
    main()
