# SPDX-License-Identifier: GPL-3.0-or-later
"""Named triangle meshes to export, in world-space millimetres."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Part:
    name: str
    verts: np.ndarray  # (V, 3) float, mm
    tris: np.ndarray  # (T, 3) int
    is_support: bool = False

    def __post_init__(self):
        self.verts = np.asarray(self.verts, dtype=np.float64).reshape(-1, 3)
        self.tris = np.asarray(self.tris, dtype=np.int64).reshape(-1, 3)


def merge(parts: list[Part], name: str = "merged") -> Part:
    """All parts' triangles in one mesh."""
    verts, tris, offset = [], [], 0
    for p in parts:
        verts.append(p.verts)
        tris.append(p.tris + offset)
        offset += len(p.verts)
    if not parts:
        return Part(name, np.zeros((0, 3)), np.zeros((0, 3), np.int64))
    return Part(name, np.concatenate(verts), np.concatenate(tris))


def drop_degenerate(part: Part) -> Part:
    """Without triangles that repeat a vertex index or have no area (slicers reject or
    warn about them), and without unused vertices."""
    t = part.tris
    if len(t) == 0:
        return part
    distinct = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    v = part.verts
    area2 = np.linalg.norm(np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]]), axis=1)
    t = t[distinct & (area2 > 1e-12)]
    used = np.unique(t)
    remap = np.full(len(v), -1, np.int64)
    remap[used] = np.arange(len(used))
    return Part(part.name, v[used], remap[t], part.is_support)
