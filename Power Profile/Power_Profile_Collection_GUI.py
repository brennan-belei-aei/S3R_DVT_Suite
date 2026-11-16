import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import suite_common.bootstrap  # noqa: F401
from suite_common.bench import bench_default
from suite_common.output_folders import create_unique_test_output_folder

import AE_Com_Module
import Lynx

from tkinter import filedialog, ttk, messagebox
from datetime import datetime
import csv
import math
# from lynx import *
from time import ctime, sleep, time
import os
from tkinter import *
import AECommandsUniversal
import AECommands

# COMMS SETUP
navPort = bench_default("navigator")
genIP = bench_default("generator")
lynxPort = bench_default("lynx")
pMeterStr = 'Lynx'
TEST_FOLDER_NAME = 'Power Profile'
STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER = str(Path.home())
OUTPUT_PARENT_FOLDER = (
    os.environ.get(
        'PARAMOUNT_COLLECTION_PARENT_FOLDER',
        STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER,
    ).strip()
    or STANDALONE_DEFAULT_OUTPUT_PARENT_FOLDER
)
TUNER_MAP_PARENT = os.environ.get('PARAMOUNT_TUNER_MAP_PARENT', r'C:\TunerCal')
LOW_FREQUENCY_KHZ = 12882
CENTER_FREQUENCY_KHZ = 13560
HIGH_FREQUENCY_KHZ = 14238
TUNER_MOVE_SETTLE_S = 1.0
SHOW_COMPLETION_DIALOG = False

# Original test defaults are defined before the GUI so the window can initialize
# without opening instruments. The later legacy declarations are suppressed.
setpoint = 3000
maxVSWR = 5 * 1.05
stabiliseTolPercent = 0.1
stabiliseTimeoutS = 180
stableSampleTimeS = 0.25
minTimeWaitS = 2
percentAboveSpec = 0
segStart = 1
segImpStart = 1
skipFirstLoadInSegment = False
Z0 = float(50)
rhoMargin = 1.1

# ==========================================================
# GRAPHICAL POWER-PROFILE CONFIGURATION
# ==========================================================
def validate_gui_global_definitions():
    required = (
        'navPort', 'genIP', 'lynxPort', 'pMeterStr',
        'setpoint', 'maxVSWR', 'stabiliseTolPercent', 'stabiliseTimeoutS',
        'stableSampleTimeS', 'minTimeWaitS', 'percentAboveSpec',
        'segStart', 'segImpStart', 'skipFirstLoadInSegment', 'Z0',
        'rhoMargin', 'TEST_FOLDER_NAME', 'OUTPUT_PARENT_FOLDER',
        'TUNER_MAP_PARENT', 'LOW_FREQUENCY_KHZ', 'CENTER_FREQUENCY_KHZ',
        'HIGH_FREQUENCY_KHZ', 'TUNER_MOVE_SETTLE_S',
    )
    missing = [name for name in required if name not in globals()]
    if missing:
        raise RuntimeError(
            'Required Power Profile GUI settings are not defined: '
            + ', '.join(missing)
        )


def configure_power_profile_with_gui():
    global navPort, genIP, lynxPort, pMeterStr
    global setpoint, maxVSWR, stabiliseTolPercent, stabiliseTimeoutS
    global stableSampleTimeS, minTimeWaitS, percentAboveSpec
    global segStart, segImpStart, skipFirstLoadInSegment, Z0, rhoMargin
    global OUTPUT_PARENT_FOLDER, TUNER_MAP_PARENT
    global LOW_FREQUENCY_KHZ, CENTER_FREQUENCY_KHZ, HIGH_FREQUENCY_KHZ
    global TUNER_MOVE_SETTLE_S, SHOW_COMPLETION_DIALOG

    root = Tk()
    root.title('Paramount Power Profile Setup')
    root.geometry('970x740')
    root.minsize(850, 650)

    style = ttk.Style(root)
    try:
        style.theme_use('vista')
    except TclError:
        pass
    style.configure('Header.TLabel', font=('Segoe UI', 15, 'bold'))
    style.configure('Section.TLabel', font=('Segoe UI', 10, 'bold'))
    style.configure('Start.TButton', font=('Segoe UI', 11, 'bold'), padding=(14, 8))

    outer = ttk.Frame(root, padding=14)
    outer.pack(fill='both', expand=True)
    ttk.Label(outer, text='Paramount Power Profile', style='Header.TLabel').pack(anchor='w')
    ttk.Label(
        outer,
        text=(
            'Configure the DUT, Lynx, Navigator, tuner maps, RF setpoint, VSWR and '
            'segmentation limits, stability rules, and output folder. The existing '
            'map segmentation and load-by-load measurement engine is preserved.'
        ),
        wraplength=910,
    ).pack(anchor='w', pady=(2, 10))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill='both', expand=True)
    instruments_tab = ttk.Frame(notebook, padding=12)
    test_tab = ttk.Frame(notebook, padding=12)
    load_tab = ttk.Frame(notebook, padding=12)
    stability_tab = ttk.Frame(notebook, padding=12)
    output_tab = ttk.Frame(notebook, padding=12)
    notebook.add(instruments_tab, text='Instruments')
    notebook.add(test_tab, text='RF Test')
    notebook.add(load_tab, text='Load-Space Processing')
    notebook.add(stability_tab, text='Stability')
    notebook.add(output_tab, text='Output')

    variables = {}

    def add_entry(parent, row, label, key, default, help_text='', width=40):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky='w', padx=(0, 10), pady=5
        )
        variable = StringVar(value=str(default))
        variables[key] = variable
        ttk.Entry(parent, textvariable=variable, width=width).grid(
            row=row, column=1, sticky='ew', pady=5
        )
        if help_text:
            ttk.Label(
                parent, text=help_text, foreground='#555555', wraplength=360
            ).grid(row=row, column=2, sticky='w', padx=(10, 0), pady=5)

    add_entry(instruments_tab, 0, 'DUT IP / hostname', 'gen_ip', genIP, width=50)
    add_entry(instruments_tab, 1, 'Lynx hostname / port', 'lynx_port', lynxPort, width=50)
    add_entry(instruments_tab, 2, 'Navigator COM port', 'nav_port', navPort)
    add_entry(instruments_tab, 3, 'Power meter label', 'meter_label', pMeterStr)

    add_entry(test_tab, 0, 'Base RF setpoint (W)', 'setpoint', setpoint,
              'HALO mode multiplies this by 10, matching the original behavior.')
    add_entry(test_tab, 1, 'Low frequency (kHz)', 'low_freq', LOW_FREQUENCY_KHZ)
    add_entry(test_tab, 2, 'Center frequency (kHz)', 'center_freq', CENTER_FREQUENCY_KHZ)
    add_entry(test_tab, 3, 'High frequency (kHz)', 'high_freq', HIGH_FREQUENCY_KHZ)
    add_entry(test_tab, 4, 'Maximum VSWR including tolerance', 'max_vswr', maxVSWR)
    add_entry(test_tab, 5, 'Percent above reflected-power spec', 'above_spec', percentAboveSpec)
    add_entry(test_tab, 6, 'Tuner move settle time (s)', 'move_settle', TUNER_MOVE_SETTLE_S)

    add_entry(load_tab, 0, 'Starting segment number', 'seg_start', segStart)
    add_entry(load_tab, 1, 'Starting impedance index', 'imp_start', segImpStart)
    add_entry(load_tab, 2, 'Characteristic impedance Z0 (ohms)', 'z0', Z0)
    add_entry(load_tab, 3, 'Rho segment-break margin', 'rho_margin', rhoMargin)
    skip_first = BooleanVar(value=skipFirstLoadInSegment)
    ttk.Checkbutton(
        load_tab,
        text='Skip first load in each segment after segment 1',
        variable=skip_first,
    ).grid(row=4, column=0, columnspan=3, sticky='w', pady=5)
    map_parent = StringVar(value=TUNER_MAP_PARENT)
    ttk.Label(load_tab, text='Tuner map parent folder').grid(
        row=5, column=0, sticky='w', padx=(0, 10), pady=5
    )
    ttk.Entry(load_tab, textvariable=map_parent, width=58).grid(
        row=5, column=1, sticky='ew', pady=5
    )
    load_tab.columnconfigure(1, weight=1)

    def browse_map_parent():
        initial = map_parent.get().strip()
        if not os.path.isdir(initial):
            initial = str(Path.home())
        selected = filedialog.askdirectory(
            title='Select tuner-map parent folder', initialdir=initial,
            parent=root, mustexist=True,
        )
        if selected:
            map_parent.set(selected)

    ttk.Button(load_tab, text='Browse...', command=browse_map_parent).grid(
        row=5, column=2, padx=(10, 0), pady=5
    )
    ttk.Label(
        load_tab,
        text=(
            'After Start Test, the existing workflow still asks for one tuner-loss map '
            'for each configured frequency and applies the original VSWR and rho-based '
            'segmentation rules.'
        ),
        foreground='#555555', wraplength=800,
    ).grid(row=6, column=0, columnspan=3, sticky='w', pady=(14, 0))

    add_entry(stability_tab, 0, 'Delivered-power tolerance (%)', 'tol_percent', stabiliseTolPercent)
    add_entry(stability_tab, 1, 'Stabilization timeout (s)', 'timeout', stabiliseTimeoutS)
    add_entry(stability_tab, 2, 'Power sample interval (s)', 'sample_time', stableSampleTimeS)
    add_entry(stability_tab, 3, 'Minimum dwell before stable exit (s)', 'min_wait', minTimeWaitS)

    output_parent = StringVar(value=OUTPUT_PARENT_FOLDER)
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
            title='Select parent folder for Power Profile data', initialdir=initial,
            parent=root, mustexist=True,
        )
        if selected:
            output_parent.set(selected)

    ttk.Button(output_tab, text='Browse...', command=browse_output).grid(
        row=0, column=2, padx=(10, 0), pady=5
    )
    ttk.Label(
        output_tab,
        text=(
            'A run folder will be created as: YYYY-MM-DD_HH-MM-SS - Power Profile '
            '- Generator Name. Exact same-second collisions use Run 2, Run 3, etc.'
        ),
        foreground='#555555', wraplength=800,
    ).grid(row=1, column=0, columnspan=3, sticky='w', pady=(12, 0))

    result = {'accepted': False}

    def number(key, label, minimum=None, integer=False):
        try:
            value = float(variables[key].get().strip())
        except ValueError as error:
            raise ValueError(f'{label} must be numeric.') from error
        if not math.isfinite(value):
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
        global navPort, genIP, lynxPort, pMeterStr
        global setpoint, maxVSWR, stabiliseTolPercent, stabiliseTimeoutS
        global stableSampleTimeS, minTimeWaitS, percentAboveSpec
        global segStart, segImpStart, skipFirstLoadInSegment, Z0, rhoMargin
        global OUTPUT_PARENT_FOLDER, TUNER_MAP_PARENT
        global LOW_FREQUENCY_KHZ, CENTER_FREQUENCY_KHZ, HIGH_FREQUENCY_KHZ
        global TUNER_MOVE_SETTLE_S
        try:
            selected_gen = variables['gen_ip'].get().strip()
            selected_lynx = variables['lynx_port'].get().strip()
            selected_nav = variables['nav_port'].get().strip()
            if not selected_gen or not selected_lynx or not selected_nav:
                raise ValueError('DUT, Lynx, and Navigator addresses cannot be blank.')
            low = number('low_freq', 'Low frequency', 1, integer=True)
            center = number('center_freq', 'Center frequency', 1, integer=True)
            high = number('high_freq', 'High frequency', 1, integer=True)
            if not low < center < high:
                raise ValueError('Frequencies must satisfy low < center < high.')
            selected_map_parent = Path(map_parent.get().strip()).resolve()
            selected_output = Path(output_parent.get().strip()).resolve()
            if not selected_map_parent.exists() or not selected_map_parent.is_dir():
                raise ValueError('Select an existing tuner-map parent folder.')
            if not selected_output.exists() or not selected_output.is_dir():
                raise ValueError('Select an existing parent output folder.')

            genIP = selected_gen
            lynxPort = selected_lynx
            navPort = selected_nav
            pMeterStr = variables['meter_label'].get().strip() or 'Lynx'
            setpoint = number('setpoint', 'RF setpoint', 0)
            maxVSWR = number('max_vswr', 'Maximum VSWR', 1.000001)
            percentAboveSpec = number('above_spec', 'Percent above spec', 0)
            LOW_FREQUENCY_KHZ = low
            CENTER_FREQUENCY_KHZ = center
            HIGH_FREQUENCY_KHZ = high
            TUNER_MOVE_SETTLE_S = number('move_settle', 'Tuner settle time', 0)
            segStart = number('seg_start', 'Starting segment', 1, integer=True)
            segImpStart = number('imp_start', 'Starting impedance index', 1, integer=True)
            Z0 = number('z0', 'Characteristic impedance', 0.000001)
            rhoMargin = number('rho_margin', 'Rho margin', 0)
            skipFirstLoadInSegment = bool(skip_first.get())
            stabiliseTolPercent = number('tol_percent', 'Tolerance', 0)
            stabiliseTimeoutS = number('timeout', 'Timeout', 0.000001)
            stableSampleTimeS = number('sample_time', 'Sample interval', 0.000001)
            minTimeWaitS = number('min_wait', 'Minimum dwell', 0)
            TUNER_MAP_PARENT = str(selected_map_parent)
            OUTPUT_PARENT_FOLDER = str(selected_output)

            result['accepted'] = True
            root.destroy()
        except Exception as error:
            messagebox.showerror('Invalid Power Profile settings', str(error), parent=root)

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
        raise SystemExit('Power Profile cancelled before instrument connection.')


validate_gui_global_definitions()
configure_power_profile_with_gui()

lynx = Lynx.lynx(lynxPort)
lynxSerial = lynx.reportSerialNumber()

# SETPOINT/OTHER SETUP
#print('Type the desired setpoint in Watts and hit ENTER')
# setpoint configured by GUI
#print('Type the desired maximum VSWR and hit ENTER')
# maxVSWR configured by GUI

# setup navigator
nav = AECommands.navigator(navPort)

# setup paramount
Egen = AECommandsUniversal.generator(genIP)
# Egen.unlock()
unitSerial = Egen.reportSerialNumber()
Egen.rfOFF()
if str(Egen.reportPINNumber())[20] == '1':
    print('HALO MODE ON')
    halo = True
    setpoint *= 10
else:
    halo = False

#Set Generator Limits based on Generator reported Information
pRflLimitW = Egen.reportUserReflectedLimit(1)

lowFreq = LOW_FREQUENCY_KHZ #int(Egen.reportFixedFrequency(3)/1000)
centerFreq = CENTER_FREQUENCY_KHZ #int(Egen.reportFixedFrequency(8)/1000)
hiFreq = HIGH_FREQUENCY_KHZ #int(Egen.reportFixedFrequency(2)/1000)

freqsMHz = [lowFreq, centerFreq, hiFreq] #Frequencies to be tested


# POWER/MEASUREMENT SETUP
# stabiliseTolPercent configured by GUI
# stabiliseTimeoutS configured by GUI
# stableSampleTimeS configured by GUI
# minTimeWaitS configured by GUI
# pRflLimitW = 400  # reflected power limit in W #FIXME Needs to pull auto from the unit (line 124)
# percentAboveSpec configured by GUI

# IMPEDANCE SETUP
# segStart configured by GUI
# segImpStart configured by GUI
# skipFirstLoadInSegment configured by GUI
# Z0 configured by GUI
# rhoMargin configured by GUI

# ///////////////////////////// START /////////////////////////////////////////////
# choose and process map file
root = Tk()
root.attributes("-topmost", True)
root.lift()
root.withdraw()
loadSpaces = []  # list of load space for each frequency, each load space is a list of segments
nTotal = 0  # product of number of setpoints and loads
for freqMHz in freqsMHz:  # for each frequency to be tested at
    tfh = filedialog.askopenfile(parent=root, mode='rt', initialdir=TUNER_MAP_PARENT,
                                 title='Choose tuner losses file for %0.3fMHz' % freqMHz)
    rdr = csv.reader(tfh)
    segments = []  # list of segments, each segment is list of ((pos), rho, eff) tuples
    rho = 5  # initialize reflection coefficient to very high value
    seg = []
    nSeg = 0
    count = 1  # index for load number
    print('\nFreq = %0.3fMHz' % freqMHz)
    for row in rdr:  # for each row in the map file (each impedance position)
        row = [float(x) for x in row]  # generating float values for each value in the row
        pos = (int(row[0]), int(row[1]))  # cap positions for this load
        z = complex(row[2], row[3])  # impedance at this load
        prevRho = rho  # storing the current load for load-to-load comparison with next load
        rho = (z - Z0) / (z + Z0)  # reflection coefficient at this load
        eff = float(row[4])  # efficiency at this load
        if abs(prevRho - rho) > rhoMargin:  # if the reflection coefficient difference is greater than the margin
            if len(seg):  # if the segment is not zero loads long
                segments.append(seg)  # add this segment to the list of segments
                nSeg += 1  # increment number of existing segments
                nTotal += len(seg)  # add the number of loads in this segment to the total number of loads
                print('Segment %d, %d impedance points' % (nSeg, len(seg)))
                seg = []  # create new empty segment
        if abs(rho) < (maxVSWR - 1) / (maxVSWR + 1):  # if this load is within the desired VSWR range
            seg.append((pos, rho, eff))  # add it to the segment
        else:
            print('Skipped load number ' + str(count) + ' at ' + str(row[2]) + ' + (' + str(
                row[3]) + ')i due to VSWR limit')
        count += 1  # increment load index
    segments.append(seg)  # add the final segment to list of segments
    nSeg += 1  # increment number of segments
    nTotal += len(seg)  # add the number of loads in this segment to the total number of loads
    print('Segment %d, %d impedance points' % (nSeg, len(seg)))
    loadSpaces.append(segments)  # add the list of segments for this frequency as a loadSpace

# Generator Setting for Test
Egen.setActiveControlMode(2)  # Host mode
Egen.setRegulationMode(7)  # delivered mode
Egen.setFrequencyMode(0)
Egen.setPowerSetpoint(0)
# Egen.reportPulsingConfiguration(7, 0) #If in explicit enable mode UNVERIFIED
Egen.setPulsingFrequency(0) #if implicit pulsing mode is set, this turns pulsing off
Egen.setUnlockedSubcommand(15, 0) # Sets ignition mode to fixed

brandedCheck = Egen.reportSerialNumber(1)

if brandedCheck[0] == 'X': #Checks to see if the first digit of the 315 exists
    part_num = Egen.reportSerialNumber(2)
else:
    part_num = brandedCheck

# SET UP OUTPATH
now = datetime.now()  # capture the current date and time
dt_string = now.strftime("%m.%d.%Y_%H.%M.%S")  # format the date and time as a string
outPath = str(
    create_unique_test_output_folder(
        OUTPUT_PARENT_FOLDER,
        TEST_FOLDER_NAME,
        genIP,
    )
)
print('Test data folder:', outPath)
if len(freqsMHz) == 1:  # if there is only 1 frequency being tested
    freqStr = str(freqsMHz[0])  # frequency string is just the one frequency
else:  # if multiple frequencies are being tested
    freqStr = 'multi_freq'
outFname = os.path.join(outPath, '%s_%s_%s_%s.csv' % (freqStr, str(unitSerial), str(lynxSerial), dt_string))  # format file name
fh = open(outFname, 'wt')  # create the output file and open it
writer = csv.writer(fh, 'excel', lineterminator='\n')  # create the writer object for the file
header = ['loadIndex', 'shunt', 'series', 'zReal', 'zImag', 'freq', 'DCVoltage', 'DCCurrent', 'Setpoint', 'pDelLynx',
          'pDelGen', 'pError(%)', 'pFwdGen', 'pRflGen', 'zGenReal', 'zGenImag', 'pDiss', 'dwellTime', 'DCTherm', 'RFTherm',
          'pInput']
writer.writerow(header)

def readPowerMeter():
    """ return power in W """
    return lynx.readUnit222()[2]  # delivered power on lynx

def motorsMoving():
    moving = 1
    while moving:
        move = nav.nav.create_buffer_cmd(135, '0:0', '1:8')
        moving = move[2]


stabiliseTol = stabiliseTolPercent / 100.0  # convert stabilized percent to decimal form

# ////////////////////////// START TEST /////////////////////////////////////
print('Press ENTER to begin testing')
reply = sys.stdin.readline().strip()
startTime = time()  # document the start time
loads_complete = 0  # initialize number of loads completed to zero
try:
    for freqIndex in range(len(freqsMHz)):  # for each frequency
        freqMHz = freqsMHz[freqIndex]  # this frequency is this frequency (duh)
        segments = loadSpaces[freqIndex]  # the current list of segments is the frequency index in the loadSpaces list
        Egen.setFixedFrequency(int(freqMHz))  # set generator to the current frequency
        lynx.setFreq(freqMHz)  # set Lynx to the current frequency
        for segmentIndex in range(len(segments)):  # for each segment for this frequency
            if freqIndex and (segStart > segmentIndex + 1):  # if frequency index > 0 and segStart is after this segment
                print('skipping segment %d' % segmentIndex)  # skip this segment
                nTotal -= len(segments[segmentIndex])  # remove the loads in this segment from total number of loads
                continue  # on to the next segment (back to top of for loop)
            seg = segments[segmentIndex]  # 'seg' is the current segment
            for iZ in range(len(seg)):  # for each load in this segment
                if freqIndex and (segStart == segmentIndex + 1) and (segImpStart > iZ + 1):
                    print('skipping load %d' % (iZ + 1))  # skip load if supposed to be skipped
                    nTotal -= 1  # decrement total number of loads
                    continue  # on to next load in segment
                if skipFirstLoadInSegment and (iZ + 1) == 1 and (segmentIndex + 1) > 1:
                    print('Skipping first load in segment')  # skip first load if supposed to be skipped
                    nTotal -= 1  # decrement total number of loads
                    continue  # on to next load in segment
                pos, rho, eff = seg[iZ]  # assigning cap positions, refl coeff, and efficiency to these variables
                print('Freq=%0.3fMHz Segment=%d LoadIndex=%d' % (
                    freqMHz, segmentIndex + 1, iZ + 1), 'Moving to rho = (%0.2f%+0.2fj) (|%0.2f|)' % (
                          rho.real, rho.imag, abs(rho))),
                Egen.rfOFF()  # RF off during cap position change
                nav.moveLoadMotor(pos[0])  # change shunt cap position to the load's specified value
                nav.moveTuneMotor(pos[1])  # change tune cap position to the load's specified value
                motorsMoving()  # wait until cap positions are fully updated #FIXME Updated to motors moving
                sleep(TUNER_MOVE_SETTLE_S)
                Egen.setPowerSetpoint(0)  # RF will turn on at 0W setpoint
                Egen.rfON()
                Egen.setPowerSetpoint(setpoint)  # change to desired setpoint
                tic = time()  # capture current time
                stable = False  # initialize to not stable (when stabilized, measurement is taken)
                prevPdel = 1e6  # initialize delivered power to very high value for first comparison
                sampleTime = 0  # initialize sample time
                while 1:  # infinite loop that will break when power has stabilized
                    if (time() - sampleTime) > stableSampleTimeS:  # if minimum time for sampling has passed
                        pDel = readPowerMeter()  # sample the lynx for delivered power reading
                        sampleTime = time()  # update sample time
                        if (pDel == prevPdel) or (  # if delivered power is stable (within 0.001W)
                                (pDel != 0) and (abs(pDel - prevPdel) / float(pDel) <= stabiliseTol)):
                            stable = True
                        prevPdel = pDel  # update previous delivered power as current delivered power
                    elapsed = time() - tic  # update time
                    if elapsed > stabiliseTimeoutS:  # if timeout limit is reached before power stabilized
                        print('Power meter stabilise timeout!')
                    if stable and (elapsed > minTimeWaitS):  # if stable and minimum time requirement met
                        break  # end stablizing loop
                stableTime = time() - tic  # time from beginning to end of stablizing phase
                # BELEI Note:
                # commenting out pDElMtr variable for time being for debug purposes. Was throwing divide by zero error.
                #pDelMtr = readPowerMeter() / eff  # delivered power is read from lynx and divided by efficiency
                pDelMtr = 0
                snap = Egen.reportGeneratorSnapshotData()  # snapshot of statistics from the generator
                if pDelMtr != 0:
                    pError = abs((snap[2] - pDelMtr) / pDelMtr) * 100  # % error between deliv power (gen vs lynx)
                else:
                    pError = 0
                #DCinfo = Egen.reportSPIBusADCData(4)  # pull DC Section ADC data
                #DCCurrent = DCinfo[2] * 40.8 / 4096  # format and scale DC current from DC section ADC info
                #DCVoltage = DCinfo[0] * 201.4 / 4096  # format and scale DC voltage from DC section ADC info
                DCCurrent = Egen.readIDC()
                DCVoltage = Egen.readVDC()
                pDissGen = Egen.reportPowerDisspiation()  # get the dissipated power from the generator
                # samples += 1    # increment sample count
                pIn = DCVoltage * DCCurrent  # calculate input power
                pDiss = pIn - snap[2]  # dissipated power is input power minus delivered power
                z = (1 + rho) / (1 - rho) * Z0  # calculate impedance from reflection coefficients and char imp(50)
                DCTherm = Egen.reportColdPlateTemperature(1)  # pull DC thermistor value
                RFTherm = Egen.reportColdPlateTemperature(2)  # pull RF thermistor value
                row = [iZ + 1, pos[0], pos[1], z.real, z.imag, freqMHz * 1e6,
                       DCVoltage, DCCurrent, snap[3], pDelMtr, snap[2], pError, snap[0], snap[1], snap[4],
                       snap[5], pDissGen, stableTime, DCTherm, RFTherm, pIn]  # create row of data
                writer.writerow(row)  # write row of data
                loads_complete += 1  # increment number of loads completed
                t = time()  # capture current time
                avgTime = (t - startTime) / loads_complete  # calculate average time per load
                print('%0.1f%% complete, avg %0.1fs per point, estimated end time: %s' % (
                    float(loads_complete) / nTotal * 100, avgTime, ctime(t + avgTime * (nTotal - loads_complete))))
                Egen.rfOFF()
finally:
    print('Turning everything off ...', )
    Egen.reportFaultOrWarningCodeList(1)
    Egen.rfOFF()
    Egen.setPowerSetpoint(0)
    fh.flush()
    os.fsync(fh)
    fh.close()
    print('DONE, total time %0.1fmin' % ((time() - startTime) / 60))