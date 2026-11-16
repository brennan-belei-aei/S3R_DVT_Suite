"""Plugin registry: finds plugin files, validates them, and answers "which tests exist?".

A plugin file is `plugin.json` (Phase 4 layout: one per plugin folder) or
`plugin.<kind>.json` (a folder that holds both the collection and analysis scripts).
Invalid plugins never crash the GUIs: they are listed in `PluginRegistry.unavailable`
with the reason.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from suite_common import REPO_ROOT
from suite_sdk.schema import validate

KINDS = ("collection", "analysis")
SCHEMA_PATH = Path(__file__).with_name("plugin.schema.json")
PLUGIN_FILENAMES = {"plugin.json", *(f"plugin.{kind}.json" for kind in KINDS)}
EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "_archive", "archive",
    "runtime", "tests", "_backup_local_settings",
}
ALL = "*"


@dataclass(frozen=True)
class PluginTest:
    """One selectable entry in a GUI."""
    key: str
    name: str
    kind: str
    plugin_id: str
    script: Path
    order: int
    listed: bool = True
    description: str = ""
    instructions: str = ""
    image: str = ""
    equipment: tuple[str, ...] = ()
    capabilities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    modes: dict[str, Path] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plugin:
    id: str
    name: str
    kind: str
    version: str
    path: Path
    tests: tuple[PluginTest, ...]
    paired_with: str = ""
    supported_products: tuple[str, ...] = (ALL,)
    supported_firmware: tuple[str, ...] = (ALL,)

    @property
    def directory(self) -> Path:
        return self.path.parent

    def supports(self, product: str | None = None, firmware: str | None = None) -> bool:
        return _matches(self.supported_products, product) and _matches(self.supported_firmware, firmware)


@dataclass
class PluginRegistry:
    plugins: list[Plugin] = field(default_factory=list)
    unavailable: list[tuple[Path, str]] = field(default_factory=list)

    def tests(self, kind: str, product: str | None = None, firmware: str | None = None,
              include_unlisted: bool = False) -> list[PluginTest]:
        found = [
            test
            for plugin in self.plugins if plugin.kind == kind and plugin.supports(product, firmware)
            for test in plugin.tests if include_unlisted or test.listed
        ]
        return sorted(found, key=lambda t: (t.order, t.key))

    def test(self, kind: str, key: str) -> PluginTest:
        for test in self.tests(kind, include_unlisted=True):
            if test.key == key:
                return test
        raise KeyError(f"No {kind} test with key {key!r}")

    def plugin(self, kind: str, plugin_id: str) -> Plugin:
        for plugin in self.plugins:
            if plugin.kind == kind and plugin.id == plugin_id:
                return plugin
        raise KeyError(f"No {kind} plugin {plugin_id!r}")

    def resolve_aliases(self, kind: str, values: list[str]) -> list[str]:
        """Replace legacy keys from old saved settings with current keys, keeping order and dropping repeats."""
        tests = self.tests(kind, include_unlisted=True)
        current = {t.key for t in tests}
        resolved: list[str] = []
        for value in values:
            replacements = [value] if value in current else [t.key for t in tests if value in t.aliases] or [value]
            resolved += [key for key in replacements if key not in resolved]
        return resolved


def _matches(supported: tuple[str, ...], value: str | None) -> bool:
    if value is None or ALL in supported:
        return True
    return value.strip().upper() in {item.strip().upper() for item in supported}


def discover_plugin_files(root: Path = REPO_ROOT) -> list[Path]:
    found = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith("."))
        found += [Path(directory) / name for name in sorted(filenames) if name in PLUGIN_FILENAMES]
    return found


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def parse_plugin(path: Path, schema: dict | None = None) -> Plugin:
    """Load and validate one plugin file. Raises ValueError with every problem found."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"unreadable JSON: {error}") from error
    errors = validate(data, schema or _schema())
    if errors:
        raise ValueError("; ".join(errors))
    expected_kind = path.name.split(".")[1] if path.name.count(".") == 2 else data["kind"]
    if data["kind"] != expected_kind:
        raise ValueError(f"file name says kind '{expected_kind}' but the file says '{data['kind']}'")
    tests, problems, keys = [], [], set()
    for raw in data["tests"]:
        script = (path.parent / raw["entry"]).resolve()
        modes = {mode: (path.parent / rel).resolve() for mode, rel in raw.get("modes", {}).items()}
        for label, target in [("entry", script), *((f"mode {m}", p) for m, p in modes.items())]:
            if not target.is_file():
                problems.append(f"test '{raw['key']}' {label} not found: {target.relative_to(path.parent) if target.is_relative_to(path.parent) else target}")
        if raw["key"] in keys:
            problems.append(f"duplicate test key '{raw['key']}'")
        keys.add(raw["key"])
        requires = [r if isinstance(r, dict) else {"role": r} for r in raw.get("requires", [])]
        tests.append(PluginTest(
            key=raw["key"],
            name=raw["name"],
            kind=data["kind"],
            plugin_id=data["id"],
            script=script,
            order=raw["order"],
            listed=raw.get("listed", True),
            description=raw.get("description", ""),
            instructions=raw.get("instructions", ""),
            image=raw.get("image", ""),
            equipment=tuple(r["role"] for r in requires),
            capabilities={r["role"]: tuple(r.get("capabilities", ())) for r in requires},
            modes=modes,
            aliases=tuple(raw.get("aliases", ())),
        ))
    if problems:
        raise ValueError("; ".join(problems))
    return Plugin(
        id=data["id"],
        name=data["name"],
        kind=data["kind"],
        version=data["version"],
        path=path,
        tests=tuple(tests),
        paired_with=data.get("paired_with", ""),
        supported_products=tuple(data.get("supported_products", [ALL])),
        supported_firmware=tuple(data.get("supported_firmware", [ALL])),
    )


def load_registry(root: Path = REPO_ROOT) -> PluginRegistry:
    registry = PluginRegistry()
    schema = _schema()
    seen_ids: set[tuple[str, str]] = set()
    seen_keys: dict[tuple[str, str], str] = {}
    for path in discover_plugin_files(root):
        try:
            plugin = parse_plugin(path, schema)
            if (plugin.kind, plugin.id) in seen_ids:
                raise ValueError(f"another {plugin.kind} plugin already uses id '{plugin.id}'")
            clashes = [t.key for t in plugin.tests if (plugin.kind, t.key) in seen_keys]
            if clashes:
                raise ValueError(f"test key(s) {clashes} already used by plugin '{seen_keys[(plugin.kind, clashes[0])]}'")
        except ValueError as error:
            registry.unavailable.append((path, str(error)))
            continue
        seen_ids.add((plugin.kind, plugin.id))
        seen_keys.update({(plugin.kind, t.key): plugin.id for t in plugin.tests})
        registry.plugins.append(plugin)
    return registry


def describe_unavailable(registry: PluginRegistry, root: Path = REPO_ROOT) -> list[str]:
    lines = []
    for path, reason in registry.unavailable:
        shown = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        lines.append(f"{shown}: unavailable ({reason})")
    return lines
