"""Unified sequential launcher for Paramount data-collection GUIs.

This application does not import collection scripts. It launches one child process at a
time, waits for that process to exit, and then presents the setup stage for the next test.
Bench addresses come from the selected bench profile (runtime/Bench_Profiles.json) and are
passed to each child script as PARAMOUNT_* environment variables.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
from suite_common.bench import (  # noqa: E402
    BENCH_PROFILE_ENV,
    EQUIPMENT_FIELDS,
    FIELD_BY_KEY,
    detect_instruments,
    empty_addresses,
    load_bench_profiles,
    save_bench_profiles,
)
from suite_common.config import (
    bench_profiles_path,
    collection_gui_settings_path,
    display_path,
    migrate_legacy_settings,
)
from suite_common.paths import (
    load_json_settings,
    resolve_portable_path,
    resolve_saved_script,
    save_json_settings,
    to_portable_path,
)
from suite_sdk.registry import describe_unavailable, load_registry
from suite_apps.collection_process_manager import CollectionProcessManager  # noqa: E402

APP_TITLE = "Paramount Unified Data Collection"
APP_DIR = Path(__file__).resolve().parent
POLL_INTERVAL_MS = 400
COLLECTION_PARENT_FOLDER_ENV = "PARAMOUNT_COLLECTION_PARENT_FOLDER"
RISE_FALL_TEST_KEY = "rise_fall_time_verification"
RISE_FALL_EDGES = ("RISE", "FALL")
SEQUENCE_MODE_SEPARATOR = "::"
EQUIPMENT_LABELS = {item.key: item.label for item in EQUIPMENT_FIELDS}


@dataclass
class TestDefinition:
    key: str
    name: str
    script: str
    equipment: list[str]
    instructions: str
    image: str = ""


# Tests come from the plugin registry (plugin.json files); add a test there, not here.
REGISTRY = load_registry()
TESTS = [
    TestDefinition(t.key, t.name, str(t.script), list(t.equipment), t.instructions, t.image)
    for t in REGISTRY.tests("collection")
]
TEST_BY_KEY = {test.key: test for test in TESTS}
RISE_FALL_SCRIPTS = {mode: str(path) for mode, path in REGISTRY.test("collection", RISE_FALL_TEST_KEY).modes.items()}
RISE_FALL_MODES = tuple(RISE_FALL_SCRIPTS)


def migrate_selection(values: list[str]) -> list[str]:
    """Map legacy keys in saved settings (plugin.json "aliases") to current tests, keeping order."""
    return [key for key in REGISTRY.resolve_aliases("collection", values) if key in TEST_BY_KEY]


def default_config() -> dict:
    return {
        "collection_parent_folder": str(Path.home()),
        "scripts": {test.key: test.script for test in TESTS},
        "rise_fall_scripts": dict(RISE_FALL_SCRIPTS),
        "selected_tests": [test.key for test in TESTS],
        "test_order": [test.key for test in TESTS],
        "show_setup_pages": True,
        "open_child_console": True,
        "rise_fall_modes": list(RISE_FALL_MODES),
        "rise_fall_mode_edges": {
            mode: list(RISE_FALL_EDGES) for mode in RISE_FALL_MODES
        },
        "setup_images": {test.key: test.image for test in TESTS},
    }


def load_config() -> dict:
    config = default_config()
    saved = load_json_settings(collection_gui_settings_path())
    for key, saved_path in (saved.get("scripts") or {}).items():
        if key in TEST_BY_KEY:
            config["scripts"][key] = resolve_saved_script(saved_path, TEST_BY_KEY[key].script)
    for mode, saved_path in (saved.get("rise_fall_scripts") or {}).items():
        if mode in RISE_FALL_SCRIPTS:
            config["rise_fall_scripts"][mode] = resolve_saved_script(saved_path, RISE_FALL_SCRIPTS[mode])
    if isinstance(saved.get("setup_images"), dict):
        config["setup_images"].update(saved["setup_images"])
        # An image saved under a legacy key carries over to the tests that replaced it.
        for legacy_key, image in saved["setup_images"].items():
            if legacy_key in TEST_BY_KEY or not str(image).strip():
                continue
            for replacement_key in migrate_selection([legacy_key]):
                if not str(saved["setup_images"].get(replacement_key, "")).strip():
                    config["setup_images"][replacement_key] = str(image).strip()
    for key in (
        "selected_tests", "test_order", "show_setup_pages",
        "open_child_console", "rise_fall_modes", "rise_fall_mode_edges",
    ):
        if key in saved:
            config[key] = saved[key]
    for key in ("selected_tests", "test_order"):
        config[key] = migrate_selection([str(item) for item in config[key]])
    if saved.get("collection_parent_folder"):
        config["collection_parent_folder"] = resolve_portable_path(saved["collection_parent_folder"])
    return config


def save_config(config: dict) -> None:
    portable = dict(config)
    portable["scripts"] = {key: to_portable_path(value) for key, value in config["scripts"].items()}
    portable["rise_fall_scripts"] = {key: to_portable_path(value) for key, value in config["rise_fall_scripts"].items()}
    portable["collection_parent_folder"] = to_portable_path(config["collection_parent_folder"])
    save_json_settings(collection_gui_settings_path(), portable)


def build_child_environment(
    addresses: dict,
    test: TestDefinition,
    collection_parent_folder: str = "",
    rise_fall_mode: str | None = None,
    rise_fall_edge: str | None = None,
    bench_profile: str = "",
) -> dict:
    environment = os.environ.copy()
    environment["PARAMOUNT_UNIFIED_LAUNCHER"] = "1"
    environment["PARAMOUNT_SELECTED_TEST"] = test.key
    environment[COLLECTION_PARENT_FOLDER_ENV] = str(collection_parent_folder).strip()
    if bench_profile:
        environment[BENCH_PROFILE_ENV] = bench_profile
    if rise_fall_mode:
        environment["PARAMOUNT_RISE_FALL_MODE"] = rise_fall_mode
        environment["PARAMOUNT_RISE_FALL_TEST_TYPE"] = rise_fall_mode
    if rise_fall_edge:
        environment["PARAMOUNT_RISE_FALL_EDGE"] = rise_fall_edge
        environment["PARAMOUNT_RISE_FALL_TRANSITION"] = rise_fall_edge
    for equipment, value in addresses.items():
        if equipment in FIELD_BY_KEY:
            environment[FIELD_BY_KEY[equipment].env] = str(value).strip()
    return environment


def _com_port_from_visa(resource: str) -> str | None:
    """Map 'ASRL5::INSTR' to 'COM5'."""
    upper = resource.upper()
    if upper.startswith("ASRL") and upper[4:].split("::")[0].isdigit():
        return "COM" + upper[4:].split("::")[0]
    return None


class UnifiedCollectionApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()
        self.bench_data = load_bench_profiles()
        self.runner = CollectionProcessManager()
        self.sequence: list[str] = []
        self.sequence_index = -1
        self.active_key: str | None = None
        self.active_mode: str | None = None
        self.active_edge: str | None = None
        self.stopping = False
        self.status_by_key = {test.key: "Not selected" for test in TESTS}
        self.bench_profile_var = tk.StringVar(value=self.bench_data["active_profile"])
        active_addresses = self.bench_data["profiles"][self.bench_data["active_profile"]]
        self.address_vars = {item.key: tk.StringVar(value=active_addresses.get(item.key, "")) for item in EQUIPMENT_FIELDS}
        self.address_boxes: dict[str, ttk.Combobox] = {}
        self.detected_resources: list[str] = []
        self.collection_parent_folder_var = tk.StringVar(
            value=str(self.config.get("collection_parent_folder", APP_DIR))
        )
        self.script_vars = {test.key: tk.StringVar(value=self.config["scripts"].get(test.key, "")) for test in TESTS}
        self.rise_fall_script_vars = {
            mode: tk.StringVar(value=self.config.get("rise_fall_scripts", {}).get(mode, ""))
            for mode in RISE_FALL_MODES
        }
        self.selected_vars = {test.key: tk.BooleanVar(value=test.key in self.config.get("selected_tests", [])) for test in TESTS}
        saved_rise_fall_modes = set(self.config.get("rise_fall_modes", RISE_FALL_MODES))
        saved_mode_edges = self.config.get("rise_fall_mode_edges")
        if not isinstance(saved_mode_edges, dict):
            # Backward compatibility: modes selected by older launcher versions
            # receive both edges by default.
            saved_mode_edges = {
                mode: list(RISE_FALL_EDGES) if mode in saved_rise_fall_modes else []
                for mode in RISE_FALL_MODES
            }
        self.rise_fall_edge_vars = {
            mode: {
                edge: tk.BooleanVar(value=edge in saved_mode_edges.get(mode, []))
                for edge in RISE_FALL_EDGES
            }
            for mode in RISE_FALL_MODES
        }
        self.rise_fall_menu_button = None
        self.show_setup_var = tk.BooleanVar(value=bool(self.config.get("show_setup_pages", True)))
        self.console_var = tk.BooleanVar(value=bool(self.config.get("open_child_console", True)))
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.StringVar(value="No sequence is running.")
        self._build_gui()
        self._refresh_script_statuses()

    def _build_gui(self):
        self.root.title(APP_TITLE)
        self.root.geometry("1120x820")
        self.root.minsize(980, 700)
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
        ttk.Label(
            outer,
            text="Enter bench addresses once, select and order collection tests, then run each existing test GUI sequentially. Child scripts remain separate processes.",
            wraplength=1040,
        ).pack(anchor="w", pady=(2, 10))
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        bench = ttk.Frame(notebook, padding=12)
        tests = ttk.Frame(notebook, padding=12)
        paths = ttk.Frame(notebook, padding=12)
        progress = ttk.Frame(notebook, padding=12)
        notebook.add(bench, text="Bench Equipment")
        notebook.add(tests, text="Test Sequence")
        notebook.add(paths, text="Script Paths")
        notebook.add(progress, text="Progress")

        bench.columnconfigure(1, weight=1)
        profile_row = ttk.Frame(bench)
        profile_row.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        ttk.Label(profile_row, text="Bench profile", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.profile_box = ttk.Combobox(profile_row, textvariable=self.bench_profile_var, state="readonly", width=32)
        self.profile_box.pack(side="left", padx=8)
        self.profile_box.bind("<<ComboboxSelected>>", lambda _event: self._load_bench_profile(self.bench_profile_var.get()))
        ttk.Button(profile_row, text="Save Profile", command=self._save_bench_profile).pack(side="left")
        ttk.Button(profile_row, text="Save As New...", command=self._save_bench_profile_as).pack(side="left", padx=6)
        ttk.Button(profile_row, text="Delete", command=self._delete_bench_profile).pack(side="left")
        ttk.Button(profile_row, text="Detect Instruments", command=self._detect_instruments).pack(side="right")
        self._refresh_profile_choices()
        for row, item in enumerate(EQUIPMENT_FIELDS, start=1):
            ttk.Label(bench, text=item.label).grid(row=row, column=0, sticky="w", pady=5)
            box = ttk.Combobox(bench, textvariable=self.address_vars[item.key], width=55)
            box.grid(row=row, column=1, sticky="ew", pady=5)
            self.address_boxes[item.key] = box
            ttk.Label(bench, text=item.env, foreground="#666666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        self._refresh_address_choices()
        output_row = len(EQUIPMENT_FIELDS) + 1
        ttk.Label(bench, text="Default collection data parent folder").grid(row=output_row, column=0, sticky="w", pady=(14, 5))
        ttk.Entry(bench, textvariable=self.collection_parent_folder_var, width=55).grid(row=output_row, column=1, sticky="ew", pady=(14, 5))
        ttk.Button(bench, text="Browse...", command=self._browse_collection_parent_folder).grid(row=output_row, column=2, sticky="w", padx=(10, 0), pady=(14, 5))
        ttk.Label(
            bench,
            text=f"Passed to each child GUI as {COLLECTION_PARENT_FOLDER_ENV}. Each child GUI may still override it.",
            foreground="#666666",
            wraplength=900,
        ).grid(row=output_row + 1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Checkbutton(bench, text="Show setup and instruction page before every test", variable=self.show_setup_var).grid(row=output_row+2, column=0, columnspan=3, sticky="w", pady=(8, 4))
        ttk.Checkbutton(bench, text="Open each collection script in a separate console", variable=self.console_var).grid(row=output_row+3, column=0, columnspan=3, sticky="w")

        left = ttk.LabelFrame(tests, text="Select Tests", style="Section.TLabelframe", padding=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        for test in TESTS:
            row_frame = ttk.Frame(left)
            row_frame.pack(fill="x", anchor="w", pady=4)
            ttk.Checkbutton(
                row_frame,
                text=test.name,
                variable=self.selected_vars[test.key],
                command=lambda key=test.key: self._on_test_selection_changed(key),
            ).pack(side="left", anchor="w")
            if test.key == RISE_FALL_TEST_KEY:
                self.rise_fall_menu_button = ttk.Menubutton(row_frame, text="Test Types...", direction="below")
                mode_menu = tk.Menu(self.rise_fall_menu_button, tearoff=False)
                for mode in RISE_FALL_MODES:
                    edge_menu = tk.Menu(mode_menu, tearoff=False)
                    for edge in RISE_FALL_EDGES:
                        edge_menu.add_checkbutton(
                            label=edge,
                            variable=self.rise_fall_edge_vars[mode][edge],
                            command=self._on_rise_fall_modes_changed,
                        )
                    mode_menu.add_cascade(label=mode, menu=edge_menu)
                self.rise_fall_menu_button["menu"] = mode_menu
                self._update_rise_fall_menu_button()
        controls = ttk.Frame(left)
        controls.pack(fill="x", pady=(15, 0))
        ttk.Button(controls, text="Select All", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(controls, text="Clear All", command=lambda: self._set_all(False)).pack(side="left", padx=8)

        right = ttk.LabelFrame(tests, text="Execution Order", style="Section.TLabelframe", padding=10)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.order_list = tk.Listbox(right, height=13, exportselection=False)
        self.order_list.pack(fill="both", expand=True)
        order_controls = ttk.Frame(right)
        order_controls.pack(fill="x", pady=(8, 0))
        ttk.Button(order_controls, text="Move Up", command=lambda: self._move_order(-1)).pack(side="left")
        ttk.Button(order_controls, text="Move Down", command=lambda: self._move_order(1)).pack(side="left", padx=8)
        initial_order = [key for key in self.config.get("test_order", []) if key in TEST_BY_KEY]
        initial_order.extend(test.key for test in TESTS if test.key not in initial_order)
        self.order_keys = initial_order
        self._render_order()

        paths.columnconfigure(1, weight=1)
        self.path_status_labels = {}
        path_row = 0
        for test in TESTS:
            if test.key == RISE_FALL_TEST_KEY:
                ttk.Label(
                    paths,
                    text="Rise and Fall Time Verification scripts",
                    style="Section.TLabel",
                ).grid(row=path_row, column=0, columnspan=4, sticky="w", pady=(12, 4))
                path_row += 1
                for mode in RISE_FALL_MODES:
                    label_text = f"    {mode} collection script"
                    ttk.Label(paths, text=label_text).grid(row=path_row, column=0, sticky="w", pady=6)
                    ttk.Entry(paths, textvariable=self.rise_fall_script_vars[mode]).grid(
                        row=path_row, column=1, sticky="ew", pady=6, padx=8
                    )
                    ttk.Button(
                        paths,
                        text="Browse...",
                        command=lambda mode=mode: self._browse_rise_fall_script(mode),
                    ).grid(row=path_row, column=2)
                    label = ttk.Label(paths, text="", width=12)
                    label.grid(row=path_row, column=3, padx=(8, 0))
                    self.path_status_labels[f"{RISE_FALL_TEST_KEY}:{mode}"] = label
                    path_row += 1
                continue
            ttk.Label(paths, text=test.name).grid(row=path_row, column=0, sticky="w", pady=6)
            ttk.Entry(paths, textvariable=self.script_vars[test.key]).grid(row=path_row, column=1, sticky="ew", pady=6, padx=8)
            ttk.Button(paths, text="Browse...", command=lambda key=test.key: self._browse_script(key)).grid(row=path_row, column=2)
            label = ttk.Label(paths, text="", width=12)
            label.grid(row=path_row, column=3, padx=(8, 0))
            self.path_status_labels[test.key] = label
            path_row += 1
        ttk.Label(paths, text="Script paths inside the repo are saved as repo-relative paths in runtime/collection_gui_settings.json; missing paths are rediscovered automatically.", foreground="#555555").grid(row=path_row, column=0, columnspan=4, sticky="w", pady=(14, 0))

        ttk.Label(progress, textvariable=self.progress_var, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(progress, textvariable=self.status_var, wraplength=940).pack(anchor="w", pady=(5, 12))
        self.status_tree = ttk.Treeview(progress, columns=("status",), show="tree headings", height=12)
        self.status_tree.heading("#0", text="Test")
        self.status_tree.heading("status", text="Status")
        self.status_tree.column("#0", width=280)
        self.status_tree.column("status", width=520)
        self.status_tree.pack(fill="both", expand=True)
        for test in TESTS:
            self.status_tree.insert("", "end", iid=test.key, text=test.name, values=(self.status_by_key[test.key],))

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        self.start_button = ttk.Button(footer, text="Start Selected Sequence", command=self.start_sequence)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(footer, text="Stop After Current Test", command=self.stop_after_current, state="disabled")
        self.stop_button.pack(side="right", padx=8)
        ttk.Button(footer, text="Save Bench Defaults", command=self.save_current_config).pack(side="left")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _current_addresses(self) -> dict[str, str]:
        return {key: variable.get().strip() for key, variable in self.address_vars.items()}

    def _refresh_profile_choices(self):
        self.profile_box.configure(values=sorted(self.bench_data["profiles"]))

    def _refresh_address_choices(self):
        com_ports = sorted({port for port in map(_com_port_from_visa, self.detected_resources) if port})
        visa = [resource for resource in self.detected_resources if not resource.upper().startswith("ASRL")]
        for item in EQUIPMENT_FIELDS:
            known = {profile.get(item.key, "") for profile in self.bench_data["profiles"].values()}
            detected = com_ports if item.kind == "com" else visa if item.kind == "visa" else []
            self.address_boxes[item.key].configure(values=sorted(value for value in known | set(detected) if value))

    def _load_bench_profile(self, name: str):
        addresses = self.bench_data["profiles"].get(name, empty_addresses())
        for key, variable in self.address_vars.items():
            variable.set(addresses.get(key, ""))
        self.bench_data["active_profile"] = name
        save_bench_profiles(self.bench_data)
        self.status_var.set(f"Loaded bench profile '{name}'.")

    def _save_bench_profile(self, name: str | None = None):
        name = name or self.bench_profile_var.get()
        self.bench_data["profiles"][name] = self._current_addresses()
        self.bench_data["active_profile"] = name
        save_bench_profiles(self.bench_data)
        self.bench_profile_var.set(name)
        self._refresh_profile_choices()
        self._refresh_address_choices()
        self.status_var.set(f"Saved bench profile '{name}' to {display_path(bench_profiles_path())}.")

    def _save_bench_profile_as(self):
        name = simpledialog.askstring("New bench profile", "Bench profile name (e.g. 'Lab B - Bench 3'):", parent=self.root)
        if name and name.strip():
            self._save_bench_profile(name.strip())

    def _delete_bench_profile(self):
        name = self.bench_profile_var.get()
        if len(self.bench_data["profiles"]) <= 1:
            messagebox.showinfo("Bench profile", "At least one bench profile must remain.", parent=self.root)
            return
        if not messagebox.askyesno("Delete bench profile", f"Delete bench profile '{name}'?", parent=self.root):
            return
        self.bench_data["profiles"].pop(name, None)
        first = sorted(self.bench_data["profiles"])[0]
        self.bench_profile_var.set(first)
        self._refresh_profile_choices()
        self._load_bench_profile(first)

    def _detect_instruments(self):
        self.status_var.set("Detecting VISA instruments and COM ports...")

        def worker():
            found = detect_instruments()
            self.root.after(0, lambda: self._finish_detection(found))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_detection(self, found: list[str]):
        self.detected_resources = found
        self._refresh_address_choices()
        self.status_var.set(
            f"Detected {len(found)} instrument resource(s); they are listed in the address drop-downs."
            if found else "No instruments detected. Check NI-VISA/pyvisa installation, cabling, and network."
        )

    def _selected_rise_fall_edges(self, mode: str) -> list[str]:
        return [
            edge for edge in RISE_FALL_EDGES
            if self.rise_fall_edge_vars[mode][edge].get()
        ]

    def _selected_rise_fall_modes(self) -> list[str]:
        return [mode for mode in RISE_FALL_MODES if self._selected_rise_fall_edges(mode)]

    def _selected_rise_fall_runs(self) -> list[tuple[str, str]]:
        return [
            (mode, edge)
            for mode in RISE_FALL_MODES
            for edge in RISE_FALL_EDGES
            if self.rise_fall_edge_vars[mode][edge].get()
        ]

    def _update_rise_fall_menu_button(self):
        if self.rise_fall_menu_button is None:
            return
        if self.selected_vars[RISE_FALL_TEST_KEY].get():
            selected = [f"{mode}-{edge}" for mode, edge in self._selected_rise_fall_runs()]
            label = "Test Types: " + (", ".join(selected) if selected else "None")
            self.rise_fall_menu_button.configure(text=label)
            if not self.rise_fall_menu_button.winfo_ismapped():
                self.rise_fall_menu_button.pack(side="left", padx=(10, 0))
        elif self.rise_fall_menu_button.winfo_ismapped():
            self.rise_fall_menu_button.pack_forget()

    def _on_test_selection_changed(self, key: str):
        if key == RISE_FALL_TEST_KEY:
            self._update_rise_fall_menu_button()
        self._sync_order()

    def _on_rise_fall_modes_changed(self):
        self._update_rise_fall_menu_button()

    @staticmethod
    def _sequence_parts(sequence_key: str) -> tuple[str, str | None, str | None]:
        parts = sequence_key.split(SEQUENCE_MODE_SEPARATOR)
        key = parts[0]
        mode = parts[1] if len(parts) > 1 else None
        edge = parts[2] if len(parts) > 2 else None
        return key, mode, edge

    @staticmethod
    def _sequence_base_key(sequence_key: str) -> str:
        return UnifiedCollectionApp._sequence_parts(sequence_key)[0]

    def _expanded_sequence(self) -> list[str]:
        expanded = []
        for key in self.order_keys:
            if key == RISE_FALL_TEST_KEY:
                expanded.extend(
                    f"{key}{SEQUENCE_MODE_SEPARATOR}{mode}{SEQUENCE_MODE_SEPARATOR}{edge}"
                    for mode, edge in self._selected_rise_fall_runs()
                )
            else:
                expanded.append(key)
        return expanded

    def _set_all(self, value: bool):
        for variable in self.selected_vars.values():
            variable.set(value)
        self._update_rise_fall_menu_button()
        self._sync_order()

    def _sync_order(self):
        selected = {key for key, variable in self.selected_vars.items() if variable.get()}
        self.order_keys = [key for key in self.order_keys if key in selected]
        self.order_keys.extend(test.key for test in TESTS if test.key in selected and test.key not in self.order_keys)
        self._render_order()

    def _render_order(self):
        self.order_list.delete(0, "end")
        for key in self.order_keys:
            self.order_list.insert("end", TEST_BY_KEY[key].name)

    def _move_order(self, delta: int):
        selection = self.order_list.curselection()
        if not selection:
            return
        index = selection[0]
        target = index + delta
        if target < 0 or target >= len(self.order_keys):
            return
        self.order_keys[index], self.order_keys[target] = self.order_keys[target], self.order_keys[index]
        self._render_order()
        self.order_list.selection_set(target)

    def _browse_collection_parent_folder(self):
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Select default collection data parent folder",
            initialdir=self.collection_parent_folder_var.get().strip() or str(APP_DIR),
        )
        if selected:
            self.collection_parent_folder_var.set(selected)

    def _browse_script(self, key: str):
        selected = filedialog.askopenfilename(parent=self.root, title=f"Select {TEST_BY_KEY[key].name} collection script", filetypes=[("Python files", "*.py")])
        if selected:
            self.script_vars[key].set(selected)
            self._refresh_script_statuses()

    def _browse_rise_fall_script(self, mode: str):
        selected = filedialog.askopenfilename(
            parent=self.root,
            title=f"Select {mode} Rise/Fall collection script",
            filetypes=[("Python files", "*.py")],
        )
        if selected:
            self.rise_fall_script_vars[mode].set(selected)
            self._refresh_script_statuses()

    def _script_path_for_run(self, test_key: str, mode: str | None = None) -> Path:
        if test_key == RISE_FALL_TEST_KEY:
            if mode not in RISE_FALL_MODES:
                raise ValueError("A valid Rise/Fall mode is required to select its script path.")
            return Path(self.rise_fall_script_vars[mode].get().strip())
        return Path(self.script_vars[test_key].get().strip())

    def _refresh_script_statuses(self):
        for test in TESTS:
            if test.key == RISE_FALL_TEST_KEY:
                for mode in RISE_FALL_MODES:
                    valid = Path(self.rise_fall_script_vars[mode].get().strip()).is_file()
                    self.path_status_labels[f"{RISE_FALL_TEST_KEY}:{mode}"].configure(
                        text="Found" if valid else "Missing",
                        foreground="#187A28" if valid else "#B00020",
                    )
                continue
            valid = Path(self.script_vars[test.key].get().strip()).is_file()
            self.path_status_labels[test.key].configure(
                text="Found" if valid else "Missing",
                foreground="#187A28" if valid else "#B00020",
            )

    def _current_config(self) -> dict:
        return {
            "collection_parent_folder": self.collection_parent_folder_var.get().strip(),
            "scripts": {key: variable.get().strip() for key, variable in self.script_vars.items()},
            "rise_fall_scripts": {
                mode: variable.get().strip()
                for mode, variable in self.rise_fall_script_vars.items()
            },
            "selected_tests": [key for key, variable in self.selected_vars.items() if variable.get()],
            "test_order": list(self.order_keys),
            "show_setup_pages": self.show_setup_var.get(),
            "open_child_console": self.console_var.get(),
            "rise_fall_modes": self._selected_rise_fall_modes(),
            "rise_fall_mode_edges": {
                mode: self._selected_rise_fall_edges(mode)
                for mode in RISE_FALL_MODES
            },
            "setup_images": self.config.get("setup_images", default_config()["setup_images"]),
        }

    def save_current_config(self):
        self.config = self._current_config()
        save_config(self.config)
        self._save_bench_profile()
        self.status_var.set(
            f"Saved settings to {display_path(collection_gui_settings_path())} and bench profile '{self.bench_profile_var.get()}' to {display_path(bench_profiles_path())}."
        )
        self._refresh_script_statuses()

    def _set_test_status(self, key: str, text: str):
        self.status_by_key[key] = text
        self.status_tree.set(key, "status", text)

    def start_sequence(self):
        self._sync_order()
        if not self.order_keys:
            messagebox.showerror("No tests selected", "Select at least one collection test.", parent=self.root)
            return
        if self.selected_vars[RISE_FALL_TEST_KEY].get() and not self._selected_rise_fall_runs():
            messagebox.showerror(
                "No Rise/Fall test type selected",
                "Select at least one Rise and Fall Time Verification mode-edge combination, such as CW RISE or CW FALL.",
                parent=self.root,
            )
            return
        missing = []
        for key in self.order_keys:
            if key == RISE_FALL_TEST_KEY:
                for mode in self._selected_rise_fall_modes():
                    if not self._script_path_for_run(key, mode).is_file():
                        missing.append(f"Rise and Fall Time Verification - {mode}")
            elif not self._script_path_for_run(key).is_file():
                missing.append(TEST_BY_KEY[key].name)
        if missing:
            messagebox.showerror("Missing collection scripts", "Configure valid script paths for:\n\n" + "\n".join(missing), parent=self.root)
            return
        collection_parent_folder = self.collection_parent_folder_var.get().strip()
        if not collection_parent_folder:
            messagebox.showerror(
                "Collection folder not specified",
                "Select a default collection data parent folder before starting the sequence.",
                parent=self.root,
            )
            return
        if not Path(collection_parent_folder).is_dir():
            messagebox.showerror(
                "Invalid collection folder",
                "The default collection data parent folder does not exist or is not a folder:\n\n"
                + collection_parent_folder,
                parent=self.root,
            )
            return
        self.save_current_config()
        self.sequence = self._expanded_sequence()
        self.sequence_index = -1
        self.stopping = False
        for test in TESTS:
            self._set_test_status(
                test.key,
                "Queued" if any(self._sequence_base_key(item) == test.key for item in self.sequence) else "Not selected",
            )
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._advance_sequence()

    def _advance_sequence(self):
        if self.stopping:
            self._finish_sequence("Sequence stopped after the current test.")
            return
        self.sequence_index += 1
        if self.sequence_index >= len(self.sequence):
            self._finish_sequence("All selected collection tests are complete.")
            return
        sequence_key = self.sequence[self.sequence_index]
        key, mode, edge = self._sequence_parts(sequence_key)
        self.active_key = key
        self.active_mode = mode
        self.active_edge = edge
        test = TEST_BY_KEY[key]
        qualifiers = " - ".join(value for value in (mode, edge) if value)
        display_name = f"{test.name} - {qualifiers}" if qualifiers else test.name
        self.progress_var.set(f"Test {self.sequence_index + 1} of {len(self.sequence)}: {display_name}")
        wait_label = " / ".join(value for value in (mode, edge) if value)
        self._set_test_status(key, f"Waiting for setup confirmation ({wait_label})" if wait_label else "Waiting for setup confirmation")
        if self.show_setup_var.get():
            self._show_setup_dialog(test)
        else:
            self._launch_test(test)

    def _show_setup_dialog(self, test: TestDefinition):
        dialog = tk.Toplevel(self.root)
        qualifiers = " - ".join(value for value in (self.active_mode, self.active_edge) if value)
        display_name = f"{test.name} - {qualifiers}" if qualifiers else test.name
        dialog.title(f"Setup: {display_name}")
        dialog.geometry("760x620")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"{display_name} Bench Setup", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        image_path = resolve_portable_path(self.config.get("setup_images", {}).get(test.key, ""))
        image_holder = ttk.LabelFrame(frame, text="Setup Image", padding=10)
        image_holder.pack(fill="both", expand=True, pady=10)
        photo = None
        if image_path and Path(image_path).is_file():
            try:
                photo = tk.PhotoImage(file=image_path)
                max_w, max_h = 680, 300
                scale = max(1, int(max(photo.width() / max_w, photo.height() / max_h)))
                if scale > 1:
                    photo = photo.subsample(scale, scale)
                label = ttk.Label(image_holder, image=photo)
                label.image = photo
                label.pack(expand=True)
            except tk.TclError:
                ttk.Label(image_holder, text=f"Unable to display setup image:\n{image_path}", justify="center").pack(expand=True)
        else:
            ttk.Label(image_holder, text="No setup image has been configured for this test yet.\nThe launcher is ready to display one when an image path is added.", justify="center").pack(expand=True)
        instructions = test.instructions
        if self.active_mode or self.active_edge:
            instructions = (
                f"Selected Rise/Fall mode: {self.active_mode or 'N/A'}\n"
                f"Selected transition edge: {self.active_edge or 'N/A'}\n\n"
                f"{instructions}"
            )
        ttk.Label(frame, text=instructions, wraplength=700, justify="left").pack(anchor="w", pady=(0, 10))
        equipment_text = "\n".join(
            f"• {EQUIPMENT_LABELS[equipment]}: {self.address_vars[equipment].get().strip() or '[not entered]'}"
            for equipment in test.equipment
        )
        ttk.LabelFrame(frame, text=f"Bench profile: {self.bench_profile_var.get()}", padding=10).pack(fill="x")
        address_frame = frame.winfo_children()[-1]
        ttk.Label(address_frame, text=equipment_text, justify="left").pack(anchor="w")
        ttk.Label(
            address_frame,
            text="Default collection parent folder: "
            + (self.collection_parent_folder_var.get().strip() or "[not entered]"),
            justify="left",
        ).pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))
        def continue_test():
            dialog.destroy()
            self._launch_test(test)
        def skip_test():
            dialog.destroy()
            self._set_test_status(test.key, "Skipped by user")
            self.root.after(50, self._advance_sequence)
        def stop_sequence():
            dialog.destroy()
            self.stopping = True
            self._set_test_status(test.key, "Not run")
            self._finish_sequence("Sequence stopped before launching the next test.")
        ttk.Button(buttons, text="Stop Sequence", command=stop_sequence).pack(side="left")
        ttk.Button(buttons, text="Skip Test", command=skip_test).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Continue to Test", command=continue_test).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", stop_sequence)

    def _launch_test(self, test: TestDefinition):
        script_path = self._script_path_for_run(test.key, self.active_mode).resolve()
        try:
            environment = build_child_environment(
                self._current_addresses(),
                test,
                self.collection_parent_folder_var.get().strip(),
                self.active_mode,
                self.active_edge,
                self.bench_profile_var.get(),
            )
            self.runner.launch_test(
                registry=REGISTRY,
                test_key=test.key,
                script=script_path,
                environment=environment,
                collection_parent=self.collection_parent_folder_var.get().strip(),
                bench_profile=self.bench_profile_var.get(),
                addresses=self._current_addresses(),
                new_console=self.console_var.get(),
            )
            run_label = " / ".join(
                value for value in (self.active_mode, self.active_edge) if value
            )
            self._set_test_status(
                test.key,
                f"Running {run_label} (PID {self.runner.pid})" if run_label
                else f"Running (PID {self.runner.pid})",
            )
            qualifiers = " - ".join(value for value in (self.active_mode, self.active_edge) if value)
            display_name = f"{test.name} - {qualifiers}" if qualifiers else test.name
            self.status_var.set(f"Launched {display_name} with bench profile '{self.bench_profile_var.get()}'. Waiting for the child GUI to close.")
            self.root.after(POLL_INTERVAL_MS, self._poll_process)
        except Exception as error:
            self._handle_test_failure(test, f"Launch failed: {error}")

    def _poll_process(self):
        if self.active_key is None:
            return
        result = self.runner.poll()
        if result is None:
            if self.runner.busy():
                self.root.after(POLL_INTERVAL_MS, self._poll_process)
            return
        if result.manifest_error:
            print(f"Warning: run manifest could not be written:\n{result.manifest_error}")
        return_code = result.exit_code
        test = TEST_BY_KEY[self.active_key]
        if return_code == 0:
            completed_label = " / ".join(
                value for value in (self.active_mode, self.active_edge) if value
            )
            completed_text = f"Completed {completed_label}" if completed_label else "Completed successfully"
            qualifiers = " - ".join(value for value in (self.active_mode, self.active_edge) if value)
            display_name = f"{test.name} - {qualifiers}" if qualifiers else test.name
            self._set_test_status(test.key, completed_text)
            self.status_var.set(f"{display_name} finished successfully. Preparing the next test.")
            self.root.after(100, self._advance_sequence)
        else:
            self._handle_test_failure(test, f"Exited with code {return_code}")

    def _handle_test_failure(self, test: TestDefinition, detail: str):
        self._set_test_status(test.key, detail)
        dialog = tk.Toplevel(self.root)
        qualifiers = " - ".join(value for value in (self.active_mode, self.active_edge) if value)
        display_name = f"{test.name} - {qualifiers}" if qualifiers else test.name
        dialog.title(f"{display_name} did not complete")
        dialog.geometry("560x260")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"{display_name} did not complete successfully.", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text=detail + "\n\nReview the child console for details, then choose how to continue.", wraplength=510).pack(anchor="w", pady=12)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", side="bottom")
        def retry():
            dialog.destroy()
            self._launch_test(test)
        def skip():
            dialog.destroy()
            self._set_test_status(test.key, "Skipped after error")
            self._advance_sequence()
        def stop():
            dialog.destroy()
            self._finish_sequence("Sequence stopped after a test error.")
        ttk.Button(buttons, text="Stop Sequence", command=stop).pack(side="left")
        ttk.Button(buttons, text="Skip Test", command=skip).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Retry Test", command=retry).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", stop)

    def stop_after_current(self):
        self.stopping = True
        self.status_var.set("The sequence will stop after the currently running test closes.")
        self.stop_button.configure(state="disabled")

    def _finish_sequence(self, message: str):
        self.active_key = None
        self.active_mode = None
        self.active_edge = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.progress_var.set(message)
        self.status_var.set(message)
        messagebox.showinfo("Collection sequence", message, parent=self.root)

    def on_close(self):
        if self.runner.busy():
            if not messagebox.askyesno("Collection test is running", "A child collection script is still running. Close only the launcher and leave the test running?", parent=self.root):
                return
        self.save_current_config()
        self.root.destroy()


def _warn_unavailable_plugins(root: tk.Tk) -> None:
    problems = describe_unavailable(REGISTRY)
    if problems:
        print("\n".join(problems))
        messagebox.showwarning("Plugins unavailable", "These plugins were skipped:\n\n" + "\n\n".join(problems), parent=root)


def self_test() -> int:
    config = default_config()
    addresses = {**empty_addresses(), "generator": "GEN01", "oscilloscope": "10.0.0.5"}
    env = build_child_environment(
        addresses, TEST_BY_KEY["power_profile"], config["collection_parent_folder"], bench_profile="Bench A"
    )
    assert env["PARAMOUNT_GENERATOR_ADDRESS"] == "GEN01"
    assert env["PARAMOUNT_OSCILLOSCOPE_ADDRESS"] == "10.0.0.5"
    assert env[BENCH_PROFILE_ENV] == "Bench A"
    assert env[COLLECTION_PARENT_FOLDER_ENV] == config["collection_parent_folder"]
    assert _com_port_from_visa("ASRL5::INSTR") == "COM5"
    assert len(TESTS) == 14
    analog_keys = ["setpoint_transfer_function", "analog_processing_timing", "rf_enable_timing",
                   "interlock_response_timing", "cex_lock_acquisition"]
    assert migrate_selection(["power_accuracy", "analog_port_timing", "psync"]) == [
        "power_accuracy", *analog_keys, "psync"
    ]
    assert RISE_FALL_MODES == ("CW", "SLP", "DLP")
    expected_equipment = {
        "setpoint_transfer_function": {"generator", "waveform_generator"},
        "analog_processing_timing": {"generator", "oscilloscope", "waveform_generator"},
        "rf_enable_timing": {"generator", "oscilloscope", "waveform_generator"},
        "interlock_response_timing": {"generator", "oscilloscope", "waveform_generator", "dc_supply"},
        "cex_lock_acquisition": {"generator", "oscilloscope", "waveform_generator"},
        "rf_tuning": {"generator", "navigator"},
    }
    for key, equipment in expected_equipment.items():
        assert set(TEST_BY_KEY[key].equipment) == equipment
    missing = [
        f"{test.key}: {config['scripts'][test.key]}"
        for test in TESTS if not Path(config["scripts"][test.key]).is_file()
    ]
    missing += [
        f"rise_fall {mode}: {path}"
        for mode, path in config["rise_fall_scripts"].items() if not Path(path).is_file()
    ]
    if missing:
        print("Scripts not discovered:\n  " + "\n  ".join(missing))
    for line in describe_unavailable(REGISTRY):
        print(f"Plugin {line}")
    print("Unified collection launcher self-test passed.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    migrate_legacy_settings()
    root = tk.Tk()
    _warn_unavailable_plugins(root)
    UnifiedCollectionApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
