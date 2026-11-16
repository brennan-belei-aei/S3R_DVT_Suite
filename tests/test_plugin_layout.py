"""Phase 4 layout: plugins live in <kind>_plugins/<id>/, and every plugin script still runs from there."""
from __future__ import annotations

import ast
import json
import subprocess
import sys

import pytest

from suite_sdk.registry import load_registry
from tests.conftest import REPO_ROOT

PLUGIN_SCRIPTS = sorted(
    p for folder in ("collection_plugins", "analysis_plugins") for p in (REPO_ROOT / folder).rglob("*.py")
)
SELF_TEST_SCRIPTS = [p for p in PLUGIN_SCRIPTS if '"--self-test"' in p.read_text(encoding="utf-8")]
# Modules that adjust sys.path themselves at import time: the probe imports them instead.
SELF_LOCATING = {"AECommandsSerialTimingAdapter.py"}
ROOT_LINE = 'sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))'

# Run in a fresh interpreter: start like `python <script>` (script folder first on sys.path),
# apply the script's own repo-root line and AE bootstrap, then resolve every module-level import.
IMPORT_PROBE = r"""
import importlib, importlib.util, json, sys
from pathlib import Path
script = Path(sys.argv[1]); modules = json.loads(sys.argv[2])
sys.path[0] = str(script.parent)
if sys.argv[3] == "1":
    sys.path.insert(0, str(next(p for p in script.resolve().parents if (p / "suite_common").is_dir())))
if sys.argv[4] == "1":
    importlib.import_module(script.stem)
    print("[]"); raise SystemExit
if "suite_common.bootstrap" in modules:
    importlib.import_module("suite_common.bootstrap")
missing = []
for name in modules:
    try:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    except ImportError:
        missing.append(name)
print(json.dumps(missing))
"""


def _module_imports(path):
    names = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and node.module != "__future__":
            names.append(node.module)
    return names


def test_plugin_files_live_in_kind_folders_named_by_id():
    registry = load_registry()
    assert len(registry.plugins) == 26
    for plugin in registry.plugins:
        assert plugin.path == REPO_ROOT / f"{plugin.kind}_plugins" / plugin.id / "plugin.json"
        for test in plugin.tests:
            assert test.script.parent == plugin.directory
            assert all(path.parent == plugin.directory for path in test.modes.values())


def test_every_plugin_folder_has_a_plugin_file():
    for kind in ("collection", "analysis"):
        folders = [d for d in (REPO_ROOT / f"{kind}_plugins").iterdir() if d.is_dir() and d.name != "__pycache__"]
        assert all((d / "plugin.json").is_file() for d in folders)
        assert all(json.loads((d / "plugin.json").read_text(encoding="utf-8"))["kind"] == kind for d in folders)


@pytest.mark.parametrize("script", PLUGIN_SCRIPTS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_no_fixed_depth_repo_root(script):
    source = script.read_text(encoding="utf-8")
    assert "parents[" not in source.replace("list(APP_DIR.parents)[", "")
    if "suite_common" in source:
        assert ROOT_LINE in source, "use the depth-independent repo-root line before importing suite_common"


@pytest.mark.parametrize("script", PLUGIN_SCRIPTS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_module_imports_resolve_from_new_location(script):
    modules = _module_imports(script)
    uses_root = "1" if ROOT_LINE in script.read_text(encoding="utf-8") else "0"
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE, str(script), json.dumps(modules), uses_root,
         "1" if script.name in SELF_LOCATING else "0"],
        cwd=script.parent, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


@pytest.mark.parametrize("script", SELF_TEST_SCRIPTS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_plugin_self_test_runs_from_new_location(script, tmp_path):
    # Run from an unrelated folder so nothing depends on the current directory.
    result = subprocess.run(
        [sys.executable, "-W", "ignore", str(script), "--self-test"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "self-test passed" in result.stdout
