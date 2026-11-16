"""Migrated collection scripts must drive their instruments exactly as before (Phase 6 part 2).

Each scenario runs the script's real collection routine on a scripted bench and compares the full
ordered instrument log (VISA opens, attribute sets, writes, queries, binary reads, closes, and
AE-bus frames) with the golden log recorded from the script before migration.
"""
from __future__ import annotations

import difflib
import re
import tempfile
from pathlib import Path

import pytest

from tests.tools.golden_scpi import REPO_ROOT, SCENARIOS, load_golden, run_scenario

MIGRATED_SCRIPTS = sorted({scenario.script for scenario in SCENARIOS.values()})
DIRECT_IMPORT = re.compile(
    r"^\s*(import|from)\s+(pyvisa|AE_Com_Module|AECommandsUniversal|AECommands|AE_Commands_7434753|Lynx|AECommandsSerialTimingAdapter)\b",
    re.MULTILINE,
)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_instrument_log_matches_golden(name):
    golden = load_golden(name)
    # Short base folder: run-folder + waveform file names would exceed Windows' 260-character
    # path limit under pytest's deep tmp_path.
    with tempfile.TemporaryDirectory(prefix="g") as folder:
        current = run_scenario(name, Path(folder))
    assert current["outcome"] == golden["outcome"]
    if current["log"] != golden["log"]:
        diff = "\n".join(list(difflib.unified_diff(golden["log"], current["log"], "golden", "current", lineterm="", n=2))[:40])
        pytest.fail(f"instrument log changed:\n{diff}")
    assert golden["outcome"] == "return 0", "golden must cover the successful collection path"


@pytest.mark.parametrize("script", MIGRATED_SCRIPTS)
def test_migrated_script_uses_the_instrument_layer(script):
    source = (REPO_ROOT / script).read_text(encoding="utf-8")
    assert not DIRECT_IMPORT.findall(source), "migrated scripts get instruments from suite_instruments"
    assert "InstrumentFactory" in source
