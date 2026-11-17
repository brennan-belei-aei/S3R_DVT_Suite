"""Tektronix 4/5/6 Series MSO helpers shared by the collection scripts."""
from __future__ import annotations

from typing import Any

import numpy as np


def read_waveform(scope: Any, source: str) -> tuple[np.ndarray, np.ndarray, dict]:
    """Transfer one channel (16-bit RIBINARY, full record) and scale it to (times, values, metadata).

    The units in metadata are as the scope reports them (possibly empty). Acquisition state, timeouts
    and file writing stay with the caller.
    """
    scope.write(f"DATA:SOURCE {source}")
    scope.write("DATA:ENCDG RIBINARY")
    scope.write("DATA:WIDTH 2")
    record_length = int(float(scope.query("HORIZONTAL:RECORDLENGTH?")))
    scope.write("DATA:START 1")
    scope.write(f"DATA:STOP {record_length}")
    xincr = float(scope.query("WFMOUTPRE:XINCR?"))
    xzero = float(scope.query("WFMOUTPRE:XZERO?"))
    pt_off = float(scope.query("WFMOUTPRE:PT_OFF?"))
    ymult = float(scope.query("WFMOUTPRE:YMULT?"))
    yzero = float(scope.query("WFMOUTPRE:YZERO?"))
    yoff = float(scope.query("WFMOUTPRE:YOFF?"))
    xunit = scope.query("WFMOUTPRE:XUNIT?").strip().strip('"')
    yunit = scope.query("WFMOUTPRE:YUNIT?").strip().strip('"')
    raw = np.asarray(scope.query_binary_values(
        "CURVE?", datatype="h", is_big_endian=True,
        container=np.array, expect_termination=False), dtype=np.float64)
    if not raw.size:
        raise RuntimeError(f"No waveform points returned for {source}")
    index = np.arange(raw.size, dtype=np.float64)
    times = xzero + (index - pt_off) * xincr
    values = (raw - yoff) * ymult + yzero
    metadata = dict(channel=source, points=int(raw.size), xincr=xincr, xzero=xzero,
                    pt_off=pt_off, ymult=ymult, yzero=yzero, yoff=yoff,
                    xunit=xunit, yunit=yunit)
    return times, values, metadata
