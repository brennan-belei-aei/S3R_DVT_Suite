#Yokogama 1800 interface
#Neels van Greunen, Advanced Energy 2013
import pyvisa
import time

class CommandError:
    def __init__(self, cmd, esr):
        self.cmd = cmd
        self.esr = esr

    def __str__(self): return 'Cannot execute "%s" esr=0x%x' %(self.cmd, self.esr)

class Yoko1800:
    def __init__(self, address):
        """ instrument must be a pyvisa instrument.
        GPIB and Ethernet can be used """
        self.instrument = pyvisa.ResourceManager().open_resource('TCPIP0::%s::INSTR'%address)
        idn = self.instrument.query('*IDN?')
        if idn.upper().find('YOKOGAWA') == -1:
            raise 'not Yokogawa instrument, report as '+idn
        self._do('*CLS')
        self._do('COMM:REM ON')
        #set ascii format
        self._do(':NUM:FORM ASCII')
        #setup default values
        self._do(':NUM:NORM:CLE ALL')
        self._do(':NUM:NORM:ITEM1 P,1')
        self._do(':NUM:NORM:ITEM2 P,2')
        self._do(':NUM:NORM:ITEM3 URMS,1')
        self._do(':NUM:NORM:ITEM4 IRMS,1')
        self._do(':NUM:NORM:ITEM5 PHI,1')
        self._do(':NUM:NORM:ITEM6 URMS,2') 
        self._do(':NUM:NORM:ITEM7 IRMS,2')
        self._do(':NUM:NORM:ITEM8 PHI,2')
        # self._do(':NUM:NORM:ITEM8 URMS,3') 
        # self._do(':NUM:NORM:ITEM9 IRMS,3')
        # self._do(':NUM:NORM:ITEM10 PHI,3')
        self._do(':NUM:NORM:NUM 8')
        self._do(':HOLD ON')
        self._do(':STAT:EESE 1') #enable bit 0 (update) in event enable register
        self._do(':STAT:FILT1 FALL') #enable falling edge on update for extended events


    def _do(self, cmd):
        self.instrument.write(cmd)
        esr = int(self.instrument.query('*ESR?'))
        if esr: 
            self.instrument.write('*CLS')
            raise CommandError(cmd, esr)
                      
    def trigger(self, timeoutS=10):
        self._do('*TRG')
        tic = time.time()
        while 1:
            if int(self.instrument.query('STAT:EESR?')): break
            if time.time()-tic > abs(timeoutS): raise 'trigger timeout'
            
    def getValues(self, triggerFirst=True):
        """ return last values assuming 3P3W connection
        if triggering, must be in hold mode
         [ptotalW, VRMS1V, IRMS1A, angle1Deg, VRMS2V, IRMS2A, angle2Deg]
         timeout is based on instrument timeout
         """
        if triggerFirst: self.trigger()
        return [float(x) for x in self.instrument.query(':NUM:NORM:VAL?').split(',')]
        
    def close(self):
        self.instrument.close()
# if __name__=='__main__': #test
#     i = '172.24.245.58'
#     y = Yoko1800(i)
#     while 1:
#         print(y.getValues()[0]+y.getValues()[1])

