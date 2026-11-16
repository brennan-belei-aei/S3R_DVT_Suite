# archive

Unused legacy code kept for reference. Nothing in the suite imports these files, they are not on any import path, and script discovery skips this folder. Delete them once you no longer need them for reference.

| Path | What it was |
|---|---|
| `analog_port_timing/CollectionBeta.py`, `AnalysisBeta.py` | The original combined Analog Port Timing monolith. Its five tests now live as standalone scripts under `Analog Port Timing/`. These files still import the retired `AE_Commands_PMNT_HF_1XX` module and are not maintained. |
| `AECommandsBEOL.py`, `FilteringExperiment.py`, `MISC/`, `SpectrumAnalyzer/RIGOL_DSA815.py` | Former `AESpecificDependencies/_archive/`: scratch scripts and unused instrument drivers. |