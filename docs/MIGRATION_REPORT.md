# Plugin-architecture migration report

Branch: `refactor/plugin-architecture`. Each phase appends its section here. The final report (moves, deletions, diagram discrepancies, bench checklist) is completed in Phase 8.

## Phase 0: Safety net

### Backups
- `_backup_local_settings/` (gitignored) holds copies of `Bench_Profiles.json`, `Unified_Collection_GUI_Settings.json`, `Unified_Analysis_GUI_Settings.json`, and `RF Ramping/RF_Ramp_Collection_Settings.json`. `AE_Transparency_Testing_Suite_Settings.json` did not exist on this PC.
- A full pre-refactor zip (excluding `.venv` and `node_modules`) was saved outside the repo.

### Baseline checks (untouched code)
| Check | Result |
|---|---|
| `Paramount_Data_Collection_Unified_GUI.py --self-test` | pass. Reports `rf_tuning` script not discovered |
| `Paramount_Data_Analysis_Unified_GUI.py --self-test` | pass |
| `Transparency_Test_Suite_Launcher.py --self-test` | pass |
| `pyflakes suite_common` + root scripts | clean |
| `pytest tests` | 107 passed |

### Test harness added
- `tests/test_registry_snapshot.py` compares against `tests/fixtures/registry_baseline.json`: 14 collection tests, 3 rise/fall modes, 16 analyses, and the legacy aliases. Every definition field must match, and no script discoverable at baseline may become undiscoverable.
- `tests/test_compile_all.py`: every tracked `.py` outside the archives compiles.
- `tests/test_import_smoke.py`: modules that are safe to import. Most collection scripts connect to hardware at import time, so compile-only coverage applies to them.
- `tests/test_self_tests.py`: the three `--self-test` entry points.
- `tests/fakes/`: `FakeAEBus` records `create_buffer_cmd` frames, and `FakeResourceManager` records SCPI. `tests/test_fakes.py` proves both plug into the real libraries.
- `requirements-dev.txt` adds `pytest` and `pyflakes`.

### Pre-existing defects found (not fixed; behavior-preserving rule)
- **`rf_tuning` is not discoverable.** The registry expects `RF_Tuning_Collection.py` / `RF_Tuning_Analysis.py`, but the files are `Frequency Tuning/frequency_tuning_collection.py` and `frequency_tuning_analysis.py`. The plugin registry (Phase 3) will fix this naturally, because `plugin.json` names the entry explicitly.
- **Unregistered tests:** Arc Management, RF Ramping, Quasi CW Oscillations, Rise/Fall Quasi-CW and Split-CW, and Digital Timing exist on disk but are not in `TESTS`/`BUILTIN_ANALYSES`.
- **pyflakes `undefined name` hits:**
  - `AECommands_forFastDAQ` in `Quasi CW Oscillations/{QuasiCW,SplitCW}OscillationsCollection_GUI.py:128` and `Rise and Fall/{QuasiCW,SplitCW}DataCollection_GUI.py:~450`. The module does not exist in the repo.
  - `CSRError` in `AECommandsUniversal.py:149,155` (`rep_cmd`, `rep_cmd_fastDAQ`).
  - `memLocation` in `AE_Commands_7434753.py:1231`.
  - `exc` in `setpoint_transfer_function_analysis.py:320`.
  - `is_low_power_fall` in `CWDataAnalysis_GUI.py:1275`.
  - `j` in `MISC/helperFunctions.py:2341`.
- **Invalid-escape `SyntaxWarning`s** (`'C:\Windows\...'`, `'$\mu$'`) in the AE modules and `helperFunctions.py`. They are harmless today, but will become errors in a future Python.

## Phase 1: Hygiene

### Removed
| Path | Why |
|---|---|
| `RF Execution Timing/` (3 files) | Deprecated by the user. `Digital Timing/` is the live version, and its CLI and `UNITS` are supersets of the deprecated copy. |
| `suite_sdk/AE_Commands_PMNT_HF_1XX.py` | Byte-identical to `AESpecificDependencies/AE_Commands_PMNT_HF_1XX.py`. |
| `suite_sdk/AE_Com_Module.py` | An older strict subset of `AESpecificDependencies/AE_Com_Module.py`: it lacks `create_buffer_cmd_fastDAQ` and `AEB32.close` and has no unique code. |

### Untracked (files kept locally; now gitignored)
- `logs/log_1..12.csv`: output of `Digital Timing/CommandProcessing.py` (`CSV_PATH = "logs/log_%d.csv"`, relative to the working directory).
- `RF Ramping/RF_Ramp_Collection_Settings.json`: per-PC settings. Phase 2 migrates it to `runtime/`.
- New ignore rules: `logs/`, `runtime/`, `*_Settings.json`.

### Moved
| From | To |
|---|---|
| `AESpecificDependencies/_archive/*` | `archive/` (`README.txt` became `README.md`) |
| `Analog Port Timing/CollectionBeta.py`, `AnalysisBeta.py` | `archive/analog_port_timing/` |
| `AESpecificDependencies/MISC/ParamPorter/`, `PulseEyePPTGenerator.py`, `omegaSigmaFastDAQPowerPointGenerator.py` | `tools/` |

**Plan deviation:** `MISC/helperFunctions.py` (with `MISC/__init__.py`) stays in `AESpecificDependencies/`. It is live code: `Harmonics_Spurious_Collection.py` does `from MISC.helperFunctions import *`.

### Changed
- **`Digital Timing/CommandProcessing.py`** now builds `AECommandsSerialTimingAdapter.generator(0, COM_PORT)` / `(IP_ADDRESS, None)` instead of the nonexistent `AE_Commands_PMNT_HF_Anj`. All 23 generator methods it calls exist on `AECommandsUniversal.generator`. The unused `from turtle import delay` is removed.
- **`AEBus_Command_Latency_Collection_GUI.py`:**
  - **Pre-existing bug fixed as part of the required repoint.** `find_script` searched only its own folder, so both latency modes failed with "Missing required script". It now uses `suite_common.paths.discover_script`, which resolves both collectors in `Digital Timing/`.
  - The `rf_scope` mode runs `rf_onoff_scope_capture_GUI.py` with `--no-gui --output-parent <run folder>/rf_onoff_scope --dut-name <generator>`. The Digital Timing script otherwise opens its own settings GUI and writes to `PARAMOUNT_COLLECTION_PARENT_FOLDER` instead of the AEBus run folder.
  - The checkbox label is updated.
- **`RF_Ramp_Collection.py`:** the module search checks `AESpecificDependencies/` where it used to check `suite_sdk/`. It now loads the superset `AE_Com_Module`.
- **Quasi-CW / Split-CW collapse:**
  - `Rise and Fall/QuasiCWDataCollection_GUI.py`: `main(split_cw=False)` and `capture_transition(..., split_cw=False)`. `SplitCWDataCollection_GUI.py` is now a wrapper calling `main(split_cw=True)`.
  - A recording mock showed identical call sequences against the original files: 29 calls for Quasi-CW, 32 for Split-CW.
  - `SplitCWDataAnalysis_GUI.py`, `SplitCWOscillationsCollection_GUI.py`, and `SplitCWOscillationsAnalysis_GUI.py` were byte-identical to their Quasi counterparts and are now `runpy` wrappers.
- **`suite_common/paths.py`:** discovery also skips `archive/`.
- **`Unified_Collection_GUI_Settings.example.json`:** the legacy `analog_port_timing -> CollectionBeta.py` script entry is dropped. If a saved settings file still points there, `load_config` now rediscovers `analog_processing_timing_collection.py`, because the archived path no longer resolves.

### Observations for the user
- `Quasi CW Oscillations/SplitCWOscillationsCollection_GUI.py` was byte-identical to the Quasi version. Unlike the Rise and Fall Split-CW collector, it never called `setSplitCWEnable`. That behavior is preserved, but it may be an oversight.
- All four Quasi/Split-CW collectors still reference the missing `AECommands_forFastDAQ` module and methods absent from `AECommandsUniversal` (`setStartFreq`, `RF_ON`, `serialNumber`, ...). They cannot run today.

### Tests added
`test_aebus_latency_collectors.py` (both collectors resolve to Digital Timing, and every patched constant matches exactly once), `test_quasi_split_cw.py`, and `test_retired_modules.py`. Gate: **122 passed**, all three `--self-test`s pass, and the AEBus adapter resolves `AESpecificDependencies/`.
## Phase 1b: `AECommandsUniversal` is the single DUT command adapter

### Pre-check: `reportUnlockedSubCommand`
The only method whose body differed between `AE_Commands_PMNT_HF_1XX` and `AECommandsUniversal`. The difference is **purely additive**: Universal adds `elif` branches for 27 subcommands that PMNT_HF_1XX did not support (64, 71, 88, 89, 111, 112, 114, 116, 120-122, 128-131, 134-139, 142, 145-147, 173, 174, 183-185, 227, 302). PMNT_HF_1XX raised `Exception('Invalid subcommand!')` for those. Every existing branch is unchanged, so no previously working call behaves differently. The second, dead `elif subcommand == 183` branch in Universal is harmless.

### Golden-frame harness
- `tests/tools/golden_frames.py` recorded the retired libraries **before deletion** into `tests/fixtures/golden_frames/` (about 200 KB). `tests/test_golden_frames.py` replays every case against the current code and requires identical frames `(cmd, send_sizes, recv_sizes, args)`, order, and outcome (return value, or exception type and message).
- **Adapter:** 568 cases, covering the 36 `generator` methods the 12 scripts plus RF Ramping call. Arguments are generic values plus every literal argument tuple found at the scripts' call sites. Each case runs under four reply profiles:
  - `bench`: replies shaped by `recv_sizes`, with host control mode and CSR 0;
  - `csr_ok`;
  - `data`;
  - `csr12_all`.

  Every method reaches a normal return in at least one case (701 frames in total).
- **DLP methods:** 210 cases for the 10 `AECommandsDLPUnits` methods, compared against their Universal names, under five profiles. The fifth, `csr12_on_cmd8`, injects a CSR error on the setpoint command.
- **DLP script sequence:** the real `DLPDataCollection_GUI.py` functions run in collection order (`connect_generator`, two `configure_generator_dlp_mode` conditions, `turn_rf_off_between_conditions`, `disable_generator_dlp_mode`, `close_connections`) under five profiles, including the CSR-error paths and the transport close.
- **Result:** the adapter replay passed **before** the swap (proof the swap is safe) and again after. The DLP replay passes after the Universal additions. **51/51.**

### Changes
- **12 scripts swapped** from `AE_Commands_PMNT_HF_1XX` to `AECommandsUniversal`. Only the import, constructor, and docstring lines changed:
  - the five Analog Port Timing collectors;
  - DC Operating Point, Harmonics and Spurious, Power Accuracy, Power Linearity, Power Profile, and SLP Rise/Fall;
  - `RF_Ramp_Collection.py`, whose `GENERATOR_MODULE` is now `AECommandsUniversal`. This one was not in the plan's list of 12, because it loads its library dynamically.
- **`AECommandsUniversal.py` additions** (nothing existing changed):
  - a module-level `CSRError`, with the same codes and messages as DLPUnits;
  - a private `_dlp_csr_cmd`, with DLPUnits `csr_cmd` semantics;
  - a DLP command section.

  `CSRError` was already referenced but undefined in `rep_cmd`/`rep_cmd_fastDAQ`. Those methods still fail first on `self.device` (`AttributeError`), so their behavior is unchanged.
- **DLP method mapping** (old `AECommandsDLPUnits` name to new `AECommandsUniversal` name):

  | Old | New | Why |
  |---|---|---|
  | `RF_ON` | `DLP_RF_ON` | Universal `rfON` sends a control-mode query first |
  | `RF_OFF` | `DLP_RF_OFF` | Universal `rfOFF` adds a CSR check |
  | `setRegulationMode` | `DLP_setRegulationMode` | Different frame (`'2:1'`, state selector 1) |
  | `DLP_setpoint` | `DLP_setpoint_csr` | Universal's `DLP_setpoint` sends a different frame |
  | `pulseEnable`, `setPulseMode`, `setDLPfreqMode`, `setDLPFreq_s1`, `setDLPFreq_s0`, `setPulseFreq_DutyCycle` | same names | New in Universal; copied verbatim (`self.device` became `self.gen`) |
- **`DLPDataCollection_GUI.py`** uses `AECommandsUniversal.generator`. `close_generator_socket()` now just calls `gen.close()`. The old `device` fallback existed only because `AECommandsDLPUnits.close()` referenced a nonexistent `self.gen`.
- **Deleted:** `AESpecificDependencies/AE_Commands_PMNT_HF_1XX.py`, `AESpecificDependencies/AECommandsDLPUnits.py`. Its unused `flackpack`, `navigator`, and `pinmatch` classes went with it; nothing imported them.
- **`tests/test_retired_modules.py`** now guards all three retired modules (imports, constructors, and module-name strings). I verified it fails on a reintroduced `import AECommandsDLPUnits`.

### Latent defects in `AECommandsUniversal` (reported, not fixed)
- `rep_cmd` / `rep_cmd_fastDAQ` use `self.device` (the constructor sets `self.gen`) and `raise 'unexpected'` (a string raise, which is a `TypeError`). No migrated script calls them.
- `DLP_setpoint` (pre-existing) passes `2` as `recv_sizes`, so it sends a malformed frame. Only the broken Quasi/Split-CW collectors call it. The DLP collector uses `DLP_setpoint_csr`.

Gate: **177 passed**, all three `--self-test`s pass, and RF Ramping resolves `AESpecificDependencies/AECommandsUniversal.py`.
## Phase 2: Configuration (`config/` + `runtime/`)

### Layout
| Old location | New location | Tracked? |
|---|---|---|
| `AE_Product_Profiles.json` | `config/AE_Product_Profiles.json` | yes |
| `Bench_Profiles.example.json` | `config/templates/Bench_Profiles.example.json` | yes |
| `Unified_Collection_GUI_Settings.example.json` | `config/templates/collection_gui_settings.example.json` | yes |
| `Unified_Analysis_GUI_Settings.example.json` | `config/templates/analysis_gui_settings.example.json` | yes |
| `AE_Transparency_Testing_Suite_Settings.example.json` | `config/templates/launcher_settings.example.json` | yes |
| `Bench_Profiles.json` | `runtime/Bench_Profiles.json` | no |
| `Unified_Collection_GUI_Settings.json` | `runtime/collection_gui_settings.json` | no |
| `Unified_Analysis_GUI_Settings.json` | `runtime/analysis_gui_settings.json` | no |
| `AE_Transparency_Testing_Suite_Settings.json` | `runtime/launcher_settings.json` | no |
| `Arc Management/Arc_Management_{Collection,Analysis}_Settings.json` | `runtime/plugin_settings/arc_management.{collection,analysis}.json` | no |
| `Frequency Tuning/RF_Tuning_{Collection,Analysis}_Settings.json` | `runtime/plugin_settings/rf_tuning.{collection,analysis}.json` | no |
| `RF Ramping/RF_Ramp_Collection_Settings.json` | `runtime/plugin_settings/rf_ramping.collection.json` | no |

`Bench_Profiles.json` and `AE_Product_Profiles.json` keep the names used in the block diagram. The other runtime files use snake_case names that say which app owns them.

### Code
- **New `suite_common/config.py`** is the only place that knows these paths. It provides:
  - `product_profiles_path()`, `bench_profiles_path()`, `collection_gui_settings_path()`, `analysis_gui_settings_path()`, `launcher_settings_path()`, and `plugin_settings_path(plugin_id, kind)`;
  - `runtime_file()`, `migrate_legacy_settings()`, and `display_path()`.
- **First-use adoption:** each runtime accessor adopts its own legacy file the first time it is used, so standalone test scripts migrate too. The three apps also call `migrate_legacy_settings()` at GUI startup (not during `--self-test`).
- **Repointed:**
  - `suite_common/bench.py`: the `BENCH_PROFILES_PATH` constant is removed; `load/save_bench_profiles(path=None)` default to the accessor;
  - the three apps;
  - the five per-test scripts that save their own settings (Arc Management x2, Frequency Tuning x2, RF Ramping). Those five gained the standard `sys.path` and `suite_common` import; Phase 4 replaces it with package imports.
- **Status labels** in the GUIs now show the repo-relative runtime path.

**Plan deviation:** the plan said to rename originals to `*.migrated` in place. That would have left `*.json.migrated` files at the repo root and beside scripts, which defeats the goal of a clean root. Originals are instead **moved** to `runtime/migrated_legacy/<old relative path>`. Nothing is ever deleted, and an existing runtime file is never overwritten.

### Migration on this PC
- Ran `migrate_legacy_settings()`. `Bench_Profiles.json`, `Unified_Collection_GUI_Settings.json`, `Unified_Analysis_GUI_Settings.json`, and `RF_Ramp_Collection_Settings.json` were adopted. All four are **byte-identical** to the Phase 0 backups.
- I confirmed the collection GUI, analysis GUI, bench profiles, and launcher load them from `runtime/`.
- The repo root now has no JSON except `package.json` and `package-lock.json`.
- **If you switch back to `main`:** the old code looks for settings at the repo root. Copy them back from `runtime/migrated_legacy/` or `_backup_local_settings/`.

### Tests
`tests/test_config_paths.py` covers migration of a temporary legacy tree (content preserved, originals kept), idempotence, no overwrite when a legacy file reappears, first-access adoption, and a guard that only `package*.json` is tracked at the repo root. Gate: **183 passed**, all three `--self-test`s pass, and pyflakes on the core is clean.
## Phase 3: Plugin framework (in place)

### `suite_sdk/` (new package; the old `suite_sdk/` held only duplicate modules and was removed in Phase 1)
- **`plugin.schema.json`:** the plugin contract. Plugin files reference it in `$schema` for editor validation.
- **`schema.py`:** a small validator for the JSON Schema keywords that file uses. The plan allows no new runtime dependencies, so it does not use `jsonschema`.
- **`registry.py`:** `load_registry()` discovers plugin files with a pruned `os.walk` that skips `.venv`, `archive/`, `runtime/`, and `tests/`. It validates schema, file-name kind, entry and mode script existence, duplicate plugin ids, and duplicate test keys. Invalid plugins go to `registry.unavailable` with the reason and never raise. Queries:
  - `tests(kind, product=, firmware=, include_unlisted=)`, sorted by `order`;
  - `test(kind, key)` and `plugin(kind, id)`;
  - `resolve_aliases(kind, saved_keys)`.
- **`context.py`:** a `SuiteContext` dataclass with JSON `save`, `load`, and `from_environment` (`PARAMOUNT_SUITE_CONTEXT`), so Phase 5 can wire it in. It ignores unknown fields, so newer files still load.

### Plugin files (26)
- **Layout:** while collection and analysis scripts still share a folder, each folder has `plugin.collection.json` and `plugin.analysis.json`. Phase 4 splits them into `collection_plugins/<id>/plugin.json` and `analysis_plugins/<id>/plugin.json`.
- **Generated, not hand-written:** a one-off generator built them from the Phase 2 `TESTS`, `RISE_FALL_SCRIPT_CANDIDATES`, and `BUILTIN_ANALYSES`. Names, equipment, instruction text, descriptions, and order are therefore verbatim. Entries use each script's real on-disk filename.
- **Plugin ids:** follow the plan's mapping: `dc_operating_point`, `harmonics_spurious`, `power_linearity_accuracy`, `rise_fall_time`, `analog_port_timing`, `aebus_command_latency`, `power_profile`, `pulse_sync`, `rf_tuning`, `arc_management`, `rf_ramping`, `quasi_cw_oscillations`, `rf_on_off_timing`. Test keys are unchanged, so saved settings keep working.
- **Rise/Fall:** keeps one collection key, `rise_fall_time_verification`, with `modes` `{CW, SLP, DLP}` mapped to scripts.
- **Legacy keys move into `aliases`:** collection `analog_port_timing`; analysis `t4`-`t8` and `analog_port_timing`.
- **Unlisted tests:** tests that exist on disk but were never in the GUIs are included with `"listed": false`, so the GUIs look exactly as before. They are Arc Management, RF Ramping, Quasi-CW / Split-CW Oscillations, Rise/Fall Quasi-CW and Split-CW, and Digital Timing (`rf_onoff_scope_capture`, `rf_onoff_command_processing`, `rf_onoff_latency`). Their `requires` is empty because those scripts hardcode their instruments and never call `bench_default`.

### GUI changes
- **Collection GUI:**
  - `TESTS`, `TEST_BY_KEY`, `RISE_FALL_SCRIPTS`, and `RISE_FALL_MODES` are now built from the registry.
  - **Deleted:** `RISE_FALL_SCRIPT_CANDIDATES`, the hardcoded `TestDefinition` list, `LEGACY_ANALOG_PORT_TIMING_KEY`, `ANALOG_PORT_TIMING_REPLACEMENT_KEYS`, `migrate_legacy_analog_port_selection`, and the legacy CollectionBeta script-path upgrade (its target was archived in Phase 1).
  - **New:** `migrate_selection()` uses the registry aliases. Setup images saved under any legacy key carry over to the replacement tests, a generalization of the old analog-only rule.
- **Analysis GUI:**
  - `BUILTIN_ANALYSES` is now built from the registry.
  - **Deleted:** `NEW_ANALOG_ANALYSIS_KEYS` and `migrate_legacy_analysis_entries`; `migrate_analysis_entries()` replaces them. Unknown keys (custom analyses) pass through unchanged.
  - **Custom analyses** still work: they are found again by filename. Built-ins use the registry path.
- **Saved script paths:** `suite_common.paths.resolve_saved_script(saved, default)` keeps a saved script path if it still exists, otherwise uses the plugin default. The filename-candidate lists are gone from both GUIs.
- **Unavailable plugins:** both GUIs warn about them at startup (console plus a dialog), and both `--self-test`s print them.
- **AEBus Command Latency** finds its two Digital Timing collectors with `load_registry().test("collection", key)`, as amendment A1 required.

### Behavior changes (intended)
- **`rf_tuning` now resolves:** collection `Frequency Tuning/frequency_tuning_collection.py`, analysis `frequency_tuning_analysis.py`. Before, the GUIs looked for nonexistent `RF_Tuning_*.py` names and showed "Missing". This user's saved selection is RF Tuning only, so it now works.
- **The test suite runs in about 3 s instead of about 40 s,** because filename discovery no longer walks `.venv`.

### Gate
- **Registry baseline:** `tests/test_registry_snapshot.py` passes against the **Phase 0** baseline (the baseline file was not regenerated): same 14 collection tests, 3 Rise/Fall modes, 16 analyses, order, names, equipment, instructions, and legacy aliases. No test lost discoverability; `rf_tuning` gained it.
- **Real settings:** loaded through both GUIs, all 14 + 3 + 16 script paths resolve.
- **`tests/test_plugin_registry.py`:**
  - every shipped plugin is valid;
  - `requires` values are real bench equipment keys;
  - every `paired_with` resolves;
  - six kinds of invalid file are reported, not raised;
  - a duplicate key keeps the first plugin;
  - the file name kind must match the file's `kind`;
  - product and firmware filters work;
  - unlisted tests stay hidden;
  - `SuiteContext` round-trips.
- **Result: 204 passed**, all three `--self-test`s pass, and pyflakes is clean on `suite_sdk`, `suite_common`, and the three apps.