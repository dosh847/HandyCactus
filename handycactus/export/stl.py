# SPDX-License-Identifier: GPL-3.0-or-later
"""Binary STL writer (spec section 11). Units are millimetres, as slicers assume."""

from __future__ import annotations

import numpy as np

from .parts import Part

_RECORD = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


def write_stl(path: str, part: Part) -> int:
    """Write one part as binary STL. Returns the triangle count."""
    tri = part.verts[part.tris]  # (T, 3, 3)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(n, axis=1)
    n = np.where(length[:, None] > 0, n / np.maximum(length, 1e-30)[:, None], 0.0)
    records = np.zeros(len(tri), dtype=_RECORD)
    records["normal"] = n
    records["v"] = tri
    header = f"HandyCactus {part.name}".encode("ascii", "replace")[:80].ljust(80, b" ")
    with open(path, "wb") as f:
        f.write(header)
        f.write(np.uint32(len(tri)).tobytes())
        f.write(records.tobytes())
    return len(tri)


def read_stl(path: str) -> np.ndarray:
    """(T, 3, 3) triangle corners of a binary STL (for tests and round trips)."""
    with open(path, "rb") as f:
        f.read(80)
        count = int(np.frombuffer(f.read(4), dtype="<u4")[0])
        records = np.frombuffer(f.read(count * _RECORD.itemsize), dtype=_RECORD)
    return records["v"].astype(np.float64)
