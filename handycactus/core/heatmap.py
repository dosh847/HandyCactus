# SPDX-License-Identifier: GPL-3.0-or-later
"""Overhang heatmap geometry (spec section 8). numpy only, no BVH queries.

Produces unrolled triangles (3 vertices per face) with one colour per face:
- faces that need support, ramped from yellow (just past the overhang
  angle) to red (horizontal ceiling);
- when contacts are known, faces farther than `tip_spacing` from every
  contact are drawn in the gap colour instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .detection import PAINT_THRESHOLD, needs_support, overhang_severity
from .mesh_data import MeshData
from .params import Params

LOW = np.array([1.0, 0.85, 0.1, 0.55])
HIGH = np.array([0.9, 0.1, 0.05, 0.65])
GAP = np.array([1.0, 0.0, 0.85, 0.8])


@dataclass
class Heatmap:
    positions: np.ndarray  # (3F, 3) mm
    colors: np.ndarray  # (3F, 4) RGBA
    face_count: int
    gap_face_count: int


def build(
    mesh: MeshData,
    params: Params,
    contacts: np.ndarray | None = None,
    support_faces: np.ndarray | None = None,
) -> Heatmap:
    """`contacts` (K, 3) mm turns on coverage-gap colouring. `support_faces` (a detection
    result's mask) replaces the angle-only estimate, e.g. leaving out buried faces."""
    if support_faces is None:
        support_faces = estimate_support_faces(mesh, params)
    faces = np.flatnonzero(support_faces)

    t = np.clip(overhang_severity(mesh.face_normals[faces], params.overhang_angle), 0.0, 1.0)
    colors = LOW + (HIGH - LOW) * t[:, None]
    gaps = 0
    if contacts is not None and len(faces):
        gap = ~within(mesh.face_centroids[faces], np.asarray(contacts, dtype=np.float64), params.tip_spacing)
        colors[gap] = GAP
        gaps = int(gap.sum())

    # Drawn exactly on the surface; the overlay shader biases depth (no z-fighting).
    corners = mesh.verts[mesh.tris[faces]]  # (F, 3, 3)
    return Heatmap(
        positions=corners.reshape(-1, 3),
        colors=np.repeat(colors, 3, axis=0),
        face_count=len(faces),
        gap_face_count=gaps,
    )


def estimate_support_faces(mesh: MeshData, params: Params) -> np.ndarray:
    """Faces that need support by overhang angle (and HC_Force paint) alone. numpy only:
    unlike detection it cannot see buried faces."""
    overhang = needs_support(mesh.face_normals, params.overhang_angle) & (mesh.face_areas > 0)
    forced = mesh.force_weights[mesh.tris].mean(axis=1) > PAINT_THRESHOLD if len(mesh.tris) else overhang
    return (overhang | forced) & (mesh.face_min_z >= params.bed_ignore_height)


def within(points: np.ndarray, targets: np.ndarray, radius: float) -> np.ndarray:
    """(N,) bool: is any target within `radius` of each point. Spatial hash, radius-sized cells."""
    out = np.zeros(len(points), dtype=bool)
    if len(points) == 0 or len(targets) == 0 or radius <= 0:
        return out
    t_cell = np.floor(targets / radius).astype(np.int64)
    p_cell = np.floor(points / radius).astype(np.int64)
    # One int64 key per cell (np.unique(axis=0) is slow); one cell of margin keeps
    # every neighbour offset inside the key range.
    lo = np.minimum(t_cell.min(axis=0), p_cell.min(axis=0)) - 1
    span = np.maximum(t_cell.max(axis=0), p_cell.max(axis=0)) - lo + 2

    def key(c):
        c = c - lo
        return (c[..., 0] * span[1] + c[..., 1]) * span[2] + c[..., 2]

    t_key = key(t_cell)
    t_order = np.argsort(t_key, kind="stable")
    t_sorted, targets_sorted = t_key[t_order], targets[t_order]
    p_key = key(p_cell)
    p_keys, p_inverse = np.unique(p_key, return_inverse=True)
    order = np.argsort(p_inverse, kind="stable")
    starts = np.searchsorted(p_inverse[order], np.arange(len(p_keys) + 1))
    offsets = np.array(
        [(dx * span[1] + dy) * span[2] + dz for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    )
    r2 = radius * radius
    neighbour_keys = p_keys[:, None] + offsets[None, :]  # (C, 27)
    first = np.searchsorted(t_sorted, neighbour_keys, side="left")
    last = np.searchsorted(t_sorted, neighbour_keys, side="right")
    has_any = (last > first).any(axis=1)
    for k in np.flatnonzero(has_any):
        cand = np.concatenate([targets_sorted[a:b] for a, b in zip(first[k], last[k], strict=True) if b > a])
        idx = order[starts[k] : starts[k + 1]]
        d2 = ((points[idx, None, :] - cand[None, :, :]) ** 2).sum(axis=2)
        out[idx] = (d2 <= r2).any(axis=1)
    return out
