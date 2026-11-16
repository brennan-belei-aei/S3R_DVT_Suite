"""Analysis Process Manager: runs one analysis script and records its Analysis Package provenance."""
from __future__ import annotations

from pathlib import Path

from suite_apps.process_manager import ChildProcessManager, plugin_summary
from suite_common.manifests import ANALYSIS_MANIFEST
from suite_sdk.registry import PluginRegistry


class AnalysisProcessManager(ChildProcessManager):
    kind = "analysis"
    manifest_name = ANALYSIS_MANIFEST

    def launch_analysis(
        self,
        *,
        registry: PluginRegistry,
        analysis_key: str,
        script: Path,
        environment: dict[str, str],
        dataset_folders: list[str],
        output_parent: str = "",
        package_folder: Path | None = None,
        new_console: bool = False,
    ) -> str:
        """analysis_manifest.json goes into the presentation package folder (when exporting) and into
        every folder the script creates under the output folder or a dataset folder. Dataset folders
        are recorded as inputs (by their run_manifest.json when they are Run Packages)."""
        datasets = [Path(folder) for folder in dataset_folders if str(folder).strip()]
        watch = ([Path(output_parent)] if str(output_parent).strip() else []) + datasets
        return self.launch(
            key=analysis_key,
            script=script,
            environment=environment,
            new_console=new_console,
            watch_folders=list(dict.fromkeys(watch)),
            fixed_outputs=[package_folder] if package_folder else [],
            inputs=datasets,
            plugin=plugin_summary(registry, "analysis", analysis_key),
        )