"""Plugin registry: every shipped plugin is valid, and bad plugins are reported, never fatal."""
from __future__ import annotations

import json

import pytest

from suite_common.bench import FIELD_BY_KEY
from suite_sdk.context import SUITE_CONTEXT_ENV, SuiteContext
from suite_sdk.registry import KINDS, load_registry, parse_plugin

VALID = {
    "schema_version": 1,
    "id": "demo",
    "name": "Demo",
    "kind": "collection",
    "version": "1.0.0",
    "tests": [{"key": "demo_test", "name": "Demo Test", "entry": "demo.py", "order": 10, "requires": ["generator"]}],
}


def _write_plugin(folder, data, filename="plugin.json", script="demo.py"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / script).write_text("print('demo')\n", encoding="utf-8")
    (folder / filename).write_text(json.dumps(data), encoding="utf-8")
    return folder / filename


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def test_every_shipped_plugin_is_valid(registry):
    assert registry.unavailable == []
    assert {p.kind for p in registry.plugins} == set(KINDS)


def test_listed_tests_only_require_known_bench_equipment(registry):
    unknown = {(t.key, role) for t in registry.tests("collection") for role in t.equipment if role not in FIELD_BY_KEY}
    assert not unknown


def test_every_plugin_pairing_resolves(registry):
    ids = {(p.kind, p.id) for p in registry.plugins}
    other = {"collection": "analysis", "analysis": "collection"}
    broken = [(p.kind, p.id) for p in registry.plugins if p.paired_with and (other[p.kind], p.paired_with) not in ids]
    assert not broken


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.pop("version"), "missing required field 'version'"),
    (lambda d: d.update(colour="blue"), "unknown field 'colour'"),
    (lambda d: d["tests"][0].update(key="Bad Key"), "does not match"),
    (lambda d: d.update(kind="report"), "must be one of"),
    (lambda d: d["tests"][0].update(requires=[{"capabilities": []}]), "does not match any allowed form"),
    (lambda d: d["tests"][0].update(entry="missing.py"), "entry not found"),
])
def test_invalid_plugin_is_listed_unavailable(tmp_path, mutate, message):
    data = json.loads(json.dumps(VALID))
    mutate(data)
    _write_plugin(tmp_path / "demo", data)
    registry = load_registry(tmp_path)
    assert registry.plugins == []
    assert message in registry.unavailable[0][1]


def test_duplicate_test_key_keeps_first_plugin(tmp_path):
    _write_plugin(tmp_path / "a", VALID)
    _write_plugin(tmp_path / "b", {**VALID, "id": "other"})
    registry = load_registry(tmp_path)
    assert [p.id for p in registry.plugins] == ["demo"]
    assert "already used" in registry.unavailable[0][1]


def test_filename_kind_must_match(tmp_path):
    path = _write_plugin(tmp_path / "demo", VALID, filename="plugin.analysis.json")
    with pytest.raises(ValueError, match="file name says kind 'analysis'"):
        parse_plugin(path)


def test_product_and_firmware_filters(tmp_path):
    _write_plugin(tmp_path / "demo", {**VALID, "supported_products": ["APEX"], "supported_firmware": ["7433660J00"]})
    registry = load_registry(tmp_path)
    assert registry.tests("collection")  # no filter -> everything
    assert registry.tests("collection", product="apex", firmware="7433660J00")
    assert not registry.tests("collection", product="PARAMOUNT")
    assert not registry.tests("collection", firmware="OTHER")


def test_unlisted_tests_are_hidden_from_gui_lists(registry):
    hidden = [t.key for t in registry.tests("collection", include_unlisted=True) if not t.listed]
    assert "rf_onoff_scope_capture" in hidden
    assert not set(hidden) & {t.key for t in registry.tests("collection")}


def test_suite_context_round_trip(tmp_path, monkeypatch):
    context = SuiteContext(product_id="PARAMOUNT", firmware="7433660J00", bench_profile="Bench A")
    path = context.save(tmp_path / "suite_context.json")
    assert SuiteContext.load(path) == context
    monkeypatch.setenv(SUITE_CONTEXT_ENV, str(path))
    assert SuiteContext.from_environment().firmware == "7433660J00"
    assert SuiteContext.from_dict({**context.to_dict(), "unknown_future_field": 1}) == context
