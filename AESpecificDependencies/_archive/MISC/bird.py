import pyvisa

class CommandError:
    def __init__(self, cmd, esr):
        self.cmd = cmd
        self.esr = esr

    def __str__(self): return 'Cannot execute "%s" esr=0x%x' %(self.cmd, self.esr)

class bird:
    def __init__(self, ipAddress):
        self.ipAddress = ipAddress
        try:
            self.device = pyvisa.ResourceManager().open_resource('GPIB::%s::INSTR'%ipAddress)
            self.device.timeout = 3000
        except:
            self.device = pyvisa.ResourceManager().open_resource(('TCPIP0::%s::5025::SOCKET') %ipAddress)
            self.device.write_termination = "\n"
            self.device.read_termination = "\n"
            self.device.send_end = True
            self.device.timeout = 80000
            self.device.write('*RST\n')

    def reportReflectedPower(self):
        """ Return power in W from Bird meter """
        self.device.query('RC?')
        k, rfl, unit_rfl = self.device.read('RC?').split()
        rfl = float(rfl)
        if unit_rfl.upper().find('KW') != -1: 
            rfl *= 1000
        return rfl

    def reportForwardPower(self):
        self.device.query('FC?')
        k, fwd, unit_fwd = self.device.read('FC?').split()
        fwd = float(fwd)
        if unit_fwd.upper().find('KW') != -1: 
            fwd *= 1000
        return fwd

    def reportFWDAVG1(self):
        fwd = self.device.query("MEAS:AVER? 1").rstrip()
        return(fwd)

    def reportRFLAVG1(self):
        rfl = self.device.query("MEAS:REFL:AVER? 1").rstrip()
        return(rfl)

    def reportGateMax(self):
        gate = self.device.query("MEAS:GATE:MAX? 1").rstrip()
        return (gate)

    def reportGateMean(self):
        gate = self.device.query("MEAS:GATE:MEAN? 1").rstrip()
        return (gate)

    def reportStateMax(self,N):
        StateM = self.device.query("MEAS:STATE%0.1s:MAX? 1" %N).rstrip()
        return (StateM)

    def reportStateMean(self,N):
        StateM = self.device.query("MEAS:STATE%0.1s:MEAN? 1" %N).rstrip()
        return (StateM)

    def reportFreq(self):
        freq = self.device.query("MEAS:FREQ? 1").rstrip()
        return (freq)

    def setStateBegin(self,N,time):
        self.device.write("CALC:STATE%0.1s:BEGIN %0.1fUS,1" %(N,time))

    def setStateEnd(self,N,time):
        self.device.write("CALC:STATE%0.1s:END %0.1fUS,1" %(N,time))

    def setPeriod(self,time):
        self.device.write("SENSE:PERIOD %0.1fUS,1" %time)

    def reportPeriod(self):
        period = self.device.query("SENSE:PERIOD? 1").rstrip()
        return period

    def reportStateBegin(self,N):
        StateBegin = self.device.query("CALC:STATE%.01f:BEGIN? 1" %N)
        return(StateBegin)

    def reportStateEnd(self,N):
        StateEnd = self.device.query("CALC:STATE%0.1s:END? 1" %N).rstrip()
        return (StateEnd)

    def StateEnable(self,N,mode):
        self.device.write("CALC:STATE%0.1s:ENABLE %0.1f ,1" %(N,mode)) #0 is off, 1 is On

    def ReportStateEnable(self,N):
        Renable = self.device.query("CALC:STATE%0.1s:ENABLE?").rstrip()
        return Renable

    def setTrigger(self):
        self.device.write("CALC:PULSE TRIG, 1")

    def readTrigger(self):
        ReadGate = self.device.query("CALC:PULSE? 1").rstrip()
        return ReadGate

    def Commit(self):
        self.device.write("SYST:PULS:CONF:COMM 1")

    def StatePowerMax(self,N):
        MaxState = self.device.query("MEAS:STATE%0.1s:MAX? 1" %N).rstrip()
        return MaxState

    def StatePowerMin(self,N):
        MinState = self.device.query("MEAS:STATE%0.1s:MIN? 1" %N).rstrip()
        return MinState

    def StatePowerMean(self,N):
        AvgState = self.device.query("MEAS:STATE%0.1s:MEAN? 1" %N).rstrip()
        return AvgState

    def SystemSync(self):
        self.device.write("SYST:PULS:CONF:SYNC 1")

    def PulseEnable(self):
        self.device.write("SYST:PULS:ON 1")

    def RisingEdge(self):
        self.device.write("TRIG:SLOP POS,1")

    def FallingEdge(self):
        self.device.write("TRIG:SLOP NEG,1")

    def ReportEdge(self):
        Edge = self.device.query("TRIG:SLOPE?").rstrip()
        return Edge

    def close(self):
        self.device.close()      
    #####Fetch Commands for the Meter Head
class bird_head:
    def __init__(self, ipAddress):
        self.ipAddress = ipAddress
        self.device = pyvisa.ResourceManager().open_resource(('TCPIP0::%s::5025::SOCKET') %ipAddress)
        self.device.write_termination = "\n"
        self.device.read_termination = "\n"
        self.device.send_end = True
        self.device.timeout = 10000
        self.device.write('*RST\n')

    def FetchStateMean(self,N):
        CouplerM = self.device.query('FETC:STATE%0.1s:MEAN?' %N).rstrip()
        return CouplerM

    def FetchStateMax(self,N):
        CouplerMax = self.device.query('FETCH:STATE%0.1s:MAX?' %N).rstrip()
        return CouplerMax

    def FetchStateMin(self,N):
        CouplerMin = self.device.query('FETCH:STATE%0.1s:MIN?' %N).rstrip()
        return CouplerMin

    def CouplerStatePowerMax(self,N):
        CouplerMaxState = self.device.query("MEAS:STATE%0.1s:MAX?" %N).rstrip()
        return CouplerMaxState

    def CouplerStatePowerMin(self,N):
        CouplerMinState = self.device.query("MEAS:STATE%0.1s:MIN?" %N).rstrip()
        return CouplerMinState

    def CouplerStatePowerMean(self,N):
        CouplerAvgState = self.device.query("MEAS:STATE%0.1s:MEAN?" %N).rstrip()
        return CouplerAvgState

    def CouplersetStateBegin(self,N,time):
        self.device.write("CALC:STATE%0.1s:BEGIN %0.1f" %(N,time))

    def CouplersetStateEnd(self,N,time):
        self.device.write("CALC:STATE%0.1s:END %0.1f" %(N,time))

    def CouplersetPeriod(self,time):
        self.device.write("SENSE:PERIOD %0.1f" %time)