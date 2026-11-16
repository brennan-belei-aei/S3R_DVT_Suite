"""Normalized instrument errors. Vendor and transport exceptions are re-raised as these (with the
original chained as __cause__), so callers can handle any instrument the same way."""
from __future__ import annotations


class InstrumentError(Exception):
    """Base class. `role` and `resource` say which instrument failed."""

    def __init__(self, message: str, *, role: str = "", resource: str = ""):
        self.role = role
        self.resource = resource
        where = " ".join(part for part in (role, f"({resource})" if resource else "") if part)
        super().__init__(f"{where}: {message}" if where else message)


class InstrumentNotConfigured(InstrumentError):
    """The bench profile has no address (or an unknown adapter) for a required role."""


class InstrumentConnectionError(InstrumentError):
    """The instrument could not be opened."""


class InstrumentTimeout(InstrumentError, TimeoutError):
    """An I/O operation timed out."""


class InstrumentCapabilityError(InstrumentError):
    """The configured adapter lacks a capability the test requires."""
