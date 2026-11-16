"""Where suite configuration lives.

    config/            committed, read-only defaults (product profiles, *.example.json templates)
    runtime/           per-PC data, gitignored (bench profiles, GUI settings, per-plugin settings)

Every accessor returns a path inside runtime/ and, the first time it is used, adopts a
settings file from its pre-refactor location (repo root or beside a test script). The
original is never deleted: it moves to runtime/migrated_legacy/<old relative path>.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from suite_common import REPO_ROOT

CONFIG_DIR = REPO_ROOT / "config"
TEMPLATES_DIR = CONFIG_DIR / "templates"
RUNTIME_DIRNAME = "runtime"
MIGRATED_DIRNAME = "migrated_legacy"

BENCH_PROFILES = "Bench_Profiles.json"
COLLECTION_GUI_SETTINGS = "collection_gui_settings.json"
ANALYSIS_GUI_SETTINGS = "analysis_gui_settings.json"
LAUNCHER_SETTINGS = "launcher_settings.json"

# runtime-relative name -> repo-relative locations used before the refactor
LEGACY_LOCATIONS: dict[str, tuple[str, ...]] = {
    BENCH_PROFILES: ("Bench_Profiles.json",),
    COLLECTION_GUI_SETTINGS: ("Unified_Collection_GUI_Settings.json",),
    ANALYSIS_GUI_SETTINGS: ("Unified_Analysis_GUI_Settings.json",),
    LAUNCHER_SETTINGS: ("AE_Transparency_Testing_Suite_Settings.json",),
    "plugin_settings/arc_management.collection.json": ("Arc Management/Arc_Management_Collection_Settings.json",),
    "plugin_settings/arc_management.analysis.json": ("Arc Management/Arc_Management_Analysis_Settings.json",),
    "plugin_settings/rf_tuning.collection.json": ("Frequency Tuning/RF_Tuning_Collection_Settings.json",),
    "plugin_settings/rf_tuning.analysis.json": ("Frequency Tuning/RF_Tuning_Analysis_Settings.json",),
    "plugin_settings/rf_ramping.collection.json": ("RF Ramping/RF_Ramp_Collection_Settings.json",),
}


def product_profiles_path() -> Path:
    return CONFIG_DIR / "AE_Product_Profiles.json"


def runtime_dir(repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / RUNTIME_DIRNAME


def runtime_file(name: str, repo_root: Path = REPO_ROOT) -> Path:
    """Path of a runtime file, adopting its legacy copy on first use. Parent folders are created."""
    path = runtime_dir(repo_root) / name
    _adopt_legacy(name, path, repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def bench_profiles_path() -> Path:
    return runtime_file(BENCH_PROFILES)


def collection_gui_settings_path() -> Path:
    return runtime_file(COLLECTION_GUI_SETTINGS)


def analysis_gui_settings_path() -> Path:
    return runtime_file(ANALYSIS_GUI_SETTINGS)


def launcher_settings_path() -> Path:
    return runtime_file(LAUNCHER_SETTINGS)


def plugin_settings_path(plugin_id: str, kind: str) -> Path:
    """Per-plugin GUI settings, e.g. plugin_settings_path("rf_tuning", "collection")."""
    return runtime_file(f"plugin_settings/{plugin_id}.{kind}.json")


def display_path(path: Path) -> str:
    """Repo-relative text for status messages."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def migrate_legacy_settings(repo_root: Path = REPO_ROOT) -> list[tuple[Path, Path]]:
    """Adopt every known legacy settings file into runtime/. Safe to call repeatedly."""
    moved = []
    for name in LEGACY_LOCATIONS:
        target = runtime_dir(repo_root) / name
        source = _adopt_legacy(name, target, repo_root)
        if source is not None:
            moved.append((source, target))
    return moved


def _adopt_legacy(name: str, target: Path, repo_root: Path) -> Path | None:
    if target.exists():
        return None
    for relative in LEGACY_LOCATIONS.get(name, ()):
        legacy = repo_root / relative
        if legacy.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, target)
            kept = runtime_dir(repo_root) / MIGRATED_DIRNAME / relative
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(kept))
            print(f"Moved settings {relative} -> {RUNTIME_DIRNAME}/{name} (original kept in {RUNTIME_DIRNAME}/{MIGRATED_DIRNAME}/)")
            return legacy
    return None
