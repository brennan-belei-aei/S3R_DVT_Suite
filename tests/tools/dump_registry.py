"""Dump the suite's test/analysis registry to JSON.

Reads what the unified GUIs actually show (TESTS / BUILTIN_ANALYSES, now built from the
plugin registry) so the snapshot test proves the Phase 0 lists survived every refactor.

    .venv\\Scripts\\python -m tests.tools.dump_registry --write   # regenerate the baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "registry_baseline.json"


def _discovered(script: str) -> str | None:
    path = Path(script)
    return path.name if path.is_file() else None


def current_registry() -> dict:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from suite_apps import analysis_gui, collection_gui

    collection = [
        {
            "key": t.key,
            "name": t.name,
            "equipment": list(t.equipment),
            "instructions": t.instructions,
            "image": t.image,
            "script_candidates": [Path(t.script).name],
            "discovered_script": _discovered(t.script),
        }
        for t in collection_gui.TESTS
    ]
    rise_fall_modes = {
        mode: {"script_candidates": [Path(script).name], "discovered_script": _discovered(script)}
        for mode, script in collection_gui.RISE_FALL_SCRIPTS.items()
    }
    analyses = [
        {
            "key": a.key,
            "name": a.name,
            "description": a.description,
            "custom": a.custom,
            "script_candidates": list(a.default_filenames),
            "discovered_script": _discovered(a.default_script),
        }
        for a in analysis_gui.BUILTIN_ANALYSES
    ]
    return {
        "collection_tests": collection,
        "rise_fall_modes": rise_fall_modes,
        "analyses": analyses,
        "legacy_collection_aliases": {
            "analog_port_timing": collection_gui.migrate_selection(["analog_port_timing"])
        },
        "legacy_analysis_aliases": {
            alias: analysis_gui.migrate_analysis_entries([alias])
            for alias in ("t4", "t5", "t6", "t7", "t8", "analog_port_timing")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="overwrite the committed baseline")
    args = parser.parse_args()
    data = current_registry()
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if args.write:
        BASELINE_PATH.write_text(text, encoding="utf-8")
        print(f"Wrote {BASELINE_PATH}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
