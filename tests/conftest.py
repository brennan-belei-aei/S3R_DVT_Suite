"""Shared pytest setup: repo root and AE dependencies on sys.path, plus fake-instrument fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import suite_common.bootstrap  # noqa: E402,F401  (puts AE dependency modules on sys.path)

from tests.fakes.ae_bus import FakeAEBus  # noqa: E402
from tests.fakes.visa import FakeResourceManager  # noqa: E402


@pytest.fixture
def ae_bus(monkeypatch):
    """Replace AE_Com_Module transports with a recorder shared by every connection."""
    import AE_Com_Module

    bus = FakeAEBus()
    monkeypatch.setattr(AE_Com_Module, "AETCP", bus.tcp_factory)
    monkeypatch.setattr(AE_Com_Module, "AEB32", bus.serial_factory)
    return bus


@pytest.fixture
def visa_rm(monkeypatch):
    """Replace pyvisa.ResourceManager with a recorder."""
    pyvisa = pytest.importorskip("pyvisa")
    rm = FakeResourceManager()
    monkeypatch.setattr(pyvisa, "ResourceManager", lambda *args, **kwargs: rm)
    return rm
