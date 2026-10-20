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


# The riser pad (outline from the user's Riser.stl): a 4 mm rounded square, 0.5 mm tall.
RISER_OUTLINE = np.array(
    [
        (2.0, -0.903), (2.0, 0.903), (1.679, 1.679), (0.903, 2.0), (-0.903, 2.0), (-1.679, 1.679),
        (-2.0, 0.903), (-2.0, -0.903), (-1.679, -1.679), (-0.903, -2.0), (0.903, -2.0), (1.679, -1.679),
    ]
)  # fmt: skip
RISER_HEIGHT = 0.5  # mm
RISER_GAP = 1.0  # mm between the pad and the footprint of the model and supports


def riser_pad(center_xy, z0: float, height: float = RISER_HEIGHT) -> tuple[np.ndarray, np.ndarray]:
    """A closed prism on RISER_OUTLINE: (verts, tris), outward normals, bottom at z0."""
    n = len(RISER_OUTLINE)
    ring = RISER_OUTLINE + np.asarray(center_xy, float)[:2]
    verts = np.vstack(
        [np.column_stack([ring, np.full(n, z0)]), np.column_stack([ring, np.full(n, z0 + height)])]
    )
    tris = []
    for i in range(n):  # outline is counter-clockwise seen from above
        j = (i + 1) % n
        tris += [[i, j, n + j], [i, n + j, n + i]]
    for i in range(1, n - 1):  # caps as fans
        tris += [[0, i + 1, i], [n, n + i, n + i + 1]]
    return verts, np.array(tris)


def add_risers(parts: list[Part]) -> list[Part]:
    """Merge a riser pad into each model part (not the supports), on the supports' floor.

    A slicer drops a floating object onto the bed, which would pull the model away from
    its supports; with a pad of its own touching the bed, each model part stays where it
    is, also after splitting the object into its parts. The pads sit in a row just off
    the corner (min x, min y) of the footprint of everything, so they touch nothing.
    """
    supports = [p for p in parts if p.is_support and len(p.verts)]
    everything = np.concatenate([p.verts for p in parts if len(p.verts)])
    floor = min(float(p.verts[:, 2].min()) for p in supports) if supports else float(everything[:, 2].min())
    lo = everything[:, :2].min(axis=0)
    radius = float(np.abs(RISER_OUTLINE).max())
    out, k = [], 0
    for p in parts:
        if p.is_support or not len(p.verts):
            out.append(p)
            continue
        center = lo - (radius + RISER_GAP) - [k * (2 * radius + RISER_GAP), 0.0]
        pv, pt = riser_pad(center, floor)
        out.append(
            Part(p.name, np.vstack([p.verts, pv]), np.vstack([p.tris, pt + len(p.verts)]), p.is_support)
        )
        k += 1
    return out
