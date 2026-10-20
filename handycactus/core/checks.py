# SPDX-License-Identifier: GPL-3.0-or-later
"""Printability checks (spec section 10).

Each check returns findings with a severity, a message and a location (mm),
so the UI can list them and frame the view on each. Findings are grouped
(one per tree, per gap region...) so a bad parameter does not produce
thousands of rows.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .graph import CONTACT, FLAG_THICK_LANDING, FOOT, JUNCTION, KNUCKLE, LANDING, SupportGraph
from .heatmap import COVERAGE_FACTOR, covered, estimate_support_faces
from .mesh_data import MeshData, connected_components
from .params import Params
from .spatial import Obstacles, points_inside
from .tips import BRANCH_HARD_CLEARANCE

ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"  # not a problem, but worth seeing (e.g. where branches land on the model)
_SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}

STEEP_TOLERANCE_DEG = 5.0
SLENDERNESS_LIMIT = 40.0
COLLISION_SAMPLE_MM = 0.5


@dataclass(frozen=True)
class Finding:
    code: str  # graph, unreachable, coverage, thin, steep, collision, slender, foot, landing
    severity: str  # ERROR or WARNING
    message: str
    location: tuple[float, float, float]  # mm

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


def run_checks(
    graph: SupportGraph,
    params: Params,
    mesh: MeshData | None = None,
    obstacles: Obstacles | None = None,
    unreachable: np.ndarray | None = None,
    support_faces: np.ndarray | None = None,
) -> list[Finding]:
    """All checks. `mesh` enables coverage (on `support_faces` from detection when given),
    `obstacles` enables collision."""
    findings = check_graph(graph)
    if any(f.code == "graph" for f in findings):
        return findings  # the rest assume a valid forest
    findings += check_unreachable(unreachable)
    findings += check_thin(graph, params)
    findings += check_steep(graph, params)
    findings += check_slender(graph)
    findings += check_feet(graph, params)
    findings += check_landings(graph)
    if obstacles is not None:
        findings += check_collision(graph, params, obstacles)
    if mesh is not None:
        findings += check_coverage(graph, params, mesh, support_faces)
    findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.code))
    return findings


def check_graph(graph: SupportGraph) -> list[Finding]:
    problems = graph.problems()
    where = tuple(graph.pos[0]) if len(graph) else (0.0, 0.0, 0.0)
    return [Finding("graph", ERROR, f"Support graph is invalid: {p}", where) for p in problems]


def check_unreachable(points: np.ndarray | None) -> list[Finding]:
    if points is None:
        return []
    return [
        Finding("unreachable", ERROR, "Contact could not reach the bed (unsupported)", tuple(p))
        for p in np.asarray(points, dtype=np.float64).reshape(-1, 3)
    ]


def _edges(graph: SupportGraph, include_tips: bool = False) -> np.ndarray:
    """Graph edges; without tips, contact tips and landing tips (thin and touching the
    model by design) are left out."""
    e = graph.edges
    if not include_tips:
        e = e[(graph.kind[e[:, 0]] != CONTACT) & (graph.kind[e[:, 1]] != LANDING)]
    return e


def _per_tree(graph: SupportGraph, nodes: np.ndarray, values: np.ndarray, worst=np.argmax):
    """Group flagged nodes by tree: yields (tree, count, node with the worst value)."""
    for tree in np.unique(graph.tree[nodes]):
        mine = graph.tree[nodes] == tree
        yield int(tree), int(mine.sum()), int(nodes[mine][worst(values[mine])])


def check_thin(graph: SupportGraph, params: Params) -> list[Finding]:
    out = []
    min_d = 2 * params.nozzle_diameter
    e = _edges(graph)
    d = 2 * np.minimum(graph.radius[e[:, 0]], graph.radius[e[:, 1]])
    thin = d < min_d - 1e-9
    if thin.any():
        nodes = e[thin, 0]
        for tree, count, node in _per_tree(graph, nodes, d[thin], worst=np.argmin):
            out.append(
                Finding(
                    "thin",
                    WARNING,
                    f"Tree {tree}: {count} segment(s) thinner than {min_d:.2f} mm (2 × nozzle)",
                    tuple(graph.pos[node]),
                )  # fmt: skip
            )
    if params.contact_diameter < 0.75 * params.nozzle_diameter:
        contact = np.flatnonzero(graph.kind == CONTACT)
        where = tuple(graph.pos[contact[0]]) if len(contact) else (0.0, 0.0, 0.0)
        out.append(
            Finding(
                "thin",
                WARNING,
                f"Contact diameter {params.contact_diameter:.2f} mm is under 0.75 × nozzle; "
                "tips may not print",
                where,
            )  # fmt: skip
        )
    return out


def check_steep(graph: SupportGraph, params: Params) -> list[Finding]:
    e = _edges(graph)
    v = graph.pos[e[:, 1]] - graph.pos[e[:, 0]]
    length = np.linalg.norm(v, axis=1)
    keep = length > 1e-6
    e, v, length = e[keep], v[keep], length[keep]
    angle = np.degrees(np.arccos(np.clip(-v[:, 2] / length, -1, 1)))
    limit = params.max_branch_angle + STEEP_TOLERANCE_DEG
    steep = angle > limit
    return [
        Finding(
            "steep",
            WARNING,
            f"Tree {tree}: {count} segment(s) lean more than {limit:.0f}° (worst {angle[steep].max():.0f}°)",
            tuple(graph.pos[node]),
        )  # fmt: skip
        for tree, count, node in _per_tree(graph, e[steep, 0], angle[steep])
    ]


def check_collision(graph: SupportGraph, params: Params, obstacles: Obstacles) -> list[Finding]:
    """Branches (not tips) that run into or nearly touch the model."""
    e = _edges(graph)
    # Knuckle -> first branch node starts at the tip: the knuckle itself is checked by tips.
    a, b = graph.pos[e[:, 0]], graph.pos[e[:, 1]]
    length = np.linalg.norm(b - a, axis=1)
    counts = np.maximum(1, np.ceil(length / COLLISION_SAMPLE_MM).astype(int))
    seg = np.repeat(np.arange(len(e)), counts)
    t = np.concatenate([(np.arange(c) + 0.5) / c for c in counts]) if len(e) else np.zeros(0)
    pts = a[seg] + (b - a)[seg] * t[:, None]
    radius = graph.radius[e[seg, 0]] + (graph.radius[e[seg, 1]] - graph.radius[e[seg, 0]]) * t
    gap = obstacles.nearest_distance(pts) - radius if len(pts) else np.zeros(0)
    buried = points_inside(obstacles, pts)
    gap[buried] = -(gap[buried] + 2 * radius[buried])  # inside: at least this deep
    limit = min(0.5 * params.model_clearance, BRANCH_HARD_CLEARANCE) - 0.05
    hits = gap < limit
    if not hits.any():
        return []
    nodes = e[seg[hits], 0]
    out = []
    for tree, _count, node in _per_tree(graph, nodes, -gap[hits]):
        worst = gap[hits][graph.tree[nodes] == tree].min()
        what = "runs into the model" if worst < 0 else f"comes within {worst:.2f} mm of the model"
        out.append(Finding("collision", ERROR, f"Tree {tree}: a branch {what}", tuple(graph.pos[node])))
    return out


def trunk_chains(graph: SupportGraph) -> list[np.ndarray]:
    """Per foot, the nodes from the foot up to the first junction or knuckle."""
    children: dict[int, list[int]] = {}
    for child, parent in graph.edges.tolist():
        children.setdefault(parent, []).append(child)
    chains = []
    for foot in np.flatnonzero(graph.kind == FOOT):
        chain = [int(foot)]
        node = int(foot)
        while len(children.get(node, [])) == 1 and graph.kind[node] not in (JUNCTION, KNUCKLE):
            node = children[node][0]
            chain.append(node)
        chains.append(np.array(chain))
    return chains


def check_slender(graph: SupportGraph) -> list[Finding]:
    out = []
    for chain in trunk_chains(graph):
        height = graph.pos[chain[-1], 2] - graph.pos[chain[0], 2]
        diameter = 2 * graph.radius[chain[1:]].mean() if len(chain) > 1 else 2 * graph.radius[chain[0]]
        ratio = height / max(diameter, 1e-9)
        if ratio > SLENDERNESS_LIMIT:
            out.append(
                Finding(
                    "slender",
                    WARNING,
                    f"Tree {graph.tree[chain[0]]}: trunk is {height:.1f} mm tall and {diameter:.2f} mm thick "
                    f"({ratio:.0f}:1); it may wobble",
                    tuple(graph.pos[chain[-1]]),
                )  # fmt: skip
            )
    return out


def check_feet(graph: SupportGraph, params: Params) -> list[Finding]:
    feet = np.flatnonzero(graph.kind == FOOT)
    small = feet[graph.radius[feet] < params.nozzle_diameter]
    return [
        Finding(
            "foot",
            WARNING,
            f"Tree {graph.tree[f]}: foot {2 * graph.radius[f]:.2f} mm wide; first layer may not stick",
            tuple(graph.pos[f]),
        )  # fmt: skip
        for f in small
    ]


def check_landings(graph: SupportGraph) -> list[Finding]:
    """Landings leave a small mark (info). A tree slimmed to land, because it was thicker
    than max_landing_diameter with no way to the bed, is a warning."""
    out = []
    for n in np.flatnonzero(graph.kind == LANDING):
        if graph.flags[n] & FLAG_THICK_LANDING:
            msg = f"Tree {graph.tree[n]}: a thick branch had to land on the model (slimmed to the limit)"
            out.append(Finding("landing", WARNING, msg, tuple(graph.pos[n])))
        else:
            msg = f"Tree {graph.tree[n]}: branch lands on the model (leaves a small mark)"
            out.append(Finding("landing", INFO, msg, tuple(graph.pos[n])))
    return out


def check_coverage(
    graph: SupportGraph, params: Params, mesh: MeshData, support_faces: np.ndarray | None = None
) -> list[Finding]:
    """Overhang regions with no contact within COVERAGE_FACTOR × tip_spacing at or below
    their height (a tip does not support anything below it)."""
    if support_faces is None:
        support_faces = estimate_support_faces(mesh, params)
    faces = np.flatnonzero(support_faces)
    if len(faces) == 0:
        return []
    contact = np.flatnonzero(graph.kind == CONTACT)
    # Contact nodes sit `penetration` inside the model; measure from the surface point.
    surface = graph.pos[contact] + graph.dir[contact] * params.penetration
    gap = ~covered(mesh.face_centroids[faces], surface, params)
    gap_faces = faces[gap]
    if len(gap_faces) == 0:
        return []
    # Group gap faces into connected regions; report the ones big enough to matter.
    fa, fb, _ = mesh.face_edge_pairs()
    is_gap = np.zeros(len(mesh.tris), bool)
    is_gap[gap_faces] = True
    both = is_gap[fa] & is_gap[fb]
    labels = connected_components(len(mesh.tris), fa[both], fb[both])[gap_faces]
    min_area = params.tip_spacing**2
    out = []
    for label in np.unique(labels):
        region = gap_faces[labels == label]
        area = float(mesh.face_areas[region].sum())
        if area < min_area:
            continue
        centre = (mesh.face_centroids[region] * mesh.face_areas[region, None]).sum(axis=0) / area
        out.append(
            Finding(
                "coverage",
                WARNING,
                f"{area:.1f} mm² of overhang has no contact within "
                f"{COVERAGE_FACTOR * params.tip_spacing:.1f} mm",
                tuple(centre),
            )  # fmt: skip
        )
    return out
