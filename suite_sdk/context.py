"""SuiteContext: what the launcher selected (product, firmware, DUT adapter) for this session.

The launcher saves it to runtime/suite_context.json and passes that path to the GUIs (and on
to every child script) in PARAMOUNT_SUITE_CONTEXT. Process managers copy it into each
run/analysis manifest.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from suite_common.process_protocol import SUITE_CONTEXT_ENV

SCHEMA_VERSION = 1
DEFAULT_DUT_ADAPTER = "AECommandsUniversal"
VERIFIED, UNVERIFIED, UNAVAILABLE = "verified", "unverified", "unavailable"


@dataclass
class SuiteContext:
    product_id: str = "UNSPECIFIED"
    product_name: str = ""
    firmware: str = ""
    dut_adapter: str = DEFAULT_DUT_ADAPTER
    adapter_compatibility: str = UNVERIFIED
    bench_profile: str = ""
    product_profiles_revision: str = ""
    bench_profiles_revision: str = ""
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    paths: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SuiteContext":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "SuiteContext":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> "SuiteContext | None":
        """The context the launcher handed to this process, if any."""
        value = (environment if environment is not None else os.environ).get(SUITE_CONTEXT_ENV, "").strip()
        if not value or not Path(value).is_file():
            return None
        try:
            return cls.load(Path(value))
        except (OSError, ValueError):
            return None

    @classmethod
    def current(cls, environment: dict[str, str] | None = None) -> "SuiteContext":
        """The launcher's context, or one built from PARAMOUNT_* variables for a standalone run."""
        environment = environment if environment is not None else dict(os.environ)
        context = cls.from_environment(environment)
        if context is not None:
            return context
        return cls(
            product_id=environment.get("PARAMOUNT_PRODUCT_ID", "") or "UNSPECIFIED",
            product_name=environment.get("PARAMOUNT_PRODUCT_NAME", ""),
            firmware=environment.get("PARAMOUNT_FIRMWARE_VERSION", ""),
            dut_adapter=environment.get("PARAMOUNT_DUT_ADAPTER", "") or DEFAULT_DUT_ADAPTER,
            adapter_compatibility=environment.get("PARAMOUNT_ADAPTER_COMPATIBILITY", "") or UNVERIFIED,
            bench_profile=environment.get("PARAMOUNT_BENCH_PROFILE", ""),
        )


def firmware_entries(product: dict) -> list[dict]:
    """Firmware rows of a product profile (schema v2); empty for schema v1 products."""
    return [entry for entry in product.get("firmware", []) if isinstance(entry, dict) and entry.get("version")]


def resolve_adapter(product: dict, firmware: str, adapter_dir: Path) -> tuple[str, str, str]:
    """(dut_adapter, compatibility, message) for a product + firmware choice.

    A listed firmware uses its profile's adapter and compatibility. Unlisted firmware falls back to
    DEFAULT_DUT_ADAPTER as "unverified". A missing adapter module is "unavailable" (do not launch).
    """
    firmware = firmware.strip()
    entry = next((e for e in firmware_entries(product) if e["version"].strip().upper() == firmware.upper()), None)
    if entry is None:
        adapter = DEFAULT_DUT_ADAPTER
        compatibility = UNVERIFIED
        name = product.get("display_name") or product.get("id", "")
        message = (f"Firmware {firmware} is not listed for {name}; using {adapter} (unverified)."
                   if firmware else f"No firmware selected; using {adapter} (unverified).")
    else:
        adapter = str(entry.get("dut_adapter") or DEFAULT_DUT_ADAPTER)
        compatibility = VERIFIED if str(entry.get("compatibility", "")).lower() == VERIFIED else UNVERIFIED
        message = f"{adapter} is {compatibility} with firmware {entry['version']}."
    if not (Path(adapter_dir) / f"{adapter}.py").is_file():
        return adapter, UNAVAILABLE, f"DUT adapter module {adapter}.py was not found in {adapter_dir}."
    return adapter, compatibility, message