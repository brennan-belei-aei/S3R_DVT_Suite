# suite_sdk

The plugin layer. Each GUI asks the registry which tests exist; nothing is hardcoded in the GUIs.

| Module | Use it for |
|---|---|
| `plugin.schema.json` | The `plugin.json` contract. Plugin files reference it in `$schema`, so editors validate and autocomplete them. |
| `registry` | `load_registry()` finds every `plugin.json` / `plugin.<kind>.json` (skipping `.venv`, `archive/`, `runtime/`, `tests/`), validates it, and returns a `PluginRegistry`. Use `tests(kind, product=, firmware=, include_unlisted=)`, `test(kind, key)`, and `resolve_aliases(kind, saved_keys)`. Invalid files land in `registry.unavailable` with the reason; `describe_unavailable()` formats them. |
| `schema` | A small JSON Schema validator for the keywords `plugin.schema.json` uses (no third-party dependency). |
| `context` | `SuiteContext`: the product, firmware, DUT adapter, and bench the launcher selected. It is saved to `runtime/suite_context.json` and its path is passed in `PARAMOUNT_SUITE_CONTEXT`. |

## Plugin file at a glance

```json
{
  "$schema": "../suite_sdk/plugin.schema.json",
  "schema_version": 1,
  "id": "power_profile",
  "name": "Power Profile",
  "kind": "collection",
  "version": "1.0.0",
  "paired_with": "power_profile",
  "tests": [
    {
      "key": "power_profile",
      "name": "Power Profile",
      "entry": "Power_Profile_Collection_GUI.py",
      "order": 120,
      "requires": ["generator", "lynx", "navigator"],
      "instructions": "Configure the Power Profile bench..."
    }
  ]
}
```

- **`key`:** must be unique per kind. Saved GUI settings refer to it, so do not rename a key. Instead, add the old key to the new entry's `aliases` so existing settings carry over.
- **`listed: false`:** keeps a test out of the GUI lists while it is still under development.
- **Optional fields:** `supported_products` and `supported_firmware` default to all.
