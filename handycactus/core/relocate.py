# SPDX-License-Identifier: GPL-3.0-or-later
"""Second chances for contacts whose tip could not be built (spec section 6.3.1).

A contact marks a spot of overhang to hold up, not an exact point. Island contacts
(the lowest point of an overhang patch, local minima) often sit right where the
patch meets a wall, in a crease the tip cone cannot clear. For each failed contact:

1. **Covered**: a working tip already within RELOCATE_FACTOR × tip_spacing and no
   more than one layer above it holds the spot up (the coverage rule of the heatmap):
   the failed contact is dropped.
2. **Moved**: otherwise, the overhang faces nearby (facing the same way, within the
   same radius) are tried lowest first, and the contact moves to the first one with
   room for a tip.
3. Otherwise it stays unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .detection import PAINT_THRESHOLD, Contacts
from .mesh_data import MeshData
from .params import Params
from .spatial import Obstacles
from .tips import Tips, _separate_knuckles, build_tips

RELOCATE_FACTOR = 0.5  # × tip_spacing: how far a failed contact may move (or be covered from)
MAX_TRIES = 24  # nearby faces tried per failed contact
SAME_FACING = 0.5  # min dot product between the old and new surface normals


@dataclass
class Relocation:
    contacts: Contacts
    tips: Tips
    covered: int  # failed contacts dropped because a working tip holds the spot
    moved: int  # failed contacts moved to a nearby spot with room for a tip


def relocate_failed(
    contacts: Contacts,
    built: Tips,
    mesh: MeshData,
    support_faces: np.ndarray,
    params: Params,
    obstacles: Obstacles,
) -> Relocation:
    failed = np.flatnonzero(~built.ok)
    if len(failed) == 0:
        return Relocation(contacts, built, 0, 0)
    radius = RELOCATE_FACTOR * params.tip_spacing
    pos = contacts.positions

    # 1. Covered by a working tip close by and not above.
    good = np.flatnonzero(built.ok)
    covered = np.zeros(len(contacts), bool)
    if len(good):
        for i in failed:
            d = np.linalg.norm(pos[good] - pos[i], axis=1)
            near = (d <= radius) & (pos[good, 2] <= pos[i, 2] + params.layer_height)
            covered[i] = bool(near.any())
    todo = failed[~covered[failed]]

    # 2. Nearby overhang faces, lowest first.
    blocked = mesh.block_weights[mesh.tris].mean(axis=1) > PAINT_THRESHOLD if len(mesh.tris) else None
    allowed = support_faces & ~blocked if blocked is not None else support_faces
    faces = np.flatnonzero(allowed)
    owner, cand_faces = _nearby_faces(pos[todo], contacts.normals[todo], mesh, faces, radius)
    moved_to = np.full(len(contacts), -1)
    new_tips = None
    if len(cand_faces):
        cand = Contacts(
            positions=mesh.face_centroids[cand_faces],
            normals=mesh.face_normals[cand_faces],
            target=mesh.face_target[cand_faces],
            kind=contacts.kind[todo[owner]],
        )
        new_tips = build_tips(cand, params, obstacles)
        for k in range(len(todo)):
            mine = np.flatnonzero((owner == k) & new_tips.ok)
            if len(mine):
                moved_to[todo[k]] = mine[0]  # candidates are in try order

    # Assemble: drop covered contacts, swap in moved ones.
    keep = ~covered
    moved = np.flatnonzero(moved_to >= 0)
    c_pos, c_nrm = pos.copy(), contacts.normals.copy()
    c_tgt = contacts.target.copy()
    t = {name: getattr(built, name).copy() for name in ("surface", "axis", "length", "ok", "target")}
    if len(moved):
        src = moved_to[moved]
        c_pos[moved] = cand.positions[src]
        c_nrm[moved] = cand.normals[src]
        c_tgt[moved] = cand.target[src]
        for name in t:
            t[name][moved] = getattr(new_tips, name)[src]
    out_contacts = Contacts(c_pos[keep], c_nrm[keep], c_tgt[keep], contacts.kind[keep].copy())
    surface, axis, length, ok, target = (t[n][keep] for n in ("surface", "axis", "length", "ok", "target"))
    knuckle = surface + axis * length[:, None]
    _separate_knuckles(knuckle, axis, length, ok, params.knuckle_diameter / 2)
    knuckle = surface + axis * length[:, None]
    tips = Tips(surface, surface - axis * params.penetration, knuckle, axis, length, ok, target)
    return Relocation(out_contacts, tips, int(covered.sum()), len(moved))


def _nearby_faces(points, normals, mesh: MeshData, faces: np.ndarray, radius: float):
    """For each point, up to MAX_TRIES faces within `radius` facing the same way, lowest
    first. Returns (owner index per candidate, face per candidate), grouped by owner."""
    if len(points) == 0 or len(faces) == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    centres = mesh.face_centroids[faces]
    cell = np.floor(centres / radius).astype(np.int64)
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for j, c in enumerate(map(tuple, cell.tolist())):
        buckets.setdefault(c, []).append(j)
    owners, picks = [], []
    for k, (p, n) in enumerate(zip(points, normals, strict=True)):
        cx, cy, cz = np.floor(p / radius).astype(np.int64)
        near = [
            j
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for dz in (-1, 0, 1)
            for j in buckets.get((cx + dx, cy + dy, cz + dz), ())
        ]
        if not near:
            continue
        near = np.array(near)
        d = np.linalg.norm(centres[near] - p, axis=1)
        ok = (d <= radius) & (d > 1e-6) & (mesh.face_normals[faces[near]] @ n >= SAME_FACING)
        near, d = near[ok], d[ok]
        if len(near) == 0:
            continue
        order = np.lexsort((d, np.round(centres[near, 2], 2)))[:MAX_TRIES]
        owners.append(np.full(len(order), k))
        picks.append(faces[near[order]])
    if not owners:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    return np.concatenate(owners), np.concatenate(picks)
