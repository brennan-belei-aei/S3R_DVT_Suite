"""Settings live in config/ (committed) and runtime/ (per-PC); legacy files migrate without data loss."""
from __future__ import annotations

import subprocess

from suite_common import config
from tests.conftest import REPO_ROOT

LEGACY_SAMPLES = {
    "Bench_Profiles.json": '{"active_profile": "Bench A"}',
    "Unified_Collection_GUI_Settings.json": '{"selected_tests": ["psync"]}',
    "Arc Management/Arc_Management_Collection_Settings.json": '{"samples": 3}',
    "RF Ramping/RF_Ramp_Collection_Settings.json": '{"power": "50, 1000"}',
}


def _seed(root, files):
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _target(root, legacy_relative):
    name = next(n for n, olds in config.LEGACY_LOCATIONS.items() if legacy_relative in olds)
    return config.runtime_dir(root) / name


def test_migration_moves_content_and_keeps_originals(tmp_path):
    _seed(tmp_path, LEGACY_SAMPLES)
    moved = config.migrate_legacy_settings(repo_root=tmp_path)
    assert len(moved) == len(LEGACY_SAMPLES)
    for relative, text in LEGACY_SAMPLES.items():
        assert _target(tmp_path, relative).read_text(encoding="utf-8") == text
        assert not (tmp_path / relative).exists()
        kept = config.runtime_dir(tmp_path) / config.MIGRATED_DIRNAME / relative
        assert kept.read_text(encoding="utf-8") == text


def test_migration_is_idempotent_and_never_overwrites(tmp_path):
    _seed(tmp_path, LEGACY_SAMPLES)
    config.migrate_legacy_settings(repo_root=tmp_path)
    assert config.migrate_legacy_settings(repo_root=tmp_path) == []
    # A legacy file recreated later (e.g. by old code) must not clobber newer runtime settings.
    _seed(tmp_path, {"Bench_Profiles.json": '{"stale": true}'})
    assert config.migrate_legacy_settings(repo_root=tmp_path) == []
    assert _target(tmp_path, "Bench_Profiles.json").read_text(encoding="utf-8") == LEGACY_SAMPLES["Bench_Profiles.json"]


def test_runtime_file_adopts_legacy_on_first_access(tmp_path):
    _seed(tmp_path, {"Frequency Tuning/RF_Tuning_Analysis_Settings.json": '{"x": 1}'})
    path = config.runtime_file("plugin_settings/rf_tuning.analysis.json", repo_root=tmp_path)
    assert path.read_text(encoding="utf-8") == '{"x": 1}'
    new = config.runtime_file("plugin_settings/brand_new.collection.json", repo_root=tmp_path)
    assert new.parent.is_dir() and not new.exists()


def test_no_tracked_json_at_repo_root():
    tracked = subprocess.run(
        ["git", "ls-files", "--", "*.json"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(p for p in tracked if "/" not in p) == ["package-lock.json", "package.json"]
