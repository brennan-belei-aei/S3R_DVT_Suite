import pyvisa
from time import sleep
class SpectrumAnalyzer:
    def __init__(self, address):
        if len(address) > 3:
            self.instrument = pyvisa.ResourceManager().open_resource('TCPIP0::%s::inst0::INSTR'%address)
        else:
            self.instrument = pyvisa.ResourceManager().open_resource('GPIB0::%s::INSTR'%address)
        self.instrument.timeout = 8000
        # print(self.instrument.query('*IDN?'))
        self.instrument.write('FORMAT:DATA:ASCII')
        self.instrument.write('TRAC1:MODE:WRIT') #update trace 1 with live data
        self.instrument.write('INIT:CONT:OFF') #continous initiation off

        '''
        self.instrument.write_termination = '\n'  # what you SEND (N9320B accepts \n, \r, or \r\n)
        self.instrument.read_termination = '\n'  # what you EXPECT to RECEIVE (N9320B sends \n)
        self.instrument.timeout = 20000  # give yourself headroom if a sweep is in progress
        '''
    def reportID(self):
        return self.instrument.query('*IDN?')
    
    def singleSweep(self):
        self.instrument.write('INIT:CONT 0')
    
    def contSweep(self):
        self.instrument.write('INIT:CONT 1')

    def setResBW(self, BW):
        self.instrument.write(':SENSe:BANDwidth {}Hz'.format(BW))

    def setCenterFrequency(self, freq):
        self.instrument.query('*OPC?')
        self.instrument.write(':SENSe:FREQ:CENTer {}Hz'.format(freq))       

    def setFrequencySpan(self, start, stop):
        # self.instrument.query('*OPC?')
        self.instrument.write(':SENSe:FREQ:START {}Hz'.format(start))
        # self.instrument.query('*OPC?')
        self.instrument.write(':SENSe:FREQ:STOP {}Hz'.format(stop))

    def getCenterFrequency(self):
        return float(self.instrument.query(':SENSe:FREQ:CENTER?'))

    def getStartFrequency(self):
        return float(self.instrument.query(':SENSe:FREQ:STARt?'))

    def getStopFrequency(self):
        return float(self.instrument.query(':SENSe:FREQ:STOP?'))

    def getInputAttenuation(self):
        return float(self.instrument.query(':SENSe:POWer:RF:ATTenuation?'))
        
    # :POWer:ATTenuation 20dB
    def setInputAttenuation(self, att):
        att = ':POWer:ATTenuation '+ str(att)+'db'
        return self.instrument.write(att)

    def setOffsetLevel(self, db):
        offset = ':DISPlay:WINDow:TRACe:Y:RLEVel:OFFSet '+str(db)+'db'
        return self.instrument.write(offset)

    #  :CAT:SCALe:REFerence 12
    def setRefLevel(self, ref):
        ref = ':CAT:SCALe:REFerence '+ str(ref)
        return self.instrument.write(ref)
    

    def getInputPortAttenuationAuto(self):
        return self.instrument.query(':SENSe:POWer:RF:ATTenuation:AUTO?')

    def enableHighSense(self):
        return self.instrument.write('SENSe:POWer:RF:HSENsitive:STATe 1')
    
    def disableHighSense(self):
        return self.instrument.write('SENSe:POWer:RF:HSENsitive:STATe 0')
    

    def statusRead(self):
        status = self.instrument.query(':SENSe:POWer:RF:HSENsitive:STATe?')
        print(f"High Sensitivity Mode: {status}")

    # def getSpectrum(self, trace=1):
    #     self.instrument.write(':INIT:IMM')
    #     try:
    #         self.instrument.query('*OPC?')
    #     except:
    #         sleep(8)
    #     return [float(x) for x in \
    #                 self.instrument.query(':TRAC:DATA? TRACE{}'.format(trace)).split(',')]

    def getSpectrum(self, trace=1):
        """
        Perform a single sweep and return the trace amplitudes as a Python list of floats.
        Keeps ASCII CSV parsing to match your existing API.
        """
        # Make the sweep deterministic: one sweep per call
        self.instrument.write(':INIT:CONT OFF')  # single-sweep mode
        self.instrument.write(':INIT:IMM')  # start a sweep

        # Always read the OPC reply so nothing is left in the output queue
        # Prefer a generous timeout instead of swallowing exceptions.
        prev_to = getattr(self.instrument, 'timeout', None)
        try:
            if prev_to is not None and prev_to < 40000:
                self.instrument.timeout = 40000  # 40 s safety margin for long sweeps
            op = self.instrument.query('*OPC?')  # must read
            # '1\n' before any other query
            # Optional: sanity check
            # if op.strip() != '1':
            #     raise RuntimeError(f'Unexpected OPC reply: {op!r}')
        finally:
            if prev_to is not None:
                self.instrument.timeout = prev_to

        # Now it is safe to fetch the trace; parse CSV into floats as you requested
        csv = self.instrument.query(f':TRACe:DATA? TRACE{trace}')
        # (strip optional) csv = csv.strip()
        return [float(x) for x in csv.split(',')]

    def getSpectrumAndWriteToFile(self,w,R,X,P,F, trace=1):
        """w us if type csv.writer.
        Gets spectrum and writes to row in csv file"""
        row = [R, X, P, F]
        row.append(self.getStartFrequency())
        row.append(self.getStopFrequency())
        #row.extend(self.getSpectrum())
        data = self.getSpectrum()
        print(f"this is the data: {data}")

        row.append(data)
        w.writerow(row)
        return data, row

    def close(self):
        self.instrument.close()


