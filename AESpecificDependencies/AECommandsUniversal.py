"""
Created by:             Christopher McLean
Last Modified by:       Jacob O'Burke (JACOB.OBURKE@aei.com)
Data Last Modified:     November 21, 2024
Revision of Python:     3.12

Known to work with Firmwares: 7433660J00

To be determined with 7434759A31

Intended to be used for communicating with PMNT HF Units via Ethernet

Dependencies: AE_Com_Module
"""
import AE_Com_Module
import hashlib
from fixedpoint import FixedPoint
from datetime import datetime
import struct
import ctypes

#HELPER FUNCTIONS --------------------------------------------------------------------
def twos_comp(val, bits):
    """compute the 2's complement of int value val"""
    if (val & (1 << (bits - 1))) != 0: # if sign bit is set e.g., 8bit: 128-255
        val = val - (1 << bits)        # compute negative value
    return val

def bin_to_float(binary):
    """Use this to convert the binary form of bytes to a float for float-precision EEPROM values"""
    return struct.unpack('!f',struct.pack('!I', int(binary, 2)))[0]

def csrChecker(response):
    if response[1] == 0:
        if response[2] == 1:
            raise Exception('Control mode is incorrect!')
        elif response[2] == 2:
            raise Exception('Output is on!')
        elif response[2] == 4:
            raise Exception('Data is out of range!')
        elif response[2] == 5:
            raise Exception('User Port Off Signal Active!')
        elif response[2] == 7:
            raise Exception('One Or More Faults Active!')
        elif response[2] == 8:
            raise Exception('Setpoint Ramping Is Active!')
        elif response[2] == 9:
            raise Exception('Byte Count Is Incorrect!')
        elif response[2] == 12:
            raise Exception('Feature Not Available!')
        elif response[2] == 16:
            raise Exception('End of Target Life!')
        elif response[2] == 17:
            raise Exception('Minimum Off Time Active!')
        elif response[2] == 19:
            raise Exception('Recipe is Active!')
        elif response[2] == 28:
            raise Exception('Setpoint Exceeds User Limit!')
        elif response[2] == 30:
            raise Exception('EEPROM Read/Write Error!')
        elif response[2] == 41:
            raise Exception('One Or More Warnings Active!')
        elif response[2] == 42:
            raise Exception('DHCP Is Enabled!')
        elif response[2] == 50:
            raise Exception('Pulsing Frequency Is Out Of Range!')
        elif response[2] == 51:
            raise Exception('Pulsing Duty Cycle Is Out Of Range!')
        elif response[2] == 52:
            raise Exception('Pulsing On Or Off Time Violated!')
        elif response[2] == 61:
            raise Exception('Real Time Clock Is Busy!')
        elif response[2] == 63:
            raise Exception('Flashmode Is Active!')
        elif response[2] == 99:
            raise Exception('Command Not Implemented!')
        elif response[2] == 119:
            raise Exception('Profibus Was Reset!')

def processStatusTranslate(statusBits):
    print('PROCESS STATUS DATA BEGIN')
    x = ['In Tolerance', 'Out of Tolerance']
    print('Setpoint Tolerance State 1: ' + x[int(statusBits[0])])
    x = ['OFF', 'ON']
    print('RF On Requested: ' + x[int(statusBits[1])])
    print('RF Output On: ' + x[int(statusBits[2])])
    x = ['Recipe Disabled', 'Recipe Enabled']
    print('Recipe enable state: ' + x[int(statusBits[4])])
    x = ['Recipe Inactive', 'Recipe Active']
    print('Recipe active state: ' + x[int(statusBits[5])])
    x = ['Not Ramping', 'Ramp in Progress']
    print('Setpoint Ramping State 1: ' + x[int(statusBits[6])])
    x = ['Not Tuned', 'Tuned']
    print('Tuning Status State 1: ' + x[int(statusBits[7])])
    x = ['Closed', 'Open']
    print('Interlock: ' + x[int(statusBits[8])])
    x = ['Disabled', 'Enabled']
    print('Arc Enable State: ' + x[int(statusBits[9])])
    print('Pulsing Enable State: ' + x[int(statusBits[10])])
    x = ['Fixed', 'Sweep']
    print('Frequency Mode State 1: ' + x[int(statusBits[11])])
    if statusBits[12] == '1':
        print('Coldplate overtemp fault active')
    else:
        print('Coldplate overtemp fault not active')
    if statusBits[18] == '1':
        print('Protection limit active')
    else:
        print('Protection limit not active')
    if statusBits[19] == '1':
        print('Low AC line voltage warning active')
    else:
        print('Low AC line voltage warning not active')
    if statusBits[21] == '1':
        print('High AC line voltage warning active')
    else:
        print('High AC line voltage warning not active')
    if statusBits[24] == '1':
        print('CEX is locked')
    else:
        print('CEX is not locked')
    x = ['No warnings', 'Warnings exist']
    print('Warnings present: ' + x[int(statusBits[25])])
    x = ['No faults', 'Faults exist']
    print('Faults present: ' + x[int(statusBits[26])])
    if statusBits[27] == '1':
        print('CEX is enabled')
    else:
        print('CEX is not enabled')
    if statusBits[29] == '1':
        print('Fieldbus Error Exists')
    else:
        print('Fieldbus Error Does Not Exist')
    if statusBits[30] == '1':
        print('Inverter not ready')
    else:
        print('Inverter ready')
    print('PROCESS STATUS DATA END')
#--------------------------------------------------------------------------------------

class generator:
    def __init__(self, ipAddress):
        self.gen = AE_Com_Module.AETCP()
        self.gen.connect(ipAddress)

    def rep_cmd(self, cmd, send_sizes, recv_sizes, *args):
        buf = self.device.create_buffer_cmd(cmd, send_sizes, recv_sizes, *args)
        if buf[0] != 0: raise 'unexpected'  # ethernet fault, bad ack etc.
        if buf[1] == 0: raise CSRError(buf[2])  # CSR only
        return buf  # bytes start at buf[2]

    def rep_cmd_fastDAQ(self, cmd, send_sizes, recv_sizes, *args):
        buf = self.device.create_buffer_cmd_fastDAQ(cmd, send_sizes, recv_sizes, *args)
        if buf[0] != 0: raise 'unexpected'  # ethernet fault, bad ack etc.
        if buf[1] == 0: raise CSRError(buf[2])  # CSR only
        return buf  # bytes start at buf[2]

    def rfOFF(self):  # VERIFIED
        res = self.gen.create_buffer_cmd(1, '0:0', '1:1')
        csrChecker(res)

    def rfON(self):  # VERIFIED
        if self.reportActiveControlMode() != 2:
            raise Exception('Unit not in host control mode!')
        res = self.gen.create_buffer_cmd(2, '0:0', '1:1')  # RF ON!
        csrChecker(res)
        while res[2] != 0:
            res = self.gen.create_buffer_cmd(2, '0:0', '1:1')
            csrChecker(res)
        return res[2]

    def setRegulationMode(self, mode):  # VERIFIED
        if self.reportActiveControlMode() != 2:
            raise Exception('Unit not in host control mode!')
        if mode not in [6, 7, 8, 9]:    # 6 = fwd, 7 = dlvd, 8 = External, 9 = VA Limit
            raise Exception('Invalid regulation mode entered')
        if mode == 8:
            if self.isRFon() == True:
                raise Exception('Cannot change to External Regulation mode with RF on!')
        res = self.gen.create_buffer_cmd(3, '1:1', '1:1', mode)
        csrChecker(res)

    def setUserPowerLimit(self, power):     #VERIFIED
        low = self.reportLowPowerLimit()
        high = self.reportPowerSupplySize()
        if power >= low and power <= high:
            if self.isRFon() == True:
                raise Exception('Cannot set user power limit with RF on!')
            else:
                res = self.gen.create_buffer_cmd(4, '1:2', '1:1', int(power))
                csrChecker(res)
        else:
            if power < low:
                raise Exception('Requested power limit is below low power limit (' + str(low) + ' watts)')
            if power > high:
                raise Exception('Requested power limit is above high power limit (' + str(high) + ' watts)')

    def setUserReflectedLimit(self, power):     # VERIFIED
        high = self.reflectedPowerLimitEEPROM()
        if power >= 100 and power <= (high):
            if self.isRFon() == True:
                raise Exception('Cannot set user reflected power limit with RF on!')
            else:
                res = self.gen.create_buffer_cmd(5, '1:2', '1:1', int(power))
                csrChecker(res)
        else:
            if power < 100:
                raise Exception('Requested limit is below minimum reflected limit (100 watts)')
            if power > high:
                raise Exception('Requested limit is above maximum reflected power limit (' + str(high) + ' watts)')

    def setUserExternalFeedbackLimit(self, volts):  #VERIFIED
        if self.isRFon():
            raise Exception('Cannot do this while RF is on!')
        max_value = self.reportUserExternalFeedbackLimit(1)
        min_value = (float(self.reportLowPowerLimit())/float(self.reportPowerSupplySize()))*max_value
        if (max_value < volts):
            raise Exception('Requested limit is greater than maximum feedback limit (' + str(max_value) + ' volts)!')
        elif (min_value > volts):
            raise Exception('Requested limit is less than minimum feedback limit (' + str(min_value) + ' volts)!')
        else:
            if self.isRFon() == True:
                raise Exception('Cannot set user external feedback limit with RF on!')
            res = self.gen.create_buffer_cmd(6, '1:2', '1:1', int(volts))
            csrChecker(res)

    def restoreFactoryDefaults(self, mode):  # VERIFIED
        if mode != 1 and mode != 0:     # mode 0 resets all values, mode 1 resets all values EXCEPT network settings
            raise Exception('Invalid Entry, mode must be 0 or 1')
        res = self.gen.create_buffer_cmd(7, '2:1', '1:1', mode, 0)
        csrChecker(res)

    #BONUS COMMAND
    def isHaloEnabled(self):    #VERIFIED
        if self.reportPINNumber()[20] == '1':
            return True
        else:
            return False

    def setPowerSetpoint(self, setpoint):   #VERIFIED
        #***FOR INTERNAL REGULATION MODES: ENTER SETPOINT IN 10THS OF WATTS REGARDLESS OF HALO STATE***
        if self.reportActiveControlMode() != 2:
            raise Exception('Unit not in host control mode!')
        reg = self.reportRegulationMode()
        if reg == 8:
            userExtLimit = self.reportUserExternalFeedbackLimit(0)
            maxExtLimit = self.reportUserExternalFeedbackLimit(1)
            if setpoint > userExtLimit or setpoint > maxExtLimit:
                raise Exception('Requested setpoint greater than user/maximum external feedback limit.')
        else:
            unitPowLimit = self.reportPowerSupplySize() * 10
            userPowLimit = self.reportUserPowerLimit() * 10
            if setpoint > unitPowLimit or setpoint > userPowLimit:
                raise Exception('Requested setpoint greater than user/unit maximum power limit.')
        res = self.gen.create_buffer_cmd(8, '1:2', '1:1', setpoint)
        csrChecker(res)

    def DLP_setpoint(self, s1_setpoint, s0_setpoint):
        res = self.gen.create_buffer_cmd(8, '1:1,1:4', 2, s1_setpoint)
        csrChecker(res)

        res = self.gen.create_buffer_cmd(8, '1:1,1:4', 3, s0_setpoint)
        csrChecker(res)

    def setMaxExternalFeebackValue(self, value):    # VERIFIED
        dummyByte = 1 # Used for RFXII compatibility
        res = self.gen.create_buffer_cmd(9, '1:2,1:1', '1:1', value, dummyByte)
        csrChecker(res)

    def setActiveControlMode(self, mode):  #VERIFIED
        if mode == 2 or mode == 4 or mode == 8 or mode == 16 or mode == 32:
            res = self.gen.create_buffer_cmd(14, '1:1', '1:1', mode)
            csrChecker(res)
        else:
            raise Exception('Invalid control mode number entered! (Must be 2,4,8,16 or 32)')
        '''Mode 2 - Host
           Mode 4 - User Port (Analog)
           Mode 8 - Diagnostic Mode
           Mode 16 - DeviceNet
           Mode 32 - EtherCAT'''

    def setRecipeParameters(self, RecipeNumber, RecipeParameterType, RecipeStepNumber, ParameterSelect, ParameterValue):     # VERIFIED
        goodToGo = False
        if (RecipeNumber in [x + 1 for x in range(15)]):
            if RecipeParameterType == 0 or RecipeParameterType == 1:
                if (RecipeStepNumber in [x + 1 for x in range(15)]):
                    if RecipeParameterType == 0 and ParameterSelect == 0:
                        if (ParameterValue in [x + 1 for x in range(15)]):
                            goodToGo = True
                    elif RecipeParameterType == 0 and ParameterSelect == 1:
                        if (ParameterValue in [x + 1 for x in range(65535)]):
                            goodToGo = True
                    elif RecipeParameterType == 0 and ParameterSelect == 2:
                        if ParameterValue == 0 or ParameterValue == 1:
                            goodToGo = True
                    elif RecipeParameterType == 1 and ParameterSelect == 0:
                        if ParameterValue == 0 or ParameterValue == 1 or ParameterValue == 2:
                            goodToGo = True
                    elif RecipeParameterType == 1 and ParameterSelect == 1:
                        if ParameterValue > 0 and ParameterValue < 2147483647 and isinstance(ParameterValue, int):
                            goodToGo = True
                    elif RecipeParameterType == 1 and ParameterSelect == 2:
                        size = self.reportPowerSupplySize()
                        if self.isHaloEnabled():
                            size *= 10
                        if ParameterValue <= size:
                            goodToGo = True
                    elif RecipeParameterType == 1 and ParameterSelect == 3:
                        if ParameterValue == 0 or ParameterValue == 1 or ParameterValue == 2:
                            goodToGo = True
                    elif RecipeParameterType == 1 and ParameterSelect == 4:
                        if (ParameterValue in [x for x in range(65536)]):
                            goodToGo = True
                    elif RecipeParameterType == 1 and ParameterSelect == 5:
                        if ParameterValue == 0 or ParameterValue == 1:
                            goodToGo = True
                    elif RecipeParameterType == 1 and ParameterSelect == 6:
                        cent = self.reportFixedFrequency()
                        if (cent*0.95) <= ParameterValue and (cent*1.05) >= ParameterValue:
                            goodToGo = True
        if goodToGo == True:
            res = self.gen.create_buffer_cmd(21, '4:1,1:4', '1:1', RecipeNumber, RecipeParameterType, RecipeStepNumber, ParameterSelect, ParameterValue)
            csrChecker(res)
        else:
            raise Exception('There is something wrong with the input recipe parameter!')

    def setPulsingConfiguration(self, command, data, opt = None):   #VERIFIED
        if opt is None:    # opt == 1 is for Profibus
            if command != 8 and self.reportPulsingConfiguration(8) != 1:
                    raise Exception('Cannot change pulsing settings while pulsing is disabled (see cmd26 subcmd8)!')
            if command == 1: #Set Pulsing Mode
                if data not in [1, 2]:
                    raise Exception('Invalid data entry')
                if self.isRFon() == True:
                    raise Exception('Cannot execute command 1 with RF on!')
                res = self.gen.create_buffer_cmd(26, '1:2,1:2,1:2', '1:1', command, data, 0)
                csrChecker(res)
            elif command == 2:  #Set pulse sync output
                if data not in [0, 1]:
                    raise Exception('Invalid data entry')
                res = self.gen.create_buffer_cmd(26, '1:2,1:2,1:2', '1:1', command, data, 0)
                csrChecker(res)
            elif command == 4:  # Set sync input delay
                if self.reportPulsingConfiguration(1) == 1:
                    maximum = 1000000000
                    freq = self.reportPulsingFrequency()
                    if freq > 0:
                        maximum = (1.00/freq) * 1000000
                    if data > maximum:
                        raise Exception('Sync Input Delay cannot exceed the pulse period when operating in master mode.')
                if data > 250000 or data < 0 or (isinstance(data,int) != True):
                    raise Exception('Invalid data entry')
                res = self.gen.create_buffer_cmd(26, '1:2,1:4', '1:1', command, data)
                csrChecker(res)
            elif command == 5:  # set memory mode
                if self.isRFon() == True:
                    raise Exception('Cannot execute command 5 with RF on!')
                if data not in [0, 1]:
                    raise Exception('Invalid data entry')
                res = self.gen.create_buffer_cmd(26, '1:2,1:2,1:2', '1:1', command, data, 0)
                csrChecker(res)
            elif command == 6:  # set explicit enable mode
                if data not in [0, 1]:
                    raise Exception('Invalid data entry')
                res = self.gen.create_buffer_cmd(26, '1:2,1:2,1:2', '1:1', command, data, 0)
                csrChecker(res)
            elif command == 7:  # pulsing enable command
                if self.reportPulsingConfiguration(6) != 1:
                    raise Exception('Command 7 is only available while operating in Explicit Enable Mode')
                if data not in [0, 1, 2]:
                    raise Exception('Invalid data entry')
                res = self.gen.create_buffer_cmd(26, '1:2,1:2,1:2', '1:1', command, data, 0)
                csrChecker(res)
            elif command == 8:  # set pulsing/arc management mode
                if self.isRFon() == True:
                    raise Exception('Cannot execute command 8 with RF on!')
                if self.reportPINNumber()[8] == '8' and self.reportActiveControlMode() != 2:
                    raise Exception('With this user card configuration, must be in Host mode to execute command 8!')
                if data not in [0, 1, 2]:
                    raise Exception('Invalid data entry')
                if data == 2 and self.reportPINNumber()[19] == '0':
                    raise Exception('Arc management cannot be enabled because it is disabled in the PIN!')
                res = self.gen.create_buffer_cmd(26, '1:2,1:2,1:2', '1:1', command, data, 0)
                csrChecker(res)
            elif command == 9:  # set pulse maximum on time
                if self.reportPulsingConfiguration(11) != 0:
                    raise Exception('Slave duty cycle (CMD26 SUBCMD11) must be 0 for the slave duty cycle to be active')
                if (data not in [x for x in range(5,250001)]) and data != 0:
                    raise Exception('Invalid data entry')
                else:
                    res = self.gen.create_buffer_cmd(26, '1:2,1:4', '1:1', command, data)
                    csrChecker(res)
            elif command == 10: # set pulse sync output mode
                if data not in [0, 1]:
                    raise Exception('Invalid mode entry!')
                else:
                    res = self.gen.create_buffer_cmd(26, '1:2,1:2,1:2', '1:1', command, data, 0)
                    csrChecker(res)
            elif command == 11:     # set slave duty cycle
                if self.reportPulsingConfiguration(9) != 0:
                    raise Exception('Pulse maximum on-time (CMD26 SUBCMD9) must be 0 for the slave duty cycle to be active')
                if data > 1000 or data < 0 or (isinstance(data,int) != True):
                    raise Exception('Invalid Entry')
                else:
                    res = self.gen.create_buffer_cmd(26, '1:2,1:4', '1:1', command, data)
                    csrChecker(res)
            else:
                raise Exception('Invalid command entry')
        elif opt == 1:
            if data > 65535:
                raise Exception('Invalid Data')
            res = self.gen.create_buffer_cmd(26, '1:1,1:2', '1:1', command, data, 0)
            csrChecker(res)
        else:
            raise Exception('Invalid option number, must be 1 or blank')

    def setActiveRecipe(self, recipe):  #VERIFIED
        if self.reportProcessStatus()[0][5] == '0':
            if (recipe in [x + 1 for x in range(15)]):
                res = self.gen.create_buffer_cmd(28, '1:2', '1:1', recipe, 0)
                csrChecker(res)
            else:
                raise Exception('Invalid recipe number!')
        else:
            raise Exception('Cannot set active recipe while another is running!')

    def setSetpointRampingConfiguration(self, opt, config, rmode, up, down, mem_mode, rconfig):  #VERIFIED
        if self.reportProcessStatus()[0][5] == '0':
            if opt == 6:
                if (rmode == 1 or rmode == 2) and (up < 0 or up > 65535 or down < 0 or down > 65535):
                    raise Exception('Ramp parameters out of acceptable range (0-65535)')
                if rmode not in [0, 1, 2]:
                    raise Exception('Invalid ramp mode number!')
                res = self.gen.create_buffer_cmd(31, '3:2', '1:1', rmode, up, down)
                csrChecker(res)
            elif opt == 8:
                if config == 1:
                    if (rmode == 1 or rmode == 2) and (up < 0 or up > 65535 or down < 0 or down > 65535):
                        raise Exception('Ramp parameters out of acceptable range (0-65535)')
                    if rmode not in [0, 1, 2]:
                        raise Exception('Invalid ramp mode number!')
                    res = self.gen.create_buffer_cmd(31, '4:2', '1:1', config, rmode, up, down)
                    csrChecker(res)
                elif config == 2:
                    if mem_mode not in [0, 1]:
                        raise Exception('Invalid memory mode number!')
                    res = self.gen.create_buffer_cmd(31, '4:2', '1:1', config, mem_mode, 0, 0)
                    csrChecker(res)
                elif config == 5:
                    if rconfig not in [0, 1]:
                        raise Exception('Invalid ramping configuration mode number!')
                    res = self.gen.create_buffer_cmd(31, '4:2', '1:1', config, rconfig, 0, 0)
                    csrChecker(res)
                else:
                    raise Exception('Invalid configuration number; must be 1, 2, or 5!')
            elif opt == 3:
                if rmode not in [0, 1, 2, 3, 4]:
                    raise Exception('Invalid ramp mode/parameter number!')
                if (rmode in[1, 2, 3, 4]) and (rconfig < 0 or rconfig > 65535):
                    raise Exception('Ramp parameters out of acceptable range (0-65535)')
                res = self.gen.create_buffer_cmd(31, '1:1,1:2', '1:1', rmode, rconfig)
                csrChecker(res)
            else:
                raise Exception('Invalid option number; must be 6, 8, or 3!')
        else:
            raise Exception('Cannot change setpoint ramping parameters while recipe is active!')

    def setCEXOffset(self, offset):  # VERIFIED
        if (offset in [x for x in range(3601)]):
           res = self.gen.create_buffer_cmd(32, '1:1,1:2', '1:1', 0, offset)
           csrChecker(res)
        else:
            raise Exception('Invalid offset value entered, must be integer between 0 and 3600')

    def setArcSuppressionParameters(self, parameter, data): #VERIFIED
        if self.reportPINNumber()[19] == '0':
            raise Exception('Arc Management not enabled in PIN!')
        elif self.reportPINNumber()[14] == '1':
            raise Exception('Cannot configure Arc Suppression settings while pulsing is enabled!')
        else:
            if (parameter in [x for x in range(13)]):
                if parameter != 4 and parameter != 5 and parameter != 10 and self.isRFon():
                    raise Exception('Cannot perform this action while RF is on!')
                elif parameter == 0 and (data != 0) and (data > 511 or data < 5 or isinstance(data, int)!= True):
                    raise Exception('Invalid Arc Suppression Time requested!')
                elif parameter == 1 and (data > 10000 or data < 0 or isinstance(data, int)!= True):
                    raise Exception('Invalid Initial Delay Time requested!')
                elif parameter == 2 and (data > 5000 or data < 0 or isinstance(data, int)!= True):
                    raise Exception('Invalid Setpoint Delay Time requested!')
                elif parameter == 3 and (data > 250 or data < 0 or isinstance(data, int)!= True):
                    raise Exception('Invalid Number of Attempts requested!')
                elif parameter == 4 and data != 0 and data != 1:
                    raise Exception('Invalid Reflected Power Limit Mode requested!')
                elif parameter == 5 and data != 0 and data != 1:
                    raise Exception('Invalid External Arc Input State requested!')
                elif parameter == 6 and (data != 0 and data != 1):
                    raise Exception('Invalid RF Power Latch State requested!')
                elif parameter == 7 and (data != 0 and data != 1):
                    raise Exception('Invalid Arc Output Signal State requested!')
                elif parameter == 8 and (data not in [x for x in range(101)]):
                    raise Exception('Invalid Reflected Coefficient Window requested!')
                elif parameter == 9 and data != 0:
                    raise Exception('Data value must be zero to Reset Cumulative Arc Events')
                elif parameter == 10 and (data != 0 and data != 1):
                    raise Exception('Invalid Gamma Arc Detection Mode requested!')
                elif parameter == 11 and (data > self.reportUserReflectedLimit(1) or data < 1 or isinstance(data, int)!= True):
                    raise Exception('Invalid Reflected Power Threshold for Arc Detection requested!')
                else:
                    res = self.gen.create_buffer_cmd(36, '1:1,1:2', '1:1', parameter, data)
                    csrChecker(res)
            else:
                raise Exception('''Invalid Parameter Selected:
                                 0 = Arc Suppression Time
                                 1 = Initial Delay Time
                                 2 = Setpoint Delay Time
                                 3 = Number of Attempts
                                 4 = Reflected Power Limit Mode
                                 5 = Enable External Arc Input
                                 6 = RF Power Latch State
                                 7 = Arc Output Control Signal
                                 8 = Reflection Coefficient Window
                                 9 = Reset Cumulative Arc Events
                                 10 = Gamma Arc Detection Mode
                                 11 = Reflected Power Threshold for Arc Detection''')

    def setTuningTimeoutValue(self, value):   #VERIFIED
        if value > 60000 or value < 0 or isinstance(value,int) != True:
            raise Exception('Invalid tuning timeout value requested!')
        elif self.isRFon():
            raise Exception('Cannot perform this action while RF is on!')
        else:
            res = self.gen.create_buffer_cmd(38, '1:4', '1:1', value)
            csrChecker(res)

    def setCommWatchDogTimer(self, time):   #VERIFIED
        if time > 65535 or time < 0 or isinstance(time, int) != True:
            raise Exception('Invalid time value; must be 0 - 65535')
        else:
            res = self.gen.create_buffer_cmd(39, '1:1,1:2', '1:1', 0, time)
            csrChecker(res)

    def setHostPortTimeout(self, value):
            raise Exception('setHostPortTimeout() is only able to be used over serial communications!')

    def setMinimumTuningFrequency(self, freqkHz):   #VERIFIED
        min = int(self.reportFixedFrequency(3)/1000)
        max = (int(self.reportFixedFrequency(2)/1000)) + 1
        maxTune = self.reportMaximumTuningFrequency()
        startTune = self.reportTuningStartFrequency()
        if freqkHz > max or freqkHz < min or freqkHz > maxTune or freqkHz > startTune or isinstance(freqkHz, int) != True:
            raise Exception('Invalid frequency')
        elif self.isRFon():
            raise Exception('Cannot perform this action while RF is on!')
        else:
            res = self.gen.create_buffer_cmd(44, '1:4', '1:1', freqkHz)
            csrChecker(res)

    def setMaximumTuningFrequency(self, freqkHz):   #VERIFIED
        min = int(self.reportFixedFrequency(3) / 1000)
        max = (int(self.reportFixedFrequency(2) / 1000)) + 1
        minTune = self.reportMinimumTuningFrequency()
        startTune = self.reportTuningStartFrequency()
        if freqkHz > max or freqkHz < min or freqkHz < minTune or freqkHz < startTune or isinstance(freqkHz, int) != True:
            raise Exception('Invalid frequency')
        elif self.isRFon():
            raise Exception('Cannot perform this action while RF is on!')
        else:
            res = self.gen.create_buffer_cmd(45, '1:4', '1:1', freqkHz)
            csrChecker(res)

    def setTuningStartFrequency(self, freqkHz):     #VERIFIED
        minTune = self.reportMinimumTuningFrequency()
        maxTune = self.reportMaximumTuningFrequency() + 1
        if freqkHz < minTune or freqkHz > maxTune or isinstance(freqkHz, int) != True:
            raise Exception('Invalid frequency')
        elif self.isRFon():
            raise Exception('Cannot perform this action while RF is on!')
        res = self.gen.create_buffer_cmd(46, '1:4', '1:1', freqkHz)
        csrChecker(res)

    def setFrequencyMode(self, mode):   #VERIFIED
        if self.reportProcessStatus()[0][5] != '0':
            raise Exception('Cannot change frequency mode while recipe is active!')
        else:
            if mode == 1:  # Variable
                if self.reportPINNumber()[2] == '0':
                    raise Exception('Cannot change to variable frequency mode if no Tuning algorithm is enabled!')
                res = self.gen.create_buffer_cmd(48, '1:1', '1:1', mode)
                csrChecker(res)
            elif mode == 0:  # Fixed
                res = self.gen.create_buffer_cmd(48, '1:1', '1:1', mode)
                csrChecker(res)
            else:
                raise Exception('Invalid Mode; must be 0 or 1')

    def setSpecialSubCommand(self):
        print('Refer to FW00416 for use; intended to be used by ECAT or DeviceNet')

    def setRetuningThreshold(self, threshold):      #VERIFIED
        if self.reportPINNumber()[2] != '3':
            raise Exception('Cannot do this if Tuning algorithm III is not enabled!')
        if self.isRFon():
            raise Exception('Cannot do this while RF is on!')
        else:
            if threshold > 3000 or threshold < 1 or isinstance(threshold,int) != True:
                raise Exception('Invalid threshold; must be 1 - 3000')
            else:
                res = self.gen.create_buffer_cmd(58, '1:4', '1:1', threshold)
                csrChecker(res)

    def setTuneDelay(self, delay):  #VERIFIED
        if self.isRFon():
            raise Exception('Cannot do this while RF is on!')
        else:
            if delay > 60000 or delay < 0 or isinstance(delay, int) != True:
                raise Exception('Invalid Delay; must be 0 - 60000')
            else:
                res = self.gen.create_buffer_cmd(60, '1:4', '1:1', delay)
                csrChecker(res)

    def setFixedFrequency(self, freqKHz):       # VERIFIED
        min = int(self.reportFixedFrequency(3) / 1000)
        max = (int(self.reportFixedFrequency(2) / 1000)) + 1
        if self.reportProcessStatus()[0][5] != '0':
            raise Exception('Cannot change frequency mode while recipe is active!')
        elif freqKHz not in [x for x in range(min, max)]:
            raise Exception('Invalid frequency')
        else:
            res = self.gen.create_buffer_cmd(61, '1:4', '1:1', freqKHz)
            csrChecker(res)

    def genericCMD(self, arguments):
        generic = self.rep_cmd_fastDAQ(*arguments)

        import struct

        def convert_bytes_to_uint32_list(byte_data):
            if len(byte_data) % 4 != 0:
                raise ValueError("Byte data length must be a multiple of 4")

            num_integers = len(byte_data) // 4
            return list(struct.unpack('<' + 'I' * num_integers, byte_data))

        if arguments[3] == 57:
            raw_bytes = generic[2]
            int_list = convert_bytes_to_uint32_list(raw_bytes)
            #print(f"int list is: {int_list}")
            generic[2] = int_list[2:]

        #print(f"generic send return is: {generic}")

        return generic



    def resetMicroprocessor(self):  #VERIFIED
        if self.isRFon():
            raise Exception('Cannot do this while RF is on!')
        elif self.reportLockedSubCommand(0, 0, 0) != 'Unlocked':
            raise Exception('Unit is not unlocked!')
        else:
            res = self.gen.create_buffer_cmd(66, '1:1', '1:1', 170)
            csrChecker(res)

    def lock(self, lock_unlock):
        """This function locks or unlocks a device.  Parameter is
        lock_unlock which should be either 'u' for unlock or 'l' for lock"""
        pass_level = 0
        # Lock type lists
        no_md5 = ['7432030', '7432032', '7432035', '7434650', '7432047']
        md5 = ['7432011', '7434649', '7432030', '7432038', '7432048', '7434718', '7434773', '7434660']

        AE_key = ctypes.LibraryLoader('C:\Windows\SysWOW64\AeKey.dll')
        fw_num = self.gen.create_buffer_cmd(130, '1:1', '7:s', 0)[2]
        if lock_unlock.lower() == 'u':
            # if self.is_locked():
            if self.lockStatus() == 'Locked':
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
            if self.lockStatus() == 'Locked':
                print('Unable to unlock generator')
        elif lock_unlock.lower() == 'l':
            self.gen.create_buffer_cmd(68, '1:1', '1:1', 219)
            print('Unit is locked')
        else:
            print('Wrong character input!')

    def openUnlockWindow(self):     #FIXME
        if self.lockStatus() == 'Unlocked':
            return 'Already unlocked!'
        else:
            hashSeed = self.reportHashSeed()
            md5 = hashlib.md5()
            sh = list(range(0, 32, 8))
            sh.reverse()  # hashseed is processed msb first
            for i in sh: md5.update((chr((hashSeed >> i) & 0xFF)).encode('utf-8'))
            md5.update(('gt5>Zx#9QsR3dA*P8$wUk!F]').encode('utf-8'))
            digStr = ''
            for x in md5.digest(): digStr += ' %d' % x
            # digStr = digStr.split()
            # for x in range(len(digStr)):
            #     digStr[x] = int(digStr[x])
            if int(self.gen.create_buffer_cmd(67, '16:s', '1:1', digStr)[2]) != 99:
                raise Exception('Unexpected CSR')
            if self.lockStatus() != 'Unlocked':
                raise Exception('Unable to unlock unit!')
            print('Unit unlocked!')

    def lockUnit(self):  # VERIFIED
        res = self.gen.create_buffer_cmd(68, '1:1', '1:1', 219)
        csrChecker(res)
        if self.lockStatus() != 'Locked':
            raise Exception('Unable to lock unit!')
        print('Unit locked!')
        #NOTE: If connected in VFP via ethernet, unit will automatically unlock again immediately

    #BONUS COMMAND
    def lockStatus(self):   # VERIFIED
        return self.reportLockedSubCommand(0, 0, 0)

    def setSerialAddressBaud(self, address, baud, mode485):  # VERIFIED
        if baud not in [x for x in range(7)]:
            raise Exception('''Invalid baud chosen, options are:
                     0 = 2400
                     1 = 4800
                     2 = 9600
                     3 = 19200
                     4 = 38400
                     5 = 57600
                     6 = 115200''')
        if mode485 not in [0, 1]:
            raise Exception('Invalid RS-485 mode chosen; must be 0 = OFF or 1 = ON')
        if address not in [x for x in range(32)]:
            raise Exception('Invalid address entered (must be 0-31)!')
        res = self.gen.create_buffer_cmd(69, '4:1', '1:1', 1, address, baud, mode485)
        csrChecker(res)

    def setRealTimeClock(self, seconds, minutes, hours, dayofweek, day, month, year):   # VERIFIED
        if seconds not in [x for x in range(60)]:
            raise Exception('Invalid seconds number!')
        if minutes not in [x for x in range(60)]:
            raise Exception('Invalid minutes number!')
        if hours not in [x for x in range(24)]:
            raise Exception('Invalid hours number!')
        if dayofweek not in [x + 1 for x in range(7)]:
            raise Exception('Invalid day of week number!')
        if day not in [x + 1 for x in range(31)]:
            raise Exception('Invalid day of month number!')
        if month not in [x + 1 for x in range(12)]:
            raise Exception('Invalid month number!')
        if year not in [x for x in range(100)]:
            raise Exception('Invalid year number!')
        seconds = int(str(seconds), 16)
        minutes = int(str(minutes), 16)
        hours = int(str(hours), 16)
        dayofweek = int(str(dayofweek), 16)
        day = int(str(day), 16)
        month = int(str(month), 16)
        year = int(str(year), 16)
        res = self.gen.create_buffer_cmd(70, '7:1', '1:1', seconds, minutes, hours, dayofweek, day, month, year)
        csrChecker(res)

    #BONUS COMMAND
    def setRealTimeClockNow(self):  #VERIFIED
        now = datetime.now()
        dateList = now.strftime("%m.%d.%Y.%H.%M.%S").split('.')
        dateList[2] = dateList[2][2:]
        day = datetime.today().weekday() + 2
        for i in range(0, len(dateList)):
            dateList[i] = int(dateList[i])
        res = self.setRealTimeClock(dateList[5], dateList[4], dateList[3], day, dateList[1], dateList[0], dateList[2])

    def setSystemControl(self, subcommand, data):  #VERIFIED
        #NOTE: for subcommands 0, 1, 2, 200, and 202 the data should be entered as a string
        if subcommand == 0: #Set IP Address
            if self.reportSystemControl(5, 0, 0) == 'DHCP Enabled':
                raise Exception('DHCP is enabled!')
            iP = data.split('.')
            oct1 = int(iP[3])
            oct2 = int(iP[2])
            oct3 = int(iP[1])
            oct4 = int(iP[0])
            res = self.gen.create_buffer_cmd(71, '5:1', '1:1', subcommand, oct1, oct2, oct3, oct4)
        elif subcommand == 1: #Set Default Gateway
            if self.reportSystemControl(5, 0, 0) == 'DHCP Enabled':
                raise Exception('DHCP is enabled!')
            gateway = data.split('.')
            oct1 = int(gateway[3])
            oct2 = int(gateway[2])
            oct3 = int(gateway[1])
            oct4 = int(gateway[0])
            res = self.gen.create_buffer_cmd(71, '5:1', '1:1', subcommand, oct1, oct2, oct3, oct4)
        elif subcommand == 2: #Set subnet mask
            if self.reportSystemControl(5, 0, 0) == 'DHCP Enabled':
                raise Exception('DHCP is enabled!')
            subnetmask = data.split('.')
            oct1 = int(subnetmask[3])
            oct2 = int(subnetmask[2])
            oct3 = int(subnetmask[1])
            oct4 = int(subnetmask[0])
            res = self.gen.create_buffer_cmd(71, '5:1', '1:1', subcommand, oct1, oct2, oct3, oct4)
        elif subcommand == 5: #Set DHCP Client Enable
            if data != 1 and data != 0:
                raise Exception('Invalid DHCP mode, must be 0 or 1')
            res = self.gen.create_buffer_cmd(71, '2:1', '1:1', subcommand, data)
        elif subcommand == 20: #Reset MD5 seed
            res = self.gen.create_buffer_cmd(71, '1:1', '1:1', 20)
        elif subcommand == 56: #Enter flash mode
            if self.isRFon():
                raise Exception('Cannot do this hwile RF is on!')
            if data not in [x + 1 for x in range(6)]:
                raise Exception('''Invalid baud chosen, options are:
                         0 = 2400
                         1 = 4800
                         2 = 9600
                         3 = 19200
                         4 = 38400
                         5 = 57600
                         6 = 115200 [Default]''')
            res = self.gen.create_buffer_cmd(71, '2:1', '0:0', 56, data)
        elif subcommand == 200:  #Set Domain Name (NV)
            if self.reportSystemControl(5, 0, 0) == 'DHCP Enabled':
                raise Exception('DHCP is enabled!')
            if data is not None:
                length = len(data)
            else:
                res = self.gen.create_buffer_cmd(71, '1:1', '1:1', 200, None)
            my_chars = [ord(char) for char in data]
            if length < 65:
                res = self.gen.create_buffer_cmd(71, '1:1,' + str(length) + ':b', '1:1', 200, my_chars)
            else:
                raise Exception('Domain name has more than 64 characters!')
        elif subcommand == 202:  #Set DNS Server IP Address (NV)
            if self.reportSystemControl(5, 0, 0) == 'DHCP Enabled':
                raise Exception('DHCP is enabled!')
            iP = data.split('.')
            oct1 = int(iP[3])
            oct2 = int(iP[2])
            oct3 = int(iP[1])
            oct4 = int(iP[0])
            res = self.gen.create_buffer_cmd(71, '5:1', '1:1', subcommand, oct1, oct2, oct3, oct4)
        elif subcommand == 203:  #Set DNS Configuration (NV)
            if data != 1 and data != 0:
                raise Exception('''Invalid DNS Selection:
                         0 = Disbale DNS server updates
                         1 = Enable DNS Server Updates''')
            res = self.gen.create_buffer_cmd(71, '1:1,1:2', '1:1', subcommand, data)
        else:
            raise Exception('Invalid command requested!')
        csrChecker(res)

    def setDAC(self, device, channel, value): #VERIFIED
        if value > 65535 or value < 0 or isinstance(value, int) != True:
            raise Exception('Invalid data value entered!')
        if device == 0 and channel not in [0, 1, 2]:
            raise Exception('Device 0 only has channels 0, 1, and 2!')
        if device == 1 and channel != 0:
            raise Exception('Device 1 only has channel 0!')
        if (device == 1 or device == 0) and self.reportOpenCloseLoopOperation() == 'Closed Loop':
                raise Exception('Unit must be in open control loop to do this!')
        res = self.gen.create_buffer_cmd(80, '2:1,1:2', '1:1', device, channel, value)
        csrChecker(res)

    def setSplitCWEnable(self):
        res = self.gen.create_buffer_cmd(118, '1:4', 183, 0, 1, 0)
        csrChecker(res)

    def setSplitCWDisable(self):
        res = self.gen.create_buffer_cmd(118, '1:4', 183, 0, 0, 0)
        csrChecker(res)

    def setMaskedFault(self, mode, fcode):  #VERIFIED
        if mode not in [0, 1]:
            raise Exception('Invalid Mode set: 0 = clear mask, 1 = set mask')
        faultCodes = [16, 17, 18, 21, 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 38, 39, 40, 43, 46, 47, 48, 49, 50,
                      51, 52, 53, 57, 58, 61, 68, 69, 74, 75, 76, 82, 93, 99, 101, 120, 144, 145, 146, 200, 201, 300,
                      301, 302, 303, 304, 305, 30, 307, 313, 314, 316, 318, 319, 320, 325, 326, 327, 328, 329, 330, 331,
                      332, 338, 339, 341, 343, 344, 345, 30, 351, 352, 353, 354, 355, 356, 357, 358, 359, 360, 365, 366,
                      367, 368, 369, 370, 371, 372, 373, 374, 375, 376, 377, 378, 379, 380, 381, 382, 383, 384, 385,
                      386, 389, 393, 397, 398, 4011, 4021, 4031, 4041, 4051, 4061, 4071, 4081, 460, 550, 600, 700, 701,
                      702, 703, 704, 705, 706, 707, 709, 710, 712, 714, 715, 720, 750, 751, 800, 801, 802, 803, 804,
                      805, 806, 807, 808, 809, 810, 811, 812, 813, 814, 815, 816, 817, 818, 819, 820, 821, 822, 823,
                      824, 825, 826, 827, 828, 829, 830, 9982, 9992, 1001, 1002, 10031, 10041, 10051, 10061, 10071,
                      10081, 10091, 10101, 10111, 10121, 10131, 10141, 10151, 10161, 10171, 10181]
        if fcode not in faultCodes:
            raise Exception('Invalid fault code entered!')
        res = self.gen.create_buffer_cmd(82, '1:1,1:2', '1:1', mode, fcode)
        csrChecker(res)

    def setOutputLatch(self, device, value): #VERIFIED
        if value > 255 or value < 0 or isinstance(value, int) != True:
            raise Exception('Invalid value!')
        if device != 1 and device != 0:
            raise Exception('Invalid device chosen, must be 1 or 0')
        res = self.gen.create_buffer_cmd(83, '2:1', '1:1', device, value)
        csrChecker(res)

    def setLockedSubCommand(self, memLocation, subcommand, p1 = None, p2 = None, p3 = None, p4 = None, p5 = None, p6 = None):
        faultCodes = [16, 17, 18, 21, 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 38, 39, 40, 43, 46, 47, 48, 49, 50,
                      51, 52, 53, 57, 58, 61, 68, 69, 74, 75, 76, 82, 93, 99, 101, 120, 144, 145, 146, 200, 201, 300,
                      301, 302, 303, 304, 305, 30, 307, 313, 314, 316, 318, 319, 320, 325, 326, 327, 328, 329, 330, 331,
                      332, 338, 339, 341, 343, 344, 345, 30, 351, 352, 353, 354, 355, 356, 357, 358, 359, 360, 365, 366,
                      367, 368, 369, 370, 371, 372, 373, 374, 375, 376, 377, 378, 379, 380, 381, 382, 383, 384, 385,
                      386, 389, 393, 397, 398, 4011, 4021, 4031, 4041, 4051, 4061, 4071, 4081, 460, 550, 600, 700, 701,
                      702, 703, 704, 705, 706, 707, 709, 710, 712, 714, 715, 720, 750, 751, 800, 801, 802, 803, 804,
                      805, 806, 807, 808, 809, 810, 811, 812, 813, 814, 815, 816, 817, 818, 819, 820, 821, 822, 823,
                      824, 825, 826, 827, 828, 829, 830, 9982, 9992, 1001, 1002, 10031, 10041, 10051, 10061, 10071,
                      10081, 10091, 10101, 10111, 10121, 10131, 10141, 10151, 10161, 10171, 10181]
        if subcommand == 1: #Reset Performance Monitor Statistics  #FIXME Not Tested
            res = self.gen.create_buffer_cmd(87, '2:2', '1:1',  subcommand, 0)
        elif subcommand == 2: #Set memory address value #FIXME Not Tested
            # NOTE: baseAddress options = [1610678272, 1610743808, 1610809344, 1610940416, 1610973184, 1611071488, 4026531840]
            # corresponding to: [Logic FPGA BCLK Section, User CPLD Section, Logic FPGA ADC Section, COMM card FPGA Section,
            #  COMM card CPLD Section, Logic FPGA DDS Section, CPU Module FPGA Section]
            res = self.gen.create_buffer_cmd(87, '4:2,3:4', '1:1', subcommand, 0, 0, 0, p1, p2, p3)
        elif subcommand == 4: #Weird test command   #FIXME Not Implemented
            raise Exception('Subcommand not implemented in python class because brain is smooth like back of dolphin.')
        elif subcommand == 5: #Set Analog User Card Setpoint Input Bias Gain and Offset     #VERIFIED
            if p1 != 1 and p1 != 0:
                raise Exception('Invalid destination entered!')
            if p2 > 65535 or p2 < 0 or isinstance(p2, int) != True:
                raise Exception('Invalid setpoint gain entered!')
            if p3 > 32767 or p3 < -32768 or isinstance(p3, int) != True:
                raise Exception('Invalid setpoint offset entered!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, p3)
        elif subcommand == 6: #Set Analog User Card Forward Power Gain and Offset       #VERIFIED
            if p1 != 1 and p1 != 0:
                raise Exception('Invalid destination entered!')
            if p2 > 65535 or p2 < 0 or isinstance(p2, int) != True:
                raise Exception('Invalid forward power gain entered!')
            if p3 > 32767 or p3 < -32768 or isinstance(p3, int) != True:
                raise Exception('Invalid forward power offset entered!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, p3)
        elif subcommand == 7: #Set Analog User Card Reflected Power Gain and Offset     #VERIFIED
            if p1 != 1 and p1 != 0:
                raise Exception('Invalid destination entered!')
            if p2 > 65535 or p2 < 0 or isinstance(p2, int) != True:
                raise Exception('Invalid reflected power gain entered!')
            if p3 > 32767 or p3 < -32768 or isinstance(p3, int) != True:
                raise Exception('Invalid reflected power offset entered!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, p3)
        elif subcommand == 8: #Set Analog User Card Load Power Gain and Offset
            if p1 != 1 and p1 != 0:
                raise Exception('Invalid destination entered!')
            if p2 > 65535 or p2 < 0 or isinstance(p2, int) != True:
                raise Exception('Invalid load power gain entered!')
            if p3 > 32767 or p3 < -32768 or isinstance(p3, int) != True:
                raise Exception('Invalid load power offset entered!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, p3)
        elif subcommand == 10: #DC Bias Gain and Offset [Analog]
            if p1 != 1 and p1 != 0:
                raise Exception('Invalid destination entered!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, p3)
        elif subcommand == 11: # Tuning Log Control
            if p1 != 1 and p1 != 2 and p1 != 3:
                raise Exception('Invalid tuning log command number entered!')
            if p1 == 1 and p2 != 1 and p2 != 2 and p2 != 3:
                raise Exception('Invalid tuning log mode number entered!')
            if p1 == 2 and p2 != 1 and p2 != 2:
                raise Exception('Invalid tuning log start number entered!')
            if p1 == 3 and p2 != 1:
                raise Exception('Invalid tuning log stop number entered!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, 0)
        elif subcommand == 12: # Reset NOVRAM
            if self.isRFon():
                raise Exception('Cannot do this hile RF is on!')
            res = self.gen.create_buffer_cmd(87, '2:2', '1:1', subcommand, 57)
        elif subcommand == 14: # Enable/Disable Fans
            if p1 != 1 and p1 != 2:
                raise Exception('Invalid fans selection!')
            if p2 != 0 and p2 != 1:
                raise Exception('Invalid fan setting selection!')
            res = self.gen.create_buffer_cmd(87, '3:2', '1:1', subcommand, p1, p2)
        elif subcommand == 16: # Set subsystem fault
            if p1 not in faultCodes or p1 >= 408 or p1 <= 402:
                raise Exception('Invalid fault code entered!')
            res = self.gen.create_buffer_cmd(87, '2:2', '1:1', subcommand, p1)
        elif subcommand == 17: # Clear subsystem fault
            if p1 not in faultCodes or (p1 != 403 and p1 != 402):
                raise Exception('Invalid fault code entered!')
            res = self.gen.create_buffer_cmd(87, '2:2', '1:1', subcommand, p1)
        elif subcommand == 21: #Internal Logging Configuration
            if p1 != 0 and p1 != 1:
                raise Exception('Invalid log selection!')
            if p2 not in [x for x in range(6)]:
                raise Exception('Invalid logging configuration peter selection!')
            if p2 == 0 and p3 != 0 and p3 != 1:
                raise Exception('Invalid logging enable state selection!')
            if p2 == 1 and p3 != 0 and p3 != 1:
                raise Exception('Invalid log reset election!')
            if p2 == 2 and (p3 != 1 or p1 != 0):
                raise Exception('Invalid restore default config parameters!')
            if p2 == 3 and p3 != 0 and p3 != 1:
                raise Exception('Invalid memory type selection!')
            if p2 == 4 and p3 != 0 and p3 != 1:
                raise Exception('Invalid memory buffer config selection!')
            if p2 == 5 and p1 != 0:
                raise Exception('Invalid log selection for set process status mask!')
            res = self.gen.create_buffer_cmd(87, '1:2,2:1,1:4', '1:1', subcommand, p1, p2, p3)
        elif subcommand == 22: # Set Internal Data Logging Event
            raise Exception('Cmd 87 Subcommand 22 is given its own function called setInternalDataLogEvent!')
        elif subcommand == 23: # Set Communication Interface Logging
            if p1 != 1 and p1 != 2 and p1 != 4:
                raise Exception('Invalid control source selection!')
            if p2 != 0 and p2 != 1:
                raise Exception('Invalid logging enable state!')
            res = self.gen.create_buffer_cmd(87, '1:2,2:1', '1:1', subcommand, p1, p2)
        elif subcommand == 25: #RF Drive Disable #FIXME need to check for DC rail DAC
            if p1 != 0 and p1 != 1:
                raise Exception('Invalid enable state selection!')
            if self.reportOpenCloseLoopOperation() == 'Closed Loop':
                raise Exception('Unit must be in open control loop to do this!')
            res = self.gen.create_buffer_cmd(87, '2:2', '1:1', subcommand, p1)
        elif subcommand == 27: #Frequency Sweep Log Configuration
            if p1 not in [x for x in range(5)]:
                raise Exception('Invalid sweep log command number!')
            if p2 != 0 and p2 != 1:
                raise Exception('Invalid sweep log command parameter!')
            if self.reportFrequencyMode() != 1:
                raise Exception('Unit must be in variable frequency mode!')
            if self.reportPINNumber()[2] != '3':
                raise Exception('Unit must be using Tuning Algorithm 3!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, 0)
        elif subcommand == 28: #RF Bias DAC Claibration Value
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 != 0 and p1 != 1 and p1 != 2 and p1 != 3:
                raise Exception('Invalid destination!')
            if self.reportOpenCloseLoopOperation() == 'Closed Loop':
                raise Exception('Unit must be in open control loop to do this!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, p3)
        elif subcommand == 29: #Phase Offset Calibration Value
            if p1 != 0 and p1 != 1:
                raise Exception('Invalid destination!')
            if p3 not in [x for x in range(3601)]:
                raise Exception('Invalid phase value!')
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, 0, p3)
        elif subcommand == 30: #RF Bias DAC Lookup Table Cal Values
            if p1 != 0 and p1 != 1 and p1 != 2 and p1 != 3:
                raise Exception('Invalid destination!')
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if self.reportOpenCloseLoopOperation() == 'Closed Loop':
                raise Exception('Unit must be in open control loop to do this!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, p3)
        elif subcommand == 32: #Thermistor Resistance Correction
            if p1 != 0 and p1 != 1:
                raise Exception('Invalid destination!')
            if p2 not in [0, 1, 2, 3, 8, 9, 10, 11, 16]:
                raise Exception('Invalid thermistor selection!')
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, p3)
        elif subcommand == 33: #Thermistor Delta Temperature
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1, 16, 17]:
                raise Exception('Invalid destination!')
            fixedPointInt = int(p2)
            fixedPointDec = int((float(p2) - int(p2))*256)
            bytes_form = (fixedPointInt).to_bytes(3, byteorder = 'big') + (fixedPointDec).to_bytes(1, byteorder = 'big')
            int_form = int.from_bytes(bytes_form, 'big')
            res = self.gen.create_buffer_cmd(87, '2:2,1:4', '1:1', subcommand, p1, int_form) #FIXME not sure if form is right
        elif subcommand == 34: #DC Voltage Offset #FIXME NEED TO CHECK FORMATTING
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 != 0 and p1 != 1:
                raise Exception('Invalid destination!')
            val = abs(p2)
            format = int.from_bytes(bytes(bytearray.fromhex(str(FixedPoint(val, signed=True, m=11, n=3)))), "little")
            if p2 < 0:
                format += 64
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, format, 0)
        elif subcommand == 35: #DC Current Offset
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 != 0 and p1 != 1:
                raise Exception('Invalid destination!')
            val = abs(p2)
            format = int.from_bytes(bytes(bytearray.fromhex(str(FixedPoint(val, signed=True, m=11, n=3)))), "little")
            if p2 < 0:
                format += 64
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, format, 0)
        elif subcommand == 36: #Diagnostics Calibration
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 != 0 and p1 != 1:
                raise Exception('Invalid destination!')
            if p2 != 0 and p2 != 1 and p2 != 2:
                raise Exception('Invalid diagnostic parameter selection!')
            res = self.gen.create_buffer_cmd(87, '5:2', '1:1', subcommand, p1, p2, p3, p4)
        elif subcommand == 48: #Clear NV Fault Flags
            res = self.gen.create_buffer_cmd(87, '2:2', '1:1',subcommand, 0)
        elif subcommand == 49: #Fault State
            if p1 != 0 and p1 != 1:
                raise Exception('Invalid tree selection!')
            if p2 != 0 and p2 != 1:
                raise Exception('Invalid action selection!')
            if p3 not in faultCodes:
                raise Exception('Invalid fault code!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, p2, p3)
        elif subcommand == 50: #CEX Output Scale Factor
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 != 0 and p1 != 1:
                raise Exception('Invalid destination!')
            if p2 not in [x for x in range(1024)]:
                raise Exception('Invalid scale factor!')
            res = self.gen.create_buffer_cmd(87, '3:2', '1:1', subcommand, memLocation, p1)
        elif subcommand == 51: #CEX Output Calibration Value
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 != 0 and p1 != 1:
                raise Exception('Invalid destination!')
            if p3 not in [x for x in range(3601)]:
                raise Exception('Invalid scale factor!')
            res = self.gen.create_buffer_cmd(87, '4:2', '1:1', subcommand, p1, 0, p3)
        elif subcommand == 231: #Write Manufacture Data  #FIXME VFP says command not implemented
            if p1 == 0: #Write Unit Serial Number
                res = self.gen.create_buffer_cmd(87, '2:2,1:4', '1:1', 0, 0, p2)
            elif p1 == 1: #Write Unit 315 Part Number
                Unit315 = str(p2)
                parts = list(Unit315)
                if len(Unit315) > 11:
                    res = self.gen.create_buffer_cmd(87, '2:2,12:2', '1:1', 1, 1, parts[0], parts[1], parts[2], parts[3],
                                    parts[4], parts[5], parts[6], parts[7], parts[8],parts[9], parts[10], parts[11])
                else:
                    res = self.gen.create_buffer_cmd(87, '2:2,10:2', '1:1', p1, 1, parts[0], parts[1], parts[2], parts[3],
                        parts[4], parts[5], parts[6], parts[7], parts[8], parts[9], parts[10])
                    #FIXME Assumes trailing zero is ignored and 7 or 8 digit PN's
            elif p1 == 2: #Write Customer Part Number
                customerPN = str(p2)
                parts = list(customerPN)
                res = self.gen.create_buffer_cmd(87, '2:2,16:2', '1:1', p1, 2, parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6], parts[7], parts[8],parts[9],
                                                                             parts[10], parts[11], parts[12], parts[13], parts[14], parts[15]) #FIXME Assumes 16 ASCII
            elif p1 == 3: #Write Unit Date of Manufacture
                manufactureDate = str(p2)
                parts = list(manufactureDate)
                res = self.gen.create_buffer_cmd(87, '2:2,10:2', '1:1', p1, 3, parts[0], parts[1], parts[2], parts[3],parts[4], parts[5], parts[6], parts[7], parts[8],parts[9])  # FIXME Assumes 10 ASCII
            elif p1 == 6: #Write Final Assembly Revision
                assemblyRevision = str(p2)
                parts = list(assemblyRevision)
                res = self.gen.create_buffer_cmd(87, '2:2,1:1', '1:1', p1, 6, parts[0])
            elif p1 == 7: #Write Customer Part Number Revision
                customerRevision = str(p2)
                parts = list(customerRevision)
                res = self.gen.create_buffer_cmd(87, '3:2', '1:1', p1, 7, parts[0], parts[1])
            else:
                print('''Invalid Write Location Selected:
                         0 = Write Unit Serial Number
                         1 = Write Unit 315 Number
                         2 = Write Customer Part Number
                         3 = Write Unit Date of Manufacture
                         4 = None
                         5 = None
                         6 = Write Unit Final Assembly Revision
                         7 = Write Customer Part Number Revision''')
        elif subcommand == 249: #Set Calibration Mode
            if p1 != 0 and p1 != 1:
                raise Exception('''Invalid Mode Selected:
                         0 = Calibration Mode Disabled
                         1 = Calibration Mode Enabled''')
            res = self.gen.create_buffer_cmd(87, '2:2', '1:1', subcommand, p1)
        elif subcommand == 253: #Set Ignore DC Ready
            if p1 != 0 and p1 != 1:
                raise Exception('''Invalid Mode Selected:
                         0 = Ignore DC Ready Mode Disabled
                         1 = Ignore DC Ready Mode Enabled''')
            res = self.gen.create_buffer_cmd(87, '2:2', '1:1', subcommand, p1)
        else:
            print('Subcommand does not exist')
        csrChecker(res)

    # giving AEbus cmd 87 subcommand 22 its own function due to unique parameter numbers
    def setInternalDataLogEvent(self, p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, d):
        events = [1, 2, 3, 5, 6, 7, 11, 12, 13, 14, 15, 18, 20, 21, 25, 26, 28, 29, 30, 31, 32, 33, 34, 35]
        if p1 not in events:
            raise Exception('Invalid event number!')
        if p2 != 0 and p2 != 1:
            raise Exception('Invalid event enable state!')
        if p3 != 0 and p3 != 1 and p3 != 2:
            raise Exception('Invalid log event polarity!')
        if p4 != 0 and p4 != 1 and p4 != 2:
            raise Exception('Invalid list control selection!')
        if p5 not in [x for x in range(5)]:
            raise Exception('Invalid event time base!')
        if p6 not in [x for x in range(101)]:
            raise Exception('Invalid pre-trigger events number!')
        if p7 not in [x for x in range(101)]:
            raise Exception('Invalid post-trigger events number!')
        if p8 != 0 and p8!= 1 and p8 != 2:
            raise Exception('Invalid event log mode!')
        if p9 not in [x for x in range(128, 256)]:
            raise Exception('Invalid report command number!')
        if p10 not in [x for x in range(17)]:
            raise Exception('Invalid data length!')
        res = self.gen.create_buffer_cmd(87, '1:2,10:1,1:' + str(p10), '1:1', 22, p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, d)
        csrChecker(res)

    def writeEEPROM(self, size, location, modID, blockOffset, calibratedValStatus, data):
        if type(data) is int and data >= 0:
            bytesList = []
            hexform = hex(data).replace('0x', '')
            if len(hexform) % 2 != 0:
                hexform = '0' + hexform
            currentByte = ''
            count = 0
            for digit in hexform:
                currentByte += digit
                count += 1
                if count > 1:
                    bytesList.append(int(currentByte, 16))
                    currentByte = ''
                    count = 0
            while size > len(bytesList):
                bytesList.append(0)
            bytesList.reverse()
            parameterList = [90, '255:1', '0:0', location, size]
            for x in range(size):
                parameterList.append(bytesList[x])
            while len(parameterList) < 253:
                parameterList.append(0)
            parameterList.append(modID)
            parameterList.append(blockOffset)
            parameterList.append(calibratedValStatus)
            parameterList.append(3)
            parameterList.append(0)
            res = self.gen.create_buffer_cmd(*parameterList)
            csrChecker(res)
        else:
            raise Exception('Sorry datatypes other than positive integers are not supported yet!')

    def writeEEPROM_alt(self, data): #size, location, modID, blockOffset, calibratedValStatus, data):
        # byte = int(data/256)
        # remainder = data - (byte*256)
        parameterList = [90, '255:1', '0:0']#, location, size]
        # parameterList.append(remainder)
        # parameterList.append(byte)
        # while len(parameterList) < 253:
        #     parameterList.append(0)
        # parameterList.append(modID)
        # parameterList.append(blockOffset)
        # parameterList.append(calibratedValStatus)
        # parameterList.append(3)
        # parameterList.append(0)
        for n in range(len(data)):
            parameterList.append(data[n])
        print(len(parameterList))
        print(parameterList)
        res = self.gen.create_buffer_cmd(*parameterList)
        csrChecker(res)

    def selectOpenClosedLoop(self, mode):   # VERIFIED
        if self.isRFon():
            raise Exception('Cannot do this while RF is on!')
        if mode != 0 and mode != 1:
            raise Exception('''Invalid Mode Selected:
                     0 = Open Loop Selected
                     1 = Closed Loop Selected''')
        res = self.gen.create_buffer_cmd(92, '1:1', '1:1', mode)
        csrChecker(res)

    def setPulsingFrequency(self, freq):    # VERIFIED
        if freq not in [x for x in range(2,100001)] and freq != 0:
            raise Exception('Invalid Frequency Chosen: 2 - 100,000')
        res = self.gen.create_buffer_cmd(93, '1:4', '1:1', freq)
        csrChecker(res)

    def setDiagnosticCommand(self, mode):
        if self.reportActiveControlMode() != 8:
            raise Exception('Must be in External (diagnostic) control mode!')
        if self.isRFon():
            raise Exception('Cannot do this while RF is on!')
        if mode != 0 and mode != 1:
            raise Exception('''Invalid Mode Selected:
                     0 = Disables Diagnostics Mode
                     1 = Enables Diagnostics Mode and Start Diagnostics''')
        res = self.gen.create_buffer_cmd(95, '1:1', '1:1', mode)
        csrChecker(res)

    def setPulsingDutyCycle(self, dc):      # VERIFIED
        if dc not in [x for x in range(100)]:
            raise Exception('Invalid Duty Cycle Chosen: 0 - 99')
        res = self.gen.create_buffer_cmd(96, '1:2', '1:1', dc)
        csrChecker(res)

    def setMeasurementCorrectionFactor(self, memLocation, matrix, realK11, realK12, realK21, realK22, reacK11, reacK12, reacK21, reacK22):
        if self.isRFon():
            raise Exception('Cannot do this while RF is on!')
        res = self.gen.create_buffer_cmd(98, '2:1,8:2', '1:1', memLocation, matrix,
                                         realK11, realK12, realK21, realK22, reacK11, reacK12, reacK21, reacK22)
        csrChecker(res)

    def setPinNumber (self, pin): #FIXME VFP says command not implemented
        pin = str(pin)
        parts = list(pin)
        if len(parts) != 31:
            raise Exception('Invalid number of PIN digits (must be 31)!')
        for digit in range(len(parts)):
            parts[digit] = ord(parts[digit])
        res = self.gen.create_buffer_cmd(99, '32:2', '1:1', parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6], parts[7], parts[8],
                                                      parts[9], parts[10], parts[11], parts[12], parts[13], parts[14], parts[15], parts[16],
                                                      parts[17], parts[18], parts[19], parts[20], parts[21], parts[22], parts[23], parts[24],parts[25],
                                                      parts[26], parts[27], parts[28], parts[29], parts[30], 0)
        csrChecker(res)

    def setUnlockedSubcommand (self, subcommand, param1 = None, param2 = None, param3 = None):    #FIXME NEED TESTED
        if subcommand == 1: # set frequency step minimum    #VERIFIED
            if param1 < 10 or param1 > 96000 or isinstance(param1, int) != True:
                raise Exception('Invalid frequency step size!')
            if param1 > self.reportUnlockedSubCommand(2, 0):
                raise Exception('Must be less than the maximum!')
            res = self.gen.create_buffer_cmd(118, '1:2,1:4', '1:1', subcommand, param1)
        elif subcommand == 2:   # set frequency step maximum    #VERIFIED
            if param1 >= 200000:
                raise Exception('Invalid frequency step size!')
            if param1 < self.reportUnlockedSubCommand(1, 0):
                raise Exception('Must be greater than the minimum!')
            res = self.gen.create_buffer_cmd(118, '1:2,1:4', '1:1', subcommand, param1)
        elif subcommand == 3:   # set step up gain      #VERIFIED
            if param1 not in [x + 1 for x in range(7)]:
                raise Exception('Invalid gain!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 4:   # set step down gain    #VERIFIED
            if param1 not in [x + 1 for x in range(7)]:
                raise Exception('Invalid gain!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 6:   # set gamma threshold high  #VERIFIED
            if param1 > 3000:
                raise Exception('Invalid threshold value!')
            if param1 < self.reportUnlockedSubCommand(7,0):
                raise Exception('Must be greater than low threshold!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 7:   # set gamma threshold low
            if param1 > self.reportUnlockedSubCommand(6,0):     #VERIFIED
                raise Exception('Must be less than high threshold!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 8:   # set maximum tuning count
            if param1 < 0 or param1 > 65535 or isinstance(param1, int) != True:
                raise Exception('Invalid tuning count!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 15:  # set ignition mode #VERIFIED
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if param1 not in [x for x in range(4)]:
                raise Exception('Invalid mode!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 16:  # set CEX lock mode     #VERIFIED
            if param1 not in [x for x in range(4)]:
                raise Exception('Invalid mode!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 17: # set tuning scan step size  #VERIFIED
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if param1 < 5296 or param1 > 193353 or isinstance(param1, int) != True:
                raise Exception('Invalid step size!')
            res = self.gen.create_buffer_cmd(118, '1:2,1:4', '1:1', subcommand, param1)
        elif subcommand == 20:  # set CEX output mode   #VERIFIED
            if param1 not in [0, 1, 2]:
                raise Exception('Invalid mode!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 21:  # set gamma threshold mode  #VERIFIED
            if param1 not in [0, 1]:
                raise Exception('Invalid mode!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 22:  # set tuning step time      #VERIFIED
            if param1 < 8 or param1 > 4096 or isinstance(param1, int) != True:
                raise Exception('Invalid step time!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 23:  # set tuning gain delay     #VERIFIED
            if param1 not in [x for x in range(8)]:
                raise Exception('Invalid delay!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 38:  # set match interface mode  #VERIFIED
            if param1 not in [0, 1, 2]:
                raise Exception('Invalid mode!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 43: # set IMD filter frequency   #VERIFIED
            if ((param1 < 325 or param1 > 461) and param1 != 0) or isinstance(param1, int) != True:
                raise Exception('Invalid frequency!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 44:  # CEX output phase offset   #VERIFIED
            if param1 < 0 or param1 > 3600 or isinstance(param1, int) != True:
                raise Exception('Invalid phase!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 45:  # set user power correction     #VERIFIED
            if self.userPowerCorrectionMinEEPROM() == 0 and self.userPowerCorrectionMaxEEPROM() == 0:
                raise Exception('Feature not available!')
            if param1 < self.userPowerCorrectionMinEEPROM() or param1 > self.userPowerCorrectionMaxEEPROM():
                raise Exception('Invalid power correction coefficient!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 47:  # set auto-stability configuration  #VERIFIED
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if param1 == 1:
                if param2 not in [x for x in range(5)]:
                    raise Exception('Invalid mode number!')
            elif param1 == 2:
                if param2 < 0 or param2 > 65535 or isinstance(param2, int) != True:
                    raise Exception('Invalid delay!')
            elif param1 == 3:
                if param2 < 0 or param2 > 3000 or isinstance(param2, int) != True:
                    raise Exception('Invalid threshold!')
            elif param1 == 4:
                if param2 < 0 or param2 > 3000 or isinstance(param2, int) != True:
                    raise Exception('Invalid threshold!')
            elif param1 == 5:
                if param2 < 0 or param2 > 9929 or isinstance(param2, int) != True:
                    raise Exception('Invalid rate!')
            elif param1 == 6:
                if param2 < 1 or param2 > 256 or isinstance(param2, int) != True:
                    raise Exception('Invalid step size!')
            elif param1 == 7:
                if param2 < 0 or param2 > 4096 or isinstance(param2, int) != True:
                    raise Exception('Invalid threshold!')
            elif param1 == 8:
                if param2 < 0 or param2 > 4096 or isinstance(param2, int) != True:
                    raise Exception('Invalid threshold!')
            else:
                raise Exception('Invalid auto-stability config subcommand!')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, param1, param2)
        elif subcommand == 51:  # set fixed frequency memory mode   #VERIFIED
            if param1 not in [0, 1]:
                raise Exception('Invalid mode!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 52:  # set frequency tuning mode     #VERIFIED
            if param1 == 0:
                if param2 not in [0, 1, 2]:
                    raise Exception('Invalid tuning mode!')
            elif param1 == 1:
                if param2 not in [0, 1]:
                    raise Exception('Invalid tuning mode!')
            else:
                raise Exception('Invalid tuning mode command!')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, param1, param2)
        elif subcommand == 53:  # Set User Readback Filter Configuration    #VERIFIED
            if param1 not in [0, 1]:
                raise Exception('Invalid enable state!')
            if param2 < 7 or param2 > 432537 or isinstance(param2, int) != True:
                raise Exception('Invalid time constant!')
            res = self.gen.create_buffer_cmd(118, '1:2,1:1,1:4', '1:1', subcommand, param1, param2)
        elif subcommand == 59: # set configurable generator snapshot config     #VERIFIED
            # ***for subcommand 59, send index values as a list***
            for index in param1:
                if index < 1 or index > 90 or isinstance(index, int) != True:
                    raise Exception('Invalid index number detected!')
            paddedList = [] # have to modify input list so there is a zero after every index (2-byte values)
            for i in range(len(param1)):
                paddedList.append(param1[i])
                paddedList.append(0)
            if len(param1) > 0 and len(param1) < 60:
                res = self.gen.create_buffer_cmd(118, '2:2,' + str(len(paddedList)) +':b', '1:1', subcommand, 1, paddedList)
            else:
                raise Exception('Invalid number of parameters!')
        elif subcommand == 62:  #set fast ignition sweep time   #VERIFIED
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if param1 < 100 or param1 > 2000 or isinstance(param1, int) != True:
                raise Exception('Invalid time!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 63:  #set fast ignition ramp time       #VERIFIED
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if param1 < 100 or param1 > 2000 or isinstance(param1, int) != True:
                raise Exception('Invalid time!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 66:  # set reflected power timer     #VERIFIED
            if param1 in [1, 2, 5, 6, 7]:
                if param1 == 1 and param2 not in [x for x in range(3601)]:
                    raise Exception('Invalid Reflected Power Fault Timeout!')
                if param1 == 2 and param2 not in [x for x in range(10,self.reportUserReflectedLimit()+1)]:
                    raise Exception('Invalid reflected power fault threshold!')
                if param1 == 5 and param2 not in [x for x in range(10,self.reportUserReflectedLimit()+1)]:
                    raise Exception('Invalid reflected power warning threshold!')
                if param1 == 6 and param2 != 0:
                    raise Exception('Invalid parameter, must equal 0')
                if param1 == 7 and param2 != 0:
                    raise Exception('Invalid parameter, must equal 0')
                res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, param1, param2)
            elif param1 in [3,4]:
                if param2 > 3600000 or param2 < 0 or isinstance(param2, int) != True:
                    raise Exception('Invalid timeout entered')
                res = self.gen.create_buffer_cmd(118, '2:2,1:4', '1:1', subcommand, param1, param2)
            else:
                raise Exception('Invalid Reflected Power Timer Sub-Command Number!')
        elif subcommand == 67:  #set sweep auto frequency mode
            if param1 not in [0,1]:
                raise Exception('Invalid mode')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 69:  #Set Pulsing Measurement Warning Threshold
            if param1 not in [1,2]:
                raise Exception('Invalid Pulsing Measurement Warning Threshold Selection')
            if param2 not in [x for x in range(101)]:
                raise Exception('Invalid percentage')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, param1, param2)
        elif subcommand == 70:  #Set Pulsing Measurement Errors and Warnings
            if param1 != 2:
                raise Exception('Invalid Pulsing Measurement Errors and Warnings Sub-Command Number!')
            if param2 not in [0, 1, 2, 3 ,4]:
                raise Exception('Invalid measurement selection!')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, param1, param2)
        elif subcommand == 72:  #Set User Card Low Scale Enable Mode
            if self.reportPINNumber()[8] == '8' and self.reportActiveControlMode() != 2:
                raise Exception('Unit must be in host mode with this user card configuration to use this mode!')
            if self.reportProcessStatus()[0][5] != '0':
                raise Exception('Cannot do this while a recipe is active!')
            if param1 not in [0,1]:
                raise Exception('Invalid enable state!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 73:  # Set Statistics Pulse Mask Time
            if param1 == 0:
                if param2 not in [0,1]:
                    raise Exception('Invalid Mask Time Mode')
            if param1 == 1:
                if param2 < 0 or param2 > 10000 or isinstance(param2, int) != True:
                    raise Exception('Invalid time entered!')
            if param1 == 3:
                if param2 not in [x for x in range(100)]:
                    raise Exception('Invalid percentage entered!')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, param1, param2)
        elif subcommand == 74:  # Set Statistics Sample Rate
            if param1 != 1:
                raise Exception('Invalid Statistics Sample Rate Selection')
            if param2 < 1 or param2 > 125 or isinstance(param2, int) != True:
                raise Exception('Invalid sample rate!')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, param1, param2)
        elif subcommand == 75:  #Set Statistics Sample Count
            if param1 not in [1,2]:
                raise Exception('Invalid Statistics Sample Count Selection')
            if param2 < 1 or param2 > 4096 or isinstance(param2, int) != True:
                raise Exception('Invalid Statistics Sample Count Value')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, param1, param2)
        elif subcommand == 76: # Set Fast Ignition Tuning Parameters
            if param1 == 1:
                if param2 < 0 or param2 > 60000 or isinstance(param2, int) != True:
                    raise Exception('Invalid start delay time')
            elif param1 == 2:
                if param2 < 0 or param2 > 65535 or isinstance(param2, int) != True:
                    raise Exception('Invalid max tuning count')
            elif param1 == 3:
                if param2 < 8 or param2 > 4096 or isinstance(param2, int) != True:
                    raise Exception('Invalid tuning step time')
            elif param1 == 4:
                if param2 < 10 or param2 > 96000 or isinstance(param2, int) != True:
                    raise Exception('Invalid step minimum')
                if param2 > self.reportUnlockedSubCommand(76,5):
                    raise Exception('Cannot be greater than the step maximum')
            elif param1 == 5:
                if param2 > 200000:
                    raise Exception('Invalid step maximum')
                if param2 < self.reportUnlockedSubCommand(76,4):
                    raise Exception('Cannot be less than the step minimum')
            else:
                raise Exception('Invalid tuning parameter number')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, param1, param2)
        elif subcommand == 77:  # Set Pulsing Measurement Delays
            if param2 < param1:
                raise Exception('Requested maximum is less than requested minimum!')
            if param3 != 25 and (param3/2) < param2:
                raise Exception('Inflection point must be greater than or equal to twice the maximum delay')
            if param3 == 25 and 20 < param2:
                raise Exception('Inflection point at minimum value must be at least 5uS greater than the maximum delay')
            if param1 not in [1, 2, 3, 4]:
                raise Exception('Invalid minimum measurement delay')
            if param2 < 1 or param2 > 496 or isinstance(param2, int) != True:
                raise Exception('Invalid maximum measurement delay')
            if param3 < 25 or param3 > 1000 or isinstance(param3, int) != True:
                raise Exception('Invalid inflection point')
            res = self.gen.create_buffer_cmd(118, '4:2', '1:1', subcommand, param1, param2, param3)
        elif subcommand == 78:  # Set Sweep Frequency Step Direction
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if param1 not in [0,1]:
                raise Exception('Invalid direction number selection')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 79:  # set control loop gain
            if param1 not in [x for x in range(49,501)]:
                raise Exception('Invalid gain!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 81:  #Set Sweep Gamma Average Mask Enable
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if param1 not in [0,1]:
                raise Exception('Invalid enable state!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 83:  # Set Sweep Skip Frequency Range
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if (param3 - param2) > 200:
                raise Exception('Range is too large!')
            if (param3 - param2) < 0:
                raise Exception('Maximum must be greater than or equal to minimum!')
            min = int(self.reportFixedFrequency(3) / 1000)
            max = (int(self.reportFixedFrequency(2) / 1000)) + 1
            if param2 not in [x for x in range(min, max)]:
                raise Exception('Invalid minimum frequency')
            if param3 not in [x for x in range(min, max)]:
                raise Exception('Invalid maximum frequency')
            res = self.gen.create_buffer_cmd(118, '2:2,2:4', '1:1', subcommand, 1, param2, param3)
        elif subcommand == 86:
            if param1 not in [1,2]:
                raise Exception('Invalid mode!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 93:  # Set Fixed Frequency Control
            if param2 != 0 and param2 not in [x for x in range(100,60001)]:
                raise Exception('Invalid ramp time')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, 1, param2)
        elif subcommand == 94:  # set frequency tuning mask time
            if param2 < 0 or param2 > 99 or isinstance(param2, int) != True:
                raise Exception('Invalid percentage')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, 1, param2)
        elif subcommand == 117:  # Set Setpoint Change Masking Time
            if param2 < 0 or param2 > 1000 or isinstance(param2, int) != True:
                raise Exception('Invalid mask time')
            res = self.gen.create_buffer_cmd(118, '3:2', '1:1', subcommand, 1, param2)
        elif subcommand == 126:  # Set Pulsing Frequency/Duty Cycle Ramp Time
            if param1 < 100 or param1 > 10000 or isinstance(param1, int) != True:
                raise Exception('Invalid mask time')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 127:  # Set Pulsing Frequency/Duty Cycle Combo
            if param1 < 2 or param1 > 100000 or isinstance(param1, int) != True:
                raise Exception('Invalid pulsing frequency')
            if param2 < 0 or param2 > 99 or isinstance(param2, int) != True:
                raise Exception('Invalid percentage')
            if ((1/param1)*(0.01*param2)) < 0.000005 or ((1/param1)*(1-(0.01*param2))) < 0.000005:
                raise Exception('Pulsing frequency/duty cycle combo creates on or off time of less than 5us!')
            res = self.gen.create_buffer_cmd(118, '1:2,1:4,1:2', '1:1', subcommand, param1, param2)
        elif subcommand == 300:  # Set RF Bias DAC Manual Mode
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if param1 not in [0,1]:
                raise Exception('Invalid mode number')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        elif subcommand == 301:  #  Set RF Bias DAC Offset
            if self.reportOpenCloseLoopOperation() == 'Closed Loop':
                raise Exception('Unit must be in open control loop to do this!')
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if self.reportUnlockedSubCommand(300, 0) != 1:
                raise Exception('Unit not in RF Bias DAC Manual Mode!')
            if param1 < 0 or param1 > (self.reportLockedSubCommand(28, 1, 0) - self.autoStabilityTableCoeff0EEPROM()):
                raise Exception('Invalid offset value entered!')
            res = self.gen.create_buffer_cmd(118, '2:2', '1:1', subcommand, param1)
        else:
            raise Exception('Subcommand does not exist')
        csrChecker(res)

    def explicitFaultClear(self):
        res = self.gen.create_buffer_cmd(119, '0:0', '1:1')

    def setMatchSubCommand(self): #FIXME Need more info; refer to FW00266
        raise Exception('Not yet implemented')

    def resetStatistics(self, statIndex):
        res = self.gen.create_buffer_cmd(126, '1:2', '1:1', statIndex)

    def reportPowerSupplyType(self, opt = None):  # VERIFIED
        if opt is None:
            res = self.gen.create_buffer_cmd(128, '0:0', '9:1')[2:]
            typeString = ''
            for i in range(len(res)):
                typeString += chr(res[i])
        elif opt == 1:
            res = self.gen.create_buffer_cmd(128, '0:0', '10:1', 3)[2:]
            typeString = ''
            for i in range(len(res)):
                typeString += chr(res[i])
        else:
            raise Exception('Invalid option, must be blank for 9 byte response or 1 for 10 byte response.')
        return typeString

    def reportPowerSupplySize(self, opt = None):   #VERIFIED
        if opt == 1: #returns Low Power Limit in 6 ascii digits
            res = self.gen.create_buffer_cmd(129, '1:1', '6:1', 1)
            csrChecker(res)
            res = res[2:]
        elif opt == None: # returns power supply size in 6 ascii digits
            res = self.gen.create_buffer_cmd(129, '1:1', '6:1', 0)
            csrChecker(res)
            res = res[2:]
        else:
            raise Exception('Invalid Report Mode Chosen: 1 or None')
        return int(''.join(chr(i) for i in res).strip())

    #BONUS COMMAND
    def reportLowPowerLimit(self):      # VERIFIED
        res = self.gen.create_buffer_cmd(129, '1:1', '6:1', 1)
        csrChecker(res)
        res = res[2:]
        return int(''.join(chr(i) for i in res).strip())

    def reportSoftwarePartNumber(self, module = None):  #VERIFIED
        options = [0, 3, 4, 5, 6, 17, 18]
        if module is None:
            module = 0
        if module not in options:
            raise Exception('''Invalid Module Selected:
                     0 = Coldfire Application Firmware Part Number
                     3 = Coldfire Bootloader Firmware Part Number
                     4 = Communications Card Application Firmware Part Number
                     5 = Communications Card Bootloader Firmware Part Number
                     6 = Communications Card FPGA Firmware Part Number
                     17 = Communications Card File 1
                     18 = Communications Card File 2  ''')
        res = self.gen.create_buffer_cmd(130, '1:1', '7:1', module)
        csrChecker(res)
        res = res[2:]
        if len(res) == 1:
            raise Exception('Part number not available!')
        pnString = ''
        for i in range(len(res)):
            pnString += chr(res[i])
        return pnString

    def reportCEXOffset(self):      #VERIFIED
        res = self.gen.create_buffer_cmd(132, '1:1', '1:2', 0)
        csrChecker(res)
        offset = res[2]/10
        return offset

    def reportTuningTimeoutValue(self):  # VERIFIED
        res = self.gen.create_buffer_cmd(138, '0:0', '1:4')
        csrChecker(res)
        timeout = res[2]
        return timeout

    def reportCommWatchDogTimer(self):  #VERIFIED
        res = self.gen.create_buffer_cmd(139, '1:1', '1:2', 0)
        csrChecker(res)
        timer = res[2]
        return timer

    def reportHostTimeout(self):
        raise Exception('Host timeout functions are only viable when using serial port communications!')

    def reportMinimumTuningFrequency(self):  # VERIFIED
        res = self.gen.create_buffer_cmd(144, '0:0', '1:4')
        csrChecker(res)
        minTuning = res[2]
        return minTuning

    def reportMaximumTuningFrequency(self):     # VERIFIED
        res = self.gen.create_buffer_cmd(145, '0:0', '1:4')
        csrChecker(res)
        maxTuning = res[2]
        return maxTuning

    def reportTuningStartFrequency(self):  # VERIFIED
        res = self.gen.create_buffer_cmd(146, '0:0', '1:4')
        csrChecker(res)
        tuningStart = res[2]
        return tuningStart

    def reportGeneratorActualFrequency(self):   #VERIFIED
        res = self.gen.create_buffer_cmd(147, '0:0', '1:4')
        csrChecker(res)
        actualFreq = res[2]
        return actualFreq

    def reportFrequencyMode(self, value = None):    #VERIFIED
        if value != None:
            if value == 0:
                res = self.gen.create_buffer_cmd(148, '1:1', '1:2', value)
                csrChecker(res)
            else:
                raise Exception('Invalid Reporting Mode Chosen; 0 or Blank')
        else:
            res = self.gen.create_buffer_cmd(148, '0:0', '1:1')
            csrChecker(res)
        freqMode = res[2]  # 0 = Fixed, 1 = Variable
        return freqMode

    def reportSetpointRampingConfiguration(self, length, config):  #VERIFIED
        if length == 5:
            res = self.gen.create_buffer_cmd(151, '1:1', '1:1,2:2', 1)
            csrChecker(res)
            rampMode = res[2]
            upRamp = res[3]
            downRamp = res[4]
            return rampMode, upRamp, downRamp
        if length == 6:
            res = self.gen.create_buffer_cmd(151, '0:0', '3:2')
            csrChecker(res)
            rampMode = res[2]
            upRamp = res[3]
            downRamp = res[4]
            return rampMode, upRamp, downRamp
        if length == 8:
            if config == 1:
                res = self.gen.create_buffer_cmd(151, '1:2', '4:2', 1)
                csrChecker(res)
                rampMode = res[3]
                upRamp = res[4]
                downRamp = res[5]
                return rampMode, upRamp, downRamp
            if config == 2:
                res = self.gen.create_buffer_cmd(151, '1:2', '4:2', 2)
                csrChecker(res)
                memMode = res[3]
                return memMode
            if config == 5:
                res = self.gen.create_buffer_cmd(151, '1:2', '4:2', 5)
                csrChecker(res)
                configMode = res[3]
                return configMode

    def reportRegulationMode(self):     #VERIFIED
        if self.reportActiveControlMode() != 2:
            raise Exception('Unit not in host mode!')
        res = self.gen.create_buffer_cmd(154, '1:1', '1:2', 0)
        csrChecker(res)
        regMode = res[2]
        '''6 = Forward
           7 = Delivered
           8 = External (DC Bias)
           9  = VA Limit'''
        return regMode

    def reportActiveControlMode(self):  #VERIFIED
        res = self.gen.create_buffer_cmd(155, '1:1', '1:2', 0)
        csrChecker(res)
        controlMode = res[2]
        '''2:Host
            4:User Port (Analog)
            8:Diagnostic Mode
            16:DeviceNet
            32:EtherCAT'''
        return controlMode

    def reportRetuningThreshold(self):  #VERIFIED
        res = self.gen.create_buffer_cmd(158, '0:0', '1:4')
        csrChecker(res)
        retuningThreshold = res[2]
        return retuningThreshold

    def reportTuningTime(self):     #VERIFIED
        res = self.gen.create_buffer_cmd(159, '0:0', '1:4')
        csrChecker(res)
        retuningTime = res[2]
        return retuningTime

    def reportTuneDelay(self):  #VERIFIED
        res = self.gen.create_buffer_cmd(160, '0:0', '1:4')
        csrChecker(res)
        tuneDelay = res[2]
        return tuneDelay

    def reportFixedFrequency(self, value = None): #VERIFIED
        if value == 0:
            res = self.gen.create_buffer_cmd(161, '1:1', '1:4', 0)
            csrChecker(res)
            fixedFrequency = res[2]
        elif value == 1:
            res = self.gen.create_buffer_cmd(161, '1:1', '1:4', 1)
            csrChecker(res)
            fixedFrequency = res[2]
        elif value == 2:
            res = self.gen.create_buffer_cmd(161, '1:1', '1:4', 2)
            csrChecker(res)
            fixedFrequency = res[2]
        elif value == 3:
            res = self.gen.create_buffer_cmd(161, '1:1', '1:4', 3)
            csrChecker(res)
            fixedFrequency = res[2]
        elif value == 8:
            res = self.gen.create_buffer_cmd(161, '1:1', '1:4', 8)
            csrChecker(res)
            fixedFrequency = res[2]
        elif value == None:
            res = self.gen.create_buffer_cmd(161, '0:0', '1:4')
            csrChecker(res)
            fixedFrequency = res[2]
        else:
            raise Exception('''Invalid Report Mode Chosen:'
                     0 = Generator Frequency [kHz]
                     1 = Generator Frequency [Hz]
                     2 = Generator Max Frequency [Hz]
                     3 = Generator Min Frequency [Hz]
                     8 = Generator Center Frequency [Hz]''')
        return fixedFrequency

    def reportProcessStatus(self):      #VERIFIED
        res = self.gen.create_buffer_cmd(162, '0:0', '4:1')
        csrChecker(res)
        res = res[2:]
        sB1 = '{0:08b}'.format(res[0])
        sB2 = '{0:08b}'.format(res[1])
        sB3 = '{0:08b}'.format(res[2])
        sB4 = '{0:08b}'.format(res[3])
        status = [sB1, sB2, sB3, sB4]
        return status
        #note: first byte in list byte is d0, then d1, etc.

    # BONUS COMMAND!
    def isRFon(self):       #VERIFIED
        if self.reportProcessStatus()[0][2] == '1':
            return True
        else:
            return False

    def reportFanTachometerRegister(self, value1, value2 = None):   #VERIFIED
        if (value1 > 3 or value1 < 0) and value2 is not None:
            raise Exception('Invalid Fan Selected: Must be 0, 1, 2, 3')
        if value2 is not None and (value2 not in [0, 1]):
            raise Exception('Invalid Reporting Mode Selected: Must be 0 or 1')
        if value2 is None and value1 not in [0, 2]:
            raise Exception('Invalid Reporting Mode Selected: Must be 0 or 2')
        if value2 is None:
            res = self.gen.create_buffer_cmd(163, '1:1', '1:2', value1)
        else:
            res = self.gen.create_buffer_cmd(163, '2:1', '1:2', value1, value2)
        csrChecker(res)
        tach = res[2]
        return tach

    def reportSetpointandRegulationMode(self):  #VERIFIED
        res = self.gen.create_buffer_cmd(164, '0:0', '1:2,1:1')
        csrChecker(res)
        setpoint = res[2]
        regulationMode = res[3]
        if self.isHaloEnabled():
            setpoint = round(setpoint*0.1, 1)
        return setpoint, regulationMode

    def reportForwardPower(self):       #VERIFIED
        res = self.gen.create_buffer_cmd(165, '0:0', '1:2')
        csrChecker(res)
        forwardPower = res[2]
        if self.isHaloEnabled():
            forwardPower = round(forwardPower*0.1, 1)
        return forwardPower

    def reportReflectedPower(self):     #VERIFIED
        res = self.gen.create_buffer_cmd(166, '0:0', '1:2')
        csrChecker(res)
        reflectedPower = res[2]
        if self.isHaloEnabled():
            reflectedPower = round(reflectedPower*0.1, 1)
        return reflectedPower

    def reportDeliveredPower(self):  #VERIFIED
        res = self.gen.create_buffer_cmd(167, '0:0', '1:2')
        csrChecker(res)
        deliveredPower = res[2]
        if self.isHaloEnabled():
            deliveredPower = round(deliveredPower*0.1, 1)
        return deliveredPower

    def reportExternalFeedback(self):   #VERIFIED
        res = self.gen.create_buffer_cmd(168, '0:0', '1:4')
        csrChecker(res)
        externalFeedback = res[2]
        return externalFeedback

    def reportUserPowerLimit(self):     # VERIFIED
        res = self.gen.create_buffer_cmd(169, '0:0', '1:2')
        csrChecker(res)
        userPowerLimit = res[2]
        return userPowerLimit

    def reportUserReflectedLimit(self, mode = None):    # VERIFIED
        if mode == 0 or mode is None:   # user reflected power limit
            res = self.gen.create_buffer_cmd(170, '1:1', '1:2', 0)
            csrChecker(res)
            userReflectedLimit = res[2]
            return userReflectedLimit
        elif mode == 1:  # generator maximum reflected power limit
            res = self.gen.create_buffer_cmd(170, '1:1', '1:2', mode)
            csrChecker(res)
            userReflectedLimit = res[2]
            return userReflectedLimit
        else:
            raise Exception('Invalid mode selection!')

    def reportUserExternalFeedbackLimit(self, mode = None):     #VERIFIED
        if mode == 1:  # Maximum External Feedback Value [V]
            res = self.gen.create_buffer_cmd(171, '1:1', '1:2', mode)
            csrChecker(res)
            userExternalFeebackLimit = res[2]
        elif mode == 0 or mode is None:  # User External Feedback Limit [V]
            res = self.gen.create_buffer_cmd(171, '1:1', '1:2', 0)
            csrChecker(res)
            userExternalFeebackLimit = res[2]
        else:
            raise Exception('Invalid Reporting Mode Selected: 0, 1, or Blank')
        return userExternalFeebackLimit

    def reportPulsingConfiguration(self, selection):    # VERIFIED
        options = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]
        if selection not in options:
            raise Exception('''Invalid Pulsing Configuration Option Selected:
                     1 = Report Pulsing Mode
                     2 = Report Pulse Sync Output
                     3 = Report Pulsing Enable
                     4 = Report Sync Input Delay
                     5 = Report Memory Mode
                     6 = Report Explicit Enable Mode
                     7 = Report Pulsing Enable State
                     8 = Report Pulsing/Arc Management Mode
                     9 = Report Pulse Maximum On Time
                     10 = Report Pulse Sync Output Mode
                     11 = Report SLave Duty Cycle
                     12 = Reserved
                     13 = Reserved
                     14 = Report Minimum Pulsing Frequency
                     15 = Report Maximum Pulsing Frequency
                     16 = Report Minimum Pulsing Duty Cycle
                     17 = Report Maximum Pulsing Duty Cycle''')
        res = self.gen.create_buffer_cmd(172, '1:2', '1:4', selection)
        csrChecker(res)
        paramInterest = res[2]
        return paramInterest

    def reportPowerDisspiation(self):       #VERIFIED
        res = self.gen.create_buffer_cmd(173, '0:0', '1:2')
        csrChecker(res)
        dissipation = res[2]
        return dissipation

    def reportDACState(self, device, channel):
        if device not in [0, 1, 2]:
            raise Exception('Invalid Device Selected: 0, 1, or 2 ')
        if channel not in [0, 1, 2, 3]:
            raise Exception('Invalid Channel Selected: 0, 1, 2, or 3')
        if device == 0 and channel not in [1, 2]:
            raise Exception('Invalid Channel Selected for Device 0: 1 or 2')
        if device == 1 and channel != 0:
            raise Exception('Invalid Channel Selected for Device 1: Must be 0')
        res = self.gen.create_buffer_cmd(174, '2:1', '1:2', device, channel)
        #csrChecker(res)
        dataDAC = res[2]
        return dataDAC

    def reportInputLatchData(self):     #VERIFIED
        res = self.gen.create_buffer_cmd(180, '1:1', '2:1', 0)
        csrChecker(res)
        latchDataByte = bin(res[2])[2:]
        while(len(latchDataByte) < 8):
            latchDataByte = '0' + latchDataByte
        return latchDataByte
        #NOTE: bit 7 is the leftmost bit and bit 0 is the rightmost bit

    def reportSPIBusADCData(self, device):      #VERIFIED
        if device not in [0, 2, 3, 4, 5]:
            raise Exception('''Invalid Device Selected
                     0 = Motherbaord Onboard ADC (Returns 16 bytes)
                     1 = Reserved
                     2 = User Card ADC (Returns 8 bytes)
                     3 = DC Section ADC2 (Returns 32 bytes)
                     4 = DC Section ADC1 (Returns 16 bytes)
                     5 = RFC Board ADC (Returns 4 bytes)''')
        if device == 0:
            res = self.gen.create_buffer_cmd(181, '1:1', '8:2', device)
            csrChecker(res)
            thermistor0 = res[2]
            thermistor1 = res[3]
            thermistor2 = res[4]
            thermistor3 = res[5]
            userCardSetpoint = res[6]
            userCardDCBias = res[7]
            userCardChannel = res[8]
            smokeDetector = res[9]
            return thermistor0,thermistor1, thermistor2, thermistor3, userCardSetpoint, userCardDCBias, userCardChannel, smokeDetector
        elif device == 2:
            res = self.gen.create_buffer_cmd(181, '1:1', '4:2', device)
            csrChecker(res)
            userCardSetpoint = res[2]
            userCardDCBias = res[3]
            userCardChannel = res[4]
            reserved = res[5]
            return userCardSetpoint, userCardDCBias, userCardChannel, reserved
        elif device == 3:
            res = self.gen.create_buffer_cmd(181, '1:1', '16:2', device)
            csrChecker(res)
            priPhaseOne = res[2]
            priPhaseTwo = res[3]
            priPhaseThree = res[4]
            priVbus350 = res[5]
            priExciterOne = res[6]
            priDCWaterTemp = res[7]
            priAmbientTemp = res[8]
            priHeatedTemp = res[9]
            secPhaseOne = res[10]
            secPhaseTwo = res[11]
            secPhaseThree = res[12]
            secVbus350 = res[13]
            secExciterOne = res[14]
            secDCWaterTemp = res[15]
            secAmbientTemp = res[16]
            secHeatedTemp = res[17]
            return priPhaseOne, priPhaseTwo, priPhaseThree, priVbus350, priExciterOne, priDCWaterTemp, priAmbientTemp,\
                   priHeatedTemp, secPhaseOne, secPhaseTwo, secPhaseThree, secVbus350, secExciterOne, secDCWaterTemp,\
                   secAmbientTemp, secHeatedTemp
        elif device == 4:
            res = self.gen.create_buffer_cmd(181, '1:1', '8:2', device)
            csrChecker(res)
            priDVVoltageOut = res[2]
            priHighCurrentOutSense = res[3]
            priDCCurrentOut = res[4]
            priPreRegulatorVoltage = res[5]
            secDCVoltageOut = res[6]
            secHighCurrentOutSense = res[7]
            secDCCurrentOut = res[8]
            secPreRegulatorVoltage = res[9]
            return priDVVoltageOut, priHighCurrentOutSense, priDCCurrentOut, priPreRegulatorVoltage, \
                   secDCVoltageOut, secHighCurrentOutSense, secDCCurrentOut, secPreRegulatorVoltage
        else:
            res = self.gen.create_buffer_cmd(181, '1:1', '2:2', device)
            csrChecker(res)
            rfwaterThermistor0 = res[2]
            rfwaterThermistor1 = res[3]
            return rfwaterThermistor0,  rfwaterThermistor1

    def readVDC(self):
        voltage = self.gen.create_buffer_cmd(207, '2:2', '1:2', 66, 0)
        return voltage[2] / 10.0

    def readIDC(self):
        current = self.gen.create_buffer_cmd(207, '2:2', '1:2', 67, 0)
        return current[2] / 10.0

    def reportMeasurementData(self, Opt):   #VERIFIED
        if Opt not in [0, 1, 2]:
            raise Exception('''Invalid Measurement Option Selected:
                      0 = Selects Uncorrected Voltage Measurements
                      1 = Selects Corrected Voltage Measurements
                      2 = Selects Corrected Power Measurements''')
        res = self.gen.create_buffer_cmd(182, '1:1', '4:4', Opt)
        csrChecker(res)
        reflectedQ = res[2]
        if reflectedQ & (1 << (32 - 1)):
            reflectedQ -= 1 << 32
        reflectedI = res[3]
        if reflectedI & (1 << (32 - 1)):
            reflectedI -= 1 << 32
        forwardQ = res[4]
        if forwardQ & (1 << (32 - 1)):
            forwardQ -= 1 << 32
        forwardI = res[5]
        if forwardI & (1 << (32 - 1)):
            forwardI -= 1 << 32
        return reflectedQ, reflectedI, forwardQ, forwardI

    def reportOutputLatchState(self):   #VERIFIED
        res = self.gen.create_buffer_cmd(183, '1:1', '2:1', 0)
        csrChecker(res)
        latchDataByte = bin(res[2])[2:]
        while(len(latchDataByte) < 8):
            latchDataByte = '0' + latchDataByte
        return latchDataByte
        #NOTE: bit 7 is the leftmost bit and bit 0 is the rightmost bit

    def reportRecipeParameters(self, recipeNumber, paramType, recipeStep, paramSelect):     #VERIFIED
        if recipeNumber > 15 or recipeNumber < 1 or isinstance(recipeNumber, int) != True:
            raise Exception('Invalid Recipe Number Selected: 1-15')
        if paramType not in [0, 1]:
            raise Exception('''Invalid Recipe Parameter Type Selected:
                     0 = Report Global Recipe Parameters
                     1 = Report Recipe Step Parameters''')
        if recipeStep == 1:
            if paramType != 1:
                raise Exception('Report Recipe Step Parameters NOT Enabled')
        if paramType == 0:
            if paramSelect not in [0, 1, 2]:
                raise Exception('Not a valid global recipe parameter!')
        if paramType == 1:
            if paramSelect not in [0, 1, 2, 3, 4, 5, 6]:
                raise Exception('Not a valid recipe step parameter!')
        res = self.gen.create_buffer_cmd(188, '4:1', '1:4', recipeNumber, paramType, recipeStep, paramSelect)
        csrChecker(res)
        res = res[2]
        return res

    def reportRecipeStatus(self):       #VERIFIED
        res = self.gen.create_buffer_cmd(189, '1:1', '1:1,1:2,2:1', 1)
        csrChecker(res)
        activeRecipe = res[2]
        currentRecipeIteration = res[3]
        currentReciptStep = res[4]
        statusFlags = res[5]
        if bin(statusFlags)[(len(bin(statusFlags)[2:])-1)] == '1':
            returnState = 'Recipe Running'
        else:
            returnState = 'Recipe Not Running'
        return activeRecipe, currentRecipeIteration, currentReciptStep, returnState

    def readEEPROM(self, address, offset):  #VERIFIED
        res = self.gen.create_buffer_cmd(190, '3:1', '255:1', address, offset, 3)
        csrChecker(res)
        res = res[2:]
        for i in range(len(res)):
            res[i] = hex(res[i])
            res[i] = res[i][2:] # removes the '0x' so that combining bytes is simpler
            if len(res[i]) == 1:
                res[i] = '0' + res[i]   #ensures that the byte has two charcaters so combining bytes is simpler
        # response is a list of hexadecimal bytes in string form
        return res

    def reportOpenCloseLoopOperation(self):     #VERIFIED
        res = self.gen.create_buffer_cmd(192, '1:1', '1:2', 192)
        csrChecker(res)
        mode = res[2]
        if mode == 0:
            opMode = 'Open Loop'
        else:
            opMode = 'Closed Loop'
        return opMode

    def reportPulsingFrequency(self):   #VERIFIED
        res = self.gen.create_buffer_cmd(193, '0:0', '1:4')
        csrChecker(res)
        pulsingFreq = res[2]
        return pulsingFreq

    def reportPulsingDutyCycle(self):   #VERIFIED
        res = self.gen.create_buffer_cmd(196, '0:0', '1:2')
        csrChecker(res)
        pulsingDutyCycle = res[2]
        return pulsingDutyCycle

    def reportSoftwareRevisionLevel(self, module = None):  #VERIFIED
        options = [0, 3, 4, 5, 6, 17, 18]
        if module not in options:
            raise Exception('''Invalid Module Selected:
                     0 = Coldfire Application Firmware Part Number
                     3 = Coldfire Bootloader Firmware Part Number
                     4 = Communications Card Application Firmware Part Number
                     5 = Communications Card Bootloader Firmware Part Number
                     6 = Communications Card FPGA Firmware Part Number
                     17 = Communications Card File 1
                     18 = Communications Card File 2  ''')
        res = self.gen.create_buffer_cmd(198, '1:1', '3:1', module)
        csrChecker(res)
        revision = chr(res[2])
        level = chr(res[3]) + chr(res[4])
        softwareRevision = revision + level
        return softwareRevision

    #BONUS COMMAND
    def reportFullColdfireAppPN(self):  #VERIFIED
        a = self.reportSoftwarePartNumber(0)
        b = self.reportSoftwareRevisionLevel(0)
        return a + b

    def reportArcEvents(self, param):   #VERIFIED
        if param > 14 or param < 1:
            raise Exception('''Invalid Parameter Selected:
                                 1 = Arc Events Per Run
                                 2 = Arc Events Per Second
                                 3 = Arc Suppression Time
                                 4 = Reflected Power Limit Mode
                                 5 = Enable External Arc Input
                                 6 = RF Power Latch State
                                 7 = Arc Output Control Signal
                                 8 = Initial Delay Time
                                 9 = Setpoint Delay Time
                                 10 = Number of Attempts
                                 11 = Reflection Coefficient Window
                                 12 = Cumulative Arc Events 
                                 13 = Gamma Arc Detection Mode
                                 14 = Reflected Power Threshold for Arc Detection''')
        else:
            res = self.gen.create_buffer_cmd(199, '1:1', '1:4', param)
            csrChecker(res)
            arcData = res[2]
            return arcData

    def reportUnitOnEvents(self):   #VERIFIED
        res = self.gen.create_buffer_cmd(201, '0:0', '1:4')
        csrChecker(res)
        events = res[2]
        return events

    def reportOutputOnEvents(self):     #VERIFIED
        res = self.gen.create_buffer_cmd(202, '0:0', '1:4')
        csrChecker(res)
        events = res[2]
        return events

    def reportOvertempEvents(self):     #VERIFIED
        res = self.gen.create_buffer_cmd(203, '0:0', '1:4')
        csrChecker(res)
        events = res[2]
        return events

    def reportSystemControl(self, subcommand, p1, p2):  #VERIFIED
        if subcommand not in [0, 1, 2, 3, 4, 5, 8, 20, 21, 92, 97, 101, 200, 202, 203]:
            raise Exception('Invalid subcommand entered!')
        if subcommand == 0: #report IP address
            res = self.gen.create_buffer_cmd(204, '1:1', '4:1', 0)
            csrChecker(res)
            res = res[2:]
            return str(res[3]) + '.' + str(res[2]) + '.' + str(res[1]) + '.' + str(res[0])
        if subcommand == 1: #report default gateway
            res = self.gen.create_buffer_cmd(204, '1:1', '4:1', 1)
            csrChecker(res)
            res = res[2:]
            return str(res[3]) + '.' + str(res[2]) + '.' + str(res[1]) + '.' + str(res[0])
        if subcommand == 2: #report subnet mask
            res = self.gen.create_buffer_cmd(204, '1:1', '4:1', 2)
            csrChecker(res)
            res = res[2:]
            return str(res[3]) + '.' + str(res[2]) + '.' + str(res[1]) + '.' + str(res[0])
        if subcommand == 3: #report MAC ID
            res = self.gen.create_buffer_cmd(204, '1:1', '6:1', 3)
            csrChecker(res)
            res = res[2:]
            for i in range(len(res)):
                res[i] = hex(res[i])[2:].upper()
                if len(res[i]) == 1:
                    res[i] = '0' + res[i]
            return res[5] + res[4] + res[3] + res[2] + res[1] + res[0]
        if subcommand == 4: #report PHY status
            res = self.gen.create_buffer_cmd(204, '1:1', '4:1', 4)
            csrChecker(res)
            res = res[2:]
            return format(res[0], '08b')
            #NOTE: bit 7 is the leftmost bit and bit 0 is the rightmost bit
        if subcommand == 5: #report DHCP Client Enable (1 byte response)
            res = self.gen.create_buffer_cmd(204, '1:1', '1:1', 5)
            csrChecker(res)
            res = res[2]
            q = ['DHCP Disabled', 'DHCP Enabled']
            return q[res]
        if subcommand == 8: #report DHCP Client Enable (2 byte response)
            res = self.gen.create_buffer_cmd(204, '1:1', '1:2', 8)
            csrChecker(res)
            res = res[2]
            q = ['DHCP Disabled', 'DHCP Enabled']
            return q[res]
        if subcommand == 20:  # report MD5 Pseudo-Random Seed
            res = self.gen.create_buffer_cmd(204, '1:1', '1:4', 20)
            csrChecker(res)
            res = res[2]
            csrChecker(res)
            return res
        if subcommand == 21:  # report MD5 Hash Value
            res = self.gen.create_buffer_cmd(204, '1:1,1:4', '16:1', 21, p1)
            csrChecker(res)
            res = res[2:]
            res.reverse()
            return res
        if subcommand == 92:    #Report Warning or Fault Description
            faultCodes = [16, 17, 18, 21, 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 38, 39, 40, 43, 46, 47, 48, 49,
                          50, 51, 52, 53, 57, 58, 61, 68, 69, 74, 75, 76, 82, 93, 99, 101, 120, 144, 145, 146, 200, 201,
                          300, 301, 302, 303, 304, 305, 30, 307, 313, 314, 316, 318, 319, 320, 325, 326, 327, 328, 329,
                          330, 331, 332, 338, 339, 341, 343, 344, 345, 30, 351, 352, 353, 354, 355, 356, 357, 358, 359,
                          360, 365, 366, 367, 368, 369, 370, 371, 372, 373, 374, 375, 376, 377, 378, 379, 380, 381, 382,
                          383, 384, 385, 386, 389, 393, 397, 398, 4011, 4021, 4031, 4041, 4051, 4061, 4071, 4081, 460,
                          550, 600, 700, 701, 702, 703, 704, 705, 706, 707, 709, 710, 712, 714, 715, 720, 750, 751, 800,
                          801, 802, 803, 804, 805, 806, 807, 808, 809, 810, 811, 812, 813, 814, 815, 816, 817, 818, 819,
                          820, 821, 822, 823, 824, 825, 826, 827, 828, 829, 830, 9982, 9992, 1001, 1002, 10031, 10041,
                          10051, 10061, 10071, 10081, 10091, 10101, 10111, 10121, 10131, 10141, 10151, 10161, 10171,
                          10181]
            warningCodes = [31, 32, 39, 41, 42, 47, 49, 50, 51, 53, 54, 56, 57, 58, 61, 62, 63, 300, 301, 302, 303,
                            304, 305, 320, 321, 322, 323, 324, 325]
            if p1 not in [1, 2, 3, 4]:
                raise Exception('Invalid type selection: must be 1-4')
            if p1 in [1, 3] and p2 not in faultCodes:
                raise Exception('Invalid fault code selection')
            if p1 in [2, 4] and p2 not in warningCodes:
                raise Exception('Invalid warning code selection')
            res = self.gen.create_buffer_cmd(204, '2:1,1:2', '250:1', 92, p1, p2)
            csrChecker(res)
            res = res[2:]
            outString = ''
            for i in range(len(res)):
                outString += chr(res[i])
            return outString
        if subcommand == 97:  #Report CSR description
            csrCodes = [0, 1, 2, 4, 5, 7, 8, 9, 12, 16, 17, 19, 28, 30, 41, 42, 50, 51, 52, 61, 63, 99, 119]
            if p1 not in csrCodes:
                raise Exception('Invalid CSR code selection')
            res = self.gen.create_buffer_cmd(204, '3:1', '250:1', 97, p1, 0)[2:]
            outString = ''
            for i in range(len(res)):
                outString += chr(res[i])
            return outString
        if subcommand == 101: #Report Number of TCP Connections
            res = self.gen.create_buffer_cmd(204, '2:2', '1:2', 101, 502)[2]
            csrChecker(res)
            return res
        if subcommand == 200:
            res = self.gen.create_buffer_cmd(204, '1:1', '250:1', 200)[2:]
            outString = ''
            for i in range(len(res)):
                outString += chr(res[i])
            return outString
        if subcommand == 202: #Report DNS Server IP Address
            res = self.gen.create_buffer_cmd(204, '1:1', '4:1', 202)
            csrChecker(res)
            res = res[2:]
            return str(res[3]) + '.' + str(res[2]) + '.' + str(res[1]) + '.' + str(res[0])
        if subcommand == 203: #Report DNS Configuration
            res = self.gen.create_buffer_cmd(204, '1:1', '1:2', 203)
            csrChecker(res)
            res = res[2]
            q = ['DNS Server Updates Disabled', 'DNS Server Updates Enabled']
            return q[res]

    # BONUS COMMAND
    def reportHashSeed(self):   #VERIFIED
        res = self.gen.create_buffer_cmd(204, '1:1', '1:4', 20)
        csrChecker(res)
        res = res[2]
        return res

    def reportUnitRunTime(self):    #VERIFIED
        res = self.gen.create_buffer_cmd(205, '0:0', '1:4')
        csrChecker(res)
        runTime = res[2]
        return runTime

    def reportTotalEnergyOutput(self):  #VERIFIED
        res = self.gen.create_buffer_cmd(206, '0:0', '1:4')
        csrChecker(res)
        energyOutput = res[2]
        return energyOutput

    def reportLockedSubCommand(self, subcommand, p1, p2):   #FIXME NEED TO FIX SBCMD5, SBCMD24, SBCMD27
        valids = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 12, 13, 14, 15, 17, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 32, 33, 34,
                  35, 36, 48, 49, 50, 51, 52, 249, 253]
        if subcommand == 0:     # report unlock status
            res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 0, 0)
#            csrChecker(res)
            res = int(res[2])
            if res == 42793:
                return 'Unlocked'
            elif res == 99:
                return 'Locked'
            else:
                raise Exception('Unexpected CSR!')
        if subcommand == 1:     # report tuning log
            if p1 > 63 or p1 < 0 or isinstance(p1, int) != True:
                raise Exception('Invalib block offset: must be 0-63')
            res = self.gen.create_buffer_cmd(207, '2:2', '32:4', 1, p1)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 2:     # report debug flags
            if p1 not in [0, 1]:
                raise Exception('Invalid debug flag mode: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '2:2', '16:1', 2, p1)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 3:     # report life test snapshot
            res = self.gen.create_buffer_cmd(207, '2:2', '15:2', 3, 0)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 4:     # report performance monitor statistics
            if p1 not in [0, 1]:
                raise Exception('Invalid statistics version: must be 0 or 1')
            if p1 == 0:
                res = self.gen.create_buffer_cmd(207, '2:2', '8:4', 4, p1)
                csrChecker(res)
                res = res[2:]
                return res
            if p1 == 1:
                res = self.gen.create_buffer_cmd(207, '2:2', '11:4', 4, p1)
                csrChecker(res)
                res = res[2:]
                return res
        if subcommand == 5:     # report memory address value #FIXME (unsure of if this one works)
            res = self.gen.create_buffer_cmd(207, '4:2,2:4', '1:4', 5, p1, p2)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 7:     # read test engineering NVSRAM
            res = self.gen.create_buffer_cmd(207, '2:2', '255:1', 7, 0)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 8:     # report airflow
            if p1 not in [0, 1]:
                raise Exception('Invalid inverter selection: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 8, p1)
            csrChecker(res)
            res = res[2]
            return res
        if subcommand == 9:     # report line voltage
            res = self.gen.create_buffer_cmd(207, '2:2', '3:2', 9, 0)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 10:     # Report Output Slew Rate Limits
            res = self.gen.create_buffer_cmd(207, '2:2', '3:2,3:4', 10, 0)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 12:     # Report Analog User Card Setpoint Input Gain and Offset
            if p1 not in [0, 1]:
                raise Exception('Invalid source selection: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 12, p1)
            csrChecker(res)
            res = res[2:]
            if res[1] & (1 << (16 - 1)):
                res[1] -= 1 << 16
            return res
        if subcommand == 13:     # Report Analog User Card Forward Power Gain and Offset
            if p1 not in [0, 1]:
                raise Exception('Invalid source selection: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 13, p1)
            csrChecker(res)
            res = res[2:]
            if res[1] & (1 << (16 - 1)):
                res[1] -= 1 << 16
            return res
        if subcommand == 14:     # Report Analog User Card Reflected Power Gain and Offset
            if p1 not in [0, 1]:
                raise Exception('Invalid source selection: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 14, p1)
            csrChecker(res)
            res = res[2:]
            if res[1] & (1 << (16 - 1)):
                res[1] -= 1 << 16
            return res
        if subcommand == 15:     # Report Analog User Card Load Power Gain and Offset
            if p1 not in [0, 1]:
                raise Exception('Invalid source selection: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 15, p1)
            csrChecker(res)
            res = res[2:]
            if res[1] & (1 << (16 - 1)):
                res[1] -= 1 << 16
            return res
        if subcommand == 17:     # Report Analog User Card DC Bias Input Gain and Offset
            if p1 not in [0, 1]:
                raise Exception('Invalid source selection: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 17, p1)
            csrChecker(res)
            res = res[2:]
            if res[1] & (1 << (16 - 1)):
                res[1] -= 1 << 16
            return res
        if subcommand == 21:     # Report Internal Logging Configuration
            if p1 not in [0, 1]:
                raise Exception('Invalid log selection: must be 0 or 1')
            if p2 not in [0, 3, 4, 5]:
                raise Exception('Invalid parameter selection: must be 0, 3, 4, or 5')
            if p2 == 5:
                p1 = 0
            res = self.gen.create_buffer_cmd(207, '1:2,2:1', '1:4', 21, p1, p2)
            csrChecker(res)
            res = res[2]
            if p2 == 5:
                res = format(res, '08b')
            return res
        if subcommand == 22:     # Report Internal Data Logging Event
            if p1 not in [0, 1]:
                raise Exception('Invalid parameter selection: must be 0 or 1')
            if p1 == 0:
                p2 = 0
                res = self.gen.create_buffer_cmd(207, '3:2', '1:2,255:1', 22, 0, 0)
                csrChecker(res)
                res = res[2:]
                return res
            else:
                res = self.gen.create_buffer_cmd(207, '3:2', '255:1', 22, 1, p2)
                csrChecker(res)
                res = res[2:]
                return res
        if subcommand == 23:     # Report Communication Interface Logging
            if p1 not in [1, 2, 4]:
                raise Exception('Invalid control source: must be 1, 2, or 4')
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 23, p1)
            csrChecker(res)
            res = res[2]
            return res
        if subcommand == 24:     # Report Internal Data Log Contents    #FIXME
            raise Exception('Not yet implemented, brain smooth like back of dolphin')
        if subcommand == 25:  # Report RF Drive Disable
            res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 25, 0)
            csrChecker(res)
            res = res[2]
            state = ['Drive is enabled.', 'Drive is disabled.']
            return state[res]
        if subcommand == 26:  # Report Measurement Average Counts
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2,2:4', 26, 0)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 27:     # Report Frequency Sweep Log Data    #FIXME
            raise Exception('Not yet implemented, brain smooth like back of dolphin')
        if subcommand == 28:     # Report RF Bias DAC Calibration Value
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1, 2, 3]:
                raise Exception('Invalid source: must be 0, 1, 2, or 3')
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 28, p1)
            csrChecker(res)
            res = res[2]
            return res
        if subcommand == 29:     # Report Phase Offset Calibration Value
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1]:
                raise Exception('Invalid source: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '4:2', '2:2', 29, p1, 0, 0)
            csrChecker(res)
            res = res[2]
            return res
        if subcommand == 30:     #Report RF Bias DAC Lookup Table Cal Values
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1, 2, 3]:
                raise Exception('Invalid source: must be 0, 1, 2, or 3')
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 30, p1)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 32:     #Report Thermistor Resistance Correction
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1]:
                raise Exception('Invalid source: must be 0 or 1')
            if p2 not in [0, 1, 2, 3, 8, 9, 10, 11, 16]:
                raise Exception('Invalid thermistor selection!')
            res = self.gen.create_buffer_cmd(207, '3:2', '1:2', 32, p1, p2)
            csrChecker(res)
            res = res[2]
            return res
        if subcommand == 33:     #Report Thermistor Delta Temperature
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1, 16, 17]:
                raise Exception('Invalid source: must be 0, 1, 16, or 17')
            res = self.gen.create_buffer_cmd(207, '2:2', '4:1', 33, p1)
            csrChecker(res)
            res = res[2:]
            int_part = res[0:3]
            for i in range(len(int_part)):
                int_part[i] = hex(int_part[i])[2:]
                if len(int_part[i]) == 1:
                    int_part[i] = '0' + int_part[i]
            hex_str = int_part[2] + int_part[1] + int_part[0]
            myint = int(hex_str, 16)
            if myint & (1 << (24 - 1)):
                myint -= 1 << 24
            return myint
        if subcommand == 34:     #Report DC Voltage Offset
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1]:
                raise Exception('Invalid source: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 34, p1)
            csrChecker(res)
            res = res[2]
            return twos_comp(res, 16)
        if subcommand == 35:     #Report DC Current Offset
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1]:
                raise Exception('Invalid source: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 35, p1)
            csrChecker(res)
            res = res[2]
            return twos_comp(res, 16)
        if subcommand == 36:     #Report Diagnostics Calibration
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1]:
                raise Exception('Invalid source: must be 0 or 1')
            if p2 not in [0, 1, 2]:
                raise Exception('Invalid parameter: must be 0, 1, or 2')
            res = self.gen.create_buffer_cmd(207, '3:2', '2:2', 36, p1, p2)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 48:     # Report NV Fault Status
            res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 48, 0)
            csrChecker(res)
            res = res[2]
            status = ['No active NV faults', 'Active NV faults']
            return status[res]
        if subcommand == 49:     # Report Smoke Detector Values
            res = self.gen.create_buffer_cmd(207, '2:2', '2:2', 49, 0)
            csrChecker(res)
            res = res[2:]
            return res
        if subcommand == 50:     # Report CEX Output Scale Factor
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1]:
                raise Exception('Invalid source: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 50, p1)
            csrChecker(res)
            res = res[2]
            return res
        if subcommand == 51:     # Report CEX Output Calibration Value
            if self.isRFon():
                raise Exception('Cannot do this while RF is on!')
            if p1 not in [0, 1]:
                raise Exception('Invalid source: must be 0 or 1')
            res = self.gen.create_buffer_cmd(207, '4:2', '2:2', 51, p1, 0, 0)
            csrChecker(res)
            res = res[2]
            return res
        if subcommand == 52:     # Report Actual Measurement Delay Time
            res = self.gen.create_buffer_cmd(207, '2:2', '1:4', 52, 0)
            csrChecker(res)
            res = res[2]
            return res
        if subcommand == 66: #Report DC Voltage
            res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 66, 0)
            csrChecker(res)
            res = res[2]
            return res
        if subcommand == 67: #Report DC Current
            res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 67, 0)
            csrChecker(res)
            res = res[2]
            return res
        if subcommand == 249:     # Report Calibration Mode
            res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 249, 0)
            csrChecker(res)
            res = res[2]
            status = ['Cal mode disabled', 'Cal mode enabled']
            return status[res]
        if subcommand == 253:     # Report Ignore DC Ready
            res = self.gen.create_buffer_cmd(207, '2:2', '1:2', 253, 0)
            csrChecker(res)
            res = res[2]
            status = ['Normal Operation', 'Ignore DC Ready Mode Enabled']
            return status[res]

    def reportMeasurementCorrectionFactors(self, location, matrix):     #VERIFIED
        # if self.isRFon():
        #     raise Exception('Cannot do this while RF is on!')
        if location not in [0, 1]:
           raise Exception('''Invalid Memory Location Selected:
                     0 = RAM
                     1 = EEPROM''')
        if matrix not in [0, 1, 2]:
            raise Exception('''Invalid Matrix Selected:
                                 0 = Center Frequency
                                 1 = Correction Matrix A
                                 2 = Correction Matrix B''')
        if matrix == 0:
            location = 0
        res = self.gen.create_buffer_cmd(208, '2:1', '8:2', location, matrix)
        csrChecker(res)
        realK11 = res[2]
        realK12 = res[3]
        realK21 = res[4]
        realK22 = res[5]
        reacK11 = res[6]
        reacK12 = res[7]
        reacK21 = res[8]
        reacK22 = res[9]
        if realK11 & (1 << (16 - 1)):
            realK11 -= 1 << 16
        if realK12 & (1 << (16 - 1)):
            realK12 -= 1 << 16
        if realK21 & (1 << (16 - 1)):
            realK21 -= 1 << 16
        if realK22 & (1 << (16 - 1)):
            realK22 -= 1 << 16
        if reacK11 & (1 << (16 - 1)):
            reacK11 -= 1 << 16
        if reacK12 & (1 << (16 - 1)):
            reacK12 -= 1 << 16
        if reacK21 & (1 << (16 - 1)):
            reacK21 -= 1 << 16
        if reacK22 & (1 << (16 - 1)):
            reacK22 -= 1 << 16
        coefficientMatrix = [realK11, realK12, realK21, realK22, reacK11, reacK12, reacK21, reacK22]
        return coefficientMatrix

    def reportSerialAddressandBaudRate(self, port):  #VERIFIED
        options = [1, 2, 11, 12]
        if port not in options:
            raise Exception('''Invalid Port Selected:
                     1 = Host Port
                     2 = Flash Port
                     11 = USB
                     12 = Front Port USB''')
        res = self.gen.create_buffer_cmd(212, '1:1', '3:1', port)
        csrChecker(res)
        busAddress = res[2]
        baudRate = res[3]
        RS485Mode = res[4]
        return busAddress, baudRate, RS485Mode

    def reportMaskedFaultCode(self):    #VERIFIED
        res = self.gen.create_buffer_cmd(214, '1:1', '255:2', 0)
        csrChecker(res)
        res = res[2:]
        if res[0] == 0 and len(res) == 1:
            return 'No masked fault codes!'
        descriptions = []
        for i in range(len(res)):
            descriptions.append(str(res[i]) + ': ' + self.reportSystemControl(92, 1, res[i]))
        return descriptions

    def reportRealTimeClock(self):      # VERIFIED
        res = self.gen.create_buffer_cmd(215, '0:0', '7:1')
        csrChecker(res)
        seconds = str(hex(res[2]))[2:]
        if int(seconds) < 10:
            seconds = '0' + seconds
        minutes = str(hex(res[3]))[2:]
        if int(minutes) < 10:
            minutes = '0' + minutes
        hours = str(hex(res[4]))[2:]
        dayofweek = str(hex(res[5]))[2:]
        day = str(hex(res[6]))[2:]
        month = str(hex(res[7]))[2:]
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        year = str(hex(res[8]))[2:]
        if int(year) < 10:
            year = '200' + year
        else:
            year = '20' + year
        dateStr = hours + ':' + minutes + ':' + seconds + '_' + months[int(month) - 1] + '_' + day + '_' + year
        return dateStr

    def reportCondensedGeneratorSnapshot(self, option = None):  #VERIFIED
        options = [0, 6, 8, 9]
        if option not in options and option is not None:
            raise Exception('Invalid Snapshot Selected: 0, 6, 8, 9')
        if option is None or option == 0:
            res = self.gen.create_buffer_cmd(219, '0:0', '4:2,4:4,2:1,1:2')
            csrChecker(res)
            res = res[2:]
            if self.isHaloEnabled():
                res[0] *= 0.1
                res[1] *= 0.1
                res[2] *= 0.1
                if self.reportRegulationMode() != 8:
                    res[3] *= 0.1
            res[4] *= 0.01
            res[5] *= 0.01
            res[7] = '{0:032b}'.format(res[7])
            #format: [fwdPow, refPow, delPow, setpoint, realImp, reacImp, freq, process status, regMode, ctrlMode,
            # coldplateTemp]
        if option == 6:
            res = self.gen.create_buffer_cmd(219, '1:1', '6:2,4:4,2:1,1:2,2:4', 6)
            csrChecker(res)
            res = res[2:]
            if self.isHaloEnabled():
                res[2] *= 0.1
                res[3] *= 0.1
                res[4] *= 0.1
                if self.reportRegulationMode() != 8:
                    res[5] *= 0.1
            res[6] *= 0.01
            res[7] *= 0.01
            res[9] = '{0:032b}'.format(res[9])
            # format: [snapshot revision, reserved, fwdPow, refPow, delPow, setpoint, realImp, reacImp, freq,
            # process status, regMode, ctrlMode, coldplateTemp, refPow warning count, refPow fault count]
        if option == 8:
            res = self.gen.create_buffer_cmd(219, '1:1', '6:2,4:4,2:1,1:2,1:4,1:2', 8)
            csrChecker(res)
            res = res[2:]
            if self.isHaloEnabled():
                res[2] *= 0.1
                res[3] *= 0.1
                res[4] *= 0.1
                if self.reportRegulationMode() != 8:
                    res[5] *= 0.1
            res[6] *= 0.01
            res[7] *= 0.01
            res[9] = '{0:032b}'.format(res[9])
            # format: [snapshot revision, reserved, fwdPow, refPow, delPow, setpoint, realImp, reacImp, freq,
            # process status, regMode, ctrlMode, coldplateTemp, pulse freq, pulse d.c.]
        if option == 9:
            res = self.gen.create_buffer_cmd(219, '1:1', '6:2,4:4,2:1,1:2,1:4,1:2', 9)
            csrChecker(res)
            res = res[2:]
            if self.isHaloEnabled():
                res[2] *= 0.1
                res[3] *= 0.1
                res[4] *= 0.1
                if self.reportRegulationMode() != 8:
                    res[5] *= 0.1
            res[6] *= 0.01
            res[7] *= 0.01
            res[9] = '{0:032b}'.format(res[9])
            # format: [snapshot revision, reserved, avg fwdPow, avg refPow, avg delPow, setpoint, realImp, reacImp,
            # freq, process status, regMode, ctrlMode, coldplateTemp, pulse freq, pulse d.c.]
        return res

    def reportGeneratorStatistics(self, stat, param):
        if stat not in [0, 1, 2, 3 ,4]:
            raise Exception('Invalid statistic selection')
        if stat == 0:
            res = self.gen.create_buffer_cmd(220, '1:2', '53:4', stat)
            csrChecker(res)
            res = res[2:]
        if stat == 1 or stat == 2:
            response = self.gen.create_buffer_cmd(220, '2:2', '3:2,240:1', stat, param)
            csrChecker(response)
            response = response[2:]
            removedHeader = response[3:]
            codes = []
            quantity = []
            for i in range(len(removedHeader)):
                if i % 6 == 0:
                    first = hex(removedHeader[i+1])[2:]
                    second = hex(removedHeader[i])[2:]
                    if len(second) < 2:
                        second = '0' + second
                    codes.append(int(first + second, 16))
                    first = hex(removedHeader[i+5])[2:]
                    second = hex(removedHeader[i+4])[2:]
                    if len(second) < 2:
                        second = '0' + second
                    third = hex(removedHeader[i+3])[2:]
                    if len(third) < 2:
                        third = '0' + third
                    fourth = hex(removedHeader[i+2])[2:]
                    if len(fourth) < 2:
                        fourth = '0' + fourth
                    quantity.append(int(first + second + third + fourth, 16))
            res = [response[0], response[1], response[2]]
            for i in range(len(codes)):
                res.append(codes[i])
                res.append(quantity[i])
            return res
        if stat == 3:
            res = self.gen.create_buffer_cmd(220, '1:2', '1:4,8:1,1:4,8:1,1:4,8:1', stat)
            csrChecker(res)
            res = res[2:]
            for i in range(len(res)):
                if i != 0 and i != 9 and i != 18:
                    res[i] = hex(res[i])
            if res[18] == 4294966296:
                res[18] = 0
        if stat == 4:
            if param > 78 or param < 1 or isinstance(param, int) != True:
                raise Exception('Invalid parameter selection!')
            response = self.gen.create_buffer_cmd(220, '2:2', '2:2,1:4,255:1', stat, param)
            csrChecker(response)
            response = response[2:]
            description = ''
            chars = response[3:]
            for i in range(len(chars)):
                description += chr(chars[i])
            res = [response[0], response[1], response[2], description]
        return res

    def reportPINNumber(self):  #VERIFIED
        res = self.gen.create_buffer_cmd(221, '0:0', '32:1')
        csrChecker(res)
        res = res[2:]
        pin = ''
        for digit in range(len(res)):
            pin += chr(res[digit])
        return pin

    def reportGeneratorSnapshotData(self): #VERIFIED
        res = self.gen.create_buffer_cmd(222, '0:0', '4:2,2:4,2:4,2:2,4:1,38:2,4:4')
        csrChecker(res)
        if self.isHaloEnabled():
            res[2] *= 0.1
            res[3] *= 0.1
            res[4] *= 0.1
            if self.reportRegulationMode() != 8:
                res[5] *= 0.1
        forwardPower = res[2]
        reflectedPower = res[3]
        deliveredPower = res[4]
        setpointPower = res[5]
        realImpedance = res[6] * 0.01
        reactiveImpedance = (twos_comp(res[7], 32)) * 0.01
        actualFrequecy = res[8]
        processStatus = '{0:032b}'.format(res[9])   #d0 is the LSB, d3 is MSB
        reserved = res[10]
        controlLoopStatus = '{0:016b}'.format(res[11])
        regulationMode = res[12]
        controlMode = res[13]
        userCardInputLatch = '{0:08b}'.format(res[14])
        userCardOutputLatch = '{0:08b}'.format(res[15])
        userCardDAC1StateCH1 = res[16]      #fwd/del power
        userCardDAC1StateCH0 = res[17]      #refl power
        userCardDAC1StateCH2 = res[18]      #reserved
        userCardDAC1StateCH3 = res[19]      #reserved
        motherboardADC1CH0 = res[20]
        motherboardADC1CH1 = res[21]
        motherboardADC1CH2 = res[22]
        motherboardADC1CH3 = res[23]
        motherboardADC1CH4 = res[24]
        motherboardADC1CH5 = res[25]
        motherboardADC1CH6 = res[26]
        motherboardADC1CH7 = res[27]
        dcSectionADC2CH0 = res[28]
        dcSectionADC2CH1 = res[29]
        dcSectionADC2CH2 = res[30]
        dcSectionADC2CH3 = res[31]
        dcSectionADC2CH4 = res[32]
        dcSectionADC2CH5 = res[33]
        dcSectionADC2CH6 = res[34]
        dcSectionADC2CH7 = res[35]
        dcRailDACMonitorStatus = res[36]
        rfBiasDACPrimaryMonitorStatus = res[37]
        rfBiasDACSecondaryMonitorStatus = res[38]
        reserved1 = res[39]
        userCardADCCH0 = res[40]    #setpoint
        userCardADCCH1 = res[41]       #dc bias
        userCardADCCH2_3 = res[42]      #channel 2/3
        userCardADCCH3 = res[43]        #always 0
        dcSectionADC1CH0 = res[44]
        dcSectionADC1CH1 = res[45]
        dcSectionADC1CH2 = res[46]
        dcSectionADC1CH3 = res[47]
        dcSectionADC1CH4 = res[48]  #always 0
        dcSectionADC1CH5 = res[49]  #always 0
        dcSectionADC1CH6 = res[50]  #always 0
        dcSectionADC1CH7 = res[51]  #always 0
        rfcADCCH0 = res[52]
        rfcADCCH1 = res[53]
        correctedVoltageReflectedQ = res[54]
        if correctedVoltageReflectedQ & (1 << (32 - 1)):
            correctedVoltageReflectedQ -= 1 << 32
        correctedVoltageReflectedI= res[55]
        if correctedVoltageReflectedI & (1 << (32 - 1)):
            correctedVoltageReflectedI -= 1 << 32
        correctedVoltageForwardQ = res[55]
        if correctedVoltageForwardQ & (1 << (32 - 1)):
            correctedVoltageForwardQ -= 1 << 32
        correctedVoltageForwardI = res[56]
        if correctedVoltageForwardI & (1 << (32 - 1)):
            correctedVoltageForwardI -= 1 << 32

        dataMatrix = [forwardPower, reflectedPower, deliveredPower, setpointPower, realImpedance, reactiveImpedance, actualFrequecy, processStatus,
                      reserved, controlLoopStatus, regulationMode, controlMode, userCardInputLatch, userCardOutputLatch, userCardDAC1StateCH1,
                      userCardDAC1StateCH0, userCardDAC1StateCH2, userCardDAC1StateCH3, motherboardADC1CH0, motherboardADC1CH1, motherboardADC1CH2,
                      motherboardADC1CH3, motherboardADC1CH4, motherboardADC1CH5, motherboardADC1CH6, motherboardADC1CH7, dcSectionADC2CH0,
                      dcSectionADC2CH1, dcSectionADC2CH2, dcSectionADC2CH3, dcSectionADC2CH4, dcSectionADC2CH5, dcSectionADC2CH6, dcSectionADC2CH7,
                      dcRailDACMonitorStatus, rfBiasDACPrimaryMonitorStatus,  rfBiasDACSecondaryMonitorStatus, reserved1,  userCardADCCH0,  userCardADCCH1,
                      userCardADCCH2_3, userCardADCCH3, dcSectionADC1CH0, dcSectionADC1CH1, dcSectionADC1CH2, dcSectionADC1CH3, dcSectionADC1CH4,
                      dcSectionADC1CH5, dcSectionADC1CH6, dcSectionADC1CH7, rfcADCCH0, rfcADCCH1, correctedVoltageReflectedQ, correctedVoltageReflectedI,
                      correctedVoltageForwardQ, correctedVoltageForwardI]
        return dataMatrix

    def reportFaultOrWarningCodeList(self, type):   #VERIFIED
        if type not in [1, 2, 3, 4]:
            raise Exception('Invalid Type Selected: 1-4')
        response = self.gen.create_buffer_cmd(223, '1:1', '20:2', type)
        csrChecker(response)
        response = response[2:]
        if type == 1 or type == 2:
            if len(response) == 1 and response[0] == 0:
                if type == 1:
                    res = 'No active faults'
                if type == 2:
                    res = 'No active warnings'
                return res
        return response

    def reportImpedance(self):      #VERIFIED
        res = self.gen.create_buffer_cmd(225, '0:0', '2:4')
        csrChecker(res)
        if res[2] & (1 << (32 - 1)):
            res[2] -= 1 << 32
        if res[3] & (1 << (32 - 1)):
            res[3] -= 1 << 32
        realImp = res[2]/100
        reacImp = res[3]/100
        return realImp, reacImp


    def reportColdPlateTemperature(self, select):   #VERIFIED
        tenths = [0, 1, 2, 3, 4, 5, 17, 18, 19, 20]
        fixed = [6, 7, 8, 9, 10, 22, 23, 24, 25]
        fortyMeg = []
        if float(self.reportFixedFrequency()) >= 38000:
            fortyMeg = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
        if select not in tenths and select not in fixed and select not in fortyMeg:
            raise Exception('Invalid temperature selection!')
        res = self.gen.create_buffer_cmd(228, '1:1', '1:2', select)
        csrChecker(res)
        res = res[2]
        if select in tenths or select in fortyMeg:
            return round(res * 0.1, 2)
        if select in fixed or select in fortyMeg:
            translated = '{0:016b}'.format(res)
            int_part = int(translated[:8],2)
            frac_part = round(int(translated[8:],2)/256,2)
            return int_part + frac_part


    def reportSerialNumber(self, part = None):  #VERIFIED
            options = [0, 1, 2, 3, 6, 7]
            if part is not None and part not in options:
                raise Exception('''Invalid Part Selected:
                         0 = Unit Serial Number
                         1 = Unit Final Assembly Part Number
                         7 = Customer Part Number Revision''')
            if part is None or part == 0:
                res = self.gen.create_buffer_cmd(231, '0:0', '1:4')
                csrChecker(res)
                res = res[2]
                return res
            if part == 1:
                res = self.gen.create_buffer_cmd(231, '1:1', '12:1', part)
                csrChecker(res)
                res = res[2:]
                nameString = ''
                for i in range(len(res)):
                    if res[i] != 0:
                        nameString += chr(res[i])
                return nameString
            if part == 2:
                res = self.gen.create_buffer_cmd(231, '1:1', '16:1', part)
                csrChecker(res)
                res = res[2:]
                nameString = ''
                for i in range(len(res)):
                    if res[i] != 0:
                        nameString += chr(res[i])
                return nameString
            if part == 3:
                res = self.gen.create_buffer_cmd(231, '1:1', '16:1', part)
                csrChecker(res)
                res = res[2:]
                nameString = ''
                for i in range(len(res)):
                    if res[i] != 0:
                        nameString += chr(res[i])
                return nameString
            if part == 6:
                res = self.gen.create_buffer_cmd(231, '1:1', '3:1', part)
                csrChecker(res)
                res = res[2:]
                nameString = ''
                for i in range(len(res)):
                    if res[i] != 0:
                        nameString += chr(res[i])
                return nameString
            if part == 7:
                res = self.gen.create_buffer_cmd(231, '1:1', '2:1', part)
                csrChecker(res)
                res = res[2:]
                nameString = ''
                for i in range(len(res)):
                    if res[i] != 0:
                        nameString += chr(res[i])
                return nameString

    def reportDiagnosticStatusEfficiency(self): #FIXME VFP doesnt like CMD244, says the control mode is incorrect for both host and user modes
        res = self.gen.create_buffer_cmd(244, '1:1', '5:1', 0)
        csrChecker(res)
        res = res[2:]
        status = ['Diagnostics In Progress', 'Diagnostic Test Complete – Passed', 'Diagnostic Test Complete – Failed', 'Diagnostic Test Interrupted', 'Diagnostic Mode Disabled']
        res[1] = '{0:08b}'.format(res[1])
        res[2] = '{0:08b}'.format(res[2])
        res[3] = '{0:08b}'.format(res[3])
        res[4] = '{0:08b}'.format(res[4])
        return status[res[0]], res[1], res[2], res[3], res[4]

    def reportUnlockedSubCommand(self, subcommand, p1 = None):
        if subcommand == 1: # Report Frequency Step Minimum
            res = self.gen.create_buffer_cmd(248, '1:2', '1:4', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 2:  # Report Frequency Step Maximum
            res = self.gen.create_buffer_cmd(248, '1:2', '1:4', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 3:   #Report Step Up Gain
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 4:   #Report Step Down Gain
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 6:  #Report Gamma Threshold High
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 7:  #Report Gamma Threshold Low
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 8:   #Report Max Tuning Count
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 15:  #Report Ignition Mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 16:  #Report CEX Lock Mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 17:  #Report Tuning Scan Step Size
            res = self.gen.create_buffer_cmd(248, '1:2', '1:4', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 20:  #Report CEX Output Mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 21:  #Report Gamma Threshold Mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 22:  #Report Tuning Step Time
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 23:  #Report Tuning Gain Delay
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 38:  #Report Match Interface Mode (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 43:  #Report IMD Filter Frequency (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 44:  #Report CEX Output Phase Offset
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 45:  #Report User Power Correction (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 47:  #Report Auto Stability Configuration
            if p1 not in [1, 2, 3, 4, 5, 6, 7, 8, 9]:
                raise Exception('Invalid Auto-Stability subcommand number!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 51:  #Report Fixed Frequency Memory Mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 52:  # Report Frequency Tuning Mode (NV)
            if p1 not in [0, 1]:
                raise Exception('Invalid Frequency Tuning Command!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 53:  #Report User Readback Filter Configuration (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:1,1:4', subcommand)
            csrChecker(res)
            res = res[2:]
        elif subcommand == 57: #Report fast data aquisition data
            res = self.gen.create_buffer_cmd(248, '1:6', '1:6', subcommand)
            csrChecker(res)
            res=res[2]
        elif subcommand == 59:  #Report Configurable Generator Snapshot Configuration (NV)
            res = self.gen.create_buffer_cmd(248, '2:2', '255:2', subcommand, 1)
            csrChecker(res)
            res = res[2:]
        elif subcommand == 60:  #Report Statistics Data
            res = self.gen.create_buffer_cmd(248, '2:2', '16:4', subcommand, 1)
            csrChecker(res)
            res = res[2:]
            if self.isHaloEnabled():
                res[0] *= 0.1
                res[1] *= 0.1
                res[2] *= 0.1
                res[3] *= 0.1
                res[4] *= 0.1
                res[5] *= 0.1
                res[6] *= 0.1
                res[7] *= 0.1
                res[8] *= 0.1
                res[9] *= 0.1
                res[10] *= 0.1
                res[11] *= 0.1
        elif subcommand == 61:  #Report Configurable Generator Snapshot Data
            res = self.gen.create_buffer_cmd(248, '2:2', '8:1,61:4', subcommand, 1)
            csrChecker(res)
            res = res[2:]
            #NOTE: This reads in the timestamp as 7 single bytes instead of a single 7-byte object
        elif subcommand == 62:  #Report Fast Ignition Sweep Time
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 63:  #Report Fast Ignition Ramp Time
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 64: #Report Pulse Edge Frequency Ramp Times (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:6', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 65:  #Report Measured Pulsing Frequency and Duty Cycle
            if p1 not in [0, 1]:
                raise Exception('Invalid parameter select!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:4,1:2', subcommand, p1)
            csrChecker(res)
            res = res[2:]
        elif subcommand == 66:  #Report Reflected Power Timer
            if p1 not in [1, 2, 3, 4, 5, 6, 7]:
                raise Exception('Invalid Reflected Power Timer Sub-Command Number!')
            if p1 in [1, 2, 5]:
                res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand, p1)
            else:
                res = self.gen.create_buffer_cmd(248, '2:2', '1:4', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 67:  #Report Sweep Auto Frequency Mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 69:  #Report Pulsing Measurement Warning Threshold
            if p1 not in [1, 2]:
                raise Exception('Invalid Pulsing Measurement Warning Threshold Selection!')
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 70:  #Report Pulsing Measurement Errors and Warnings
            if p1 not in [1, 2]:
                raise Exception('Invalid Pulsing Measurement Selection!')
            if p1 == 1:
                res = self.gen.create_buffer_cmd(248, '1:2', '2:2,6:1', subcommand, p1)
            else:
                res = self.gen.create_buffer_cmd(248, '1:2', '4:4', subcommand, p1)
            csrChecker(res)
            res = res[2:]
        elif subcommand == 71: #Report SDD Pulsing Freq/Duty Cycle Sample Count (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 72:  #Report User Card Low Scale Enable Mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 73:  #Report Statistics Pulse Mask Time
            if p1 not in [0, 1, 2, 5, 6]:
                raise Exception('Invalid Statistics Pulse Mask Time Selection!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 74:  #Report Statistics Sample Rate
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand, 1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 75:  #Report Statistics Sample Count
            if p1 not in [1, 2]:
                raise Exception('Invalid Statistics Sample Count Selection!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 76:  #Report Fast Ignition Tuning Parameters
            if p1 not in [1, 2, 3, 4, 5]:
                raise Exception('Invalid Fast Ignition Tuning Parameter Selection!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:4', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 77:  #Report Pulsing Measurement Delays (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '3:2', subcommand)
            csrChecker(res)
            res = res[2:]
        elif subcommand == 78:  #Report Sweep Frequency Step Direction
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 79:  #Report Control Loop Gain
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 81:  #Report Sweep Gamma Average Mask Enable
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 83:  #Report Sweep Skip Frequency Range
            res = self.gen.create_buffer_cmd(248, '2:2', '2:4', subcommand, 1)
            csrChecker(res)
            res = res[2:]
        elif subcommand == 86:  #Report Statistics Acquisition Mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 88: #Report User Card Configuration
            res = self.gen.create_buffer_cmd(248, '2:2', '1:4', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 89: #Report Model Based Tuning Parameters
            res = self.gen.create_buffer_cmd(248, '2:2', '1:4', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 93:  #Report Fixed Frequency Control (NV)
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand, 1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 94:  #Report Frequency Tuning Mask Time
            if p1 not in [2, 3]:
                raise Exception('Invalid Frequency Tuning Mask Time selection!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 111: #Report Configurable Ignition Mode (NV)
            if p1 not in [0, 1]:
                raise Exception('Invalid Ignition Mode Selection!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:12', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 112: #Report Tuning Delay Mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 114: #Report Pulsing Chopper Parameters
            if p1 not in [1, 2, 3]:
                raise Exception('Invalid Ignition Mode Selection!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:12', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 116: #Report Tuning Delay Event (NV)
            if p1 not in [0, 1]:
                raise Exception('Invalid Ignition Mode Selection!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:8', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 117: #Report Setpoint Change Masking Time (NV)
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand, 1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 120: #Report Data Logging Parameters
            if p1 not in [0, 1, 10]:
                raise Exception('Invalid Ignition Mode Selection!')
            res = self.gen.create_buffer_cmd(248, '2:2', '1:4', subcommand, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 121: #Report Internal Data Log Contents
            res = self.gen.create_buffer_cmd(248, '2:3', '1:4', subcommand, 0, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 122: #Report Fault Log Contents
            res = self.gen.create_buffer_cmd(248, '2:3', '1:34', subcommand, 0, p1)
            csrChecker(res)
            res = res[2]
        elif subcommand == 126: #Pulsing Frequency/Duty Cycle Ramp Time
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 127: #Set Pulsing Frequency/Duty Cycle Combo
            res = self.gen.create_buffer_cmd(248, '1:2', '1:4,1:2', subcommand)
            csrChecker(res)
            res = res[2:]
        elif subcommand == 128: #Report Expected PSYNC-IN Frequency (NV)
            res = self.gen.create_buffer_cmd(248, '2:2', '1:4', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 129: #Report Expected PSYNC-IN Duty Cycle (NV)
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 130: #Report Reflected Power Monitor Offset (NV)
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 131: #Report Reflection Coefficient (gamma real and imag)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:8', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 134: #Report Line Voltage
            res = self.gen.create_buffer_cmd(248, '2:2', '3:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 135: #Report Out of Setpoint Tolerance (NV)
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 136: #Report Fast Ignition State Enable (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 137: #Report Previous Off Time Latch Threshold (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 138: #Report Continuous Tuning Enable (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 139: #Report Statistical Data Decimation Reflected Power Offset Enable (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 142: #Report Pulse State Frequency Tracking
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 145: #Report Hold Setpoint State 1
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 146: #Report Adjusted Drive Start
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 147: #Report Hold Frequency State 1
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 183: #Report Quasi(split)-CW in DLP mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 227: #Report Configuration File Checksum
            res = self.gen.create_buffer_cmd(248, '1:2', '1:16', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 173: #Report Pulse Synchronized Averaging Maximum Time (NV)
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 174: #Report Pulse Synchronized Data
            res = self.gen.create_buffer_cmd(248, '2:2', '1:64', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 183: #Report Quasi(split)-CW in DLP mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 184: #Report Continuous Rail State 0 Reference Enable in DLP mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 185: #Report Drive Track All Tracking Reference
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 302: #Report SyncTech Auto-Data Config (NV)
            res = self.gen.create_buffer_cmd(248, '2:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 300: #Report RF Bias DAC Manual Mode
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        elif subcommand == 301: #Report RF Bias DAC Offset
            res = self.gen.create_buffer_cmd(248, '1:2', '1:2', subcommand)
            csrChecker(res)
            res = res[2]
        else:
            raise Exception('Invalid subcommand!')
        return res



# -------------------------------------REPORT EEPROM VALUE COMMANDS----------------------------------------
    def reflectedPowerLimitEEPROM(self):  #LOCATION: BYTES 96 AND 97 OF MB1
        hexbytes = self.readEEPROM(67,0)[96:98]
        return int(hexbytes[0] + hexbytes[1], 16)

    def autoStabilityTableCoeff0EEPROM(self):   #LOCATION: BYTES 156 - 159 OF MEAS1
        hexbytes = self.readEEPROM(34,0)[156:160]
        bin_form = '{0:032b}'.format(int(hexbytes[0] + hexbytes[1] + hexbytes[2] + hexbytes[3], 16))
        return bin_to_float(bin_form)

    def autoStabilityTableCoeff1EEPROM(self):   #LOCATION: BYTES 160 - 163 OF MEAS1
        hexbytes = self.readEEPROM(34,0)[160:164]
        bin_form = '{0:032b}'.format(int(hexbytes[0] + hexbytes[1] + hexbytes[2] + hexbytes[3], 16))
        return bin_to_float(bin_form)

    def userPowerCorrectionMaxEEPROM(self):  #LOCATION: BYTE 135 OF MEAS1
        hexbytes = self.readEEPROM(34,0)[135]
        return int(hexbytes, 16)

    def userPowerCorrectionMinEEPROM(self):  #LOCATION: BYTE 134 OF MEAS1
        hexbytes = self.readEEPROM(34,0)[134]
        return int(hexbytes, 16)

    def tempRateFaultThresholdEEPROM(self):  #LOCATION: BYTE 124 OF MB1
        hexbytes = self.readEEPROM(67,0)[124]
        return int(hexbytes, 16)

    def tempRateFaultTimerEEPROM(self):  #LOCATION: BYTE 124 OF MB1
        hexbytes = self.readEEPROM(67,0)[125]
        return int(hexbytes, 16)

    def close(self):
        self.gen.close()

