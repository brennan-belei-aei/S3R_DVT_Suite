"""Phase 6: Instrument Abstraction Layer (suite_instruments)."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

import suite_instruments.factory as factory_module
from suite_common import bench
from suite_common.bench import EQUIPMENT_FIELDS
from suite_instruments.adapters import ADAPTERS, DEFAULT_ADAPTERS
from suite_instruments.errors import (
    InstrumentCapabilityError, InstrumentConnectionError, InstrumentError, InstrumentNotConfigured, InstrumentTimeout,
)
from suite_instruments.factory import InstrumentFactory
from suite_instruments.interfaces import ROLE_INTERFACES, Generator
from suite_instruments.transport.ae_com import AeBusTransport
from suite_instruments.transport.visa import VisaTransport
from suite_sdk.registry import load_registry
from tests.conftest import REPO_ROOT
from tests.fakes.visa import FakeInstrument, FakeResourceManager
from tests.tools.golden_frames import fake_bus, load_fixture

GOLDEN = load_fixture("dut_adapter_pmnt_hf_1xx.json")


def _outcome(call):
    try:
        return "return " + repr(call())
    except Exception as error:
        return f"raise {type(error).__name__}: {error}"


def test_factory_generator_sends_identical_frames_to_golden_recording():
    """Every recorded AE-bus case replays identically through a factory-built (logging) generator."""
    factory = InstrumentFactory({"generator": "GOLDEN"})
    mismatches = []
    for case in GOLDEN:
        with fake_bus(case["profile"]) as bus:
            gen = factory.create("generator")
            outcome = _outcome(lambda: getattr(gen, case["method"])(*case["args"]))
        frames = [repr((f.cmd, f.send_sizes, f.recv_sizes, f.args)) for f in bus.frames]
        if (frames, outcome) != (case["frames"], case["outcome"]):
            mismatches.append(case["method"])
        assert isinstance(gen.gen, AeBusTransport) and gen.gen.frames_sent == len(frames)
    assert not mismatches, sorted(set(mismatches))


@pytest.mark.parametrize("role, address, attribute", [
    ("generator", "PMNT000001", "gen"),
    ("generator_com_port", "COM5", "gen"),
    ("lynx", "COM7", "lynx"),
    ("navigator", "COM9", "nav"),
])
def test_ae_roles_are_built_by_their_existing_drivers_and_wrapped(ae_bus, role, address, attribute):
    instrument = InstrumentFactory({role: address}).create(role)
    transport = getattr(instrument, attribute)
    assert isinstance(transport, AeBusTransport)
    assert transport.connection is ae_bus.transports[0]
    assert isinstance(instrument, ROLE_INTERFACES[role])


def test_generator_connect_failure_is_normalized(monkeypatch):
    import AE_Com_Module

    class Refused:
        def connect(self, address):
            raise OSError("connection refused")

    monkeypatch.setattr(AE_Com_Module, "AETCP", Refused)
    with pytest.raises(InstrumentConnectionError, match="generator \\(PMNT9\\): connect failed") as info:
        InstrumentFactory({"generator": "PMNT9"}).create("generator")
    assert isinstance(info.value.__cause__, OSError)


def test_scpi_roles_get_a_pass_through_visa_transport(visa_rm):
    scope = InstrumentFactory({"oscilloscope": "192.0.2.10"}).create("oscilloscope", timeout_ms=60000)
    assert isinstance(scope, VisaTransport)
    scope.write(":ACQ:STATE RUN")
    assert scope.query("*IDN?").startswith("FAKE,")
    raw = visa_rm.instruments["TCPIP0::192.0.2.10::INSTR"]
    assert raw.log == [("write", ":ACQ:STATE RUN"), ("query", "*IDN?")]
    assert raw.timeout == 60000
    scope.read_termination = "\n"
    assert raw.read_termination == "\n" and scope.timeout == 60000
    scope.close()
    scope.close()
    assert raw.closed and visa_rm.closed
    with pytest.raises(InstrumentError, match="after close"):
        scope.write("*RST")


def test_visa_errors_are_normalized_with_cause():
    class Flaky(FakeInstrument):
        def query(self, command, *args, **kwargs):
            raise RuntimeError("VI_ERROR_TMO (-1073807339): Timeout expired before operation completed.")

        def write(self, command):
            raise RuntimeError("VI_ERROR_IO: I/O error")

    rm = FakeResourceManager(instruments={"R": Flaky("R")})
    session = VisaTransport("R", role="dc_supply", resource_manager=rm)
    with pytest.raises(InstrumentTimeout, match="dc_supply \\(R\\): query 'APPL\\?' failed") as info:
        session.query("APPL?")
    assert isinstance(info.value, TimeoutError) and isinstance(info.value.__cause__, RuntimeError)
    with pytest.raises(InstrumentError) as info:
        session.write("OUTP ON")
    assert not isinstance(info.value, InstrumentTimeout)


def test_visa_open_retries_then_normalizes():
    attempts = []

    class Unreliable(FakeResourceManager):
        def open_resource(self, resource, *args, **kwargs):
            attempts.append(resource)
            if len(attempts) < 2:
                raise OSError("busy")
            return super().open_resource(resource)

    VisaTransport("R", resource_manager=Unreliable(), open_attempts=2, retry_delay_s=0)
    assert len(attempts) == 2

    class Dead(FakeResourceManager):
        def open_resource(self, resource, *args, **kwargs):
            raise OSError("no route")

    with pytest.raises(InstrumentConnectionError, match="could not open"):
        VisaTransport("R", resource_manager=Dead(), open_attempts=3, retry_delay_s=0)


def test_check_reports_every_problem_and_require_raises():
    factory = InstrumentFactory(
        {"generator": "PMNT1", "oscilloscope": "", "dc_supply": "10.0.0.9", "lynx": "COM3"},
        {"dc_supply": "ae.lynx", "lynx": "no.such.adapter"},
        profile="Bench A",
    )
    problems = factory.check([
        ("generator", ["rf_on_off", "dlp"]),
        "oscilloscope",
        "dc_supply",
        "lynx",
        ("generator", ["teleportation"]),
        "toaster",
    ])
    assert problems == [
        "oscilloscope: no address in bench profile 'Bench A'",
        "dc_supply: adapter 'ae.lynx' cannot serve this role",
        "lynx: no adapter 'no.such.adapter' (known: " + ", ".join(sorted(ADAPTERS)) + ")",
        "generator: adapter 'ae.universal' lacks capabilities ['teleportation']",
        "toaster: unknown instrument role",
    ]
    with pytest.raises(InstrumentNotConfigured):
        factory.require(["oscilloscope"])
    with pytest.raises(InstrumentCapabilityError):
        factory.require({"generator": ["teleportation"]})
    with pytest.raises(InstrumentNotConfigured, match="no address"):
        factory.create("oscilloscope")


def test_every_listed_collection_test_can_be_served():
    """Each role a GUI test requires has an interface and a default adapter that supports it."""
    for test in load_registry().tests("collection"):
        factory = InstrumentFactory({role: "x" for role in test.equipment})
        assert factory.check(test) == [], test.key
    assert set(ROLE_INTERFACES) == {f.key for f in EQUIPMENT_FIELDS} == set(DEFAULT_ADAPTERS)


def test_from_bench_uses_env_override_and_profile_adapters(monkeypatch):
    monkeypatch.setattr(factory_module, "load_bench_profiles", lambda: {
        "active_profile": "Bench A",
        "profiles": {"Bench A": {"generator": "PMNT-A", "oscilloscope": "10.0.0.5"}, "Bench B": {"generator": "PMNT-B"}},
        "adapters": {"Bench B": {"oscilloscope": "visa.scpi"}},
    })
    factory = InstrumentFactory.from_bench(environment={"PARAMOUNT_OSCILLOSCOPE_ADDRESS": "10.9.9.9"})
    assert (factory.profile, factory.addresses["generator"], factory.addresses["oscilloscope"]) == ("Bench A", "PMNT-A", "10.9.9.9")
    factory = InstrumentFactory.from_bench(environment={"PARAMOUNT_BENCH_PROFILE": "Bench B"})
    assert (factory.addresses["generator"], factory.adapters) == ("PMNT-B", {"oscilloscope": "visa.scpi"})


def test_bench_profile_adapters_survive_load_and_save(tmp_path):
    path = tmp_path / "Bench_Profiles.json"
    path.write_text(json.dumps({"active_profile": "A", "profiles": {"A": {"generator": "G"}},
                                "adapters": {"A": {"oscilloscope": "visa.scpi"}}}), encoding="utf-8")
    data = bench.load_bench_profiles(path)
    data["profiles"]["A"]["oscilloscope"] = "10.0.0.5"
    bench.save_bench_profiles(data, path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["adapters"] == {"A": {"oscilloscope": "visa.scpi"}}
    assert saved["profiles"]["A"]["oscilloscope"] == "10.0.0.5"


def test_universal_generator_satisfies_generator_role():
    import AECommandsUniversal

    assert issubclass(AECommandsUniversal.generator, Generator)


@pytest.mark.parametrize("script, probe", [
    ("collection_plugins/arc_management/arc_management_collection.py", "module.load_universal_module().__name__"),
    ("collection_plugins/rf_tuning/frequency_tuning_collection.py",
     "__import__('importlib').import_module('AECommandsUniversal').__name__"),
])
def test_dynamic_loaders_find_the_adapter_modules(script, probe, tmp_path):
    """These scripts load AECommandsUniversal at run time; before Phase 6 that import could not resolve."""
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('probe', {str(REPO_ROOT / script)!r})\n"
        "module = importlib.util.module_from_spec(spec); sys.modules['probe'] = module; spec.loader.exec_module(module)\n"
        f"print({probe})\n"
    )
    result = subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "AECommandsUniversal"
