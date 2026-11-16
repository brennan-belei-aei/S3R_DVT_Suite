"""Vendor-neutral instrument roles.

These are structural Protocols: any adapter with the listed methods satisfies the role, so the
existing AE command modules qualify without being modified (AECommandsUniversal.generator *is*
the Generator implementation). Role-specific methods for the SCPI instruments are added as
collection scripts migrate onto the factory (Phase 6 part 2); until then they expose the SCPI
session itself, and the factory guarantees that session is the logged, error-normalizing
VisaTransport.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Instrument(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class ScpiInstrument(Instrument, Protocol):
    def write(self, command: str): ...
    def query(self, command: str) -> str: ...


@runtime_checkable
class Generator(Instrument, Protocol):
    """DUT RF generator (AEBus). Implemented by AECommandsUniversal.generator."""
    def rfON(self): ...
    def rfOFF(self): ...
    def setPowerSetpoint(self, setpoint): ...
    def setRegulationMode(self, mode): ...
    def reportActiveControlMode(self): ...


@runtime_checkable
class Oscilloscope(ScpiInstrument, Protocol):
    """Oscilloscope (SCPI)."""


@runtime_checkable
class WaveformGenerator(ScpiInstrument, Protocol):
    """Arbitrary/function waveform generator (SCPI)."""


@runtime_checkable
class DCSupply(ScpiInstrument, Protocol):
    """Programmable DC supply, e.g. BK Precision 9141 (SCPI)."""


@runtime_checkable
class SpectrumAnalyzer(Instrument, Protocol):
    """Spectrum analyzer. The Keysight adapter keeps its existing driver API."""


@runtime_checkable
class PowerMeter(Protocol):
    """RF power meter (Lynx, AEBus)."""


@runtime_checkable
class Tuner(Protocol):
    """Navigator / matching network (AEBus serial)."""


ROLE_INTERFACES: dict[str, type] = {
    "generator": Generator,
    "generator_com_port": Generator,
    "psync_slave_generator": Generator,
    "oscilloscope": Oscilloscope,
    "waveform_generator": WaveformGenerator,
    "dc_supply": DCSupply,
    "spectrum_analyzer": SpectrumAnalyzer,
    "lynx": PowerMeter,
    "navigator": Tuner,
}
