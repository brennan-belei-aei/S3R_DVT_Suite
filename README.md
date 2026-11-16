# S3R Transparency Testing Suite

Bench automation for collecting and analyzing AE RF generator transparency data. The suite covers DC operating point, harmonics and spurious, power accuracy and linearity, power profile, rise/fall time (CW/SLP/DLP), analog port timing, AEBus command latency, and Pulse Sync. It produces PowerPoint summaries.

## Quick start

1. Install Python 3.10+ and NI-VISA (or use `pyvisa-py`). Install Node.js if you need PowerPoint generation.
2. From the repo root, set up the environment:
   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   npm install
   ```
3. Start the launcher:
   ```powershell
   .venv\Scripts\python Transparency_Test_Suite_Launcher.py
   ```
4. In **Data Collection → Bench Equipment**, set up the bench:
   - Pick or create a **bench profile**.
   - Click **Detect Instruments** to fill the address drop-downs.
   - Click **Save Profile**.

## How it fits together

```
Transparency_Test_Suite_Launcher.py      product selection; starts the two GUIs
suite_apps/
├── collection_gui.py                    bench profile + test sequence; runs each collection script as a child process
├── analysis_gui.py                      runs each analysis script, then Paramount_PowerPoint_Builder.py
└── Paramount_PowerPoint_Builder.py      Node.js/pptxgenjs PowerPoint generation (child process)
collection_plugins/<plugin id>/          plugin.json + collection script(s), one folder per plugin
analysis_plugins/<plugin id>/            plugin.json + analysis script(s), same ids as collection
suite_sdk/                               plugin registry, plugin.json schema, suite context
suite_common/                            shared helpers (config paths, bench addresses, output folders, parsing)
suite_instruments/                       instrument abstraction layer: factory, interfaces, transports, adapters (AE modules incl. AECommandsUniversal = DUT adapter, Keysight)
config/  runtime/                        committed defaults / per-PC settings (see Configuration)
assets/presentation_themes/              PowerPoint theme (.thmx) used by the builder
tools/  archive/  docs/  tests/          standalone utilities, unused legacy code, documentation, hardware-free tests
```

- **Plugins:** every folder under `collection_plugins/` and `analysis_plugins/` holds a `plugin.json` describing its tests. Both GUIs build their test lists from the plugin registry (`suite_sdk/registry.py`). An invalid plugin file is skipped with a warning that gives the reason; it never stops the GUI.
- **Child processes:** the GUIs never import test scripts. Each script runs as its own process and receives `PARAMOUNT_*` environment variables. The full, versioned list is in `suite_common/process_protocol.py`; a test fails if code uses an undeclared name.
- **Product, firmware, and DUT adapter:** the launcher's firmware list comes from `config/AE_Product_Profiles.json` (schema v2: each product's `firmware` rows name a `dut_adapter` and its `compatibility`). The launcher shows whether the adapter is *Verified*, *Unverified* (asks before launching), or *Unavailable* (blocks), writes `runtime/suite_context.json`, and passes its path in `PARAMOUNT_SUITE_CONTEXT`.
- **Process managers and provenance:** `suite_apps/collection_process_manager.py` and `analysis_process_manager.py` launch, monitor, and cancel child scripts. When a script ends, they write `run_manifest.json` into each folder it created under the collection folder (a *Run Package*), and `analysis_manifest.json` into each analysis output/package folder. A manifest records product, firmware, adapter, bench profile and instrument addresses, effective `PARAMOUNT_*` settings, suite commit, plugin id/version, script hash, timestamps, status, and input/output file hashes. Runs with no detectable output folder are recorded in `runtime/run_records/`.
- **Standalone runs:** every collection script can also run on its own. It then reads addresses from the active bench profile.
- **Address lookup order:** `bench_default()` in `suite_common/bench.py` checks the environment variable first, then the active profile in `runtime/Bench_Profiles.json`, then blank.

## Configuration

| Folder | Committed? | Contents |
|---|---|---|
| `config/` | yes | `AE_Product_Profiles.json` (product list for the launcher) and `templates/*.example.json` |
| `runtime/` | no (gitignored) | Everything specific to this PC, listed below. The apps create it on first use. |

| `runtime/` file | Purpose | Template in `config/templates/` |
|---|---|---|
| `Bench_Profiles.json` | Instrument addresses per bench | `Bench_Profiles.example.json` |
| `collection_gui_settings.json` | Test selection/order, script paths | `collection_gui_settings.example.json` |
| `analysis_gui_settings.json` | Analysis selection, dataset/presentation defaults | `analysis_gui_settings.example.json` |
| `launcher_settings.json` | Launcher script paths, selected product | `launcher_settings.example.json` |
| `plugin_settings/<plugin>.<collection\|analysis>.json` | Settings saved by individual test GUIs (Arc Management, RF Tuning, RF Ramping) | none |

All paths come from `suite_common/config.py`; never build a settings path by hand. Settings files from before the refactor (at the repo root or beside a test script) are moved into `runtime/` automatically the first time they are needed, and the originals are kept in `runtime/migrated_legacy/`.

Script paths are stored relative to the repo. If a saved path no longer exists, the GUI falls back to the script named in the plugin file.

## Adding a test

1. Create `collection_plugins/<plugin_id>/<Name>_Collection.py` (and `analysis_plugins/<plugin_id>/<Name>_Analysis.py`); `<plugin_id>` is snake_case. Start the collection script with:
   ```python
   sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
   import suite_common.bootstrap  # noqa: F401
   from suite_common.bench import bench_default
   ```
   That first line finds the repo root at any folder depth; do not use `parents[N]`.
2. Get every instrument address with `bench_default("<equipment key>")`. Never hardcode IPs, serials, or COM ports.
3. Describe the test in a `plugin.json` beside each script (`"kind": "collection"` or `"analysis"`, the same `id` in both). Copy an existing one; the format is defined in [suite_sdk/plugin.schema.json](suite_sdk/plugin.schema.json). Each entry under `tests` needs a unique `key`, a display `name`, the script path in `entry`, an `order`, and, for collection tests, the bench equipment in `requires` and the setup text in `instructions`. Do not edit the GUIs.
4. Run `.venv\Scripts\python -m pytest tests/test_plugin_registry.py tests/test_plugin_layout.py` to confirm the plugin is valid and its imports resolve.

Tests that exist on disk but are not offered in the GUIs yet (Arc Management, RF Ramping, Quasi-CW / Split-CW, Digital Timing) are in the plugin files with `"listed": false`. Set it to `true` to show one.

## Checks

```powershell
.venv\Scripts\pip install -r requirements-dev.txt   # once: adds pytest and pyflakes
.venv\Scripts\python -m pytest tests
.venv\Scripts\python -m pyflakes suite_common suite_sdk suite_apps Transparency_Test_Suite_Launcher.py
```

`tests/` needs no hardware. `tests/fakes/` records AE-bus frames and SCPI traffic so command sequences can be compared before and after a change. If you intentionally change a test definition, regenerate the registry baseline with `.venv\Scripts\python -m tests.tools.dump_registry --write`.

## Docs

See [docs/README.md](docs/README.md) for the documentation index: project memory, AI workflow, review write-ups, roadmap, and legacy notes. AI agents should start at [AGENTS.md](AGENTS.md). `archive/` holds unused legacy scripts kept for reference; nothing imports them. `tools/` holds standalone utilities that are not part of the suite.
