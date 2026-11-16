"""The registry must keep every test, analysis, instruction string, and equipment list."""
from __future__ import annotations

import json

from tests.tools.dump_registry import BASELINE_PATH, current_registry

DEFINITION_FIELDS = {
    "collection_tests": ("key", "name", "equipment", "instructions", "image"),
    "analyses": ("key", "name", "description", "custom"),
}


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _definitions(entries: list[dict], fields: tuple[str, ...]) -> list[dict]:
    return [{f: e[f] for f in fields} for e in entries]


def test_definitions_match_baseline():
    baseline, current = _baseline(), current_registry()
    for section, fields in DEFINITION_FIELDS.items():
        assert _definitions(current[section], fields) == _definitions(baseline[section], fields), section
    assert sorted(current["rise_fall_modes"]) == sorted(baseline["rise_fall_modes"])
    assert current["legacy_collection_aliases"] == baseline["legacy_collection_aliases"]
    assert current["legacy_analysis_aliases"] == baseline["legacy_analysis_aliases"]


def test_nothing_discoverable_at_baseline_becomes_undiscoverable():
    baseline, current = _baseline(), current_registry()
    lost = []
    for section in ("collection_tests", "analyses"):
        now = {e["key"]: e["discovered_script"] for e in current[section]}
        lost += [f"{section}:{e['key']}" for e in baseline[section] if e["discovered_script"] and not now.get(e["key"])]
    for mode, entry in baseline["rise_fall_modes"].items():
        if entry["discovered_script"] and not current["rise_fall_modes"][mode]["discovered_script"]:
            lost.append(f"rise_fall_modes:{mode}")
    assert not lost, f"Scripts no longer discoverable: {lost}"
