"""AE bus transport: wraps an AE_Com_Module connection (AETCP or AEB32).

Frames are passed through untouched: same create_buffer_cmd arguments, same return value, same
exceptions, no resends (AE bus commands such as RF ON must never be repeated implicitly). It adds
logging of every frame on the "suite_instruments.aebus" logger; connection failures are normalized
by the adapter that opens the connection. Anything else passes through to the wrapped object.
"""
from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger("suite_instruments.aebus")


class AeBusTransport:
    def __init__(self, connection: Any, *, role: str = "", address: str = ""):
        self._connection = connection
        self._role = role
        self._address = address
        self.frames_sent = 0  # frames that completed (a reply was returned)

    @property
    def connection(self) -> Any:
        return self._connection

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def _frame(self, method: str, cmd, send_sizes, recv_sizes, *args, **kwargs):
        LOG.debug("%s %s> %s cmd=%s send=%s recv=%s args=%r", self._role, self._address, method, cmd, send_sizes, recv_sizes, args)
        reply = getattr(self._connection, method)(cmd, send_sizes, recv_sizes, *args, **kwargs)
        self.frames_sent += 1
        LOG.debug("%s %s< %r", self._role, self._address, reply)
        return reply

    def create_buffer_cmd(self, cmd, send_sizes, recv_sizes, *args, **kwargs):
        return self._frame("create_buffer_cmd", cmd, send_sizes, recv_sizes, *args, **kwargs)

    def create_buffer_cmd_fastDAQ(self, cmd, send_sizes, recv_sizes, *args, **kwargs):
        return self._frame("create_buffer_cmd_fastDAQ", cmd, send_sizes, recv_sizes, *args, **kwargs)

    def close(self) -> None:
        LOG.info("closing %s %s", self._role or "AE bus", self._address)
        self._connection.close()


def wrap_connection_attribute(instrument: Any, attribute: str, *, role: str, address: str) -> Any:
    """Replace instrument.<attribute> (its AE_Com_Module connection) with a logging AeBusTransport."""
    connection = getattr(instrument, attribute)
    if not isinstance(connection, AeBusTransport):
        setattr(instrument, attribute, AeBusTransport(connection, role=role, address=address))
    return instrument
