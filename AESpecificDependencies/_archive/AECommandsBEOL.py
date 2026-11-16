"""
Created by:             Sterling Krone
Last Modified by:       February 15 2021
Revision of Python:     3.7
"""
import AE_Com_Module
import numpy as np
import os
import csv
from time import *
#from ctypes import *
import ctypes

class AEBUSError(Exception):
    faults={-2:"COM is not available",
        -5:"COM is busy",
        -3:"aebus timeout",
        -4:"chksum",
        -6:"bad ack",
        -7:"wrong port",
        -8:"read error",
        -9:"no ack"}

    def __init__(self, retVal, ret):
        if retVal > 2**15: retVal = retVal-2**16
        self.errCode = retVal
        self.ret = ret

    def __str__(self):
        if self.faults.has_key(self.errCode): msg = self.faults[self.errCode]
        else: msg = 'aebus dll retVal=%d' %self.errCode
        return "unable to communicate with paramount : " + msg

class generator:
    def __init__(self, ipAddress):
        self.gen = AE_Com_Module.AETCP()
        self.gen.connect(ipAddress)
        self.gen.create_buffer_cmd(14, '1:1', '0:1', 2)  # Host Mode
        self.gen.create_buffer_cmd(3, '3:2', '0:1', 0, 0, 6)  # Forward Power Regulation
        self.gen.create_buffer_cmd(48, '3:2', '0:1', 0, 0, 0 )  # Set Fixed
        freq = self.gen.create_buffer_cmd(147, '1:4', '1:4', 0)  # Report Generator Actual Frequency
        print("Connected to the : " + str(freq[2] / 1e3) + "MHz Generator")


    def serialNumber(self):
        res = self.gen.create_buffer_cmd(231, '1:1', '1:4', 0)  # Serial Number
        serialNo = str(res[2])
        return serialNo

    def readPinNumber(self):
        """ returns PIN number as integer """
        PIN = self.gen.create_buffer_cmd(221, '0:0', 's:s' )  # Report Generator PIN number
        return PIN

    def readFreq(self):
        rfreq = self.gen.create_buffer_cmd(147, '1:4', '1:4', 0)  # Report Generator Actual Frequency
        return rfreq

    def setPulsingMode(self, dutyCyclePercent, pulseFreq, syncDelay1us, setpoint1W, state1regMode, state1Ramptimems,
                       state1AutoFreqTuning, state1freq, state1startFreq, syncDelay0us, setpoint0W, state0regMode,
                       state0Ramptimems, state0AutoFreqTuning, state0freq, state0startFreq, state0maxOnTime):
        """
         regulation modes are
        6 : Forward
        7: Delivered or Real
        8: External (DC Bias),NA for dual level pulsing
        9: VA Limit

        state0/1AutoFreqTuning = <True, False>
        """
        state1freqkHz = int(round(state1freq / 1e3))
        state0freqkHz = int(round(state0freq / 1e3))
        state1startFreqkHz = int(round(state1startFreq / 1e3))
        state0startFreqkHz = int(round(state0startFreq / 1e3))

        self._setCmd(118, '4:2,3:4,4:2,4:4,4:2,3:4', 54, 2, 1, (int(round(dutyCyclePercent)), int(round(pulseFreq)),
                        syncDelay1us, setpoint1W, state1regMode, state1Ramptimems,
                        state1AutoFreqTuning, state1freqkHz, state1startFreqkHz,
                        syncDelay0us, setpoint0W, state0regMode, state0Ramptimems,
                        state0AutoFreqTuning, state0freqkHz, state0startFreqkHz, state0maxOnTime))

    def enablePulsing(self):
        self.gen.create_buffer_cmd(26, '3:2',  7, 1, 0)

    def disablePulsing(self):
        self.gen.create_buffer_cmd(26, '3:2',  7, 0, 0)

    def RF_OFF(self):
        self.gen.create_buffer_cmd(1, '0:0', '1:1')  # RF OFF

    def PulsingState(self,mode):
        self.gen.create_buffer_cmd(118, '4:2', '4:1', 149, 0, 0, mode, 0)

    def RF_ON(self):
        res = self.gen.create_buffer_cmd(2, '0:0', '1:1')  # RF ON!
        return res[2]

    def set_Measurement_Correction_Factors(self, PINselect, Mvalues):
        destination = 1  # 0 = RAM, 1 = RAM & EEPROM
        matrix = 0  # 0 = Center Frequency Matrix
        res = self.gen.create_buffer_cmd(98, '2:1,1:2,8:2d', '8:2d', destination, matrix, PINselect, int(Mvalues[0]), \
                                         int(Mvalues[1]), int(Mvalues[2]), int(Mvalues[3]), int(Mvalues[4]),
                                         int(Mvalues[5]), int(Mvalues[6]), \
                                         int(Mvalues[7]))
        return res[2:]

    def setRegulationMode(self,mode):
        self.gen.create_buffer_cmd(3, '3:2', '0:1', 0, 0,  mode)
        if mode == 7:
            print('Output Power Regulation Mode Set')

    def power_Setpoint(self, setpoint, SetpMode, HaloMode):
        #self.gen.create_buffer_cmd(8, '4:2', '0:1', SetpMode, 0, int(setpoint * 10), 0)  # Set Power Setpoint
        self.gen.create_buffer_cmd(8, '4:2', '0:1', SetpMode, 0, int(setpoint * (HaloMode)), 0)  # Set Power Setpoint

    # def readFreq(self):
    #     rfreq = self.gen.create_buffer_cmd(147, '0:0', '1:4', 0)  # Report Generator Actual Frequency
    #     return rfreq

    # def setGenFreq(self, freq):
    #     res = self.gen.create_buffer_cmd(61, '4:2', '1:1', 0, 0, int(freq), 0)

    def setGenFreq(self, freq, freqMode):
        res = self.gen.create_buffer_cmd(61, '4:2', '1:1', freqMode, 0, int(freq), 0)

    def setFixedFrequencyMode(self,freqsetmode):
        self.gen.create_buffer_cmd(48, '3:2', '0:0', 0, 0, freqsetmode)

    def readFrequencyMode(self,mode):
        self.gen.create_buffer_cmd(162, '0:0', '2:1', 4, )

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
        res = self.gen.create_buffer_cmd(173, '0:0', '1:2', )
        dissipation = res[2] #/ 10.0
        power = float(voltage) * float(current)
        return voltage, current, power, dissipation

    # def reportDriveDAC():
    #     return gen.readDMA(1610874880, 16)

    #    return gen.reportDAC(3, 1)

    def reportDACState(self):
        res = self.gen.create_buffer_cmd(174, '2:1', '1:2', 3, 1)
        RFdriveDAC = res[2]
        res = self.gen.create_buffer_cmd(174, '2:1', '1:2', 4, 0)
        DCdriveDAC = res[2]
        return RFdriveDAC, DCdriveDAC

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
        return [int(x) for x in self.gen.create_buffer_cmd(181, '1:1d , 12:1', 4)]
        #return [int(x) for x in self.gen.create_buffer_cmd(181, '0:0', '1:1d , 12:1',  4 ).split(',')]

    def reportSnubberDissipationW(self):
        """ only works for 2mhz """
        return int(self.gen.create_buffer_cmd(207, '0:0', '3:2', 64, 0));

    def readcalCoeff(self):
        Destination = 1 # Destination (0 = Ram or 1 = Ram and EEPROM)
        values = self.gen.create_buffer_cmd(208,'2:1,1:2','8:2d', Destination, 0, 3)
        return values


    def writecalCoeff(self):
        res = self.gen.create_buffer_cmd(98,'2:1,1:2,8:2d','0:0',1,0,4,1,2,3,4,5,6,7,7)
        return res


    def genSnapShot(self,Opt):
        if Opt == 1:
            #res = self.gen.create_buffer_cmd(222, '1:1', '4:2,2:4d,1:4,10:2,2:4d,1:4,25:2,1:4', 1)
            res = self.gen.create_buffer_cmd(222, '3:2', '2:2, 4:4, 2:4d, 2:4, 2:1, 1:2, 1:4, 4:2', 0, 0, 0)
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
            res = self.gen.create_buffer_cmd(222, '1:1', '0:0', '4:2,2:4d,1:4,1:2,1:2d,4:2,2:4d,1:4,1:2,1:4,1:2d,1:2,4:1,12:2,8:1,7:2,4:4d,4:f', 2)

        return res[2:]

    # def _reportLockedCmd(self, cmd, cmdStr, expReceive):

    def writeDMA(self, baseAddr, offset, value):
        """ bclkbase = 0x60010000 """
        self.gen.create_buffer_cmd(87, '2:2, 4:4', 2, 0, 0, baseAddr, offset, value)

    def writeDCdma(self, selection, offset, data):
        """ selection: 0=main, 1 buck, 2 rf contol)
            offset and data are 16bit values """
        self.gen.create_buffer_cmd(87, '4:2', 39, selection, offset, data)

    def readDCdma(self, selection, offset):
        """ selection: 0=main, 1 buck, 2 rf control
            returns 16 bit int value """
        return int(self.gen.create_buffer_cmd(207, '4:2', 39, selection, offset))

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
        RFDACvalue=self.gen.create_buffer_cmd(207, '4:2,2:4', '1:4', 5, 0, 0, 0, selection, offset)
        return RFDACvalue[2:]
        #return self.gen.create_buffer_cmd(207, '4:2,2:4', '1:4', 5, 0, 0, 0, selection, offset)

    def reset(self):
        """ command 66, reset uProc, unit needs to be unlocked """
        ret = self.gen.create_buffer_cmd(66, '1:1','0:0', 170)
        if ret[2]: raise AEBUSError(66, ret[2])

    def isLocked(self):
        temp = self.gen.create_buffer_cmd(207, '1:4', '1:2', 0)
        temp = int(temp[2])
        if temp == 0xA729: return False
        return True

    def lock(self, lock_unlock):
        """This function locks or unlocks a device.  Parameter is
        lock_unlock which should be either 'u' for unlock or 'l' for lock"""
        pass_level = 0
        # Lock type lists
        no_md5 = ['7432030', '7432032', '7432035', '7434650', '7432047']
        md5 = ['7432011', '7434649', '7432030', '7432038', '7432048', '7434718', '7434773']

        AE_key = ctypes.LibraryLoader('C:\Windows\SysWOW64\AeKey.dll')
        #AE_key = ctypes.CDLL('C:\Matlab\Matlab_Basics\GetKeyEx.exe')
        fw_num_list = self.gen.create_buffer_cmd(130, '1:1', '7:s', 0)
        # get the list number, first 2 numbers are aebus error code and bytes read
        fw_num = fw_num_list[2]
        if lock_unlock.lower() == 'u':
            #if self.is_locked():
            if self.isLocked():
                # PDX II no MD5
                if any([pn in fw_num for pn in no_md5]):
                    self.gen.create_buffer_cmd(67, '1:1', '1:1', 19)
                    self.gen.create_buffer_cmd(68, '1:1', '1:1', 47)
                # PMNT-MF, PMNT 3K (MD5), PMNT 6K
                elif any([pn in fw_num for pn in md5]):
                    pass_level = 2
                # Any password level 4 (new) firmware
                else:
                    pass_level = 4
            if pass_level > 0:
                seed_buf = self.gen.create_buffer_cmd(204, '1:1', '1:4', 20)
                seed = seed_buf[2]
                pv = (ctypes.c_ubyte * 16)()
                a = AE_key._GetKeyEx(seed, ctypes.pointer(pv), pass_level)
                if a != 0:
                    print('Error!')

                buf = self.gen.create_buffer_cmd(67, '16:b', '1:1', pv)
                if buf[2] != 99:
                    print('Error!')
            if not self.isLocked():
                #                print('Unit is unlocked!')
                pass
            else:
                print('Unable to unlock generator')
        elif lock_unlock.lower() == 'l':
            self.gen.create_buffer_cmd(68, '1:1', '1:1', 219)
            print('Unit is locked')
        else:
            print('Wrong character input!')

    # def unlock(self, level=4):
    #     """ md5 unlocking. command 67 and 204 sub 20 """
    #     if not self.isLocked(): return
    #     hashSeed = self.getHashSeed()
    #     md5 = hashlib.md5()
    #     sh = range(0, 32, 8)
    #     sh.reverse() #hashseed is processed msb first
    #     for i in sh: md5.update(chr((hashSeed >> i) & 0xFF))
    #     if level==4: md5.update('gt5>Zx#9QsR3dA*P8$wUk!F]')
    #     else: md5.update('123!@#qwe')
    #     digStr = ''
    #     for x in md5.digest(): digStr += ' %d' %ord(x)
    #     if int(self._call(67, ' 16:1', '0:0', + digStr)) != 99:
    #         raise 'unexpected CSR'
    #     if self.isLocked(): raise 'unable to unlock unit'


    def resetAndWait(self, timeoutS=14):
        """ reset and wait for unit to come back online
        unit needs to be unlocked """
        self.reset()
        #from time import *
        tic = time()
        while 1:
            try:
                self.serialNumber()
                break
            except(AEBUSError):
                pass
            if time() - tic > timeoutS: raise 'timeout'

    def report_Measurement_Correction_Factors(self,matrix):
        source = 1  # 0 = Ram, 1 = EEPROM
        #matrix = 0  # 0 = Center Frequency Matrix
        res = self.gen.create_buffer_cmd(208, '2:1', '8:2d', source, matrix)
        return res[2:]

    def reportMeasUncorrData(self):
        res = self.gen.create_buffer_cmd(182, '1:1', '4:4d', 0)
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

    def set_Measurement_Correction_Factors(self, matrix, Mvalues):
        destination = 1  # 0 = RAM, 1 = RAM & EEPROM
        #matrix = 0  # 0 = Center Frequency Matrix
        res = self.gen.create_buffer_cmd(98, '2:1,8:2d', '0:0', destination, matrix, int(Mvalues[0]), \
                                         int(Mvalues[1]), int(Mvalues[2]), int(Mvalues[3]), int(Mvalues[4]),
                                         int(Mvalues[5]), int(Mvalues[6]), \
                                         int(Mvalues[7]))
        return res[2:]

    def setMeasCorrFactor(self, destination, matrixSelect, values):
        """ values and destination as in FW383
    d1 = 0 selects Measurement Board Center Frequency Matrix
    d1 = 1 selects Measurement Board Correction Matrix A
    d1 = 2 selects Measurement Board Correction Matrix B
    """
        if len(values) != 8: raise 'invalid number of values'
        #valStr = str(values).strip('[').strip(']').replace(',', '')
        #self.gen.create_buffer_cmd(98, 'BBiiiiiiii %d %d %s' %(destination, matrixSelect, valStr))
        res = self.gen.create_buffer_cmd(98, '2:1,8:2d', '0:0', destination, matrixSelect, \
        int(values[0]), int(values[1]), int(values[2]), int(values[3]), int(values[4]),int(values[5]), int(values[6]), int(values[7]))

    def setHostMode(self,mode):
        self.gen.create_buffer_cmd(14, '1:1','0:0', mode)

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


    def reportPINpostion(self):
        res = self.gen.create_buffer_cmd(240, '1:2', '2:1', 226)
        PinPos = res[2]
        return PinPos


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
        res = self.gen.create_buffer_cmd(228,'1:1','1:1d', 1)
        waterIn = float(res[2]) / 10.0
        res = self.gen.create_buffer_cmd(228,'1:1','1:1d', 2)
        waterOut = float(res[2]) / 10.0
        return waterIn, waterOut

    def reportTemperature(self, tempIndex):
        """ reports following temperatures (d0 is tempIndex)
        for 27/40/60MHz units:
         0 Reports Cold Plate Temperature (tenths of degrees C)
         1 Reports Water In Thermistor Temperature (tenths of degrees C)
         2 Reports Water Out Thermistor Temperature (tenths of degrees C)
         7 Reports Boost T1 Thermistor Temperature (tenths of degrees C)
         8 Reports Boost T2 Thermistor Temperature (tenths of degrees C)
         9 Reports Boost T3 Thermistor Temperature (tenths of degrees C)
         10 Reports Buck T1 Thermistor Temperature (tenths of degrees C)
         11 Reports Buck T2 Thermistor Temperature (tenths of degrees C)

         for 2MHz 15kW:
         0 Reports Cold Plate Temperature (tenths of degrees C)
         1 Reports Water In Thermistor Temperature (tenths of degrees C)
         2 Reports Water Out Thermistor Temperature (tenths of degrees C)
         7 Reports Boost T1 Thermistor Temperature (tenths of degrees C)
         8 Reports Boost T2 Thermistor Temperature (tenths of degrees C)
         9 Reports Boost T3 Thermistor Temperature (tenths of degrees C)
         10 Reports Boost T4 Thermistor Temperature (tenths of degrees C)
         11 Reports Boost T5 Thermistor Temperature (tenths of degrees C)
         12 Reports Boost T6 Thermistor Temperature (tenths of degrees C)
         13 Reports Half Bridge T7 Thermistor Temperature (tenths of degrees C)
         14 Reports Half Bridge T8 Thermistor Temperature (tenths of degrees C)
         15 Reports Half Bridge T9 Thermistor Temperature (tenths of degrees C)
         16 Reports Buck T1 Thermistor Temperature (tenths of degrees C)
         17 Reports Buck T2 Thermistor Temperature (tenths of degrees C)
         18 Reports Buck T3 Thermistor Temperature (tenths of degrees C)
         19 Reports Buck T4 Thermistor Temperature (tenths of degrees C)
         20 Reports Buck T5 Thermistor Temperature (tenths of degrees C)
         21 Reports Buck T6 Thermistor Temperature (tenths of degrees C)
         22 Reports RF Control T1 Thermistor Temperature (tenths of degrees C)
         23 Reports RF Control T2 Thermistor Temperature (tenths of degrees C)
         24 Reports RF Control T3 Thermistor Temperature (tenths of degrees C)
         25 Reports RF Control T4 Thermistor Temperature (tenths of degrees C)
         26 Reports RF Control T5 Thermistor Temperature (tenths of degrees C)
         27 Reports RF Control T6 Thermistor Temperature (tenths of degrees C)
        """
        #return int( self.gen.create_buffer_cmd(228, '1:1', '2:1', '%d' %tempIndex))/10.0
        #return int(self.gen.create_buffer_cmd(228, '1:1', '2:1', '%tempIndex'))# / 10.0
        res = self.gen.create_buffer_cmd(228, '1:1d', '1:4', tempIndex)
        return int(res[2]) / 10.0 # temperature


    def reportFaultorWarning(self):
        res = self.gen.create_buffer_cmd(223, '1:1','2:2', 3)
        faultCodeOne = res[2]
        faultCodeTwo = res[3]
        return faultCodeOne, faultCodeTwo


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

    # def setFixedFrequencyMode(self):
    #     self.gen.create_buffer_cmd(48, '1:1', '0:0', 0)

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

#===================================Nav Movement=================================
    def moveLoadMotor(self, position):
        res = self.nav.create_buffer_cmd(112, '1:2', '0:0', (position))
        while len(res) < 3:# or res[2] == 99:
            res = self.nav.create_buffer_cmd(112, '1:2', '0:0', (position))

    def moveTuneMotor(self, position):
        res = self.nav.create_buffer_cmd(122, '1:2', '0:0', (position))
        while len(res) < 3:# or res[2] == 99:
            res = self.nav.create_buffer_cmd(122, '1:2', '0:0', (position))

    def reportMotorMovement(self):
        res = self.nav.create_buffer_cmd(135, '0:0', '1:1')
        while len(res) < 3 or res[2] == 99:
            res = self.nav.create_buffer_cmd(135, '0:0', '1:1')
        while res[2]:
            res = self.nav.create_buffer_cmd(135, '0:0', '1:1')
            # print('Cap movement...')

#===============================================================================
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

    def close(self):
        """Close the socket"""
        self.nav.close()