"""Collection Process Manager: runs one collection script and turns its output into a Run Package."""
from __future__ import annotations

from pathlib import Path

from suite_apps.process_manager import ChildProcessManager, plugin_summary
from suite_common.manifests import RUN_MANIFEST
from suite_sdk.registry import PluginRegistry


class CollectionProcessManager(ChildProcessManager):
    kind = "run"
    manifest_name = RUN_MANIFEST

    def launch_test(
        self,
        *,
        registry: PluginRegistry,
        test_key: str,
        script: Path,
        environment: dict[str, str],
        collection_parent: str,
        bench_profile: str,
        addresses: dict[str, str],
        new_console: bool = False,
    ) -> str:
        """Each folder the script creates under the collection parent folder becomes a Run Package
        with run_manifest.json (bench profile, instrument addresses, settings, file hashes)."""
        return self.launch(
            key=test_key,
            script=script,
            environment=environment,
            new_console=new_console,
            watch_folders=[Path(collection_parent)] if collection_parent else [],
            plugin=plugin_summary(registry, "collection", test_key),
            bench={"profile": bench_profile, "instruments": dict(addresses)},
        )