"""
Created by:             Christopher McLean
Last Modified by:       Christopher McLean
Data Last Modified:     June 24 2022
Revision of Python:     3.9

Separated from AECommands created by Sterling Krone and Luis Martinez

Dependencies: AE_Com_Module
"""

import AE_Com_Module
import numpy as np

class lynx:
    def __init__(self, ipAddress):
        if ipAddress[0] == 'C':
            self.lynx = AE_Com_Module.AEB32(ipAddress, baud=115200, timeout=1000, ae_addr=1)
        else:
            self.lynx = AE_Com_Module.AETCP()
            self.lynx.connect(ipAddress)

    def unlockUnit(self):
        res = self.lynx.create_buffer_cmd(67, '1:1', '1:1', 19)
        res = self.lynx.create_buffer_cmd(68, '1:1', '1:1', 47)
        print('Unit Unlocked.')

    def setFreq(self, frequency):
        res = self.lynx.create_buffer_cmd(77, '1:4', '1:1', int(frequency))

    def getFreq(self):
        res = self.lynx.create_buffer_cmd(147, '1:0', '1:4')
        freq = res[2]
        return freq

    def reportRawMeasurementData(self):
        res = self.lynx.create_buffer_cmd(207, '1:2,2:1', '4:4d', 104, 1, 0)
        uncorrVRFL_Q = res[2]
        uncorrVRFL_I = res[3]
        uncorrVFWD_Q = res[4]
        uncorrVFWD_I = res[5]
        return uncorrVRFL_Q, uncorrVRFL_I, uncorrVFWD_Q, uncorrVFWD_I

    def readUnit222(self):
        values = self.lynx.create_buffer_cmd(222, '0:0', '7:f')
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

    def reportSerialNumber(self):
        res = self.lynx.create_buffer_cmd(231, '0:0', '1:4')
        serialNum = res[2]
        return serialNum

    def close(self):
        """Close the socket"""
        self.lynx.close()