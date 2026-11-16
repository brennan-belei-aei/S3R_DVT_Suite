"""Compatibility adapter for Serial RF On-Off Latency collection.

This module intentionally preserves the small API expected by the existing
rf_onoff_scope_capture_GUI.py script while using the suite's shared dependencies:

    AE_Com_Module.py
    AECommandsUniversal.py

It does not copy or fork the universal command library. All normal generator
commands are inherited from AECommandsUniversal.generator. The only added command
is rfON_TEST(), which sends exactly one AEBus RF ON command so the oscilloscope
triggers on the command being measured rather than a preceding control-mode query.

The legacy constructor signature generator(ipAddress, comPort) is retained so the
working collection script does not need any other changes:

    generator(0, "COM5")        -> direct serial AEBus
    generator("PMNT012837", None) -> Ethernet AEBus
"""

import sys
from pathlib import Path

# Locate the suite's shared dependencies without assuming they are directly in
# Digital Timing or exactly one folder above it. Start at this adapter's folder
# and walk upward; at each level, search recursively for both required files.
# The directory containing both files is added to sys.path.
ADAPTER_DIR = Path(__file__).resolve().parent
DEPENDENCY_DIR = None
SEARCHED_ROOTS = []
for root in (ADAPTER_DIR, *ADAPTER_DIR.parents):
    SEARCHED_ROOTS.append(str(root))
    # Prefer direct placement before recursive search.
    if (root / "AE_Com_Module.py").is_file() and (root / "AECommandsUniversal.py").is_file():
        DEPENDENCY_DIR = root
        break
    try:
        com_candidates = list(root.rglob("AE_Com_Module.py"))
    except (OSError, PermissionError):
        continue
    for com_path in com_candidates:
        candidate = com_path.parent
        if (candidate / "AECommandsUniversal.py").is_file():
            DEPENDENCY_DIR = candidate
            break
    if DEPENDENCY_DIR is not None:
        break

if DEPENDENCY_DIR is None:
    raise ModuleNotFoundError(
        "Could not locate AE_Com_Module.py and AECommandsUniversal.py in the same folder. "
        "Place both shared dependency files together anywhere inside the repository. "
        f"Adapter location: {ADAPTER_DIR}."
    )

if str(DEPENDENCY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPENDENCY_DIR))

import AE_Com_Module
import AECommandsUniversal


class generator(AECommandsUniversal.generator):
    """Universal generator commands with serial transport and timed RF ON support."""

    def __init__(self, ipAddress, comPort=None):
        # Match the existing RF timing collector's connection convention.
        if ipAddress == 0 or comPort:
            if not comPort:
                raise ValueError("A COM port is required for serial generator control.")
            self.gen = AE_Com_Module.AEB32(comPort)
            self.connection_type = "serial"
            self.connection_target = comPort
        else:
            if not ipAddress:
                raise ValueError("An IP address or hostname is required for Ethernet control.")
            self.gen = AE_Com_Module.AETCP()
            self.gen.connect(ipAddress)
            self.connection_type = "ethernet"
            self.connection_target = ipAddress

        # Retain the attribute used by some older PMNT command workflows.
        self.PINNumber = None

    def rfON_TEST(self):
        """Send exactly one RF ON command and return its CSR.

        Do not replace this with inherited rfON() in the latency collector.
        inherited rfON() first reports active control mode and can resend RF ON,
        creating extra CH2 bursts that invalidate the oscilloscope timing reference.
        """
        response = self.gen.create_buffer_cmd(2, "0:0", "1:1")
        AECommandsUniversal.csrChecker(response)
        return response[2]
