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
Transparency_Test_Suite_Launcher.py              product selection; starts the two unified GUIs
├── Paramount_Data_Collection_Unified_GUI.py     bench profile + test sequence; runs each *Collection* script as a child process
└── Paramount_Data_Analysis_Unified_GUI.py       runs each *Analysis* script, then Paramount_PowerPoint_Builder.py
suite_common/                                    shared helpers (bench addresses, output folders, parsing, script discovery)
AESpecificDependencies/                          AE command modules, Lynx, spectrum analyzer drivers
<Test folder>/                                   one Collection + one Analysis script per test
```

- **Child processes:** the GUIs never import test scripts. Each script runs as its own process and receives `PARAMOUNT_*` environment variables.
- **Standalone runs:** every collection script can also run on its own. It then reads addresses from the active bench profile.
- **Address lookup order:** `bench_default()` in `suite_common/bench.py` checks the environment variable first, then the active profile in `Bench_Profiles.json`, then blank.

## Per-PC files (not committed)

| File | Purpose | Template |
|---|---|---|
| `Bench_Profiles.json` | Instrument addresses per bench | `Bench_Profiles.example.json` |
| `Unified_Collection_GUI_Settings.json` | Test selection/order, script paths | `*.example.json` |
| `Unified_Analysis_GUI_Settings.json` | Analysis selection, dataset/presentation defaults | `*.example.json` |
| `AE_Transparency_Testing_Suite_Settings.json` | Launcher script paths, selected product | `*.example.json` |

Script paths are stored relative to the repo. If a saved path no longer exists, the GUI finds the script again by filename.

## Adding a test

1. Create `<Test Name>/<Name>_Collection_GUI.py` (and an `_Analysis_GUI.py`). Start the collection script with:
   ```python
   sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
   import suite_common.bootstrap  # noqa: F401
   from suite_common.bench import bench_default
   ```
2. Get every instrument address with `bench_default("<equipment key>")`. Never hardcode IPs, serials, or COM ports.
3. Register the test in `TESTS` in `Paramount_Data_Collection_Unified_GUI.py`, and in `BUILTIN_ANALYSES` in `Paramount_Data_Analysis_Unified_GUI.py`.

## Checks

```powershell
.venv\Scripts\python Paramount_Data_Collection_Unified_GUI.py --self-test
.venv\Scripts\python -m pyflakes suite_common *.py
```

## Docs

See [docs/README.md](docs/README.md) for the documentation index: project memory, AI workflow, review write-ups, roadmap, and legacy notes. AI agents should start at [AGENTS.md](AGENTS.md). `AESpecificDependencies/_archive/` holds unused legacy scripts kept for reference; nothing imports them.
