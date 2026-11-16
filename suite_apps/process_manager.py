"""Launch, monitor, cancel, and package one child process at a time.

The GUIs decide *what* to run and what to show; this module owns the subprocess and the
provenance manifest written when it ends. Manifests are written on a worker thread so a
large data folder never freezes the GUI; poll() returns None until that is finished too.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from suite_common.config import run_records_dir
from suite_common.manifests import STATUSES, build_manifest, describe_input, now, write_manifest
from suite_common.paths import child_creation_flags
from suite_common.process_protocol import RUN_ID_ENV, protocol_environment
from suite_sdk.context import SuiteContext
from suite_sdk.registry import PluginRegistry, PluginTest


@dataclass
class ChildResult:
    run_id: str
    key: str
    exit_code: int | None
    status: str
    manifests: list[Path] = field(default_factory=list)
    manifest_error: str = ""


@dataclass
class _ActiveRun:
    run_id: str
    key: str
    script: Path
    environment: dict[str, str]
    process: subprocess.Popen
    started: str
    watch: dict[Path, set[str]]
    fixed_outputs: list[Path]
    inputs: list[Path]
    plugin: dict | None
    bench: dict | None
    cancelled: bool = False
    finalizer: threading.Thread | None = None
    result: ChildResult | None = None


def plugin_summary(registry: PluginRegistry, kind: str, key: str) -> dict | None:
    """Plugin identity for a manifest, or None for a script that is not a registered plugin test."""
    try:
        test: PluginTest = registry.test(kind, key)
    except KeyError:
        return None
    plugin = registry.plugin(kind, test.plugin_id)
    return {"id": plugin.id, "version": plugin.version, "kind": kind, "test_key": test.key, "test_name": test.name}


def _subfolders(folder: Path) -> set[str]:
    try:
        return {p.name for p in folder.iterdir() if p.is_dir()}
    except OSError:
        return set()


class ChildProcessManager:
    """One child at a time. Subclasses set `kind` and `manifest_name`."""

    kind = "run"
    manifest_name = "run_manifest.json"

    def __init__(self) -> None:
        self._active: _ActiveRun | None = None

    def busy(self) -> bool:
        """True from launch until poll() has returned the result (child running or manifests being written)."""
        return self._active is not None

    @property
    def pid(self) -> int | None:
        return self._active.process.pid if self._active else None

    def launch(
        self,
        *,
        key: str,
        script: Path,
        environment: dict[str, str],
        new_console: bool = False,
        watch_folders: list[Path] | tuple[Path, ...] = (),
        fixed_outputs: list[Path] | tuple[Path, ...] = (),
        inputs: list[Path] | tuple[Path, ...] = (),
        plugin: dict | None = None,
        bench: dict | None = None,
    ) -> str:
        """Start `script`. Folders created directly under any watch folder during the run become
        its packages; `fixed_outputs` always get a manifest. Returns the run id."""
        if self.busy():
            raise RuntimeError("A child process is already running.")
        script = Path(script).resolve()
        run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        environment = protocol_environment(environment)
        environment[RUN_ID_ENV] = run_id
        watch = {Path(f): _subfolders(Path(f)) for f in watch_folders if str(f).strip() and Path(f).is_dir()}
        process = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(script.parent),
            env=environment,
            creationflags=child_creation_flags(new_console),
        )
        self._active = _ActiveRun(
            run_id=run_id, key=key, script=script, environment=environment, process=process, started=now(),
            watch=watch, fixed_outputs=[Path(p) for p in fixed_outputs], inputs=[Path(p) for p in inputs],
            plugin=plugin, bench=bench,
        )
        return run_id

    def cancel(self) -> None:
        """Terminate the running child; its manifest records status "cancelled"."""
        run = self._active
        if run is not None and run.process.poll() is None:
            run.cancelled = True
            run.process.terminate()

    def poll(self) -> ChildResult | None:
        """None while the child (or its manifest writing) is still running; then the result, once."""
        run = self._active
        if run is None:
            return None
        if run.result is None:
            exit_code = run.process.poll()
            if exit_code is None:
                return None
            if run.finalizer is None:
                run.finalizer = threading.Thread(target=self._finalize, args=(run, exit_code), daemon=True)
                run.finalizer.start()
            if run.finalizer.is_alive():
                return None
        self._active = None
        return run.result

    def wait(self, timeout: float | None = None) -> ChildResult:
        """Blocking variant of poll() (tests, scripts)."""
        run = self._active
        if run is None:
            raise RuntimeError("No child process was launched.")
        run.process.wait(timeout=timeout)
        while True:
            result = self.poll()
            if result is not None:
                return result
            if run.finalizer is not None:
                run.finalizer.join(timeout)

    def _finalize(self, run: _ActiveRun, exit_code: int) -> None:
        status = "cancelled" if run.cancelled else ("complete" if exit_code == 0 else "failed")
        assert status in STATUSES
        result = ChildResult(run.run_id, run.key, exit_code, status)
        try:
            context = SuiteContext.current(run.environment).to_dict()
            finished = now()
            outputs = list(run.fixed_outputs)
            for folder, before in run.watch.items():
                outputs += [folder / name for name in sorted(_subfolders(folder) - before)]
            inputs = [describe_input(path) for path in run.inputs]
            common = dict(
                kind=self.kind, run_id=run.run_id, status=status, exit_code=exit_code, started=run.started,
                finished=finished, environment=run.environment, context=context, script=run.script,
                plugin=run.plugin, bench=run.bench, inputs=inputs,
            )
            for folder in dict.fromkeys(outputs):
                result.manifests.append(write_manifest(folder, self.manifest_name, build_manifest(output_folder=folder, **common)))
            if not result.manifests:
                record = build_manifest(output_folder=None, **common)
                name = f"{run.run_id}_{run.key}.{self.manifest_name}"
                result.manifests.append(write_manifest(run_records_dir(), name, record))
        except Exception:
            result.manifest_error = traceback.format_exc()
        run.result = result

    def describe_input(self, path: Path) -> dict:
        from suite_common.manifests import describe_input
        return describe_input(path)
