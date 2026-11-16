"""The fakes must plug into the real command libraries, or later golden logs are meaningless."""
from __future__ import annotations

import pyvisa


def test_ae_bus_records_frames_from_universal_library(ae_bus):
    import AECommandsUniversal

    gen = AECommandsUniversal.generator("PMNT000000")
    gen.rfOFF()
    assert ae_bus.wire() == [(1, "0:0", "1:1", ())]
    assert ae_bus.frames[0].target == "PMNT000000"


def test_ae_bus_scripted_csr_error_reaches_library(ae_bus):
    import AECommandsUniversal

    ae_bus.reply(1, [0, 0, 7])
    gen = AECommandsUniversal.generator("PMNT000000")
    try:
        gen.rfOFF()
    except Exception as error:
        assert "Faults" in str(error)
    else:
        raise AssertionError("CSR 7 should raise")


def test_visa_rm_records_scpi(visa_rm):
    scope = pyvisa.ResourceManager().open_resource("TCPIP0::192.0.2.10::INSTR")
    scope.write(":ACQ:STATE RUN")
    assert scope.query("*IDN?").startswith("FAKE,")
    assert scope.log == [("write", ":ACQ:STATE RUN"), ("query", "*IDN?")]
