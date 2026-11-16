"""Top-level launcher for the Paramount data collection and analysis suites.

This wrapper launches each unified GUI as an independent Python process. It does
not import, modify, or terminate either child suite.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from suite_common.paths import (
    child_creation_flags as creation_flags,
    discover_script,
    load_json_settings,
    open_path,
    resolve_script_setting,
    save_json_settings,
    to_portable_path,
)

APP_TITLE = "AE Transparency Testing Suite"
APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "AE_Transparency_Testing_Suite_Settings.json"
PRODUCT_PROFILES_PATH = APP_DIR / "AE_Product_Profiles.json"
POLL_INTERVAL_MS = 500
DEFAULT_PRODUCT_ID = "UNSPECIFIED"

COLLECTION_CANDIDATES = ["Paramount_Data_Collection_Unified_GUI.py"]
ANALYSIS_CANDIDATES = ["Paramount_Data_Analysis_Unified_GUI.py"]


def default_product_profiles() -> dict:
    """Return the versioned product-profile schema used by this launcher.

    The recommendation dictionaries are intentionally empty today. Future unified
    collection and analysis GUIs can consume them without changing this launcher
    or the individual test scripts.
    """
    return {
        "schema_version": 1,
        "products": [
            {
                "id": "UNSPECIFIED",
                "display_name": "Other / Unspecified",
                "description": "No product-specific recommendations are applied.",
                "collection_recommendations": {},
                "analysis_recommendations": {},
                "metadata": {},
            },
            {
                "id": "PARAMOUNT",
                "display_name": "Paramount",
                "description": "Paramount product-line profile. Recommended settings can be added later.",
                "collection_recommendations": {},
                "analysis_recommendations": {},
                "metadata": {},
            },
        ],
    }


def ensure_product_profiles_file() -> None:
    if not PRODUCT_PROFILES_PATH.exists():
        PRODUCT_PROFILES_PATH.write_text(
            json.dumps(default_product_profiles(), indent=2), encoding="utf-8"
        )


def load_product_profiles() -> dict:
    ensure_product_profiles_file()
    try:
        data = json.loads(PRODUCT_PROFILES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("products"), list):
            raise ValueError("Product profile file must contain a products list.")
        valid_products = []
        seen_ids = set()
        for raw in data["products"]:
            if not isinstance(raw, dict):
                continue
            product_id = str(raw.get("id", "")).strip().upper()
            display_name = str(raw.get("display_name", "")).strip()
            if not product_id or not display_name or product_id in seen_ids:
                continue
            valid_products.append({
                "id": product_id,
                "display_name": display_name,
                "description": str(raw.get("description", "")).strip(),
                "collection_recommendations": raw.get("collection_recommendations", {})
                    if isinstance(raw.get("collection_recommendations", {}), dict) else {},
                "analysis_recommendations": raw.get("analysis_recommendations", {})
                    if isinstance(raw.get("analysis_recommendations", {}), dict) else {},
                "metadata": raw.get("metadata", {})
                    if isinstance(raw.get("metadata", {}), dict) else {},
            })
            seen_ids.add(product_id)
        if not valid_products:
            raise ValueError("No valid product profiles were found.")
        return {"schema_version": int(data.get("schema_version", 1)), "products": valid_products}
    except Exception as error:
        print(f"Warning: using built-in product profiles; {PRODUCT_PROFILES_PATH.name} is invalid: {error}")
        return default_product_profiles()


def product_by_id(catalog: dict, product_id: str) -> dict:
    normalized = str(product_id).strip().upper()
    for product in catalog.get("products", []):
        if product.get("id") == normalized:
            return product
    return catalog["products"][0]


def default_settings() -> dict:
    return {
        "collection_script": discover_script(COLLECTION_CANDIDATES),
        "analysis_script": discover_script(ANALYSIS_CANDIDATES),
        "open_child_console": True,
        "warn_before_parallel_launch": True,
        "selected_product_id": DEFAULT_PRODUCT_ID,
    }


def load_settings() -> dict:
    settings = default_settings()
    saved = load_json_settings(SETTINGS_PATH)
    for key in settings:
        if key in saved:
            settings[key] = saved[key]
    settings["collection_script"] = resolve_script_setting(settings["collection_script"], COLLECTION_CANDIDATES)
    settings["analysis_script"] = resolve_script_setting(settings["analysis_script"], ANALYSIS_CANDIDATES)
    return settings


def save_settings(settings: dict) -> None:
    portable = dict(settings)
    for key in ("collection_script", "analysis_script"):
        portable[key] = to_portable_path(settings[key])
    save_json_settings(SETTINGS_PATH, portable)


class AETransparencyTestingSuite:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = load_settings()
        self.product_catalog = load_product_profiles()
        saved_product_id = str(self.settings.get("selected_product_id", DEFAULT_PRODUCT_ID)).upper()
        selected_product = product_by_id(self.product_catalog, saved_product_id)
        self.product_id_by_display = {
            item["display_name"]: item["id"] for item in self.product_catalog["products"]
        }
        self.product_display_by_id = {
            item["id"]: item["display_name"] for item in self.product_catalog["products"]
        }
        self.product_var = tk.StringVar(value=selected_product["display_name"])
        self.product_description_var = tk.StringVar(value=selected_product.get("description", ""))
        self.collection_path = tk.StringVar(value=str(self.settings["collection_script"]))
        self.analysis_path = tk.StringVar(value=str(self.settings["analysis_script"]))
        self.console_var = tk.BooleanVar(value=bool(self.settings["open_child_console"]))
        self.parallel_warning_var = tk.BooleanVar(value=bool(self.settings["warn_before_parallel_launch"]))
        self.status_var = tk.StringVar(value="Ready")
        self.processes: dict[str, subprocess.Popen | None] = {
            "collection": None,
            "analysis": None,
        }
        self.status_labels: dict[str, ttk.Label] = {}
        self.path_status_labels: dict[str, ttk.Label] = {}
        self.launch_buttons: dict[str, ttk.Button] = {}
        self._build_gui()
        self._refresh_path_statuses()
        self._poll_processes()

    def _build_gui(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("900x735")
        self.root.minsize(800, 650)

        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("CardTitle.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("Launch.TButton", font=("Segoe UI", 11, "bold"), padding=(14, 10))

        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Open the unified data collection or data analysis suite. Each suite runs "
                "independently and retains its own scripts, settings, progress, and outputs."
            ),
            wraplength=790,
        ).pack(anchor="w", pady=(3, 18))

        product_frame = ttk.LabelFrame(outer, text="Product line", padding=10)
        product_frame.pack(fill="x", pady=(0, 16))
        product_frame.columnconfigure(1, weight=1)
        ttk.Label(product_frame, text="Select product").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.product_combo = ttk.Combobox(
            product_frame,
            textvariable=self.product_var,
            values=[item["display_name"] for item in self.product_catalog["products"]],
            state="readonly",
            width=34,
        )
        self.product_combo.grid(row=0, column=1, sticky="w")
        self.product_combo.bind("<<ComboboxSelected>>", self._on_product_selected)
        ttk.Button(product_frame, text="Reload Product List", command=self._reload_product_profiles).grid(
            row=0, column=2, padx=(10, 0)
        )
        ttk.Button(product_frame, text="Open Profiles Folder", command=self._open_profiles_folder).grid(
            row=0, column=3, padx=(8, 0)
        )
        ttk.Label(
            product_frame,
            textvariable=self.product_description_var,
            foreground="#555555",
            wraplength=750,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(
            product_frame,
            text=("Product selection is recorded and passed to child suites. Recommended settings are "
                  "reserved in the profile schema but are not applied yet."),
            foreground="#555555",
            wraplength=750,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        cards = ttk.Frame(outer)
        cards.pack(fill="both", expand=True)
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        cards.rowconfigure(0, weight=1)

        self._build_card(
            cards,
            column=0,
            key="collection",
            title="Data Collection",
            description=(
                "Configure bench equipment, select collection tests, choose the default data "
                "folder, and run the individual collection GUIs sequentially."
            ),
            path_variable=self.collection_path,
            button_text="Launch Data Collection",
        )
        self._build_card(
            cards,
            column=1,
            key="analysis",
            title="Data Analysis",
            description=(
                "Select analysis tools, configure Dataset A/B defaults, process collected data, "
                "and optionally build a consolidated PowerPoint presentation."
            ),
            path_variable=self.analysis_path,
            button_text="Launch Data Analysis",
        )

        options = ttk.LabelFrame(outer, text="Launcher options", padding=10)
        options.pack(fill="x", pady=(16, 0))
        ttk.Checkbutton(
            options,
            text="Open child suites in separate console windows on Windows",
            variable=self.console_var,
        ).pack(anchor="w")
        ttk.Checkbutton(
            options,
            text="Warn before running Collection and Analysis at the same time",
            variable=self.parallel_warning_var,
        ).pack(anchor="w", pady=(4, 0))

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(14, 0))
        ttk.Label(footer, textvariable=self.status_var, foreground="#555555").pack(side="left")
        ttk.Button(footer, text="Save Settings", command=self.save_current_settings).pack(side="right")
        ttk.Button(footer, text="Refresh Paths", command=self._refresh_path_statuses).pack(side="right", padx=8)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_card(
        self,
        parent: ttk.Frame,
        column: int,
        key: str,
        title: str,
        description: str,
        path_variable: tk.StringVar,
        button_text: str,
    ) -> None:
        card = ttk.LabelFrame(parent, padding=16)
        card.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column == 0 else (8, 0))
        card.columnconfigure(0, weight=1)

        ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(card, text=description, wraplength=340).grid(
            row=1, column=0, columnspan=2, sticky="nw", pady=(8, 18)
        )

        path_frame = ttk.LabelFrame(card, text="Unified GUI script", padding=8)
        path_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        path_frame.columnconfigure(0, weight=1)
        ttk.Entry(path_frame, textvariable=path_variable).grid(row=0, column=0, sticky="ew")
        ttk.Button(path_frame, text="Browse...", command=lambda: self._browse_script(key)).grid(
            row=0, column=1, padx=(8, 0)
        )
        path_status = ttk.Label(path_frame, width=12)
        path_status.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.path_status_labels[key] = path_status

        runtime_status = ttk.Label(card, text="Not running", foreground="#555555")
        runtime_status.grid(row=3, column=0, columnspan=2, sticky="w", pady=(18, 8))
        self.status_labels[key] = runtime_status

        button = ttk.Button(
            card,
            text=button_text,
            style="Launch.TButton",
            command=lambda: self.launch_suite(key),
        )
        button.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.launch_buttons[key] = button

    def _path_variable(self, key: str) -> tk.StringVar:
        return self.collection_path if key == "collection" else self.analysis_path

    def _other_key(self, key: str) -> str:
        return "analysis" if key == "collection" else "collection"

    def _display_name(self, key: str) -> str:
        return "Data Collection" if key == "collection" else "Data Analysis"

    def _selected_product(self) -> dict:
        product_id = self.product_id_by_display.get(self.product_var.get(), DEFAULT_PRODUCT_ID)
        return product_by_id(self.product_catalog, product_id)

    def _on_product_selected(self, _event=None) -> None:
        product = self._selected_product()
        self.product_description_var.set(product.get("description", ""))
        self.status_var.set(f"Selected product line: {product['display_name']}.")

    def _reload_product_profiles(self) -> None:
        previous_id = self._selected_product().get("id", DEFAULT_PRODUCT_ID)
        self.product_catalog = load_product_profiles()
        self.product_id_by_display = {
            item["display_name"]: item["id"] for item in self.product_catalog["products"]
        }
        self.product_display_by_id = {
            item["id"]: item["display_name"] for item in self.product_catalog["products"]
        }
        self.product_combo.configure(values=list(self.product_id_by_display))
        selected = product_by_id(self.product_catalog, previous_id)
        self.product_var.set(selected["display_name"])
        self.product_description_var.set(selected.get("description", ""))
        self.status_var.set(f"Reloaded {PRODUCT_PROFILES_PATH.name}.")

    def _open_profiles_folder(self) -> None:
        ensure_product_profiles_file()
        try:
            open_path(APP_DIR)
        except Exception as error:
            messagebox.showerror("Could not open folder", str(error), parent=self.root)

    def _product_environment(self, key: str) -> dict[str, str]:
        product = self._selected_product()
        recommendation_key = (
            "collection_recommendations" if key == "collection" else "analysis_recommendations"
        )
        recommendations = product.get(recommendation_key, {})
        return {
            "PARAMOUNT_PRODUCT_ID": product["id"],
            "PARAMOUNT_PRODUCT_NAME": product["display_name"],
            "PARAMOUNT_PRODUCT_PROFILE_SCHEMA_VERSION": str(self.product_catalog.get("schema_version", 1)),
            "PARAMOUNT_PRODUCT_PROFILE_FILE": str(PRODUCT_PROFILES_PATH),
            "PARAMOUNT_PRODUCT_RECOMMENDATIONS_JSON": json.dumps(recommendations),
        }

    def _browse_script(self, key: str) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            title=f"Select the unified {self._display_name(key).lower()} GUI",
            initialdir=str(APP_DIR),
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if selected:
            self._path_variable(key).set(selected)
            self._refresh_path_statuses()

    def _valid_script(self, key: str) -> bool:
        path = Path(self._path_variable(key).get().strip())
        return path.is_file() and path.suffix.lower() == ".py"

    def _refresh_path_statuses(self) -> None:
        for key in ("collection", "analysis"):
            valid = self._valid_script(key)
            self.path_status_labels[key].configure(
                text="Found" if valid else "Missing",
                foreground="#187A28" if valid else "#B00020",
            )
            running = self._is_running(key)
            self.launch_buttons[key].configure(state="normal" if valid and not running else "disabled")
        self.status_var.set("Launcher paths refreshed.")

    def _is_running(self, key: str) -> bool:
        process = self.processes.get(key)
        return process is not None and process.poll() is None

    def launch_suite(self, key: str) -> None:
        if self._is_running(key):
            messagebox.showinfo(
                f"{self._display_name(key)} is running",
                f"The {self._display_name(key)} suite is already running.",
                parent=self.root,
            )
            return

        script = Path(self._path_variable(key).get().strip()).expanduser()
        if not script.is_file() or script.suffix.lower() != ".py":
            messagebox.showerror(
                "Launcher script not found",
                f"Select a valid Python file for {self._display_name(key)}.",
                parent=self.root,
            )
            self._refresh_path_statuses()
            return

        other = self._other_key(key)
        if self._is_running(other) and self.parallel_warning_var.get():
            proceed = messagebox.askyesno(
                "Another suite is already running",
                f"The {self._display_name(other)} suite is currently running.\n\n"
                f"You may also open {self._display_name(key)}, but do not analyze a folder "
                "that is still being written by an active collection test.\n\n"
                "Launch anyway?",
                parent=self.root,
            )
            if not proceed:
                return

        self.save_current_settings(show_status=False)
        environment = os.environ.copy()
        environment["PARAMOUNT_TESTING_SUITE_LAUNCHER"] = "1"
        environment["PARAMOUNT_LAUNCHED_SUITE"] = key.upper()
        environment.update(self._product_environment(key))

        try:
            process = subprocess.Popen(
                [sys.executable, str(script.resolve())],
                cwd=str(script.resolve().parent),
                env=environment,
                creationflags=creation_flags(self.console_var.get()),
            )
            self.processes[key] = process
            self.status_labels[key].configure(
                text=f"Running (PID {process.pid})",
                foreground="#187A28",
            )
            self.launch_buttons[key].configure(state="disabled")
            self.status_var.set(f"Launched {self._display_name(key)}.")
        except Exception as error:
            self.processes[key] = None
            self.status_labels[key].configure(text="Launch failed", foreground="#B00020")
            messagebox.showerror(
                "Launch failed",
                f"Could not launch {self._display_name(key)}:\n\n{error}",
                parent=self.root,
            )

    def _poll_processes(self) -> None:
        for key in ("collection", "analysis"):
            process = self.processes.get(key)
            if process is None:
                continue
            return_code = process.poll()
            if return_code is None:
                continue
            self.processes[key] = None
            if return_code == 0:
                self.status_labels[key].configure(text="Closed normally", foreground="#555555")
            else:
                self.status_labels[key].configure(
                    text=f"Closed with code {return_code}",
                    foreground="#B00020",
                )
            if self._valid_script(key):
                self.launch_buttons[key].configure(state="normal")
            self.status_var.set(f"{self._display_name(key)} closed with exit code {return_code}.")
        self.root.after(POLL_INTERVAL_MS, self._poll_processes)

    def current_settings(self) -> dict:
        return {
            "collection_script": self.collection_path.get().strip(),
            "analysis_script": self.analysis_path.get().strip(),
            "open_child_console": self.console_var.get(),
            "warn_before_parallel_launch": self.parallel_warning_var.get(),
            "selected_product_id": self._selected_product()["id"],
        }

    def save_current_settings(self, show_status: bool = True) -> None:
        save_settings(self.current_settings())
        if show_status:
            self.status_var.set(f"Settings saved to {SETTINGS_PATH.name}.")

    def on_close(self) -> None:
        running = [self._display_name(key) for key in ("collection", "analysis") if self._is_running(key)]
        if running:
            message = (
                "The following child suites are still running:\n\n"
                + "\n".join(running)
                + "\n\nClose only this wrapper and leave those suites running?"
            )
            if not messagebox.askyesno("Child suites are running", message, parent=self.root):
                return
        self.save_current_settings(show_status=False)
        self.root.destroy()


def self_test() -> int:
    settings = default_settings()
    assert "collection_script" in settings and "analysis_script" in settings
    assert creation_flags(False) == 0
    assert COLLECTION_CANDIDATES and ANALYSIS_CANDIDATES
    catalog = default_product_profiles()
    assert catalog["schema_version"] == 1
    assert product_by_id(catalog, "PARAMOUNT")["display_name"] == "Paramount"
    assert "collection_recommendations" in product_by_id(catalog, "PARAMOUNT")
    print("Transparency Testing Suite launcher self-test passed.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    root = tk.Tk()
    AETransparencyTestingSuite(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
