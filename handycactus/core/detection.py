# SPDX-License-Identifier: GPL-3.0-or-later
"""Contact detection (spec section 6.2).

Finds the points on the target set that need a support tip:
1. faces that need support (overhang angle or HC_Force paint), minus faces
   near the bed and faces inside another target;
2. forced contacts at vertex local minima and at each patch's lowest point;
3. Poisson-disk samples on the remaining faces, denser where steeper;
4. the bridging filter, the HC_Block filter and the inside filter.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from .mesh_data import MeshData, connected_components
from .params import Params
from .spatial import InsideTester, in_bounds

# Contact kinds.
SAMPLE = 0  # Poisson sample on an overhang (or forced) face
MINIMUM = 1  # vertex lower than all its neighbours
PATCH_LOWEST = 2  # lowest point of an overhang patch

PAINT_THRESHOLD = 0.5
MAX_CANDIDATES = 400_000
_EPS = 1e-3  # mm
MINIMUM_DEPTH = 0.01  # mm, spec 6.2 step 2


@dataclass
class Contacts:
    positions: np.ndarray  # (K, 3) mm
    normals: np.ndarray  # (K, 3) unit surface normals
    target: np.ndarray  # (K,) index into MeshData.names
    kind: np.ndarray  # (K,) SAMPLE / MINIMUM / PATCH_LOWEST

    def __len__(self) -> int:
        return len(self.positions)

    @property
    def is_island(self) -> np.ndarray:
        return self.kind != SAMPLE

    @staticmethod
    def empty() -> Contacts:
        return Contacts(np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0, np.int64), np.zeros(0, np.int64))


@dataclass
class DetectionResult:
    contacts: Contacts
    support_faces: np.ndarray  # (T,) bool, faces that need support
    patch: np.ndarray  # (T,) int patch label, -1 where not a support face
    blocked_islands: np.ndarray  # (B, 3) forced contacts removed by HC_Block
    warnings: list[str] = field(default_factory=list)


def overhang_severity(normals: np.ndarray, overhang_angle: float) -> np.ndarray:
    """0 at the overhang threshold rising to 1 for a horizontal ceiling; < 0 below it."""
    s = np.sin(np.radians(overhang_angle))
    return (-normals[:, 2] - s) / max(1.0 - s, 1e-9)


def needs_support(normals: np.ndarray, overhang_angle: float) -> np.ndarray:
    return normals[:, 2] < -np.sin(np.radians(overhang_angle))


def detect(
    mesh: MeshData,
    params: Params,
    inside: Sequence[InsideTester | None],
    seed: int = 0,
) -> DetectionResult:
    """Run contact detection. `inside[i]` tests target i (None = open mesh, skipped)."""
    if len(inside) != mesh.target_count:
        raise ValueError("need one inside tester (or None) per target")
    warnings = [
        f'"{mesh.names[i]}" is not a closed mesh; overlaps with it are not detected'
        for i, tester in enumerate(inside)
        if tester is None
    ]
    if len(mesh.tris) == 0:
        empty = np.zeros(0, dtype=bool)
        return DetectionResult(Contacts.empty(), empty, np.zeros(0, np.int64), np.zeros((0, 3)), warnings)

    z = mesh.verts[:, 2]
    face_force = mesh.force_weights[mesh.tris].mean(axis=1) > PAINT_THRESHOLD
    face_block = mesh.block_weights[mesh.tris].mean(axis=1) > PAINT_THRESHOLD
    severity = overhang_severity(mesh.face_normals, params.overhang_angle)
    overhang = needs_support(mesh.face_normals, params.overhang_angle) & (mesh.face_areas > 0)
    near_bed = mesh.face_min_z < params.bed_ignore_height
    face_inside = _inside_other(mesh.face_centroids, mesh.face_target, inside)
    support = (overhang | face_force) & ~near_bed & ~face_inside
    sample_faces = support & ~face_block

    # Patches: support faces connected through shared edges.
    fa, fb, edge_verts = mesh.face_edge_pairs()
    both = support[fa] & support[fb]
    labels = connected_components(len(mesh.tris), fa[both], fb[both])
    patch = np.where(support, labels, -1)

    # Supported patch boundaries: material continues downward on the other side.
    seg_a, seg_b, seg_patch = _supported_boundaries(
        mesh, support, near_bed | face_inside, patch, fa, fb, edge_verts
    )

    # Forced contacts.
    vnormals = mesh.vertex_normals()
    blocked = []
    minima = _local_minima(mesh, vnormals)
    minima = minima[z[minima] >= params.bed_ignore_height]
    minima = minima[~_inside_other(mesh.verts[minima], mesh.vert_target[minima], inside)]
    is_blocked = mesh.block_weights[minima] > PAINT_THRESHOLD
    blocked.append(mesh.verts[minima[is_blocked]])
    minima = minima[~is_blocked]

    lowest, lowest_patch, lowest_face = _patch_lowest_vertices(mesh, patch)
    keep = ~_inside_other(mesh.verts[lowest], mesh.vert_target[lowest], inside)
    keep &= _far_from_boundaries(
        mesh.verts[lowest], lowest_patch, seg_a, seg_b, seg_patch, params.max_bridge / 2
    )
    lowest, lowest_face = lowest[keep], lowest_face[keep]
    is_blocked = mesh.block_weights[lowest] > PAINT_THRESHOLD
    blocked.append(mesh.verts[lowest[is_blocked]])
    lowest, lowest_face = lowest[~is_blocked], lowest_face[~is_blocked]
    # A patch's lowest point is often on its rim, where the vertex normal leans
    # into the walls; the patch face's own normal is the surface being held up.
    forced_normal = vnormals.copy()
    forced_normal[lowest] = mesh.face_normals[lowest_face]

    forced_v = np.concatenate([minima, lowest])
    forced_kind = np.concatenate([np.full(len(minima), MINIMUM), np.full(len(lowest), PATCH_LOWEST)])
    forced_v, idx = np.unique(forced_v, return_index=True)  # a minimum may also be a patch lowest
    forced_kind = forced_kind[idx]
    order = np.argsort(forced_kind, kind="stable")  # minima win over patch lowest points
    forced_v, forced_kind = forced_v[order], forced_kind[order]
    forced_pos = mesh.verts[forced_v]
    keep = _thin_out(forced_pos, np.full(len(forced_pos), 0.5 * params.tip_spacing))
    forced_v, forced_kind, forced_pos = forced_v[keep], forced_kind[keep], forced_pos[keep]

    # Poisson-disk samples, seeded with the forced contacts.
    spacing = params.tip_spacing / (1.0 + (params.steepness_density - 1.0) * np.clip(severity, 0.0, 1.0))
    rng = np.random.default_rng(seed)
    s_pos, s_face = _poisson_samples(mesh, sample_faces, spacing, forced_pos, rng)

    # Bridging, then the inside filter at the sample points themselves.
    keep = _far_from_boundaries(s_pos, patch[s_face], seg_a, seg_b, seg_patch, params.max_bridge / 2)
    keep &= ~_inside_other(s_pos, mesh.face_target[s_face], inside)
    s_pos, s_face = s_pos[keep], s_face[keep]

    contacts = Contacts(
        positions=np.concatenate([forced_pos, s_pos]),
        normals=np.concatenate([forced_normal[forced_v], mesh.face_normals[s_face]]),
        target=np.concatenate([mesh.vert_target[forced_v], mesh.face_target[s_face]]),
        kind=np.concatenate([forced_kind, np.full(len(s_pos), SAMPLE)]).astype(np.int64),
    )
    return DetectionResult(contacts, support, patch, np.concatenate(blocked), warnings)


def _inside_other(points: np.ndarray, owner: np.ndarray, inside: Sequence[InsideTester | None]) -> np.ndarray:
    """True where a point is inside a target other than the one it belongs to."""
    out = np.zeros(len(points), dtype=bool)
    for t, tester in enumerate(inside):
        if tester is None:
            continue
        idx = np.flatnonzero((owner != t) & ~out & in_bounds(points, tester.bounds_min, tester.bounds_max))
        if len(idx):
            out[idx] = tester.is_inside(points[idx])
    return out


def _local_minima(mesh: MeshData, vertex_normals: np.ndarray) -> np.ndarray:
    """Downward-facing vertices at least MINIMUM_DEPTH below every neighbour.

    The depth keeps float noise on flat faces (Blender stores float32) from
    reading as minima; a shallow real minimum lies in an overhang patch and
    gets that patch's lowest-point contact instead.
    """
    edges = mesh.half_edges()  # repeats are harmless for a minimum
    z = mesh.verts[:, 2]
    nbr_min = np.full(len(z), np.inf)
    np.minimum.at(nbr_min, edges[:, 0], z[edges[:, 1]])
    np.minimum.at(nbr_min, edges[:, 1], z[edges[:, 0]])
    lower = (z < nbr_min - MINIMUM_DEPTH) & np.isfinite(nbr_min)  # isolated vertices excluded
    return np.flatnonzero(lower & (vertex_normals[:, 2] < 0))


def _patch_lowest_vertices(mesh: MeshData, patch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    faces = np.flatnonzero(patch >= 0)
    if len(faces) == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64)
    corner = mesh.tris[faces]  # (F, 3)
    corner_z = mesh.verts[corner, 2]
    lowest_corner = corner[np.arange(len(faces)), np.argmin(corner_z, axis=1)]
    lowest_z = mesh.verts[lowest_corner, 2]
    face_patch = patch[faces]
    order = np.lexsort((lowest_corner, lowest_z, face_patch))
    first = np.concatenate([[True], face_patch[order][1:] != face_patch[order][:-1]])
    return lowest_corner[order][first], face_patch[order][first], faces[order][first]


def _supported_boundaries(mesh, support, attached, patch, fa, fb, edge_verts):
    """Patch boundary edges where the neighbouring face continues downward.

    That neighbour holds the patch up, so short spans next to it can bridge.
    Neighbours resting on the bed or buried in another target also count.
    """
    a_in, b_in = support[fa], support[fb]
    boundary = a_in != b_in
    inner = np.where(a_in, fa, fb)[boundary]
    outer = np.where(a_in, fb, fa)[boundary]
    ev = edge_verts[boundary]
    outer_tris = mesh.tris[outer]
    # The outer face's vertex that is not on the shared edge.
    is_edge_vert = (outer_tris == ev[:, :1]) | (outer_tris == ev[:, 1:])
    far = outer_tris[np.arange(len(outer)), np.argmin(is_edge_vert, axis=1)]
    edge_mid_z = mesh.verts[ev, 2].mean(axis=1)
    goes_down = mesh.verts[far, 2] < edge_mid_z - _EPS
    ok = goes_down | attached[outer]
    return mesh.verts[ev[ok, 0]], mesh.verts[ev[ok, 1]], patch[inner[ok]]


def _far_from_boundaries(points, point_patch, seg_a, seg_b, seg_patch, limit) -> np.ndarray:
    """False where a point is within `limit` (3D) of a supported boundary of its own patch."""
    keep = np.ones(len(points), dtype=bool)
    if len(points) == 0 or len(seg_a) == 0 or limit <= 0:
        return keep
    seg_order = np.argsort(seg_patch, kind="stable")
    seg_a, seg_b, seg_patch = seg_a[seg_order], seg_b[seg_order], seg_patch[seg_order]
    uniq, starts = np.unique(seg_patch, return_index=True)
    ends = np.append(starts[1:], len(seg_patch))
    for p, s, e in zip(uniq, starts, ends, strict=True):
        idx = np.flatnonzero(point_patch == p)
        if len(idx) == 0:
            continue
        a, b = seg_a[s:e], seg_b[s:e]
        for c in range(0, len(idx), 512):
            sub = idx[c : c + 512]
            d = _point_segment_distance(points[sub], a, b).min(axis=1)
            keep[sub] = d > limit
    return keep


def _point_segment_distance(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(P, 3) points, (S, 3) segments -> (P, S) distances."""
    ab = b - a
    denom = np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-18)
    ap = p[:, None, :] - a[None, :, :]
    t = np.clip(np.einsum("psj,sj->ps", ap, ab) / denom, 0.0, 1.0)
    closest = a[None, :, :] + t[..., None] * ab[None, :, :]
    return np.linalg.norm(p[:, None, :] - closest, axis=2)


def _poisson_samples(mesh, faces_mask, spacing, seeds, rng):
    """Area-weighted dart throwing with a per-face spacing; returns (positions, faces)."""
    faces = np.flatnonzero(faces_mask)
    if len(faces) == 0:
        return np.zeros((0, 3)), np.zeros(0, np.int64)
    area = mesh.face_areas[faces]
    r = spacing[faces]
    expected = area / (r * r)
    n = int(min(MAX_CANDIDATES, max(1, np.ceil(expected.sum() * 6))))
    pick = faces[rng.choice(len(faces), size=n, p=area / area.sum())]
    u, v = rng.random(n), rng.random(n)
    su = np.sqrt(u)
    a, b, c = (mesh.verts[mesh.tris[pick, i]] for i in range(3))
    pts = (1 - su)[:, None] * a + (su * (1 - v))[:, None] * b + (su * v)[:, None] * c
    keep = _thin_out(pts, spacing[pick], seeds=seeds, seed_radius=float(spacing[faces].min()))
    return pts[keep], pick[keep]


def _thin_out(points, radius, seeds=None, seed_radius=0.0) -> np.ndarray:
    """Greedy dart throwing in the given order: keep a point if no kept point is within its radius.

    `seeds` are pre-accepted points that block candidates but are not returned.
    """
    keep = np.zeros(len(points), dtype=bool)
    if len(points) == 0:
        return keep
    cell = float(max(radius.max(), seed_radius, 1e-6))
    grid: dict[tuple[int, int, int], list[tuple[float, float, float]]] = {}
    if seeds is not None:
        for p in np.asarray(seeds).tolist():
            grid.setdefault(_cell_of(p, cell), []).append(p)
    neighbours = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    for i, (p, r) in enumerate(zip(points.tolist(), radius.tolist(), strict=True)):
        r2 = r * r
        px, py, pz = p
        cx, cy, cz = _cell_of(p, cell)
        clear = True
        for dx, dy, dz in neighbours:
            for qx, qy, qz in grid.get((cx + dx, cy + dy, cz + dz), ()):
                if (px - qx) ** 2 + (py - qy) ** 2 + (pz - qz) ** 2 < r2:
                    clear = False
                    break
            if not clear:
                break
        if clear:
            keep[i] = True
            grid.setdefault((cx, cy, cz), []).append(p)
    return keep


def _cell_of(p, cell: float) -> tuple[int, int, int]:
    return (math.floor(p[0] / cell), math.floor(p[1] / cell), math.floor(p[2] / cell))
