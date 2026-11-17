# suite_instruments: Instrument Abstraction Layer

Collection scripts get instruments **by role** from the factory. Vendor code lives in `adapters/`, connections in `transport/`.

```python
from suite_instruments.factory import InstrumentFactory

factory = InstrumentFactory.from_bench()        # active bench profile + PARAMOUNT_* overrides
factory.require(plugin_test)                    # fail early: missing address, adapter, or capability
gen = factory.create("generator")               # AECommandsUniversal.generator (AE bus frames logged)
scope = factory.create("oscilloscope", timeout_ms=60000)   # VisaTransport (SCPI logged)
```

| Part | Contents |
|---|---|
| `interfaces/` | Vendor-neutral roles (`Generator`, `Oscilloscope`, `WaveformGenerator`, `DCSupply`, `SpectrumAnalyzer`, `PowerMeter`, `Tuner`) as structural Protocols, and `ROLE_INTERFACES` mapping each bench role to one. |
| `transport/visa.py` | `VisaTransport`: a drop-in wrapper for a pyvisa resource. It opens with bounded retries, applies `timeout_ms`, logs every write/query/reply (`suite_instruments.visa`), and normalizes errors to `InstrumentTimeout` / `InstrumentError`. It never adds or resends commands. |
| `transport/ae_com.py` | `AeBusTransport`: wraps an `AE_Com_Module` connection and logs every frame (`suite_instruments.aebus`). Frames, replies, and exceptions pass through unchanged, with no resends. |
| `adapters/__init__.py` | The adapter catalog (`ADAPTERS`): which adapter serves which roles, with which capabilities. `DEFAULT_ADAPTERS` gives the per-role default. `ae.7434753` (Pulse Sync's command set) is never a default; Pulse Sync requests it explicitly. `ae.serial_timing` serves `generator_com_port` (serial) and, when requested for `generator`, Ethernet AEBus. `ae.fastdaq` (the Quasi-CW collectors) needs `AECommands_forFastDAQ.py`, which is not in the repository; until it is copied into `adapters/ae/`, opening it raises `InstrumentNotConfigured`. It sets `checks_interface=False` because its callers use `RF_ON`/`RF_OFF`, not the `Generator` interface. `csr_check(response)` is `AECommandsUniversal.csrChecker` for scripts that send raw frames through `gen.gen.create_buffer_cmd`. |
| `adapters/ae/` | AE command modules: `AE_Com_Module`, **`AECommandsUniversal` (the DUT command adapter)**, `AECommandsSerialTimingAdapter` (serial AEBus, single-frame RF ON), `AECommands` (Navigator), `AE_Commands_7434753` (Pulse Sync), `Lynx`. Always import these by their **flat name** after `import suite_common.bootstrap`, never as `suite_instruments.adapters.ae.X`; two names would load two copies. |
| `adapters/keysight/` | Keysight N9320B/N932xC spectrum analyzer drivers (`from suite_instruments.adapters.keysight.spectrum_analyzer import SpectrumAnalyzer`). Programmer's guides are in `docs/instruments/`. |
| `factory.py` | `InstrumentFactory`: `from_bench()`, `check()` / `require()` (roles, addresses, adapters, `plugin.json` capabilities), `create(role, **options)`. |
| `errors.py` | `InstrumentError` and subclasses `InstrumentNotConfigured`, `InstrumentConnectionError`, `InstrumentTimeout`, `InstrumentCapabilityError`. |

## Choosing an adapter per bench

Optional, in `runtime/Bench_Profiles.json`:

```json
"adapters": { "Bench A": { "oscilloscope": "visa.scpi" } }
```

Roles without an entry use `DEFAULT_ADAPTERS`.

## Adding an instrument model

1. Write the driver in `adapters/<vendor>/`, reusing `VisaTransport` for SCPI.
2. Register an `AdapterSpec` in `adapters/__init__.py` with its roles and capabilities.
3. Select it per bench as shown above.

Tests that need a capability list it in `plugin.json` (`"requires": [{"role": "oscilloscope", "capabilities": ["scpi"]}]`).

## Status

Phase 6 is complete: every collection script gets its instruments from `InstrumentFactory`. Each migration was gated by an unchanged SCPI/AE-bus command log (`tests/tools/golden_scpi.py`), and `tests/test_golden_scpi.py::test_no_collection_script_opens_instrument_drivers_directly` keeps it that way. Collection scripts must not import pyvisa or an AE module directly.