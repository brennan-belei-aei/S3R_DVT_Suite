"""AEBus Command Latency runs the rf_on_off_timing (Digital Timing) collectors through patched runtime copies."""
from __future__ import annotations

import importlib.util

import pytest

from tests.conftest import REPO_ROOT

GUI_PATH = REPO_ROOT / "collection_plugins" / "aebus_command_latency" / "AEBus_Command_Latency_Collection_GUI.py"

# The constants the GUI rewrites in each collector (see App.worker); renaming any of
# them in a collector breaks the latency test.
PATCHED_CONSTANTS = {
    "command_processing": ["COMM_TYPE", "IP_ADDRESS", "COM_PORT", "ITERATION", "CSV_PATH"],
    "rf_scope": ["COMM_TYPE", "IP_ADDRESS", "COM_PORT", "REPORT_ROOT"],
}


@pytest.fixture(scope="module")
def gui():
    spec = importlib.util.spec_from_file_location("aebus_latency_collection_gui", GUI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode", sorted(PATCHED_CONSTANTS))
def test_collectors_resolve_to_digital_timing(gui, mode):
    script = gui.find_script(mode)
    assert script.is_file()
    assert script.parent.name == "rf_on_off_timing"


@pytest.mark.parametrize("mode", sorted(PATCHED_CONSTANTS))
def test_runtime_copy_patches_every_constant(gui, mode, tmp_path):
    original = gui.find_script(mode)
    replacements = [
        (rf"^{name}\s*=.*$", f"{name} = 'PATCHED_{name}'", name) for name in PATCHED_CONSTANTS[mode]
    ]
    runtime = gui.runtime_copy(original, tmp_path, replacements)
    try:
        assert runtime.parent == original.parent, "runtime copy must sit beside the original for its imports"
        source = runtime.read_text(encoding="utf-8")
        for name in PATCHED_CONSTANTS[mode]:
            assert f"{name} = 'PATCHED_{name}'" in source
        compile(source, str(runtime), "exec")
    finally:
        runtime.unlink(missing_ok=True)
