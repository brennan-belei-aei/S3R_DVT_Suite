"""
Script Name             Power Linearity
Date Created:           May 05, 2022
Created by:             Christopher McLean
Date last modified:     August 18, 2022
Last Modified by:       Christopher McLean
Revision of Python:     3.9
Brief Description:      Sweeps frequency at maximum power and records changes in power
Dependancies:           datetime, time, numpy, csv, os, tkinter, sys, suite_instruments (Lynx, AECommandsUniversal DUT, Navigator)

"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default
from suite_common.output_folders import create_unique_test_output_folder

import datetime
import time
import numpy
import os
import csv
from tkinter import *
from tkinter import filedialog, messagebox
from suite_instruments.factory import InstrumentFactory


ERROR_LOG_NAME = "Power_Linearity_Error.log"


def _show_error_and_pause(title, message):
    try:
        dialog_root = Tk()
        dialog_root.withdraw()
        messagebox.showerror(title, message, parent=dialog_root)
        dialog_root.destroy()
    except Exception:
        pass
    if sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close this window...")
        except (EOFError, OSError):
            pass


def _write_error_log(context, error_info=None, folder=None):
    error_info = error_info or sys.exc_info()
    target_folder = Path(folder).expanduser().resolve() if folder else Path(__file__).resolve().parent
    if not target_folder.is_dir():
        target_folder = Path(__file__).resolve().parent
    path = target_folder / ERROR_LOG_NAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + "=" * 80 + "\n")
        handle.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} - {context}\n")
        traceback.print_exception(*error_info, file=handle)
    traceback.print_exception(*error_info)
    return path


def _unhandled_exception_hook(exc_type, exc_value, exc_traceback):
    log_path = _write_error_log(
        "Unhandled exception before or outside the collection loop",
        (exc_type, exc_value, exc_traceback),
    )
    _show_error_and_pause(
        "Power Linearity collection failed",
        f"{exc_type.__name__}: {exc_value}\n\nFull traceback saved to:\n{log_path}",
    )


sys.excepthook = _unhandled_exception_hook

WARMUP_TIME_S = int(os.environ.get("PARAMOUNT_WARMUP_TIME_S", "10"))
basePath = os.environ.get('PARAMOUNT_COLLECTION_PARENT_FOLDER', '').strip() or str(Path.home())
NUMBER_OF_CYCLES = 1

lynxID = bench_default("lynx")
genID = bench_default("generator")


def configure_instrument_addresses_before_connection():
    """Collect addresses and output parent before constructing instrument objects."""
    global lynxID, genID, basePath, NUMBER_OF_CYCLES

    root = Tk()
    root.title('Paramount Power Linearity Collection')
    root.geometry('680x420')
    root.minsize(620, 320)

    outer = Frame(root, padx=18, pady=18)
    outer.pack(fill='both', expand=True)

    Label(
        outer,
        text='Paramount Power Linearity Collection',
        font=('Segoe UI', 15, 'bold'),
    ).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 6))
    Label(
        outer,
        text=(
            'Enter the instrument hostnames or IP addresses before connecting. '
            'No device communication occurs until Start Test is clicked.'
        ),
        justify='left',
        wraplength=630,
    ).grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 18))

    generator_var = StringVar(value=genID)
    lynx_var = StringVar(value=lynxID)
    output_var = StringVar(value=basePath)
    cycle_var = StringVar(value=str(NUMBER_OF_CYCLES))

    Label(outer, text='Generator hostname / IP address').grid(row=2, column=0, sticky='w', pady=7)
    Entry(outer, textvariable=generator_var, width=46).grid(row=2, column=1, sticky='ew', pady=7)
    Label(outer, text='Lynx hostname / IP address').grid(row=3, column=0, sticky='w', pady=7)
    Entry(outer, textvariable=lynx_var, width=46).grid(row=3, column=1, sticky='ew', pady=7)
    Label(outer, text='Parent output folder').grid(row=4, column=0, sticky='w', pady=7)
    Entry(outer, textvariable=output_var, width=46).grid(row=4, column=1, sticky='ew', pady=7)
    Label(outer, text='Number of collection cycles').grid(row=5, column=0, sticky='w', pady=7)
    Entry(outer, textvariable=cycle_var, width=46).grid(row=5, column=1, sticky='ew', pady=7)

    def browse_output():
        selected = filedialog.askdirectory(
            title='Select parent folder for Power Linearity data',
            initialdir=output_var.get() if os.path.isdir(output_var.get()) else str(Path.home()),
            mustexist=True,
            parent=root,
        )
        if selected:
            output_var.set(selected)

    Button(outer, text='Browse...', command=browse_output).grid(row=4, column=2, padx=(10, 0), pady=7)
    outer.columnconfigure(1, weight=1)

    accepted = {'value': False}

    def start_test():
        generator_address = generator_var.get().strip()
        lynx_address = lynx_var.get().strip()
        parent_folder = output_var.get().strip()
        cycle_text = cycle_var.get().strip()
        if not generator_address:
            messagebox.showerror('Invalid generator address', 'Enter a generator hostname or IP address.', parent=root)
            return
        if not lynx_address:
            messagebox.showerror('Invalid Lynx address', 'Enter a Lynx hostname or IP address.', parent=root)
            return
        try:
            cycle_count = int(cycle_text)
            if cycle_count < 1 or str(cycle_count) != cycle_text:
                raise ValueError
        except ValueError:
            messagebox.showerror('Invalid cycle count', 'Number of collection cycles must be a positive whole number.', parent=root)
            return
        if not parent_folder or not os.path.isdir(parent_folder):
            messagebox.showerror('Invalid output folder', 'Select an existing parent output folder.', parent=root)
            return
        genID = generator_address
        lynxID = lynx_address
        basePath = str(Path(parent_folder).resolve())
        globals()['genID'] = genID
        globals()['lynxID'] = lynxID
        globals()['basePath'] = basePath
        NUMBER_OF_CYCLES = cycle_count
        globals()['NUMBER_OF_CYCLES'] = NUMBER_OF_CYCLES
        accepted['value'] = True
        root.destroy()

    buttons = Frame(outer)
    buttons.grid(row=6, column=0, columnspan=3, sticky='e', pady=(22, 0))
    Button(buttons, text='Cancel', command=root.destroy, width=12).pack(side='right', padx=(8, 0))
    Button(buttons, text='Start Test', command=start_test, width=14).pack(side='right')
    root.protocol('WM_DELETE_WINDOW', root.destroy)
    root.mainloop()

    if not accepted['value']:
        raise SystemExit('Power Linearity collection cancelled before instrument connection.')


configure_instrument_addresses_before_connection()


#Connect to Lynx
factory = InstrumentFactory({'lynx': lynxID, 'generator': genID})
lynx = factory.create('lynx')
lynxSerial = str(lynx.reportSerialNumber())

#Connect to Generator and get information
Egen = factory.create('generator')
# unitNum = Egen.reportSerialNumber(1)
brandedCheck = Egen.reportSerialNumber(1)
if brandedCheck[0] == 'X': #Checks to see if the first digit of the 315 exists
    unitNumber = Egen.reportSerialNumber(2)
else:
    unitNumber = brandedCheck
unitSerial = Egen.reportSerialNumber() #Some point will need to get more info from unit
unitSerial = 2918753
Egen.setActiveControlMode(2)
Egen.setRegulationMode(6)
genPIN = Egen.reportPINNumber()

#Frequency range and center Check
Egen.setFrequencyMode(0) # 0 = fixed, 1 = variable
genCenterFreq = int(Egen.reportFixedFrequency(8)/1000)

#Check if base folder exists for data collection
outPath = str(
    create_unique_test_output_folder(
        basePath,
        'Power Linearity',
        genID,
    )
)
print('Test data folder:', outPath)

loopVar = 3

minFreq = 12882
maxFreq = 14238

# freqRange changed in order to +match the Apex spec
# 13.56MHz +/-0.005%
# Belei Note: May need to come back here and fix these, not sure yet.
freqRange = [minFreq, genCenterFreq, maxFreq] # +/-5% range
det = True
tunerCheck = 'N'



if tunerCheck == 'Y':

    tunerCOM = input('Enter Tuner COM port: [COMXX] ')
    nav = InstrumentFactory({'navigator': tunerCOM}).create('navigator')

    def motorsMoving(): # Need to be able to check Nav movement
        moving = 1
        while moving:
            move = nav.nav.create_buffer_cmd(135, '0:0', '1:8')
            moving = move[2]

    tunerFilePath = input('Where are the tuner maps stored? ')
    loadSpaces = [] #Below section was adapted from Power Profile script, not pretty
    seg = []
    nSeg = 0
    nTotal = 0
    root = Tk()
    root.attributes("-topmost", True)
    root.lift()
    root.withdraw()
    for freqMHz in freqRange:
        tfh = filedialog.askopenfile(parent=root, mode='rt', initialdir=(r'%s' %(tunerFilePath)),
                                     title='Choose 50 ohm file for %0.3fMHz' % freqMHz)
        rdr = csv.reader(tfh)
        for row in rdr:
            pos = (int(row[0]), int(row[1]))
            z = complex(float(row[2]), float(row[3]))
            seg.extend([pos, z])
        loadSpaces.append(seg)
        nSeg += 1  # increment number of segments
        nTotal += len(seg)  # add the number of loads in this segment to the total number of loads
        print('Segment %d, %d impedance points' % (nSeg, len(seg)))
        seg = []

#Ensure the tuner is set to center frequency for warm up phase!!
if tunerCheck == 'Y':
    pos = loadSpaces[1][0]
    nav.moveLoadMotor(pos[0])  # change shunt cap position to the load's specified value
    nav.moveTuneMotor(pos[1])  # change tune cap position to the load's specified value
    motorsMoving()

#HALO Check
if genPIN[20] == '1':
    H_Mode = 10
else:
    H_Mode = 1

#Accounts for HALO
maxPower = Egen.reportPowerSupplySize()
# Belei Note: commented out old minPower definition and replaced it with the 50W minimum power that Apex has.
minPower = 50
#minPower = 10*H_Mode #Generic 10W setpoint; will not necessarily work with all units
maxPower = maxPower*H_Mode

#Odd test distribution of test setpoints #FIXME Needs to auto adapt to power ranges
lowScale = numpy.linspace(minPower,100*H_Mode, 10)
midScale = numpy.linspace(200*H_Mode,1000*H_Mode, 9)
midhighScale = numpy.linspace(1100*H_Mode, maxPower, 5)
#maxScale = numpy.linspace(2000*H_Mode,maxPower,9)
#scalingMatrix =numpy.concatenate([lowScale, midScale, midhighScale, maxScale])
scalingMatrix =numpy.concatenate([lowScale, midScale, midhighScale])
powerRange = scalingMatrix #List of test setpoints in terms of minimum power
Size = loopVar*powerRange.shape[0] #accounts for number of frequencies for data storage


#Configure Generator (expand later)
Egen.setRegulationMode(7) #Delivered power mode
Egen.setPulsingFrequency(0) #Ensure pulsing is disabled

try:
    # Warm up period
    Egen.rfOFF() #Prevents RF ON x2 case
    Egen.setFixedFrequency(genCenterFreq)
    Egen.setPowerSetpoint(maxPower)
    time.sleep(0.5)
    Egen.rfON()
    time.sleep(WARMUP_TIME_S)
    Egen.rfOFF()
except Exception as warmupFail:
    e = sys.exc_info()
    Egen.rfOFF()
    Egen.close()
    print('\nError encountered during warmup. Objects have been closed')
    print('Error message type: ', e[0])
    print('Error Message: ', e[1])
    print('Error on line: ', e[2].tb_lineno)
    error_log = _write_error_log('Warmup failure', e, outPath)
    _show_error_and_pause(
        'Power Linearity warmup failed',
        f'{type(warmupFail).__name__}: {warmupFail}\n\nFull traceback saved to:\n{error_log}',
    )

now = datetime.datetime.now()
dt_string = now.strftime("%m.%d.%Y_%H.%M.%S")

#Belei commenting out below for test
'''
#Define data paths and folder names
dataPath = os.path.join(outPath, dt_string)

#Write and save data to Excel sheet
fh = open(dataPath, 'w', newline='')
'''
dataPath = os.path.join(outPath, f"{dt_string}.csv")
fh = open(dataPath, 'w', newline='', encoding='utf-8')

writer = csv.writer(fh, 'excel')
header = ['Measure_Frequency', 'Gen_Forward_Power', 'Gen_Reflected_Power', 'Gen_Delivered_Power', 'Lynx_Forward_Power',
          'Lynx_Reflected_Power', 'Lynx_Delivered_Power', 'Measured_R', 'Measured_X', 'Lynx_V', 'Lynx_I']
writer.writerow(header)
settingsPath = os.path.join(outPath, "Power_Linearity_Run_Settings.csv")
with open(settingsPath, "w", newline="", encoding="utf-8") as settings_file:
    settings_writer = csv.writer(settings_file, lineterminator="\n")
    settings_writer.writerow(["setting", "value"])
    settings_writer.writerows([
        ["collection_timestamp", datetime.datetime.now().isoformat(timespec="seconds")],
        ["generator_hostname", genID],
        ["generator_serial", unitSerial],
        ["generator_unit_number", unitNumber],
        ["generator_pin", genPIN],
        ["lynx_hostname", lynxID],
        ["lynx_serial", lynxSerial],
        ["halo_mode_multiplier", H_Mode],
        ["generator_center_frequency_kHz", genCenterFreq],
        ["minimum_frequency_kHz", minFreq],
        ["maximum_frequency_kHz", maxFreq],
        ["test_frequencies_kHz", ",".join(str(value) for value in freqRange)],
        ["minimum_power_W", minPower / H_Mode],
        ["maximum_power_W", maxPower / H_Mode],
        ["warmup_time_s", WARMUP_TIME_S],
        ["number_of_collection_cycles", NUMBER_OF_CYCLES],
        ["linearity_low_power_transition_W", 300],
        ["linearity_low_power_absolute_tolerance_W", 3],
        ["linearity_high_power_tolerance_percent", 1],
        ["measurement_csv", dataPath],
    ])
    settings_file.flush()
    os.fsync(settings_file.fileno())

# det = True
# while det is True:
#     startTest = input('Start Test [Y/N]?')
#     startTest = startTest.upper()
#     if startTest == 'Y':
#         break
#     elif startTest == 'N':
#         Egen.close()
#         lynx.close()
#         exit()
#     else:
#         print('Invalid input. Try Again')

try:
    #Run Test
    for cycle_number in range(1, NUMBER_OF_CYCLES + 1):
        print(f'Starting Power Linearity collection cycle {cycle_number} of {NUMBER_OF_CYCLES}')
        index = 0  # Need indices for segment loop if exists
        for i in freqRange:
            lynx.setFreq(i)
            Egen.setFixedFrequency(int(i)) #Set generator to test frequency
            # if tunerCheck == 'Y':
            #     pos = loadSpaces[index][0]
            #     nav.moveLoadMotor(pos[0])  # change shunt cap position to the load's specified value
            #     nav.moveTuneMotor(pos[1])  # change tune cap position to the load's specified value
            #     motorsMoving()
            #     index =+ 1 #Increment index
            time.sleep(2.5)
            print(f'Cycle {cycle_number} of {NUMBER_OF_CYCLES}; Testing Frequency [kHz] {i}')

            for ii in powerRange:
                # Belei Note: I had to change the min Power to 50W because that is as low as the Apex is capable of going.
                data = []
                Egen.setPowerSetpoint(int(ii))
                Egen.rfON()
                print(f'Cycle {cycle_number} of {NUMBER_OF_CYCLES}; Testing Power Level [W] {ii/H_Mode}')
                time.sleep(.1) #Allow RF to stabilize

                #Gather Power data
                data.append(lynx.getFreq())
                data.append(Egen.reportForwardPower())
                data.append(Egen.reportReflectedPower())
                data.append(Egen.reportDeliveredPower())
                data.extend(lynx.readUnit222())
                Egen.rfOFF()
                time.sleep(0.25)
                writer.writerow(data)
                fh.flush()
                os.fsync(fh)
        print(f'Completed Power Linearity collection cycle {cycle_number} of {NUMBER_OF_CYCLES}')
except Exception as testFail:
    e = sys.exc_info()
    print('\nError encountered during test. Objects have been closed '
          'and data written for review at %s' % outPath)
    print('Error message type: ', e[0])
    print('Error Message: ', e[1])
    print('Error on line: ', e[2].tb_lineno)
    error_log = _write_error_log('Collection-loop failure', e, outPath)
    _show_error_and_pause(
        'Power Linearity collection failed',
        f'{type(testFail).__name__}: {testFail}\n\nFull traceback saved to:\n{error_log}',
    )

finally:
    # Belei Note: As of 2/2 2:14PM I am getting pulled into this statement
    # Belei Note: 2:24PM I realized that this is normal behavior. But for some reason I am still getting a weird file type as an output
    # My thinking is that either this weird file type is a result of some bug in the way the data is being saved, or that I am missing hardware needed to run Power Linearity
    Egen.rfOFF()
    Egen.close()
    # Belei Notes: Commented writer.writerow() out because getting compile time error where it needs argument. Not sure whether it needs "data" or should be empty
    #writer.writerow()
    fh.flush()
    os.fsync(fh)
    fh.close()

# Tuner close check
if tunerCheck == 'Y':
    nav.close()
print('Data Log Saved')
lynx.close()
print('Test Complete')
print('Data saved to: ', outPath)
print('Run settings saved to: ', settingsPath)
print('Collection-only script complete. Analyze with PMNT_Power_Linearity_Analysis_GUI.py')
