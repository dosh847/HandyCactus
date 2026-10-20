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
# A 50 mm mini needs a few hundred to a few thousand contacts. Far more means
# the model is not in millimetres (see the Units setting), so stop early.
MAX_CONTACTS = 20_000
_PACKING = 0.7  # Poisson-disk samples per spacing² of area, roughly


class TooManyContacts(ValueError):
    def __init__(self, estimate: int, overhang_area_mm2: float):
        self.estimate = estimate
        self.overhang_area_mm2 = overhang_area_mm2
        super().__init__(
            f"About {estimate:,} contacts would be needed for {overhang_area_mm2:,.0f} mm² of overhang. "
            "Is the model in millimetres? Check the Units setting."
        )


_EPS = 1e-3  # mm
MINIMUM_DEPTH = 0.01  # mm, spec 6.2 step 2
# A patch growing from material below it needs no support while smaller than this
# × tip_spacing² (0.56 mm² at the default 1.5 mm spacing): sculpted wrinkles, scales.
SMALL_PATCH_FACTOR = 0.25
# ... and only while shallow: area-weighted overhang severity below this (0 at the overhang
# angle, 1 for a horizontal ceiling; 0.5 is about 67° from vertical at the default 45°).
SELF_SUPPORT_SEVERITY = 0.5


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
    stats: dict[str, int] = field(default_factory=dict)  # counts after each stage, for diagnostics


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
    candidate = (overhang | face_force) & ~near_bed
    face_buried = np.zeros(len(mesh.tris), dtype=bool)
    c = np.flatnonzero(candidate)
    face_buried[c] = _buried(mesh.face_centroids[c], mesh.face_normals[c], inside)
    support = candidate & ~face_buried
    sample_faces = support & ~face_block

    # Patches: support faces connected through shared edges.
    fa, fb, _ = mesh.face_edge_pairs()
    both = support[fa] & support[fb]
    labels = connected_components(len(mesh.tris), fa[both], fb[both])
    patch = np.where(support, labels, -1)

    # Forced contacts.
    vnormals = mesh.vertex_normals()
    blocked = []
    minima = _local_minima(mesh, vnormals)
    minima = minima[z[minima] >= params.bed_ignore_height]
    minima = minima[~_buried(mesh.verts[minima], vnormals[minima], inside)]
    is_blocked = mesh.block_weights[minima] > PAINT_THRESHOLD
    blocked.append(mesh.verts[minima[is_blocked]])
    minima = minima[~is_blocked]

    lowest, lowest_patch, lowest_face = _patch_lowest_vertices(mesh, patch)
    # A small, shallow patch whose lowest point has a lower neighbour grows out of
    # material printed just before it (a wrinkle on a wall, a fold running into the cloth
    # below): each layer overhangs the last a little and it prints without support. It
    # needs no contact and is not a support face. Large or steep patches keep theirs.
    from_below = _neighbour_min_z(mesh)[lowest] < z[lowest] - MINIMUM_DEPTH
    n_patches = patch.max() + 1
    patch_area = np.bincount(patch[support], weights=mesh.face_areas[support], minlength=n_patches)
    weighted = np.bincount(patch[support], weights=(mesh.face_areas * severity)[support], minlength=n_patches)
    patch_severity = weighted / np.maximum(patch_area, 1e-12)
    small = patch_area[lowest_patch] < SMALL_PATCH_FACTOR * params.tip_spacing**2
    shallow = patch_severity[lowest_patch] < SELF_SUPPORT_SEVERITY
    self_supporting = lowest_patch[from_below & small & shallow]
    drop_faces = np.isin(patch, self_supporting) & ~face_force
    support &= ~drop_faces
    sample_faces &= ~drop_faces
    patch[drop_faces] = -1
    stats_dropped = len(self_supporting)
    keep_lowest = ~(from_below & small & shallow)
    lowest, lowest_face = lowest[keep_lowest], lowest_face[keep_lowest]
    keep = ~_buried(mesh.verts[lowest], mesh.face_normals[lowest_face], inside)
    keep &= ~_anchored(mesh.verts[lowest], inside, params.max_bridge)
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
    area = mesh.face_areas[sample_faces]
    estimate = int(_PACKING * (area / spacing[sample_faces] ** 2).sum())
    if estimate > MAX_CONTACTS:
        raise TooManyContacts(estimate, float(area.sum()))
    rng = np.random.default_rng(seed)
    s_pos, s_face = _poisson_samples(mesh, sample_faces, spacing, forced_pos, rng)
    stats = {
        "support_faces": int(support.sum()),
        "patches": int(len(np.unique(patch[patch >= 0]))),
        "self_supporting_patches": stats_dropped,
        "forced": int(len(forced_pos)),
        "samples": int(len(s_pos)),
    }

    # Bridging, then the inside filter at the sample points themselves.
    # Painted (HC_Force) samples are wanted by the user even where they could bridge.
    keep = ~_anchored(s_pos, inside, params.max_bridge) | face_force[s_face]
    stats["samples_after_bridging"] = int(keep.sum())
    keep &= ~_buried(s_pos, mesh.face_normals[s_face], inside)
    stats["samples_after_inside"] = int(keep.sum())
    rejected = s_pos[~keep]
    s_pos, s_face = s_pos[keep], s_face[keep]

    # Gap fill: overhang faces no contact covers get one, lowest first. Faces next to a
    # bridged (or buried) point need none and leave the support mask.
    g_pos, g_face, bridged = _fill_gaps(
        mesh, sample_faces, np.concatenate([forced_pos, s_pos]), rejected, spacing, params, inside, face_force
    )
    stats["gap_fill"] = int(len(g_pos))
    stats["bridged_faces"] = int(bridged.sum())
    s_pos, s_face = np.concatenate([s_pos, g_pos]), np.concatenate([s_face, g_face])
    support &= ~bridged

    contacts = Contacts(
        positions=np.concatenate([forced_pos, s_pos]),
        normals=np.concatenate([forced_normal[forced_v], mesh.face_normals[s_face]]),
        target=np.concatenate([mesh.vert_target[forced_v], mesh.face_target[s_face]]),
        kind=np.concatenate([forced_kind, np.full(len(s_pos), SAMPLE)]).astype(np.int64),
    )
    return DetectionResult(contacts, support, patch, np.concatenate(blocked), warnings, stats)


def _buried(points: np.ndarray, normals: np.ndarray, inside: Sequence[InsideTester | None]) -> np.ndarray:
    """True where a surface point is buried: just outside it (along its normal) is still solid.

    That happens where one target pokes into another (an arm into a body), and inside a
    single target made of overlapping shells (feathers pushed into each other), which
    many sculpted minis are. A buried surface never prints, so it needs no support.
    """
    probes = points + normals * BURIED_PROBE_MM
    out = np.zeros(len(points), dtype=bool)
    for tester in inside:
        if tester is None:
            continue
        idx = np.flatnonzero(~out & in_bounds(probes, tester.bounds_min, tester.bounds_max))
        if len(idx):
            out[idx] = tester.is_inside(probes[idx])
    return out


BURIED_PROBE_MM = 0.02


def _local_minima(mesh: MeshData, vertex_normals: np.ndarray) -> np.ndarray:
    """Downward-facing vertices at least MINIMUM_DEPTH below every neighbour.

    The depth keeps float noise on flat faces (Blender stores float32) from
    reading as minima; a shallow real minimum lies in an overhang patch and
    gets that patch's lowest-point contact instead.
    """
    z = mesh.verts[:, 2]
    nbr_min = _neighbour_min_z(mesh)
    lower = (z < nbr_min - MINIMUM_DEPTH) & np.isfinite(nbr_min)  # isolated vertices excluded
    return np.flatnonzero(lower & (vertex_normals[:, 2] < 0))


def _neighbour_min_z(mesh: MeshData) -> np.ndarray:
    """Lowest Z among each vertex's edge neighbours (inf for isolated vertices)."""
    edges = mesh.half_edges()  # repeats are harmless for a minimum
    z = mesh.verts[:, 2]
    nbr_min = np.full(len(z), np.inf)
    np.minimum.at(nbr_min, edges[:, 0], z[edges[:, 1]])
    np.minimum.at(nbr_min, edges[:, 1], z[edges[:, 0]])
    return nbr_min


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


def _anchored(points: np.ndarray, inside: Sequence[InsideTester | None], span: float) -> np.ndarray:
    """True where a point sits on a bridge no longer than `span` (max_bridge).

    A bridge is held up from both sides: just below the point there must be
    model material in two directions at least 120° apart, nearer together
    than `span` in total (left gap + right gap). Material on one side only is
    the overhang's own continuation, or a wall it cantilevers from; the
    overhang angle already decides those. Anchors are probed at
    `BRIDGE_DEPTH × span` below the point and at half that depth, so shallow
    bumps and material across an air gap do not count. The bed never counts,
    and open targets (no inside test) never anchor, which errs toward more
    supports.
    """
    n = len(points)
    if n == 0 or span <= 0 or all(t is None for t in inside):
        return np.zeros(n, dtype=bool)
    shape = (n, len(_BRIDGE_RADII), _BRIDGE_AZIMUTHS)
    # Deepest level first; shallower levels are only probed where it found material.
    hit = None
    for level in sorted(_BRIDGE_LEVELS, reverse=True):
        offsets = _BRIDGE_DISC * span
        offsets[:, 2] = -BRIDGE_DEPTH * level * span
        probes = (points[:, None, :] + offsets[None, :, :]).reshape(-1, 3)
        todo = np.ones(len(probes), dtype=bool) if hit is None else hit.ravel()
        level_hit = np.zeros(len(probes), dtype=bool)
        todo &= probes[:, 2] > 0
        for tester in inside:
            if tester is None:
                continue
            j = np.flatnonzero(todo & ~level_hit & in_bounds(probes, tester.bounds_min, tester.bounds_max))
            if len(j):
                level_hit[j] = tester.is_inside(probes[j], rays=1)  # many probes; one ray each
        hit = level_hit.reshape(shape)
    # Nearest anchored material per direction, as a fraction of span (inf = none).
    radii = np.array(_BRIDGE_RADII)[None, :, None]
    nearest = np.where(hit, radii, np.inf).min(axis=1)  # (N, azimuths)
    out = np.zeros(n, dtype=bool)
    for a, b in _BRIDGE_OPPOSITE:
        out |= nearest[:, a] + nearest[:, b] <= 1.0 + 1e-9
    return out


BRIDGE_DEPTH = 0.4  # × span below the point (0.8 mm at the default 2 mm max_bridge)
_BRIDGE_LEVELS = (0.5, 1.0)  # × BRIDGE_DEPTH
_BRIDGE_RADII = (0.25, 0.5, 0.75, 1.0)
_BRIDGE_AZIMUTHS = 12
_BRIDGE_DISC = np.array(
    [
        (r * np.cos(2 * np.pi * k / _BRIDGE_AZIMUTHS), r * np.sin(2 * np.pi * k / _BRIDGE_AZIMUTHS), 0.0)
        for r in _BRIDGE_RADII
        for k in range(_BRIDGE_AZIMUTHS)
    ]
)
# Azimuth bin pairs at least 120° apart (4 of 12 bins).
_BRIDGE_OPPOSITE = [
    (a, b) for a in range(_BRIDGE_AZIMUTHS) for b in range(a + 1, _BRIDGE_AZIMUTHS)
    if min(b - a, _BRIDGE_AZIMUTHS - (b - a)) >= _BRIDGE_AZIMUTHS // 3
]  # fmt: skip


GAP_FILL_ROUNDS = 8
BRIDGED_FACTOR = 0.5  # × tip_spacing around a bridged (or buried) point: needs no support


def _fill_gaps(mesh, faces_mask, contacts, rejected, spacing, params, inside, face_force):
    """Contacts for overhang faces that no contact covers (spec 6.2 step 6).

    Coverage is the heatmap's rule: a contact within COVERAGE_FACTOR × tip_spacing, at
    most a layer above. A sample dropped as bridged or buried handles the faces within
    BRIDGED_FACTOR × tip_spacing of it (same height rule) instead. Each round takes the
    lowest unhandled faces, thinned to one per spacing; those that are not bridged or
    buried become contacts, the others handle their neighbourhood.

    Returns (positions, faces) of the added contacts and a mask (all faces) of the faces
    handled by bridged or buried points, which need no support.
    """
    from .heatmap import covered, within  # heatmap imports this module

    bridged = np.zeros(len(mesh.tris), dtype=bool)
    faces = np.flatnonzero(faces_mask)
    if len(faces) == 0:
        return np.zeros((0, 3)), np.zeros(0, np.int64), bridged
    centres = mesh.face_centroids[faces]
    near = BRIDGED_FACTOR * params.tip_spacing

    def by_dropped(idx, pts):
        return within(centres[idx], pts, near, below=params.layer_height)

    is_covered = covered(centres, np.asarray(contacts).reshape(-1, 3), params)
    gaps = np.flatnonzero(~is_covered)
    dropped = [np.asarray(rejected).reshape(-1, 3)]
    gaps = gaps[~by_dropped(gaps, dropped[0])]
    added_pos, added_face = [], []
    for _ in range(GAP_FILL_ROUNDS):
        if len(gaps) == 0:
            break
        order = gaps[np.argsort(np.round(centres[gaps, 2], 4), kind="stable")]
        pick = order[_thin_out(centres[order], spacing[faces[order]])]
        pts, pf = centres[pick], faces[pick]
        ok = ~_anchored(pts, inside, params.max_bridge) | face_force[pf]
        ok &= ~_buried(pts, mesh.face_normals[pf], inside)
        added_pos.append(pts[ok])
        added_face.append(pf[ok])
        dropped.append(pts[~ok])
        gaps = gaps[~covered(centres[gaps], pts[ok], params) & ~by_dropped(gaps, pts[~ok])]
    all_added = np.concatenate(added_pos) if added_pos else np.zeros((0, 3))
    # Bridged: handled by a dropped point and covered by no contact.
    final_covered = is_covered | covered(centres, all_added, params)
    dropped_all = np.concatenate(dropped)
    idx = np.flatnonzero(~final_covered)
    bridged[faces[idx[by_dropped(idx, dropped_all)]]] = True
    if not added_pos:
        return all_added, np.zeros(0, np.int64), bridged
    return all_added, np.concatenate(added_face), bridged


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
    # Lowest candidates first: a tip only holds up what is printed after it (at or above
    # its height), so contacts belong at the bottom of each region, with the rest above.
    # Rounded so float noise in the evaluated mesh (it differs between runs at ~1e-9 mm)
    # cannot reorder near-equal heights and change the result.
    order = np.argsort(np.round(pts[:, 2], 4), kind="stable")
    pts, pick = pts[order], pick[order]
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
