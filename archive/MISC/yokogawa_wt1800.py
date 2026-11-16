import pyvisa

class YokogawaWT1800:
    def __init__(self, ip_address):
        self.resource_string = f"TCPIP0::{ip_address}::INSTR"
        self.rm = pyvisa.ResourceManager()
        self.instrument = self.rm.open_resource(self.resource_string)
        self.instrument.timeout = 10000  # milliseconds
        # self.instrument.write_termination = '\n'
        # self.instrument.read_termination = '\n'
        self.instrument.write('COMM:REM ON')

    def get_id(self):
        return self.instrument.query("*IDN?")

    def get_active_power(self):
        self.instrument.write(':NUM:NORM:ITEM1 P,SIGMA')
        return self.instrument.query("NUM:NORM:VAL? 1")

    def get_apparent_power(self):
        self.instrument.write(':NUM:NORM:ITEM1 S,SIGMA')
        return self.instrument.query("NUM:VAL? 1")

    def get_power_factor(self):
        self.instrument.write(':NUM:NORM:ITEM1 LAMBda,SIGMA')
        return self.instrument.query("NUM:VAL? 1")
    
    def get_ave_voltage(self):
        self.instrument.write(':NUM:NORM:ITEM1 U,SIGMA')
        return self.instrument.query("NUM:VAL? 1")
    
    def get_ave_current(self):
        self.instrument.write(':NUM:NORM:ITEM1 I,SIGMA')
        return self.instrument.query("NUM:VAL? 1")

    def get_voltage_phase(self, phase):
        """Get voltage for a specific phase: 1, 2, or 3"""
        self.instrument.write(':NUM:NORM:ITEM1 U,'+ str(phase))
        return self.instrument.query(f"NUM:VAL? 1")

    def get_current_phase(self, phase):
        """Get current for a specific phase: 1, 2, or 3"""
        self.instrument.write(':NUM:NORM:ITEM1 I,'+ str(phase))
        return self.instrument.query(f"NUM:VAL? 1")

    def get_all_phase_voltages(self):
        return {
            "V1": self.get_voltage_phase(1),
            "V2": self.get_voltage_phase(2),
            "V3": self.get_voltage_phase(3)
        }

    def get_all_phase_currents(self):
        return {
            "I1": self.get_current_phase(1),
            "I2": self.get_current_phase(2),
            "I3": self.get_current_phase(3)
        }

    def close(self):
        self.instrument.close()
