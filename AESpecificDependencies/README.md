# AESpecificDependencies

AE instrument and command libraries used by the collection scripts. To put this folder on `sys.path`, use `import suite_common.bootstrap`.

| Module | Used by |
|---|---|
| `AE_Com_Module.py` | All AE command modules (AEBus serial/TCP transport) |
| `AECommandsUniversal.py` | **The DUT command adapter.** Every collection script that drives the generator uses it. The DLP Rise/Fall collector uses its `DLP_*`, `pulseEnable`, `setPulseMode`, and `setDLPFreq*` methods. |
| `AE_Commands_7434753.py` | Pulse Sync (generated from the retired PMNT_HF_1XX library; signatures have diverged) |
| `AECommands.py` | Only the `navigator` class is used (Power Profile, Power Linearity) |
| `Lynx.py` | Lynx power meter |
| `SpectrumAnalyzer/` | Spectrum analyzer drivers (Harmonics). There is no common interface yet. |
| `MISC/helperFunctions.py` | Harmonics plotting and report helpers |
| `Command Latency/` | AEBus command-latency collection and analysis GUIs. Both collectors it runs live in `Digital Timing/`. |

Unused legacy code lives in [archive/](../archive/README.md). Standalone utilities that used to sit in `MISC/` (ParamPorter, PPT generators) are in [tools/](../tools/README.md).

Planned work: a base generator class plus per-product subclasses, and a spectrum analyzer interface. See [docs/roadmap/NEXT_STEPS.md](../docs/roadmap/NEXT_STEPS.md).
