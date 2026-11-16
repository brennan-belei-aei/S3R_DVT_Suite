"""Bench equipment addresses shared by the unified GUI and every collection script.

Lookup order for each address: launcher environment variable, then the active
profile in runtime/Bench_Profiles.json, then the caller's fallback (normally blank).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from suite_common.config import bench_profiles_path

BENCH_PROFILE_ENV = "PARAMOUNT_BENCH_PROFILE"
DEFAULT_PROFILE_NAME = "Default Bench"


@dataclass(frozen=True)
class EquipmentField:
    key: str
    label: str
    env: str
    # "visa" values are normalized to a VISA resource; "host" and "com" are passed through.
    kind: str = "host"


EQUIPMENT_FIELDS = [
    EquipmentField("generator", "Generator (DUT) hostname / IP", "PARAMOUNT_GENERATOR_ADDRESS"),
    EquipmentField("generator_com_port", "Generator AEBus COM port", "PARAMOUNT_GENERATOR_COM_PORT", "com"),
    EquipmentField("psync_slave_generator", "Pulse Sync slave generator hostname / IP", "PARAMOUNT_PSYNC_SLAVE_ADDRESS"),
    EquipmentField("lynx", "Lynx hostname / IP", "PARAMOUNT_LYNX_ADDRESS"),
    EquipmentField("navigator", "Navigator / tuner COM port", "PARAMOUNT_NAVIGATOR_PORT", "com"),
    EquipmentField("oscilloscope", "Oscilloscope IP or VISA address", "PARAMOUNT_OSCILLOSCOPE_ADDRESS", "visa"),
    EquipmentField("waveform_generator", "Waveform generator IP or VISA address", "PARAMOUNT_WAVEFORM_GENERATOR_ADDRESS", "visa"),
    EquipmentField("spectrum_analyzer", "Spectrum analyzer IP", "PARAMOUNT_SPECTRUM_ANALYZER_ADDRESS"),
    EquipmentField("dc_supply", "DC supply (BK 9141) IP or VISA address", "PARAMOUNT_DC_SUPPLY_ADDRESS", "visa"),
]
FIELD_BY_KEY = {item.key: item for item in EQUIPMENT_FIELDS}


def empty_addresses() -> dict[str, str]:
    return {item.key: "" for item in EQUIPMENT_FIELDS}


def load_bench_profiles(path: Path | None = None) -> dict:
    path = path or bench_profiles_path()
    data = {"active_profile": DEFAULT_PROFILE_NAME, "profiles": {DEFAULT_PROFILE_NAME: empty_addresses()}}
    if not path.is_file():
        return data
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"Warning: could not read {path.name}: {error}")
        return data
    profiles = saved.get("profiles")
    if isinstance(profiles, dict) and profiles:
        data["profiles"] = {
            str(name): {**empty_addresses(), **{k: str(v) for k, v in values.items() if k in FIELD_BY_KEY}}
            for name, values in profiles.items()
            if isinstance(values, dict)
        } or data["profiles"]
    active = str(saved.get("active_profile", ""))
    data["active_profile"] = active if active in data["profiles"] else next(iter(data["profiles"]))
    return data


def save_bench_profiles(data: dict, path: Path | None = None) -> None:
    path = path or bench_profiles_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def active_bench_addresses() -> dict[str, str]:
    data = load_bench_profiles()
    name = os.environ.get(BENCH_PROFILE_ENV, "").strip() or data["active_profile"]
    return data["profiles"].get(name, data["profiles"][data["active_profile"]])


def visa_resource(value: str) -> str:
    """Accept a bare IP/hostname or a full VISA resource string."""
    text = str(value).strip()
    if not text or "::" in text:
        return text
    return f"TCPIP0::{text}::INSTR"


def visa_socket_resource(value: str, port: int = 5025) -> str:
    text = str(value).strip()
    if not text:
        return ""
    host = text.split("::")[1] if "::" in text else text
    return f"TCPIP0::{host}::{port}::SOCKET"


def bench_default(equipment: str, fallback: str = "") -> str:
    field = FIELD_BY_KEY[equipment]
    value = os.environ.get(field.env, "").strip()
    if not value:
        value = str(active_bench_addresses().get(equipment, "")).strip()
    value = value or fallback
    return visa_resource(value) if field.kind == "visa" else value


def detect_instruments(timeout_ms: int = 2000) -> list[str]:
    """Return VISA resources (including ASRL/COM ports) visible to this PC."""
    try:
        import pyvisa
    except ImportError:
        return []
    try:
        manager = pyvisa.ResourceManager()
        manager.timeout = timeout_ms
        return sorted(manager.list_resources())
    except Exception as error:  # VISA backends raise backend-specific errors
        print(f"Instrument detection failed: {error}")
        return []
