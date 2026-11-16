"""Phase 5: env contract, process managers, Run/Analysis Package manifests, and suite context."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap

import pytest

import suite_apps.process_manager as process_manager
from suite_apps.analysis_process_manager import AnalysisProcessManager
from suite_apps.collection_process_manager import CollectionProcessManager
from suite_common.manifests import ANALYSIS_MANIFEST, RUN_MANIFEST, missing_fields, sha256_file
from suite_common.process_protocol import DYNAMIC_PREFIXES, ENV_VARS, PROTOCOL_VERSION_ENV, RUN_ID_ENV, SUITE_CONTEXT_ENV
from suite_sdk.context import UNAVAILABLE, UNVERIFIED, VERIFIED, SuiteContext, resolve_adapter
from suite_sdk.registry import load_registry
from tests.conftest import REPO_ROOT

COLLECTOR = """
import os, sys
from pathlib import Path
run = Path(os.environ["PARAMOUNT_COLLECTION_PARENT_FOLDER"]) / "2026-01-01 - Fake Run"
run.mkdir()
(run / "waveform.csv").write_text("t,v\\n0,1\\n", encoding="utf-8")
(run / "seen_env.txt").write_text(os.environ["{run_id}"] + "|" + os.environ["{protocol}"], encoding="utf-8")
sys.exit({exit_code})
"""
ANALYZER = """
import os
from pathlib import Path
dataset = Path(os.environ["PARAMOUNT_DATASET_A_FOLDER"])
out = Path(os.environ["PARAMOUNT_ANALYSIS_OUTPUT_FOLDER"]) / "Fake Analysis Output"
out.mkdir()
(out / "summary.csv").write_text((dataset / "waveform.csv").read_text(encoding="utf-8"), encoding="utf-8")
package = Path(os.environ["PARAMOUNT_POWERPOINT_PACKAGE_FOLDER"])
(package / "manifest.json").write_text('{"analysis_id": "power_profile"}', encoding="utf-8")
"""


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


@pytest.fixture
def context_env(tmp_path):
    context = SuiteContext(product_id="PARAMOUNT", product_name="Paramount", firmware="7433660J00",
                           adapter_compatibility=VERIFIED, product_profiles_revision="abc123")
    path = context.save(tmp_path / "suite_context.json")
    return {**os.environ, SUITE_CONTEXT_ENV: str(path)}


@pytest.fixture
def registry():
    return load_registry()


def _collect(tmp_path, registry, env, exit_code=0):
    parent = tmp_path / "collection_parent"
    parent.mkdir(exist_ok=True)
    script = _script(tmp_path, "fake_collector.py", COLLECTOR.format(run_id=RUN_ID_ENV, protocol=PROTOCOL_VERSION_ENV, exit_code=exit_code))
    manager = CollectionProcessManager()
    run_id = manager.launch_test(
        registry=registry, test_key="power_profile", script=script,
        environment={**env, "PARAMOUNT_COLLECTION_PARENT_FOLDER": str(parent)},
        collection_parent=str(parent), bench_profile="Bench A", addresses={"generator": "GEN01"},
    )
    return manager.wait(timeout=60), run_id, parent / "2026-01-01 - Fake Run"


def test_every_paramount_variable_in_code_is_declared():
    out = subprocess.run(["git", "grep", "-h", "-o", "-E", r"PARAMOUNT_[A-Z0-9_]+", "--", "*.py", ":!archive", ":!tests"],
                         cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    used = set(out.split())
    undeclared = sorted(n for n in used if n not in ENV_VARS and not n.startswith(DYNAMIC_PREFIXES) and n not in DYNAMIC_PREFIXES)
    assert not undeclared, f"declare these in suite_common/process_protocol.py: {undeclared}"
    assert all(re.fullmatch(r"PARAMOUNT_[A-Z0-9_]+", name) for name in ENV_VARS)


def test_collection_then_analysis_produce_complete_linked_manifests(tmp_path, registry, context_env):
    result, run_id, run_folder = _collect(tmp_path, registry, context_env)
    assert (result.status, result.exit_code, result.manifest_error) == ("complete", 0, "")
    run_manifest = json.loads((run_folder / RUN_MANIFEST).read_text(encoding="utf-8"))
    assert missing_fields(run_manifest) == []
    assert run_manifest["run_id"] == run_id
    assert (run_folder / "seen_env.txt").read_text(encoding="utf-8") == f"{run_id}|1"
    assert run_manifest["product"] == {"id": "PARAMOUNT", "name": "Paramount"}
    assert run_manifest["firmware"] == "7433660J00"
    assert run_manifest["dut_adapter"] == {"module": "AECommandsUniversal", "compatibility": VERIFIED}
    assert run_manifest["bench"] == {"profile": "Bench A", "instruments": {"generator": "GEN01"}}
    assert run_manifest["plugin"]["id"] == "power_profile" and run_manifest["plugin"]["version"]
    assert run_manifest["suite"]["product_profiles_revision"] == "abc123"
    files = {f["path"]: f["sha256"] for f in run_manifest["outputs"]["files"]}
    assert files["waveform.csv"] == sha256_file(run_folder / "waveform.csv")

    output_parent = tmp_path / "analysis_out"
    output_parent.mkdir()
    package = tmp_path / "presentation" / "analysis_packages" / "power_profile"
    package.mkdir(parents=True)
    analyzer = _script(tmp_path, "fake_analyzer.py", ANALYZER)
    manager = AnalysisProcessManager()
    manager.launch_analysis(
        registry=registry, analysis_key="power_profile", script=analyzer,
        environment={**context_env, "PARAMOUNT_DATASET_A_FOLDER": str(run_folder),
                     "PARAMOUNT_ANALYSIS_OUTPUT_FOLDER": str(output_parent),
                     "PARAMOUNT_POWERPOINT_PACKAGE_FOLDER": str(package)},
        dataset_folders=[str(run_folder), ""], output_parent=str(output_parent), package_folder=package,
    )
    result = manager.wait(timeout=60)
    assert result.status == "complete" and len(result.manifests) == 2
    for folder in (package, output_parent / "Fake Analysis Output"):
        manifest = json.loads((folder / ANALYSIS_MANIFEST).read_text(encoding="utf-8"))
        assert missing_fields(manifest) == [] and manifest["kind"] == "analysis"
        assert manifest["inputs"] == [{
            "folder": str(run_folder), "run_id": run_id,
            "run_manifest_sha256": sha256_file(run_folder / RUN_MANIFEST),
        }]
    assert json.loads((package / "manifest.json").read_text(encoding="utf-8")) == {"analysis_id": "power_profile"}


def test_failed_run_is_recorded_as_failed(tmp_path, registry, context_env):
    result, _run_id, run_folder = _collect(tmp_path, registry, context_env, exit_code=3)
    assert (result.status, result.exit_code) == ("failed", 3)
    assert json.loads((run_folder / RUN_MANIFEST).read_text(encoding="utf-8"))["status"] == "failed"


def test_cancel_and_run_without_output_folder(tmp_path, registry, context_env, monkeypatch):
    monkeypatch.setattr(process_manager, "run_records_dir", lambda: tmp_path / "run_records")
    (tmp_path / "run_records").mkdir()
    script = _script(tmp_path, "sleeper.py", "import time\ntime.sleep(60)\n")
    manager = CollectionProcessManager()
    manager.launch_test(registry=registry, test_key="power_profile", script=script, environment=context_env,
                        collection_parent="", bench_profile="", addresses={})
    with pytest.raises(RuntimeError):
        manager.launch(key="x", script=script, environment=context_env)
    manager.cancel()
    result = manager.wait(timeout=60)
    assert result.status == "cancelled" and not manager.busy()
    record = json.loads(result.manifests[0].read_text(encoding="utf-8"))
    assert result.manifests[0].parent == tmp_path / "run_records"
    assert record["status"] == "cancelled" and record["outputs"] == {"folder": "", "files": []}


def test_adapter_resolution_rules(tmp_path):
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "AECommandsUniversal.py").write_text("", encoding="utf-8")
    product = {"id": "PARAMOUNT", "display_name": "Paramount", "firmware": [
        {"version": "7433660J00", "dut_adapter": "AECommandsUniversal", "compatibility": "verified"},
        {"version": "NEWFW", "dut_adapter": "AECommandsFuture", "compatibility": "verified"},
    ]}
    assert resolve_adapter(product, "7433660j00", adapters)[:2] == ("AECommandsUniversal", VERIFIED)
    assert resolve_adapter(product, "OTHER", adapters)[:2] == ("AECommandsUniversal", UNVERIFIED)
    assert resolve_adapter(product, "NEWFW", adapters)[:2] == ("AECommandsFuture", UNAVAILABLE)


def test_shipped_product_profiles_are_v2_and_resolve(tmp_path):
    sys.path.insert(0, str(REPO_ROOT))
    import Transparency_Test_Suite_Launcher as launcher

    catalog = launcher.load_product_profiles()
    assert catalog["schema_version"] == 2
    paramount = launcher.product_by_id(catalog, "PARAMOUNT")
    context = launcher.build_suite_context(paramount, "7433660J00")
    assert (context.dut_adapter, context.adapter_compatibility) == ("AECommandsUniversal", VERIFIED)
    assert context.product_profiles_revision


def test_suite_context_current_falls_back_to_environment():
    context = SuiteContext.current({"PARAMOUNT_PRODUCT_ID": "APEX", "PARAMOUNT_FIRMWARE_VERSION": "1.0"})
    assert (context.product_id, context.firmware, context.dut_adapter) == ("APEX", "1.0", "AECommandsUniversal")
    assert SuiteContext.current({}).product_id == "UNSPECIFIED"
