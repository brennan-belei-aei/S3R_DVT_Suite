"""Adapter catalog: which vendor/model code serves which bench role, and what it can do.

Each adapter is created through the existing driver's own constructor (so connection behavior
is unchanged) and then has its connection wrapped by a transport for logging and error
normalization. Bench profiles pick an adapter per role (Bench_Profiles.json "adapters");
otherwise DEFAULT_ADAPTERS applies.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable

from suite_instruments.errors import InstrumentConnectionError

# Capabilities shared by every AECommandsUniversal-based generator.
UNIVERSAL_GENERATOR_CAPS = frozenset({
    "aebus", "rf_on_off", "power_setpoint", "regulation_mode", "pulsing", "dlp", "fastdaq",
    "frequency_tuning", "setpoint_ramping", "process_status",
})


@dataclass(frozen=True)
class AdapterSpec:
    name: str
    roles: frozenset[str]
    capabilities: frozenset[str]
    description: str
    create: Callable[[str, str, dict], Any]  # (address, role, options) -> instrument


def _ae_module(name: str):
    importlib.import_module("suite_common.bootstrap")  # AE command modules are imported by flat name
    return importlib.import_module(name)


def _connect(role: str, address: str, build: Callable[[], Any]) -> Any:
    try:
        return build()
    except Exception as error:
        raise InstrumentConnectionError(f"connect failed: {error}", role=role, resource=address) from error


def _wrapped(instrument: Any, attribute: str, role: str, address: str) -> Any:
    from suite_instruments.transport.ae_com import wrap_connection_attribute
    return wrap_connection_attribute(instrument, attribute, role=role, address=address)


def _ae_universal(address: str, role: str, options: dict) -> Any:
    module = _ae_module("AECommandsUniversal")
    return _wrapped(_connect(role, address, lambda: module.generator(address)), "gen", role, address)


def _ae_7434753(address: str, role: str, options: dict) -> Any:
    module = _ae_module("AE_Commands_7434753")
    return _wrapped(_connect(role, address, lambda: module.generator(address)), "gen", role, address)


def _ae_serial_timing(address: str, role: str, options: dict) -> Any:
    module = _ae_module("AECommandsSerialTimingAdapter")
    return _wrapped(_connect(role, address, lambda: module.generator(0, address)), "gen", role, address)


def _ae_lynx(address: str, role: str, options: dict) -> Any:
    module = _ae_module("Lynx")
    return _wrapped(_connect(role, address, lambda: module.lynx(address)), "lynx", role, address)


def _ae_navigator(address: str, role: str, options: dict) -> Any:
    module = _ae_module("AECommands")
    return _wrapped(_connect(role, address, lambda: module.navigator(address)), "nav", role, address)


def _keysight_spectrum(address: str, role: str, options: dict) -> Any:
    from suite_instruments.adapters.keysight.spectrum_analyzer import SpectrumAnalyzer
    return _connect(role, address, lambda: SpectrumAnalyzer(address))


def _visa_scpi(address: str, role: str, options: dict) -> Any:
    from suite_common.bench import visa_resource
    from suite_instruments.transport.visa import VisaTransport
    return VisaTransport(visa_resource(address), role=role, **options)


ADAPTERS: dict[str, AdapterSpec] = {spec.name: spec for spec in (
    AdapterSpec("ae.universal", frozenset({"generator", "psync_slave_generator"}),
                UNIVERSAL_GENERATOR_CAPS | {"ethernet"},
                "AE generator over Ethernet AEBus (AECommandsUniversal).", _ae_universal),
    AdapterSpec("ae.7434753", frozenset({"generator", "psync_slave_generator"}),
                frozenset({"aebus", "ethernet", "rf_on_off", "power_setpoint", "regulation_mode", "pulsing",
                           "pulse_sync", "process_status"}),
                "AE generator over Ethernet AEBus, 7434753 firmware command set (AE_Commands_7434753).", _ae_7434753),
    AdapterSpec("ae.serial_timing", frozenset({"generator_com_port"}),
                UNIVERSAL_GENERATOR_CAPS | {"serial", "rf_on_test"},
                "AE generator over serial AEBus with single-frame RF ON (AECommandsSerialTimingAdapter).", _ae_serial_timing),
    AdapterSpec("ae.lynx", frozenset({"lynx"}), frozenset({"aebus", "power_measurement"}),
                "AE Lynx power meter (Lynx.lynx).", _ae_lynx),
    AdapterSpec("ae.navigator", frozenset({"navigator"}), frozenset({"aebus", "serial", "impedance_tuning"}),
                "AE Navigator matching network (AECommands.navigator).", _ae_navigator),
    AdapterSpec("keysight.n9320", frozenset({"spectrum_analyzer"}), frozenset({"scpi", "spectrum"}),
                "Keysight N9320B/N932xC spectrum analyzer driver.", _keysight_spectrum),
    AdapterSpec("visa.scpi", frozenset({"oscilloscope", "waveform_generator", "dc_supply", "spectrum_analyzer"}),
                frozenset({"scpi"}),
                "Generic SCPI session (VisaTransport); vendor-specific adapters replace it per model.", _visa_scpi),
)}

def csr_check(response) -> None:
    """AECommandsUniversal.csrChecker, for scripts that send raw frames (gen.gen.create_buffer_cmd)."""
    _ae_module("AECommandsUniversal").csrChecker(response)


DEFAULT_ADAPTERS: dict[str, str] = {
    "generator": "ae.universal",
    "psync_slave_generator": "ae.universal",
    "generator_com_port": "ae.serial_timing",
    "lynx": "ae.lynx",
    "navigator": "ae.navigator",
    "spectrum_analyzer": "keysight.n9320",
    "oscilloscope": "visa.scpi",
    "waveform_generator": "visa.scpi",
    "dc_supply": "visa.scpi",
}
