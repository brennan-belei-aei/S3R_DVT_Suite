"""Dump the suite's test/analysis registry to JSON.

Phase 0 baseline: reads TESTS / BUILTIN_ANALYSES from the unified GUIs. After the plugin
registry lands, this function is the single place to switch to registry output; the
snapshot test then proves nothing was lost.

    .venv\\Scripts\\python -m tests.tools.dump_registry --write   # regenerate the baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "registry_baseline.json"


def _discovered(candidates: list[str]) -> str | None:
    from suite_common.paths import discover_script

    path = Path(discover_script(list(candidates)))
    return path.name if path.is_file() else None


def current_registry() -> dict:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import Paramount_Data_Analysis_Unified_GUI as analysis_gui
    import Paramount_Data_Collection_Unified_GUI as collection_gui

    collection = [
        {
            "key": t.key,
            "name": t.name,
            "equipment": list(t.equipment),
            "instructions": t.instructions,
            "image": t.image,
            "script_candidates": list(t.default_scripts),
            "discovered_script": _discovered(t.default_scripts),
        }
        for t in collection_gui.TESTS
    ]
    rise_fall_modes = {
        mode: {"script_candidates": list(c), "discovered_script": _discovered(c)}
        for mode, c in collection_gui.RISE_FALL_SCRIPT_CANDIDATES.items()
    }
    analyses = [
        {
            "key": a.key,
            "name": a.name,
            "description": a.description,
            "custom": a.custom,
            "script_candidates": list(a.default_filenames),
            "discovered_script": _discovered(a.default_filenames),
        }
        for a in analysis_gui.BUILTIN_ANALYSES
    ]
    return {
        "collection_tests": collection,
        "rise_fall_modes": rise_fall_modes,
        "analyses": analyses,
        "legacy_collection_aliases": {
            collection_gui.LEGACY_ANALOG_PORT_TIMING_KEY: list(collection_gui.ANALOG_PORT_TIMING_REPLACEMENT_KEYS)
        },
        "legacy_analysis_aliases": {
            alias: analysis_gui.migrate_legacy_analysis_entries([alias])
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
