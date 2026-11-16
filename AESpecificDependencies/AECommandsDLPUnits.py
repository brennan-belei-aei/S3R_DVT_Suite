"""
Created by:             Sterling Krone
Modified by:            Zach Mendez
Last Modified by:       April 10 2026
Revision of Python:     3.12
"""
import AE_Com_Module as AE_Com_Module
import numpy as np
import os
import csv
from time import *
from tkinter import *
import ctypes
import subprocess
import sys
NORM_FONT = ("Arial", 12)
BOLD_FONT = ("Arial", 12, "bold")


def popupmsg(msg):
    popup = Tk()
    popup.wm_title("!")
    Label(popup, text=msg, font=NORM_FONT).pack(side="top", fill="x", pady=10)
    Button(popup, text="Okay", command=popup.destroy).pack()
    popup.mainloop()


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


class generator:
    def __init__(self, ipAddress):
        self.ipAddress = ipAddress
        # self.device = aecom.AETCP()
        self.device = AE_Com_Module.AETCP()
        self.device.connect(ipAddress)

    def csr_cmd(self, cmd, send_sizes, *args):
        """ process command that only returns 1 byte csr """
        buf = self.device.create_buffer_cmd(cmd, send_sizes, '2:1',
                                            *args)  # buf returns [err, bytesreceived, val0, val1 ...]
        # it looks as if csr is not 0, then bytesreceieved is 0 ?!?!?
        if buf[0] != 0: raise RuntimeError("unexpected")  # ethernet fault, bad ack etc.
        if len(buf) >= 3 and buf[2] != 0: raise CSRError(buf[2])

    def rep_cmd(self, cmd, send_sizes, recv_sizes, *args):
        buf = self.device.create_buffer_cmd(cmd, send_sizes, recv_sizes, *args)
        if buf[0] != 0: raise RuntimeError("unexpected")  # ethernet fault, bad ack etc.
        if buf[1] == 0: raise CSRError(buf[2])  # CSR only
        return buf  # bytes start at buf[2]

    def __repr__(self):
        return 'parBEOL ' + self.ipAddress + ' serial ' + str(self.serialNumber())
    # def __init__(self, ipAddress):
    #     self.gen = AE_Com_Module.AETCP()
    #     self.gen.connect(ipAddress)
    #     self.device.create_buffer_cmd(14, '1:1', '0:1', 2)  # Host Mode
    #     self.device.create_buffer_cmd(3, '1:1', '0:1', 6)  # Forward Power Regulation
    #     self.device.create_buffer_cmd(48, '1:1', '0:1', 0)  # Set Fixed
    #     freq = self.device.create_buffer_cmd(147, '1:1', '1:4', 0)  # Report Generator Actual Frequency
    #     print("Connected to the : " + str(freq[2] / 1e3) + "MHz Generator")

    # def csr_cmd(self, cmd, send_sizes, *args):
    #     """ process command that only returns 1 byte csr """
    #     buf = self.device.create_buffer_cmd(cmd, send_sizes, '2:1', *args) #buf returns [err, bytesreceived, val0, val1 ...]
    #     #it looks as if csr is not 0, then bytesreceieved is 0 ?!?!?
    #     if buf[0] != 0: raise RuntimeError("unexpected") #ethernet fault, bad ack etc.
    #     if buf[2] != 0: raise CSRError(buf[2])
    #
    #
    # def rep_cmd(self, cmd, send_sizes, recv_sizes, *args):
    #     buf = self.device.create_buffer_cmd(cmd, send_sizes, recv_sizes, *args)
    #     if buf[0] != 0: raise RuntimeError("unexpected") #ethernet fault, bad ack etc.
    #     if buf[1]==0: raise CSRError(buf[2]) #CSR only
    #     return buf #bytes start at buf[2]


    def isLocked(self):
        temp = (self.device.create_buffer_cmd(207, '1:4', '1:2', 0))
        temp = int(temp[2])
        value = temp == 0xA729
        if value: return False
        return True


    def lock(self, lock_unlock):
        """This function locks or unlocks a device.  Parameter is
        lock_unlock which should be either 'u' for unlock or 'l' for lock"""
        pass_level = 0
        # Lock type lists
        no_md5 = ['7432030', '7432032', '7432035', '7434650', '7432047']
        md5 = ['7432011', '7434649', '7432030', '7432038', '7432048', '7434718', '7434773']
        subprocess.call([sys.executable, 'C:\Windows\SysWOW64\AeKey.dll'])
        # AE_key = ctypes.CDLL('C:\Windows\SysWOW64\AeKey.dll')
        AE_key = ctypes.LibraryLoader('C:\Windows\SysWOW64\AeKey.dll')
        #AE_key = ctypes.CDLL('C:\Matlab\Matlab_Basics\GetKeyEx.exe')
        fw_num_list = self.device.create_buffer_cmd(130, '1:1', '7:s', 0)
        # get the list number, first 2 numbers are aebus error code and bytes read
        fw_num = fw_num_list[2]
        if lock_unlock.lower() == 'u':
            if self.isLocked():
                # PDX II no MD5
                if any([pn in fw_num for pn in no_md5]):
                    self.device.create_buffer_cmd(67, '1:1', '1:1', 19)
                    self.device.create_buffer_cmd(68, '1:1', '1:1', 47)
                # PMNT-MF, PMNT 3K (MD5), PMNT 6K
                elif any([pn in fw_num for pn in md5]):
                    pass_level = 2
                # Any password level 4 (new) firmware
                else:
                    pass_level = 4
            if pass_level > 0:
                seed_buf = self.device.create_buffer_cmd(204, '1:1', '1:4', 20)
                seed = seed_buf[2]
                pv = (ctypes.c_ubyte * 16)()
                a = AE_key._GetKeyEx(seed, ctypes.pointer(pv), pass_level)
                if a != 0:
                    print('Error!')

                buf = self.device.create_buffer_cmd(67, '16:b', '1:1', pv)
                if buf[2] != 99:
                    print('Error!')
            if not self.isLocked():
                print('Unit is unlocked!')
                pass
            else:
                print('Unable to unlock generator')
        elif lock_unlock.lower() == 'l':
            self.device.create_buffer_cmd(68, '1:1', '1:1', 219)
            print('Unit is locked')
        else:
            print('Wrong character input!')


    def serialNumber(self):
        res = self.device.create_buffer_cmd(231, '1:1', '1:4', 0)  # Serial Number
        serialNo = str(res[2])
        return serialNo


    def RF_OFF(self):
        self.device.create_buffer_cmd(1, '0:0', '1:1')  # RF OFF


    def RF_ON(self):
        res = self.device.create_buffer_cmd(2, '0:0', '1:1')  # RF ON!
        while res[2] != 0:
            res = self.device.create_buffer_cmd(2, '0:0', '1:1')
        return res[2]


    def setRegulationMode(self,mode):
        # 1 = Sate 1 | 2 = State 0
        self.device.create_buffer_cmd(3, '2:1', '0:0', 1, mode)
        if mode == 6:
            print('Forward Mode Set')
        if mode == 7:
            print('Delivered Mode Set')

    def setPulseMode(self,mode):
        # 0 = Disabled, 1 = Pulse Enable, 3 = DLP enable
        self.device.create_buffer_cmd(26, '3:2', '0:0', 8, mode, 0)
        if mode == 3:
            print('DLP Enabled')

    def pulseEnable(self, onoff):
        #0 = Off, 1 = ON
        self.device.create_buffer_cmd(26, '3:2', '0:0', 7, onoff, 0)
        if onoff == 1:
            print('Pulsing On')

    def power_Setpoint(self, setpoint):
        self.device.create_buffer_cmd(8, '1:2', '0:1', int(setpoint))  # Set Power Setpoint

    def DLP_setpoint(self, s1_setpoint, s0_setpoint):
        self.csr_cmd(8, '1:1,1:4', 2, s1_setpoint)
        self.csr_cmd(8, '1:1,1:4', 3, s0_setpoint)

    def setGenFreq(self, freq):
        res = self.device.create_buffer_cmd(61, '1:4', '0:0', int(freq))

    def setDLPFreq_s1(self, freq):
        res = self.device.create_buffer_cmd(61, '1:1, 1:4', '0:0', 1, int(freq))

    def setDLPFreq_s0(self, freq):
        res = self.device.create_buffer_cmd(61, '1:1, 1:4', '0:0', 2, int(freq))

    def setDLPfreqMode(self, s1_mode, s0_mode):
        self.csr_cmd(48, '1:1, 1:1', 1, s1_mode)
        self.csr_cmd(48, '1:1, 1:1', 2, s0_mode)

    def setPulseFreq_DutyCycle(self, pf, dc):
        self.csr_cmd(93, '1:4', pf)
        self.csr_cmd(96, '1:2', dc)

    def OpenLoop(self, Mode=0):
        if Mode == 0:
            print('Open Loop Mode')
        if Mode == 1:
            print('Closed Loop Mode')
        self.device.create_buffer_cmd(92, '1:1', '1:1', Mode)  # 0: Open Loop | 1: Closed Loop


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
        self.device.create_buffer_cmd(80, '2:1,1:2', '0:0', 3, 1, val)


    def setBuckDAC(self, val):
        self.device.create_buffer_cmd(80, '2:1,1:2', '0:0', 4, 0, val)


    def readVDC(self):
        voltage = self.device.create_buffer_cmd(207, '2:2', '1:2', 66, 0)
        return voltage[2] / 10.0


    def readIDC(self):
        current = self.device.create_buffer_cmd(207, '2:2', '1:2', 67, 0)
        return current[2] / 10.0


    def readPDC(self):
        power = self.device.create_buffer_cmd(207, '2:2', '1:2', 68, 0)
        return power[2]


    def readDC(self):
        res = self.device.create_buffer_cmd(207, '2:2', '1:2', 66, 0)
        voltage = res[2] / 10.0
        res = self.device.create_buffer_cmd(207, '2:2', '1:2', 67, 0)
        current = res[2] / 10.0
        power = float(voltage) * float(current)
        return voltage, current, power


    def reportDACState(self):
        res = self.device.create_buffer_cmd(174, '2:1', '1:2', 3, 1)
        RFdriveDAC = res[2]
        res = self.device.create_buffer_cmd(174, '2:1', '1:2', 4, 0)
        DCdriveDAC = res[2]
        return RFdriveDAC, DCdriveDAC


    def readcalCoeff(self):
        Destination = 1 # Destination (0 = Ram or 1 = Ram and EEPROM)
        values = self.device.create_buffer_cmd(208,'2:1,1:2','8:2d', Destination, 0, 3)
        return values


    def writecalCoeff(self):
        res = self.device.create_buffer_cmd(98,'2:1,1:2,8:2d','0:0',1,0,4,1,2,3,4,5,6,7,7)
        return res


    def reportDissipation(self):
        res = self.device.create_buffer_cmd(173, '0:0', '1:2')
        return res[2:]


    def reportRFcontrolADC(self):
        """
        restricted command, returns:
        d0                            RF Control ADC  10V
        d1                            RF Control ADC  Temp 1
        d2                            RF Control ADC  Temp 2
        d3                            RF Control ADC  Temp 3
        d4                            RF Control ADC  Temp 4
        d5                            RF Control ADC  Temp 5
        d6                            RF Control ADC  Temp 6
        d7                            RF Control ADC  Temp 7
        d8                            RF Control ADC  Temp 8
        d9                            RF Control ADC  Snubber Voltage
        d10                          RF Control ADC Snubber Current 16x
        d11                          RF Control ADC  Snubber Current
        """
        return [int(x) for x in self._call('181 bBBBBBBBBBBBB 4').split(',')]

    def readDMA(self, selection, offset):
        # def readDMA(self):
        """    0x60010000 for Logic FPGA BCLK Section
          0x60030000 for Logic FPGA ADC Section
          0x60040000 for Logic FPGA DAC Section
          0x60060000 for Logic FPGA RFCLK Section
          0x60070000 for Logic FPGA DDS  Section
          0x60020000 = User Card PLD
          0x60058000 = Com Slot PLD
          0x60060000 = Logic PLD
          0xF0000000 = CPU Module FPGA
                            """
        DACvalue = self.device.create_buffer_cmd(207, '4:2,2:4', '1:4', 5, 0, 0, 0, selection, offset)
        return DACvalue[2:]


    def genSnapShot(self, Opt):
        if Opt == 1:
            res = self.device.create_buffer_cmd(222, '1:1', '4:2,2:4d,1:4,10:2,2:4d,1:4,25:2,1:4', 1)
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
            # res = self.device.create_buffer_cmd(222, '1:1', '4:2,3:4d,6:2,2:4d,1:2,1:4,2:2,3:1,13:2,8:1,2:2,4:2,4:4,4:f', 2)
            # res = self.device.create_buffer_cmd(222, '1:1', '4:2,3:4d,6:2,3:4d,1:2,1:4,2:2,4:1,12:2,8:1,2:2,1:2,2:1,3:2,4:4,4:f', 2)
            # res = self.device.create_buffer_cmd(222, '1:1', '4:2,2:4d,1:4,1:2,1:2d,4:2,2:4d,1:4,1:2,1:4,1:2d,1:2,4:1,12:2,8:1,7:2,4:4d,4:f', 2)
            res = self.device.create_buffer_cmd(222, '1:1', '4:2,2:4d,1:4,6:2,2:4d,1:4,1:2,1:4,2:2,4:1,12:2,8:1,2:2,5:2,4:4d,4:f', 2)
        return res[2:]


    def writePAreg(self, baseAddr, offset, value):
        """ locked command, PA index is physical (0-19), not customer logical"""
        # res = self.device.create_buffer_cmd(87, '2:2,3:4', 2, 0, baseAddr, offset, value)
        res = self.device.create_buffer_cmd(87,'4:2','2:2',9,baseAddr,offset,value)
        return res

    def readPAreg(self, baseAddr, offset):
        """ locked command, PA index is physical (0-19), not customer logical
            returns value """
        res = self.rep_cmd(207, '2:2,3:4', '1:4', 5, 0, baseAddr, offset)[2] & 0xFFFF
        # res = self.device.create_buffer_cmd(207,'3:2','2:2',9,baseAddr,offset)
        return res[2]



    # def reportMeasUncorrData(self, ADCnum=1):
    #     res = self.device.create_buffer_cmd(182, '2:1', '4:4d', 0, ADCnum)
    #     Reflected_Q = res[2]
    #     Reflected_I = res[3]
    #     Forward_Q = res[4]
    #     Forward_I = res[5]
    #     return Reflected_Q, Reflected_I, Forward_Q, Forward_I

    def reportMeasUncorrData(self):
        res = self.device.create_buffer_cmd(182, '1:1', '4:4d', 0)
        Reflected_Q = res[2]
        Reflected_I = res[3]
        Forward_Q = res[4]
        Forward_I = res[5]
        return Reflected_Q, Reflected_I, Forward_Q, Forward_I


    def reportMeasCorrFactors(self,Source,Mc,PINPos=0):
        # d0 = Source of correction factor data (0 = Ram, 1 = EEPROM)
        # d1 = Matrix Select (Center = 0, A = 1,B = 2)
        # d3 = PIN Select (0 - 15)
        PINMatch = False
        if PINMatch:
            res = self.device.create_buffer_cmd(208, '2:1,1:2', '8:2d', Source, Mc, PINPos)
        else:
            res = self.device.create_buffer_cmd(208, '2:1', '8:2d', Source, Mc)
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
        res = self.device.create_buffer_cmd(248, '1:2', '1:4,5:4d,3:f', 176)
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
        res = self.device.create_buffer_cmd(120, '2:2', '1:1', 93, mode)


    def setPINDiodePositions(self,Pos):
        # bit    0 = Shunt
        # bit    1 = Shunt
        # bit    2 = Shunt
        # bit    3 = Series
        Pos = int(Pos, 2)
        res = self.device.create_buffer_cmd(120, '1:2,2:1', '1:1',86, Pos,0)


    def reportMatchZc(self):
        res = self.device.create_buffer_cmd(240, '2:2', '32:f', 235, 0)
        # res = self.device.create_buffer_cmd(240, '2:2', '32:f', 235, 0)
        return res[2:]


    def reportMatchSParameters(self):
        S11 = np.zeros([16,31],dtype=complex)
        S12 = np.zeros([16,31],dtype=complex)
        S21 = np.zeros([16,31],dtype=complex)
        S22 = np.zeros([16,31],dtype=complex)
        for pIdx in range(65): # S11 1-16 | S12 17-32 | S21 33-48 | S22 49-64
            if pIdx == 0:
                continue
            res = self.device.create_buffer_cmd(240, '2:2', '62:f', 235, pIdx)
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
        res = self.device.create_buffer_cmd(240, '1:2', '2:1', 226)
        PinPos = res[2]
        return PinPos


    def report_Measurement_Correction_Factors_RFDS(self, PINselect):
        source = 1 # 0 = Ram, 1 = EEPROM
        matrix = 0 # 0 = Center Frequency Matrix
        res = self.device.create_buffer_cmd(208, '2:1,1:2', '8:2d', source, matrix, PINselect)
        return res[2:]

    def report_Measurement_Correction_Factors(self, matrix):
        source = 1 # 0 = Ram, 1 = EEPROM
        res = self.device.create_buffer_cmd(208, '2:1', '8:2d', source, matrix)
        return res[2:]


    def set_Measurement_Correction_Factors(self, PINselect, Mvalues):
        destination = 1 # 0 = RAM, 1 = RAM & EEPROM
        matrix = 0      # 0 = Center Frequency Matrix
        res = self.device.create_buffer_cmd(98, '2:1,1:2,8:2d', '8:2d', destination, matrix, PINselect, int(Mvalues[0]), \
                                         int(Mvalues[1]), int(Mvalues[2]), int(Mvalues[3]), int(Mvalues[4]), int(Mvalues[5]), int(Mvalues[6]), \
                                         int(Mvalues[7]))
        return res[2:]


    def set_Measurement_Correction_Factors_RFDS(self, PINselect, Mvalues):
        destination = 1 # 0 = RAM, 1 = RAM & EEPROM
        matrix = 0      # 0 = Center Frequency Matrix
        res = self.device.create_buffer_cmd(98, '2:1,1:2,8:2d', '8:2d', destination, matrix, PINselect, int(Mvalues[0]), \
                                         int(Mvalues[1]), int(Mvalues[2]), int(Mvalues[3]), int(Mvalues[4]), int(Mvalues[5]), int(Mvalues[6]), \
                                         int(Mvalues[7]))
        return res[2:]


    def ADCbufferData(self):
        # now = strftime('%Y_%m_%d_%H_%M',localtime())
        # outPathadc = r'P:\Paramount\Cumulus\data\RFDS27\CalibrationData\adclogData'
        # outFname = os.path.join(outPathadc, 'RFDS_adclog_data_{}.csv'.format(now))
        # fhl = open(outFname, 'w', newline='')
        # write_r = csv.writer(fhl, 'excel')
        # header = ('time (us)', 'voltage', 'current')
        # write_r.writerow(header)
        BASE_ADDR = 0x60030000
        CTRL_REG_ADDR = 0x14
        DATA1_ADDR = 0x18
        DATA2_ADDR = 0x1C
        ADC_CLOCK = 63000000.0
        # SAMPLES = 128
        SAMPLES = 256
        dT = 1.0 / ADC_CLOCK
        WINDOW = SAMPLES * dT
        adc_value1 = np.empty([SAMPLES])
        adc_value2 = np.empty([SAMPLES])
        res = self.device.create_buffer_cmd(87, '4:2, 3:4', '1:1', 2, 0, 0, 0, BASE_ADDR, CTRL_REG_ADDR, 1)
        i = 0
        # set coef reg
        for x in range(SAMPLES):
            recv = self.device.create_buffer_cmd(207, '4:2, 2:4', '1:4', 5, 0, 0, 0, BASE_ADDR, DATA1_ADDR)
            adc_value1[i] = recv[2]
            if adc_value1[i] >= 8192:
                adc_value1[i] = adc_value1[i] - 16383
            recv = self.device.create_buffer_cmd(207, '4:2, 2:4', '1:4', 5, 0, 0, 0, BASE_ADDR, DATA2_ADDR)
            adc_value2[i] = recv[2]
            if adc_value2[i] >= 8192:
                adc_value2[i] = adc_value2[i] - 16383
            i = i + 1
        # clear log freeze bit
        recv = self.device.create_buffer_cmd(87, '4:2,3:4', '1:1', 2, 0, 0, 0, BASE_ADDR, CTRL_REG_ADDR, 0)
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
        res = self.device.create_buffer_cmd(228,'1:1','1:1d', 1)
        waterIn = float(res[2]) / 10.0
        res = self.device.create_buffer_cmd(228,'1:1','1:1d', 2)
        waterOut = float(res[2]) / 10.0
        # res = self.device.create_buffer_cmd(228, '1:1', '1:1d', 3)
        # temp0 = float(res[2]) / 10.0
        res = self.device.create_buffer_cmd(228, '1:1', '2:1', 5)
        B1 = res[3] * 256
        B2 = res[2]
        five = (B1 + B2) / 10
        res = self.device.create_buffer_cmd(228, '1:1', '2:1', 6)
        B1 = res[3] * 256
        B2 = res[2]
        six = (B1 + B2) / 10

        return waterIn, waterOut, five, six

    def reportFaultorWarning(self):
        res = self.device.create_buffer_cmd(223, '1:1','2:2', 3)
        faultCodeOne = res[2]
        faultCodeTwo = res[3]
        return faultCodeOne, faultCodeTwo

    def reportFixedFreqBEOL(self):
        res = self.device.create_buffer_cmd(161, '3:2', '1:4', 0, 0, 4)
        return res[2]

    def close(self):
        """Close the socket"""
        self.gen.close()


class flackpack:
    def __init__(self, ipAddress):
        if ipAddress[0] == 'C':
            self.navII = AE_Com_Module.AEB32(ipAddress, baud=115200, timeout=1000, aebus_addr=1)
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
        res = self.navII.create_buffer_cmd(147, '1:2', '1:4', 0)
        while res[2:][0] == 0:
            res = self.navII.create_buffer_cmd(147, '1:2', '1:4', 0)
        return res[2:][0]


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


    def readKp(self):
        values = self.navII.create_buffer_cmd(207, '2:2', '2:f', 101, 0)
        Kp = values[2]
        return Kp


    def readKpf(self, p_index, fundFreq):
        p_index = 0
        res = self.navII.create_buffer_cmd(207, '1:2,1:2d,1:2', '1:f', 115, p_index, fundFreq)
        return res[2:][0]


    def reportCurrentCalibrationCoeff(self):
        values = self.navII.create_buffer_cmd(207, '2:2', '16:f', 111, 2)
        return values[2:]


    def reportSpectrumFrequencyData(self):
        values = self.navII.create_buffer_cmd(248, '2:2', '16:f', 206, 0)
        return values[2:]


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


class pinmatch:
    def __init__(self, comPort):
        self.PIN = AE_Com_Module.AEB32(comPort, baud=115200, timeout=1500)


    def identify(self):
        res = self.PIN.create_buffer_cmd(128, '0:1', '7:s')
        return res[2:]


    def pinSelect(self, position):
        res = self.PIN.create_buffer_cmd(86, '1:2', '0:0', position)
        if res[2] != 0:
            print('PIN did change!')


    def close(self):
        """Close the socket"""
        self.PIN.close()