"""Provenance manifests for Run Packages (collection) and Analysis Packages (analysis).

The process managers in suite_apps/ write these around every child run, so test scripts do
not need to know about them:

    <collection run folder>/run_manifest.json
    <analysis output folder or analysis_packages/<key>>/analysis_manifest.json

They sit beside, never replace, files the scripts write themselves (e.g. the presentation
package manifest.json). A run with no detectable output folder is recorded in
runtime/run_records/.
"""
from __future__ import annotations

import functools
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from suite_common import REPO_ROOT
from suite_common.process_protocol import PROTOCOL_VERSION, paramount_settings

SCHEMA_VERSION = 1
RUN_MANIFEST = "run_manifest.json"
ANALYSIS_MANIFEST = "analysis_manifest.json"
MANIFEST_NAMES = (RUN_MANIFEST, ANALYSIS_MANIFEST)
STATUSES = ("complete", "failed", "cancelled")

REQUIRED_FIELDS = (
    "schema_version", "kind", "run_id", "status", "exit_code", "started", "finished",
    "product", "firmware", "dut_adapter", "bench", "effective_settings", "suite",
    "plugin", "script", "inputs", "outputs",
)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def short_revision(path: Path) -> str:
    """First 12 hex digits of a file's SHA-256, or "" when the file does not exist."""
    return sha256_file(path)[:12] if Path(path).is_file() else ""


def display(path: Path) -> str:
    path = Path(path)
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def file_hashes(folder: Path) -> list[dict]:
    """Every file under folder (manifests excluded) with size and SHA-256, sorted by path."""
    folder = Path(folder)
    entries = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.name not in MANIFEST_NAMES):
        entries.append({
            "path": path.relative_to(folder).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return entries


def describe_input(folder: Path) -> dict:
    """An analysis input folder. A collection Run Package is referenced by its run manifest
    (whose hash covers every data file); any other folder has its files hashed."""
    folder = Path(folder)
    entry = {"folder": str(folder)}
    manifest = folder / RUN_MANIFEST
    if manifest.is_file():
        entry["run_manifest_sha256"] = sha256_file(manifest)
        try:
            entry["run_id"] = json.loads(manifest.read_text(encoding="utf-8")).get("run_id", "")
        except ValueError:
            entry["run_id"] = ""
    elif folder.is_dir():
        entry["files"] = file_hashes(folder)
    else:
        entry["missing"] = True
    return entry


@functools.lru_cache(maxsize=1)
def suite_version() -> dict:
    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else ""
    try:
        commit = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.SubprocessError):
        commit, dirty = "", False
    return {"commit": commit or "unknown", "dirty": dirty, "protocol_version": PROTOCOL_VERSION}


def build_manifest(
    *,
    kind: str,
    run_id: str,
    status: str,
    exit_code: int | None,
    started: str,
    finished: str,
    environment: dict[str, str],
    context: dict,
    script: Path,
    plugin: dict | None,
    bench: dict | None,
    inputs: list[dict],
    output_folder: Path | None,
) -> dict:
    assert kind in ("run", "analysis") and status in STATUSES
    script = Path(script)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "run_id": run_id,
        "status": status,
        "exit_code": exit_code,
        "started": started,
        "finished": finished,
        "product": {"id": context.get("product_id", ""), "name": context.get("product_name", "")},
        "firmware": context.get("firmware", ""),
        "dut_adapter": {
            "module": context.get("dut_adapter", ""),
            "compatibility": context.get("adapter_compatibility", ""),
        },
        "bench": bench,
        "effective_settings": paramount_settings(environment),
        "suite": {
            **suite_version(),
            "context_file": environment.get("PARAMOUNT_SUITE_CONTEXT", ""),
            "product_profiles_revision": context.get("product_profiles_revision", ""),
            "bench_profiles_revision": context.get("bench_profiles_revision", ""),
        },
        "plugin": plugin,
        "script": {"path": display(script), "sha256": sha256_file(script) if script.is_file() else ""},
        "inputs": inputs,
        "outputs": {"folder": str(output_folder) if output_folder else "", "files": file_hashes(output_folder) if output_folder else []},
    }


def write_manifest(folder: Path, name: str, manifest: dict) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def missing_fields(manifest: dict) -> list[str]:
    return [name for name in REQUIRED_FIELDS if name not in manifest]
