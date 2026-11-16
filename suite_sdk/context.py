"""SuiteContext: what the launcher selected (product, firmware, DUT adapter, bench) for this session.

The launcher writes it to runtime/suite_context.json and passes its path to the GUIs and
their child scripts in PARAMOUNT_SUITE_CONTEXT. Phase 5 wires this up end to end.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

SUITE_CONTEXT_ENV = "PARAMOUNT_SUITE_CONTEXT"
SCHEMA_VERSION = 1


@dataclass
class SuiteContext:
    product_id: str = "UNSPECIFIED"
    product_name: str = ""
    firmware: str = ""
    dut_adapter: str = "AECommandsUniversal"
    adapter_compatibility: str = "unverified"
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
    def from_environment(cls) -> "SuiteContext | None":
        """The context the launcher handed to this process, if any."""
        value = os.environ.get(SUITE_CONTEXT_ENV, "").strip()
        if not value or not Path(value).is_file():
            return None
        return cls.load(Path(value))
