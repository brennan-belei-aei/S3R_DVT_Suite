"""
Script Name:            Power Accuracy
Date Created:           January 27, 2022
Created by:             Christopher McLean
Date last modified:     August 18, 2022
Last Modified by:       Christopher McLean
Revision of Python:     3.9
Brief Description:      Sweeps power and records measured difference
Dependancies:           suite_instruments (AECommandsUniversal DUT, Lynx), datetime, time, numpy, os, csv, sys

"""
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default
from suite_common.output_folders import create_unique_test_output_folder

from suite_instruments.factory import InstrumentFactory
import datetime
import time
import numpy
import os
import csv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ==========================================================
# USER-EDITABLE DEFAULTS
# ==========================================================
lynxID = bench_default("lynx")
genID = bench_default("generator")
STANDALONE_DEFAULT_BASE_PATH = str(Path.home())
basePath = (
    os.environ.get('PARAMOUNT_COLLECTION_PARENT_FOLDER', STANDALONE_DEFAULT_BASE_PATH).strip()
    or STANDALONE_DEFAULT_BASE_PATH
)
TEST_FOLDER_NAME = 'Power Accuracy'

BASE_MAX_POWER_W = 3000
minFreq = 12882
maxFreq = 14238
FREQUENCY_POINT_COUNT = 31
NUMBER_OF_CYCLES = 1
WARMUP_FREQUENCY_KHZ = 13560
WARMUP_TIME_S = 30
POST_WARMUP_SETTLE_S = 2
FREQUENCY_RF_SETTLE_S = 0.5
RF_OFF_SETTLE_S = 0.5
FORWARD_STABILITY_FRACTION = 0.005
UNIT_SERIAL_OVERRIDE = 2918753
SHOW_PLOT = True

# Deferred runtime state.
lynx = None
lynxSerial = None
Egen = None
brandedCheck = None
unitNumber = None
unitSerial = None
genCenterFreq = None
H_Mode = None
maxPower = None
lowPower = None
testFreq = None
outPath = None
unit_outPath = None
dataPath = None
fh = None
writer = None
headers = None
settingsPath = None


# ==========================================================
# GUI AND OUTPUT HELPERS
# ==========================================================
def validate_gui_global_definitions():
    required = (
        'lynxID', 'genID', 'basePath', 'TEST_FOLDER_NAME',
        'BASE_MAX_POWER_W', 'minFreq', 'maxFreq', 'FREQUENCY_POINT_COUNT',
        'NUMBER_OF_CYCLES',
        'WARMUP_FREQUENCY_KHZ', 'WARMUP_TIME_S', 'POST_WARMUP_SETTLE_S',
        'FREQUENCY_RF_SETTLE_S', 'RF_OFF_SETTLE_S',
        'FORWARD_STABILITY_FRACTION', 'UNIT_SERIAL_OVERRIDE', 'SHOW_PLOT',
    )
    missing = [name for name in required if name not in globals()]
    if missing:
        raise RuntimeError(
            'Required Power Accuracy GUI settings are not defined: '
            + ', '.join(missing)
        )


def configure_power_accuracy_with_gui():
    global lynxID, genID, basePath, BASE_MAX_POWER_W
    global minFreq, maxFreq, FREQUENCY_POINT_COUNT, NUMBER_OF_CYCLES
    global WARMUP_FREQUENCY_KHZ, WARMUP_TIME_S, POST_WARMUP_SETTLE_S
    global FREQUENCY_RF_SETTLE_S, RF_OFF_SETTLE_S
    global FORWARD_STABILITY_FRACTION, UNIT_SERIAL_OVERRIDE, SHOW_PLOT

    root = tk.Tk()
    root.title('Paramount Power Accuracy Setup')
    root.geometry('940x680')
    root.minsize(820, 600)

    style = ttk.Style(root)
    try:
        style.theme_use('vista')
    except tk.TclError:
        pass
    style.configure('Header.TLabel', font=('Segoe UI', 15, 'bold'))
    style.configure('Section.TLabel', font=('Segoe UI', 10, 'bold'))
    style.configure('Start.TButton', font=('Segoe UI', 11, 'bold'), padding=(14, 8))

    outer = ttk.Frame(root, padding=14)
    outer.pack(fill='both', expand=True)
    ttk.Label(outer, text='Paramount Power Accuracy', style='Header.TLabel').pack(anchor='w')
    ttk.Label(
        outer,
        text=(
            'Configure the DUT, Lynx reference meter, frequency sweep, warm-up, '
            'stability criterion, and output folder. The original test measures '
            'power accuracy across frequency at maximum configured power.'
        ),
        wraplength=880,
    ).pack(anchor='w', pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill='both', expand=True)
    instruments_tab = ttk.Frame(notebook, padding=12)
    sweep_tab = ttk.Frame(notebook, padding=12)
    timing_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(instruments_tab, text='Instruments')
    notebook.add(sweep_tab, text='Frequency Sweep')
    notebook.add(timing_tab, text='Warm-up and Stability')
    notebook.add(output_tab, text='Output')

    variables = {}

    def add_entry(parent, row, label, key, default, help_text='', width=38):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky='w', padx=(0, 10), pady=5
        )
        variable = tk.StringVar(value=str(default))
        variables[key] = variable
        ttk.Entry(parent, textvariable=variable, width=width).grid(
            row=row, column=1, sticky='ew', pady=5
        )
        if help_text:
            ttk.Label(
                parent, text=help_text, foreground='#555555', wraplength=350
            ).grid(row=row, column=2, sticky='w', padx=(10, 0), pady=5)

    add_entry(instruments_tab, 0, 'DUT IP / hostname', 'gen_id', genID, width=50)
    add_entry(instruments_tab, 1, 'Lynx hostname / port', 'lynx_id', lynxID, width=50)
    add_entry(
        instruments_tab, 2, 'Unit serial override', 'unit_serial', UNIT_SERIAL_OVERRIDE,
        'Preserves the original hard-coded serial used in filenames and plots.'
    )
    add_entry(
        instruments_tab, 3, 'Base maximum power (W)', 'max_power', BASE_MAX_POWER_W,
        'The original script multiplies this by the detected HALO factor.'
    )

    add_entry(sweep_tab, 0, 'Minimum frequency (kHz)', 'min_freq', minFreq)
    add_entry(sweep_tab, 1, 'Maximum frequency (kHz)', 'max_freq', maxFreq)
    add_entry(sweep_tab, 2, 'Number of frequency points', 'point_count', FREQUENCY_POINT_COUNT)
    add_entry(
        sweep_tab, 3, 'Number of collection cycles', 'cycle_count', NUMBER_OF_CYCLES,
        'Runs the complete frequency sweep this many times in the same output CSV.'
    )
    ttk.Label(
        sweep_tab,
        text=(
            'The original inclusive numpy.linspace sweep is retained. '
            'Default: 31 points from 12,882 to 14,238 kHz.'
        ),
        foreground='#555555', wraplength=760,
    ).grid(row=4, column=0, columnspan=3, sticky='w', pady=(14, 0))

    add_entry(timing_tab, 0, 'Warm-up frequency (kHz)', 'warmup_freq', WARMUP_FREQUENCY_KHZ)
    add_entry(timing_tab, 1, 'Warm-up time (s)', 'warmup_time', WARMUP_TIME_S)
    add_entry(timing_tab, 2, 'Post-warm-up settle time (s)', 'post_warmup', POST_WARMUP_SETTLE_S)
    add_entry(timing_tab, 3, 'Per-frequency RF settle time (s)', 'rf_settle', FREQUENCY_RF_SETTLE_S)
    add_entry(timing_tab, 4, 'RF Off settle time (s)', 'off_settle', RF_OFF_SETTLE_S)
    add_entry(
        timing_tab, 5, 'Forward-power stability fraction', 'stability',
        FORWARD_STABILITY_FRACTION,
        'Default 0.005 corresponds to ±0.5% between consecutive readings.'
    )
    show_plot = tk.BooleanVar(value=SHOW_PLOT)
    ttk.Checkbutton(
        timing_tab, text='Show final plot window', variable=show_plot
    ).grid(row=6, column=0, columnspan=3, sticky='w', pady=5)

    output_parent = tk.StringVar(value=basePath)
    ttk.Label(output_tab, text='Parent output folder').grid(
        row=0, column=0, sticky='w', padx=(0, 10), pady=5
    )
    ttk.Entry(output_tab, textvariable=output_parent, width=60).grid(
        row=0, column=1, sticky='ew', pady=5
    )
    output_tab.columnconfigure(1, weight=1)

    def browse_output():
        initial = output_parent.get().strip()
        if not os.path.isdir(initial):
            initial = str(Path.home())
        selected = filedialog.askdirectory(
            title='Select parent folder for Power Accuracy data',
            initialdir=initial,
            parent=root,
            mustexist=True,
        )
        if selected:
            output_parent.set(selected)

    ttk.Button(output_tab, text='Browse...', command=browse_output).grid(
        row=0, column=2, padx=(10, 0), pady=5
    )
    ttk.Label(
        output_tab,
        text=(
            'A run folder will be created as: YYYY-MM-DD_HH-MM-SS - Power Accuracy '
            '- Generator Name. Exact same-second collisions use Run 2, Run 3, etc.'
        ),
        foreground='#555555', wraplength=780,
    ).grid(row=1, column=0, columnspan=3, sticky='w', pady=(12, 0))

    result = {'accepted': False}

    def number(key, label, minimum=None, integer=False):
        try:
            value = float(variables[key].get().strip())
        except ValueError as error:
            raise ValueError(f'{label} must be numeric.') from error
        if not numpy.isfinite(value):
            raise ValueError(f'{label} must be finite.')
        if integer:
            if not value.is_integer():
                raise ValueError(f'{label} must be a whole number.')
            value = int(value)
        if minimum is not None and value < minimum:
            raise ValueError(f'{label} must be at least {minimum}.')
        return value

    def apply_and_start():
        nonlocal result
        global lynxID, genID, basePath, BASE_MAX_POWER_W
        global minFreq, maxFreq, FREQUENCY_POINT_COUNT, NUMBER_OF_CYCLES
        global WARMUP_FREQUENCY_KHZ, WARMUP_TIME_S, POST_WARMUP_SETTLE_S
        global FREQUENCY_RF_SETTLE_S, RF_OFF_SETTLE_S
        global FORWARD_STABILITY_FRACTION, UNIT_SERIAL_OVERRIDE, SHOW_PLOT
        try:
            selected_gen = variables['gen_id'].get().strip()
            selected_lynx = variables['lynx_id'].get().strip()
            if not selected_gen or not selected_lynx:
                raise ValueError('DUT and Lynx addresses cannot be blank.')
            selected_min = number('min_freq', 'Minimum frequency', 1, integer=True)
            selected_max = number('max_freq', 'Maximum frequency', 1, integer=True)
            if selected_max <= selected_min:
                raise ValueError('Maximum frequency must be greater than minimum frequency.')
            parent = Path(output_parent.get().strip()).resolve()
            if not parent.exists() or not parent.is_dir():
                raise ValueError('Select an existing parent output folder.')

            genID = selected_gen
            lynxID = selected_lynx
            UNIT_SERIAL_OVERRIDE = number('unit_serial', 'Unit serial', 0, integer=True)
            BASE_MAX_POWER_W = number('max_power', 'Base maximum power', 0.000001)
            minFreq = selected_min
            maxFreq = selected_max
            FREQUENCY_POINT_COUNT = number('point_count', 'Frequency point count', 2, integer=True)
            NUMBER_OF_CYCLES = number('cycle_count', 'Number of collection cycles', 1, integer=True)
            WARMUP_FREQUENCY_KHZ = number('warmup_freq', 'Warm-up frequency', 1, integer=True)
            WARMUP_TIME_S = number('warmup_time', 'Warm-up time', 0)
            POST_WARMUP_SETTLE_S = number('post_warmup', 'Post-warm-up settle time', 0)
            FREQUENCY_RF_SETTLE_S = number('rf_settle', 'RF settle time', 0)
            RF_OFF_SETTLE_S = number('off_settle', 'RF Off settle time', 0)
            FORWARD_STABILITY_FRACTION = number('stability', 'Stability fraction', 0)
            SHOW_PLOT = bool(show_plot.get())
            basePath = str(parent)

            result['accepted'] = True
            root.destroy()
        except Exception as error:
            messagebox.showerror('Invalid Power Accuracy settings', str(error), parent=root)

    buttons = ttk.Frame(outer)
    buttons.pack(fill='x', pady=(12, 0))
    ttk.Button(buttons, text='Cancel', command=root.destroy).pack(
        side='right', padx=(8, 0)
    )
    ttk.Button(
        buttons, text='Start Test', style='Start.TButton', command=apply_and_start
    ).pack(side='right')

    root.protocol('WM_DELETE_WINDOW', root.destroy)
    root.mainloop()
    if not result['accepted']:
        raise SystemExit('Power Accuracy cancelled before instrument connection.')


def initialize_instruments_and_output():
    global lynx, lynxSerial, Egen, brandedCheck, unitNumber, unitSerial
    global genCenterFreq, H_Mode, maxPower, lowPower, testFreq
    global outPath, unit_outPath, dataPath, fh, writer, headers, settingsPath

    factory = InstrumentFactory({'lynx': lynxID, 'generator': genID})
    lynx = factory.create('lynx')
    lynxSerial = lynx.reportSerialNumber()

    Egen = factory.create('generator')
    brandedCheck = Egen.reportSerialNumber(1)
    if brandedCheck[0] == 'X':
        unitNumber = Egen.reportSerialNumber(2)
    else:
        unitNumber = brandedCheck
    unitSerial = UNIT_SERIAL_OVERRIDE
    genCenterFreq = Egen.reportFixedFrequency(8) / 1000

    Egen.rfOFF()
    time.sleep(RF_OFF_SETTLE_S)

    if Egen.isHaloEnabled():
        H_Mode = 10
    else:
        H_Mode = 1

    maxPower = BASE_MAX_POWER_W * H_Mode
    if H_Mode == 10:
        lowPower = Egen.reportPowerSupplySize(1) * H_Mode
    else:
        lowPower = maxPower / 40

    testFreq = numpy.linspace(
        minFreq, maxFreq, FREQUENCY_POINT_COUNT,
        endpoint=True, retstep=False, dtype=None, axis=0
    )

    Egen.setActiveControlMode(2)
    Egen.setRegulationMode(7)
    Egen.setPulsingFrequency(0)

    outPath = str(
        create_unique_test_output_folder(basePath, TEST_FOLDER_NAME, genID)
    )
    unit_outPath = os.path.join(outPath, str(unitNumber))
    os.mkdir(unit_outPath)

    dateStamp = datetime.datetime.now()
    timeStamp = (
        str(dateStamp.year) + '_' + str(dateStamp.month) + '_'
        + str(dateStamp.day) + '_' + str(dateStamp.hour) + '_'
        + str(dateStamp.minute) + '_' + str(dateStamp.second)
    )
    dataPath = os.path.join(
        outPath, str(unitSerial) + '_' + str(lynxSerial) + '_' + timeStamp + '.csv'
    )
    fh = open(dataPath, 'wt')
    writer = csv.writer(fh, 'excel', lineterminator='\n')
    headers = [
        'Test Frequency', 'pFWDGen', 'pREFGen', 'pDELGen',
        'pFWDLynx', 'pREFLynx', 'pDELLynx', 'rLynx', 'iLynx',
        'volLynx', 'curLynx'
    ]
    writer.writerow(headers)

    settingsPath = os.path.join(outPath, 'Power_Accuracy_Run_Settings.csv')
    with open(settingsPath, 'w', newline='') as settings_file:
        settings_writer = csv.writer(settings_file, lineterminator='\n')
        settings_writer.writerow(['setting', 'value'])
        settings_writer.writerows([
            ['collection_timestamp', datetime.datetime.now().isoformat(timespec='seconds')],
            ['generator_hostname', genID],
            ['generator_serial_override', unitSerial],
            ['generator_unit_number', unitNumber],
            ['lynx_hostname', lynxID],
            ['lynx_serial', lynxSerial],
            ['halo_mode_multiplier', H_Mode],
            ['base_max_power_W', BASE_MAX_POWER_W],
            ['command_max_power', maxPower],
            ['nominal_plot_power_W', maxPower / H_Mode],
            ['minimum_frequency_kHz', minFreq],
            ['maximum_frequency_kHz', maxFreq],
            ['frequency_point_count', FREQUENCY_POINT_COUNT],
            ['number_of_collection_cycles', NUMBER_OF_CYCLES],
            ['warmup_frequency_kHz', WARMUP_FREQUENCY_KHZ],
            ['warmup_time_s', WARMUP_TIME_S],
            ['post_warmup_settle_s', POST_WARMUP_SETTLE_S],
            ['frequency_rf_settle_s', FREQUENCY_RF_SETTLE_S],
            ['rf_off_settle_s', RF_OFF_SETTLE_S],
            ['forward_stability_fraction', FORWARD_STABILITY_FRACTION],
            ['spec_limit_fraction', 0.01],
            ['test_limit_fraction', 0.005],
            ['measurement_csv', dataPath],
        ])
        settings_file.flush()
        os.fsync(settings_file.fileno())
    print('Test data folder:', outPath)


validate_gui_global_definitions()
configure_power_accuracy_with_gui()
initialize_instruments_and_output()

#Run Test
try:
    # Warm up period
    Egen.setFixedFrequency(WARMUP_FREQUENCY_KHZ)
    #Egen.setFixedFrequency(2000)
    # Egen.powerSetpoint(maxPower/2)
    Egen.setPowerSetpoint(int(maxPower))
    # Egen.RF_ON()
    Egen.rfON()
    time.sleep(WARMUP_TIME_S)
    # Egen.RF_OFF()
    Egen.rfOFF()

    # Egen.powerSetpoint(maxPower)
    Egen.setPowerSetpoint(int(maxPower))
    # Egen.setGenFreq(minFreq)
    Egen.setFixedFrequency(int(minFreq))
    time.sleep(POST_WARMUP_SETTLE_S)

except Exception as startUp:
    if os.path.exists(dataPath):
        fh.flush()
        os.fsync(fh)
        fh.close()
        os.remove(dataPath)
    e = sys.exc_info()
    Egen.rfOFF()
    Egen.close()
    lynx.close()
    print(
        '\nUnexpected error encountered during startup. Objects have been closed and data file has been removed')
    print('Error message type: ', e[0])
    print('Error Message: ', e[1])
    print('Error on line: ', e[2].tb_lineno)
    sys.exit()

# Begin Test

try:
    for cycle_number in range(1, NUMBER_OF_CYCLES + 1):
        print(f'Starting Power Accuracy collection cycle {cycle_number} of {NUMBER_OF_CYCLES}')
        for i in testFreq:
            i = int(i)
            data = []
            # Egen.setGenFreq(i)
            Egen.setFixedFrequency(i)
            lynx.setFreq(i)
            Egen.rfON()
            time.sleep(FREQUENCY_RF_SETTLE_S)
            print(f'Cycle {cycle_number} of {NUMBER_OF_CYCLES}; Testing frequency [kHz], {i}')
            # time.sleep(5)  # Allow RF to stabilize; turn into average later
            det = True
            while det:
                oldPower = Egen.reportForwardPower() * H_Mode
                newPower = Egen.reportForwardPower() * H_Mode
                if newPower > (oldPower + oldPower*FORWARD_STABILITY_FRACTION) or newPower < (oldPower - oldPower*FORWARD_STABILITY_FRACTION):
                    det = True
                else:
                    det = False

            data.append(lynx.getFreq())
            data.append(Egen.reportForwardPower())
            data.append(Egen.reportReflectedPower())
            data.append(Egen.reportDeliveredPower())
            data.extend(lynx.readUnit222())

            writer.writerow(data)
            fh.flush()
            os.fsync(fh)
            Egen.rfOFF()
            time.sleep(0.5)

        print(f'Completed Power Accuracy collection cycle {cycle_number} of {NUMBER_OF_CYCLES}')

    #Ensure Generator Off and close file handler
    # Egen.RF_OFF()
    Egen.rfOFF()
    writer.writerow(data)
    fh.flush()
    os.fsync(fh)

except Exception as testFail:
    writer.writerow(data)
    e = sys.exc_info()
    print('\nUnexpected error encountered during test. Objects have been closed'
          ' and data file has been written for review at ', basePath)
    print('Error message type: ', e[0])
    print('Error Message: ', e[1])
    print('Error on line: ', e[2].tb_lineno)
    sys.exit()

finally:
    fh.flush()
    os.fsync(fh)
    fh.close()
    #Sgen.close()
    Egen.close()
    lynx.close()

print('Test Complete')
print('Data saved to: ', outPath)

print('Collection-only script complete. Analysis is performed by PMNT_Power_Accuracy_Analysis_GUI.py')
print('Run settings saved to: ', settingsPath)
