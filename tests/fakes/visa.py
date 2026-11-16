"""Recording fake for pyvisa resource managers and instruments (scope, AWG, analyzer, supply).

Each instrument records its SCPI traffic so tests can diff it against a golden log.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeInstrument:
    resource: str
    replies: dict[str, str] = field(default_factory=dict)
    log: list[tuple[str, str]] = field(default_factory=list)
    timeout: int = 2000
    read_termination: str | None = None
    write_termination: str | None = None
    closed: bool = False

    def write(self, command: str) -> int:
        self.log.append(("write", command))
        return len(command)

    def query(self, command: str, *args, **kwargs) -> str:
        self.log.append(("query", command))
        return self._reply(command)

    def read(self, *args, **kwargs) -> str:
        self.log.append(("read", ""))
        return ""

    def query_ascii_values(self, command: str, *args, **kwargs) -> list[float]:
        self.log.append(("query_ascii_values", command))
        text = self._reply(command)
        return [float(v) for v in text.split(",") if v.strip()]

    def query_binary_values(self, command: str, *args, **kwargs) -> list:
        self.log.append(("query_binary_values", command))
        return []

    def write_raw(self, data: bytes) -> int:
        self.log.append(("write_raw", data.decode(errors="replace")))
        return len(data)

    def read_raw(self, *args, **kwargs) -> bytes:
        self.log.append(("read_raw", ""))
        return b""

    def clear(self) -> None:
        self.log.append(("clear", ""))

    def close(self) -> None:
        self.closed = True

    def _reply(self, command: str) -> str:
        key = command.strip().upper()
        for pattern, reply in self.replies.items():
            if key.startswith(pattern.upper()):
                return reply
        if key.startswith("*IDN?"):
            return f"FAKE,INSTRUMENT,{self.resource},0"
        if key.startswith("*OPC?"):
            return "1"
        return "0"


@dataclass
class FakeResourceManager:
    instruments: dict[str, FakeInstrument] = field(default_factory=dict)
    replies: dict[str, dict[str, str]] = field(default_factory=dict)
    closed: bool = False

    def open_resource(self, resource: str, *args, **kwargs) -> FakeInstrument:
        inst = self.instruments.get(resource)
        if inst is None:
            inst = FakeInstrument(resource, dict(self.replies.get(resource, {})))
            self.instruments[resource] = inst
        return inst

    def list_resources(self, *args, **kwargs) -> tuple[str, ...]:
        return tuple(self.instruments)

    def close(self) -> None:
        self.closed = True
