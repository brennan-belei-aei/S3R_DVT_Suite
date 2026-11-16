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
## Phase 4: Folder restructure

### Moves (all `git mv`, so history is kept; driven by the plugin files, so the layout and the plugin files cannot drift apart)
| From | To |
|---|---|
| `<Test folder>/plugin.collection.json` + its scripts | `collection_plugins/<plugin id>/plugin.json` + scripts |
| `<Test folder>/plugin.analysis.json` + its scripts | `analysis_plugins/<plugin id>/plugin.json` + scripts |
| `Analog Port Timing/<5 subfolders>/` | flattened into `collection_plugins/analog_port_timing/` and `analysis_plugins/analog_port_timing/` (no name clashes) |
| `AESpecificDependencies/Command Latency/` | `collection_plugins/aebus_command_latency/`, `analysis_plugins/aebus_command_latency/` |
| `Digital Timing/` | `collection_plugins/rf_on_off_timing/` (with `AECommandsSerialTimingAdapter.py`), `analysis_plugins/rf_on_off_timing/` (with the standalone helper `statistics_new.py`); `usage_rf_on_off.txt` became `docs/rf_on_off_timing_usage.txt` |
| `Paramount_Data_Collection_Unified_GUI.py` | `suite_apps/collection_gui.py` |
| `Paramount_Data_Analysis_Unified_GUI.py` | `suite_apps/analysis_gui.py` |
| `Paramount_PowerPoint_Builder.py` | `suite_apps/Paramount_PowerPoint_Builder.py` |
| `Presentation Themes/` | `assets/presentation_themes/` |

The old test folders (12, plus `AESpecificDependencies/Command Latency/` and `Presentation Themes/`) were empty afterwards and were removed.

Plugin ids now double as folder names: `aebus_command_latency`, `analog_port_timing`, `arc_management`, `dc_operating_point`, `harmonics_spurious`, `power_linearity_accuracy`, `power_profile`, `pulse_sync`, `quasi_cw_oscillations`, `rf_on_off_timing`, `rf_ramping`, `rf_tuning`, `rise_fall_time`. Script filenames are unchanged, so the operators' data-folder conventions and any external references by filename still hold.

### Imports and paths
- **Plan deviation, `sys.path`:** the plan asked to replace the `sys.path.insert` hacks with package imports "where feasible". Plugin scripts run as child processes and standalone (`python <script>`), and the AEBus latency GUI executes patched copies of them, so package imports (`python -m ...`) would change how every script is launched. Instead, the 22 fixed-depth hacks (`parents[1]`, `parents[2]`, `APP_DIR.parent`) became one **depth-independent** line:
  ```python
  sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
  ```
  `tests/test_plugin_layout.py` forbids `parents[N]` in plugin scripts and requires this line wherever `suite_common` is used. `suite_common/bootstrap.py` remains the AE-module shim until Phase 6.
- **Same line in `suite_apps/collection_gui.py` and `analysis_gui.py`,** because the launcher starts them as scripts. `suite_apps/` is also a package, so the tests import `suite_apps.collection_gui`.
- **Launcher:** looks for `collection_gui.py` / `analysis_gui.py`. A stale saved launcher path falls back to discovery.
- **PowerPoint builder:** resolves `REPO_ROOT` the same way; the theme folder is `assets/presentation_themes/` and `NODE_PATH` uses `<repo>/node_modules`. The builder-local `S3RTransparencyPPTXTheme.thmx` candidate and every override (`--theme`, env, manifest) are unchanged.
- **Cross-imports:** none between analysis and collection scripts. The only sibling imports (`AECommandsSerialTimingAdapter`, `QuasiCWDataCollection_GUI`, the `runpy` wrappers) moved together with their targets.

### Pre-existing defects fixed because they block running from the new folders
- `analog_processing_timing_collection.py` imported `suite_common` with no repo-root line, so it only worked when started with the repo root as the working directory. It now has the root line.
- `QuasiCWOscillationsCollection_GUI.py` and Rise/Fall `QuasiCWDataCollection_GUI.py` imported `AECommandsUniversal` without any bootstrap, so the import could never resolve. They now have the root line plus `suite_common.bootstrap`. They still reference the missing `AECommands_forFastDAQ` (Phase 0 finding), so they still cannot run.

### Saved settings
`runtime/collection_gui_settings.json` and `analysis_gui_settings.json` still hold pre-move script paths. Those no longer exist, so every entry falls back to the plugin default, and the next save rewrites them. Verified: all 14 + 3 + 16 scripts resolve from the real settings. `suite_common.config.LEGACY_LOCATIONS` keeps the old per-test settings paths (`Arc Management/...`), because those are the pre-refactor locations that it migrates from.

### Notes
- `analog_processing_timing_collection.py` defaults `OUTPUT_ROOT` to `Results/` beside the script, so it is now `collection_plugins/analog_port_timing/Results/`. Behavior is otherwise unchanged; Phase 5 run packages replace this.
- The analog processing timing collector has **no** `--self-test`. An early probe (matching `self.test_id`) briefly opened its GUI; it was closed. The layout test only runs scripts that literally handle `"--self-test"`.

### Gate
- **New `tests/test_plugin_layout.py`:**
  - every plugin lives at `<kind>_plugins/<id>/plugin.json` with its scripts beside it;
  - no fixed-depth repo-root code;
  - **every plugin script's module-level imports resolve from its new folder**, each probed in a fresh interpreter started like `python <script>` (the self-locating adapter is imported for real);
  - the 6 plugin scripts with `--self-test` (Arc Management x2, RF Tuning x2, RF Ramping x2) pass it from an unrelated working directory.
- The three apps pass `--self-test`, the launcher resolves `suite_apps/*`, and the builder finds `assets/presentation_themes/S3RTransparencyPPTXTheme.thmx`.
- The Phase 0 registry baseline still matches.
- **Result: 314 passed;** pyflakes is clean on `suite_common`, `suite_sdk`, `suite_apps`, the launcher, and `tests`.
## Phase 5: Suite context, process managers, packages, provenance

### Process protocol (`suite_common/process_protocol.py`)
- **`ENV_VARS`** declares, with a one-line description, all 54 `PARAMOUNT_*` names found in the code, plus the new ones: `PARAMOUNT_PROTOCOL_VERSION`, `PARAMOUNT_SUITE_CONTEXT`, `PARAMOUNT_RUN_ID`, `PARAMOUNT_FIRMWARE_VERSION`, `PARAMOUNT_DUT_ADAPTER`, and `PARAMOUNT_ADAPTER_COMPATIBILITY`. `PARAMOUNT_DATASET_` is a declared dynamic prefix (Harmonics builds the name with an f-string). `PROTOCOL_VERSION = 1`.
- **No existing name was renamed or removed.** `tests/test_process_managers.py` greps the code and fails on any undeclared name.

### Launcher (product, firmware, DUT adapter)
- **`config/AE_Product_Profiles.json` is now schema v2.** Each product has a `firmware` list of `{version, dut_adapter, compatibility, notes, test_defaults, constraints}`. Paramount lists `7433660J00` (AECommandsUniversal, **verified**: its docstring says "Known to work") and `7434759A31` (**unverified**: "To be determined"). Other products have an empty list until the user fills it in. Schema v1 files still load (no firmware rows).
- **Firmware dropdown:** the launcher has an editable dropdown (an unlisted version can be typed) and a live adapter compatibility label (green Verified, amber Unverified, red Unavailable), as in the diagram.
- **At launch:** an *Unavailable* adapter (module file missing) blocks the launch, and *Unverified* asks first. The launcher writes `runtime/suite_context.json` (`SuiteContext`: product, firmware, adapter, compatibility, SHA-256 revisions of the product and bench profile files, paths) and passes `PARAMOUNT_SUITE_CONTEXT`, the firmware, adapter, compatibility, and protocol variables. Firmware selection is saved in launcher settings.
- **Fixed a Phase 2 regression:** "Open Profiles Folder" opened the repo root; it now opens `config/`.

### Process managers (`suite_apps/`)
- **`process_manager.ChildProcessManager`:**
  - `launch()` sets `PARAMOUNT_RUN_ID` and the protocol version.
  - `poll()` returns nothing while running, then the result once.
  - `cancel()` terminates the child and records it as cancelled.
  - `busy()` and `wait()`.
  - Manifests are written on a worker thread, so hashing a large data folder never freezes the GUI.
- **`collection_process_manager.CollectionProcessManager.launch_test()`** watches the collection parent folder; every folder a script creates there becomes a Run Package.
- **`analysis_process_manager.AnalysisProcessManager.launch_analysis()`** watches the analysis output folder and the dataset folders, always writes into the presentation package folder, and records dataset folders as inputs.
- **Both GUIs** now delegate launching and polling to these managers. The sequence logic, dialogs, retry/skip/stop, and status texts are unchanged. `on_close` asks the manager whether a child is still busy.
- **Plan deviation:** `cancel()` exists and is tested, but no new button was added. The GUIs keep their existing "stop after current" behavior, since adding a kill button to running bench tests is a UX/safety decision for the user.

### Manifests (`suite_common/manifests.py`)
- **`run_manifest.json`** (Run Package) and **`analysis_manifest.json`** (Analysis Package). Required fields: schema version, kind, run id, status (`complete`/`failed`/`cancelled`), exit code, started/finished, product, firmware, DUT adapter and compatibility, bench (profile and instrument addresses, collection only), effective `PARAMOUNT_*` settings, suite (git commit, dirty flag, protocol version, context file, profile revisions), plugin (id, version, test key/name), script (path and SHA-256), inputs, and outputs (every file with size and SHA-256).
- **Inputs:** an analysis input that is a Run Package is referenced by its `run_id` and the SHA-256 of its `run_manifest.json`, which transitively covers every data file. Other inputs have their files hashed.
- **No script changes:** the managers write manifests around the child. The scripts' own files (for example the presentation package `manifest.json`) are never touched, so behavior is unchanged.
- **The presentation manifest** gains a `suite_context` block, which the builder can read. The pptx filename and content are **unchanged**.
- **Discrepancy with the diagram:** the diagram's "Transparency_Test_Results.pptx (product- and date-stamped)" naming is not applied. Changing the output filename is a behavior change left for the user (`presentation.filename` in the analysis GUI).

### Tests (`tests/test_process_managers.py`)
- the environment contract is complete;
- **end to end:** a fake collector (real subprocess) writes into the collection parent, and the Run Package manifest is complete, carries the context, bench, and plugin, and its hashes match the data. A fake analyzer then reads that dataset; both of its analysis manifests are complete and reference the run by `run_id` and manifest hash, and the script's own `manifest.json` is untouched;
- a nonzero exit records `failed`;
- a cancelled run with no output folder goes to `runtime/run_records/`;
- a second launch while busy is refused;
- adapter resolution: verified, unlisted (unverified), and missing module (unavailable);
- the shipped product profiles are v2 and resolve to a verified AECommandsUniversal;
- `SuiteContext.current()` falls back to the environment for standalone runs.

Gate: **328 passed**, all three `--self-test`s pass, pyflakes is clean, and the tests write nothing into the real `runtime/`.
## Phase 6, part 1: Instrument Abstraction Layer (branch `refactor/phase6-instruments`)

All Phase 6 work is on its own branch, at the user's request. The Phase 0-5 state is preserved on `refactor/plugin-architecture` and tagged `phase5-complete`.

### Moves (`git mv`)
| From | To |
|---|---|
| `AESpecificDependencies/{AE_Com_Module, AECommandsUniversal, AECommands, AE_Commands_7434753, Lynx}.py` | `suite_instruments/adapters/ae/` |
| `collection_plugins/rf_on_off_timing/AECommandsSerialTimingAdapter.py` | `suite_instruments/adapters/ae/` (as amendment A1 required) |
| `AESpecificDependencies/SpectrumAnalyzer/{spectrum_analyzer, KeysightN9320B, __init__}.py` | `suite_instruments/adapters/keysight/` |
| `AESpecificDependencies/SpectrumAnalyzer/*.pdf` (programmer's guides) | `docs/instruments/` |
| `AESpecificDependencies/MISC/helperFunctions.py` | `collection_plugins/harmonics_spurious/helperFunctions.py` (its only user; `MISC/__init__.py` deleted) |
| `AESpecificDependencies/README.md` | `suite_instruments/README.md` (rewritten) |

`AESpecificDependencies/` no longer exists. No AE module's code was edited.

### Rewiring
- **`suite_common.bootstrap`** now puts `suite_instruments/adapters/ae/` on `sys.path`. `AE_ADAPTERS_DIR` overrides it, and the legacy `AE_SPECIFIC_DEPENDENCIES_DIR` still works. `suite_common.AE_DEPENDENCIES_DIR` became `AE_ADAPTERS_DIR`. AE modules stay **flat-imported**; a package-path import would create a second copy of each module.
- **`AECommandsSerialTimingAdapter.py`:** the `rglob` dependency hunt was deleted, as A1 required. Its public API (`generator(ipAddress, comPort=None)`, `rfON_TEST`) is byte-for-byte unchanged. Its two users (`CommandProcessing.py`, `rf_onoff_scope_capture_GUI.py`) gained the repo-root line and `suite_common.bootstrap`. The AEBus latency runtime copies still work: they run beside the original, and the root line finds the repo from there.
- **Harmonics:** imports `suite_instruments.adapters.keysight.spectrum_analyzer` and its sibling `helperFunctions`.
- **RF Ramping** searches `suite_instruments/adapters/ae/`. **The launcher's** adapter check uses the new folder and still resolves AECommandsUniversal as verified.
- **The example settings templates** still pointed at the Phase 3 `AESpecificDependencies/Command Latency/...` paths, a Phase 4 miss. They are fixed.

### Pre-existing defect fixed
`arc_management_collection.py` and **`frequency_tuning_collection.py` (RF Tuning, the user's selected test)** loaded the generator with `importlib.import_module("AESpecificDependencies.AECommandsUniversal")`, falling back to the flat name, without the bootstrap. Both failed: the package import breaks on the module's own `import AE_Com_Module`, and the flat name was never on the path. **Neither script could create a generator.** Both now import `suite_common.bootstrap`, and the dead package candidate is removed. `test_dynamic_loaders_find_the_adapter_modules` proves the import resolves.

### New layer (`suite_instruments/`)
- **`interfaces/`:** structural Protocols per role (`Generator`, `Oscilloscope`, `WaveformGenerator`, `DCSupply`, `SpectrumAnalyzer`, `PowerMeter`, `Tuner`) and `ROLE_INTERFACES`, which covers every bench equipment key.
  - **`AECommandsUniversal.generator` satisfies `Generator` unmodified**, as amendment A2 required.
  - The SCPI roles currently specify the SCPI session (`write`/`query`/`close`). Vendor-neutral methods (e.g. "arm single acquisition") are added in part 2, derived from the commands the scripts actually send, rather than invented now.
- **`transport/visa.py` `VisaTransport`:** a drop-in for a pyvisa resource. It opens with bounded retries, applies `timeout_ms`, logs at DEBUG, and normalizes errors to `InstrumentTimeout` (also a `TimeoutError`) / `InstrumentError` with the original chained. Attributes pass through. It **never adds or resends commands**; retries apply only to opening.
- **`transport/ae_com.py` `AeBusTransport`:** wraps an AE_Com_Module connection and logs every frame. Arguments, replies, and exceptions are unchanged, with no resends, because AE bus RF ON must never repeat implicitly.
- **`adapters/__init__.py`:** the adapter catalog and `DEFAULT_ADAPTERS`:
  - `ae.universal` (generator, psync slave) and `ae.serial_timing` (generator COM port);
  - `ae.lynx` and `ae.navigator`;
  - `keysight.n9320` (spectrum analyzer);
  - `visa.scpi` (oscilloscope, waveform generator, DC supply).

  AE adapters are built through each driver's **existing constructor**, so connection behavior is identical, and then have their connection attribute wrapped. Connect failures are normalized to `InstrumentConnectionError`.
- **`factory.py` `InstrumentFactory`:**
  - `from_bench()` follows the suite's address lookup order: environment, then profile. The per-profile adapter choice comes from an optional `"adapters"` section in `Bench_Profiles.json`; `suite_common.bench` now preserves that section through load and save. The Phase 5 branch ignores it and falls back to the defaults.
  - `check()` / `require()` report every missing address, unknown or wrong-role adapter, and missing `plugin.json` capability.
  - `create(role, **options)` also verifies the result implements the role's interface.
- **Plan deviation, settings and limits:** "applies settings and limits" is limited to transport options (`timeout_ms`). Instrument limits are test-specific today and move with each script in part 2.

### Gate (`tests/test_instruments.py`, 16 tests)
- **All 568 recorded golden AE-bus cases** replay identically (frames, order, outcome) through a **factory-built** generator, so the logging transport is wire-transparent.
- **AE roles:** generator, serial generator, Lynx, and Navigator are built by their own drivers and wrapped, and satisfy their role interface.
- **Errors:** a refused connection is normalized with its cause chained.
- **SCPI:** `visa.scpi` passes commands, attributes, and timeout through unchanged; close is idempotent and closes its resource manager. Timeout and I/O errors normalize correctly, and open retries then fail cleanly.
- **Factory:** `check()` lists every problem kind and `require()` raises the right error. Every role required by a listed collection test can be served by its default adapter. `from_bench()` honors environment overrides and per-profile adapters, and the `adapters` section survives a bench profile save.
- **The dynamic loaders** (Arc Management, RF Tuning) now resolve AECommandsUniversal.
- **Result: 349 passed;** the Phase 0-5 suite (golden frames, plugin import resolution from every folder, self-tests) is unchanged and green. The three apps pass `--self-test`, and pyflakes is clean.

### Not done yet (part 2)
- **Script migration:** no collection script uses the factory yet; they still open pyvisa and AE modules directly. Part 2 migrates the 18 `pyvisa` scripts one at a time. Each migration first captures a golden SCPI log with the Phase 0 VISA fakes and must reproduce it exactly.
- **Phase gate still open:** "no collection script imports `pyvisa` or an AE module directly" is part 2's exit criterion.
## Phase 6, part 2, batch 1: CEX Lock Acquisition, Interlock Response Timing, RF Enable Timing

### Golden SCPI/AE-bus harness (`tests/tools/golden_scpi.py`)
- **What it runs:** each script's real collection routine (`run_collection`), against:
  - scripted VISA instruments;
  - a frame-level DUT model: control mode via frames 14/155, RF on/off from host frames 1/2 or from the RF Enable line in User Port mode, interlock from the BK supply output, CEX lock from the CEX reference output, fixed-frequency limits 12.88-14.24 MHz, regulation mode, and power limit;
  - a virtual clock, so `sleep()` advances it and polling loops are deterministic and instant.
- **What it records, in one ordered log:** the resource manager open/close, every VISA open (and refused open), attribute set (`timeout`, `read_termination`, `write_termination`, `chunk_size`), write, query with reply, binary read with options, close, and every AE-bus frame with reply.
- **Fake instrument models:**
  - Siglent `C<n>:OUTP?` reports output state and load;
  - Tektronix `CH<n>:TERMINATION?` echoes what was set;
  - BK 9141 `MEAS:VOLT?` follows `APPL` and the output state.
- **Golden logs** were recorded from the **unmodified** scripts and committed *before* any script changed (`46bc442`). Six scenarios, every one ending `return 0` (full success path):
  - the normal path for each of the three scripts;
  - CEX and RF Enable with the waveform generator's VISA address refused (`::SOCKET` fallback);
  - Interlock with the DC supply's VISA address refused.

  Re-recording reproduces them byte for byte.

### Migration (connection code only; no measurement code touched)
- `import pyvisa` and `import AECommandsUniversal` were removed.
- `pyvisa.ResourceManager()` became `suite_instruments.transport.visa.resource_manager()`; it is still one shared manager, so Interlock's `rm.visalib` print still works.
- `rm.open_resource(addr)` became `InstrumentFactory(...).create(role, [address=fallback,] resource_manager=rm, open_attempts=1)`. The roles are `oscilloscope`, `waveform_generator`, and `dc_supply`. `open_attempts=1` keeps each script's own fallback loop authoritative (no added retries).
- `AECommandsUniversal.generator(addr)` became `factory.create("generator")`.
- **Factory additions:** `create(..., address=...)` for fallback addresses, and the `resource_manager()` helper.
- **Behavior notes (failure paths only):**
  - An open failure is now an `InstrumentConnectionError`, so the "Unable to connect" message names it instead of the raw pyvisa error. All three scripts catch `Exception`.
  - A bare IP typed into a script's address field is now normalized to a VISA resource (`TCPIP0::<ip>::INSTR`) as the bench profile already does. Before, it was passed raw to pyvisa, which rejects it.
- **Gate:**
  - all 6 golden logs are **identical** after migration (`tests/test_golden_scpi.py`);
  - a guard test fails if a migrated script imports `pyvisa` or an AE module directly;
  - the plugin import-resolution tests pass;
  - **369 passed** in total.

### Finding: Windows path length
Under pytest's deep temp folder, the Interlock scripts' waveform path (`<parent>\<timestamp> - Interlock Response Timing - <DUT>\INTERLOCK_RESPONSE_TIMING_<power>W_Cycle<n>_Interlock_FALL_CH<n>_<timestamp>.csv.partial`) went past the 260-character Windows path limit, and the write failed. This is pre-existing. On the bench, choose a collection parent folder with a short path, e.g. `C:\Data`, or enable Windows long paths.

### Bench validation for batch 1 (run on hardware before relying on these tests)
For each script, run 1 power point with 1 cycle/acquisition from the Collection GUI. Compare the output files and console with a pre-refactor run:
1. **CEX Lock Acquisition:** the scope and waveform generator connect (console shows both `*IDN?`), the DUT connects, the CEX reference toggles, and the lock/unlock checks pass. CH1-CH3 CSVs and `Lock_State_Verification.csv` are written.
2. **Interlock Response Timing:** the BK 9141 programs 25 V / 0.5 A, the interlock falls, RF drops, and CH1-CH2 CSVs are written. If the supply is reachable only by socket, confirm the `::SOCKET` fallback message.
3. **RF Enable Timing:** RF ON and RF OFF edges are captured at each power, and CH1-CH3 CSVs are written.
4. **All three:** the run folder now also contains `run_manifest.json` (Phase 5).