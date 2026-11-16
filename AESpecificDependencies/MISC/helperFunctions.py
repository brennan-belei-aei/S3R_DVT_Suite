""" Collection of functions used across multiple scripts """
import os
import csv
import random
from time import strftime, localtime
from datetime import datetime
import re
from itertools import pairwise
from pathlib import Path
import numpy as np
from pandas import DataFrame
from pandas import read_table
# import matplotlib
# matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import scipy.interpolate
# from AEBusCommands import aecom
#from MISC.controller_regs import *

mainPID_register_map = {'KPROP_REG':0x43e0d098,'KI_REG':0x43e0d084,'KPROP_THRESH_REG':0x43e0d190}

# [Max, gain, limit byte, full scale]
limit_loop_regs = {'Prfl'     :[0x43e0d00c, 0x43e0d008, 0b000000001, 6000], 'Pphntm' :[0x43e0d010, 0x43e0d08c, 0b000000010, 6000],
                   'Plin1'    :[0x43e0d048, 0x43e0d04c, 0b000001000, 1500], 'Plin2'  :[0x43e0d114, 0x43e0d118, 0b000010000, 1500], 
                   'Pldmos1'  :[0x43e0d11c, 0x43e0d120, 0b000100000, 6000], 'Pldmos2':[0x43e0d124, 0x43e0d128, 0b001000000, 6000], 
                   'DCI1'     :[0x43e0d12c, 0x43e0d130, 0b010000000,  100], 'DCI2'   :[0x43e0d134, 0x43e0d138, 0b100000000,  100],
                   'th_nrmlzd':0x43e0d088, 'Limit Enable':0x43e0d03c, 'Active Limits':[0x43e0f000, 0x43e0f004]}
trigger_mode = {'Manual':0, 'RF On':1, 'RF Step Start':2, 'Sequence Change':3, 'Frequency Change':4, 'Setpoint Change':5, 'DC Shutdown':6, 'Forward Power Threshold':7, 'Reflected Power Threshold':8}

LIN_RAM_ADDR_OFFSET = 0x43e0e020
LIN_RAM_DATA_OFFSET = 0x43e0e024
LDMOS_RAM_ADDR_OFFSET = 0x43e0e028
LDMOS_RAM_DATA_OFFSET = 0x43e0e02c
DCI_RX_DATA_1_0_OFFSET = 0x43e05008
DCI_RX_DATA_3_2_OFFSET = 0x43e0500c
DCI_RX_DATA_5_4_OFFSET = 0x43e05010
DCV_RX_DATA_1_0_OFFSET = 0x43e05014
DCV_RX_DATA_3_2_OFFSET = 0x43e05018
DCV_RX_DATA_5_4_OFFSET = 0x43e0501c
DAC_A_MANUAL_REG = 0x43e03004
DAC_B_MANUAL_REG = 0x43e03008
DAC_A_MON_REG = 0x43e03018
DAC_B_MON_REG = 0x43e0301c
PHANTOM_PWR_READ_REG = 0x43e0e060

lin_fet_diss = lambda val: 1500 * val / (2**16 - 1)
ldmos_fet_diss = lambda val: 6000 * val / (2**16 - 1)
# limit_max = lambda val, scale: scale * val / (2**32 - 1)


#######################################################################################################
def getGeneratorFrequencies(generator):
    maxFreq = generator.reportFixedFrequency(0, 0, 2)
    minFreq = generator.reportFixedFrequency(0, 0, 3)
    centerFreq = int((maxFreq + minFreq) / 2)
    # interFreqLow = int((centerFreq+minFreq)/2)
    # interFreqHigh = int((centerFreq+maxFreq)/2)
    frequencies = [minFreq,centerFreq,maxFreq]
    return frequencies
#######################################################################################################

#######################################################################################################
def getLinearGeneratorFrequencies(generator):
    maxFreq = generator.reportFixedFrequency(0, 0, 2)
    minFreq = generator.reportFixedFrequency(0, 0, 3)
    centerFreq = int((maxFreq + minFreq) / 2)
    interFreqLow = int((centerFreq+minFreq)/2)
    interFreqHigh = int((centerFreq+maxFreq)/2)
    frequencies = [minFreq,interFreqLow,centerFreq,interFreqHigh,maxFreq]
    return frequencies
#######################################################################################################

#######################################################################################################
def createFile(testPath, fName):
    if not os.path.isdir(testPath):
        os.makedirs(testPath)
    fh = open(os.path.join(testPath, fName), 'w', newline='')
    return fh, csv.writer(fh)
#######################################################################################################


#######################################################################################################
def unitInfoString(generator):
    """ Returns a string containing all firmware revision information. Used in titles of plots for now. """
    SN = generator.reportUnitSerialNumber()
    main_app = generator.reportSoftwarePartNumber(0) + generator.reportSoftwareRevisionLevel(0)
    backplane_fpga_app = generator.reportSoftwareRevisionLevel(101)
    bootloader = generator.reportSoftwareRevisionLevel(3)
    zynq_firmware = generator.reportSoftwareRevisionLevel(105)
    pn = generator.reportUnit315Number()
    #config_file = generator.reportSoftwarePartNumber(15) + generator.reportSoftwareRevisionLevel(15)
    return f'PN: {pn}, SN: {SN}, Main Application: {main_app}\nBootloader Version: {bootloader}, Zynq FPGA Firmware Version: {zynq_firmware}, Backplane FPGA Firmware Version: {backplane_fpga_app}'
#######################################################################################################

#######################################################################################################
def unitInfoStringCSV(generator,outpath):
    info = unitInfoString(generator)
    with open(outpath + 'UnitInfo.txt', 'w') as file:
        file.write(info)
    file.close()
#######################################################################################################

#######################################################################################################
def setRandomPulseSequence(generator, numSteps):
    generator.setPulseStepCount(0, numSteps)
    for i in range(numSteps):
        generator.setPowerSetpoint(i, 0, random.randint(10, 3500))
        generator.setPulseStepDuration(i, 0, random.randint(25, 150))
        generator.setRegulationMode(i, 0, 7)

#######################################################################################################

#######################################################################################################
def setPulseSequence(generator, numSteps, setpoints, durations):
    generator.setPulseStepCount(0, numSteps)
    for i in range(numSteps):
        generator.setPowerSetpoint(i, 0, setpoints[i])
        generator.setPulseStepDuration(i, 0, durations[i])
        generator.setRegulationMode(i, 0, 7)

#######################################################################################################

#######################################################################################################
def makePlots_Linearity(gen, data, outPath, SN, freq):
    now = strftime('%Y_%m_%d_%H_%M', localtime())
    imgName1 = outPath + f'\\power_linearity_{round(freq,3)}Hz_SN{SN}_{now}.png'
    imgName2 = outPath + f'\\power_linearity_error_{round(freq,3)}Hz_SN{SN}_{now}.png'
    imgName3 = outPath + f'\\power_linearity_DCI_{round(freq,3)}Hz_SN{SN}_{now}.png'
    imgName4 = outPath + f'\\power_linearity_AC_Lead_{freq}Hz_SN{SN}_{now}.png'
    imgName5 = outPath + f'\\power_linearity_AC_Lag_{freq}Hz_SN{SN}_{now}.png'
    imgName6 = outPath + f'\\power_linearity_Smoke_{freq}Hz_SN{SN}_{now}.png'
    # imgName4 = outPath + f'\\power_linearity_LeadLDMOSTemps_{round(freq,3)}Hz_SN{SN}_{now}.png'
    # imgName5 = outPath + f'\\power_linearity_LagLDMOSTemps_{round(freq,3)}Hz_SN{SN}_{now}.png'

    setpoint = []
    pgen = []
    pmeter = []
    power_error = []
    setpoint_error = []
    dci_leading = []
    dci_lagging = []
    dci2_leading = []
    dci2_lagging = []
    dci3_leading = []
    dci3_lagging = []
    AC1_lead = []
    AC2_lead = []
    AC3_lead = []
    AC1_lag = []
    AC2_lag = []
    AC3_lag = []
    Smoke1 = []
    Smoke2 = []
    # lead_temp_L = []
    # lead_temp_R = []
    # lag_temp_L = []
    # lag_temp_R = []

    spec = 2
    pos_spec_line = []
    neg_spec_line = []

    for row in data:
        setpoint.append(row[0])
        pmeter.append(row[1])
        pgen.append(row[2])
        power_error.append(row[3])
        setpoint_error.append(row[4])
        dci_leading.append(row[5])
        dci_lagging.append(row[6])
        dci2_leading.append(row[7])
        dci2_lagging.append(row[8])
        dci3_leading.append(row[9])
        dci3_lagging.append(row[10])
        # AC1_lead.append(row[11])
        # AC2_lead.append(row[12])
        # AC3_lead.append(row[13])
        # AC1_lag.append(row[14])
        # AC2_lag.append(row[15])
        # AC3_lag.append(row[16])
        # Smoke1.append(row[17])
        # Smoke2.append(row[18])
        # lead_temp_L.append(row[19])
        # lead_temp_R.append(row[20])
        # lag_temp_L.append(row[21])
        # lag_temp_R.append(row[22])

    for setpt_i in setpoint:
        tolerance_val = float(setpt_i) + spec
        if float(setpt_i) == 0.0:
            setpoint_e = 5.0
        else:
            setpoint_e = abs(((tolerance_val - float(setpt_i))/float(setpt_i)) * 100)
        if setpoint_e > 1.00:
            spec_value =  round(setpoint_e, 2)
            spec_value = min(spec_value,5.0)
        else:
            spec_value = 1
        pos_spec_line.append(spec_value)
        neg_spec_line.append(spec_value * -1)

    plt.figure(figsize=(14,8))
    plt.plot(setpoint, pgen, 'b-', label = '$P_{DLV, GEN}$')
    plt.plot(setpoint, pmeter, 'r-', label = '$P_{DLV, LYNX}$')
    plt.xlabel('Setpoint (W)', fontsize=14)
    plt.ylabel('Power (W)', fontsize=14)
    plt.xticks(np.linspace(0, setpoint[-1], 9), np.linspace(setpoint[0], setpoint[-1], 9))
    plt.yticks(np.linspace(0, setpoint[-1], 9), np.linspace(setpoint[0], setpoint[-1], 9))
    plt.title(f'Power Linearity {freq:.3f}MHz {setpoint[-1]:.3f}W Setpoint\nEverest {unitInfoString(gen)}', fontsize=16)
    plt.legend()
    plt.grid()
    plt.minorticks_on()
    plt.tick_params(which="minor", bottom=True, left=True)
    plt.savefig(imgName1)
    plt.close()

    plt.figure(figsize=(14,8))
    plt.plot(setpoint, power_error, 'b-', label = 'Gen vs Lynx')
    plt.plot(setpoint, setpoint_error, 'g-', label = 'Lynx vs Setpoint')
    plt.plot(setpoint, pos_spec_line,'r-', linestyle = 'dashed', linewidth = 1.5, label = "+Spec")
    plt.plot(setpoint, neg_spec_line,'r-', linestyle = 'dashed', linewidth = 1.5, label = "-Spec")
    #plt.hlines(y=1, xmin=setpoint[0], xmax=setpoint[-1], linestyle='--', color='k', linewidth=1.5)
    #plt.hlines(y=-1, xmin=setpoint[0], xmax=setpoint[-1], linestyle='--', color='k', linewidth=1.5, label='Spec')
    plt.xlabel('Setpoint (W)', fontsize=14)
    plt.ylabel('Error (%)', fontsize=14)
    plt.title(f'Power Linearity Error {freq:.3f}MHz {setpoint[-1]:.3f}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    plt.legend()
    plt.grid()
    plt.minorticks_on()
    plt.tick_params(which="minor", bottom=True, left=True)
    plt.savefig(imgName2)
    plt.close()

    plt.figure(figsize=(14,8))
    plt.plot(setpoint, dci_leading, 'r-', label = 'DCI_1 Leading')
    plt.plot(setpoint, dci_lagging, 'g-', label = 'DCI_1 Lagging')
    if np.mean(dci2_leading) > 0:
        plt.plot(setpoint, dci2_leading, 'r:', label = 'DCI_2 Leading')
        plt.plot(setpoint, dci2_lagging, 'g:', label = 'DCI_2 Lagging')
    if np.mean(dci3_leading) > 0:
        plt.plot(setpoint, dci3_leading, 'r-.', label = 'DCI_3 Leading')
        plt.plot(setpoint, dci3_lagging, 'g-.', label = 'DCI_3 Lagging')

    plt.xlabel('Setpoint (W)', fontsize=14)
    plt.ylabel('DCI (A)', fontsize=14)
    plt.title(f'Power Linearity DC Current {freq:.3f}MHz {setpoint[-1]:.3f}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    plt.legend()
    plt.grid()
    plt.minorticks_on()
    plt.tick_params(which="minor", bottom=True, left=True)
    plt.savefig(imgName3)
    plt.close()

    # plt.figure(figsize=(14, 8))
    # plt.plot(setpoint, AC1_lead, 'b-', label='Phase 1')
    # plt.plot(setpoint, AC2_lead, 'r-', label='Phase 2')
    # plt.plot(setpoint, AC3_lead, 'k-', label='Phase 3')
    # plt.xlabel('Setpoint (W)', fontsize=14)
    # plt.ylabel('Voltage (RMS)', fontsize=14)
    # plt.title(f'Power Linearity AC Line Voltages Leading {freq:.3f}MHz {setpoint[-1]:d}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    # plt.legend()
    # plt.grid()
    # plt.savefig(imgName4)
    # plt.close()

    # plt.figure(figsize=(14, 8))
    # plt.plot(setpoint, AC1_lag, 'b-', label='Phase 1')
    # plt.plot(setpoint, AC2_lag, 'r-', label='Phase 2')
    # plt.plot(setpoint, AC3_lag, 'k-', label='Phase 3')
    # plt.xlabel('Setpoint (W)', fontsize=14)
    # plt.ylabel('Voltage (RMS)', fontsize=14)
    # plt.title(f'Power Linearity AC Line Voltages Lagging {freq:.3f}MHz {setpoint[-1]:d}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    # plt.legend()
    # plt.grid()
    # plt.savefig(imgName5)
    # plt.close()
    # plt.show()

    # plt.figure(figsize=(14, 8))
    # plt.plot(setpoint, Smoke1, 'b-', label='Smoke 1')
    # plt.plot(setpoint, Smoke2, 'r-', label='Smoke 2')
    # plt.xlabel('Setpoint (W)', fontsize=14)
    # plt.ylabel('Smoke Detect Reading', fontsize=14)
    # plt.title(f'Power Linearity Smoke Detect {freq:.3f}MHz {setpoint[-1]:d}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    # plt.legend()
    # plt.grid()
    # plt.savefig(imgName6)
    # plt.close()
    # plt.show()

    # plt.figure(figsize=(14,8))
    # plt.plot(setpoint, lead_temp_L, 'b-', label = 'Left LDMOS')
    # plt.plot(setpoint, lead_temp_R, 'r-', label = 'Right LDMOS')
    # plt.xlabel('Setpoint (W)', fontsize=14)
    # plt.ylabel('Temperature (deg C)', fontsize=14)
    # plt.title('Power Linearity Leading LDMOS Temperatures %.3fMHz %dW Setpoint\nEverest %s'%(freq, setpoint[-1], unitInfoString(gen)), fontsize=16)
    # plt.legend()
    # plt.grid()
    # plt.savefig(imgName4)
    # plt.close()
    #
    # plt.figure(figsize=(14,8))
    # plt.plot(setpoint, lag_temp_L, 'b-', label = 'Left LDMOS')
    # plt.plot(setpoint, lag_temp_R, 'r-', label = 'Right LDMOS')
    # plt.xlabel('Setpoint (W)', fontsize=14)
    # plt.ylabel('Temperature (deg C)', fontsize=14)
    # plt.title('Power Linearity Lagging LDMOS Temperatures %.3fMHz %dW Setpoint\nEverest %s'%(freq, setpoint[-1], unitInfoString(gen)), fontsize=16)
    # plt.legend()
    # plt.grid()
    # plt.savefig(imgName5)
    # plt.close()
    # plt.show()
#######################################################################################################
#######################################################################################################

def makePlots_Accuracy(gen, data, outPath, SN, sp):
    now = strftime('%Y_%m_%d_%H_%M', localtime())
    imgName1 = outPath + f'\\power_Accuracy_{sp}W_SN{SN}_{now}.png'
    imgName2 = outPath + f'\\power_Accuracy_error_{sp}Hz_SN{SN}_{now}.png'
    imgName3 = outPath + f'\\power_Accuracy_DCI_{sp}Hz_SN{SN}_{now}.png'
    imgName4 = outPath + f'\\power_Accuracy_AC_Lead_{sp}Hz_SN{SN}_{now}.png'
    imgName5 = outPath + f'\\power_Accuracy_AC_Lag_{sp}Hz_SN{SN}_{now}.png'
    imgName6 = outPath + f'\\power_Accuracy_Smoke_{sp}Hz_SN{SN}_{now}.png'

    freq = []
    pgen = []
    pmeter = []
    power_error = []
    setpoint_error = []
    dci_leading = []
    dci_lagging = []
    AC1_lead = []
    AC2_lead = []
    AC3_lead= []
    AC1_lag = []
    AC2_lag = []
    AC3_lag = []
    Smoke1 = []
    Smoke2 = []
    # amp = []
    # lead_temp_L = []
    # lead_temp_R = []
    # lag_temp_L = []
    # lag_temp_R = []

    for row in data:
        freq.append(row[0])
        pmeter.append(row[1])
        pgen.append(row[2])
        power_error.append(row[3])
        setpoint_error.append(row[4])
        dci_leading.append(row[5])
        dci_lagging.append(row[6])
        AC1_lead.append(row[7])
        AC2_lead.append(row[8])
        AC3_lead.append(row[9])
        AC1_lag.append(row[10])
        AC2_lag.append(row[11])
        AC3_lag.append(row[12])
        Smoke1.append(row[13])
        Smoke2.append(row[14])

    plt.figure(figsize=(14, 8))
    plt.plot(freq, pgen, 'b-', label='$P_{DLV, GEN}$')
    plt.plot(freq, pmeter, 'r-', label='$P_{DLV, LYNX}$')
    plt.hlines(y=sp*1.01, xmin=freq[0], xmax=freq[-1], linestyle='--', color='k', linewidth=1.5)
    plt.hlines(y=sp*0.99, xmin=freq[0], xmax=freq[-1], linestyle='--', color='k', linewidth=1.5, label='Spec')
    plt.xlabel('Frequency (kHz)', fontsize=14)
    plt.ylabel('Power (W)', fontsize=14)
    plt.title(f'Power Accuracy {sp:d}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    plt.legend()
    plt.grid()
    plt.savefig(imgName1)
    plt.close()

    plt.figure(figsize=(14, 8))
    plt.plot(freq, power_error, 'b-', label='Gen vs Lynx')
    plt.plot(freq, setpoint_error, 'r-', label='Lynx vs Setpoint')
    plt.hlines(y=1, xmin=freq[0], xmax=freq[-1], linestyle='--', color='k', linewidth=1.5)
    plt.hlines(y=-1, xmin=freq[0], xmax=freq[-1], linestyle='--', color='k', linewidth=1.5, label='Spec')
    plt.xlabel('Frequency (kHz)', fontsize=14)
    plt.ylabel('Error (%)', fontsize=14)
    plt.title(f'Power Accuracy Error {sp:d}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    plt.legend()
    plt.grid()
    plt.savefig(imgName2)
    plt.close()

    plt.figure(figsize=(14, 8))
    plt.plot(freq, dci_leading, 'b-', label='DCI Leading')
    plt.plot(freq, dci_lagging, 'r-', label='DCI Lagging')
    plt.xlabel('Frequency (kHz)', fontsize=14)
    plt.ylabel('DCI (A)', fontsize=14)
    plt.title(f'Power Accuracy DC Current {sp:d}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    plt.legend()
    plt.grid()
    plt.savefig(imgName3)
    plt.close()

    plt.figure(figsize=(14,8))
    plt.plot(freq, AC1_lead, 'b-', label = 'Phase 1')
    plt.plot(freq, AC2_lead, 'r-', label = 'Phase 2')
    plt.plot(freq, AC3_lead, 'k-', label='Phase 3')
    plt.xlabel('Frequency (kHz)', fontsize=14)
    plt.ylabel('Voltage (RMS)', fontsize=14)
    plt.title(f'Power Accuracy AC Line Voltages Leading {sp:d}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    plt.legend()
    plt.grid()
    plt.savefig(imgName4)
    plt.close()

    plt.figure(figsize=(14,8))
    plt.plot(freq, AC1_lag, 'b-', label='Phase 1')
    plt.plot(freq, AC2_lag, 'r-', label='Phase 2')
    plt.plot(freq, AC3_lag, 'k-', label='Phase 3')
    plt.xlabel('Frequency (kHz)', fontsize=14)
    plt.ylabel('Voltage (RMS)', fontsize=14)
    plt.title(f'Power Accuracy AC Line Voltages Lagging {sp:d}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    plt.legend()
    plt.grid()
    plt.savefig(imgName5)
    plt.close()
    plt.show()

    plt.figure(figsize=(14, 8))
    plt.plot(freq, Smoke1, 'b-', label='Smoke 1')
    plt.plot(freq, Smoke2, 'r-', label='Smoke 2')
    plt.xlabel('Frequency (kHz)', fontsize=14)
    plt.ylabel('Smoke Detect Reading', fontsize=14)
    plt.title(f'Power Accuracy Smoke Detect {sp:d}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    plt.legend()
    plt.grid()
    plt.savefig(imgName6)
    plt.close()
    plt.show()

#######################################################################################################
#######################################################################################################
def makePlots_Efficiency(gen, data, outPath, SN, freq):
    now = strftime('%Y_%m_%d_%H_%M', localtime())
    imgName1 = outPath + f'\\power_Efficiency_{freq}Hz_SN{SN}_{now}.png'

    setpoint = []
    efficiency = []
    # lead_temp_L = []
    # lead_temp_R = []
    # lag_temp_L = []
    # lag_temp_R = []

    for row in data:
        setpoint.append(row[0])
        efficiency.append(row[8])

    plt.figure(figsize=(14,8))
    plt.plot(setpoint, efficiency, 'b-', label = '$Efficiency$')
    plt.xlabel('Setpoint (W)', fontsize=14)
    plt.ylabel('Efficiency (%)', fontsize=14)
    plt.xticks(np.linspace(0, setpoint[-1], 9), np.linspace(setpoint[0], setpoint[-1], 9))
    plt.yticks(np.linspace(0, efficiency[-1], 9), np.linspace(efficiency[0], efficiency[-1], 9))
    plt.title(f'Power Efficiency {freq:.3f}MHz {setpoint[-1]:d}W Setpoint\nEverest '+unitInfoString(gen), fontsize=16)
    plt.legend()
    plt.grid()
    plt.savefig(imgName1)
    plt.close()

#######################################################################################################

#######################################################################################################
def reportCurrent(gen):
    # current constantly changes, average n times
    n = 10
    dci1_leading = 0
    dci1_lagging = 0
    dci2_leading = 0
    dci2_lagging = 0
    dci3_leading = 0
    dci3_lagging = 0
    for _ in range(n):
        current = gen.reportMemoryAddressValue('1:4',DCI_RX_DATA_1_0_OFFSET)[0]
        current_bin = bin(current)[2:].zfill(30)

        dci1_leading += int(current_bin[16:],2) * 100 / (2**14 - 1)
        dci1_lagging += int(current_bin[:14],2) * 100 / (2**14 - 1)

    for _ in range(n):  
        current = gen.reportMemoryAddressValue('1:4',DCI_RX_DATA_3_2_OFFSET)[0]
        current_bin = bin(current)[2:].zfill(30)

        dci2_leading += int(current_bin[16:],2) * 100 / (2**14 - 1)
        dci2_lagging += int(current_bin[:14],2) * 100 / (2**14 - 1)

    for _ in range(n):
        current = gen.reportMemoryAddressValue('1:4',DCI_RX_DATA_5_4_OFFSET)[0]
        current_bin = bin(current)[2:].zfill(30)

        dci3_leading += int(current_bin[16:],2) * 100 / (2**14 - 1)
        dci3_lagging += int(current_bin[:14],2) * 100 / (2**14 - 1)

    dci1_leading = dci1_leading / n
    dci1_lagging = dci1_lagging / n
    dci2_leading = dci2_leading / n
    dci2_lagging = dci2_lagging / n
    dci3_leading = dci3_leading / n
    dci3_lagging = dci3_lagging / n

    if (dci2_leading != 0.0) or (dci2_lagging != 0.0):
        dc_supply_num = 2
        if (dci3_leading != 0.0) or (dci3_lagging != 0.0):
            dc_supply_num = 3
    else:
        dc_supply_num = 1
    return dc_supply_num, dci1_leading, dci1_lagging, dci2_leading, dci2_lagging, dci3_leading, dci3_lagging
#######################################################################################################


#######################################################################################################
def clearDAC(gen):
        gen.setMemoryAddressValue(DAC_A_MANUAL_REG, dacForGen(0))
        gen.setMemoryAddressValue(DAC_B_MANUAL_REG, dacForGen(0))

#######################################################################################################
def reportDAC(gen):
    # current constantly changes, average n times
    n = 10
    data1_I = 0
    data1_Q = 0
    data2_I = 0
    data2_Q = 0
    data3_I = 0
    data3_Q = 0

    for _ in range(n):
        dac = gen.reportMemoryAddressValue('1:4',DAC_A_MON_REG)[0]
        dac_bin = bin(dac)[2:].zfill(30)

        data1_Q += int(dac_bin[16:],2)
        data1_I += int(dac_bin[:14],2)

    for _ in range(n):
        dac = gen.reportMemoryAddressValue('1:4',DAC_B_MON_REG)[0]
        dac_bin = bin(dac)[2:].zfill(30)

        data2_Q += int(dac_bin[16:],2)
        data2_I += int(dac_bin[:14],2)

    data1_Q = data1_Q / n
    data1_I = data1_I / n
    data2_Q = data2_Q / n
    data2_I = data2_I / n
    data3_Q = data3_Q / n
    data3_I = data3_I / n

    return data1_Q, data1_I, data2_Q, data2_I, data3_Q, data3_I
#######################################################################################################


#######################################################################################################
def dacForGen(dac):

    return int(dac)*65537  # 2^16 + 1
#######################################################################################################


#######################################################################################################
def reportVoltage(gen):
    n = 10
    dcv1_leading = 0
    dcv1_lagging = 0
    dcv2_leading = 0
    dcv2_lagging = 0
    dcv3_leading = 0
    dcv3_lagging = 0

    for _ in range(n):
        voltage = gen.reportMemoryAddressValue('1:4',DCV_RX_DATA_1_0_OFFSET)[0]
        voltage_bin = bin(voltage)[2:].zfill(28)

        dcv1_leading += int(voltage_bin[16:],2) * 100 / (2**12 - 1)
        dcv1_lagging += int(voltage_bin[:12],2) * 100 / (2**12 - 1)
    for _ in range(n):
        voltage = gen.reportMemoryAddressValue('1:4',DCV_RX_DATA_3_2_OFFSET)[0]
        voltage_bin = bin(voltage)[2:].zfill(28)

        dcv2_leading += int(voltage_bin[16:],2) * 100 / (2**12 - 1)
        dcv2_lagging += int(voltage_bin[:12],2) * 100 / (2**12 - 1)
    for _ in range(n):
        voltage = gen.reportMemoryAddressValue('1:4',DCV_RX_DATA_5_4_OFFSET)[0]
        voltage_bin = bin(voltage)[2:].zfill(28)

        dcv3_leading += int(voltage_bin[16:],2) * 100 / (2**12 - 1)
        dcv3_lagging += int(voltage_bin[:12],2) * 100 / (2**12 - 1)

    dcv1_leading = dcv1_leading / n
    dcv1_lagging = dcv1_lagging / n
    dcv2_leading = dcv2_leading / n
    dcv2_lagging = dcv2_lagging / n
    dcv3_leading = dcv3_leading / n
    dcv3_lagging = dcv3_lagging / n

    if (dcv2_leading != 0.0) and (dcv2_lagging != 0.0):
        dc_supply_num = 2
        if (dcv3_leading != 0.0) and (dcv3_lagging != 0.0):
            dc_supply_num = 3
    else:
        dc_supply_num = 1
    return dc_supply_num, dcv1_leading, dcv1_lagging, dcv2_leading, dcv2_lagging, dcv3_leading, dcv3_lagging
#######################################################################################################


#######################################################################################################
def reportLinRegDiss(gen):
    # dissipation constantly changes, average n times
    n = 10
    dissipation_leading = 0
    dissipation_lagging = 0
    for _ in range(n):
        gen.setMemoryAddressValue(LIN_RAM_ADDR_OFFSET, 0b0000)
        dissipation_leading += lin_fet_diss(gen.reportMemoryAddressValue('1:4', LIN_RAM_DATA_OFFSET)[0])
        gen.setMemoryAddressValue(LIN_RAM_ADDR_OFFSET, 0b100000000)
        dissipation_lagging += lin_fet_diss(gen.reportMemoryAddressValue('1:4', LIN_RAM_DATA_OFFSET)[0])

    return dissipation_leading/n, dissipation_lagging/n
#######################################################################################################


#######################################################################################################
def reportLDMOSDiss(gen):
    # dissipation constantly changes, average n times
    n = 10
    dissipation_leading = 0
    dissipation_lagging = 0
    for _ in range(n):
        gen.setMemoryAddressValue(LDMOS_RAM_ADDR_OFFSET, 0b0000)
        dissipation_leading += ldmos_fet_diss(gen.reportMemoryAddressValue('1:4', LDMOS_RAM_DATA_OFFSET)[0])
        gen.setMemoryAddressValue(LDMOS_RAM_ADDR_OFFSET, 0b100000000)
        dissipation_lagging += ldmos_fet_diss(gen.reportMemoryAddressValue('1:4', LDMOS_RAM_DATA_OFFSET)[0])

    return dissipation_leading/n, dissipation_lagging/n
#######################################################################################################


#######################################################################################################
def reportActiveLimits(gen):
    gen.setMemoryAddressValue(0x43e0f000, 0b0000)
    activeLimits = bin(gen.reportMemoryAddressValue('1:4', 0x43e0f004)[0])[2:].zfill(9)
    # activeLimits = [int(lim) for lim in list(activeLimits)]
    return activeLimits
#######################################################################################################


#######################################################################################################
def reportPhantomPower(gen):
    phntm = gen.reportMemoryAddressValue('1:4', PHANTOM_PWR_READ_REG)[0]
    return 6000 * phntm / (2**32 - 1)
#######################################################################################################


#######################################################################################################
# def writeLimitInfoFile(generator, outPath):
#     now = strftime('%Y_%m_%d_%H_%M_%S', localtime())

#     K_PPHNTM_THR_REG = 0x43e0d014
#     K_PRFL_THR_REG = 0x43e0d018
#     FAST_MAX_INCR_UP_REG = 0x43e0d02c
#     FAST_MAX_INCR_DN_REG = 0x43e0d030
#     FAST_MAX_INCR_LE_REG = 0x43e0d3a0
#     FAST_MAX_INCR_TE_REG = 0x43e0d3a4
#     FAST_MAX_INCR_FE_REG = 0x43e0d3a8
#     FAST_MAX_INCR_RE_SE_REG = 0x43e0d3ac
#     FAST_MAX_INCR_FE_SE_REG = 0x43e0d3b0
#     HIPER_FDFWD_EN_REG = 0x43e0d3b4
#     GS_STATE_FDBK_EN_REG = 0x43e0d3b8
#     GS_PWR_ACT_SCALE_EN_REG = 0x43e0d3bc
#     IMC_EN_REG = 0x43e0d3c0
#     SPMOD_ENABLE = 0x43e0d524
#     K_GS_PARAM_ADDR_REG = 0x43e0d398
#     K_GS_PARAM_DATA_REG = 0x43e0d39c

#     raie_modes_byte_str_current = bin(generator.reportMemoryAddressValue('1:4', raie_mode_regs['HiPer'])[0])[2:]
#     raie_modes_byte_str_current = raie_modes_byte_str_current.zfill(7)
#     mode_byte_list = list(raie_modes_byte_str_current)
#     if mode_byte_list[5]=='1':
#         raie_modes_enabled_current['HiPer'] = 'true'
#     else:
#         raie_modes_enabled_current['HiPer'] = 'false'
#     if mode_byte_list[4]=='1':
#         raie_modes_enabled_current['HiPer Smart'] = 'true'
#     else:
#         raie_modes_enabled_current['HiPer Smart'] = 'false'
#     if mode_byte_list[3]=='1':
#         raie_modes_enabled_current['HiPer Repetitive'] = 'true'
#     else:
#         raie_modes_enabled_current['HiPer Repetitive'] = 'false'

#     if generator.reportMemoryAddressValue('1:4', raie_mode_regs['Hybrid'])[0]==2:
#         raie_modes_enabled_current['Hybrid'] = 'true'
#     else:
#         raie_modes_enabled_current['Hybrid'] = 'false'

#     limit_loop_en = hex(generator.reportMemoryAddressValue('1:4', limit_loop_regs['Limit Enable'])[0])
#     plin1_max = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Plin1'][0])[0]
#     plin1_gain = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Plin1'][1])[0]
#     plin2_max = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Plin2'][0])[0]
#     plin2_gain = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Plin2'][1])[0]
#     pldmos1_max = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Pldmos1'][0])[0]
#     pldmos1_gain = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Pldmos1'][1])[0]
#     pldmos2_max = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Pldmos2'][0])[0]
#     pldmos2_gain = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Pldmos2'][1])[0]
#     dci1_max = generator.reportMemoryAddressValue('1:4', limit_loop_regs['DCI1'][0])[0]
#     dci1_gain = generator.reportMemoryAddressValue('1:4', limit_loop_regs['DCI1'][1])[0]
#     dci2_max = generator.reportMemoryAddressValue('1:4', limit_loop_regs['DCI2'][0])[0]
#     dci2_gain = generator.reportMemoryAddressValue('1:4', limit_loop_regs['DCI2'][1])[0]
#     pphntm_max = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Pphntm'][0])[0]
#     pphntm_gain = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Pphntm'][1])[0]
#     prfl_gain = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Prfl'][1])[0]
#     prfl_max = generator.reportMemoryAddressValue('1:4', limit_loop_regs['Prfl'][0])[0]
#     k_pphntm_thr = generator.reportMemoryAddressValue('1:4', K_PPHNTM_THR_REG)[0]
#     k_prfl_thr = generator.reportMemoryAddressValue('1:4', K_PRFL_THR_REG)[0]
#     tau_blankout = int(generator.reportMemoryAddressValue('1:4', raie_constant_regs['tau_blankout'][1])[0]) & 0xFFFF

#     th_nrmlzd = generator.reportMemoryAddressValue('1:4', 0x43e0d088)[0]
#     kp = generator.reportMemoryAddressValue('1:4', mainPID_register_map['KPROP_REG'])[0]
#     ki = generator.reportMemoryAddressValue('1:4', mainPID_register_map['KI_REG'])[0]
#     kd = generator.reportMemoryAddressValue('1:4', mainPID_register_map['KDERIV_REG'])[0]
#     fy_c1 = generator.reportMemoryAddressValue('1:4', mainPID_register_map['FY_C1_REG'])[0]
#     fy_c2 = generator.reportMemoryAddressValue('1:4', mainPID_register_map['FY_C2_REG'])[0]
#     fy_c3 = generator.reportMemoryAddressValue('1:4', mainPID_register_map['FY_C3_REG'])[0]
#     fy_c4 = generator.reportMemoryAddressValue('1:4', mainPID_register_map['FY_C4_REG'])[0]
#     fy_c5 = generator.reportMemoryAddressValue('1:4', mainPID_register_map['FY_C5_REG'])[0]
#     fr_c1 = generator.reportMemoryAddressValue('1:4', mainPID_register_map['FR_C1_REG'])[0]
#     fr_c2 = generator.reportMemoryAddressValue('1:4', mainPID_register_map['FR_C2_REG'])[0]
#     fr_c3 = generator.reportMemoryAddressValue('1:4', mainPID_register_map['FR_C3_REG'])[0]
#     fr_c4 = generator.reportMemoryAddressValue('1:4', mainPID_register_map['FR_C4_REG'])[0]
#     fast_max_up_increment = generator.reportMemoryAddressValue('1:4', FAST_MAX_INCR_UP_REG)[0]
#     fast_max_down_increment = generator.reportMemoryAddressValue('1:4', FAST_MAX_INCR_DN_REG)[0]
#     dead_time_count = generator.reportMemoryAddressValue('1:4', raie_constant_regs['dead_time_mask'][1])[0]

#     altz_en = raie_modes_enabled_current['AltZ']
#     altz_normalized_k = generator.reportMemoryAddressValue('1:4', altZ_register_map['NRMLZD_ALTZ_K_REG'])[0]
#     altz_kp = generator.reportMemoryAddressValue('1:4', altZ_register_map['KPROP_ALTZ_REG'])[0]
#     altz_ki = generator.reportMemoryAddressValue('1:4', altZ_register_map['KI_ALTZ_REG'])[0]
#     altz_kd = generator.reportMemoryAddressValue('1:4', altZ_register_map['KDERIV_ALTZ_REG'])[0]
#     altz_tau_blankout = int(generator.reportMemoryAddressValue('1:4', raie_constant_regs['tau_blankout'][1])[0] >> 16) & 0xFFFF
#     fy_c1_altz = generator.reportMemoryAddressValue('1:4', altZ_register_map['FY_ALTZ_C1_REG'])[0]
#     fy_c2_altz = generator.reportMemoryAddressValue('1:4', altZ_register_map['FY_ALTZ_C2_REG'])[0]
#     fy_c3_altz = generator.reportMemoryAddressValue('1:4', altZ_register_map['FY_ALTZ_C3_REG'])[0]
#     fy_c4_altz = generator.reportMemoryAddressValue('1:4', altZ_register_map['FY_ALTZ_C4_REG'])[0]
#     fy_c5_altz = generator.reportMemoryAddressValue('1:4', altZ_register_map['FY_ALTZ_C5_REG'])[0]
#     fr_c1_altz = generator.reportMemoryAddressValue('1:4', altZ_register_map['FR_ALTZ_C1_REG'])[0]
#     fr_c2_altz = generator.reportMemoryAddressValue('1:4', altZ_register_map['FR_ALTZ_C2_REG'])[0]
#     fr_c3_altz = generator.reportMemoryAddressValue('1:4', altZ_register_map['FR_ALTZ_C3_REG'])[0]
#     fr_c4_altz = generator.reportMemoryAddressValue('1:4', altZ_register_map['FR_ALTZ_C4_REG'])[0]

#     hiper_en = raie_modes_enabled_current['HiPer']
#     hiper_kp = generator.reportMemoryAddressValue('1:4', hiper_register_map['HIPER_KP_REG'])[0]
#     hiper_kd = generator.reportMemoryAddressValue('1:4', hiper_register_map['HIPER_KD_REG'])[0]
#     hiper_ki = generator.reportMemoryAddressValue('1:4', hiper_register_map['HIPER_KI_REG'])[0]
#     hiper_iv = generator.reportMemoryAddressValue('1:4', hiper_register_map['HIPER_IV_REG'])[0]
#     hiper_max = generator.reportMemoryAddressValue('1:4', hiper_register_map['HIPER_MAX_REG'])[0]
#     hiper_min = generator.reportMemoryAddressValue('1:4', hiper_register_map['HIPER_MIN_REG'])[0]
#     hiper_smart_en = raie_modes_enabled_current['HiPer Smart']
#     hiper_smart_thresh_up = int(generator.reportMemoryAddressValue('1:4', hiper_register_map['HIPER_SMART_MODE_THRSHLD_REG'])[0]) & 0xFFFF
#     hiper_smart_thresh_down = (int(generator.reportMemoryAddressValue('1:4', hiper_register_map['HIPER_SMART_MODE_THRSHLD_REG'])[0]) >> 16) & 0xFFFF
#     hiper_repetitive_en = raie_modes_enabled_current['HiPer Repetitive']
#     anc_01_enable = raie_modes_enabled_current['ANC01']
#     f_anc1_c1 = generator.reportMemoryAddressValue('1:4', anc_register_map['F_ANC_01_C1_REG'])[0]
#     f_anc1_c2 = generator.reportMemoryAddressValue('1:4', anc_register_map['F_ANC_01_C2_REG'])[0]
#     f_anc1_c3 = generator.reportMemoryAddressValue('1:4', anc_register_map['F_ANC_01_C3_REG'])[0]
#     anc_02_enable = raie_modes_enabled_current['ANC02']
#     f_anc2_c1 = generator.reportMemoryAddressValue('1:4', anc_register_map['F_ANC_02_C1_REG'])[0]
#     f_anc2_c2 = generator.reportMemoryAddressValue('1:4', anc_register_map['F_ANC_02_C2_REG'])[0]
#     f_anc2_c3 = generator.reportMemoryAddressValue('1:4', anc_register_map['F_ANC_02_C3_REG'])[0]
#     anc_03_enable = raie_modes_enabled_current['ANC03']
#     f_anc3_c1 = generator.reportMemoryAddressValue('1:4', anc_register_map['F_ANC_03_C1_REG'])[0]
#     f_anc3_c2 = generator.reportMemoryAddressValue('1:4', anc_register_map['F_ANC_03_C2_REG'])[0]
#     f_anc3_c3 = generator.reportMemoryAddressValue('1:4', anc_register_map['F_ANC_03_C3_REG'])[0]
#     hybrid_mode_en = raie_modes_enabled_current['Hybrid']
#     recovery_max_up_incr = generator.reportMemoryAddressValue('1:4', raie_constant_regs['Recovery_max_up'])[0]
#     recovery_max_dn_incr = generator.reportMemoryAddressValue('1:4', raie_constant_regs['Recovery_max_dn'])[0]
#     recovery_th_up_nrmlzd = generator.reportMemoryAddressValue('1:4', raie_constant_regs['Recovery_th_up'])[0]
#     recovery_th_dn_nrmlzd = generator.reportMemoryAddressValue('1:4', raie_constant_regs['Recovery_th_dn'])[0]

#     LD12_RE = generator.reportMemoryAddressValue('1:4', power_actuator_RE_regs['FR_P_ACT_LD12_RE_REG'])[0]
#     LG12_RE = generator.reportMemoryAddressValue('1:4', power_actuator_RE_regs['FR_P_ACT_LG12_RE_REG'])[0]
#     pwr_act_scale_norm = generator.reportMemoryAddressValue('1:4', power_actuator_RE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_RE_REG'])[0]
#     fr_p_act_ld_c1 = LD12_RE & 0xFFFF
#     fr_p_act_ld_c2 = (LD12_RE >> 16) & 0xFFFF
#     fr_p_act_ld_c3 = generator.reportMemoryAddressValue('1:4', power_actuator_RE_regs['FR_P_ACT_LD3_RE_REG'])[0]
#     fr_p_act_lg_c1 = LG12_RE & 0xFFFF
#     fr_p_act_lg_c2 = (LG12_RE >> 16) & 0xFFFF
#     fr_p_act_lg_c3 = generator.reportMemoryAddressValue('1:4', power_actuator_RE_regs['FR_P_ACT_LG3_RE_REG'])[0]
#     pwr_act_ctrl_scale = generator.reportMemoryAddressValue('1:4', power_actuator_RE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_RE_REG'])[0]
#     pwr_act_ctrl_ff_scale = generator.reportMemoryAddressValue('1:4', power_actuator_RE_regs['PWR_ACTUATOR_CTRL_FF_SCALING_RE_REG'])[0]
#     pwr_act_enable = int(generator.reportMemoryAddressValue('1:4', power_actuator_RE_regs['P_ACT_COMP_EN_RE_REG'])[0]) & 0x1
#     pwr_act_ff_enable = int(generator.reportMemoryAddressValue('1:4', power_actuator_RE_regs['P_ACT_COMP_EN_RE_REG'])[0]) & 0x2
#     pwr_act_tf_enable = int(generator.reportMemoryAddressValue('1:4', power_actuator_RE_regs['P_ACT_COMP_EN_RE_REG'])[0]) & 0x4

#     if pwr_act_enable:
#         pwr_act_enable = 1
#     if pwr_act_ff_enable:
#         pwr_act_ff_enable = 1
#     if pwr_act_tf_enable:
#         pwr_act_tf_enable = 1

#     tmp_KPKI_le = generator.reportMemoryAddressValue('1:4', mainPID_Leading_register_map['KPKI_LEADING_REG'])[0]
#     tmp_KPKI_te = generator.reportMemoryAddressValue('1:4', mainPID_Trailing_register_map['KPKI_TRAILING_REG'])[0]
#     tmp_KPKI_fe = generator.reportMemoryAddressValue('1:4', mainPID_Falling_register_map['KPKI_FALLING_REG'])[0]
#     tmp_KPKI_re_se = generator.reportMemoryAddressValue('1:4', mainPID_RE_SE_register_map['KPKI_RE_SE_REG'])[0]
#     tmp_KPKI_fe_se = generator.reportMemoryAddressValue('1:4', mainPID_FE_SE_register_map['KPKI_FE_SE_REG'])[0]

#     tmp_FRC1C2_le = generator.reportMemoryAddressValue('1:4', mainPID_Leading_register_map['FR_C1C2_LEADING_REG'])[0]
#     tmp_FRC1C2_te = generator.reportMemoryAddressValue('1:4', mainPID_Trailing_register_map['FR_C1C2_TRAILING_REG'])[0]
#     tmp_FRC1C2_fe = generator.reportMemoryAddressValue('1:4', mainPID_Falling_register_map['FR_C1C2_FALLING_REG'])[0]
#     tmp_FRC1C2_re_se = generator.reportMemoryAddressValue('1:4', mainPID_RE_SE_register_map['FR_C1C2_RE_SE_REG'])[0]
#     tmp_FRC1C2_fe_se = generator.reportMemoryAddressValue('1:4', mainPID_FE_SE_register_map['FR_C1C2_FE_SE_REG'])[0]

#     tmp_FRC3C4_le = generator.reportMemoryAddressValue('1:4', mainPID_Leading_register_map['FR_C3C4_LEADING_REG'])[0]
#     tmp_FRC3C4_te = generator.reportMemoryAddressValue('1:4', mainPID_Trailing_register_map['FR_C3C4_TRAILING_REG'])[0]
#     tmp_FRC3C4_fe = generator.reportMemoryAddressValue('1:4', mainPID_Falling_register_map['FR_C3C4_FALLING_REG'])[0]
#     tmp_FRC3C4_re_se = generator.reportMemoryAddressValue('1:4', mainPID_RE_SE_register_map['FR_C3C4_RE_SE_REG'])[0]
#     tmp_FRC3C4_fe_se = generator.reportMemoryAddressValue('1:4', mainPID_FE_SE_register_map['FR_C3C4_FE_SE_REG'])[0]

#     tmp_thr_abs = generator.reportMemoryAddressValue('1:4', RE_SE_misc_regs['FE_RE_SE_THRESHOLD_ABSOLUTE_REG'])[0]

#     mainpid_le_kprop = (tmp_KPKI_le >> 16) & 0xFFFF
#     mainpid_le_ki = tmp_KPKI_le & 0xFFFF
#     mainpid_le_kderiv = generator.reportMemoryAddressValue('1:4', mainPID_Leading_register_map['KDERIV_LEADING_REG'])[0]
#     mainpid_le_fr_c1 = tmp_FRC1C2_le & 0xFFFF
#     mainpid_le_fr_c2 = (tmp_FRC1C2_le >> 16) & 0xFFFF
#     mainpid_le_fr_c3 = tmp_FRC3C4_le & 0xFFFF
#     mainpid_le_fr_c4 = (tmp_FRC3C4_le >> 16) & 0xFFFF
#     mainpid_le_fy_c1 = generator.reportMemoryAddressValue('1:4', mainPID_Leading_register_map['FY_C1_LEADING_REG'])[0]
#     mainpid_le_fy_c2 = generator.reportMemoryAddressValue('1:4', mainPID_Leading_register_map['FY_C2_LEADING_REG'])[0]
#     mainpid_le_fy_c3 = generator.reportMemoryAddressValue('1:4', mainPID_Leading_register_map['FY_C3_LEADING_REG'])[0]
#     mainpid_le_fy_c4 = generator.reportMemoryAddressValue('1:4', mainPID_Leading_register_map['FY_C4_LEADING_REG'])[0]
#     mainpid_le_fy_c5 = generator.reportMemoryAddressValue('1:4', mainPID_Leading_register_map['FY_C5_LEADING_REG'])[0]

#     mainpid_te_kprop = (tmp_KPKI_te >> 16) & 0xFFFF
#     mainpid_te_ki = tmp_KPKI_te & 0xFFFF
#     mainpid_te_kderiv = generator.reportMemoryAddressValue('1:4', mainPID_Trailing_register_map['KDERIV_TRAILING_REG'])[0]
#     mainpid_te_fr_c1 = tmp_FRC1C2_te & 0xFFFF
#     mainpid_te_fr_c2 = (tmp_FRC1C2_te >> 16) & 0xFFFF
#     mainpid_te_fr_c3 = tmp_FRC3C4_te & 0xFFFF
#     mainpid_te_fr_c4 = (tmp_FRC3C4_te >> 16) & 0xFFFF
#     mainpid_te_fy_c1 = generator.reportMemoryAddressValue('1:4', mainPID_Trailing_register_map['FY_C1_TRAILING_REG'])[0]
#     mainpid_te_fy_c2 = generator.reportMemoryAddressValue('1:4', mainPID_Trailing_register_map['FY_C2_TRAILING_REG'])[0]
#     mainpid_te_fy_c3 = generator.reportMemoryAddressValue('1:4', mainPID_Trailing_register_map['FY_C3_TRAILING_REG'])[0]
#     mainpid_te_fy_c4 = generator.reportMemoryAddressValue('1:4', mainPID_Trailing_register_map['FY_C4_TRAILING_REG'])[0]
#     mainpid_te_fy_c5 = generator.reportMemoryAddressValue('1:4', mainPID_Trailing_register_map['FY_C5_TRAILING_REG'])[0]

#     mainpid_fe_kprop = (tmp_KPKI_fe >> 16) & 0xFFFF
#     mainpid_fe_ki = tmp_KPKI_fe & 0xFFFF
#     mainpid_fe_kderiv = generator.reportMemoryAddressValue('1:4', mainPID_Falling_register_map['KDERIV_FALLING_REG'])[0]
#     mainpid_fe_fr_c1 = tmp_FRC1C2_fe & 0xFFFF
#     mainpid_fe_fr_c2 = (tmp_FRC1C2_fe >> 16) & 0xFFFF
#     mainpid_fe_fr_c3 = tmp_FRC3C4_fe & 0xFFFF
#     mainpid_fe_fr_c4 = (tmp_FRC3C4_fe >> 16) & 0xFFFF
#     mainpid_fe_fy_c1 = generator.reportMemoryAddressValue('1:4', mainPID_Falling_register_map['FY_C1_FALLING_REG'])[0]
#     mainpid_fe_fy_c2 = generator.reportMemoryAddressValue('1:4', mainPID_Falling_register_map['FY_C2_FALLING_REG'])[0]
#     mainpid_fe_fy_c3 = generator.reportMemoryAddressValue('1:4', mainPID_Falling_register_map['FY_C3_FALLING_REG'])[0]
#     mainpid_fe_fy_c4 = generator.reportMemoryAddressValue('1:4', mainPID_Falling_register_map['FY_C4_FALLING_REG'])[0]
#     mainpid_fe_fy_c5 = generator.reportMemoryAddressValue('1:4', mainPID_Falling_register_map['FY_C5_FALLING_REG'])[0]

#     mainpid_se_kprop = (tmp_KPKI_re_se >> 16) & 0xFFFF
#     mainpid_se_ki = tmp_KPKI_re_se & 0xFFFF
#     mainpid_se_kderiv = generator.reportMemoryAddressValue('1:4', mainPID_RE_SE_register_map['KDERIV_RE_SE_REG'])[0]
#     mainpid_se_fr_c1 = tmp_FRC1C2_re_se & 0xFFFF
#     mainpid_se_fr_c2 = (tmp_FRC1C2_re_se >> 16) & 0xFFFF
#     mainpid_se_fr_c3 = tmp_FRC3C4_re_se & 0xFFFF
#     mainpid_se_fr_c4 = (tmp_FRC3C4_re_se >> 16) & 0xFFFF
#     mainpid_se_fy_c1 = generator.reportMemoryAddressValue('1:4', mainPID_RE_SE_register_map['FY_C1_RE_SE_REG'])[0]
#     mainpid_se_fy_c2 = generator.reportMemoryAddressValue('1:4', mainPID_RE_SE_register_map['FY_C2_RE_SE_REG'])[0]
#     mainpid_se_fy_c3 = generator.reportMemoryAddressValue('1:4', mainPID_RE_SE_register_map['FY_C3_RE_SE_REG'])[0]
#     mainpid_se_fy_c4 = generator.reportMemoryAddressValue('1:4', mainPID_RE_SE_register_map['FY_C4_RE_SE_REG'])[0]
#     mainpid_se_fy_c5 = generator.reportMemoryAddressValue('1:4', mainPID_RE_SE_register_map['FY_C5_RE_SE_REG'])[0]

#     mainpid_fe_se_kprop = (tmp_KPKI_fe_se >> 16) & 0xFFFF
#     mainpid_fe_se_ki = tmp_KPKI_fe_se & 0xFFFF
#     mainpid_fe_se_kderiv = generator.reportMemoryAddressValue('1:4', mainPID_FE_SE_register_map['KDERIV_FE_SE_REG'])[0]
#     mainpid_fe_se_fr_c1 = tmp_FRC1C2_fe_se & 0xFFFF
#     mainpid_fe_se_fr_c2 = (tmp_FRC1C2_fe_se >> 16) & 0xFFFF
#     mainpid_fe_se_fr_c3 = tmp_FRC3C4_fe_se & 0xFFFF
#     mainpid_fe_se_fr_c4 = (tmp_FRC3C4_fe_se >> 16) & 0xFFFF
#     mainpid_fe_se_fy_c1 = generator.reportMemoryAddressValue('1:4', mainPID_FE_SE_register_map['FY_C1_FE_SE_REG'])[0]
#     mainpid_fe_se_fy_c2 = generator.reportMemoryAddressValue('1:4', mainPID_FE_SE_register_map['FY_C2_FE_SE_REG'])[0]
#     mainpid_fe_se_fy_c3 = generator.reportMemoryAddressValue('1:4', mainPID_FE_SE_register_map['FY_C3_FE_SE_REG'])[0]
#     mainpid_fe_se_fy_c4 = generator.reportMemoryAddressValue('1:4', mainPID_FE_SE_register_map['FY_C4_FE_SE_REG'])[0]
#     mainpid_fe_se_fy_c5 = generator.reportMemoryAddressValue('1:4', mainPID_FE_SE_register_map['FY_C5_FE_SE_REG'])[0]

#     re_se_thresh_abs = (tmp_thr_abs >> 16) & 0xFFFF
#     fe_se_thresh_abs = tmp_thr_abs & 0xFFFF
#     tau_blankout_fe = (int(generator.reportMemoryAddressValue('1:4', RE_SE_misc_regs['TAU_BLANKOUT_RE_SE_FE'])[0]) >> 16) & 0xFFFF
#     tau_blankout_re_se = int(generator.reportMemoryAddressValue('1:4', RE_SE_misc_regs['TAU_BLANKOUT_RE_SE_FE'])[0]) & 0xFFFF
#     tau_blankout_fe_se = generator.reportMemoryAddressValue('1:4', RE_SE_misc_regs['TAU_BLANKOUT_FE_SE'])[0]

#     LD12_LE = generator.reportMemoryAddressValue('1:4', power_actuator_LE_regs['FR_P_ACT_LD12_LE_REG'])[0]
#     LG12_LE = generator.reportMemoryAddressValue('1:4', power_actuator_LE_regs['FR_P_ACT_LG12_LE_REG'])[0]
#     pwr_act_scale_norm_le = generator.reportMemoryAddressValue('1:4', power_actuator_LE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_LE_REG'])[0]
#     fr_p_act_ld_c1_le = LD12_LE & 0xFFFF
#     fr_p_act_ld_c2_le = (LD12_LE >> 16) & 0xFFFF
#     fr_p_act_ld_c3_le = generator.reportMemoryAddressValue('1:4', power_actuator_LE_regs['FR_P_ACT_LD3_LE_REG'])[0]
#     fr_p_act_lg_c1_le = LG12_LE & 0xFFFF
#     fr_p_act_lg_c2_le = (LG12_LE >> 16) & 0xFFFF
#     fr_p_act_lg_c3_le = generator.reportMemoryAddressValue('1:4', power_actuator_LE_regs['FR_P_ACT_LG3_LE_REG'])[0]
#     pwr_act_ctrl_scale_le = generator.reportMemoryAddressValue('1:4', power_actuator_LE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_LE_REG'])[0]
#     pwr_act_ctrl_ff_scale_le = generator.reportMemoryAddressValue('1:4', power_actuator_LE_regs['PWR_ACTUATOR_CTRL_FF_SCALING_LE_REG'])[0]
#     pwr_act_enable_le = int(generator.reportMemoryAddressValue('1:4', power_actuator_LE_regs['P_ACT_COMP_EN_LE_REG'])[0]) & 0x1
#     pwr_act_ff_enable_le = int(generator.reportMemoryAddressValue('1:4', power_actuator_LE_regs['P_ACT_COMP_EN_LE_REG'])[0]) & 0x2
#     pwr_act_tf_enable_le = int(generator.reportMemoryAddressValue('1:4', power_actuator_LE_regs['P_ACT_COMP_EN_LE_REG'])[0]) & 0x4

#     if pwr_act_enable_le:
#         pwr_act_enable_le = 1
#     if pwr_act_ff_enable_le:
#         pwr_act_ff_enable_le = 1
#     if pwr_act_tf_enable_le:
#         pwr_act_tf_enable_le = 1

#     LD12_TE = generator.reportMemoryAddressValue('1:4', power_actuator_TE_regs['FR_P_ACT_LD12_TE_REG'])[0]
#     LG12_TE = generator.reportMemoryAddressValue('1:4', power_actuator_TE_regs['FR_P_ACT_LG12_TE_REG'])[0]
#     pwr_act_scale_norm_te = generator.reportMemoryAddressValue('1:4', power_actuator_TE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_TE_REG'])[0]
#     fr_p_act_ld_c1_te = LD12_TE & 0xFFFF
#     fr_p_act_ld_c2_te = (LD12_TE >> 16) & 0xFFFF
#     fr_p_act_ld_c3_te = generator.reportMemoryAddressValue('1:4', power_actuator_TE_regs['FR_P_ACT_LD3_TE_REG'])[0]
#     fr_p_act_lg_c1_te = LG12_TE & 0xFFFF
#     fr_p_act_lg_c2_te = (LG12_TE >> 16) & 0xFFFF
#     fr_p_act_lg_c3_te = generator.reportMemoryAddressValue('1:4', power_actuator_TE_regs['FR_P_ACT_LG3_TE_REG'])[0]
#     pwr_act_ctrl_scale_te = generator.reportMemoryAddressValue('1:4', power_actuator_TE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_TE_REG'])[0]
#     pwr_act_ctrl_ff_scale_te = generator.reportMemoryAddressValue('1:4', power_actuator_TE_regs['PWR_ACTUATOR_CTRL_FF_SCALING_TE_REG'])[0]
#     pwr_act_enable_te = int(generator.reportMemoryAddressValue('1:4', power_actuator_TE_regs['P_ACT_COMP_EN_TE_REG'])[0]) & 0x1
#     pwr_act_ff_enable_te = int(generator.reportMemoryAddressValue('1:4', power_actuator_TE_regs['P_ACT_COMP_EN_TE_REG'])[0]) & 0x2
#     pwr_act_tf_enable_te = int(generator.reportMemoryAddressValue('1:4', power_actuator_TE_regs['P_ACT_COMP_EN_TE_REG'])[0]) & 0x4

#     if pwr_act_enable_te:
#         pwr_act_enable_te = 1
#     if pwr_act_ff_enable_te:
#         pwr_act_ff_enable_te = 1
#     if pwr_act_tf_enable_te:
#         pwr_act_tf_enable_te = 1

#     LD12_FE = generator.reportMemoryAddressValue('1:4', power_actuator_FE_regs['FR_P_ACT_LD12_FE_REG'])[0]
#     LG12_FE = generator.reportMemoryAddressValue('1:4', power_actuator_FE_regs['FR_P_ACT_LG12_FE_REG'])[0]
#     pwr_act_scale_norm_fe = generator.reportMemoryAddressValue('1:4', power_actuator_FE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_FE_REG'])[0]
#     fr_p_act_ld_c1_fe = LD12_FE & 0xFFFF
#     fr_p_act_ld_c2_fe = (LD12_FE >> 16) & 0xFFFF
#     fr_p_act_ld_c3_fe = generator.reportMemoryAddressValue('1:4', power_actuator_FE_regs['FR_P_ACT_LD3_FE_REG'])[0]
#     fr_p_act_lg_c1_fe = LG12_FE & 0xFFFF
#     fr_p_act_lg_c2_fe = (LG12_FE >> 16) & 0xFFFF
#     fr_p_act_lg_c3_fe = generator.reportMemoryAddressValue('1:4', power_actuator_FE_regs['FR_P_ACT_LG3_FE_REG'])[0]
#     pwr_act_ctrl_scale_fe = generator.reportMemoryAddressValue('1:4', power_actuator_FE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_FE_REG'])[0]
#     pwr_act_ctrl_ff_scale_fe = generator.reportMemoryAddressValue('1:4', power_actuator_FE_regs['PWR_ACTUATOR_CTRL_FF_SCALING_FE_REG'])[0]
#     pwr_act_enable_fe = int(generator.reportMemoryAddressValue('1:4', power_actuator_FE_regs['P_ACT_COMP_EN_FE_REG'])[0]) & 0x1
#     pwr_act_ff_enable_fe = int(generator.reportMemoryAddressValue('1:4', power_actuator_FE_regs['P_ACT_COMP_EN_FE_REG'])[0]) & 0x2
#     pwr_act_tf_enable_fe = int(generator.reportMemoryAddressValue('1:4', power_actuator_FE_regs['P_ACT_COMP_EN_FE_REG'])[0]) & 0x4

#     if pwr_act_enable_fe:
#         pwr_act_enable_fe = 1
#     if pwr_act_ff_enable_fe:
#         pwr_act_ff_enable_fe = 1
#     if pwr_act_tf_enable_fe:
#         pwr_act_tf_enable_fe = 1

#     LD12_RE_SE = generator.reportMemoryAddressValue('1:4', power_actuator_RE_SE_regs['FR_P_ACT_LD12_RE_SE_REG'])[0]
#     LG12_RE_SE = generator.reportMemoryAddressValue('1:4', power_actuator_RE_SE_regs['FR_P_ACT_LG12_RE_SE_REG'])[0]
#     pwr_act_scale_norm_re_se = generator.reportMemoryAddressValue('1:4', power_actuator_RE_SE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_RE_SE_REG'])[0]
#     fr_p_act_ld_c1_re_se = LD12_RE_SE & 0xFFFF
#     fr_p_act_ld_c2_re_se = (LD12_RE_SE >> 16) & 0xFFFF
#     fr_p_act_ld_c3_re_se = generator.reportMemoryAddressValue('1:4', power_actuator_RE_SE_regs['FR_P_ACT_LD3_RE_SE_REG'])[0]
#     fr_p_act_lg_c1_re_se = LG12_RE_SE & 0xFFFF
#     fr_p_act_lg_c2_re_se = (LG12_RE_SE >> 16) & 0xFFFF
#     fr_p_act_lg_c3_re_se = generator.reportMemoryAddressValue('1:4', power_actuator_RE_SE_regs['FR_P_ACT_LG3_RE_SE_REG'])[0]
#     pwr_act_ctrl_scale_re_se = generator.reportMemoryAddressValue('1:4', power_actuator_RE_SE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_RE_SE_REG'])[0]
#     pwr_act_ctrl_ff_scale_re_se = generator.reportMemoryAddressValue('1:4', power_actuator_RE_SE_regs['PWR_ACTUATOR_CTRL_FF_SCALING_RE_SE_REG'])[0]
#     pwr_act_enable_re_se = int(generator.reportMemoryAddressValue('1:4', power_actuator_RE_SE_regs['P_ACT_COMP_EN_RE_SE_REG'])[0]) & 0x1
#     pwr_act_ff_enable_re_se = int(generator.reportMemoryAddressValue('1:4', power_actuator_RE_SE_regs['P_ACT_COMP_EN_RE_SE_REG'])[0]) & 0x2
#     pwr_act_tf_enable_re_se = int(generator.reportMemoryAddressValue('1:4', power_actuator_RE_SE_regs['P_ACT_COMP_EN_RE_SE_REG'])[0]) & 0x4

#     if pwr_act_enable_re_se:
#         pwr_act_enable_re_se = 1
#     if pwr_act_ff_enable_re_se:
#         pwr_act_ff_enable_re_se = 1
#     if pwr_act_tf_enable_re_se:
#         pwr_act_tf_enable_re_se = 1

#     LD12_FE_SE = generator.reportMemoryAddressValue('1:4', power_actuator_FE_SE_regs['FR_P_ACT_LD12_FE_SE_REG'])[0]
#     LG12_FE_SE = generator.reportMemoryAddressValue('1:4', power_actuator_FE_SE_regs['FR_P_ACT_LG12_FE_SE_REG'])[0]
#     pwr_act_scale_norm_fe_se = generator.reportMemoryAddressValue('1:4', power_actuator_FE_SE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_FE_SE_REG'])[0]
#     fr_p_act_ld_c1_fe_se = LD12_FE_SE & 0xFFFF
#     fr_p_act_ld_c2_fe_se = (LD12_FE_SE >> 16) & 0xFFFF
#     fr_p_act_ld_c3_fe_se = generator.reportMemoryAddressValue('1:4', power_actuator_FE_SE_regs['FR_P_ACT_LD3_FE_SE_REG'])[0]
#     fr_p_act_lg_c1_fe_se = LG12_FE_SE & 0xFFFF
#     fr_p_act_lg_c2_fe_se = (LG12_FE_SE >> 16) & 0xFFFF
#     fr_p_act_lg_c3_fe_se = generator.reportMemoryAddressValue('1:4', power_actuator_FE_SE_regs['FR_P_ACT_LG3_FE_SE_REG'])[0]
#     pwr_act_ctrl_scale_fe_se = generator.reportMemoryAddressValue('1:4', power_actuator_FE_SE_regs['PWR_ACTUATOR_SCALING_CONSTANT_NRMLZD_FE_SE_REG'])[0]
#     pwr_act_ctrl_ff_scale_fe_se = generator.reportMemoryAddressValue('1:4', power_actuator_FE_SE_regs['PWR_ACTUATOR_CTRL_FF_SCALING_FE_SE_REG'])[0]
#     pwr_act_enable_fe_se = int(generator.reportMemoryAddressValue('1:4', power_actuator_FE_SE_regs['P_ACT_COMP_EN_FE_SE_REG'])[0]) & 0x1
#     pwr_act_ff_enable_fe_se = int(generator.reportMemoryAddressValue('1:4', power_actuator_FE_SE_regs['P_ACT_COMP_EN_FE_SE_REG'])[0]) & 0x2
#     pwr_act_tf_enable_fe_se = int(generator.reportMemoryAddressValue('1:4', power_actuator_FE_SE_regs['P_ACT_COMP_EN_FE_SE_REG'])[0]) & 0x4

#     if pwr_act_enable_fe_se:
#         pwr_act_enable_fe_se = 1
#     if pwr_act_ff_enable_fe_se:
#         pwr_act_ff_enable_fe_se = 1
#     if pwr_act_tf_enable_fe_se:
#         pwr_act_tf_enable_fe_se = 1

#     tmp_fast_max_incr_le = generator.reportMemoryAddressValue('1:4', FAST_MAX_INCR_LE_REG)[0]
#     fast_max_incr_le_up = (tmp_fast_max_incr_le >> 16) & 0xFFFF
#     fast_max_incr_le_down = tmp_fast_max_incr_le & 0xFFFF
#     tmp_fast_max_incr_te = generator.reportMemoryAddressValue('1:4', FAST_MAX_INCR_TE_REG)[0]
#     fast_max_incr_te_up = (tmp_fast_max_incr_te >> 16) & 0xFFFF
#     fast_max_incr_te_down = tmp_fast_max_incr_te & 0xFFFF
#     tmp_fast_max_incr_fe = generator.reportMemoryAddressValue('1:4', FAST_MAX_INCR_FE_REG)[0]
#     fast_max_incr_fe_up = (tmp_fast_max_incr_fe >> 16) & 0xFFFF
#     fast_max_incr_fe_down = tmp_fast_max_incr_fe & 0xFFFF
#     tmp_fast_max_incr_re_se = generator.reportMemoryAddressValue('1:4', FAST_MAX_INCR_RE_SE_REG)[0]
#     fast_max_incr_re_se_up = (tmp_fast_max_incr_re_se >> 16) & 0xFFFF
#     fast_max_incr_re_se_down = tmp_fast_max_incr_re_se & 0xFFFF
#     tmp_fast_max_incr_fe_se = generator.reportMemoryAddressValue('1:4', FAST_MAX_INCR_FE_SE_REG)[0]
#     fast_max_incr_fe_se_up = (tmp_fast_max_incr_fe_se >> 16) & 0xFFFF
#     fast_max_incr_fe_se_down = tmp_fast_max_incr_fe_se & 0xFFFF

#     hiper_fdfwd_re_en = int(generator.reportMemoryAddressValue('1:4', HIPER_FDFWD_EN_REG)[0]) & 0x1
#     hiper_fdfwd_le_en = int(generator.reportMemoryAddressValue('1:4', HIPER_FDFWD_EN_REG)[0]) & 0x2
#     hiper_fdfwd_te_en = int(generator.reportMemoryAddressValue('1:4', HIPER_FDFWD_EN_REG)[0]) & 0x4
#     hiper_fdfwd_fe_en = int(generator.reportMemoryAddressValue('1:4', HIPER_FDFWD_EN_REG)[0]) & 0x8
#     hiper_fdfwd_re_se_en = int(generator.reportMemoryAddressValue('1:4', HIPER_FDFWD_EN_REG)[0]) & 0x10
#     hiper_fdfwd_fe_se_en = int(generator.reportMemoryAddressValue('1:4', HIPER_FDFWD_EN_REG)[0]) & 0x20
#     hiper_fdfwd_gs_fdbk_en = int(generator.reportMemoryAddressValue('1:4', HIPER_FDFWD_EN_REG)[0]) & 0x40

#     if hiper_fdfwd_re_en:
#         hiper_fdfwd_re_en = 1
#     if hiper_fdfwd_le_en:
#         hiper_fdfwd_le_en = 1
#     if hiper_fdfwd_te_en:
#         hiper_fdfwd_te_en = 1
#     if hiper_fdfwd_fe_en:
#         hiper_fdfwd_fe_en = 1
#     if hiper_fdfwd_re_se_en:
#         hiper_fdfwd_re_se_en = 1
#     if hiper_fdfwd_fe_se_en:
#         hiper_fdfwd_fe_se_en = 1
#     if hiper_fdfwd_gs_fdbk_en:
#         hiper_fdfwd_gs_fdbk_en = 1

#     gs_state_fdbk_re_en = int(generator.reportMemoryAddressValue('1:4', GS_STATE_FDBK_EN_REG)[0]) & 0x1
#     gs_state_fdbk_le_en = int(generator.reportMemoryAddressValue('1:4', GS_STATE_FDBK_EN_REG)[0]) & 0x2
#     gs_state_fdbk_te_en = int(generator.reportMemoryAddressValue('1:4', GS_STATE_FDBK_EN_REG)[0]) & 0x4
#     gs_state_fdbk_fe_en = int(generator.reportMemoryAddressValue('1:4', GS_STATE_FDBK_EN_REG)[0]) & 0x8
#     gs_state_fdbk_re_se_en = int(generator.reportMemoryAddressValue('1:4', GS_STATE_FDBK_EN_REG)[0]) & 0x10
#     gs_state_fdbk_fe_se_en = int(generator.reportMemoryAddressValue('1:4', GS_STATE_FDBK_EN_REG)[0]) & 0x20

#     if gs_state_fdbk_re_en:
#         gs_state_fdbk_re_en = 1
#     if gs_state_fdbk_le_en:
#         gs_state_fdbk_le_en = 1
#     if gs_state_fdbk_te_en:
#         gs_state_fdbk_te_en = 1
#     if gs_state_fdbk_fe_en:
#         gs_state_fdbk_fe_en = 1
#     if gs_state_fdbk_re_se_en:
#         gs_state_fdbk_re_se_en = 1
#     if gs_state_fdbk_fe_se_en:
#         gs_state_fdbk_fe_se_en = 1

#     gs_pwr_act_scale_re_en = int(generator.reportMemoryAddressValue('1:4', GS_PWR_ACT_SCALE_EN_REG)[0]) & 0x1
#     gs_pwr_act_scale_le_en = int(generator.reportMemoryAddressValue('1:4', GS_PWR_ACT_SCALE_EN_REG)[0]) & 0x2
#     gs_pwr_act_scale_te_en = int(generator.reportMemoryAddressValue('1:4', GS_PWR_ACT_SCALE_EN_REG)[0]) & 0x4
#     gs_pwr_act_scale_fe_en = int(generator.reportMemoryAddressValue('1:4', GS_PWR_ACT_SCALE_EN_REG)[0]) & 0x8
#     gs_pwr_act_scale_re_se_en = int(generator.reportMemoryAddressValue('1:4', GS_PWR_ACT_SCALE_EN_REG)[0]) & 0x10
#     gs_pwr_act_scale_fe_se_en = int(generator.reportMemoryAddressValue('1:4', GS_PWR_ACT_SCALE_EN_REG)[0]) & 0x20

#     if gs_pwr_act_scale_re_en:
#         gs_pwr_act_scale_re_en = 1
#     if gs_pwr_act_scale_le_en:
#         gs_pwr_act_scale_le_en = 1
#     if gs_pwr_act_scale_te_en:
#         gs_pwr_act_scale_te_en = 1
#     if gs_pwr_act_scale_fe_en:
#         gs_pwr_act_scale_fe_en = 1
#     if gs_pwr_act_scale_re_se_en:
#         gs_pwr_act_scale_re_se_en = 1
#     if gs_pwr_act_scale_fe_se_en:
#         gs_pwr_act_scale_fe_se_en = 1

#     imc_re_en = int(generator.reportMemoryAddressValue('1:4', IMC_EN_REG)[0]) & 0x1
#     imc_le_en = int(generator.reportMemoryAddressValue('1:4', IMC_EN_REG)[0]) & 0x2
#     imc_te_en = int(generator.reportMemoryAddressValue('1:4', IMC_EN_REG)[0]) & 0x4
#     imc_fe_en = int(generator.reportMemoryAddressValue('1:4', IMC_EN_REG)[0]) & 0x8
#     imc_re_se_en = int(generator.reportMemoryAddressValue('1:4', IMC_EN_REG)[0]) & 0x10
#     imc_fe_se_en = int(generator.reportMemoryAddressValue('1:4', IMC_EN_REG)[0]) & 0x20
#     imc_gs_en = int(generator.reportMemoryAddressValue('1:4', IMC_EN_REG)[0]) & 0x40

#     if imc_re_en:
#         imc_re_en = 1
#     if imc_le_en:
#         imc_le_en = 1
#     if imc_te_en:
#         imc_te_en = 1
#     if imc_fe_en:
#         imc_fe_en = 1
#     if imc_re_se_en:
#         imc_re_se_en = 1
#     if imc_fe_se_en:
#         imc_fe_se_en = 1
#     if imc_gs_en:
#         imc_gs_en = 1

#     tmp_K_L_2_up = generator.reportMemoryAddressValue('1:4', mainPID_GS_regs['K_L_2_UP_REG'])[0]
#     tmp_K_L_2_dn = generator.reportMemoryAddressValue('1:4', mainPID_GS_regs['K_L_2_DN_REG'])[0]
#     nl_zone_threshold = generator.reportMemoryAddressValue('1:4', mainPID_GS_regs['NL_ZONE_THRES_REG'])[0]
#     nl_2_nl_up = (tmp_K_L_2_up >> 16) & 0xFFFF
#     nl_2_l_up = tmp_K_L_2_up & 0xFFFF
#     nl_2_nl_down = (tmp_K_L_2_dn >> 16) & 0xFFFF
#     l_2_nl_down = tmp_K_L_2_dn & 0xFFFF

#     f_imc_ff_kp = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_FF_KP_RE_REG'])[0]
#     f_imc_ff_c1 = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_FF_C1_RE_REG'])[0]
#     f_imc_ff_c2 = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_FF_C2_RE_REG'])[0]
#     f_imc_ff_c3 = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_FF_C3_RE_REG'])[0]
#     f_imc_ff_c4 = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_FF_C4_RE_REG'])[0]
#     f_imc_ff_c5 = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_FF_C5_RE_REG'])[0]

#     f_imc_process_kp = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_PROCESS_KP_RE_REG'])[0]
#     f_imc_process_c1 = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_PROCESS_C1_RE_REG'])[0]
#     f_imc_process_c2 = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_PROCESS_C2_RE_REG'])[0]
#     f_imc_process_c3 = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_PROCESS_C3_RE_REG'])[0]
#     f_imc_process_c4 = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_PROCESS_C4_RE_REG'])[0]
#     f_imc_process_c5 = generator.reportMemoryAddressValue('1:4', IMC_RE_regs['F_IMC_PROCESS_C5_RE_REG'])[0]

#     tmp_KPKD_spmod =  generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_KPROP_KDERIV'])[0]
#     tmp_FRC1C2_spmod =  generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_FR_C1_C2'])[0]
#     tmp_FRC3C4_spmod = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_FR_C3_C4'])[0]

#     spmod_enable = int(generator.reportMemoryAddressValue('1:4', SPMOD_ENABLE)[0]) & 0x1
#     spmod_kprop = tmp_KPKD_spmod & 0xFFFF
#     spmod_ki = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_KI_REG'])[0]
#     spmod_kderiv = (tmp_KPKD_spmod >> 16) & 0xFFFF
#     spmod_sat_lb = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_SAT_LB'])[0]
#     spmod_sat_ub = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_SAT_UB'])[0]
#     spmod_max_up_incr = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_MAX_UP_INCREMENT'])[0]
#     spmod_max_down_incr = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_MAX_DOWN_INCREMENT'])[0]
#     spmod_fr_c1 = tmp_FRC1C2_spmod & 0xFFFF
#     spmod_fr_c2 = (tmp_FRC1C2_spmod >> 16) & 0xFFFF
#     spmod_fr_c3 = tmp_FRC3C4_spmod & 0xFFFF
#     spmod_fr_c4 = (tmp_FRC3C4_spmod >> 16) & 0xFFFF
#     spmod_fy_c1 = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_FY_C1'])[0]
#     spmod_fy_c2 = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_FY_C2'])[0]
#     spmod_fy_c3 = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_FY_C3'])[0]
#     spmod_fy_c4 = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_FY_C4'])[0]
#     spmod_fy_c5 = generator.reportMemoryAddressValue('1:4', SP_modifier_regs['SPMOD_FY_C5'])[0]
#     backplane_fpga_app = generator.reportSoftwareRevisionLevel(101)

#     fName = outPath + rf'\controller_info_{now}.txt'
#     fInfo = open(fName, 'w', newline='')

#     fInfo.write(f'Controller Parameters\n=====================\n\
#     Backplane Version: {backplane_fpga_app}\
#     \n\nlimit_loop_en={limit_loop_en}\
#     \npdiss_max=1789569707\
#     \npdiss_gain=1265\
#     \nplin1_max={plin1_max}\
#     \nplin1_gain={plin1_gain}\
#     \nplin2_max={plin2_max}\
#     \nplin2_gain={plin2_gain}\
#     \nplin_fet_volt_offset=1371\
#     \nplin_fet_volt_gain=37339\
#     \nplin_fet_volt_clamp=59713\
#     \npldmos1_max={pldmos1_max}\
#     \npldmos1_gain={pldmos1_gain}\
#     \npldmos2_max={pldmos2_max}\
#     \npldmos2_gain={pldmos2_gain}\
#     \ndci1_max={dci1_max}\
#     \ndci1_gain={dci1_gain}\
#     \ndci2_max={dci2_max}\
#     \ndci2_gain={dci2_gain}\
#     \npphntm_gain={pphntm_gain}\
#     \npphntm_max={pphntm_max}\
#     \nk_pphntm_thr={k_pphntm_thr}\
#     \nprfl_gain={prfl_gain}\
#     \nk_prfl_thr={k_prfl_thr}\
#     \nprfl_max={prfl_max}\
#     \ntau_blankout={tau_blankout}\
#     \n\
#     \nth_nrmlzd={th_nrmlzd}\
#     \nkp={kp}\
#     \nki={ki}\
#     \nkd={kd}\
#     \nfy_c1={fy_c1}\
#     \nfy_c2={fy_c2}\
#     \nfy_c3={fy_c3}\
#     \nfy_c4={fy_c4}\
#     \nfy_c5={fy_c5}\
#     \nfr_c1={fr_c1}\
#     \nfr_c2={fr_c2}\
#     \nfr_c3={fr_c3}\
#     \nfr_c4={fr_c4}\n\
#     zero_setpoint=357914\n\
#     fast_sat_lb=0\n\
#     fast_sat_ub=65535\n\
#     max_up_incr=65535\n\
#     max_dn_incr=65535\n\
#     fast_max_up_increment={fast_max_up_increment}\n\
#     fast_max_down_increment={fast_max_down_increment}\n\
#     fast_iv=0\n\
#     ctrl_dc_chan_enable=0x3\n\
#     ctrl_scale_factor=1706\n\
#     dead_time_mask_mode=1\n\
#     dead_time_count={dead_time_count}\n\
#     \n\
#     altz_en={altz_en}\n\
#     altz_normalized_k={altz_normalized_k}\n\
#     altz_kp={altz_kp}\n\
#     altz_ki={altz_ki}\n\
#     altz_kd={altz_kd}\n\
#     altz_sat_lb=0\n\
#     altz_sat_ub=0xFFFF\n\
#     altz_max_up_increment=3276\n\
#     altz_max_down_increment=3276\n\
#     altz_iv=0\n\
#     altz_tau_blankout={altz_tau_blankout}\n\
#     fy_c1_altz={fy_c1_altz}\n\
#     fy_c2_altz={fy_c2_altz}\n\
#     fy_c3_altz={fy_c3_altz}\n\
#     fy_c4_altz={fy_c4_altz}\n\
#     fy_c5_altz={fy_c5_altz}\n\
#     fr_c1_altz={fr_c1_altz}\n\
#     fr_c2_altz={fr_c2_altz}\n\
#     fr_c3_altz={fr_c3_altz}\n\
#     fr_c4_altz={fr_c4_altz}\n\
#     hiper_en={hiper_en}\n\
#     hiper_kp={hiper_kp}\n\
#     hiper_kd={hiper_kd}\n\
#     hiper_ki={hiper_ki}\n\
#     hiper_iv={hiper_iv}\n\
#     hiper_max={hiper_max}\n\
#     hiper_min={hiper_min}\n\
#     hiper_smart_en={hiper_smart_en}\n\
#     hiper_smart_thresh_up={hiper_smart_thresh_up}\n\
#     hiper_smart_thresh_down={hiper_smart_thresh_down}\n\
#     hiper_repetitive_en={hiper_repetitive_en}\n\
#     anc_01_enable={anc_01_enable}\n\
#     f_anc1_c1={f_anc1_c1}\n\
#     f_anc1_c2={f_anc1_c2}\n\
#     f_anc1_c3={f_anc1_c3}\n\
#     anc_02_enable={anc_02_enable}\n\
#     f_anc2_c1={f_anc2_c1}\n\
#     f_anc2_c2={f_anc2_c2}\n\
#     f_anc2_c3={f_anc2_c3}\n\
#     anc_03_enable={anc_03_enable}\n\
#     f_anc3_c1={f_anc3_c1}\n\
#     f_anc3_c2={f_anc3_c2}\n\
#     f_anc3_c3={f_anc3_c3}\n\
#     hybrid_mode_en={hybrid_mode_en}\n\
#     recovery_max_up_incr={recovery_max_up_incr}\n\
#     recovery_max_dn_incr={recovery_max_dn_incr}\n\
#     recovery_th_up_nrmlzd={recovery_th_up_nrmlzd}\n\
#     recovery_th_dn_nrmlzd={recovery_th_dn_nrmlzd}\n\
#     \n\
#     pwr_act_scale_norm={pwr_act_scale_norm}\n\
#     fr_p_act_ld_c1={fr_p_act_ld_c1}\n\
#     fr_p_act_ld_c2={fr_p_act_ld_c2}\n\
#     fr_p_act_ld_c3={fr_p_act_ld_c3}\n\
#     fr_p_act_lg_c1={fr_p_act_lg_c1}\n\
#     fr_p_act_lg_c2={fr_p_act_lg_c2}\n\
#     fr_p_act_lg_c3={fr_p_act_lg_c3}\n\
#     pwr_act_ctrl_scale={pwr_act_ctrl_scale}\n\
#     pwr_act_ctrl_ff_scale={pwr_act_ctrl_ff_scale}\n\
#     pwr_act_enable={pwr_act_enable}\n\
#     pwr_act_ff_enable={pwr_act_ff_enable}\n\
#     pwr_act_tf_enable={pwr_act_tf_enable}\n\
#     \n\
#     mainpid_le_kprop={mainpid_le_kprop}\n\
#     mainpid_le_ki={mainpid_le_ki}\n\
#     mainpid_le_kderiv={mainpid_le_kderiv}\n\
#     mainpid_le_fr_c1={mainpid_le_fr_c1}\n\
#     mainpid_le_fr_c2={mainpid_le_fr_c2}\n\
#     mainpid_le_fr_c3={mainpid_le_fr_c3}\n\
#     mainpid_le_fr_c4={mainpid_le_fr_c4}\n\
#     mainpid_le_fy_c1={mainpid_le_fy_c1}\n\
#     mainpid_le_fy_c2={mainpid_le_fy_c2}\n\
#     mainpid_le_fy_c3={mainpid_le_fy_c3}\n\
#     mainpid_le_fy_c4={mainpid_le_fy_c4}\n\
#     mainpid_le_fy_c5={mainpid_le_fy_c5}\n\
#     \n\
#     mainpid_te_kprop={mainpid_te_kprop}\n\
#     mainpid_te_ki={mainpid_te_ki}\n\
#     mainpid_te_kderiv={mainpid_te_kderiv}\n\
#     mainpid_te_fr_c1={mainpid_te_fr_c1}\n\
#     mainpid_te_fr_c2={mainpid_te_fr_c2}\n\
#     mainpid_te_fr_c3={mainpid_te_fr_c3}\n\
#     mainpid_te_fr_c4={mainpid_te_fr_c4}\n\
#     mainpid_te_fy_c1={mainpid_te_fy_c1}\n\
#     mainpid_te_fy_c2={mainpid_te_fy_c2}\n\
#     mainpid_te_fy_c3={mainpid_te_fy_c3}\n\
#     mainpid_te_fy_c4={mainpid_te_fy_c4}\n\
#     mainpid_te_fy_c5={mainpid_te_fy_c5}\n\
#     \n\
#     mainpid_fe_kprop={mainpid_fe_kprop}\n\
#     mainpid_fe_ki={mainpid_fe_ki}\n\
#     mainpid_fe_kderiv={mainpid_fe_kderiv}\n\
#     mainpid_fe_fr_c1={mainpid_fe_fr_c1}\n\
#     mainpid_fe_fr_c2={mainpid_fe_fr_c2}\n\
#     mainpid_fe_fr_c3={mainpid_fe_fr_c3}\n\
#     mainpid_fe_fr_c4={mainpid_fe_fr_c4}\n\
#     mainpid_fe_fy_c1={mainpid_fe_fy_c1}\n\
#     mainpid_fe_fy_c2={mainpid_fe_fy_c2}\n\
#     mainpid_fe_fy_c3={mainpid_fe_fy_c3}\n\
#     mainpid_fe_fy_c4={mainpid_fe_fy_c4}\n\
#     mainpid_fe_fy_c5={mainpid_fe_fy_c5}\n\
#     \n\
#     mainpid_se_kprop={mainpid_se_kprop}\n\
#     mainpid_se_ki={mainpid_se_ki}\n\
#     mainpid_se_kderiv={mainpid_se_kderiv}\n\
#     mainpid_se_fr_c1={mainpid_se_fr_c1}\n\
#     mainpid_se_fr_c2={mainpid_se_fr_c2}\n\
#     mainpid_se_fr_c3={mainpid_se_fr_c3}\n\
#     mainpid_se_fr_c4={mainpid_se_fr_c4}\n\
#     mainpid_se_fy_c1={mainpid_se_fy_c1}\n\
#     mainpid_se_fy_c2={mainpid_se_fy_c2}\n\
#     mainpid_se_fy_c3={mainpid_se_fy_c3}\n\
#     mainpid_se_fy_c4={mainpid_se_fy_c4}\n\
#     mainpid_se_fy_c5={mainpid_se_fy_c5}\n\
#     \n\
#     mainpid_fe_se_kprop={mainpid_fe_se_kprop}\n\
#     mainpid_fe_se_ki={mainpid_fe_se_ki}\n\
#     mainpid_fe_se_kderiv={mainpid_fe_se_kderiv}\n\
#     mainpid_fe_se_fr_c1={mainpid_fe_se_fr_c1}\n\
#     mainpid_fe_se_fr_c2={mainpid_fe_se_fr_c2}\n\
#     mainpid_fe_se_fr_c3={mainpid_fe_se_fr_c3}\n\
#     mainpid_fe_se_fr_c4={mainpid_fe_se_fr_c4}\n\
#     mainpid_fe_se_fy_c1={mainpid_fe_se_fy_c1}\n\
#     mainpid_fe_se_fy_c2={mainpid_fe_se_fy_c2}\n\
#     mainpid_fe_se_fy_c3={mainpid_fe_se_fy_c3}\n\
#     mainpid_fe_se_fy_c4={mainpid_fe_se_fy_c4}\n\
#     mainpid_fe_se_fy_c5={mainpid_fe_se_fy_c5}\n\
#     \n\
#     re_se_thresh_abs={re_se_thresh_abs}\n\
#     fe_se_thresh_abs={fe_se_thresh_abs}\n\
#     tau_blankout_fe={tau_blankout_fe}\n\
#     tau_blankout_re_se={tau_blankout_re_se}\n\
#     tau_blankout_fe_se={tau_blankout_fe_se}\n\
#     \n\
#     pwr_act_scale_norm_le={pwr_act_scale_norm_le}\n\
#     fr_p_act_ld_c1_le={fr_p_act_ld_c1_le}\n\
#     fr_p_act_ld_c2_le={fr_p_act_ld_c2_le}\n\
#     fr_p_act_ld_c3_le={fr_p_act_ld_c3_le}\n\
#     fr_p_act_lg_c1_le={fr_p_act_lg_c1_le}\n\
#     fr_p_act_lg_c2_le={fr_p_act_lg_c2_le}\n\
#     fr_p_act_lg_c3_le={fr_p_act_lg_c3_le}\n\
#     pwr_act_ctrl_scale_le={pwr_act_ctrl_scale_le}\n\
#     pwr_act_ctrl_ff_scale_le={pwr_act_ctrl_ff_scale_le}\n\
#     pwr_act_enable_le={pwr_act_enable_le}\n\
#     pwr_act_ff_enable_le={pwr_act_ff_enable_le}\n\
#     pwr_act_tf_enable_le={pwr_act_tf_enable_le}\n\
#     \n\
#     pwr_act_scale_norm_te={pwr_act_scale_norm_te}\n\
#     fr_p_act_ld_c1_te={fr_p_act_ld_c1_te}\n\
#     fr_p_act_ld_c2_te={fr_p_act_ld_c2_te}\n\
#     fr_p_act_ld_c3_te={fr_p_act_ld_c3_te}\n\
#     fr_p_act_lg_c1_te={fr_p_act_lg_c1_te}\n\
#     fr_p_act_lg_c2_te={fr_p_act_lg_c2_te}\n\
#     fr_p_act_lg_c3_te={fr_p_act_lg_c3_te}\n\
#     pwr_act_ctrl_scale_te={pwr_act_ctrl_scale_te}\n\
#     pwr_act_ctrl_ff_scale_te={pwr_act_ctrl_ff_scale_te}\n\
#     pwr_act_enable_te={pwr_act_enable_te}\n\
#     pwr_act_ff_enable_te={pwr_act_ff_enable_te}\n\
#     pwr_act_tf_enable_te={pwr_act_tf_enable_te}\n\
#     \n\
#     pwr_act_scale_norm_fe={pwr_act_scale_norm_fe}\n\
#     fr_p_act_ld_c1_fe={fr_p_act_ld_c1_fe}\n\
#     fr_p_act_ld_c2_fe={fr_p_act_ld_c2_fe}\n\
#     fr_p_act_ld_c3_fe={fr_p_act_ld_c3_fe}\n\
#     fr_p_act_lg_c1_fe={fr_p_act_lg_c1_fe}\n\
#     fr_p_act_lg_c2_fe={fr_p_act_lg_c2_fe}\n\
#     fr_p_act_lg_c3_fe={fr_p_act_lg_c3_fe}\n\
#     pwr_act_ctrl_scale_fe={pwr_act_ctrl_scale_fe}\n\
#     pwr_act_ctrl_ff_scale_fe={pwr_act_ctrl_ff_scale_fe}\n\
#     pwr_act_enable_fe={pwr_act_enable_fe}\n\
#     pwr_act_ff_enable_fe={pwr_act_ff_enable_fe}\n\
#     pwr_act_tf_enable_fe={pwr_act_tf_enable_fe}\n\
#     \n\
#     pwr_act_scale_norm_re_se={pwr_act_scale_norm_re_se}\n\
#     fr_p_act_ld_c1_re_se={fr_p_act_ld_c1_re_se}\n\
#     fr_p_act_ld_c2_re_se={fr_p_act_ld_c2_re_se}\n\
#     fr_p_act_ld_c3_re_se={fr_p_act_ld_c3_re_se}\n\
#     fr_p_act_lg_c1_re_se={fr_p_act_lg_c1_re_se}\n\
#     fr_p_act_lg_c2_re_se={fr_p_act_lg_c2_re_se}\n\
#     fr_p_act_lg_c3_re_se={fr_p_act_lg_c3_re_se}\n\
#     pwr_act_ctrl_scale_re_se={pwr_act_ctrl_scale_re_se}\n\
#     pwr_act_ctrl_ff_scale_re_se={pwr_act_ctrl_ff_scale_re_se}\n\
#     pwr_act_enable_re_se={pwr_act_enable_re_se}\n\
#     pwr_act_ff_enable_re_se={pwr_act_ff_enable_re_se}\n\
#     pwr_act_tf_enable_re_se={pwr_act_tf_enable_re_se}\n\
#     \n\
#     pwr_act_scale_norm_fe_se={pwr_act_scale_norm_fe_se}\n\
#     fr_p_act_ld_c1_fe_se={fr_p_act_ld_c1_fe_se}\n\
#     fr_p_act_ld_c2_fe_se={fr_p_act_ld_c2_fe_se}\n\
#     fr_p_act_ld_c3_fe_se={fr_p_act_ld_c3_fe_se}\n\
#     fr_p_act_lg_c1_fe_se={fr_p_act_lg_c1_fe_se}\n\
#     fr_p_act_lg_c2_fe_se={fr_p_act_lg_c2_fe_se}\n\
#     fr_p_act_lg_c3_fe_se={fr_p_act_lg_c3_fe_se}\n\
#     pwr_act_ctrl_scale_fe_se={pwr_act_ctrl_scale_fe_se}\n\
#     pwr_act_ctrl_ff_scale_fe_se={pwr_act_ctrl_ff_scale_fe_se}\n\
#     pwr_act_enable_fe_se={pwr_act_enable_fe_se}\n\
#     pwr_act_ff_enable_fe_se={pwr_act_ff_enable_fe_se}\n\
#     pwr_act_tf_enable_fe_se={pwr_act_tf_enable_fe_se}\n\
#     \n\
#     fast_max_incr_le_up={fast_max_incr_le_up}\n\
#     fast_max_incr_le_down={fast_max_incr_le_down}\n\
#     fast_max_incr_te_up={fast_max_incr_te_up}\n\
#     fast_max_incr_te_down={fast_max_incr_te_down}\n\
#     fast_max_incr_fe_up={fast_max_incr_fe_up}\n\
#     fast_max_incr_fe_down={fast_max_incr_fe_down}\n\
#     fast_max_incr_re_se_up={fast_max_incr_re_se_up}\n\
#     fast_max_incr_re_se_down={fast_max_incr_re_se_down}\n\
#     fast_max_incr_fe_se_up={fast_max_incr_fe_se_up}\n\
#     fast_max_incr_fe_se_down={fast_max_incr_fe_se_down}\n\
#     \n\
#     hiper_fdfwd_re_en={hiper_fdfwd_re_en}\n\
#     hiper_fdfwd_le_en={hiper_fdfwd_le_en}\n\
#     hiper_fdfwd_te_en={hiper_fdfwd_te_en}\n\
#     hiper_fdfwd_fe_en={hiper_fdfwd_fe_en}\n\
#     hiper_fdfwd_re_se_en={hiper_fdfwd_re_se_en}\n\
#     hiper_fdfwd_fe_se_en={hiper_fdfwd_fe_se_en}\n\
#     hiper_fdfwd_gs_fdbk_en={hiper_fdfwd_gs_fdbk_en}\n\
#     \n\
#     gs_state_fdbk_re_en={gs_state_fdbk_re_en}\n\
#     gs_state_fdbk_le_en={gs_state_fdbk_le_en}\n\
#     gs_state_fdbk_te_en={gs_state_fdbk_te_en}\n\
#     gs_state_fdbk_fe_en={gs_state_fdbk_fe_en}\n\
#     gs_state_fdbk_re_se_en={gs_state_fdbk_re_se_en}\n\
#     gs_state_fdbk_fe_se_en={gs_state_fdbk_fe_se_en}\n\
#     \n\
#     gs_pwr_act_scale_re_en={gs_pwr_act_scale_re_en}\n\
#     gs_pwr_act_scale_le_en={gs_pwr_act_scale_le_en}\n\
#     gs_pwr_act_scale_te_en={gs_pwr_act_scale_te_en}\n\
#     gs_pwr_act_scale_fe_en={gs_pwr_act_scale_fe_en}\n\
#     gs_pwr_act_scale_re_se_en={gs_pwr_act_scale_re_se_en}\n\
#     gs_pwr_act_scale_fe_se_en={gs_pwr_act_scale_fe_se_en}\n\
#     \n\
#     imc_re_en={imc_re_en}\n\
#     imc_le_en={imc_le_en}\n\
#     imc_te_en={imc_te_en}\n\
#     imc_fe_en={imc_fe_en}\n\
#     imc_re_se_en={imc_re_se_en}\n\
#     imc_fe_se_en={imc_fe_se_en}\n\
#     imc_gs_en={imc_gs_en}\n\
#     \n\
#     nl_zone_threshold={nl_zone_threshold}\n\
#     nl_2_nl_up={nl_2_nl_up}\n\
#     nl_2_l_up={nl_2_l_up}\n\
#     nl_2_nl_down={nl_2_nl_down}\n\
#     l_2_nl_down={l_2_nl_down}\n\
#     \n\
#     f_imc_ff_kp={f_imc_ff_kp}\n\
#     f_imc_ff_c1={f_imc_ff_c1}\n\
#     f_imc_ff_c2={f_imc_ff_c2}\n\
#     f_imc_ff_c3={f_imc_ff_c3}\n\
#     f_imc_ff_c4={f_imc_ff_c4}\n\
#     f_imc_ff_c5={f_imc_ff_c5}\n\
#     \n\
#     f_imc_process_kp={f_imc_process_kp}\n\
#     f_imc_process_c1={f_imc_process_c1}\n\
#     f_imc_process_c2={f_imc_process_c2}\n\
#     f_imc_process_c3={f_imc_process_c3}\n\
#     f_imc_process_c4={f_imc_process_c4}\n\
#     f_imc_process_c5={f_imc_process_c5}\n\n')

#     for i in range(20):
#         for j in range(10):
#             generator.setMemoryAddressValue(K_GS_PARAM_ADDR_REG, int(j + 10*i))
#             tmp_kgs = generator.reportMemoryAddressValue('1:4', K_GS_PARAM_DATA_REG)[0]
#             tmp_kgs_even = (tmp_kgs >> 16) & 0xFFFF
#             tmp_kgs_odd = tmp_kgs & 0xFFFF
#             if i < 10:
#                 if 2*j < 10:
#                     fInfo.write(f'\nk_gs_0{i}_x_0{2*j}={tmp_kgs_even}\nk_gs_0{i}_x_0{2*j+1}={tmp_kgs_odd}')
#                 else:
#                     fInfo.write(f'\nk_gs_0{i}_x_{2*j}={tmp_kgs_even}\nk_gs_0{i}_x_{2*j+1}={tmp_kgs_odd}')
#             else:
#                 fInfo.write(f'\nk_gs_{i}_x_{2*j}={tmp_kgs_even}\nk_gs_{i}_x_{2*j+1}={tmp_kgs_odd}')

#     fInfo.write(f'\n\nspmod_enable={spmod_enable}\n\
#     spmod_kprop={spmod_kprop}\n\
#     spmod_ki={spmod_ki}\n\
#     spmod_kderiv={spmod_kderiv}\n\
#     spmod_sat_lb={spmod_sat_lb}\n\
#     spmod_sat_ub={spmod_sat_ub}\n\
#     spmod_max_up_incr={spmod_max_up_incr}\n\
#     spmod_max_down_incr={spmod_max_down_incr}\n\
#     spmod_fr_c1={spmod_fr_c1}\n\
#     spmod_fr_c2={spmod_fr_c2}\n\
#     spmod_fr_c3={spmod_fr_c3}\n\
#     spmod_fr_c4={spmod_fr_c4}\n\
#     spmod_fy_c1={spmod_fy_c1}\n\
#     spmod_fy_c2={spmod_fy_c2}\n\
#     spmod_fy_c3={spmod_fy_c3}\n\
#     spmod_fy_c4={spmod_fy_c4}\n\
#     spmod_fy_c5={spmod_fy_c5}')

#     fInfo.close()
#######################################################################################################


#######################################################################################################
def plotSmith_profile(data, data_name, outPath, info, freq, setpoint):
    # 3513 & 3540 Units
    # units = {'Generator Reported Forward Power':'W', 'Generator Reported Reflected Power':'W', 'Generator Reported Delivered Power':'W',
    #     'Lynx Reported Forward Power':'W', 'Lynx Reported Reflected Power':'W', 'Lynx Reported Delivered Power':'W', 
    #     'Lagging Side DC1 Current':'A', 'Leading Side DC1 Current':'A', 'Lagging Side DC2 Current':'A', 'Leading Side DC2 Current':'A','Lagging Side DC3 Current':'A', 'Leading Side DC3 Current':'A',
    #     'Lagging Side DC1 Voltage':'V', 'Leading Side DC1 Voltage':'V','Lagging Side DC2 Voltage':'V', 'Leading Side DC2 Voltage':'V','Lagging Side DC3 Voltage':'V', 'Leading Side DC3 Voltage':'V',
    #     'Lagging Side Linear Regulator Dissipation':'W', 'Leading Side Linear Regulator Dissipation':'W', 'Lagging Side LDMOS Dissipation':'W', 'Leading Side LDMOS Dissipation':'W',
    #     'Water In Temperature':'Â°C', 'Water Out Temperature':'Â°C', 'RF Coldplate Temperature':'Â°C'}
    # , 'Ptotal Yokogawa':'W',
    #   'Phase 1 Vrms':'V', 'Phase 1 Irms':'A', 'Phase 2 Vrms':'V', 'Phase 2 Irms':'A', 'Efficiency':'%', 'Leading Side Driver Power':'W', 'Lagging Side Driver Power':'W', 'Driver Power Difference (Leading - Lagging)':'W',
    #   'Phantom Power':'W', 'Coldplate Temperature Near DC Section':'Â°C', 'Coldplate Temperature Under LDMOS':'Â°C',
    #   'Lagging Tank Circuit Resistor Temperature':'Â°C', 'Lagging Buck FET Temperature (L)':'Â°C', 'Lagging Buck FET Temperature (R)':'Â°C', 'Lagging Buck Controller Temperature':'Â°C', 'Lagging Buck Inductor Temperature':'Â°C',
    #   'Leading Tank Circuit Resistor Temperature':'Â°C', 'Leading Buck FET Temperature (L)':'Â°C', 'Leading Buck FET Temperature (R)':'Â°C', 'Leading Buck Controller Temperature':'Â°C', 'Leading Buck Inductor Temperature':'Â°C'}
    #   #  ,'Leading LDMOS Temperature (L)':'Â°C', 'Leading LDMOS Temperature (R)':'Â°C', 'Lagging LDMOS Temperature (L)':'Â°C', 'Lagging LDMOS Temperature (R)':'Â°C',
        #  'Leading LDMOS Temperature Difference (R-L)':'Â°C', 'Lagging LDMOS Temperature Difference (R-L)':'Â°C', 'Leading Resistor Temperature (L)':'Â°C',
        # 'Leading Resistor Temperature (R)':'Â°C', 'Lagging Resistor Temperature (L)':'Â°C', 'Lagging Resistor Temperature (R)':'Â°C'}

    # units = {'Generator Reported Forward Power': 'W', 'Generator Reported Reflected Power': 'W',
    #     'Generator Reported Delivered Power': 'W',
    #     'Lynx Reported Forward Power': 'W', 'Lynx Reported Reflected Power': 'W',
    #     'Lynx Reported Delivered Power': 'W',
    #     'Lagging Side DC Current': 'A', 'Leading Side DC Current': 'A', 'Lagging Side DC Voltage': 'V',
    #     'Leading Side DC Voltage': 'V',
    #     'Lagging Side Linear Regulator Dissipation': 'W', 'Leading Side Linear Regulator Dissipation': 'W',
    #     'Lagging Side LDMOS Dissipation': 'W', 'Leading Side LDMOS Dissipation': 'W',
    #     'Water In Temperature': 'Â°C', 'Water Out Temperature': 'Â°C', 'RF Coldplate Temperature': 'Â°C'}

    # 10001 & 10002 MF Units
    units = {'Generator Reported Forward Power':'W', 'Generator Reported Reflected Power':'W', 'Generator Reported Delivered Power':'W',
        'Lynx Reported Forward Power':'W', 'Lynx Reported Reflected Power':'W', 'Lynx Reported Delivered Power':'W',
        'Lagging Side DC1 Current':'A', 'Leading Side DC1 Current':'A', 'Lagging Side DC2 Current':'A', 'Leading Side DC2 Current':'A','Lagging Side DC3 Current':'A', 'Leading Side DC3 Current':'A',
        'Lagging Side DC1 Voltage':'V', 'Leading Side DC1 Voltage':'V','Lagging Side DC2 Voltage':'V', 'Leading Side DC2 Voltage':'V','Lagging Side DC3 Voltage':'V', 'Leading Side DC3 Voltage':'V',
        'PA Dissipation': 'W'}
    #'Generator Reported Forward Power', 'Generator Reported Reflected Power', 'Generator Reported Delivered Power',
      # 'Lynx Reported Forward Power', 'Lynx Reported Reflected Power', 'Lynx Reported Delivered Power',
      # 'Lagging Side DC Current', 'Leading Side DC Current', 'Lagging Side DC Voltage', 'Leading Side DC Voltage',
      # 'Lagging Side Linear Regulator Dissipation', 'Leading Side Linear Regulator Dissipation', 'Lagging Side LDMOS Dissipation', 'Leading Side LDMOS Dissipation',
      # 'Water In Temperature', 'Water Out Temperature', 'RF Coldplate Temperature'

    numlines = np.genfromtxt(data, delimiter=',', usecols=[0], dtype=str)
    headers = np.genfromtxt(data, delimiter=',', skip_footer=len(numlines)-1, dtype=str)
    data_tot = np.genfromtxt(data, usecols=range(0, len(headers)), delimiter=',', dtype=str)
    R = []
    X = []
    gamma_real = []
    gamma_imag = []
    dta = []

    gamma_real_index = np.nonzero(data_tot[0] == 'Gamma Real')[0][0]
    gamma_imag_index = np.nonzero(data_tot[0] == 'Gamma Imag')[0][0]
    R_index = np.nonzero(data_tot[0] == 'R')[0][0]
    X_index = np.nonzero(data_tot[0] == 'X')[0][0]
    dta_index = np.nonzero(data_tot[0] == data_name)[0][0]

    for data_tot_i in data_tot[1:]:
        gamma_real.append(float(data_tot_i[gamma_real_index]))
        gamma_imag.append(float(data_tot_i[gamma_imag_index]))
        R.append(float(data_tot_i[R_index]))
        X.append(float(data_tot_i[X_index]))
        dta.append(float(data_tot_i[dta_index]))

    dta = np.array(dta)
    try:
        max_index = np.nonzero(dta == np.max(dta))
        max_index = int(max_index[0])
        Z_max = complex(R[max_index], X[max_index])
    except:
        max_index = np.nonzero(dta == np.max(dta))
        max_index = int(max_index[0][0])
        # pass
    R_plot, X_plot = np.meshgrid(np.linspace(-1, 1, 500), np.linspace(-1, 1, 500))
    gamma_plot = []

    for (gr_i,gi_i) in zip(gamma_real,gamma_imag):
        gamma_plot.append([gr_i, gi_i])

    data_2D = scipy.interpolate.griddata(gamma_plot, dta, (R_plot, X_plot))

    Z0 = 50
    vswr = [1.6, 2, 3, 5]
    # unit circle outline
    theta = np.linspace(0, 2*np.pi, 100)
    x1 = np.cos(theta)
    y1 = np.sin(theta)

    R = [10, 25, 50, 100, 250]
    X = [-250, -100, -50, -25, -10, 0, 10, 25, 50, 100, 250]
    R1 = np.arange(0, np.max(R), 10)

    plt.figure(figsize=(13,11))
    plt.plot(x1,y1,'k-')
    plt.axis('off')

    for Ri in R:
        g = (Ri - Z0)/(Ri + Z0)
        center = (1+g)/2
        r = 1 - center
        x = center + r*np.cos(theta)
        y = r*np.sin(theta)
        plt.plot(x,y, 'b--', alpha=0.1)
        plt.text(x=g+0.02, y=0, s=str(Ri), fontsize=10)

    for Xi in X:
        Z = R1 + 1j*Xi
        gamma = (Z - Z0)/(Z + Z0)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'b--', alpha=0.1)
        if np.min(x) > 0 > np.min(y):
            plt.text(x=np.min(x)+0.125, y=np.min(y)-0.04, s=str(Xi), fontsize=10)
        elif np.min(x) < 0 and np.min(y) < 0:
            plt.text(x=np.min(x)-0.12, y=np.min(y)-0.04, s=str(Xi), fontsize=10)
        elif np.min(x) > 0 and np.max(y) > 0:
            plt.text(x=np.min(x)+0.125, y=np.max(y)+0.03, s=str(Xi), fontsize=10)
        elif np.min(x) < 0 < np.max(y):
            plt.text(x=np.min(x)-0.12, y=np.max(y)+0.03, s=str(Xi), fontsize=10)
        elif np.min(x) == 0:
            if np.min(y) < 0:
                plt.text(x=-0.05, y=-1.06, s='-50', fontsize=10)
            # if np.max(y) > 0:
            #     plt.text(x=-0.03, y=1.03, s='50', fontsize=10)
        else:
            plt.text(x=-1.075, y=0, s='0', fontsize=10)

    for vswr_i in vswr:
        gamma = (vswr_i -  1)/(vswr_i + 1) * np.exp(1j*theta)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'r--', alpha=0.5)
        plt.text(x=-0.1, y=np.max(x)+0.02, s=f'$\\rho$ = {vswr_i}')

    try:
        plt.title(f'{data_name} {freq * 1e-3}MHz {setpoint}W Setpoint\nMaximum: {dta[max_index]:.2f}{units[data_name]} at {Z_max:.2f} ohms\nEverest {info}', fontsize=11)
    except:
        plt.title(f'{data_name} {freq * 1e-3}MHz {setpoint}W Setpoint\nMaximum: {dta[max_index]:.2f}{units[data_name]}\nEverest {info}', fontsize=11)

    plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta)/4), cmap='jet')
    plt.colorbar(label=units[data_name])

    plt.savefig(outPath+f'\\{data_name}_{freq}kHz_{setpoint}W.png')
    plt.close()
    # plt.show()
#######################################################################################################
#######################################################################################################
def plotSmithPASSFAIL_profile(data, data_name, outPath, info, freq, setpoint):
    plot = True

    # 3513 & 3540 Units
    units = {'Generator Reported Forward Power':'W', 'Generator Reported Reflected Power':'W', 'Generator Reported Delivered Power':'W',
             'Lynx Reported Forward Power':'W', 'Lynx Reported Reflected Power':'W', 'Lynx Reported Delivered Power':'W', 
             'Lagging Side DC Current':'A', 'Leading Side DC Current':'A', 'Lagging Side DC Voltage':'V', 'Leading Side DC Voltage':'V',
             'Lagging Side Linear Regulator Dissipation':'W', 'Leading Side Linear Regulator Dissipation':'W', 'Lagging Side LDMOS Dissipation':'W', 'Leading Side LDMOS Dissipation':'W',
             'Water In Temperature':'Â°C', 'Water Out Temperature':'Â°C', 'RF Coldplate Temperature':'Â°C'}

    # 10001 & 10002 MF Units
    # units = {'Generator Reported Forward Power':'W', 'Generator Reported Reflected Power':'W', 'Generator Reported Delivered Power':'W',
    #          'Lynx Reported Forward Power':'W', 'Lynx Reported Reflected Power':'W', 'Lynx Reported Delivered Power':'W',
    #          'Generator Reported DC Current':'A', 'Generator Reported DC Voltage': 'V', 'Generator Reported DC Power': 'W', 'PA Dissipation': 'W',
    #          'Generator Efficiency':'%'}

    #Takes Vertical Data Header to Last Row
    numlines = np.genfromtxt(data, delimiter=',', usecols=[0], dtype=str)
    #Gets headers from all columns
    headers = np.genfromtxt(data, delimiter=',', skip_footer=len(numlines)-1, dtype=str)
    #Pulls all data
    data_tot = np.genfromtxt(data, usecols=range(0, len(headers)), delimiter=',', dtype=str)
    R = []
    X = []
    gamma_real = []
    gamma_imag = []
    dta = []

    for i in range(len(data_tot[0])):
        if data_tot[0][i] == 'Gamma Real':
            gamma_real_index = i
        if data_tot[0][i] == 'Gamma Imag':
            gamma_imag_index = i
        if data_tot[0][i] == 'R':
            R_index = i
        if data_tot[0][i] == 'X':
            X_index = i
        if data_tot[0][i] == data_name:
            dta_index = i

    for i in range(1, len(data_tot)):
        gamma_real.append(float(data_tot[i][gamma_real_index]))
        gamma_imag.append(float(data_tot[i][gamma_imag_index]))
        R.append(float(data_tot[i][R_index]))
        X.append(float(data_tot[i][X_index]))
        dta.append(float(data_tot[i][dta_index]))

    dta = np.array(dta)
    error = []
    gamma_real_error = []
    gamma_imag_error = []
    vswr_error = []
    for i in range(0,len(dta)):
        vswr = (1+abs(complex(gamma_real[i],gamma_imag[i])))/((1-abs(complex(gamma_real[i],gamma_imag[i]))))
        if vswr <= 3:
            vswr_error.append(vswr)
            error.append(((dta[i] - int(setpoint))/int(setpoint))*100)
            gamma_real_error.append(gamma_real[i])
            gamma_imag_error.append(gamma_imag[i])

    try:
        max_index = np.where(dta == np.max(dta))
        max_index = int(max_index[0])
        Z_max = complex(R[max_index], X[max_index])
    except:
        max_index = np.where(dta == np.max(dta))
        max_index = int(max_index[0][0])
        # pass
    R_plot, X_plot = np.meshgrid(np.linspace(-1, 1, 500), np.linspace(-1, 1, 500))
    gamma_plot = []

    for i in range(len(gamma_real_error)):
        gamma_plot.append([gamma_real_error[i], gamma_imag_error[i]])

    data_2D = scipy.interpolate.griddata(gamma_plot, error, (R_plot, X_plot))

    if plot == True: 
        Z0 = 50
        vswr = [1.6, 2, 3]
        # unit circle outline
        theta = np.linspace(0, 2*np.pi, 100)
        x1 = np.cos(theta)
        y1 = np.sin(theta)

        R = [10, 25, 50, 100, 250]
        X = [-250, -100, -50, -25, -10, 0, 10, 25, 50, 100, 250]
        R1 = np.arange(0, np.max(R), 10)

        plt.figure(figsize=(11,11))
        plt.plot(x1,y1,'k-')
        plt.axis('off')

        for i in range(len(R)):
            g = (R[i] - Z0)/(R[i] + Z0)
            center = (1+g)/2
            r = 1 - center
            x = center + r*np.cos(theta)
            y = r*np.sin(theta)
            plt.plot(x,y, 'b--', alpha=0.1)
            plt.text(x=g+0.02, y=0, s=R[i], fontsize=10)

        for i in range(len(X)):
            Z = R1 + 1j*X[i]
            gamma = (Z - Z0)/(Z + Z0)
            x = np.real(gamma)
            y = np.imag(gamma)
            plt.plot(x, y, 'b--', alpha=0.1)
            if np.min(x) > 0 and np.min(y) < 0:
                plt.text(x=np.min(x)+0.125, y=np.min(y)-0.04, s=X[i], fontsize=10)
            elif np.min(x) < 0 and np.min(y) < 0:
                plt.text(x=np.min(x)-0.12, y=np.min(y)-0.04, s=X[i], fontsize=10)
            elif np.min(x) > 0 and np.max(y) > 0:
                plt.text(x=np.min(x)+0.125, y=np.max(y)+0.03, s=X[i], fontsize=10)
            elif np.min(x) < 0 and np.max(y) > 0:
                plt.text(x=np.min(x)-0.12, y=np.max(y)+0.03, s=X[i], fontsize=10)
            elif np.min(x) == 0:
                if np.min(y) < 0:
                    plt.text(x=-0.05, y=-1.06, s=-50, fontsize=10)
                # if np.max(y) > 0:
                #     plt.text(x=-0.03, y=1.03, s=50, fontsize=10)
            else:
                plt.text(x=-1.075, y=0, s=0, fontsize=10)

        for i in range(len(vswr)):
            gamma = (vswr[i] -  1)/(vswr[i] + 1) * np.exp(1j*theta)
            x = np.real(gamma)
            y = np.imag(gamma)
            plt.plot(x, y, 'r--', alpha=0.5)
            plt.text(x=-0.1, y=np.max(x)+0.02, s=f'$\\rho$ = {vswr[i]}')

        try:
            plt.title(f'Generator Reported Error {freq * 1e-3}MHz {setpoint}W Setpoint\nMaximum: {dta[max_index]:.2f}{units[data_name]} at {Z_max:.2f} ohms\nEverest {info}', fontsize=11)
        except:
            plt.title(f'Generator Reported Error {freq * 1e-3}MHz {setpoint}W Setpoint\nMaximum: {dta[max_index]:.2f}{units[data_name]}\nEverest {info}', fontsize=11)

        #plt.contourf(R_plot, X_plot, data_2D, levels=int(len(error)/4), cmap='jet')
        #plt.colorbar(label=units[data_name])
        for i in range(0,len(gamma_imag_error)):
            point_color = 'red'
            if (1.0 <= vswr_error[i] <= 1.1) and (error[i] <= 1.0):
                point_color = 'green'
            elif (1.1 <= vswr_error[i] <= 1.6) and (error[i] <= 1.5):
                point_color = 'green'
            elif (1.6 <= vswr_error[i] <= 3) and (error[i] <= 2):
                point_color = 'green'
            plt.plot(gamma_real_error[i],gamma_imag_error[i],'*',color = point_color)

        plt.savefig(outPath+f'\\Generator Reported Error_{freq}kHz_{setpoint}W.png')
        plt.close()
        # plt.show()
#######################################################################################################

#######################################################################################################
def plotCross_Section(data, outPath, info, freq, setpoint):

    P_max = setpoint
    # FILE PATH
    R = []
    X = []
    gamma_r = []
    gamma_x = []
    P_Del = []
    gamma_plot = []
    P_plot = []

    # READ FILE
    with open(data, newline='') as f:
        next(f)  # Skips first line
        for line in f:
            temp = line.strip('\r\n').split(',')
            R.append(temp[0])
            X.append(temp[1])
            gamma_r.append(temp[4])
            gamma_x.append(temp[5])
            P_Del.append(temp[8])

#FIX ME: P_MAX  of 10kW was a copy of 3500
    if P_max == 10000:
        dd = [-0.5, -0.33, -0.2308, -0.2, -0.05, 0, 0.05, 0.2, 0.2308, 0.33, 0.5]
        ee = np.multiply([0.5, 0.67, 1, 1, 1, 1, 1, 1, 1, 0.67, 0.5], P_max)
    elif P_max == 5000:
        dd = [-0.5, -0.33, -0.2, -0.090909, 0, 0.090909, 0.2, 0.33, 0.5]
        ee = np.multiply([0.4, 0.66, 0.9, 1, 1, 1, 0.9, 0.66, 0.4], P_max)
    elif P_max in (3500, 3000):
        dd = [-0.5, -0.33, -0.2308, -0.2, -0.05, 0, 0.05, 0.2, 0.2308, 0.33, 0.5]
        ee = np.multiply([0.5, 0.67, 1, 1, 1, 1, 1, 1, 1, 0.67, 0.5], P_max)
    elif P_max == 1500:
        dd = [-0.5, -0.33, -0.2307, 0, 0.2307, 0.33, 0.5]
        ee = np.multiply([0.533, 0.8, 1, 1, 1, 0.8, 0.533], P_max)
    elif P_max in (2500, 2000, 1000):
        dd = [-0.5, -0.33, -0.2308, -0.2, -0.05, 0, 0.05, 0.2, 0.2308, 0.33, 0.5]
        ee = np.multiply([0.5, 0.67, 1, 1, 1, 1, 1, 1, 1, 0.67, 0.5], P_max)
    else:
        raise ValueError(f"Unrecognized max power {P_max}")

    # Create gamma data to plot
    for (gam_r_i,gam_x_i,p_del_i) in zip(gamma_r,gamma_x,P_Del):
        g_pt = np.hypot(float(gam_r_i),float(gam_x_i))
        if float(gam_x_i) <= 0:
            g_pt = g_pt * -1
        gamma_plot.append(g_pt)
        P_plot.append(float(p_del_i))

    # Organize data to be min to max
    gamma_plot, P_plot = zip(*sorted(zip(gamma_plot, P_plot)))

    plt.figure(figsize=(14,8))
    plt.plot(dd, ee, color='k', label='Spec', linestyle='--', linewidth=1.5)
    plt.plot(gamma_plot, P_plot,'ro', label='Generator Reported Delivered Power')
    plt.plot(dd, ee, 'o', color='k')
    plt.xlabel('\u0393', fontsize=14)
    plt.ylabel('Delivered Power (Watts)', fontsize=14)
    plt.title(f'FastDAQ Power Profile Cross Section\n{freq*1e-3}MHz at {setpoint}W Setpoint\nEverest {info}')
    plt.legend()
    plt.grid()
    plt.xlim(-0.8, 0.8)
    plt.ylim(0, P_max+500)

    plt.savefig(outPath + f'\\Power_Profile_Cross_Section_{freq * 1e-3}MHz_{setpoint}W.png')
    plt.close()
#######################################################################################################

#######################################################################################################
def plotSmith_limits(data, outPath, info, freq, setpoint):

    # [Symbol, Color]
    limit_symbols = {'Reflected Power':['*', 'k'], 'Phantom Power':['+', 'g'],
        'Leading Side Linear Regulator Dissipation':['8', 'b'], 'Lagging Side Linear Regulator Dissipation':['8', 'r'], 
        'Leading Side LDMOS Dissipation':['^', 'b'], 'Lagging Side LDMOS Dissipation':['^', 'r'], 
        'Leading Side DCI':['d', 'b'], 'Lagging Side DCI':['d', 'r']}

    numlines = np.genfromtxt(data, delimiter=',', usecols=[0], dtype=str)
    headers = np.genfromtxt(data, delimiter=',', skip_footer=len(numlines)-1, dtype=str)
    data_tot = np.genfromtxt(data, usecols=range(0, len(headers)), delimiter=',', dtype=str)

    gamma_real = []
    gamma_imag = []
    limits = []

    gamma_real_index = np.nonzero(data_tot[0] == 'Gamma Real')[0][0]
    gamma_imag_index = np.nonzero(data_tot[0] == 'Gamma Imag')[0][0]
    limit_index = np.nonzero(data_tot[0] == 'Active Limits')[0][0]
    for data_tot_i in data_tot[1:]:
        gamma_real.append(float(data_tot_i[gamma_real_index]))
        gamma_imag.append(float(data_tot_i[gamma_imag_index]))
        limits.append(data_tot_i[limit_index])

    # limits = np.genfromtxt(data, delimiter=',', dtype=object, usecols=[21], skip_header=True) # Put this back

    numlimits = [0,0,0,0,0,0,0,0,0]

    # limits = [str(lims_i)[2:-1] for lims_i in limits]

    # limits = [lims_i.zfill(9) for lims_i in limits]

    Z0 = 50
    vswr = [1.2, 1.5, 2, 3, 5]
    # unit circle outline
    theta = np.linspace(0, 2*np.pi, 100)

    x1 = np.cos(theta)
    y1 = np.sin(theta)

    R = [10, 25, 50, 100, 250]
    X = [-250, -100, -50, -25, -10, 0, 10, 25, 50, 100, 250]
    R1 = np.arange(0, np.max(R), 10)

    plt.figure(figsize=(13,13))
    plt.plot(x1,y1,'k-')
    plt.axis('off')

    for Ri in R:
        g = (Ri - Z0)/(Ri + Z0)
        center = (1+g)/2
        r = 1 - center
        x = center + r*np.cos(theta)
        y = r*np.sin(theta)
        plt.plot(x,y, 'b--', alpha=0.1)
        plt.text(x=g+0.02, y=0, s=str(Ri), fontsize=10)

    for Xi in X:
        Z = R1 + 1j*Xi
        gamma = (Z - Z0)/(Z + Z0)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'b--', alpha=0.1)
        if np.min(x) > 0 > np.min(y):
            plt.text(x=np.min(x)+0.125, y=np.min(y)-0.04, s=str(Xi), fontsize=10)
        elif np.min(x) < 0 and np.min(y) < 0:
            plt.text(x=np.min(x)-0.12, y=np.min(y)-0.04, s=str(Xi), fontsize=10)
        elif np.min(x) > 0 and np.max(y) > 0:
            plt.text(x=np.min(x)+0.125, y=np.max(y)+0.03, s=str(Xi), fontsize=10)
        elif np.min(x) < 0 < np.max(y):
            plt.text(x=np.min(x)-0.12, y=np.max(y)+0.03, s=str(Xi), fontsize=10)
        elif np.min(x) == 0:
            if np.min(y) < 0:
                plt.text(x=-0.05, y=-1.06, s='-50', fontsize=10)
            # if np.max(y) > 0:
            #     plt.text(x=-0.03, y=1.03, s='50', fontsize=10)
        else:
            plt.text(x=-1.075, y=0, s='0', fontsize=10)

    for vswr_i in vswr:
        gamma = (vswr_i -  1)/(vswr_i + 1) * np.exp(1j*theta)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'r--', alpha=0.2)
        plt.text(x=-0.1, y=np.max(x).item()+0.02, s=f'$\\rho$ = {vswr_i}')

    plt.title(f'Active Limits {freq*1e-3}MHz {setpoint}W Setpoint\nEverest {info}', fontsize=11)

    # DCI2, DCI1, LDMOS2, LDMOS1, Lin2, Lin1, Unused, Phantom, Reflected
    for (lims_i,gam_re_i,gam_im_i) in zip(limits,gamma_real,gamma_imag):
        if lims_i == '000000000':
            if numlimits[0] == 0:
                plt.plot(gam_re_i, gam_im_i, 'yo',  markersize=15, label = 'No Active Limits')
                numlimits[0]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, 'yo', markersize=15)
        if lims_i[0]=='1':
            if numlimits[1] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side DCI'][0], markeredgecolor=limit_symbols['Lagging Side DCI'][1], markersize=15, fillstyle='none', label='Lagging Side DCI')
                numlimits[1]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side DCI'][0], markeredgecolor=limit_symbols['Lagging Side DCI'][1], markersize=15, fillstyle='none')
        if lims_i[1]=='1':
            if numlimits[2] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side DCI'][0], markeredgecolor=limit_symbols['Leading Side DCI'][1], markersize=15, fillstyle='none', label='Leading Side DCI')
                numlimits[2]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side DCI'][0], markeredgecolor=limit_symbols['Leading Side DCI'][1], markersize=15, fillstyle='none')
        if lims_i[2]=='1':
            if numlimits[3] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side LDMOS Dissipation'][0], markeredgecolor=limit_symbols['Lagging Side LDMOS Dissipation'][1], markersize=15, fillstyle='none', label='Lagging Side LDMOS Dissipation')
                numlimits[3]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side LDMOS Dissipation'][0], markeredgecolor=limit_symbols['Lagging Side LDMOS Dissipation'][1], markersize=15, fillstyle='none')
        if lims_i[3]=='1':
            if numlimits[4] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side LDMOS Dissipation'][0], markeredgecolor=limit_symbols['Leading Side LDMOS Dissipation'][1], markersize=15, fillstyle='none', label='Leading Side LDMOS Dissipation')
                numlimits[4]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side LDMOS Dissipation'][0], markeredgecolor=limit_symbols['Leading Side LDMOS Dissipation'][1], markersize=15, fillstyle='none')
        if lims_i[4]=='1':
            if numlimits[5] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side Linear Regulator Dissipation'][0], markeredgecolor=limit_symbols['Lagging Side Linear Regulator Dissipation'][1], fillstyle='none', markersize=15, label='Lagging Side Linear Regulator Dissipation')
                numlimits[5]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side Linear Regulator Dissipation'][0], markeredgecolor=limit_symbols['Lagging Side Linear Regulator Dissipation'][1], markersize=15, fillstyle='none')
        if lims_i[5]=='1':
            if numlimits[6] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side Linear Regulator Dissipation'][0], markeredgecolor=limit_symbols['Leading Side Linear Regulator Dissipation'][1], markersize=15, fillstyle='none', label='Leading Side Linear Regulator Dissipation')
                numlimits[6]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side Linear Regulator Dissipation'][0], markeredgecolor=limit_symbols['Leading Side Linear Regulator Dissipation'][1], markersize=15, fillstyle='none')
        if lims_i[7]=='1':
            if numlimits[7] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Phantom Power'][0], markeredgecolor=limit_symbols['Phantom Power'][1], markersize=15, fillstyle='none', label='Phantom Power')
                numlimits[7]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Phantom Power'][0], markeredgecolor=limit_symbols['Phantom Power'][1], markersize=15, fillstyle='none')
        if lims_i[8]=='1':
            if numlimits[8] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Reflected Power'][0], markeredgecolor=limit_symbols['Reflected Power'][1], markersize=15, fillstyle='none', label='Reflected Power')
                numlimits[8]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Reflected Power'][0], markeredgecolor=limit_symbols['Reflected Power'][1], markersize=15, fillstyle='none')

    plt.legend()
    plt.savefig(outPath+f'\\activeLimits_{freq}KHz_{setpoint}W.png')
    plt.close()
    # plt.show()
#######################################################################################################

def plotSmith_limits_10kW(data, outPath, info, freq, setpoint):

    # [Symbol, Color]
    limit_symbols = {'Reflected Power':['*', 'k'], 'Phantom Power':['+', 'g'], 'Dissipation':['8', 'b'],
        'Leading Side Linear Regulator Dissipation':['8', 'b'], 'Lagging Side Linear Regulator Dissipation':['8', 'r'], 
        'Leading Side LDMOS Dissipation':['^', 'b'], 'Lagging Side LDMOS Dissipation':['^', 'r'], 
        'Leading Side DCI':['d', 'b'], 'Lagging Side DCI':['d', 'r']}

    gamma_real = np.genfromtxt(data, delimiter=',', dtype=float, usecols=[4], skip_header=True)
    gamma_imag = np.genfromtxt(data, delimiter=',', dtype=float, usecols=[5], skip_header=True)
    limits = np.genfromtxt(data, delimiter=',', dtype=object, usecols=[19], skip_header=True)

    numlimits = [0,0,0,0,0,0,0,0,0,0]

    limits = [str(lims_i)[2:-1].zfill(len(numlimits)) for lims_i in limits]

    Z0 = 50
    vswr = [2, 3, 5]
    # unit circle outline
    theta = np.linspace(0, 2*np.pi, 100)

    x1 = np.cos(theta)
    y1 = np.sin(theta)

    R = [10, 25, 50, 100, 250]
    X = [-250, -100, -50, -25, -10, 0, 10, 25, 50, 100, 250]
    R1 = np.arange(0, np.max(R), 10)

    plt.figure(figsize=(13,13))
    plt.plot(x1,y1,'k-')
    plt.axis('off')

    for Ri in R:
        g = (Ri - Z0)/(Ri + Z0)
        center = (1+g)/2
        r = 1 - center
        x = center + r*np.cos(theta)
        y = r*np.sin(theta)
        plt.plot(x,y, 'b--', alpha=0.1)
        plt.text(x=g+0.02, y=0, s=str(Ri), fontsize=10)

    for Xi in X:
        Z = R1 + 1j*Xi
        gamma = (Z - Z0)/(Z + Z0)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'b--', alpha=0.1)
        if np.min(x) > 0 > np.min(y):
            plt.text(x=np.min(x)+0.125, y=np.min(y)-0.04, s=str(Xi), fontsize=10)
        elif np.min(x) < 0 and np.min(y) < 0:
            plt.text(x=np.min(x)-0.12, y=np.min(y)-0.04, s=str(Xi), fontsize=10)
        elif np.min(x) > 0 and np.max(y) > 0:
            plt.text(x=np.min(x)+0.125, y=np.max(y)+0.03, s=str(Xi), fontsize=10)
        elif np.min(x) < 0 < np.max(y):
            plt.text(x=np.min(x)-0.12, y=np.max(y)+0.03, s=str(Xi), fontsize=10)
        elif np.min(x) == 0:
            if np.min(y) < 0:
                plt.text(x=-0.05, y=-1.06, s='-50', fontsize=10)
            # if np.max(y) > 0:
            #     plt.text(x=-0.03, y=1.03, s='50', fontsize=10)
        else:
            plt.text(x=-1.075, y=0, s='0', fontsize=10)

    for vswr_i in vswr:
        gamma = (vswr_i -  1)/(vswr_i + 1) * np.exp(1j*theta)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'r--', alpha=0.2)
        plt.text(x=-0.1, y=np.max(x).item()+0.02, s=f'$\\rho$ = {vswr_i}')

    plt.title(f'Active Limits {freq*1e-3}MHz {setpoint}W Setpoint\nEverest {info}', fontsize=11)

    # DCI2, DCI1, LDMOS2, LDMOS1, Lin2, Lin1, Dissipation, Phantom, Reflected
    for (lims_i,gam_re_i,gam_im_i) in zip(limits,gamma_real,gamma_imag):
        if lims_i == '0000000000':
            if numlimits[0] == 0:
                plt.plot(gam_re_i, gam_im_i, 'yo',  markersize=10, label = 'No Active Limits')
                numlimits[0]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, 'yo', markersize=10)
        if lims_i[1]=='1':
            if numlimits[1] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side DCI'][0], markeredgecolor=limit_symbols['Lagging Side DCI'][1], markersize=10, fillstyle='none', label='Lagging Side DCI')
                numlimits[1]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side DCI'][0], markeredgecolor=limit_symbols['Lagging Side DCI'][1], markersize=10, fillstyle='none')
        if lims_i[2]=='1':
            if numlimits[2] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side DCI'][0], markeredgecolor=limit_symbols['Leading Side DCI'][1], markersize=10, fillstyle='none', label='Leading Side DCI')
                numlimits[2]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side DCI'][0], markeredgecolor=limit_symbols['Leading Side DCI'][1], markersize=10, fillstyle='none')
        if lims_i[3]=='1':
            if numlimits[3] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side LDMOS Dissipation'][0], markeredgecolor=limit_symbols['Lagging Side LDMOS Dissipation'][1], markersize=10, fillstyle='none', label='Lagging Side LDMOS Dissipation')
                numlimits[3]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side LDMOS Dissipation'][0], markeredgecolor=limit_symbols['Lagging Side LDMOS Dissipation'][1], markersize=10, fillstyle='none')
        if lims_i[4]=='1':
            if numlimits[4] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side LDMOS Dissipation'][0], markeredgecolor=limit_symbols['Leading Side LDMOS Dissipation'][1], markersize=10, fillstyle='none', label='Leading Side LDMOS Dissipation')
                numlimits[4]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side LDMOS Dissipation'][0], markeredgecolor=limit_symbols['Leading Side LDMOS Dissipation'][1], markersize=10, fillstyle='none')
        if lims_i[5]=='1':
            if numlimits[5] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side Linear Regulator Dissipation'][0], markeredgecolor=limit_symbols['Lagging Side Linear Regulator Dissipation'][1], fillstyle='none', markersize=10, label='Lagging Side Linear Regulator Dissipation')
                numlimits[5]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Lagging Side Linear Regulator Dissipation'][0], markeredgecolor=limit_symbols['Lagging Side Linear Regulator Dissipation'][1], markersize=10, fillstyle='none')
        if lims_i[6]=='1':
            if numlimits[6] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side Linear Regulator Dissipation'][0], markeredgecolor=limit_symbols['Leading Side Linear Regulator Dissipation'][1], markersize=10, fillstyle='none', label='Leading Side Linear Regulator Dissipation')
                numlimits[6]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Leading Side Linear Regulator Dissipation'][0], markeredgecolor=limit_symbols['Leading Side Linear Regulator Dissipation'][1], markersize=10, fillstyle='none')
        if lims_i[7]=='1':
            if numlimits[7] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Dissipation'][0], markeredgecolor=limit_symbols['Dissipation'][1], markersize=10, fillstyle='none', label='Dissipation')
                numlimits[7]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Dissipation'][0], markeredgecolor=limit_symbols['Dissipation'][1], markersize=10, fillstyle='none')
        if lims_i[8]=='1':
            if numlimits[8] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Phantom Power'][0], markeredgecolor=limit_symbols['Phantom Power'][1], markersize=10, fillstyle='none', label='Phantom Power')
                numlimits[8]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Phantom Power'][0], markeredgecolor=limit_symbols['Phantom Power'][1], markersize=10, fillstyle='none')
        if lims_i[9]=='1':
            if numlimits[9] == 0:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Reflected Power'][0], markeredgecolor=limit_symbols['Reflected Power'][1], markersize=10, fillstyle='none', label='Reflected Power')
                numlimits[9]+=1
            else:
                plt.plot(gam_re_i, gam_im_i, marker=limit_symbols['Reflected Power'][0], markeredgecolor=limit_symbols['Reflected Power'][1], markersize=10, fillstyle='none')

    plt.legend()
    plt.savefig(outPath+f'\\activeLimits_{freq}KHz_{setpoint}W.png')
    plt.close()
    # plt.show()
#######################################################################################################


#######################################################################################################
def fastDAQsetup(generator, triggermode, triggerPos, sampleRate, debugParam=None, memoryMode:int|str = 0):
    """ Sets up FastDAQ :) Set debugParam to None to disable debug param selection """
    generator.setFastDataAcquisitionControl(4) # Stop acquisition
    if debugParam is not None:
        generator.setFastDataAcquisitionDebug(0, 1) # Enable Debug
        generator.setFastDataAcquisitionDebug(1, debugParam) # Set gamma real to Internal Setpoint

    if isinstance(memoryMode,int):
        if memoryMode in (0,1):
            generator.setFastDataAcquisitionConfiguration(1, memoryMode, 0)
        else:
            raise ValueError(f'{memoryMode} is not a valid memory mode selection')
    elif memoryMode[0] == 'F' or memoryMode[0] == 'f':
        generator.setFastDataAcquisitionConfiguration(1, 0, 0) # FPGA RAM memory mode
    elif memoryMode[0] == 'E' or memoryMode[0] == 'e':
        generator.setFastDataAcquisitionConfiguration(1, 1, 0) # External memory mode
    else:
        raise ValueError(f'{memoryMode} is not a valid memory mode selection')

    generator.setFastDataAcquisitionConfiguration(2, triggermode, 0) # Trigger source
    generator.setFastDataAcquisitionConfiguration(3, triggerPos, 0) # Trigger position
    generator.setFastDAQsamplerate(sampleRate) # Set sample rate
#######################################################################################################

#######################################################################################################
def sampleRateConvert_FastDAQ(generator, ns_sampleInterval):
    gen_sampleRate = 1/(ns_sampleInterval*1e9)
    generator.setFastDAQsamplerate(gen_sampleRate) # Set sample rate
    return gen_sampleRate
#######################################################################################################

#######################################################################################################
def plotFastDAQ(data, outPath, info, freq, setpoint):
    save_path = outPath + r'\FastDAQ Plots'
    try:
        os.makedirs(save_path)
    except OSError:
        pass
    
    numlines = np.genfromtxt(data, delimiter=',', usecols=[0], dtype=str)
    headers = np.genfromtxt(data, delimiter=',', skip_footer=len(numlines)-1, dtype=str)
    
    Z = np.genfromtxt(data, delimiter=',', dtype=complex, usecols=[0], skip_header=True)
    # limit = np.genfromtxt(data, delimiter=',', dtype=str, usecols=[0], skip_header=True)
    tmp_fwd = np.genfromtxt(data, delimiter=']', dtype=str, usecols=[0], skip_header=True)
    tmp_rfl = np.genfromtxt(data, delimiter=']', dtype=str, usecols=[1], skip_header=True)
    tmp_dac = np.genfromtxt(data, delimiter=']', dtype=str, usecols=[2], skip_header=True)

    if len(tmp_dac) != 0:
        fwd = []
        rfl = []
        rfdac = []

        for (fwd_i,rfl_i,dac_i) in zip(tmp_fwd,tmp_rfl,tmp_dac):
            for j in range(len(fwd_i)-1):
                if fwd_i[j]=='[':
                    fwd_i = fwd_i[j+1:-1]
                    break
            for k in range(len(rfl_i)-1):
                if rfl_i[k]=='[':
                    rfl_i = rfl_i[k+1:-1]
                    break
            for l in range(len(dac_i)-1):
                if dac_i[l]=='[':
                    dac_i = dac_i[l+1:-1]
                    break
            fwd.append(fwd_i.split(','))
            rfl.append(rfl_i.split(','))
            rfdac.append(dac_i.split(','))

        for (i,(fwd_i,rfl_i,dac_i)) in enumerate(zip(fwd,rfl,rfdac)):
            for (j,(fwd_i_j,rfl_i_j,dac_i_j)) in enumerate(zip(fwd_i,rfl_i,dac_i)):
                fwd[i][j] = float(fwd_i_j)
                rfl[i][j] = float(rfl_i_j)
                rfdac[i][j] = float(dac_i_j)
                rfdac[i][j] = fixDAC(rfdac[i][j])

        t = np.linspace(0, 1/2083333 * 1e6 * len(fwd[0]), len(fwd[0]))

        for (i,(fwd_i,rfl_i,dac_i,z_i)) in enumerate(zip(fwd,rfl,rfdac,Z)):
                    
            gamma_temp = ((z_i + 50)/(z_i - 50))
            vswr = round(abs((1+abs(gamma_temp))/(1-abs(gamma_temp))),2)
            
            fig, ax1 = plt.subplots(figsize=(14,10))
            ax2 = ax1.twinx()
            ax1.plot(t, fwd_i, 'b-', label = '$P_{FWD}$')
            ax1.plot(t, rfl_i, 'r-', label = '$P_{RFL}$')
            ax2.plot(t, dac_i, 'g-', label='$P_{DAC}$')
            ax2.set_ylim(0,5)
            ax1.hlines(600, xmin=0, xmax=2000, colors='k', linestyles='--', label='$P_{RFL}$ Limit')

            ax1.set_ylabel('Power (W)', fontsize=16)
            ax1.set_xlabel('Time ($\mu$s)', fontsize=16)
            ax1.set_ylim(ymin=0, ymax=max(fwd_i)+100)
            ax1.set_title(f'{freq/1000}MHz RF-On at {Z[i]:.2f} ohms - VSWR:{vswr}\nEverest {info}', fontsize=16)
            ax2.set_ylabel('DAC (V)', fontsize=16)
            ax1.legend(loc='lower left', fontsize=16)
            ax2.legend(loc='lower right', fontsize=16)
            plt.grid()

            plt.savefig(save_path + f'\\fastDAQ_{i+1}of{len(fwd)}.png')
            plt.close()
            print(f'Saved FastDAQ capture {i+1} of {len(fwd)}', end='\r')
    else:
        fwd = []
        rfl = []

        for (fwd_i,rfl_i) in zip(tmp_fwd,tmp_rfl):
            for j in range(len(fwd_i)-1):
                if fwd_i[j]=='[':
                    fwd_i = fwd_i[j+1:-1]
                    break
            for k in range(len(rfl_i)-1):
                if rfl_i[k]=='[':
                    rfl_i = rfl_i[k+1:-1]
                    break
            fwd.append(fwd_i.split(','))
            rfl.append(rfl_i.split(','))

        for (i,(fwd_i,rfl_i)) in enumerate(zip(fwd,rfl)):
            for (j,(fwd_i_j,rfl_i_j)) in enumerate(zip(fwd_i,rfl_i)):
                fwd[i][j] = float(fwd_i_j)
                rfl[i][j] = float(rfl_i_j)

        t = np.linspace(0, 1/2083333 * 1e6 * len(fwd[0]), len(fwd[0]))

        for (i,(fwd_i,rfl_i,z_i)) in enumerate(zip(fwd,rfl,Z)):
                    
            gamma_temp = ((z_i + 50)/(z_i - 50))
            vswr = round(abs((1+abs(gamma_temp))/(1-abs(gamma_temp))),2)
            plt.figure(figsize=(14,10))

            plt.plot(t, fwd_i, 'b-', label = '$P_{FWD}$')
            plt.plot(t, rfl_i, 'r-', label = '$P_{RFL}$')
            plt.hlines(750, xmin=0, xmax=2000, colors='k', linestyles='--', label='$P_{RFL}$ Limit')
            plt.ylim(ymin=0, ymax=max(fwd_i)+250)

            plt.ylabel('Power (W)', fontsize=16)
            plt.xlabel(r'Time ($\mu$s)', fontsize=16)
            plt.title(f'{freq/1000}MHz {setpoint}W Setpoint\nPower at {z_i:.2f} ohms\nVSWR:{vswr}', fontsize=22)
            plt.grid()

            plt.savefig(save_path + f'\\fastDAQ_{i+1}of{len(fwd)}.png')
            plt.close()
            print(f'Saved FastDAQ capture {i+1} of {len(fwd)}', end='\r')
#######################################################################################################

#######################################################################################################
def plotDPDZFastDAQ(data, outPath, info, freq, setpoint,iteration):
    save_path = outPath + r'\DPDZ FastDAQ Plots'
    try:
        os.makedirs(save_path)
    except OSError:
        pass
    
    numlines = np.genfromtxt(data, delimiter=',', usecols=[0], dtype=str)
    headers = np.genfromtxt(data, delimiter=',', skip_footer=len(numlines)-1, dtype=str)
    
    tmp_ite = np.genfromtxt(data, delimiter=',', dtype=str, usecols=[0],skip_header=True)
    Z = np.genfromtxt(data, delimiter=',', dtype=complex, usecols=[1], skip_header=True)
    # limit = np.genfromtxt(data, delimiter=',', dtype=str, usecols=[0], skip_header=True)
    tmp_fwd = np.genfromtxt(data, delimiter=']', dtype=str, usecols=[0], skip_header=True)
    tmp_rfl = np.genfromtxt(data, delimiter=']', dtype=str, usecols=[1], skip_header=True)
    tmp_dac = np.genfromtxt(data, delimiter=']', dtype=str, usecols=[2], skip_header=True)

    if len(tmp_dac) != 0:
        ite = []
        fwd = []
        rfl = []
        rfdac = []

        for (ite_i,fwd_i,rfl_i,dac_i) in zip(tmp_ite,tmp_fwd,tmp_rfl,tmp_dac):
            for i in range(len(ite_i)-1):
                if fwd_i[j]=='[':
                    ite_i = ite_i[j+1:-1]
                    break
            for j in range(len(fwd_i)-1):
                if fwd_i[j]=='[':
                    fwd_i = fwd_i[j+1:-1]
                    break
            for k in range(len(rfl_i)-1):
                if rfl_i[k]=='[':
                    rfl_i = rfl_i[k+1:-1]
                    break
            for l in range(len(dac_i)-1):
                if dac_i[l]=='[':
                    dac_i = dac_i[l+1:-1]
                    break
                '0'
            ite.append(ite_i.split(','))
            fwd.append(fwd_i.split(','))
            rfl.append(rfl_i.split(','))
            rfdac.append(dac_i.split(','))

        for (i,(ite_i,fwd_i,rfl_i,dac_i)) in enumerate(zip(ite,fwd,rfl,rfdac)):
            ite[i] = [int(x) for x in ite_i]
            for (j,(fwd_i_j,rfl_i_j,dac_i_j)) in enumerate(zip(fwd_i,rfl_i,dac_i)):
                
                fwd[i][j] = float(fwd_i_j)
                rfl[i][j] = float(rfl_i_j)
                rfdac[i][j] = float(dac_i_j)
                rfdac[i][j] = fixDAC(rfdac[i][j])

        t = np.linspace(0, 1/2083333 * 1e6 * len(fwd[0]), len(fwd[0]))

        temp_group =[]
        for (i,(ite_i,fwd_i,rfl_i,dac_i,z_i)) in enumerate(zip(ite, fwd,rfl,rfdac,Z)):
            temp_group.append([z_i,fwd_i,rfl_i,dac_i])
            gamma_temp = ((z_i + 50)/(z_i - 50))
            vswr = round(abs((1+abs(gamma_temp))/(1-abs(gamma_temp))),2)
            if len(temp_group) == 3:
                fig, ax1 = plt.subplots(figsize=(14,10))
                ax2 = ax1.twinx()
                ax1.plot(t,temp_group[0][1],'b-', label = '$P_{FWD}$')
                ax1.plot(t,temp_group[1][1],'b-')
                ax1.plot(t,temp_group[2][1],'b-')

                ax1.plot(t,temp_group[0][2],'r-', label = '$P_{RFL}$')
                ax1.plot(t,temp_group[1][2],'r-')
                ax1.plot(t,temp_group[2][2],'r-')

                ax2.plot(t,temp_group[0][3],'g-', label = '$P_{DAC}$')
                ax2.plot(t,temp_group[1][3],'g-')
                ax2.plot(t,temp_group[2][3],'g-')

                ax2.set_ylim(0,5)
                # ax1.hlines(600, xmin=0, xmax=2000, colors='k', linestyles='--', label='$P_{RFL}$ Limit')

                ax1.set_ylabel('Power (W)', fontsize=16)
                ax1.set_xlabel('Time ($\mu$s)', fontsize=16)
                ax1.set_ylim(ymin=0, ymax=max(fwd_i)+10)
                ax1.set_title(f'{freq/1000}MHz RF-On at {Z[i]:.2f} ohms - VSWR:{vswr}\nEverest {info}', fontsize=16)
                ax2.set_ylabel('DAC (V)', fontsize=16)
                ax1.legend(loc='lower left', fontsize=16)
                ax2.legend(loc='lower right', fontsize=16)
                plt.grid()

                plt.savefig(save_path + f'\\fastDAQ_{int((i+1)//iteration)}of{int(len(fwd)//iteration)}.png')
                plt.close()
                print(f'Saved FastDAQ capture {int((i+1)/3)} of {int(len(fwd)/3)}', end='\r')
                temp_group = []

    else:
        fwd = []
        rfl = []

        for (fwd_i,rfl_i) in zip(tmp_ite,tmp_fwd,tmp_rfl):
            for j in range(len(fwd_i)-1):
                if fwd_i[j]=='[':
                    fwd_i = fwd_i[j+1:-1]
                    break
            for k in range(len(rfl_i)-1):
                if rfl_i[k]=='[':
                    rfl_i = rfl_i[k+1:-1]
                    break
            fwd.append(fwd_i.split(','))
            rfl.append(rfl_i.split(','))

        for (i,(fwd_i,rfl_i)) in enumerate(zip(ite,fwd,rfl)):
            for (j,(fwd_i_j,rfl_i_j)) in enumerate(zip(fwd_i,rfl_i)):
                fwd[i][j] = float(fwd_i_j)
                rfl[i][j] = float(rfl_i_j)

        t = np.linspace(0, 1/2083333 * 1e6 * len(fwd[0]), len(fwd[0]))

        for (i,(fwd_i,rfl_i,z_i)) in enumerate(zip(ite,fwd,rfl,Z)):
                    
            gamma_temp = ((z_i + 50)/(z_i - 50))
            vswr = round(abs((1+abs(gamma_temp))/(1-abs(gamma_temp))),2)
            plt.figure(figsize=(14,10))

            plt.plot(t, fwd_i, 'b-', label = '$P_{FWD}$')
            plt.plot(t, rfl_i, 'r-', label = '$P_{RFL}$')
            plt.hlines(600, xmin=0, xmax=2000, colors='k', linestyles='--', label='$P_{RFL}$ Limit')
            plt.ylim(ymin=0, ymax=max(fwd_i)+10)

            plt.ylabel('Power (W)', fontsize=16)
            plt.xlabel(r'Time ($\mu$s)', fontsize=16)
            plt.title(f'{freq/1000}MHz {setpoint}W Setpoint\nPower at {z_i:.2f} ohms\nVSWR:{vswr}', fontsize=22)
            plt.grid()

            plt.savefig(save_path + f'\\fastDAQ_{i+1}of{len(fwd)}.png')
            plt.close()
            print(f'Saved FastDAQ capture {i+1} of {len(fwd)}', end='\r')
#######################################################################################################

#######################################################################################################
def plotDPDZ(outpath, filename, info):
    file = outpath+r'\\'+filename
    R_plot, X_plot = np.meshgrid(np.linspace(-1, 1, 500), np.linspace(-1, 1, 500))
    freq = []
    sp = []
    headers = []
    data = np.genfromtxt(file, delimiter=',', skip_header=True)

    headers = np.genfromtxt(file, delimiter=",", dtype=None, encoding=None, names=True)
    headers = headers.dtype.names
    freq = filename.split('_')
    freq = freq[1].split('k')
    freq = int(freq[0])

    sp = filename.split('_')
    sp = sp[2].split('W')
    sp = int(sp[0])

    gamma_plot = []
    gen_pdlv_plot = []
    lynx_pdlv_plot = []
    gen_pfwd_plot = []
    gen_prfl_plot = []
    dpdz_data = []
    index = 3
    for i in range(len(data)):
        gamma_plot.append([data[i][1], data[i][2]])
        gen_pdlv_plot.append(data[i][3])
        lynx_pdlv_plot.append(data[i][4])
        gen_pfwd_plot.append(data[i][5])
        gen_prfl_plot.append(data[i][6])
        dpdz_data = [gen_pdlv_plot,lynx_pdlv_plot,gen_pfwd_plot,gen_prfl_plot]
    for dpdz_i in dpdz_data:
         
        data_2D = scipy.interpolate.griddata(gamma_plot, dpdz_i , (R_plot, X_plot))

        Z0 = 50
        vswr = [2, 3, 5]
        # unit circle outline
        theta = np.linspace(0, 2*np.pi, 100)
        x1 = np.cos(theta)
        y1 = np.sin(theta)

        R = [10, 25, 50, 100, 250]
        X = [-250, -100, -50, -25, -10, 0, 10, 25, 50, 100, 250]
        R1 = np.arange(0, np.max(R), 10)


        plt.figure(figsize=(13,11))
        plt.plot(x1,y1,'k-')
        plt.axis('off')

        
        for i in range(len(R)):
            g = (R[i] - Z0)/(R[i] + Z0)
            center = (1+g)/2
            r = 1 - center
            x = center + r*np.cos(theta)
            y = r*np.sin(theta)
            plt.plot(x,y, 'b--', alpha=0.1)
            plt.text(x=g+0.02, y=0, s=R[i], fontsize=10)

        
        for i in range(len(X)):
            Z = R1 + 1j*X[i]
            gamma = (Z - Z0)/(Z + Z0)
            x = np.real(gamma)
            y = np.imag(gamma)
            plt.plot(x, y, 'b--', alpha=0.1)
            if np.min(x) > 0 and np.min(y) < 0:
                plt.text(x=np.min(x)+0.125, y=np.min(y)-0.04, s=X[i], fontsize=10)
            elif np.min(x) < 0 and np.min(y) < 0:
                plt.text(x=np.min(x)-0.12, y=np.min(y)-0.04, s=X[i], fontsize=10)        
            elif np.min(x) > 0 and np.max(y) > 0:
                plt.text(x=np.min(x)+0.125, y=np.max(y)+0.03, s=X[i], fontsize=10)
            elif np.min(x) < 0 and np.max(y) > 0:
                plt.text(x=np.min(x)-0.12, y=np.max(y)+0.03, s=X[i], fontsize=10)    
            elif np.min(x) == 0:
                if np.min(y) < 0:
                    plt.text(x=-0.05, y=-1.06, s=-50, fontsize=10)
                # if np.max(y) > 0:
                #     plt.text(x=-0.03, y=1.03, s=50, fontsize=10)
            else:
                plt.text(x=-1.075, y=0, s=0, fontsize=10)

        for i in range(len(vswr)):
            gamma = (vswr[i] -  1)/(vswr[i] + 1) * np.exp(1j*theta)
            x = np.real(gamma)
            y = np.imag(gamma)
            plt.plot(x, y, 'r--', alpha=0.2)
            plt.text(x=-0.1, y=np.max(x)+0.02, s='$\\rho$ = {}'.format(vswr[i]))

        
        plt.title('{} DPDZ  {}kHz {}W Setpoint\nEverest {}'.format(headers[index],freq, sp, info), fontsize=11)
        plt.contourf(R_plot, X_plot, data_2D, levels=int(len(gen_pdlv_plot)/4), cmap='jet')
        plt.colorbar(label='Watts')

        plt.savefig(outpath+r'\\' + '{}_DPDZ_{}KHz_{}W.png'.format(headers[index],freq, sp))
        plt.close()
        # plt.show()
        index = index + 1
#######################################################################################################

#######################################################################################################
def plotSmith_compare_profiles(data, data_name, outPath, info, freq, setpoint):
    units = {'Generator Reported Forward Power': 'W', 'Generator Reported Reflected Power': 'W',
        'Generator Reported Delivered Power': 'W',
        'Lynx Reported Forward Power': 'W', 'Lynx Reported Reflected Power': 'W',
        'Lynx Reported Delivered Power': 'W',
        'Lagging Side DC Current': 'A', 'Leading Side DC Current': 'A', 'Lagging Side DC Voltage': 'V',
        'Leading Side DC Voltage': 'V',
        'Lagging Side Linear Regulator Dissipation': 'W', 'Leading Side Linear Regulator Dissipation': 'W',
        'Lagging Side LDMOS Dissipation': 'W', 'Leading Side LDMOS Dissipation': 'W',
        'Water In Temperature': '°C', 'Water Out Temperature': '°C', 'RF Coldplate Temperature': '°C',
        'Ptotal Yokogawa': 'W',
        'Phase 1 Vrms': 'V', 'Phase 1 Irms': 'A', 'Phase 2 Vrms': 'V', 'Phase 2 Irms': 'A', 'Efficiency': '%',
        'Leading Side Driver Power': 'W', 'Lagging Side Driver Power': 'W',
        'Driver Power Difference (Leading - Lagging)': 'W',
        'Leading LDMOS Temperature (L)': '°C', 'Leading LDMOS Temperature (R)': '°C',
        'Lagging LDMOS Temperature (L)': '°C', 'Lagging LDMOS Temperature (R)': '°C',
        'Leading LDMOS Temperature Difference (R-L)': '°C', 'Lagging LDMOS Temperature Difference (R-L)': '°C'}

    data_tot = np.genfromtxt(data, delimiter=',', dtype=str)
    R = []
    X = []
    gamma_real = []
    gamma_imag = []
    dta = []

    gamma_real_index = np.nonzero(data_tot[0] == 'Gamma Real')[0][0]
    gamma_imag_index = np.nonzero(data_tot[0] == 'Gamma Imaginary')[0][0]
    R_index = np.nonzero(data_tot[0] == 'R')[0][0]
    X_index = np.nonzero(data_tot[0] == 'X')[0][0]
    dta_index = np.nonzero(data_tot[0] == data_name)[0][0]

    for data_tot_i in data_tot[1:]:
        gamma_real.append(float(data_tot_i[gamma_real_index]))
        gamma_imag.append(float(data_tot_i[gamma_imag_index]))
        R.append(float(data_tot_i[R_index]))
        X.append(float(data_tot_i[X_index]))
        dta.append(float(data_tot_i[dta_index]))

    dta = np.array(dta)
    try:
        max_index = np.nonzero(dta == np.max(dta))
        max_index = int(max_index[0])
        Z_max = complex(R[max_index], X[max_index])
    except:
        max_index = np.nonzero(dta == np.max(dta))
        max_index = int(max_index[0][0])
        # pass
    R_plot, X_plot = np.meshgrid(np.linspace(-1, 1, 500), np.linspace(-1, 1, 500))
    gamma_plot = []

    for (gr_i,gi_i) in zip(gamma_real,gamma_imag):
        gamma_plot.append([gr_i, gi_i])

    data_2D = scipy.interpolate.griddata(gamma_plot, dta, (R_plot, X_plot))

    Z0 = 50
    vswr = [2, 3, 5]
    # unit circle outline
    theta = np.linspace(0, 2 * np.pi, 100)
    x1 = np.cos(theta)
    y1 = np.sin(theta)

    R = [10, 25, 50, 100, 250]
    X = [-250, -100, -50, -25, -10, 0, 10, 25, 50, 100, 250]
    R1 = np.arange(0, np.max(R), 10)

    plt.figure(figsize=(13, 11))
    plt.plot(x1, y1, 'k-')
    plt.axis('off')

    for Ri in R:
        g = (Ri - Z0) / (Ri + Z0)
        center = (1 + g) / 2
        r = 1 - center
        x = center + r * np.cos(theta)
        y = r * np.sin(theta)
        plt.plot(x, y, 'b--', alpha=0.1)
        plt.text(x=g + 0.02, y=0, s=str(Ri), fontsize=10)

    for Xi in X:
        Z = R1 + 1j * Xi
        gamma = (Z - Z0) / (Z + Z0)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'b--', alpha=0.1)
        if np.min(x) > 0 > np.min(y):
            plt.text(x=np.min(x) + 0.125, y=np.min(y) - 0.04, s=str(Xi), fontsize=10)
        elif np.min(x) < 0 and np.min(y) < 0:
            plt.text(x=np.min(x) - 0.12, y=np.min(y) - 0.04, s=str(Xi), fontsize=10)
        elif np.min(x) > 0 and np.max(y) > 0:
            plt.text(x=np.min(x) + 0.125, y=np.max(y) + 0.03, s=str(Xi), fontsize=10)
        elif np.min(x) < 0 < np.max(y):
            plt.text(x=np.min(x) - 0.12, y=np.max(y) + 0.03, s=str(Xi), fontsize=10)
        elif np.min(x) == 0:
            if np.min(y) < 0:
                plt.text(x=-0.05, y=-1.06, s='-50', fontsize=10)
            # if np.max(y) > 0:
            #     plt.text(x=-0.03, y=1.03, s='50', fontsize=10)
        else:
            plt.text(x=-1.075, y=0, s='0', fontsize=10)

    for vswr_i in vswr:
        gamma = (vswr_i - 1) / (vswr_i + 1) * np.exp(1j * theta)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'r--', alpha=0.2)
        plt.text(x=-0.1, y=np.max(x).item()+0.02, s=f'$\\rho$ = {vswr_i}')

    try:
        plt.title(f'{data_name} {freq*1e-3}MHz {setpoint}W Setpoint\nMaximum: {dta[max_index]:.2f}{units[data_name]} at {Z_max:.2f} ohms\nEverest {info}', fontsize=11)
    except:
        plt.title(f'{data_name} {freq*1e-3}MHz {setpoint}W Setpoint\nMaximum: {dta[max_index]:.2f}{units[data_name]}\nEverest {info}', fontsize=11)

    ####################################################
    # Code For Normalizing LDMOS Temperature Data      #
    ####################################################

    if "LDMOS Temperature" in data_name:
        if "38646KHz" in outPath:
            plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=151, vmin=28)
            # if "Leading LDMOS Temperature (L)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=133)
            # elif "Leading LDMOS Temperature (R)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=147)
            # elif "Lagging LDMOS Temperature (L)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=120)
            # elif "Lagging LDMOS Temperature (R)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=135)
            # elif "Leading LDMOS Temperature Difference" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=24.8)
            # elif "Lagging LDMOS Temperature Difference" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=22.5)
            # else:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet')
        elif "40680KHz" in outPath:
            plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=150, vmin=25)
            # if "Leading LDMOS Temperature (L)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=126)
            # elif "Leading LDMOS Temperature (R)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=147)
            # elif "Lagging LDMOS Temperature (L)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=88)
            # elif "Lagging LDMOS Temperature (R)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=113)
            # elif "Leading LDMOS Temperature Difference" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=24.8)
            # elif "Lagging LDMOS Temperature Difference" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=10)
            # else:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet')
        else:
            plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=148, vmin=28)
            # if "Leading LDMOS Temperature (L)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=147)
            # elif "Leading LDMOS Temperature (R)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=168)
            # elif "Lagging LDMOS Temperature (L)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=73.25)
            # elif "Lagging LDMOS Temperature (R)" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=94)
            # elif "Leading LDMOS Temperature Difference" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=24.8)
            # elif "Lagging LDMOS Temperature Difference" in data_name:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet', vmax=27.2)
            # else:
            #     plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet')
        # plt.contourf(R_plot, X_plot, data_2D, levels=int(len(dta) / 4), cmap='jet')
        plt.colorbar(label=units[data_name])

        plt.savefig(outPath + f'\\NORM_{data_name}_{freq}KHz_{setpoint}W.png')
        plt.close()
#######################################################################################################


#######################################################################################################
def plotFastDAQ_dlv(data):

    # test = np.genfromtxt(data, delimiter=',')

    fwd = np.genfromtxt(data, delimiter=',', usecols=[3], skip_header=True)
    rfl = np.genfromtxt(data, delimiter=',', usecols=[4], skip_header=True)
    dlv = fwd - rfl

    t = np.linspace(0, 0.160 * len(dlv), len(dlv))

    plt.figure(figsize=(14,10))
    plt.plot(t, dlv, 'b-', label = '$P_{DLV}$')
    plt.ylabel('$P_{DLV}$ (W)')
    plt.xlabel(r't ($\mu$s)')
    plt.grid()
    plt.title('Delivered Power :)')
    plt.show()
#######################################################################################################

#######################################################################################################
def plotFastDAQ_dac(data):

    dac = np.genfromtxt(data, delimiter=',', usecols=[5], skip_header=True)

    a = 5/5.155
    b = 0.133 * a

    dac = a * dac + b

    t = np.linspace(0, 0.239 * len(dac), len(dac))

    plt.figure(figsize=(14,10))
    plt.plot(t, dac, 'r-', label = '$DAC_{V}$')
    plt.ylabel('$DAC_{V}$ (V)')
    plt.xlabel(r't ($\mu$s)')
    plt.grid()
    plt.title('DAC Voltage:) 0W to 30W')
    plt.savefig(r'P:\Paramount\Connor Wallis\fastdaq_0Wto30W.png')
    plt.close()

# data = r'P:\Paramount\Connor Wallis\fastdaq_rfdac1.csv'
# plotFastDAQ_dac(data)

def fixDAC(data):
    """Fix DAC scaling so correct values are displayed."""
    if isinstance(data,(list,tuple)):
        data = [(x+0.133)*5/5.155 for x in data]
    elif isinstance(data,np.ndarray) and data.dtype == np.float32:
        data = (data + np.float32(0.133)) * np.float32(5/5.155)
    else:
        data = (data + 0.133) * (5/5.155)
    return data

#######################################################################################################

#######################################################################################################
def capture_risefall_times_idx(setpoints, tolerance, abstol=False, pwrlog=(), statelog=(), backup_states=()):
    """ Returns list of output power rise & fall times between states at the specified setpoints.
    Looks for the next transition after each state time, indexing based on crossing of lower threshold.
    Tolerance accepted as either a shared number or a pair for low & high
    Use abstol=True to specify absolute watts rather than % of difference
    backup_states is a set of indices with nonzero backup count """
    if np.isscalar(tolerance):  # Duplicate if only one specified
        tolerance = [tolerance,tolerance]
    thresholds: list[tuple | None] = [None]*len(setpoints)
    back_thresholds: list[tuple | None] = [None]*len(setpoints)
    if abstol:  # Calculate thresholds using absolute tolerance
        for (i,(lastpt,thispt)) in enumerate(zip(setpoints[-1:]+setpoints[:-1],setpoints)):
            if thispt < lastpt:   # Falling, high threshold to low threshold
                thresholds[i] = (-1, (lastpt - tolerance[1]), (thispt + tolerance[0]))
                if i in backup_states:
                    back_thresholds[i] = (1, (thispt + tolerance[0]), (lastpt - tolerance[1]))
            elif thispt > lastpt: # Rising, low threshold to high threshold
                thresholds[i] = (1, (lastpt + tolerance[0]), (thispt - tolerance[1]))
                if i in backup_states:
                    back_thresholds[i] = (-1, (thispt - tolerance[1]), (lastpt + tolerance[0]))
    else:             # Calculate thresholds using relative tolerance
        for (i,(lastpt,thispt)) in enumerate(zip(setpoints[-1:]+setpoints[:-1],setpoints)):
            setDiff = abs(thispt-lastpt)
            if thispt < lastpt:   # Falling, high threshold to low threshold
                thresholds[i] = (-1, (lastpt - tolerance[1]*setDiff), (thispt + tolerance[0]*setDiff))
                if i in backup_states:
                    back_thresholds[i] = (1, (thispt + tolerance[0]*setDiff), (lastpt - tolerance[1]*setDiff))
            elif thispt > lastpt: # Rising, low threshold to high threshold
                thresholds[i] = (1, (lastpt + tolerance[0]*setDiff), (thispt - tolerance[1]*setDiff))
                if i in backup_states:
                    back_thresholds[i] = (-1, (thispt - tolerance[1]*setDiff), (lastpt + tolerance[0]*setDiff))
    stepInds = np.nonzero(statelog[:-2] != statelog[1:-1])[0] + 1  # Indices of step changes
    riseInds = []
    fallInds = []
    for (stepInd, nextInd) in pairwise(stepInds):  # Look for rise/fall time for each state transition (not after last state change)
        thisState, lastState = statelog[stepInd], statelog[stepInd-1]
        if thisState == (lastState + 1) % len(setpoints):
            thisThresh = thresholds[thisState]  # if the state count went up, use the normal threshold
            is_backup = False  # Set flag that this is not a backup transition
        elif thisState == (lastState - 1) % len(setpoints):
            thisThresh = back_thresholds[thisState]  # if the state count when down, use the reverse threshold (this is PiP)
            is_backup = True  # Set flag that this is a backup transition
        else: break  # invalid transition if step index changed by more than 1, discard
        startInd = stepInd  # Initialize start index at state transition time
        stepFound = False  # Flag to fill value if no rise/fall was detected
        if thisThresh is None:
            continue  # Skip if setpoint is the same as last state
        if thisThresh[0] == 1:  # Flag is 1 for rising
            inStep = False
            for j in range(stepInd, nextInd):
                if pwrlog[j] <= thisThresh[1]:
                    inStep = False  # Reset flag if this was a blip
                elif pwrlog[j] > thisThresh[1] and not inStep:
                    inStep = True   # Set flag that rise has started
                    startInd = j    # Provisional start index for this rise
                elif pwrlog[j] >= thisThresh[2] and inStep:  # Check prevents a zero rise duration
                    riseInds.append((startInd,j,thisState,is_backup))  # Add start & stop indices if end of rise found
                    stepFound = True
                    break
            if not stepFound:
                riseInds.append((stepInd,nextInd+1,thisState,is_backup))  # on failure to detect, treat end of step as rise time
        elif thisThresh[0] == -1:  # Flag is -1 for falling
            inStep = False
            for j in range(stepInd, nextInd):
                if pwrlog[j] >= thisThresh[1]:
                    inStep = False  # Reset flag if this was a blip
                elif pwrlog[j] < thisThresh[1] and not inStep:
                    inStep = True   # Set flag that fall has started
                    startInd = j    # Provisional start index for this fall
                elif pwrlog[j] <= thisThresh[2] and inStep:  # Check prevents a zero fall duration
                    fallInds.append((startInd,j,thisState,is_backup))  # Add start & stop indices if end of fall found
                    stepFound = True
                    break
            if not stepFound:
                fallInds.append((stepInd,nextInd+1,thisState,is_backup))  # on failure to detect, treat end of step as fall time
    return (riseInds,fallInds)
#######################################################################################################

#######################################################################################################
def get_timestamp(vfp_compatible=True):
    """Generate timestamp string, optionally in format matching VFP"""
    if vfp_compatible:
        capture_datetime = datetime.now().strftime('%m-%d-%Y %H:%M:%S')
    else:
        capture_datetime = datetime.now().strftime('%Y_%m_%d_%H%M%S')
    return capture_datetime
#######################################################################################################

#######################################################################################################
def write_data_csv_fast(filename, interval, data, keys, trigger="Unknown", pos=0, state=None, timestamp=get_timestamp(True), path:str|Path='', additional_info=''):
    """Fast version of write_data_to_csv using Pandas"""
    if isinstance(timestamp,datetime):
        timestamp = timestamp.strftime('%m-%d-%Y %H:%M:%S')
    if not path:  # If path was left undefined, then use the current working directory.
        path = os.getcwd()
    path = Path(path)
    if not os.path.exists(path):
        os.makedirs(path)

    with open(path / (filename + '_' + timestamp.replace(' ','').replace('-','').replace(':','') + ".csv"), 'w', newline='', encoding="utf-8") as file:
        file.write('\ufeff') #UTF-8 BOM encoded (VFP)
        file.write(f"Date,{timestamp}\nTrigger Source,{trigger}\nSample Interval,{interval}\nTrigger Position,{pos}\n")
        if state is not None:
            file.write(f"Trigger State, {state}\n")
        file.write(f"{additional_info}\n")   # An empty line signifying End of Header
        t_ns = np.arange(0,len(data[0])*interval,interval)
        t_ms, t_ns = divmod(t_ns, 1000000)
        t_us, t_ns = divmod(t_ns, 1000)
        df = DataFrame.from_dict(dict(zip(['Time (ms)','Time (µs)','Time (ns)']+keys,[t_ms,t_us,t_ns]+data)))
        df.to_csv(file,index=False,float_format=lambda f: np.format_float_positional(f,trim='-'))
#######################################################################################################

#######################################################################################################
def write_data_to_csv(filename, interval, data, keys, trigger="Unknown", pos=0, state=None, timestamp=get_timestamp(True), path='', additional_info=''):
    """Generate CSV of captured data in same format as VFP"""
    if isinstance(timestamp,datetime):
        timestamp = timestamp.strftime('%m-%d-%Y %H:%M:%S')
    if not path:  # If path was left undefined, then use the current working directory.
        path = os.getcwd()
    if not os.path.exists(path):
        os.makedirs(path)

    with open(path + filename + '_' + timestamp.replace(' ','').replace('-','').replace(':','') + ".csv", 'w', encoding="utf-8") as file:
        file.write('\ufeff') #UTF-8 BOM encoded (VFP)

        # Write Header
        # Date, 10 / 21 / 2022 10: 02:14
        # Trigger Source, Setpoint
        # Sample Interval, 20000
        # Trigger Position, 10

        file.write(f"Date,{timestamp}\n")
        file.write(f"Trigger Source,{trigger}\n")
        file.write(f"Sample Interval,{interval}\n")
        file.write(f"Trigger Position,{pos}\n")
        if state is not None:
            file.write(f"Trigger State, {state}\n")
        file.write(f"{additional_info}\n")   # An empty line signifying End of Header

        # Write Data Header Line
        line = "Time (ms),Time (µs),Time (ns)"
        # Write Channel Headers by reformatting the channel id.
        for channel in keys:
            # Split the string by Capital letters.
            # NOTE: The result splits it too much. Need to put words back together.
            formatted_channel = re.split(r'([A-Z])', channel)
            line += ','
            # While there are still items in the split list of substrings
            if channel.lower() == 'mask':
                line += ','.join([f'Mask {bit}' for bit in range(1,5)])
            elif channel.lower() in ('step','state','stateidx'):
                line += 'StateIdx'
            elif channel.lower() in ('rfdacai','rfdac','dac'):
                line += '3-RF DAC AI'
            else:
                while formatted_channel:
                    # Pop the first item off of the list.
                    substring = formatted_channel.pop(0)

                    if substring:   # If substring is non-empty string.
                        line += substring

                        # Put a space between words that are Camel Case,
                        #   except if on the final part of channel string.
                        if len(formatted_channel) > 0 and not re.match(r'[A-Z]', substring):
                            line += ' '

        file.write(line + "\n")

        # Write Data
        t = 0    # Get the time index.
        for index in range(0, len(data[0])):
            time_split = _time_split(t)
            # Write Time
            line = f"{time_split['ms']}, {time_split['us']}, {time_split['ns']}"
            for (ci,channel) in enumerate(keys):
                if channel.lower() in ['internalsetpoint']:
                    line += f",{int(data[ci][index]/10)}"
                elif channel.lower() == 'mask':
                    for mask_bit in range(0, 4):
                        line += f",{int(data[ci][index]>>mask_bit & 1)}"
                else:  # print numpy single floats without insignificant digits
                    line += ","+np.format_float_positional(data[ci][index],trim='-')
            file.write(line + '\n')
            t += interval

    return path
#######################################################################################################

#######################################################################################################
def _time_split(time_ns: int):
    """@return Time as a dict split separately into integer ms, us, and ns components"""
    result = {}
    result['ms'], time_ns = divmod(time_ns, 1000000)
    result['us'], result['ns'] = divmod(time_ns, 1000)
    return result
#######################################################################################################

#######################################################################################################
def config_gs_parse(config: str|bytes|bytearray, savepath=''):
    """Parse config file and return gain schedule as 20x20 uint16 NumPy array.
    This function can accept the output of reportConfigurationFile() directly.
    Inputs:
    - config: Uncompressed config file as str or bytes. (Pass output of read() if using file.)
    - savepath (optional): Save gain schedule in this location as .csv file."""
    if isinstance(config,str):
        entries = config.split('k_gs_')
        sep = ';'
    else:
        entries = config.split(b'k_gs_')
        sep = b';'
    if len(entries) != 401:
        raise ValueError("unexpected config format: does not contain 400 GS table entries")
    gs_tab = np.empty((20,20),dtype=np.uint16)
    for entry in entries[1:]:
        try:
            gs_tab[int(entry[0:2]),int(entry[5:7])] = int(entry[8:13].split(sep)[0])
        except ValueError as e:
            e.add_note(f'failed to parse entry "{entry}"')
            raise
    if savepath:
        np.savetxt(savepath,gs_tab,delimiter=',',fmt='%d')
    return gs_tab
#######################################################################################################

#######################################################################################################
def powerMonics_SmithPlot(data, spec, outPath, info, freq, setpoint):
    # numlines = np.genfromtxt(data, delimiter=',', usecols=[0], dtype=str)
    # headers = np.genfromtxt(data, delimiter=',', skip_footer=len(numlines)-1, dtype=str)
    # data_tot = np.genfromtxt(data, usecols=range(0, len(headers)), delimiter=',', dtype=str)
    R_spur = []
    X_spur = []
    setpoint_csv = []
    start_freq = []
    stop_freq = []
    amp_csv = []
    delimiters_string = (',','[',']')
    df = read_table(data,delimiter=',')
    R_spur = df['R'].to_numpy()
    X_spur = df['X'].to_numpy()
    setpoint_csv = df['Setpoint'].to_numpy()
    start_freq = df['Start'].to_numpy()
    stop_freq = df['Stop'].to_numpy()
    df['Amplitude'] = df['Amplitude'].str.replace('[','',regex=False).str.replace(']','',regex=False)
    df['Amplitude'] = df['Amplitude'].str.split(',')
    # amp_csv = df['Amplitude'].apply(lambda x: np.max([float(i) for i in x]))
    amp_csv = df['Amplitude'].to_numpy()
    amp_max = []
    for i in range(0,len(amp_csv),1):
        amp_temp = spec
        for x in range(0,len(amp_csv[i]),1):
            temp_amp = float(amp_csv[i][x])
            amp_csv[i][x] = temp_amp
            if -100 < temp_amp < -30:
                if amp_temp < temp_amp:
                    amp_temp = temp_amp         
        amp_max.append(amp_temp)
    gamma_plot = []

    for i in range(0,len(R_spur),1):
        if (i > 0) & (amp_max[i]>amp_max[i-1]):
            amp_indx = i
        Z = R_spur[i]+1j*X_spur[i]
        gamma = (Z-50)/(Z+50)
        gamma_plot.append([np.real(gamma),np.imag(gamma)])

    R_plot, X_plot = np.meshgrid(np.linspace(-1, 1, 500), np.linspace(-1, 1, 500))
    
    data_2D = scipy.interpolate.griddata(gamma_plot, amp_max, (R_plot, X_plot))

    Z0 = 50
    vswr = [1.2, 1.5, 2, 3, 5]
    # unit circle outline
    theta = np.linspace(0, 2*np.pi, 100)
    x1 = np.cos(theta)
    y1 = np.sin(theta)

    R = [10, 25, 50, 100, 250]
    X = [-250, -100, -50, -25, -10, 0, 10, 25, 50, 100, 250]
    R1 = np.arange(0, np.max(R), 10)

    plt.figure(figsize=(13,11))
    plt.plot(x1,y1,'k-')
    plt.axis('off')

    for Ri in R:
        g = (Ri - Z0)/(Ri + Z0)
        center = (1+g)/2
        r = 1 - center
        x = center + r*np.cos(theta)
        y = r*np.sin(theta)
        plt.plot(x,y, 'b--', alpha=0.1)
        plt.text(x=g+0.02, y=0, s=str(Ri), fontsize=10)

    for Xi in X:
        Z = R1 + 1j*Xi
        gamma = (Z - Z0)/(Z + Z0)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'b--', alpha=0.1)
        if np.min(x) > 0 > np.min(y):
            plt.text(x=np.min(x)+0.125, y=np.min(y)-0.04, s=str(Xi), fontsize=10)
        elif np.min(x) < 0 and np.min(y) < 0:
            plt.text(x=np.min(x)-0.12, y=np.min(y)-0.04, s=str(Xi), fontsize=10)
        elif np.min(x) > 0 and np.max(y) > 0:
            plt.text(x=np.min(x)+0.125, y=np.max(y)+0.03, s=str(Xi), fontsize=10)
        elif np.min(x) < 0 < np.max(y):
            plt.text(x=np.min(x)-0.12, y=np.max(y)+0.03, s=str(Xi), fontsize=10)
        elif np.min(x) == 0:
            if np.min(y) < 0:
                plt.text(x=-0.05, y=-1.06, s='-50', fontsize=10)
            # if np.max(y) > 0:
            #     plt.text(x=-0.03, y=1.03, s='50', fontsize=10)
        else:
            plt.text(x=-1.075, y=0, s='0', fontsize=10)

    for vswr_i in vswr:
        gamma = (vswr_i -  1)/(vswr_i + 1) * np.exp(1j*theta)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'r--', alpha=0.5)
        plt.text(x=-0.1, y=np.max(x)+0.02, s=f'$\\rho$ = {vswr_i}')

    try:
        plt.title(f'Spurious {freq * 1e-3}MHz {setpoint}W Setpoint\nMaximum: {max(amp_max):.2f} dBc at {R_spur[amp_indx]:.2f},{X_spur[amp_indx]:.2f} ohms\nEverest {info}', fontsize=11)
    except:
        plt.title(f'Spurious {freq * 1e-3}MHz {setpoint}W Setpoint\nMaximum: {max(amp_max):.2f} dBc\nEverest {info}', fontsize=11)

    plt.contourf(R_plot, X_plot, data_2D, levels=np.linspace(-65,max(amp_max),20), cmap='jet')
    plt.colorbar(label='dBc')

    plt.savefig(outPath+f'\\Spurious_{freq}kHz_{setpoint}W.png')
    plt.close()
#######################################################################################################

#######################################################################################################
def plot_stability(info, outPath, fileName, SN, setpoint, hours):
    testing10kWPlatform = False
    file = outPath + r'\\' + fileName
    pfwd_gen = []
    prfl_gen = []
    pfwd_lynx = []
    prfl_lynx = []
    genvsmeter_fwd = []
    genvsmeter_rfl = []
    genvssetpoint = []
    metervssetpoint = []
    rftemp = []
    if testing10kWPlatform:
        dcInVolt = []
        dcInCurrent  = []
        dcInPower = []
        dcOutVolt = []
        dcOutCurrent = []
        dcOutPower = []
        PaDissPower = []
    
        data = np.genfromtxt(file, delimiter=',', usecols=[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15], skip_header=True)
        for x in range(len(data)):
            pfwd_gen.append(data[x][0])
            prfl_gen.append(data[x][1])
            pfwd_lynx.append(data[x][2])
            prfl_lynx.append(data[x][3])
            genvsmeter_fwd.append(data[x][4])
            genvsmeter_rfl.append(data[x][5])
            genvssetpoint.append(data[x][6])
            metervssetpoint.append(data[x][7])
            rftemp.append(data[x][8])
            dcInVolt.append(data[x][9])
            dcInCurrent.append(data[x][10])
            dcInPower.append(data[x][11])
            dcOutVolt.append(data[x][12])
            dcOutCurrent.append(data[x][13])
            dcOutPower.append(data[x][14])
            PaDissPower.append(data[x][15])
    else:
        data = np.genfromtxt(file, delimiter=',', usecols=[0,1,2,3,4,5,6,7,8], skip_header=True)
        for x in range(len(data)):
            pfwd_gen.append(data[x][0])
            prfl_gen.append(data[x][1])
            pfwd_lynx.append(data[x][2])
            prfl_lynx.append(data[x][3])
            genvsmeter_fwd.append(data[x][4])
            genvsmeter_rfl.append(data[x][5])
            genvssetpoint.append(data[x][6])
            metervssetpoint.append(data[x][7])
            rftemp.append(data[x][8])

    t = np.linspace(0, len(pfwd_gen), len(pfwd_gen))

    img_name1 = os.path.join(outPath, r'SN{}_genreportedpower_{}Wsetpoint_{}hours.png'.format(SN, setpoint, hours))
    img_name2 = os.path.join(outPath, r'lynxreportedpower_{}Wsetpoint_{}hours.png'.format(setpoint, hours))
    img_name3 = os.path.join(outPath, r'genvslynxerror_{}Wsetpoint_{}hours.png'.format(setpoint, hours))
    img_name4 = os.path.join(outPath, r'setpointerror_{}Wsetpoint_{}hours.png'.format(setpoint, hours))
    img_name5 = os.path.join(outPath, r'rfcoldplatetemp_{}Wsetpoint_{}hours.png'.format(setpoint, hours))
    img_name6 = os.path.join(outPath, r'Burn-In_{}Wsetpoint_{}hours.png'.format(setpoint, hours))
    if testing10kWPlatform:
        img_name7 = os.path.join(outPath, r'dcVoltage_{}Wsetpoint_{}hours.png'.format(setpoint, hours))
        img_name8 = os.path.join(outPath, r'dcCurrent_{}Wsetpoint_{}hours.png'.format(setpoint, hours))
        img_name9 = os.path.join(outPath, r'dcPower_{}Wsetpoint_{}hours.png'.format(setpoint, hours))

    time = np.linspace(0, hours * 3600, 1)
    labels = np.linspace(0, hours,1)

    plt.figure(figsize=(14,8))
    plt.plot(t, pfwd_gen, 'b', label='$P_{fwd}$')
    plt.plot(t, prfl_gen, 'r', label='$P_{rfl}$')
    plt.xticks(time, labels)
    plt.xlabel('Hours')
    plt.ylabel('Watts')
    plt.title('Generator Reported Power\n{}W Setpoint for {} Hours\n{}'.format(SN, setpoint, hours, info))
    plt.legend()
    plt.grid()
    plt.savefig(img_name1)
    plt.close()

    plt.figure(figsize=(14,8))
    plt.plot(t, pfwd_lynx, 'b', label='$P_{fwd}$')
    plt.plot(t, prfl_lynx, 'r', label='$P_{rfl}$')
    plt.xticks(time, labels)
    plt.xlabel('Hours', fontsize=14)
    plt.ylabel('Watts', fontsize=14)
    plt.title('Lynx Reported Power\n{}W Setpoint for {} Hours'.format(setpoint, hours))
    plt.legend()
    plt.grid()
    plt.savefig(img_name2)
    plt.close()

    fig, ax1 = plt.subplot(figsize=(14,8))
    ax1.plot(t,genvsmeter_fwd, 'b', label='$P_{fwd}$ Error')
    plt.xticks(time, labels,step=0.2)
    plt.xlabel('Hours', fontsize=14)
    ax1.ylabel('Error (%)', fontsize=14)

    ax2 = ax1.twinx()
    ax2.plot(t, genvsmeter_rfl, 'r', label='$P_{rfl}$ Error')
    ax2.ylabel()

    plt.title('Generator vs Lynx Reported Power Error\n{}W Setpoint for {} Hours'.format(setpoint, hours))
    plt.legend()
    plt.grid()
    plt.savefig(img_name3)
    plt.close()

    plt.figure(figsize=(14,8))
    plt.plot(t, genvssetpoint, 'b', label='Generator')
    plt.plot(t, metervssetpoint, 'r', label='Lynx')
    plt.xticks(time, labels)
    plt.xlabel('Hours', fontsize=14)
    plt.ylabel('Error (%)', fontsize=14)
    plt.title('Setpoint Error\n{}W Setpoint for {} Hours'.format(setpoint, hours))
    plt.legend()
    plt.grid()
    plt.savefig(img_name4)
    plt.close()

    plt.figure(figsize=(14, 8))
    plt.plot(t, rftemp, 'r', label='$Temperature_{RF}$')
    plt.xticks(time, labels)
    plt.xlabel('Hours')
    plt.ylabel('Temperature (°C)')
    plt.title('RF Temperature\n{}W Setpoint for {} Hours'.format(setpoint, hours))
    plt.legend()
    plt.grid()
    plt.savefig(img_name5)
    plt.close()

    fig = plt.figure(figsize=(14, 8))

    plt.plot(t, pfwd_gen, 'b', label='$P_{fwd}$')
    plt.plot(t, prfl_gen, 'r', label='$P_{rfl}$')
    plt.plot(t, pfwd_lynx, 'c', label='$P_{fwd}$')
    plt.plot(t, prfl_lynx, 'm', label='$P_{rfl}$')
    plt.plot(t, rftemp, 'g', label='$RF Temperature$')
    plt.xticks(time, labels)
    plt.xlabel('Hours')
    plt.ylabel('Watts')
    plt.title('Generator Reported Power\n{}W Setpoint for {} Hours\n{}'.format(SN, setpoint, hours, info))
    plt.legend()
    plt.grid()
    plt.savefig(img_name6)
    plt.close()

    if testing10kWPlatform:

        plt.figure(figsize=(14,8))
        plt.plot(t, dcInVolt, 'b', label='$V_{Volts}$')
        plt.plot(t, dcOutVolt, 'r', label='$V_{Volts}$')
        plt.xticks(time, labels)
        plt.xlabel('Hours')
        plt.ylabel('DC Voltage')
        plt.title('Generator Reported DC Voltage\n{}Wsetpoint for {} Hours\n{}'.format(SN, setpoint, hours, info))
        plt.legend()
        plt.grid()
        plt.savefig(img_name7)
        plt.close()

        plt.figure(figsize=(14,8))
        plt.plot(t, dcInCurrent, 'c', label='$A_{Amps}$')
        plt.plot(t, dcOutCurrent, 'm', label='$A_{Amps}$')
        plt.xticks(time, labels)
        plt.xlabel('Hours')
        plt.ylabel('DC Current')
        plt.title('Generator Reported DC Current\n{}Wsetpoint for {} Hours\n{}'.format(SN, setpoint, hours, info))
        plt.legend()
        plt.grid()
        plt.savefig(img_name8)
        plt.close()

        plt.figure(figsize=(14,8))
        plt.plot(t, dcInPower, 'c', label='$W_{Watts}$')
        plt.plot(t, dcOutPower, 'm', label='$W_{Watts}$')
        plt.plot(t, PaDissPower, 'r', label='$W_{Watts}$')
        plt.xticks(time, labels)
        plt.xlabel('Hours')
        plt.ylabel('DC Power')
        plt.title('Generator Reported DC Power\n{}Wsetpoint for {} Hours\n{}'.format(SN, setpoint, hours, info))
        plt.legend()
        plt.grid()
        plt.savefig(img_name9)
        plt.close()
#######################################################################################################

#######################################################################################################
def error(val1, val2):
    return abs((val1 - val2) / val2) * 100
#######################################################################################################