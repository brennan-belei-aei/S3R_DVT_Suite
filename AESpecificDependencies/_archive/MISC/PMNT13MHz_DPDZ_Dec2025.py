
import AE_Com_Module
import Lynx

# pathAdd = [r'C:\pymodules']
import sys

# for path in pathAdd:
#     sys.path.append(path)
from tkinter import filedialog
from datetime import datetime
import csv
# from lynx import *
from time import *
import os
from tkinter import *
import AE_Commands_PMNT_HF_1XX
import AECommands

#Setup for Hobbs computer july 2024 - 2013 P+ Low Power escalation


navPort = 'com15'
genIP = 'PMNT03C763'
lynxPort = 'STND03EC29'

lynxBaud = 115200
pMeterStr = 'Lynx'
lynx = Lynx.lynx(lynxPort)
lynxSerial = lynx.reportSerialNumber()

# setup navigator
nav = AECommands.navigator(navPort)
# setup paramount
Egen = AE_Commands_PMNT_HF_1XX.generator(genIP)
# Egen.unlock()
unitSerial = Egen.reportSerialNumber()
Egen.rfOFF()

sethigh = True
while sethigh == True:
    # SETPOINT/OTHER SETUP
    print('Type the desired setpoint in Watts and hit ENTER')
    setpoint = int(sys.stdin.readline().strip())

    # SETPOINT/VSWR SETUP
    if setpoint > int(Egen.reportPowerSupplySize()/4):
        print('Setpoint is too High')
    else:
        sethigh = False

maxVSWR = 5  # maximum VSWR will have a tolerance of 5%
print('Max VSWR set to %0.1f' % (maxVSWR) )

if str(Egen.reportPINNumber())[20] == '1':
    print('HALO MODE ON')
    halo = True
    setpoint *= 10
else:
    halo = False

#Set Generator Limits based on Generator reported Information
pRflLimitW = Egen.reportUserReflectedLimit(1)
lowFreq = int(Egen.reportFixedFrequency(3)/1000)
centerFreq = int(Egen.reportFixedFrequency(8)/1000)
hiFreq = int(Egen.reportFixedFrequency(2)/1000)
freqsMHz = [lowFreq, centerFreq, hiFreq] #Frequencies to be tested

# POWER/MEASUREMENT SETUP
stabiliseTolPercent = 0.1  # two readings within 0.1% of each other is considered "stable"
stabiliseTimeoutS = 180  # timeout for power meter to stabilise in seconds
stableSampleTimeS = 0.25
minTimeWaitS = 1  # minimum time to wait at each setpoint
# pRflLimitW = 400  # reflected power limit in W #FIXME Needs to pull auto from the unit (line 124)
percentAboveSpec = 0  # percent to go above spec

# IMPEDANCE SETUP
segStart = 1  # segment to start at, will only apply to first listed frequency
segImpStart = 1  # impedance index within segment to start at, only applies to first listed frequency
skipFirstLoadInSegment = False
Z0 = float(50)  # Charcateristic impedance is 50 ohms
rhoMargin = 1.1

# ///////////////////////////// START /////////////////////////////////////////////
# choose and process map file
root = Tk()
root.attributes("-topmost", True)
root.lift()
root.withdraw()
loadSpaces = []  # list of load space for each frequency, each load space is a list of segments
nTotal = 0  # product of number of setpoints and loads
for freqMHz in freqsMHz:  # for each frequency to be tested at
    tfh = filedialog.askopenfile(parent=root, mode='rt', initialdir=r'C:\TunerCal',
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
# Egen.setPulsingFrequency(0) #if implicit pulsing mode is set, this turns pulsing off
Egen.setUnlockedSubcommand(15, 0) # Sets ignition mode to fixed

brandedCheck = Egen.reportSerialNumber(1)

if brandedCheck[0] == 'X': #Checks to see if the first digit of the 315 exists
    part_num = Egen.reportSerialNumber(2)
else:
    part_num = brandedCheck

# SET UP OUTPATH
now = datetime.now()  # capture the current date and time
dt_string = now.strftime("%m.%d.%Y_%H.%M.%S")  # format the date and time as a string
outPath = r'C:\Users\s.sivaravichandran\PycharmProjects\PythonProject\DPDZ2013_Dec12\%s\%d' %(part_num, unitSerial)
if not os.path.exists(outPath):  # if the outpath doesnt already exist
    os.makedirs(outPath)  # create the outpath
if len(freqsMHz) == 1:  # if there is only 1 frequency being tested
    freqStr = str(freqsMHz[0])  # frequency string is just the one frequency
else:  # if multiple frequencies are being tested
    freqStr = 'multi_freq'
if halo == True:
    outFname = os.path.join(outPath, '%s_%s_%s_%s_%s.csv' % (freqStr, str(setpoint/10), str(unitSerial), str(lynxSerial), dt_string))  # format file name
else:
    outFname = os.path.join(outPath, '%s_%s_%s_%s_%s.csv' % (freqStr, str(setpoint), str(unitSerial), str(lynxSerial), dt_string))  # format file name
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

        for i in range(len(segments[0])):
            if abs(segments[0][i][1]) < 0.025:
                pos, rho, eff = segments[0][i]  # assigning cap positions, refl coeff, and efficiency to these variables
                break

        print(pos)
        nav.moveLoadMotor(pos[0])  # change shunt cap position to the load's specified value
        nav.moveTuneMotor(pos[1])  # change tune cap position to the load's specified value
        motorsMoving()  # wait until cap positions are fully updated #FIXME Updated to motors moving

        Egen.selectOpenClosedLoop(1)
        Egen.setPowerSetpoint(setpoint)
        sleep(0.2)
        Egen.rfON()
        sleep(0.5)
        DCrailDAC = 17770 #Egen.reportDACState(1, 0)
        RFBiasDAC = 48139 #Egen.reportDACState(0, 1)
        Egen.rfOFF()
        sleep(0.5)

        Egen.selectOpenClosedLoop(0)
        Egen.setDAC(1, 0, DCrailDAC)
        Egen.setDAC(0, 1, RFBiasDAC)

        print(DCrailDAC)
        print(RFBiasDAC)


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
                sleep(1)

                Egen.rfON()

                tic = time()  # capture current time
                stable = False  # initialize to not stable (when stabilized, measurement is taken)
                prevPdel = 1e6  # initialize delivered power to very high value for first comparison
                sampleTime = 0  # initialize sample time
                while 1:  # infinite loop that will break when power has stabilized
                    if Egen.reportReflectedPower() > pRflLimitW:
                        Egen.rfOFF()
                        break
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
                pDelMtr = readPowerMeter() / eff  # delivered power is read from lynx and divided by efficiency
                snap = Egen.reportGeneratorSnapshotData()  # snapshot of statistics from the generator
                if pDelMtr != 0:
                    pError = abs((snap[2] - pDelMtr) / pDelMtr) * 100  # % error between deliv power (gen vs lynx)
                else:
                    pError = 0
                DCinfo = Egen.reportSPIBusADCData(4)  # pull DC Section ADC data
                DCCurrent = DCinfo[2] * 40.8 / 4096  # format and scale DC current from DC section ADC info
                DCVoltage = DCinfo[0] * 201.4 / 4096  # format and scale DC voltage from DC section ADC info
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
    Egen.selectOpenClosedLoop(1)
    Egen.reportFaultOrWarningCodeList(1)
    Egen.rfOFF()
    Egen.setPowerSetpoint(0)
    fh.flush()
    os.fsync(fh)
    fh.close()
    print('DONE, total time %0.1fmin' % ((time() - startTime) / 60))