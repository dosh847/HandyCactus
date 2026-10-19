# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic closed test meshes (mm, outward normals)."""

from __future__ import annotations

import numpy as np

from handycactus.core.mesh_data import TargetMesh


def voxels(boxes, voxel=0.5, name="voxels") -> TargetMesh:
    """Closed surface of the union of axis-aligned boxes [(lo, hi), ...].

    Box corners must lie on the voxel grid. Faces are split into voxel-sized
    quads, so there are plenty of vertices for sampling and minima.
    """
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    dims = np.round((hi - lo) / voxel).astype(int)
    occ = np.zeros(dims + 2, dtype=bool)  # one empty voxel of padding on every side
    for blo, bhi in boxes:
        a = np.round((np.asarray(blo) - lo) / voxel).astype(int) + 1
        b = np.round((np.asarray(bhi) - lo) / voxel).astype(int) + 1
        occ[a[0] : b[0], a[1] : b[1], a[2] : b[2]] = True

    quads = []  # (4 corner int coords), with outward normal axis/sign
    unit = np.eye(3, dtype=int)
    for axis in range(3):
        u, v = unit[(axis + 1) % 3], unit[(axis + 2) % 3]
        for sign in (1, -1):
            shifted = np.roll(occ, -sign, axis=axis)
            faces = np.argwhere(occ & ~shifted)
            base = faces + (unit[axis] if sign > 0 else 0)
            c = np.stack([base, base + u, base + u + v, base + v], axis=1)
            if sign < 0:
                c = c[:, ::-1]
            quads.append(c)
    q = np.concatenate(quads)  # (Q, 4, 3)
    corners, inverse = np.unique(q.reshape(-1, 3), axis=0, return_inverse=True)
    idx = inverse.reshape(-1, 4)
    tris = np.concatenate([idx[:, [0, 1, 2]], idx[:, [0, 2, 3]]])
    verts = (corners - 1) * voxel + lo
    return TargetMesh(name, verts.astype(np.float64), _orient_outward_by_axis(verts, tris, occ, lo, voxel))


def _orient_outward_by_axis(verts, tris, occ, lo, voxel):
    # Flip any triangle whose normal points into the solid.
    a, b, c = (verts[tris[:, i]] for i in range(3))
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=1)[:, None]
    probe = (a + b + c) / 3 + n * (voxel * 0.25)
    cell = np.floor((probe - lo) / voxel).astype(int) + 1
    into_solid = occ[cell[:, 0], cell[:, 1], cell[:, 2]]
    tris = tris.copy()
    tris[into_solid] = tris[into_solid][:, [0, 2, 1]]
    return tris


def uv_sphere(center, radius, segments=32, rings=16, name="sphere") -> TargetMesh:
    center = np.asarray(center, dtype=float)
    verts = [center + [0, 0, radius]]
    for i in range(1, rings):
        theta = np.pi * i / rings
        for j in range(segments):
            phi = 2 * np.pi * j / segments
            verts.append(
                center
                + radius * np.array([np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)])
            )
    verts.append(center + [0, 0, -radius])
    verts = np.array(verts)
    top, bottom = 0, len(verts) - 1
    tris = []
    for j in range(segments):
        tris.append([top, 1 + j, 1 + (j + 1) % segments])
    for i in range(rings - 2):
        r0, r1 = 1 + i * segments, 1 + (i + 1) * segments
        for j in range(segments):
            j1 = (j + 1) % segments
            tris += [[r0 + j, r1 + j, r1 + j1], [r0 + j, r1 + j1, r0 + j1]]
    last = 1 + (rings - 2) * segments
    for j in range(segments):
        tris.append([last + j, bottom, last + (j + 1) % segments])
    return TargetMesh(name, verts, _orient_convex(verts, np.array(tris)))


def cone_down(apex, height, radius, segments=32, name="cone") -> TargetMesh:
    """Cone with its point at the bottom and a flat cap on top."""
    apex = np.asarray(apex, dtype=float)
    ring = [
        apex + [radius * np.cos(a), radius * np.sin(a), height]
        for a in np.linspace(0, 2 * np.pi, segments, endpoint=False)
    ]
    verts = np.array([apex, *ring, apex + [0, 0, height]])
    cap = len(verts) - 1
    tris = []
    for j in range(segments):
        a, b = 1 + j, 1 + (j + 1) % segments
        tris += [[0, a, b], [cap, b, a]]
    return TargetMesh(name, verts, _orient_convex(verts, np.array(tris)))


def _orient_convex(verts, tris):
    center = verts.mean(axis=0)
    a, b, c = (verts[tris[:, i]] for i in range(3))
    n = np.cross(b - a, c - a)
    inward = np.einsum("ij,ij->i", n, (a + b + c) / 3 - center) < 0
    tris = tris.copy()
    tris[inward] = tris[inward][:, [0, 2, 1]]
    return tris
