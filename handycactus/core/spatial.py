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

    def is_inside(self, points: np.ndarray, rays: int = 3) -> np.ndarray:
        """(N, 3) points -> (N,) bool. `rays` = 1 trades robustness at edges for speed."""
        ...


def in_bounds(points: np.ndarray, lo: np.ndarray, hi: np.ndarray, pad: float = 1e-6) -> np.ndarray:
    return np.all((points >= lo - pad) & (points <= hi + pad), axis=1)


class NumpyInsideTester:
    """Inside test against one target's triangles by ray winding number: along a ray, surface
    exits minus entries is positive inside (majority of 3 skewed rays). Works for closed
    meshes made of overlapping shells, where crossing parity does not."""

    def __init__(self, verts: np.ndarray, tris: np.ndarray):
        self.bounds = (verts.min(axis=0), verts.max(axis=0)) if len(verts) else None
        self._a = verts[tris[:, 0]]
        self._e1 = verts[tris[:, 1]] - self._a
        self._e2 = verts[tris[:, 2]] - self._a
        self._n = np.cross(self._e1, self._e2)
        self.bounds_min = verts.min(axis=0) if len(verts) else np.zeros(3)
        self.bounds_max = verts.max(axis=0) if len(verts) else np.zeros(3)

    def is_inside(self, points: np.ndarray, rays: int = 3) -> np.ndarray:
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        result = np.zeros(len(points), dtype=bool)
        candidates = np.flatnonzero(in_bounds(points, self.bounds_min, self.bounds_max))
        for start in range(0, len(candidates), 256):
            idx = candidates[start : start + 256]
            votes = sum(self._winding(points[idx], d) > 0 for d in PARITY_DIRECTIONS[:rays])
            result[idx] = votes * 2 > rays
        return result

    def _winding(self, origins: np.ndarray, direction: np.ndarray) -> np.ndarray:
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
        exits = np.sign(self._n @ direction)  # +1 leaving a shell, -1 entering
        return (hit * exits[None, :]).sum(axis=1)


class Obstacles(Protocol):
    """Distance and ray queries against the whole target set (world mm)."""

    def nearest_distance(self, points: np.ndarray) -> np.ndarray:
        """(N, 3) -> (N,) distance to the closest surface point."""
        ...

    def ray_distance(self, origins: np.ndarray, directions: np.ndarray, max_distance: float) -> np.ndarray:
        """(N, 3), (N, 3) unit -> (N,) distance to the first hit, inf if none within max_distance."""
        ...

    def ray_hit(
        self, origins: np.ndarray, directions: np.ndarray, max_distance: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Like ray_distance, plus the unit surface normal at each hit ((N, 3), zero if none)."""
        ...


class NumpyObstacles:
    """Brute-force Obstacles over triangles; exact, for tests and small meshes."""

    def __init__(self, verts: np.ndarray, tris: np.ndarray):
        self.bounds = (verts.min(axis=0), verts.max(axis=0)) if len(verts) else None
        self._a = verts[tris[:, 0]]
        self._b = verts[tris[:, 1]]
        self._c = verts[tris[:, 2]]

    def nearest_distance(self, points: np.ndarray) -> np.ndarray:
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        out = np.empty(len(points))
        for start in range(0, len(points), 64):
            p = points[start : start + 64]
            q = closest_point_on_triangles(p[:, None, :], self._a[None], self._b[None], self._c[None])
            out[start : start + 64] = np.linalg.norm(q - p[:, None, :], axis=2).min(axis=1)
        return out

    def ray_distance(self, origins: np.ndarray, directions: np.ndarray, max_distance: float) -> np.ndarray:
        return self.ray_hit(origins, directions, max_distance)[0]

    def ray_hit(self, origins, directions, max_distance):
        origins = np.asarray(origins, dtype=np.float64).reshape(-1, 3)
        directions = np.asarray(directions, dtype=np.float64).reshape(-1, 3)
        e1, e2 = self._b - self._a, self._c - self._a
        face_n = np.cross(e1, e2)
        face_n /= np.maximum(np.linalg.norm(face_n, axis=1), 1e-12)[:, None]
        out = np.full(len(origins), np.inf)
        normals = np.zeros((len(origins), 3))
        for i, (o, d) in enumerate(zip(origins, directions, strict=True)):
            pvec = np.cross(d, e2)
            det = np.einsum("ij,ij->i", e1, pvec)
            ok = np.abs(det) > 1e-12
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tvec = o - self._a
            u = np.einsum("ij,ij->i", tvec, pvec) * inv
            qvec = np.cross(tvec, e1)
            v = (qvec @ d) * inv
            t = np.einsum("ij,ij->i", qvec, e2) * inv
            hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9) & (t <= max_distance)
            if hit.any():
                j = np.flatnonzero(hit)[np.argmin(t[hit])]
                out[i], normals[i] = t[j], face_n[j]
        return out, normals

    def column_occupancy(self, xy: np.ndarray, z: np.ndarray) -> np.ndarray:
        """(K, N) bool: is (xy[n], z[k]) inside the mesh (winding number of a vertical ray
        > 0; overlapping shells count once)."""
        return column_occupancy_from_triangles(self._a, self._b, self._c, xy, z)


def column_occupancy_from_triangles(a, b, c, xy, z) -> np.ndarray:
    """Vertical-ray winding numbers: for each column, every triangle whose XY projection
    holds it crosses it at the barycentric height; a face pointing down is an entry going
    up (+1), up an exit (-1). Inside where the running sum below a height is > 0."""
    xy = np.asarray(xy, np.float64).reshape(-1, 2)
    z = np.asarray(z, np.float64)
    out = np.zeros((len(z), len(xy)), dtype=bool)
    nz = np.cross(b - a, c - a)[:, 2]
    keep = np.abs(nz) > 1e-12  # vertical faces are never crossed by a vertical ray
    a, b, c, nz = a[keep], b[keep], c[keep], nz[keep]
    lo = np.minimum(np.minimum(a[:, :2], b[:, :2]), c[:, :2])
    hi = np.maximum(np.maximum(a[:, :2], b[:, :2]), c[:, :2])
    for start in range(0, len(xy), 256):
        p = xy[start : start + 256]
        cand = np.all((p[:, None, :] >= lo[None] - 1e-12) & (p[:, None, :] <= hi[None] + 1e-12), axis=2)
        ci, ti = np.nonzero(cand)
        if len(ci) == 0:
            continue
        pa, pb, pc, q = a[ti, :2], b[ti, :2], c[ti, :2], p[ci]
        d = nz[ti]  # twice the signed XY area
        w0 = ((pb[:, 0] - q[:, 0]) * (pc[:, 1] - q[:, 1]) - (pb[:, 1] - q[:, 1]) * (pc[:, 0] - q[:, 0])) / d
        w1 = ((pc[:, 0] - q[:, 0]) * (pa[:, 1] - q[:, 1]) - (pc[:, 1] - q[:, 1]) * (pa[:, 0] - q[:, 0])) / d
        w2 = 1.0 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        ci, ti, w0, w1, w2 = ci[inside], ti[inside], w0[inside], w1[inside], w2[inside]
        hz = w0 * a[ti, 2] + w1 * b[ti, 2] + w2 * c[ti, 2]
        sign = np.where(nz[ti] < 0, 1, -1)  # downward face: entering when going up
        for k, zk in enumerate(z):
            wind = np.zeros(len(p), np.int64)
            below = hz < zk
            np.add.at(wind, ci[below], sign[below])
            out[k, start : start + len(p)] |= wind > 0
    return out


def closest_point_on_triangles(p, a, b, c):
    """Closest points on triangles (a, b, c) to p; all broadcastable (..., 3). Ericson 5.1.5."""
    ab, ac, ap = b - a, c - a, p - a
    d1 = np.einsum("...j,...j->...", ab, ap)
    d2 = np.einsum("...j,...j->...", ac, ap)
    bp = p - b
    d3 = np.einsum("...j,...j->...", ab, bp)
    d4 = np.einsum("...j,...j->...", ac, bp)
    cp = p - c
    d5 = np.einsum("...j,...j->...", ab, cp)
    d6 = np.einsum("...j,...j->...", ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = 1.0 / (va + vb + vc)
        v = vb * denom
        w = vc * denom
        result = a + ab * v[..., None] + ac * w[..., None]  # inside the face

        t_ab = d1 / (d1 - d3)
        t_ac = d2 / (d2 - d6)
        t_bc = (d4 - d3) / ((d4 - d3) + (d5 - d6))
    shape = np.broadcast_shapes(d1.shape, a.shape[:-1])

    def put(mask, value):
        nonlocal result
        mask = np.broadcast_to(mask, shape)
        result = np.where(mask[..., None], np.broadcast_to(value, result.shape), result)

    # Apply regions from least to most specific so vertex regions win.
    put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), b + (c - b) * np.nan_to_num(t_bc)[..., None])
    put((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + ac * np.nan_to_num(t_ac)[..., None])
    put((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + ab * np.nan_to_num(t_ab)[..., None])
    put((d6 >= 0) & (d5 <= d6), c)
    put((d3 >= 0) & (d4 <= d3), b)
    put((d1 <= 0) & (d2 <= 0), a)
    return result
