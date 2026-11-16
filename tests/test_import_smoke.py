"""Import modules that are safe to import (no hardware access or GUI start at import time).

Most test scripts connect to instruments at module level, so they are covered by
test_compile_all instead of being imported here.
"""
from __future__ import annotations

import importlib

import pytest

SAFE_MODULES = [
    "suite_common",
    "suite_common.bench",
    "suite_common.bootstrap",
    "suite_common.config",
    "suite_common.inputs",
    "suite_common.output_folders",
    "suite_common.paths",
    "suite_common.powerpoint",
    "suite_sdk",
    "suite_sdk.context",
    "suite_sdk.registry",
    "suite_sdk.schema",
    "Transparency_Test_Suite_Launcher",
    "Paramount_Data_Collection_Unified_GUI",
    "Paramount_Data_Analysis_Unified_GUI",
    "Paramount_PowerPoint_Builder",
    "AE_Com_Module",
    "AECommandsUniversal",
    "AE_Commands_7434753",
    "AECommands",
    "Lynx",
]


@pytest.mark.parametrize("module", SAFE_MODULES)
def test_imports(module):
    importlib.import_module(module)
