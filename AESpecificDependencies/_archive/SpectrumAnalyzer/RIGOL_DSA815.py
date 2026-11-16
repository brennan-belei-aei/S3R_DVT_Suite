import pyvisa
from time import sleep
class SpectrumAnalyzer:
    def __init__(self, address):
        if len(address) > 3:
            self.instrument = pyvisa.ResourceManager().open_resource('TCPIP0::%s::INSTR'%address)
        else:
            self.instrument = pyvisa.ResourceManager().open_resource('GPIB0::%s::INSTR'%address)
        self.instrument.timeout = 8000
        # print(self.instrument.query('*IDN?'))
        self.instrument.write('FORMAT:DATA:ASCII')
        self.instrument.write('TRAC1:MODE:WRIT') #update trace 1 with live data
        self.instrument.write('INIT:CONT:OFF') #continous initiation off

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
        return float(self.instrument.query(':SENSe:FREQ:START?'))

    def getStopFrequency(self):
        return float(self.instrument.query(':SENSe:FREQ:STOP?'))

    def getInputAttenuation(self):
        return float(self.instrument.query(':SENSe:POWer:RF:ATTenuation?'))

    def getInputPortAttenuationAuto(self):
        return self.instrument.query(':SENSe:POWer:RF:ATTenuation:AUTO?')


    def getSpectrum(self, trace=1):
        self.instrument.write(':INIT:IMM')
        try:
            self.instrument.query('*OPC?')
        except:
            sleep(8)
        out = [str(x) for x in self.instrument.query(':TRAC:DATA? TRACE{}'.format(trace)).split(',')]
        holdB = out[0]
        holdE = out[-1]
        out[0] = holdB[11:]   
        out[-1] = holdE[:14]
        return list(map(float,out))
        
    
    def getSpectrumAndWriteToFile(self,R,X,P,F, trace=1):
        """w us if type csv.writer.
        Gets spectrum and writes to row in csv file"""
        row = [R, X, P, F]
        row.append(self.getStartFrequency())
        row.append(self.getStopFrequency())
        #row.extend(self.getSpectrum())
        data = self.getSpectrum()
        row.append(data)
        # w.writerow(row)
        return data

    def close(self):
        self.instrument.close()


