# suite_common

Shared helpers imported by the unified GUIs and every test script. Its only dependency outside the standard library is `pyvisa`, which is optional and used only for instrument detection.

| Module | Use it for |
|---|---|
| `bootstrap` | `import suite_common.bootstrap` adds `AESpecificDependencies/` to `sys.path`. Set `AE_SPECIFIC_DEPENDENCIES_DIR` to override the location. |
| `config` | Every config and settings path: `product_profiles_path()` (`config/`), plus `bench_profiles_path()`, `collection_gui_settings_path()`, `analysis_gui_settings_path()`, `launcher_settings_path()`, and `plugin_settings_path(plugin_id, kind)` (`runtime/`). Each runtime accessor adopts its pre-refactor file on first use; `migrate_legacy_settings()` adopts all of them. |
| `bench` | `bench_default(key)` returns an address from the env var, then the active bench profile, then blank. `EQUIPMENT_FIELDS` is the single list of equipment keys and env var names. Also handles profile load/save, `visa_resource`, `visa_socket_resource`, and `detect_instruments`. |
| `paths` | `discover_script` searches the repo recursively by filename (used for custom analyses and the launcher). `resolve_saved_script` keeps a saved script path if it still exists, otherwise uses the plugin default. `to_portable_path` / `resolve_portable_path` handle repo-relative settings paths. Also `child_creation_flags` and `open_path`. |
| `output_folders` | `create_unique_test_output_folder(parent, test, generator)` |
| `inputs` | `parse_number_list` / `format_number_list` for comma-separated GUI fields |
| `powerpoint` | PNG snapshot and diff helpers for analysis PowerPoint export |

To add bench equipment, add an `EquipmentField` to `EQUIPMENT_FIELDS`. The Collection GUI and the environment hand-off pick it up automatically.
