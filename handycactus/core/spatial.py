# SPDX-License-Identifier: GPL-3.0-or-later
"""Spatial queries the core needs, behind a small protocol.

Inside Blender these are backed by mathutils.bvhtree (blender/spatial_bvh.py).
The numpy implementations here are exact but brute force; they serve tests
and small meshes.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

# Three skewed directions, so a ray rarely runs exactly along an edge.
PARITY_DIRECTIONS = np.array(
    [
        [0.3127, 0.5419, 0.7801],
        [-0.8133, 0.1871, 0.5509],
        [0.2269, -0.9017, -0.3683],
    ]
)
PARITY_DIRECTIONS /= np.linalg.norm(PARITY_DIRECTIONS, axis=1)[:, None]


class InsideTester(Protocol):
    """Answers whether points are inside one closed target."""

    bounds_min: np.ndarray  # (3,)
    bounds_max: np.ndarray  # (3,)

    def is_inside(self, points: np.ndarray) -> np.ndarray:
        """(N, 3) points -> (N,) bool."""
        ...


def in_bounds(points: np.ndarray, lo: np.ndarray, hi: np.ndarray, pad: float = 1e-6) -> np.ndarray:
    return np.all((points >= lo - pad) & (points <= hi + pad), axis=1)


class NumpyInsideTester:
    """Ray-parity inside test against one target's triangles (majority of 3 rays)."""

    def __init__(self, verts: np.ndarray, tris: np.ndarray):
        self._a = verts[tris[:, 0]]
        self._e1 = verts[tris[:, 1]] - self._a
        self._e2 = verts[tris[:, 2]] - self._a
        self.bounds_min = verts.min(axis=0) if len(verts) else np.zeros(3)
        self.bounds_max = verts.max(axis=0) if len(verts) else np.zeros(3)

    def is_inside(self, points: np.ndarray) -> np.ndarray:
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        result = np.zeros(len(points), dtype=bool)
        candidates = np.flatnonzero(in_bounds(points, self.bounds_min, self.bounds_max))
        for start in range(0, len(candidates), 256):
            idx = candidates[start : start + 256]
            votes = sum(self._odd_crossings(points[idx], d) for d in PARITY_DIRECTIONS)
            result[idx] = votes >= 2
        return result

    def _odd_crossings(self, origins: np.ndarray, direction: np.ndarray) -> np.ndarray:
        # Möller–Trumbore, origins (P, 3) against all triangles (T, 3).
        pvec = np.cross(direction, self._e2)  # (T, 3)
        det = np.einsum("ij,ij->i", self._e1, pvec)  # (T,)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tvec = origins[:, None, :] - self._a[None, :, :]  # (P, T, 3)
        u = np.einsum("ptj,tj->pt", tvec, pvec) * inv
        qvec = np.cross(tvec, self._e1[None, :, :])
        v = np.einsum("ptj,j->pt", qvec, direction) * inv
        t = np.einsum("ptj,tj->pt", qvec, self._e2) * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
        return np.count_nonzero(hit, axis=1) % 2 == 1
