"""
Created by:             Sterling Krone
Last Modified by:       Luis Martinez
Data Last Modified:     February 25 2021
Revision of Python:     3.7
"""
import AE_Com_Module
import numpy as np
import os
import csv
from time import *
from tkinter import *
NORM_FONT = ("Arial", 12)
BOLD_FONT = ("Arial", 12, "bold")


def popupmsg(msg):
    popup = Tk()
    popup.wm_title("!")
    Label(popup, text=msg, font=NORM_FONT).pack(side="top", fill="x", pady=10)
    Button(popup, text="Okay", command=popup.destroy).pack()
    popup.mainloop()

class generator:
    def __init__(self, ipAddress):
        self.gen = AE_Com_Module.AETCP()
        self.gen.connect(ipAddress)
        self.gen.create_buffer_cmd(14, '1:1', '0:1', 2)  # Host Mode
#       self.gen.create_buffer_cmd(3, '1:1', '0:1', 6)  # Forward Power Regulation
#       self.gen.create_buffer_cmd(48, '1:1', '0:1', 0)  # Set Fixed
#       freq = self.gen.create_buffer_cmd(147, '1:1', '1:4', 0)  # Report Generator Actual Frequency
#       print("Connected to the : " + str(freq[2] / 1e3) + "MHz Generator")

    def setActiveControlMode(self, mode):
        if mode == 2 or mode == 4 or mode == 8 or mode == 16:
            res = self.gen.create_buffer_cmd(14, '1:1', '1:1', mode)
        else:
            raise Exception('Invalid control mode number entered! (Must be 2,4,8, or 16)')
        '''Mode 2 - Host
           Mode 4 - User Port (Analog)
           Mode 8 - Diagnostic Mode
           Mode 16 - DeviceNet'''

    def setSerialTimeout (self,Time):
        Time = Time/10
        res = self.gen.create_buffer_cmd(40, '1:2', '0:0',Time)

    def serialNumber(self):
        res = self.gen.create_buffer_cmd(231, '1:1', '1:4', 0)  # Serial Number
        serialNo = str(res[2])
        return serialNo

    def reportPINNumber(self):
        res = self.gen.create_buffer_cmd(221, '0:0', '32:1')
        res = res[2:]
        pin = ''
        for digit in range(len(res)):
            if res[digit] != 0:
                pin += chr(res[digit])
        return pin

    def isHaloEnabled(self):
        if self.reportPINNumber()[20] == '1':
            return True
        else:
            return False

    def unit315(self):
        res = self.gen.create_buffer_cmd(231, '1:1', '12:1', 1)  # Unit 315 number
        res = res[2:]
        unitPN = ''
        for digit in range(len(res)):
            if res[digit] != 0:
                unitPN += chr(res[digit])
        return unitPN

    def supplySize(self):
        res = self.gen.create_buffer_cmd(129, '1:1','6:1',0)
        res = res[2:]
        size = ''
        for digit in range(len(res)):
            if res[digit] != 0:
                size += chr(res[digit])
        return int(size)

    def RF_OFF(self):
        self.gen.create_buffer_cmd(1, '0:0', '1:1')  # RF OFF

    def RF_ON(self):
        res = self.gen.create_buffer_cmd(2, '0:0', '1:1')  # RF ON!
        while res[2] != 0:
            res = self.gen.create_buffer_cmd(2, '0:0', '1:1')
        return res[2]

    def testPIN(self, tempPin):
        res = self.gen.create_buffer_cmd(256, '1:2', '31:1',[tempPin])

    def setRegulationMode(self,mode):
        # 1 = Sate 1 | 2 = State 0
        self.gen.create_buffer_cmd(3, '2:1', '0:0', 1, mode)
        if mode == 6:
            print('Forward Mode Set')
        if mode == 7:
            print('Delivered Mode Set')

    def powerSetpoint(self, setpoint):
        self.gen.create_buffer_cmd(8, '1:2', '0:1', int(setpoint))  # Set Power Setpoint

    def setGenFreq(self, freq): #kHz
        res = self.gen.create_buffer_cmd(61, '1:4', '0:0', int(freq))

    def setPFreq(self, freq):
        res = self.gen.create_buffer_cmd(93, '1:4', '0:0', int(freq))

    def setPDutyCycle(self, dc):
        res = self.gen.create_buffer_cmd(96, '1:2', "0:0", int(dc))

    def OpenLoop(self, Mode=0):
        if Mode == 0:
            print('Open Loop Mode')
        if Mode == 1:
            print('Closed Loop Mode')
        self.gen.create_buffer_cmd(92, '1:1', '1:1', Mode)  # 0: Open Loop | 1: Closed Loop


    def setRFDAC(self, val):
        """ restricted command
        Device      Channel     Value 		Description
        1		    0		    0-65535		Power Control DAC State 0
        1		    1		    0-65535		Power Control DAC State 1
        2		    0		    0-4095		User Card DAC  CH 0
        2		    1		    0-4095		User Card DAC  CH 1
        2		    2		    0-4095		User Card DAC  CH 2
        2		    3		    0-4095		User Card DAC  CH 3
        3		    0		    0-4095		RF Drive DAC  CH 0
        3		    1		    0-4095		RF Drive DAC  CH1
        4           0           0-65535     Buck DAC - CH0"""
        self.gen.create_buffer_cmd(80, '2:1,1:2', '0:0', 3, 1, val)


    def setBuckDAC(self, val):
        self.gen.create_buffer_cmd(80, '2:1,1:2', '0:0', 4, 0, val)

    def readVDC(self):
        voltage = self.gen.create_buffer_cmd(207, '2:2', '1:2', 66, 0)
        return voltage[2] / 10.0

    def readIDC(self):
        current = self.gen.create_buffer_cmd(207, '2:2', '1:2', 67, 0)
        return current[2] / 10.0

    def readPDC(self):
        power = self.gen.create_buffer_cmd(207, '2:2', '1:2', 68, 0)
        return power[2]

    def readDC(self):
        res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 66, 0)
        voltage = res[2] / 10.0
        res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 67, 0)
        current = res[2] / 10.0
        power = float(voltage) * float(current)
        return voltage, current, power

    def reportDACState(self):
        res = self.gen.create_buffer_cmd(174, '2:1', '1:2', 3, 1)
        RFdriveDAC = res[2]
        res = self.gen.create_buffer_cmd(174, '2:1', '1:2', 4, 0)
        DCdriveDAC = res[2]
        return RFdriveDAC, DCdriveDAC

    def reportPower(self):
        res = self.gen.create_buffer_cmd(165, '1:1', '2:1', 0) #Forward
        pwrFwd = res[2]
        res = self.gen.create_buffer_cmd(166, '1:1', '1:1', 0) # eflected
        pwrRfl = res[2]
        res = self.gen.create_buffer_cmd(167, '1:1', '1:1',0) #Delivered
        PwrDel = res[2]
        return pwrFwd, pwrRfl, PwrDel

    def readcalCoeff(self):
        Destination = 1 # Destination (0 = Ram or 1 = Ram and EEPROM)
        values = self.gen.create_buffer_cmd(208,'2:1,1:2','8:2d', Destination, 0, 3)
        return values

    def writecalCoeff(self):
        res = self.gen.create_buffer_cmd(98,'2:1,1:2,8:2d','0:0',1,0,4,1,2,3,4,5,6,7,7)
        return res

    def genSnapShot(self,Opt):
        if Opt == 1:
            res = self.gen.create_buffer_cmd(222, '1:1', '4:2,2:4d,1:4,10:2,2:4d,1:4,25:2,1:4', 1)
        if Opt == 2:
            # IIII 4:2
            # ll 2:4d
            # L 1:4
            # I 1:2
            # i 1:2d
            # IIII 4:2
            # ll 2:4d
            # L 1:4
            # I 1:2
            # L 1:4
            # i 1:2d
            # I 1:2
            # BBBB 4:1
            # IIIIIIIIIIII 12:2
            # BBBBBBBB 8:1
            # IIIIIII 7:2
            # llll 4:4d
            # ffff 4:f
            # 4:2,2:4d,1:4,1:2,1:2d,4:2,2:4d,1:4,1:2,1:4,1:2d,1:2,4:1,12:2,8:1,7:2,4:4d,4:f
            # res = self.gen.create_buffer_cmd(222, '1:1', '4:2,3:4d,6:2,2:4d,1:2,1:4,2:2,3:1,13:2,8:1,2:2,4:2,4:4,4:f', 2)
            # res = self.gen.create_buffer_cmd(222, '1:1', '4:2,3:4d,6:2,3:4d,1:2,1:4,2:2,4:1,12:2,8:1,2:2,1:2,2:1,3:2,4:4,4:f', 2)
            # res = self.gen.create_buffer_cmd(222, '1:1', '4:2,2:4d,1:4,1:2,1:2d,4:2,2:4d,1:4,1:2,1:4,1:2d,1:2,4:1,12:2,8:1,7:2,4:4d,4:f', 2)
            res = self.gen.create_buffer_cmd(222, '1:1', '4:2,2:4d,1:4,6:2,2:4d,1:4,1:2,1:4,2:2,4:1,12:2,8:1,2:2,5:2,4:4d,4:f', 2)
        return res[2:]

    def reportMeasUncorrData(self, ADCnum=1):
        res = self.gen.create_buffer_cmd(182, '2:1', '4:4d', 0, ADCnum)  #0 reports uncorrected V, 1 reports corrected V, 2 reports corrected power
        Reflected_Q = res[2]
        Reflected_I = res[3]
        Forward_Q = res[4]
        Forward_I = res[5]
        return Reflected_Q, Reflected_I, Forward_Q, Forward_I

    def reportMeasCorrFactors(self,Source,Mc,PINPos):
        # d0 = Source of correction factor data (0 = Ram, 1 = EEPROM)
        # d1 = Matrix Select (Center = 0, A = 1,B = 2)
        # d3 = PIN Select (0 - 15)
        res = self.gen.create_buffer_cmd(208, '2:1,1:2', '8:2d', Source, Mc, PINPos)
        RealK11 = res[2]
        RealK12 = res[3]
        RealK21 = res[4]
        RealK22 = res[5]
        ReacK11 = res[6]
        ReacK12 = res[7]
        ReacK21 = res[8]
        ReacK22 = res[9]
        return RealK11,RealK12,RealK21,RealK22,ReacK11,ReacK12,ReacK21,ReacK22

    def reportMediumFreqMeasurements(self):
        res = self.gen.create_buffer_cmd(248, '1:2', '1:4,5:4d,3:f', 176)
        requestFreq = res[2]
        forward = res[3]
        reflected = res[4]
        delivered = res[5]
        R = res[6] / 100.0
        X = res[7] / 100.0
        voltage = res[8]
        current = res[9]
        # There is phase if needed
        phase = res[10]
        return requestFreq, forward, reflected, delivered, R, X, voltage, current

    def matchSetActiveControlmode(self,mode):
        if mode == 1:
            print('Auto Mode')
        if mode == 2:
            print('Host Mode')
        res = self.gen.create_buffer_cmd(120, '2:2', '1:1', 93, mode)

    def setPINDiodePositions(self,Pos):
        # bit    0 = Shunt
        # bit    1 = Shunt
        # bit    2 = Shunt
        # bit    3 = Series
        Pos = int(Pos, 2)
        res = self.gen.create_buffer_cmd(120, '1:2,2:1', '1:1',86, Pos,0)


    def reportMatchSParameters(self):
        S11 = np.zeros([16,31],dtype=complex)
        S12 = np.zeros([16,31],dtype=complex)
        S21 = np.zeros([16,31],dtype=complex)
        S22 = np.zeros([16,31],dtype=complex)
        for pIdx in range(65): # S11 1-16 | S12 17-32 | S21 33-48 | S22 49-64
            if pIdx == 0:
                continue
            res = self.gen.create_buffer_cmd(240, '2:2', '62:f', 235, pIdx)
            PINdata = res[2:]
            fIdx = 0
            for Idx in range(0, 62, 2):  # 62 / 2 = 31 frequencies
                if pIdx <= 16 and pIdx >= 1:
                    S11[pIdx - 1,fIdx] = complex(PINdata[Idx], PINdata[Idx + 1])
                if pIdx <= 32 and pIdx >= 17:
                    S12[pIdx - 17,fIdx] = complex(PINdata[Idx], PINdata[Idx + 1])
                if pIdx <= 48 and pIdx >= 33:
                    S21[pIdx - 33,fIdx] = complex(PINdata[Idx], PINdata[Idx + 1])
                if pIdx <= 64 and pIdx >= 49:
                    S22[pIdx - 49,fIdx] = complex(PINdata[Idx], PINdata[Idx + 1])
                fIdx += 1
        return S11, S12, S21, S22


    def reportPINpostion(self):
        res = self.gen.create_buffer_cmd(240, '1:2', '2:1', 226)
        PinPos = res[2]
        return PinPos


    def ADCbufferData(self, rfa):
        # now = strftime('%Y_%m_%d_%H_%M',localtime())
        # outPathadc = r'P:\Paramount\Cumulus\data\RFDS27\CalibrationData\adclogData'
        # outFname = os.path.join(outPathadc, 'RFDS_adclog_data_{}.csv'.format(now))
        # fhl = open(outFname, 'w', newline='')
        # write_r = csv.writer(fhl, 'excel')
        # header = ('time (us)', 'voltage', 'current')
        # write_r.writerow(header)
        if rfa == 1:
            #measure HF system
            CTRL_REG_ADDR = 0x14  # LM - added addresses for MF read back, uses if statement to switch between addresses
            DATA1_ADDR = 0x18     # use 'rfa = 1' for HF read back, 'rfa = 2' for MF read back
            DATA2_ADDR = 0x1C
        else:
            CTRL_REG_ADDR = 0x1E0
            DATA1_ADDR = 0x1E4
            DATA2_ADDR = 0x1E8
        BASE_ADDR = 0x60030000
        ADC_CLOCK = 63000000.0
        # SAMPLES = 128
        SAMPLES = 256
        dT = 1.0 / ADC_CLOCK
        WINDOW = SAMPLES * dT
        adc_value1 = np.empty([SAMPLES])
        adc_value2 = np.empty([SAMPLES])
        res = self.gen.create_buffer_cmd(87, '4:2, 3:4', '1:1', 2, 0, 0, 0, BASE_ADDR, CTRL_REG_ADDR, 1)
        i = 0
        # set coef reg
        for x in range(SAMPLES):
            recv = self.gen.create_buffer_cmd(207, '4:2, 2:4', '1:4', 5, 0, 0, 0, BASE_ADDR, DATA1_ADDR)
            adc_value1[i] = recv[2]
            if adc_value1[i] >= 8192:
                adc_value1[i] = adc_value1[i] - 16383
            recv = self.gen.create_buffer_cmd(207, '4:2, 2:4', '1:4', 5, 0, 0, 0, BASE_ADDR, DATA2_ADDR)
            adc_value2[i] = recv[2]
            if adc_value2[i] >= 8192:
                adc_value2[i] = adc_value2[i] - 16383
            i = i + 1
        # clear log freeze bit
        recv = self.gen.create_buffer_cmd(87, '4:2,3:4', '1:1', 2, 0, 0, 0, BASE_ADDR, CTRL_REG_ADDR, 0)
        VADCFS = np.max(np.abs(adc_value1))
        IADCFS = np.max(np.abs(adc_value2))
        print('Voltage ADC Counts: {} | Current ADC Counts: {}'.format(VADCFS, IADCFS))
        # Data for plotting
        # step = dT * 1000000.0
        # window = WINDOW * 1000000.0
        # t = np.arange(0.0, window, step)
        # data = []
        # for i in range(len(t)):
        #     data.append(t[i])
        #     data.append(adc_value1[i])
        #     data.append(adc_value2[i])
        #     write_r.writerow(data)
        #     fhl.flush()
        #     os.fsync(fhl)
        #     data = []
        # fhl.close()
        return VADCFS, IADCFS


    def reportCPTemp(self):
        res = self.gen.create_buffer_cmd(228,'1:1','1:2d', 1)
        waterIn = float(res[2]) / 10.0
        res = self.gen.create_buffer_cmd(228,'1:1','1:2d', 2)
        waterOut = float(res[2]) / 10.0
        return waterIn, waterOut

    def reportFaultorWarning(self):
        res = self.gen.create_buffer_cmd(223, '1:1','2:2', 3)
        faultCodeOne = res[2]
        faultCodeTwo = res[3]
        return faultCodeOne, faultCodeTwo

    def reportPulsingConfig(self,mode):
#        i=1
#        mode = list(range(1,18))
        Pulsedata = []
#        for i in range(mode):
        res = self.gen.create_buffer_cmd(172, '1:2', '2:2', mode)
        Pulsedata = res[2]
#            i=i+1
        return Pulsedata

    def reportMeasurePulse(self):
        res = self.gen.create_buffer_cmd(248, '2:2', '1:4,1:2', 65, 1)
        freq = res[2]
        dutyCycle = res[3]
        return freq, dutyCycle

    def close(self):
        """Close the socket"""
        self.gen.close()

class flackpack:
    def __init__(self, ipAddress):
        if ipAddress[0] == 'C':
            self.navII = AE_Com_Module.AEB32(ipAddress, baud=115200, timeout=1000, ae_addr=1)
        else:
            self.navII = AE_Com_Module.AETCP()
            self.navII.connect(ipAddress)


    def unlockUnit(self):
        res = self.navII.create_buffer_cmd(67, '1:1', '1:1', 19)
        res = self.navII.create_buffer_cmd(68, '1:1', '1:1', 47)
        print('Unit Unlocked.')

    def setFreq(self, frequency):
        res = self.navII.create_buffer_cmd(77, '1:4', '1:1', int(frequency))

    def getFreq(self):
        res = self.navII.create_buffer_cmd(147, '0:0', '1:4')
        # while res[2:][0] == 0:
        #     res = self.navII.create_buffer_cmd(147, '1:2', '1:4', 0)
        return res[2]

    def readUnit222(self):
        values = self.navII.create_buffer_cmd(222, '0:0', '7:f')
        forward = values[2]
        reflected = values[3]
        delivered = values[4]
        R = values[5]
        X = values[6]
        voltage = values[7]
        current = values[8]
        if R > 0.0:
            phase = np.arctan(X / R)
        else:
            phase = 0.0
        return forward, reflected, delivered, R, X, voltage, current


    def reportRawMeasurementData(self):
        res = self.navII.create_buffer_cmd(207,'1:2,2:1','4:4d',104,1,0)
        uncorrVRFL_Q = res[2]
        uncorrVRFL_I = res[3]
        uncorrVFWD_Q = res[4]
        uncorrVFWD_I = res[5]
        return uncorrVRFL_Q, uncorrVRFL_I, uncorrVFWD_Q, uncorrVFWD_I

    def close(self):
        """Close the socket"""
        self.navII.close()

class navigator:
    def __init__(self, comPort):
        self.nav = AE_Com_Module.AEB32(comPort,19200,1000,1)

    def unlockUnit(self):
        res = self.nav.create_buffer_cmd(67, '1:1', '1:1', 19)
        res = self.nav.create_buffer_cmd(68, '1:1', '1:1', 47)
        print('Unit Unlocked.')


    def reportCapLimits(self):
        res = self.nav.create_buffer_cmd(154, '0:0', '4:2')
        return res[2]


    def reportCapPositions(self):
        res = self.nav.create_buffer_cmd(180, '1:1', '2:2', 4)
        while len(res) < 3 or res[2] == 99:
            res = self.nav.create_buffer_cmd(180, '1:1', '2:2', 4)
        loadPos = res[2]
        seriPos = res[3]
        return loadPos, seriPos


    def reportCapRelatedData(self):
        values = self.nav.create_buffer_cmd(159, '0:0', '4:2')
        loadSteps = int(values[2])
        tuneSteps = int(values[3])
        loadOff = int(values[4])
        tuneOff = int(values[5])
        return  loadSteps, tuneSteps, loadOff, tuneOff


    def reportManufactureData(self, dataType):
        if dataType == 0: # Serial Number
            res = self.nav.create_buffer_cmd(231, '0:0', '2:4')
            while len(res) < 3:
                res = self.nav.create_buffer_cmd(231, '0:0', '2:4')
        if dataType == 1: # Unit 315 - Doesn't work
            res = self.nav.create_buffer_cmd(231, '1:1', 's:12', 1)
        return res[2]


    def moveLoadMotor(self, position):
        res = self.nav.create_buffer_cmd(112, '1:2', '0:0', int(position))
        while len(res) < 3 or res[2] == 99:
            res = self.nav.create_buffer_cmd(112, '1:2', '0:0', int(position))


    def moveTuneMotor(self, position):
        res = self.nav.create_buffer_cmd(122, '1:2', '0:0', int(position))
        while len(res) < 3 or res[2] == 99:
            res = self.nav.create_buffer_cmd(122, '1:2', '0:0', int(position))


    def reportTotalLoadPos(self):
        res = self.nav.create_buffer_cmd(159, '1:1', '2:2', 0)
        while len(res) < 3 or res[2] == 99:
            res = self.nav.create_buffer_cmd(159, '1:1', '2:2', 0)
        totPos = res[2]
        SHstepsOffHS = res[3]
        return totPos, SHstepsOffHS


    def reportTotalTunePos(self):
        res = self.nav.create_buffer_cmd(159, '1:2', '2:2', 1)
        while len(res) < 3 or res[2] == 99:
            res = self.nav.create_buffer_cmd(159, '1:2', '2:2', 1)
        totPos = res[2]
        SEstepsOffHS = res[3]
        return totPos, SEstepsOffHS


    def reportMotorMovement(self):
        movement = self.nav.create_buffer_cmd(135, '0:0', '1:1')
        while len(movement) < 3 or movement[2] == 99:
            movement = self.nav.create_buffer_cmd(135, '0:0', '1:1')
        while movement[2]:
            movement = self.nav.create_buffer_cmd(135, '0:0', '1:1')
            # print('Cap movement...')

    def close(self):
        """Close the socket"""
        self.nav.close()

#added class for pin match commands
class rfdspmat:
    def __init__(self, comport):
        self.rfdspmat = AE_Com_Module.AEB32(comport, 115200, 1000)
        # name = self.rfdspmat.create_buffer_cmd(128, '1:1', '1:4d')
        # return name

    def sockID(self):
        res = self.rfdspmat.create_buffer_cmd(128, '0:1', 's:s')
        return res[2:]

    def pinSelect(self, position):
        res = self.rfdspmat.create_buffer_cmd(86, '1:2', '0:0', position)
        return res

#### PULSE BOX Needs debug, not sure it will work#####
class Pulse_Box:
    def __init__(self,comport):
        self.Pulse_Box = AE_Com_Module.AEB32(comport,9600,1000)

    def pulse(self):
        res = self.Pulse_Box.create_buffer_cmd(0,'1:1','0:0','*V async')
        print('Response =',res[0])

    def close(self):
        """Close the socket"""
        self.nav.close()

class CSRError(Exception):
    csrCodes={
        2:'Output is on',
        4:'Data is out of range',
        7:'Faults are active',
        12:'Feature not available',
        99:'Command not implemented'}
    def __init__(self, csr):
        self.csr = csr

    def __str__(self):
        if self.csr in self.csrCodes.keys(): return self.csrCodes[self.csr]
        return 'CSR code %s' %(self.csr)

