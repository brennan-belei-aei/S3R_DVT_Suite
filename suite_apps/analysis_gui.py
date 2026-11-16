"""Unified sequential launcher for Paramount data-analysis GUIs.

The launcher never imports or modifies an analysis script. Each selected analyzer
runs in its own Python process, using its own GUI, folder selection, progress
window, and outputs. Built-in analyses come from the plugin registry (plugin.json
files); users can also add their own scripts through the GUI.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.paths import (  # noqa: E402
    child_creation_flags,
    discover_script,
    load_json_settings,
    resolve_portable_path,
    resolve_saved_script,
    save_json_settings,
    to_portable_path,
)
from suite_common.config import analysis_gui_settings_path, display_path, migrate_legacy_settings
from suite_sdk.registry import describe_unavailable, load_registry
from suite_sdk.context import SuiteContext  # noqa: E402
from suite_apps.analysis_process_manager import AnalysisProcessManager  # noqa: E402

APP_TITLE = "Paramount Unified Data Analysis"
APP_DIR = Path(__file__).resolve().parent
POLL_INTERVAL_MS = 400
PRESENTATION_SCHEMA_VERSION = 1
PRESENTATION_MANIFEST_NAME = "presentation_manifest.json"
ANALYSIS_PACKAGES_FOLDER = "analysis_packages"
POWERPOINT_BUILDER_FILENAME = "Paramount_PowerPoint_Builder.py"
ANALYSIS_MODE_INDIVIDUAL = "INDIVIDUAL"
ANALYSIS_MODE_SINGLE = "SINGLE"
ANALYSIS_MODE_COMPARE = "COMPARE"
ANALYSIS_MODE_VALUES = (ANALYSIS_MODE_INDIVIDUAL, ANALYSIS_MODE_SINGLE, ANALYSIS_MODE_COMPARE)


@dataclass
class AnalysisDefinition:
    key: str
    name: str
    default_filenames: list[str]
    description: str = ""
    custom: bool = False
    default_script: str = ""


def default_script(item: AnalysisDefinition) -> str:
    """Registry path for built-ins; custom scripts are found again by filename."""
    return item.default_script or discover_script(item.default_filenames)


# Built-in analyses come from the plugin registry (plugin.json files); add one there, not here.
REGISTRY = load_registry()
BUILTIN_ANALYSES = [
    AnalysisDefinition(t.key, t.name, [t.script.name], t.description, False, str(t.script))
    for t in REGISTRY.tests("analysis")
]


def migrate_analysis_entries(values: list[str]) -> list[str]:
    """Map legacy keys in saved settings (plugin.json "aliases") to current analyses, keeping order."""
    return REGISTRY.resolve_aliases("analysis", values)


def safe_key(name: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "analysis"
    key, number = base, 2
    while key in existing:
        key, number = f"{base}_{number}", number + 1
    return key


def default_config() -> dict:
    return {
        "scripts": {item.key: default_script(item) for item in BUILTIN_ANALYSES},
        "selected": [item.key for item in BUILTIN_ANALYSES],
        "order": [item.key for item in BUILTIN_ANALYSES],
        "custom_analyses": [],
        "open_child_console": True,
        "show_launch_prompt": True,
        "dataset_defaults": {
            "preferred_mode": ANALYSIS_MODE_INDIVIDUAL,
            "dataset_a_folder": "",
            "dataset_b_folder": "",
            "dataset_a_label": "Dataset A",
            "dataset_b_label": "Dataset B",
        },
        "presentation": {
            "enabled": False,
            "title": "Paramount Analysis Results",
            "subtitle": "",
            "dut_name": "",
            "project_name": "",
            "operator": "",
            "output_parent": os.environ.get("PARAMOUNT_COLLECTION_PARENT_FOLDER", "").strip() or str(Path.home()),
            "filename": "Paramount_Analysis_Results.pptx",
            "include_title_slide": True,
            "include_executive_summary": True,
            "include_failure_tables": True,
            "include_appendix": True,
            "open_after_generation": True,
            "builder_script": str(APP_DIR / POWERPOINT_BUILDER_FILENAME),
        },
    }


def load_config() -> dict:
    config = default_config()
    saved = load_json_settings(analysis_gui_settings_path())
    builtin_by_key = {item.key: item for item in BUILTIN_ANALYSES}
    for key, value in (saved.get("scripts") or {}).items():
        key = str(key)
        if key in builtin_by_key:
            config["scripts"][key] = resolve_saved_script(str(value), default_script(builtin_by_key[key]))
        else:
            config["scripts"][key] = resolve_portable_path(str(value))
    if isinstance(saved.get("custom_analyses"), list):
        config["custom_analyses"] = saved["custom_analyses"]
    for key in ("selected", "order", "open_child_console", "show_launch_prompt"):
        if key in saved:
            config[key] = saved[key]
    for key in ("selected", "order"):
        config[key] = migrate_analysis_entries([str(item) for item in config.get(key, [])])
    if isinstance(saved.get("dataset_defaults"), dict):
        config["dataset_defaults"].update(saved["dataset_defaults"])
    if isinstance(saved.get("presentation"), dict):
        saved_presentation = dict(saved["presentation"])
        # Builder path always comes from code so stale absolute paths can't break it.
        saved_presentation.pop("builder_script", None)
        config["presentation"].update(saved_presentation)
    for section, key in (("dataset_defaults", "dataset_a_folder"), ("dataset_defaults", "dataset_b_folder"), ("presentation", "output_parent")):
        config[section][key] = resolve_portable_path(config[section][key])
    return config


def save_config(config: dict) -> None:
    portable = json.loads(json.dumps(config))
    portable["scripts"] = {key: to_portable_path(value) for key, value in config["scripts"].items()}
    portable["presentation"].pop("builder_script", None)
    for section, key in (("dataset_defaults", "dataset_a_folder"), ("dataset_defaults", "dataset_b_folder"), ("presentation", "output_parent")):
        portable[section][key] = to_portable_path(portable[section][key])
    save_json_settings(analysis_gui_settings_path(), portable)


class UnifiedAnalysisApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()
        self.definitions = list(BUILTIN_ANALYSES)
        existing = {item.key for item in self.definitions}
        for raw in self.config.get("custom_analyses", []):
            try:
                key = str(raw["key"])
                if key in existing:
                    continue
                item = AnalysisDefinition(key, str(raw["name"]), [str(raw.get("filename", ""))], str(raw.get("description", "")), True)
                self.definitions.append(item)
                existing.add(key)
            except Exception:
                continue
        self.by_key = {item.key: item for item in self.definitions}
        self.runner = AnalysisProcessManager()
        self.sequence: list[str] = []
        self.sequence_index = -1
        self.active_key: str | None = None
        self.stopping = False
        self.status_by_key = {item.key: "Not selected" for item in self.definitions}
        self.script_vars = {item.key: tk.StringVar(value=self.config.get("scripts", {}).get(item.key, default_script(item))) for item in self.definitions}
        selected = set(self.config.get("selected", []))
        self.selected_vars = {item.key: tk.BooleanVar(value=item.key in selected) for item in self.definitions}
        self.console_var = tk.BooleanVar(value=bool(self.config.get("open_child_console", True)))
        self.prompt_var = tk.BooleanVar(value=bool(self.config.get("show_launch_prompt", True)))
        self.progress_var = tk.StringVar(value="No sequence is running.")
        self.status_var = tk.StringVar(value="Ready")
        self.presentation_run_folder: Path | None = None
        self.presentation_manifest_path: Path | None = None
        self.package_status_by_key: dict[str, str] = {}
        dataset_defaults = self.config.get("dataset_defaults", {})
        saved_mode = str(dataset_defaults.get("preferred_mode", ANALYSIS_MODE_INDIVIDUAL)).upper()
        if saved_mode not in ANALYSIS_MODE_VALUES:
            saved_mode = ANALYSIS_MODE_INDIVIDUAL
        self.analysis_mode_var = tk.StringVar(value=saved_mode)
        self.dataset_default_vars = {
            key: tk.StringVar(value=str(dataset_defaults.get(key, default)))
            for key, default in {
                "dataset_a_folder": "", "dataset_b_folder": "",
                "dataset_a_label": "Dataset A", "dataset_b_label": "Dataset B",
            }.items()
        }
        presentation = self.config.get("presentation", {})
        self.presentation_enabled_var = tk.BooleanVar(value=bool(presentation.get("enabled", False)))
        self.presentation_text_vars = {
            key: tk.StringVar(value=str(presentation.get(key, default)))
            for key, default in {
                "title": "Paramount Analysis Results", "subtitle": "", "dut_name": "",
                "project_name": "", "operator": "", "output_parent": str(APP_DIR),
                "filename": "Paramount_Analysis_Results.pptx",
                "builder_script": str(APP_DIR / POWERPOINT_BUILDER_FILENAME),
            }.items()
        }
        self.presentation_bool_vars = {
            key: tk.BooleanVar(value=bool(presentation.get(key, default)))
            for key, default in {
                "include_title_slide": True, "include_executive_summary": True,
                "include_failure_tables": True, "include_appendix": True,
                "open_after_generation": True,
            }.items()
        }
        self._build_gui()
        self._refresh_path_statuses()

    def _build_gui(self):
        self.root.title(APP_TITLE)
        self.root.geometry("1120x820")
        self.root.minsize(950, 680)
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=APP_TITLE, style="Header.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Select and order analysis tools, then launch their existing GUIs sequentially. Each analyzer remains an independent process and retains its own settings, input selection, progress, and outputs.", wraplength=1040).pack(anchor="w", pady=(2, 10))
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        self.sequence_tab = ttk.Frame(self.notebook, padding=12)
        self.paths_tab = ttk.Frame(self.notebook, padding=12)
        self.dataset_defaults_tab = ttk.Frame(self.notebook, padding=12)
        self.presentation_tab = ttk.Frame(self.notebook, padding=12)
        self.progress_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.sequence_tab, text="Analysis Sequence")
        self.notebook.add(self.paths_tab, text="Script Paths")
        self.notebook.add(self.dataset_defaults_tab, text="Dataset Defaults")
        self.notebook.add(self.presentation_tab, text="Presentation")
        self.notebook.add(self.progress_tab, text="Progress")
        self._build_sequence_tab()
        self._build_paths_tab()
        self._build_dataset_defaults_tab()
        self._build_presentation_tab()
        self._build_progress_tab()
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Button(footer, text="Save Settings", command=self.save_current_config).pack(side="left")
        self.start_button = ttk.Button(footer, text="Start Selected Sequence", command=self.start_sequence)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(footer, text="Stop After Current Analysis", command=self.stop_after_current, state="disabled")
        self.stop_button.pack(side="right", padx=8)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_sequence_tab(self):
        left = ttk.LabelFrame(self.sequence_tab, text="Select Analyses", style="Section.TLabelframe", padding=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.selection_holder = ttk.Frame(left)
        self.selection_holder.pack(fill="both", expand=True)
        self._render_selection_controls()
        controls = ttk.Frame(left)
        controls.pack(fill="x", pady=(12, 0))
        ttk.Button(controls, text="Select All", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(controls, text="Clear All", command=lambda: self._set_all(False)).pack(side="left", padx=8)
        ttk.Button(controls, text="Add Analysis Script...", command=self.add_custom_analysis).pack(side="right")
        right = ttk.LabelFrame(self.sequence_tab, text="Execution Order", style="Section.TLabelframe", padding=10)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.order_list = tk.Listbox(right, height=16, exportselection=False)
        self.order_list.pack(fill="both", expand=True)
        order_controls = ttk.Frame(right)
        order_controls.pack(fill="x", pady=(8, 0))
        ttk.Button(order_controls, text="Move Up", command=lambda: self._move_order(-1)).pack(side="left")
        ttk.Button(order_controls, text="Move Down", command=lambda: self._move_order(1)).pack(side="left", padx=8)
        ttk.Button(order_controls, text="Remove Custom", command=self.remove_selected_custom).pack(side="right")
        saved_order = [key for key in self.config.get("order", []) if key in self.by_key]
        saved_order.extend(item.key for item in self.definitions if item.key not in saved_order)
        self.order_keys = saved_order
        self._sync_order()
        ttk.Checkbutton(self.sequence_tab, text="Show a Continue / Skip prompt before each analysis", variable=self.prompt_var).pack(anchor="w", side="bottom", pady=(12, 0))
        ttk.Checkbutton(self.sequence_tab, text="Open each analysis script in a separate console", variable=self.console_var).pack(anchor="w", side="bottom")

    def _build_paths_tab(self):
        self.paths_tab.columnconfigure(1, weight=1)
        self.path_rows = ttk.Frame(self.paths_tab)
        self.path_rows.grid(row=0, column=0, sticky="nsew")
        self.paths_tab.rowconfigure(0, weight=1)
        self.paths_tab.columnconfigure(0, weight=1)
        self.path_status_labels = {}
        self._render_path_controls()

    def _build_dataset_defaults_tab(self):
        self.dataset_defaults_tab.columnconfigure(1, weight=1)
        mode_frame = ttk.LabelFrame(
            self.dataset_defaults_tab, text="Analysis mode preference",
            style="Section.TLabelframe", padding=10,
        )
        mode_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        modes = [
            ("Configure separately in each analysis GUI", ANALYSIS_MODE_INDIVIDUAL),
            ("Prefer single-dataset analysis", ANALYSIS_MODE_SINGLE),
            ("Prefer Dataset A versus Dataset B comparison", ANALYSIS_MODE_COMPARE),
        ]
        for row, (label, value) in enumerate(modes):
            ttk.Radiobutton(
                mode_frame, text=label, variable=self.analysis_mode_var, value=value,
                command=self._update_dataset_default_controls,
            ).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Label(
            self.dataset_defaults_tab,
            text=("These are starting values only. Each individual analysis GUI remains authoritative "
                  "and may change the mode, folders, or labels before analysis begins."),
            foreground="#555555", wraplength=940,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 12))
        fields = [
            ("dataset_a_folder", "Default Dataset A folder"),
            ("dataset_b_folder", "Default Dataset B folder"),
            ("dataset_a_label", "Dataset A label"),
            ("dataset_b_label", "Dataset B label"),
        ]
        self.dataset_default_entries = {}
        self.dataset_default_browse_buttons = {}
        for row, (key, label) in enumerate(fields, start=2):
            ttk.Label(self.dataset_defaults_tab, text=label).grid(row=row, column=0, sticky="w", pady=6)
            entry = ttk.Entry(self.dataset_defaults_tab, textvariable=self.dataset_default_vars[key])
            entry.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
            self.dataset_default_entries[key] = entry
            if key.endswith("_folder"):
                button = ttk.Button(
                    self.dataset_defaults_tab, text="Browse...",
                    command=lambda target=key: self._browse_dataset_default(target),
                )
                button.grid(row=row, column=2, pady=6)
                self.dataset_default_browse_buttons[key] = button
        ttk.Label(
            self.dataset_defaults_tab,
            text=("Folder validation here checks only that a supplied location exists. Each child GUI "
                  "continues to validate its analysis-specific files and lets the user browse elsewhere."),
            foreground="#555555", wraplength=940,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self._update_dataset_default_controls()

    def _browse_dataset_default(self, key: str):
        current = self.dataset_default_vars[key].get().strip()
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Select default Dataset A folder" if key == "dataset_a_folder" else "Select default Dataset B folder",
            initialdir=current if current and Path(current).is_dir() else str(APP_DIR),
            mustexist=True,
        )
        if selected:
            self.dataset_default_vars[key].set(selected)

    def _update_dataset_default_controls(self):
        mode = self.analysis_mode_var.get().upper()
        b_state = "normal" if mode != ANALYSIS_MODE_SINGLE else "disabled"
        if hasattr(self, "dataset_default_entries"):
            self.dataset_default_entries["dataset_b_folder"].configure(state=b_state)
            self.dataset_default_entries["dataset_b_label"].configure(state=b_state)
            self.dataset_default_browse_buttons["dataset_b_folder"].configure(state=b_state)

    def _dataset_defaults(self) -> dict:
        mode = self.analysis_mode_var.get().strip().upper()
        if mode not in ANALYSIS_MODE_VALUES:
            mode = ANALYSIS_MODE_INDIVIDUAL
        return {
            "preferred_mode": mode,
            **{key: variable.get().strip() for key, variable in self.dataset_default_vars.items()},
        }

    def _validate_dataset_defaults(self) -> bool:
        settings = self._dataset_defaults()
        mode = settings["preferred_mode"]
        folder_a_text = settings["dataset_a_folder"]
        folder_b_text = settings["dataset_b_folder"]
        label_a = settings["dataset_a_label"]
        label_b = settings["dataset_b_label"]
        if not label_a:
            messagebox.showerror("Invalid Dataset A label", "Enter a Dataset A label.", parent=self.root)
            self.notebook.select(self.dataset_defaults_tab)
            return False
        if mode != ANALYSIS_MODE_SINGLE and not label_b:
            messagebox.showerror("Invalid Dataset B label", "Enter a Dataset B label.", parent=self.root)
            self.notebook.select(self.dataset_defaults_tab)
            return False
        for label, value in (("Dataset A", folder_a_text), ("Dataset B", folder_b_text)):
            if value and not Path(value).expanduser().is_dir():
                messagebox.showerror(
                    f"Invalid {label} folder",
                    f"The default {label} folder does not exist or is not a directory:\n\n{value}",
                    parent=self.root,
                )
                self.notebook.select(self.dataset_defaults_tab)
                return False
        if mode == ANALYSIS_MODE_SINGLE and not folder_a_text:
            messagebox.showerror(
                "Dataset A folder required",
                "Select a default Dataset A folder for preferred single-dataset analysis.",
                parent=self.root,
            )
            self.notebook.select(self.dataset_defaults_tab)
            return False
        if mode == ANALYSIS_MODE_COMPARE:
            if not folder_a_text or not folder_b_text:
                messagebox.showerror(
                    "Dataset folders required",
                    "Select both default Dataset A and Dataset B folders for preferred comparison mode.",
                    parent=self.root,
                )
                self.notebook.select(self.dataset_defaults_tab)
                return False
            if Path(folder_a_text).expanduser().resolve() == Path(folder_b_text).expanduser().resolve():
                messagebox.showerror(
                    "Dataset folders must differ",
                    "Dataset A and Dataset B cannot use the same default folder in comparison mode.",
                    parent=self.root,
                )
                self.notebook.select(self.dataset_defaults_tab)
                return False
        return True

    def _build_presentation_tab(self):
        self.presentation_tab.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            self.presentation_tab,
            text="Generate a consolidated PowerPoint after the selected analyses finish",
            variable=self.presentation_enabled_var,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        fields = [
            ("title", "Presentation title"), ("subtitle", "Subtitle"),
            ("dut_name", "DUT / unit name"), ("project_name", "Project / campaign"),
            ("operator", "Operator"), ("output_parent", "Presentation output parent folder"),
            ("filename", "Presentation filename"), ("builder_script", "PowerPoint builder script"),
        ]
        for row, (key, label) in enumerate(fields, start=1):
            ttk.Label(self.presentation_tab, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(self.presentation_tab, textvariable=self.presentation_text_vars[key]).grid(row=row, column=1, sticky="ew", padx=8, pady=5)
            if key == "output_parent":
                ttk.Button(self.presentation_tab, text="Browse...", command=self._browse_presentation_parent).grid(row=row, column=2)
            elif key == "builder_script":
                ttk.Button(self.presentation_tab, text="Browse...", command=self._browse_builder_script).grid(row=row, column=2)
        options = ttk.LabelFrame(self.presentation_tab, text="Deck content", padding=10)
        options.grid(row=len(fields)+1, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        labels = {
            "include_title_slide": "Include title slide",
            "include_executive_summary": "Include executive summary",
            "include_failure_tables": "Include failure tables",
            "include_appendix": "Include appendix and source locations",
            "open_after_generation": "Open presentation after generation",
        }
        for index, (key, label) in enumerate(labels.items()):
            ttk.Checkbutton(options, text=label, variable=self.presentation_bool_vars[key]).grid(row=index//2, column=index%2, sticky="w", padx=(0, 30), pady=3)
        ttk.Label(
            self.presentation_tab,
            text=("This version establishes the package/export contract. Individual analysis scripts will write "
                  "manifest.json packages under analysis_packages. The final deck is generated only when a valid builder script is configured."),
            foreground="#555555", wraplength=920,
        ).grid(row=len(fields)+2, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _browse_presentation_parent(self):
        selected = filedialog.askdirectory(parent=self.root, title="Select presentation output parent folder",
                                           initialdir=self.presentation_text_vars["output_parent"].get().strip() or str(APP_DIR))
        if selected:
            self.presentation_text_vars["output_parent"].set(selected)

    def _browse_builder_script(self):
        selected = filedialog.askopenfilename(parent=self.root, title="Select PowerPoint builder script", filetypes=[("Python files", "*.py")])
        if selected:
            self.presentation_text_vars["builder_script"].set(selected)

    def _build_progress_tab(self):
        ttk.Label(self.progress_tab, textvariable=self.progress_var, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(self.progress_tab, textvariable=self.status_var, wraplength=1000).pack(anchor="w", pady=(5, 12))
        self.progress_bar = ttk.Progressbar(self.progress_tab, maximum=100, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(0, 12))
        self.status_tree = ttk.Treeview(self.progress_tab, columns=("status",), show="tree headings", height=15)
        self.status_tree.heading("#0", text="Analysis")
        self.status_tree.heading("status", text="Status")
        self.status_tree.column("#0", width=300)
        self.status_tree.column("status", width=600)
        self.status_tree.pack(fill="both", expand=True)
        for item in self.definitions:
            self.status_tree.insert("", "end", iid=item.key, text=item.name, values=(self.status_by_key[item.key],))

    def _render_selection_controls(self):
        for widget in self.selection_holder.winfo_children():
            widget.destroy()
        for item in self.definitions:
            ttk.Checkbutton(self.selection_holder, text=item.name, variable=self.selected_vars[item.key], command=self._sync_order).pack(anchor="w", pady=4)

    def _render_path_controls(self):
        for widget in self.path_rows.winfo_children():
            widget.destroy()
        self.path_rows.columnconfigure(1, weight=1)
        self.path_status_labels = {}
        for row, item in enumerate(self.definitions):
            ttk.Label(self.path_rows, text=item.name).grid(row=row, column=0, sticky="w", pady=6)
            ttk.Entry(self.path_rows, textvariable=self.script_vars[item.key]).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
            ttk.Button(self.path_rows, text="Browse...", command=lambda key=item.key: self._browse_script(key)).grid(row=row, column=2)
            label = ttk.Label(self.path_rows, width=12)
            label.grid(row=row, column=3, padx=(8, 0))
            self.path_status_labels[item.key] = label
        ttk.Label(self.path_rows, text=f"Settings are saved in {display_path(analysis_gui_settings_path())}.", foreground="#555555").grid(row=len(self.definitions), column=0, columnspan=4, sticky="w", pady=(14, 0))

    def _set_all(self, value: bool):
        for variable in self.selected_vars.values():
            variable.set(value)
        self._sync_order()

    def _sync_order(self):
        selected = {key for key, variable in self.selected_vars.items() if variable.get()}
        self.order_keys = [key for key in self.order_keys if key in selected]
        self.order_keys.extend(item.key for item in self.definitions if item.key in selected and item.key not in self.order_keys)
        self._render_order()

    def _render_order(self):
        self.order_list.delete(0, "end")
        for key in self.order_keys:
            self.order_list.insert("end", self.by_key[key].name)

    def _move_order(self, delta: int):
        selected = self.order_list.curselection()
        if not selected:
            return
        index, target = selected[0], selected[0] + delta
        if target < 0 or target >= len(self.order_keys):
            return
        self.order_keys[index], self.order_keys[target] = self.order_keys[target], self.order_keys[index]
        self._render_order()
        self.order_list.selection_set(target)

    def _browse_script(self, key: str):
        selected = filedialog.askopenfilename(parent=self.root, title=f"Select {self.by_key[key].name} analysis script", filetypes=[("Python files", "*.py"), ("All files", "*.*")])
        if selected:
            self.script_vars[key].set(selected)
            self._refresh_path_statuses()

    def _refresh_path_statuses(self):
        for key, label in self.path_status_labels.items():
            path = Path(self.script_vars[key].get().strip())
            valid = path.is_file() and path.suffix.lower() == ".py"
            label.configure(text="Found" if valid else "Missing", foreground="#187A28" if valid else "#B00020")

    def add_custom_analysis(self):
        path_text = filedialog.askopenfilename(parent=self.root, title="Select an analysis Python script", filetypes=[("Python files", "*.py")])
        if not path_text:
            return
        default_name = Path(path_text).stem.replace("_", " ").strip()
        name = simpledialog.askstring("Analysis display name", "Enter the name shown in the unified GUI:", initialvalue=default_name, parent=self.root)
        if not name or not name.strip():
            return
        key = safe_key(name.strip(), set(self.by_key))
        item = AnalysisDefinition(key, name.strip(), [Path(path_text).name], "User-added analysis script", True)
        self.definitions.append(item)
        self.by_key[key] = item
        self.script_vars[key] = tk.StringVar(value=path_text)
        self.selected_vars[key] = tk.BooleanVar(value=True)
        self.status_by_key[key] = "Not selected"
        self.order_keys.append(key)
        self._render_selection_controls()
        self._render_path_controls()
        self.status_tree.insert("", "end", iid=key, text=item.name, values=("Not selected",))
        self._sync_order()
        self._refresh_path_statuses()
        self.save_current_config()

    def remove_selected_custom(self):
        selected = self.order_list.curselection()
        if not selected:
            messagebox.showinfo("Remove custom analysis", "Select a custom analysis in the execution order first.", parent=self.root)
            return
        key = self.order_keys[selected[0]]
        item = self.by_key[key]
        if not item.custom:
            messagebox.showinfo("Built-in analysis", "Built-in analysis definitions cannot be removed. Clear their checkbox if they are not needed.", parent=self.root)
            return
        if not messagebox.askyesno("Remove custom analysis", f"Remove '{item.name}' from this launcher?\n\nThe Python script itself will not be deleted.", parent=self.root):
            return
        self.definitions = [entry for entry in self.definitions if entry.key != key]
        self.by_key.pop(key, None)
        self.script_vars.pop(key, None)
        self.selected_vars.pop(key, None)
        self.status_by_key.pop(key, None)
        self.order_keys = [entry for entry in self.order_keys if entry != key]
        if self.status_tree.exists(key):
            self.status_tree.delete(key)
        self._render_selection_controls()
        self._render_path_controls()
        self._render_order()
        self._refresh_path_statuses()
        self.save_current_config()

    def _current_config(self) -> dict:
        custom = []
        for item in self.definitions:
            if item.custom:
                data = asdict(item)
                data["filename"] = item.default_filenames[0] if item.default_filenames else ""
                custom.append(data)
        return {
            "scripts": {key: variable.get().strip() for key, variable in self.script_vars.items()},
            "selected": [key for key, variable in self.selected_vars.items() if variable.get()],
            "order": list(self.order_keys),
            "custom_analyses": custom,
            "open_child_console": self.console_var.get(),
            "show_launch_prompt": self.prompt_var.get(),
            "dataset_defaults": self._dataset_defaults(),
            "presentation": {
                "enabled": self.presentation_enabled_var.get(),
                **{key: variable.get().strip() for key, variable in self.presentation_text_vars.items()},
                **{key: variable.get() for key, variable in self.presentation_bool_vars.items()},
            },
        }

    def save_current_config(self):
        self.config = self._current_config()
        save_config(self.config)
        self._refresh_path_statuses()
        self.status_var.set(f"Saved analysis selections and script paths to {display_path(analysis_gui_settings_path())}.")

    def _set_status(self, key: str, text: str):
        self.status_by_key[key] = text
        if self.status_tree.exists(key):
            self.status_tree.set(key, "status", text)

    @staticmethod
    def _safe_folder_component(value: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*]+', "_", str(value)).strip().rstrip(".")
        return cleaned or "Paramount Analysis Presentation"

    def _create_presentation_run_folder(self) -> Path:
        from datetime import datetime
        parent = Path(self.presentation_text_vars["output_parent"].get().strip()).expanduser().resolve()
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base_name = f"{timestamp} - {self._safe_folder_component(self.presentation_text_vars['title'].get())}"
        candidate = parent / base_name
        run_number = 2
        while candidate.exists():
            candidate = parent / f"{base_name} - Run {run_number}"
            run_number += 1
        candidate.mkdir(parents=False, exist_ok=False)
        (candidate / ANALYSIS_PACKAGES_FOLDER).mkdir()
        return candidate

    def _presentation_settings(self) -> dict:
        return {
            **{key: variable.get().strip() for key, variable in self.presentation_text_vars.items()},
            **{key: variable.get() for key, variable in self.presentation_bool_vars.items()},
        }

    def _write_presentation_manifest(self, final_status: str = "running") -> None:
        if not self.presentation_manifest_path:
            return
        payload = {
            "schema_version": PRESENTATION_SCHEMA_VERSION,
            "status": final_status,
            "run_folder": str(self.presentation_run_folder),
            "presentation": self._presentation_settings(),
            "analysis_context": self._dataset_defaults(),
            "suite_context": SuiteContext.current().to_dict(),
            "selected_analyses": [
                {"analysis_id": key, "display_name": self.by_key[key].name,
                 "status": self.status_by_key.get(key, "Unknown"),
                 "package_status": self.package_status_by_key.get(key, "Not checked")}
                for key in self.sequence
            ],
        }
        self.presentation_manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _package_folder(self, key: str) -> Path | None:
        if not self.presentation_run_folder:
            return None
        return self.presentation_run_folder / ANALYSIS_PACKAGES_FOLDER / key

    def _check_analysis_package(self, key: str) -> str:
        folder = self._package_folder(key)
        if folder is None:
            return "Presentation export disabled"
        manifest = folder / "manifest.json"
        if not manifest.is_file():
            return "Package not found"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("analysis_id") != key:
                return "Package manifest analysis_id mismatch"
            return "Package ready"
        except Exception as error:
            return f"Invalid package manifest: {error}"

    def _run_powerpoint_builder(self) -> tuple[bool, str]:
        if not self.presentation_run_folder or not self.presentation_manifest_path:
            return False, "No presentation run folder is available."
        builder = Path(self.presentation_text_vars["builder_script"].get().strip())
        if not builder.is_file():
            return False, "Presentation packages were collected, but no valid PowerPoint builder script is configured yet."
        try:
            result = subprocess.run([sys.executable, str(builder), "--manifest", str(self.presentation_manifest_path)],
                                    cwd=str(builder.parent), capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or f"Exit code {result.returncode}").strip()
                return False, f"PowerPoint builder failed: {detail}"
            return True, (result.stdout or "PowerPoint builder completed successfully.").strip()
        except Exception as error:
            return False, f"PowerPoint builder could not be started: {error}"

    def start_sequence(self):
        self._sync_order()
        if not self.order_keys:
            messagebox.showerror("No analyses selected", "Select at least one analysis script.", parent=self.root)
            return
        missing = [self.by_key[key].name for key in self.order_keys if not Path(self.script_vars[key].get().strip()).is_file()]
        if missing:
            messagebox.showerror("Missing analysis scripts", "Configure valid script paths for:\n\n" + "\n".join(missing), parent=self.root)
            self.notebook.select(self.paths_tab)
            return
        if not self._validate_dataset_defaults():
            return
        if self.presentation_enabled_var.get():
            parent_text = self.presentation_text_vars["output_parent"].get().strip()
            filename = self.presentation_text_vars["filename"].get().strip()
            if not parent_text or not Path(parent_text).is_dir():
                messagebox.showerror("Invalid presentation folder", "Select an existing presentation output parent folder.", parent=self.root)
                self.notebook.select(self.presentation_tab)
                return
            if not filename.lower().endswith(".pptx"):
                messagebox.showerror("Invalid presentation filename", "The presentation filename must end with .pptx.", parent=self.root)
                self.notebook.select(self.presentation_tab)
                return
        self.save_current_config()
        self.sequence = list(self.order_keys)
        self.presentation_run_folder = None
        self.presentation_manifest_path = None
        self.package_status_by_key = {}
        if self.presentation_enabled_var.get():
            try:
                self.presentation_run_folder = self._create_presentation_run_folder()
                self.presentation_manifest_path = self.presentation_run_folder / PRESENTATION_MANIFEST_NAME
                self._write_presentation_manifest("running")
            except Exception as error:
                messagebox.showerror("Presentation setup failed", f"The presentation run folder could not be created:\n\n{error}", parent=self.root)
                return
        self.sequence_index = -1
        self.stopping = False
        for item in self.definitions:
            self._set_status(item.key, "Queued" if item.key in self.sequence else "Not selected")
        self.progress_bar["value"] = 0
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.notebook.select(self.progress_tab)
        self._advance_sequence()

    def _advance_sequence(self):
        if self.stopping:
            self._finish_sequence("Sequence stopped after the current analysis.")
            return
        self.sequence_index += 1
        if self.sequence_index >= len(self.sequence):
            self._finish_sequence("All selected analyses are complete.")
            return
        self.active_key = self.sequence[self.sequence_index]
        item = self.by_key[self.active_key]
        self.progress_bar["value"] = 100 * self.sequence_index / max(1, len(self.sequence))
        self.progress_var.set(f"Analysis {self.sequence_index + 1} of {len(self.sequence)}: {item.name}")
        self._set_status(item.key, "Waiting to launch")
        if self.prompt_var.get():
            self._show_launch_dialog(item)
        else:
            self._launch_analysis(item)

    def _show_launch_dialog(self, item: AnalysisDefinition):
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Ready: {item.name}")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Ready to launch {item.name}", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(frame, text="The analysis tool will open as a separate process. Complete or cancel its own GUI, then close it to return to this sequence.", wraplength=540).pack(anchor="w", pady=(8, 14))
        ttk.Label(frame, text=self.script_vars[item.key].get(), foreground="#555555", wraplength=540).pack(anchor="w", pady=(0, 14))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        def launch(): dialog.destroy(); self._launch_analysis(item)
        def skip(): dialog.destroy(); self._set_status(item.key, "Skipped by user"); self.root.after(50, self._advance_sequence)
        def stop(): dialog.destroy(); self._finish_sequence("Sequence stopped before launching the next analysis.")
        ttk.Button(buttons, text="Stop Sequence", command=stop).pack(side="left")
        ttk.Button(buttons, text="Skip Analysis", command=skip).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Launch Analysis", command=launch).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", stop)
        dialog.update_idletasks()
        x = max(0, (dialog.winfo_screenwidth() - dialog.winfo_reqwidth()) // 2)
        y = max(0, (dialog.winfo_screenheight() - dialog.winfo_reqheight()) // 2)
        dialog.geometry(f"+{x}+{y}")

    def _launch_analysis(self, item: AnalysisDefinition):
        script = Path(self.script_vars[item.key].get().strip()).resolve()
        environment = os.environ.copy()
        environment["PARAMOUNT_UNIFIED_ANALYSIS_LAUNCHER"] = "1"
        environment["PARAMOUNT_SELECTED_ANALYSIS"] = item.key
        dataset_defaults = self._dataset_defaults()
        environment["PARAMOUNT_ANALYSIS_MODE"] = dataset_defaults["preferred_mode"]
        environment["PARAMOUNT_DATASET_A_FOLDER"] = dataset_defaults["dataset_a_folder"]
        environment["PARAMOUNT_DATASET_B_FOLDER"] = dataset_defaults["dataset_b_folder"]
        environment["PARAMOUNT_DATASET_A_LABEL"] = dataset_defaults["dataset_a_label"]
        environment["PARAMOUNT_DATASET_B_LABEL"] = dataset_defaults["dataset_b_label"]
        output_parent = self.presentation_text_vars["output_parent"].get().strip()
        if output_parent:
            environment["PARAMOUNT_ANALYSIS_OUTPUT_FOLDER"] = output_parent
        package_folder = None
        if self.presentation_enabled_var.get() and self.presentation_run_folder:
            package_folder = self._package_folder(item.key)
            package_folder.mkdir(parents=True, exist_ok=True)
            environment["PARAMOUNT_POWERPOINT_EXPORT_ENABLED"] = "1"
            environment["PARAMOUNT_POWERPOINT_RUN_FOLDER"] = str(self.presentation_run_folder)
            environment["PARAMOUNT_POWERPOINT_PACKAGE_FOLDER"] = str(package_folder)
            environment["PARAMOUNT_POWERPOINT_ANALYSIS_ID"] = item.key
            environment["PARAMOUNT_POWERPOINT_SCHEMA_VERSION"] = str(PRESENTATION_SCHEMA_VERSION)
            environment["PARAMOUNT_POWERPOINT_PRESENTATION_TITLE"] = self.presentation_text_vars["title"].get().strip()
            environment["PARAMOUNT_POWERPOINT_DUT_NAME"] = self.presentation_text_vars["dut_name"].get().strip()
            environment["PARAMOUNT_POWERPOINT_PROJECT_NAME"] = self.presentation_text_vars["project_name"].get().strip()
        try:
            self.runner.launch_analysis(
                registry=REGISTRY,
                analysis_key=item.key,
                script=script,
                environment=environment,
                dataset_folders=[dataset_defaults["dataset_a_folder"], dataset_defaults["dataset_b_folder"]],
                output_parent=output_parent,
                package_folder=package_folder,
                new_console=self.console_var.get(),
            )
            self._set_status(item.key, f"Running (PID {self.runner.pid})")
            self.status_var.set(f"Launched {item.name}. Waiting for its process to close before continuing.")
            self.root.after(POLL_INTERVAL_MS, self._poll_process)
        except Exception as error:
            self._handle_failure(item, f"Launch failed: {error}")

    def _poll_process(self):
        if self.active_key is None:
            return
        result = self.runner.poll()
        if result is None:
            if self.runner.busy():
                self.root.after(POLL_INTERVAL_MS, self._poll_process)
            return
        if result.manifest_error:
            print(f"Warning: analysis manifest could not be written:\n{result.manifest_error}")
        return_code = result.exit_code
        item = self.by_key[self.active_key]
        if return_code == 0:
            if self.presentation_enabled_var.get():
                package_status = self._check_analysis_package(item.key)
                self.package_status_by_key[item.key] = package_status
                self._set_status(item.key, f"Completed successfully; {package_status}")
                self._write_presentation_manifest("running")
            else:
                self._set_status(item.key, "Completed successfully")
            self.status_var.set(f"{item.name} finished successfully. Preparing the next analysis.")
            self.root.after(100, self._advance_sequence)
        else:
            self._handle_failure(item, f"Exited with code {return_code}")

    def _handle_failure(self, item: AnalysisDefinition, detail: str):
        self._set_status(item.key, detail)
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{item.name} did not complete")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"{item.name} did not complete successfully.", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text=detail + "\n\nA user cancellation may also produce a nonzero exit code. Choose how to continue.", wraplength=520).pack(anchor="w", pady=12)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        def retry(): dialog.destroy(); self._launch_analysis(item)
        def skip(): dialog.destroy(); self._set_status(item.key, "Skipped after nonzero exit"); self._advance_sequence()
        def stop(): dialog.destroy(); self._finish_sequence("Sequence stopped after an analysis error or cancellation.")
        ttk.Button(buttons, text="Stop Sequence", command=stop).pack(side="left")
        ttk.Button(buttons, text="Skip / Continue", command=skip).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Retry Analysis", command=retry).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", stop)

    def stop_after_current(self):
        self.stopping = True
        self.status_var.set("The sequence will stop after the currently running analysis closes.")
        self.stop_button.configure(state="disabled")

    def _finish_sequence(self, text: str):
        self.active_key = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        completed = max(0, min(self.sequence_index + 1, len(self.sequence)))
        self.progress_bar["value"] = 100 if self.sequence and completed >= len(self.sequence) else 100 * completed / max(1, len(self.sequence))
        self.progress_var.set(text)
        self.status_var.set(text)
        presentation_message = ""
        if self.presentation_enabled_var.get() and self.presentation_run_folder:
            for key in self.sequence:
                self.package_status_by_key.setdefault(key, self._check_analysis_package(key))
            self._write_presentation_manifest("complete" if "complete" in text.lower() else "stopped")
            built, detail = self._run_powerpoint_builder()
            presentation_message = f"\n\nPresentation run folder:\n{self.presentation_run_folder}\n\n{detail}"
            if built:
                self.status_var.set(text + " PowerPoint generation completed.")
        messagebox.showinfo("Analysis sequence", text + presentation_message, parent=self.root)

    def on_close(self):
        if self.runner.busy():
            if not messagebox.askyesno("Analysis is running", "A child analysis process is still running. Close only the unified launcher and leave that analysis running?", parent=self.root):
                return
        self.save_current_config()
        self.root.destroy()


def self_test() -> int:
    config = default_config()
    assert len(BUILTIN_ANALYSES) == 16
    assert set(config["scripts"]) == {item.key for item in BUILTIN_ANALYSES}
    assert safe_key("My New Analysis", {"my_new_analysis"}) == "my_new_analysis_2"
    assert child_creation_flags(False) == 0
    assert default_config()["presentation"]["filename"].endswith(".pptx")
    assert POWERPOINT_BUILDER_FILENAME.endswith(".py")
    assert default_config()["dataset_defaults"]["preferred_mode"] == ANALYSIS_MODE_INDIVIDUAL
    assert set(ANALYSIS_MODE_VALUES) == {"INDIVIDUAL", "SINGLE", "COMPARE"}
    analog_keys = ["setpoint_transfer_function", "analog_processing_timing", "rf_enable_timing",
                   "interlock_response_timing", "cex_lock_acquisition"]
    assert set(analog_keys) <= {item.key for item in BUILTIN_ANALYSES}
    assert migrate_analysis_entries(["power_accuracy", "analog_port_timing", "psync"]) == [
        "power_accuracy", *analog_keys, "psync"
    ]
    assert migrate_analysis_entries(["t4", "t5", "t6", "t7", "t8"]) == analog_keys
    assert migrate_analysis_entries(["my_custom"]) == ["my_custom"]
    sample = APP_DIR / "temporary_analysis_test.py"
    try:
        sample.write_text("print('ok')\n", encoding="utf-8")
        definition = AnalysisDefinition("temporary", "Temporary", [sample.name], custom=True)
        assert Path(discover_script(definition.default_filenames)).is_file()
    finally:
        sample.unlink(missing_ok=True)
    for line in describe_unavailable(REGISTRY):
        print(f"Plugin {line}")
    print("Unified analysis launcher self-test passed.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    migrate_legacy_settings()
    root = tk.Tk()
    problems = describe_unavailable(REGISTRY)
    if problems:
        print("\n".join(problems))
        messagebox.showwarning("Plugins unavailable", "These plugins were skipped:\n\n" + "\n\n".join(problems), parent=root)
    UnifiedAnalysisApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
