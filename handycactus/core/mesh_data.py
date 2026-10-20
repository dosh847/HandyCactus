# SPDX-License-Identifier: GPL-3.0-or-later
"""Combined triangle mesh of a target set (spec section 6.1).

All coordinates are millimetres in world space with the bed at Z = 0.
Every array is numpy; nothing here imports bpy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

# Fraction of a target's edges that may be open or shared by more than two faces
# while it still counts as closed for the inside test (138 of 149k on a decimated sculpt).
NEARLY_CLOSED = 0.005


@dataclass
class TargetMesh:
    """One target's triangles, as read from Blender (or built by a test)."""

    name: str
    verts: np.ndarray  # (V, 3) float, mm, world space
    tris: np.ndarray  # (T, 3) int, indices into verts
    # Optional paint masks (spec 6.2 step 4), one weight per vertex.
    force_weights: np.ndarray | None = None
    block_weights: np.ndarray | None = None


@dataclass
class MeshData:
    """All targets concatenated, with derived per-face and per-vertex data."""

    names: list[str]
    verts: np.ndarray  # (V, 3) float64
    tris: np.ndarray  # (T, 3) int64
    face_target: np.ndarray  # (T,) int, index into names
    vert_target: np.ndarray  # (V,) int
    force_weights: np.ndarray  # (V,) float, 0 where unpainted
    block_weights: np.ndarray  # (V,) float
    face_normals: np.ndarray = field(init=False)  # (T, 3) unit
    face_areas: np.ndarray = field(init=False)  # (T,)
    face_centroids: np.ndarray = field(init=False)  # (T, 3)
    face_min_z: np.ndarray = field(init=False)  # (T,)

    def __post_init__(self):
        a, b, c = (self.verts[self.tris[:, i]] for i in range(3))
        cross = _cross(b - a, c - a)
        length = np.sqrt(np.einsum("ij,ij->i", cross, cross))
        self.face_areas = 0.5 * length
        with np.errstate(invalid="ignore", divide="ignore"):
            normals = cross / length[:, None]
        # Degenerate triangles get a zero normal, so they never need support.
        self.face_normals = np.nan_to_num(normals)
        self.face_centroids = (a + b + c) / 3.0
        self.face_min_z = np.minimum(np.minimum(a[:, 2], b[:, 2]), c[:, 2])

    @property
    def target_count(self) -> int:
        return len(self.names)

    def target_face_mask(self, target: int) -> np.ndarray:
        return self.face_target == target

    def vertex_normals(self) -> np.ndarray:
        """Area-weighted vertex normals (unit; zero for isolated vertices)."""
        weighted = self.face_normals * self.face_areas[:, None]
        out = np.zeros_like(self.verts)
        for i in range(3):
            np.add.at(out, self.tris[:, i], weighted)
        length = np.linalg.norm(out, axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.nan_to_num(out / length[:, None])

    def half_edges(self) -> np.ndarray:
        """Every triangle edge, (3T, 2), sorted per row; shared edges repeat."""
        e = np.concatenate([self.tris[:, [0, 1]], self.tris[:, [1, 2]], self.tris[:, [2, 0]]])
        e.sort(axis=1)
        return e

    def edges(self) -> tuple[np.ndarray, np.ndarray]:
        """Unique undirected edges and, per edge, how many faces use it.

        Returns (edges (E, 2) sorted per row, counts (E,)).
        """
        return _unique_edges(self.half_edges(), len(self.verts))

    def face_edge_pairs(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Pairs of faces sharing an edge.

        Returns (face_a, face_b, edge_vertices (P, 2)). Edges used by more than
        two faces (non-manifold) produce one pair per face combination with
        the first face.
        """
        e = self.half_edges()
        faces = np.tile(np.arange(len(self.tris)), 3)
        keys = e[:, 0] * np.int64(len(self.verts)) + e[:, 1]
        order = np.argsort(keys, kind="stable")
        e, faces, keys = e[order], faces[order], keys[order]
        same = keys[1:] == keys[:-1]
        # Group starts: each face after the first in a run pairs with the run's first face.
        run_start = np.concatenate([[0], np.flatnonzero(~same) + 1])
        run_id = np.repeat(np.arange(len(run_start)), np.diff(np.append(run_start, len(e))))
        first = run_start[run_id]
        is_follower = np.arange(len(e)) != first
        return faces[first[is_follower]], faces[is_follower], e[is_follower]

    def is_nearly_closed(self, target: int) -> bool:
        """Closed but for pinholes: at most NEARLY_CLOSED of its edges are used by other
        than two faces. Decimated sculpts often have a few; the ray winding-number inside
        test (majority of 3 rays) is not thrown off by them."""
        n_edges = 1.5 * np.count_nonzero(self.face_target == target)
        return self.non_manifold_edge_count(target) <= NEARLY_CLOSED * n_edges

    def non_manifold_edge_count(self, target: int) -> int:
        """Edges of a target used by other than exactly two faces (0 = closed)."""
        sub = self.tris[self.face_target == target]
        e = np.concatenate([sub[:, [0, 1]], sub[:, [1, 2]], sub[:, [2, 0]]])
        e.sort(axis=1)
        _, counts = _unique_edges(e, len(self.verts))
        return int(np.count_nonzero(counts != 2))


def _unique_edges(e: np.ndarray, n_verts: int) -> tuple[np.ndarray, np.ndarray]:
    # One int64 key per edge is far faster than np.unique(axis=0).
    keys, counts = np.unique(e[:, 0] * np.int64(n_verts) + e[:, 1], return_counts=True)
    return np.stack([keys // n_verts, keys % n_verts], axis=1), counts


def _cross(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    # Several times faster than np.cross for (N, 3) arrays.
    out = np.empty_like(u)
    out[:, 0] = u[:, 1] * v[:, 2] - u[:, 2] * v[:, 1]
    out[:, 1] = u[:, 2] * v[:, 0] - u[:, 0] * v[:, 2]
    out[:, 2] = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    return out


def combine(targets: Sequence[TargetMesh]) -> MeshData:
    verts, tris, face_target, vert_target, force, block = [], [], [], [], [], []
    offset = 0
    for i, t in enumerate(targets):
        v = np.asarray(t.verts, dtype=np.float64).reshape(-1, 3)
        f = np.asarray(t.tris, dtype=np.int64).reshape(-1, 3)
        verts.append(v)
        tris.append(f + offset)
        face_target.append(np.full(len(f), i, dtype=np.int64))
        vert_target.append(np.full(len(v), i, dtype=np.int64))
        force.append(_weights(t.force_weights, len(v)))
        block.append(_weights(t.block_weights, len(v)))
        offset += len(v)

    def cat(parts, shape, dtype):
        return np.concatenate(parts) if parts else np.zeros(shape, dtype=dtype)

    return MeshData(
        names=[t.name for t in targets],
        verts=cat(verts, (0, 3), np.float64),
        tris=cat(tris, (0, 3), np.int64),
        face_target=cat(face_target, (0,), np.int64),
        vert_target=cat(vert_target, (0,), np.int64),
        force_weights=cat(force, (0,), np.float64),
        block_weights=cat(block, (0,), np.float64),
    )


def _weights(w, n: int) -> np.ndarray:
    if w is None:
        return np.zeros(n, dtype=np.float64)
    w = np.asarray(w, dtype=np.float64).reshape(-1)
    if len(w) != n:
        raise ValueError(f"expected {n} weights, got {len(w)}")
    return w


def connected_components(n: int, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Label nodes 0..n-1 joined by edges (a[i], b[i]); labels are the smallest node id."""
    labels = np.arange(n)
    if len(a) == 0:
        return labels
    while True:
        la, lb = labels[a], labels[b]
        diff = la != lb
        if not diff.any():
            return labels
        lo = np.minimum(la[diff], lb[diff])
        hi = np.maximum(la[diff], lb[diff])
        np.minimum.at(labels, hi, lo)
        # Pointer jumping until every node points at its root.
        while True:
            nxt = labels[labels]
            if np.array_equal(nxt, labels):
                break
            labels = nxt
