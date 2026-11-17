"""InstrumentFactory: turn a bench profile into instrument objects, by role.

    factory = InstrumentFactory.from_bench()
    factory.require(plugin_test)              # fail early: missing address / adapter / capability
    gen = factory.create("generator")         # AECommandsUniversal.generator, frames logged
    scope = factory.create("oscilloscope", timeout_ms=60000)   # VisaTransport

Addresses follow the suite's lookup order (PARAMOUNT_* environment variable, then the bench
profile). The adapter for each role comes from the profile's "adapters" entry in
Bench_Profiles.json, else suite_instruments.adapters.DEFAULT_ADAPTERS.
"""
from __future__ import annotations

import os
from typing import Any

from suite_common.bench import BENCH_PROFILE_ENV, EQUIPMENT_FIELDS, load_bench_profiles
from suite_instruments.adapters import ADAPTERS, DEFAULT_ADAPTERS, AdapterSpec
from suite_instruments.errors import InstrumentCapabilityError, InstrumentError, InstrumentNotConfigured
from suite_instruments.interfaces import ROLE_INTERFACES

def _normalize(requirements: Any) -> list[tuple[str, frozenset[str]]]:
    """Accept a PluginTest (uses .capabilities), a {role: caps} dict, or role / (role, caps) items."""
    if hasattr(requirements, "capabilities") and hasattr(requirements, "equipment"):
        requirements = {role: requirements.capabilities.get(role, ()) for role in requirements.equipment}
    if isinstance(requirements, dict):
        return [(role, frozenset(caps)) for role, caps in requirements.items()]
    return [(item, frozenset()) if isinstance(item, str) else (item[0], frozenset(item[1])) for item in requirements]


class InstrumentFactory:
    def __init__(self, addresses: dict[str, str], adapters: dict[str, str] | None = None, *, profile: str = ""):
        self.addresses = {role: str(value).strip() for role, value in addresses.items()}
        self.adapters = dict(adapters or {})
        self.profile = profile

    @classmethod
    def from_bench(cls, profile: str | None = None, environment: dict[str, str] | None = None) -> "InstrumentFactory":
        environment = os.environ if environment is None else environment
        data = load_bench_profiles()
        name = profile or environment.get(BENCH_PROFILE_ENV, "").strip() or data["active_profile"]
        values = data["profiles"].get(name, data["profiles"][data["active_profile"]])
        addresses = {f.key: environment.get(f.env, "").strip() or values.get(f.key, "") for f in EQUIPMENT_FIELDS}
        return cls(addresses, data.get("adapters", {}).get(name, {}), profile=name)

    def adapter_name(self, role: str) -> str:
        return self.adapters.get(role) or DEFAULT_ADAPTERS.get(role, "")

    def adapter(self, role: str) -> AdapterSpec:
        name = self.adapter_name(role)
        spec = ADAPTERS.get(name)
        if spec is None:
            raise InstrumentNotConfigured(f"no adapter '{name}' (known: {', '.join(sorted(ADAPTERS))})", role=role)
        if role not in spec.roles:
            raise InstrumentNotConfigured(f"adapter '{name}' cannot serve this role", role=role)
        return spec

    def check(self, requirements: Any) -> list[str]:
        """Problems that would stop a test from getting its instruments (empty = ready)."""
        problems = []
        for role, capabilities in _normalize(requirements):
            if role not in ROLE_INTERFACES:
                problems.append(f"{role}: unknown instrument role")
                continue
            if not self.addresses.get(role):
                problems.append(f"{role}: no address in bench profile '{self.profile}'")
            try:
                spec = self.adapter(role)
            except InstrumentNotConfigured as error:
                problems.append(str(error))
                continue
            missing = sorted(capabilities - spec.capabilities)
            if missing:
                problems.append(f"{role}: adapter '{spec.name}' lacks capabilities {missing}")
        return problems

    def require(self, requirements: Any) -> None:
        problems = self.check(requirements)
        if problems:
            kind = InstrumentCapabilityError if all("lacks capabilities" in p for p in problems) else InstrumentNotConfigured
            raise kind("; ".join(problems))

    def create(self, role: str, *, address: str | None = None, **options: Any) -> Any:
        """Open the instrument for `role`. `address` overrides the profile's (e.g. a ::SOCKET fallback);
        other options go to the adapter (e.g. timeout_ms, resource_manager, open_attempts for SCPI)."""
        if role not in ROLE_INTERFACES:
            raise InstrumentNotConfigured("unknown instrument role", role=role)
        address = (address if address is not None else self.addresses.get(role, "")).strip()
        if not address:
            raise InstrumentNotConfigured(f"no address in bench profile '{self.profile}'", role=role)
        spec = self.adapter(role)
        instrument = spec.create(address, role, options)
        if spec.checks_interface and not isinstance(instrument, ROLE_INTERFACES[role]):
            raise InstrumentError(f"adapter '{self.adapter_name(role)}' does not implement {ROLE_INTERFACES[role].__name__}",
                                  role=role, resource=address)
        return instrument
