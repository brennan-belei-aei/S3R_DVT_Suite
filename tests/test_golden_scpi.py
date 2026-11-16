"""Migrated collection scripts must drive their instruments exactly as before (Phase 6 part 2).

Each scenario runs the script's real collection routine on a scripted bench and compares the full
ordered instrument log (VISA opens, attribute sets, writes, queries, binary reads, closes, and
AE-bus frames) with the golden log recorded from the script before migration.
"""
from __future__ import annotations

import difflib

import pytest

from tests.tools.golden_scpi import SCENARIOS, load_golden, run_scenario


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_instrument_log_matches_golden(name, tmp_path):
    golden = load_golden(name)
    current = run_scenario(name, tmp_path)
    assert current["outcome"] == golden["outcome"]
    if current["log"] != golden["log"]:
        diff = "\n".join(list(difflib.unified_diff(golden["log"], current["log"], "golden", "current", lineterm="", n=2))[:40])
        pytest.fail(f"instrument log changed:\n{diff}")
    assert golden["outcome"] == "return 0", "golden must cover the successful collection path"
