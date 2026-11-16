"""Instrument Abstraction Layer.

    interfaces/   vendor-neutral roles (Generator, Oscilloscope, WaveformGenerator, ...)
    transport/    connections: VISA (SCPI) and AE bus, with logging, timeouts, error normalization
    adapters/     vendor/model code: ae/ (AE command modules, imported flat), keysight/, ...
    factory.py    InstrumentFactory: bench profile role -> configured adapter instance

Collection scripts ask the factory for an instrument by role; SCPI and vendor code stay in
adapters. See README.md.
"""
