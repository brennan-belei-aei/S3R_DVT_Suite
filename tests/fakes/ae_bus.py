"""Recording fake for AE_Com_Module transports (AETCP / AEB32).

Every create_buffer_cmd call is stored as a Frame so tests can compare the exact
wire-level sequence two command libraries produce. Replies use AE_Com_Module's
shape: [err, byte_count, data...]; a CSR-only reply is [0, 0, csr].
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Frame:
    transport: str
    target: object
    cmd: int
    send_sizes: str
    recv_sizes: str
    args: tuple


Responder = Callable[[Frame], list]


def csr_ok(_frame: Frame) -> list:
    return [0, 0, 0]


@dataclass
class FakeAEBus:
    frames: list[Frame] = field(default_factory=list)
    responders: dict[int, Responder] = field(default_factory=dict)
    default: Responder = csr_ok

    def reply(self, cmd: int, response: list | Responder) -> None:
        """Script the reply for an AE command number."""
        self.responders[cmd] = response if callable(response) else (lambda _f, r=list(response): list(r))

    def wire(self) -> list[tuple]:
        """Frames without transport details, for library-to-library comparison."""
        return [(f.cmd, f.send_sizes, f.recv_sizes, f.args) for f in self.frames]

    def clear(self) -> None:
        self.frames.clear()

    def tcp_factory(self, *args, **kwargs) -> "FakeTransport":
        return FakeTransport(self, "tcp", None)

    def serial_factory(self, port=None, *args, **kwargs) -> "FakeTransport":
        return FakeTransport(self, "serial", port)


class FakeTransport:
    def __init__(self, bus: FakeAEBus, kind: str, target: object):
        self.bus = bus
        self.kind = kind
        self.target = target
        self.closed = False

    def connect(self, address, *args, **kwargs) -> None:
        self.target = address

    def create_buffer_cmd(self, cmd, send_sizes, recv_sizes, *args, **kwargs) -> list:
        frame = Frame(self.kind, self.target, cmd, send_sizes, recv_sizes, tuple(args))
        self.bus.frames.append(frame)
        return list(self.bus.responders.get(cmd, self.bus.default)(frame))

    create_buffer_cmd_fastDAQ = create_buffer_cmd

    def close(self) -> None:
        self.closed = True
