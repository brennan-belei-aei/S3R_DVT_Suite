import pyvisa
class KeysightN9320B:

    def __init__(self, ipAddress):
        self.ipAddress = ipAddress
        rm = pyvisa.ResourceManager()

        ip_string = ('TCPIP0::'+str(ipAddress)+'::INSTR')
        
        self.instr = rm.open_resource(ip_string)
        
    def close(self):
        self.instr.close()
        return

    def reset(self):
        self.instr.write('*RST')

    def SingleSweepMode(self):
        self.instr.write('INIT:CONT 0')
        return

    def ContSweepMode(self):
        self.instr.write('INIT:CONT 1')
        
        return

    def TriggerSweep(self):
        self.instr.write('INIT:IMM')
        return

    def TriggerSweepWait(self):
        self.instr.write('INIT:IMM')
  
    def startfreq(self, freqstart):
        self.instr.write('SENS:FREQ:START ' + str(freqstart))
        return

    def centerfreq(self, freqcenter):
        self.instr.write('SENS:FREQ:CENT ' + str(freqcenter))
        return

    def spanfreq(self, freqspan):
        self.instr.write('SENS:FREQ:SPAN ' + str(freqspan))
        return

    def stopfreq(self, freqstop):
        self.instr.write('SENS:FREQ:STOP ' + str(freqstop))
        return
    
    def identity(self):
        print(self.instr.query('*IDN?'))
        return self.instr.query('*IDN?')

    def BandwidthRES(self, freq):
        #Freq in terms of Hz
        self.instr.write('SENS:BAND ' + str(freq))
        return

    def GetStartFrequency(self):
        return float(self.instr.query(':FREQ:START?'))

    def GetStopFrequency(self):
        return float(self.instr.query(':FREQ:STOP?'))

    def SetAttLvl(self, numbr):
        self.instr.write(':POW:ATT ' + repr(numbr))
        return
    
    def SetRefLvl(self, numbr):
        self.instr.write(':DISP:WIND:TRAC:Y:SCAL:RLEV ' + repr(numbr))
        return

    def MaxPeak(self):
        self.instr.write('CALC:MARK:MAX')
        return
   
    def GetMarkerFreq(self):
        return self.instr.query("CALC:MARK:X?")
   
    def GetMarkerAmp(self):
        return self.instr.query("CALC:MARK:Y?")

    def DeltaMark(self):
        self.instr.write("CALC:MARK:MODE DELT")
        return
    
    def NextPeak(self):
        self.instr.write('CALC:MARK:MAX:NEXT')
        return

    def PeakLeft(self):
        self.instr.write('CALC:MARK:MAX:LEFT')
        return

    def PeakRight(self):
        self.instr.write('CALC:MARK:MAX:RIGH')
        return
    #specific for N9321C
    def SaveJPG(self,Frequency, Power):
        self.instr.write('MMEM:SAVE "C:\\' + str(Frequency) + 'kHz_' + str(Power) + 'W.jpg"')
