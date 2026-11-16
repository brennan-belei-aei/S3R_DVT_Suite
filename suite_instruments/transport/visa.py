"""VISA transport: a drop-in wrapper around a pyvisa resource.

It sends exactly what the caller sends (no added commands, no automatic resends, so SCPI
sequences are unchanged), and adds:
  * connection management: open with bounded retries, idempotent close;
  * timeouts: optional timeout_ms applied at open;
  * logging: every write/query/reply at DEBUG on the "suite_instruments.visa" logger;
  * error normalization: pyvisa errors become InstrumentTimeout / InstrumentError.

Attributes it does not define (timeout, read_termination, chunk_size, ...) pass through to the
underlying resource, so code written against a pyvisa resource keeps working.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from suite_instruments.errors import InstrumentConnectionError, InstrumentError, InstrumentTimeout

LOG = logging.getLogger("suite_instruments.visa")
_OWN = {"_resource", "_resource_name", "_role", "_manager", "_owns_manager", "_closed"}


def resource_manager(*args, **kwargs) -> Any:
    """A pyvisa ResourceManager, for scripts that share one manager across several instruments."""
    import pyvisa
    return pyvisa.ResourceManager(*args, **kwargs)


def _is_timeout(error: Exception) -> bool:
    text = f"{type(error).__name__} {error}".lower()
    return "timeout" in text or "tmo" in text


class VisaTransport:
    def __init__(
        self,
        resource_name: str,
        *,
        role: str = "",
        timeout_ms: int | None = None,
        open_attempts: int = 2,
        retry_delay_s: float = 0.5,
        resource_manager: Any = None,
        resource_manager_factory: Callable[[], Any] | None = None,
        open_kwargs: dict | None = None,
    ):
        object.__setattr__(self, "_resource_name", resource_name)
        object.__setattr__(self, "_role", role)
        object.__setattr__(self, "_closed", False)
        if resource_manager is None:
            if resource_manager_factory is None:
                import pyvisa
                resource_manager_factory = pyvisa.ResourceManager
            resource_manager = resource_manager_factory()
            object.__setattr__(self, "_owns_manager", True)
        else:
            object.__setattr__(self, "_owns_manager", False)
        object.__setattr__(self, "_manager", resource_manager)
        last_error: Exception | None = None
        for attempt in range(1, max(1, open_attempts) + 1):
            try:
                resource = resource_manager.open_resource(resource_name, **(open_kwargs or {}))
                break
            except Exception as error:  # vendor errors vary; normalized below
                last_error = error
                LOG.warning("open %s attempt %d failed: %s", resource_name, attempt, error)
                if attempt < open_attempts:
                    time.sleep(retry_delay_s)
        else:
            raise InstrumentConnectionError(f"could not open: {last_error}", role=role, resource=resource_name) from last_error
        if timeout_ms is not None:
            resource.timeout = timeout_ms
        object.__setattr__(self, "_resource", resource)
        LOG.info("opened %s %s", role or "instrument", resource_name)

    # -- pass-through for everything not wrapped below ------------------------------------
    def __getattr__(self, name: str) -> Any:
        return getattr(self._resource, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self._resource, name, value)

    @property
    def resource(self) -> Any:
        """The underlying pyvisa resource (escape hatch)."""
        return self._resource

    # -- wrapped I/O ------------------------------------------------------------------------
    def _call(self, operation: str, payload: Any, *args, **kwargs) -> Any:
        if self._closed:
            raise InstrumentError(f"{operation} after close", role=self._role, resource=self._resource_name)
        LOG.debug("%s %s> %s %r", self._role, self._resource_name, operation, payload)
        try:
            method = getattr(self._resource, operation)
            result = method(payload, *args, **kwargs) if payload is not None else method(*args, **kwargs)
        except InstrumentError:
            raise
        except Exception as error:
            kind = InstrumentTimeout if _is_timeout(error) else InstrumentError
            raise kind(f"{operation} {payload!r} failed: {error}", role=self._role, resource=self._resource_name) from error
        if result is not None and operation.startswith(("query", "read")):
            LOG.debug("%s %s< %r", self._role, self._resource_name, result)
        return result

    def write(self, command: str, *args, **kwargs):
        return self._call("write", command, *args, **kwargs)

    def query(self, command: str, *args, **kwargs) -> str:
        return self._call("query", command, *args, **kwargs)

    def read(self, *args, **kwargs) -> str:
        return self._call("read", None, *args, **kwargs)

    def query_ascii_values(self, command: str, *args, **kwargs):
        return self._call("query_ascii_values", command, *args, **kwargs)

    def query_binary_values(self, command: str, *args, **kwargs):
        return self._call("query_binary_values", command, *args, **kwargs)

    def write_raw(self, data: bytes, *args, **kwargs):
        return self._call("write_raw", data, *args, **kwargs)

    def read_raw(self, *args, **kwargs) -> bytes:
        return self._call("read_raw", None, *args, **kwargs)

    def clear(self) -> None:
        return self._call("clear", None)

    def close(self) -> None:
        if self._closed:
            return
        object.__setattr__(self, "_closed", True)
        try:
            self._resource.close()
        finally:
            if self._owns_manager and hasattr(self._manager, "close"):
                self._manager.close()
            LOG.info("closed %s %s", self._role or "instrument", self._resource_name)

    def __enter__(self) -> "VisaTransport":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
