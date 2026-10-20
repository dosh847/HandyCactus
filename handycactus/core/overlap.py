# SPDX-License-Identifier: GPL-3.0-or-later
"""Overlap repair after routing (spec section 6.4.4).

Routing checks clearance only at the nodes it places, one `step_z` apart, and the
distance query is unsigned. So a segment between two clear nodes can still cut
through a thin part of the model (an arm, a cloak edge), and smoothing and
decimation lengthen segments further. This pass samples every branch segment,
flags the ones that cross the model surface or come too close to it, and moves
nodes sideways (or splits the segment and pushes the new node out) until the
segments around them are clear. Whatever cannot be fixed is left for the
collision check to report.
"""

from __future__ import annotations

import numpy as np

from .graph import BRANCH, CONTACT, FOOT, JUNCTION, LANDING, SupportGraph
from .params import Params
from .spatial import Obstacles, points_inside
from .tips import BRANCH_HARD_CLEARANCE

SAMPLE_MM = 0.25  # spacing of clearance samples along a segment
ROUNDS = 8
AZIMUTHS = 16
# Offsets tried, as multiples of the node's radius + hard clearance.
OFFSET_SCALES = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)
MOVABLE = (BRANCH, JUNCTION)
MAX_SPLITS_PER_EDGE = 3  # split budget: bad edges at the start × this


def repair_overlaps(graph: SupportGraph, params: Params, obstacles: Obstacles) -> int:
    """Fix branch segments that run into the model, in place. Returns how many were left."""
    if len(graph) == 0:
        return 0
    tan_max = np.tan(np.radians(params.max_branch_angle))
    max_nodes = None
    for _ in range(ROUNDS):
        bad = _bad_edges(graph, params, obstacles)
        if len(bad) == 0:
            return 0
        if max_nodes is None:
            max_nodes = len(graph) + MAX_SPLITS_PER_EDGE * len(bad)
        fixed_any = False
        for child, parent in bad:
            if not _edge_bad(graph, child, parent, params, obstacles):
                continue  # an earlier move fixed it
            if _fix_edge(graph, child, parent, params, obstacles, tan_max, len(graph) < max_nodes):
                fixed_any = True
        if not fixed_any:
            break
    return len(_bad_edges(graph, params, obstacles))


def _checked_edges(graph: SupportGraph) -> np.ndarray:
    """(child, parent) edges the collision check looks at: not contact or landing tips."""
    e = graph.edges
    return e[(graph.kind[e[:, 0]] != CONTACT) & (graph.kind[e[:, 1]] != LANDING)]


def _limit(params: Params) -> float:
    """Gap below which a segment counts as overlapping (as in checks.check_collision)."""
    return min(0.5 * params.model_clearance, BRANCH_HARD_CLEARANCE) - 0.05


def _segment_gap(a, b, ra, rb, obstacles) -> tuple[float, float]:
    """(smallest surface gap along a->b, t where it occurs). Crossing the surface is -inf."""
    v = b - a
    length = float(np.linalg.norm(v))
    if length > 1e-9:
        hit = obstacles.ray_distance(a[None], (v / length)[None], length)[0]
        if np.isfinite(hit):
            return -np.inf, float(hit / length)
    n = max(1, int(np.ceil(length / SAMPLE_MM)))
    t = (np.arange(n + 1)) / n
    pts = a + v * t[:, None]
    gap = obstacles.nearest_distance(pts) - (ra + (rb - ra) * t)
    gap[points_inside(obstacles, pts)] = -np.inf  # buried: no surface crossing to find
    i = int(np.argmin(gap))
    return float(gap[i]), float(t[i])


def _edge_bad(graph, child, parent, params, obstacles) -> bool:
    if graph.kind[child] == CONTACT or graph.kind[parent] == LANDING:
        return False
    a, b = graph.pos[child], graph.pos[parent]
    gap, _ = _segment_gap(a, b, graph.radius[child], graph.radius[parent], obstacles)
    return gap < _limit(params)


def _bad_edges(graph, params, obstacles) -> list[tuple[int, int]]:
    """Edges whose samples come too close, lie inside, or whose axis crosses the surface."""
    e = _checked_edges(graph)
    if len(e) == 0:
        return []
    a, b = graph.pos[e[:, 0]], graph.pos[e[:, 1]]
    ra, rb = graph.radius[e[:, 0]], graph.radius[e[:, 1]]
    v = b - a
    length = np.linalg.norm(v, axis=1)
    counts = np.maximum(1, np.ceil(length / SAMPLE_MM).astype(int)) + 1
    seg = np.repeat(np.arange(len(e)), counts)
    t = np.concatenate([np.arange(c) / (c - 1) for c in counts])
    pts = a[seg] + v[seg] * t[:, None]
    gap = obstacles.nearest_distance(pts) - (ra[seg] + (rb - ra)[seg] * t)
    gap[points_inside(obstacles, pts)] = -np.inf
    worst = np.full(len(e), np.inf)
    np.minimum.at(worst, seg, gap)
    bad = worst < _limit(params)
    rest = np.flatnonzero(~bad & (length > 1e-9))
    if len(rest):
        hit = obstacles.ray_distance(a[rest], v[rest] / length[rest, None], float(length[rest].max()))
        bad[rest[hit <= length[rest]]] = True
    return [(int(c), int(p)) for c, p in e[bad]]


def _incident(graph: SupportGraph, node: int) -> list[tuple[int, int]]:
    out = [(int(c), node) for c in np.flatnonzero(graph.parent == node)]
    if graph.parent[node] >= 0:
        out.append((node, int(graph.parent[node])))
    return out


def _foot_below(graph: SupportGraph, node: int) -> int:
    p = int(graph.parent[node])
    return p if p >= 0 and graph.kind[p] == FOOT else -1


def _movable(graph: SupportGraph, node: int) -> bool:
    if graph.kind[node] not in MOVABLE or graph.locked[node] or graph.parent[node] < 0:
        return False
    # The node above a landing tip sets the tip's axis.
    return graph.kind[graph.parent[node]] != LANDING


def _lean_ok(graph, edges, tan_max) -> bool:
    for c, p in edges:
        if graph.kind[c] not in MOVABLE and graph.kind[c] != FOOT:
            continue  # knuckle -> branch follows the tip axis, not the branch cone
        d = graph.pos[c] - graph.pos[p]
        if np.hypot(d[0], d[1]) > tan_max * max(d[2], 0.0) + 1e-6:
            return False
    return True


def _worst_gap(graph, edges, params, obstacles) -> float:
    worst = np.inf
    for c, p in edges:
        if graph.kind[c] == CONTACT or graph.kind[p] == LANDING:
            continue
        g, _ = _segment_gap(graph.pos[c], graph.pos[p], graph.radius[c], graph.radius[p], obstacles)
        worst = min(worst, g)
    return worst


def _offsets(scale: float) -> np.ndarray:
    phis = 2 * np.pi * np.arange(AZIMUTHS) / AZIMUTHS
    rim = np.stack([np.cos(phis), np.sin(phis), np.zeros(AZIMUTHS)], axis=1)
    return np.vstack([rim * s * scale for s in OFFSET_SCALES])


def _try_move(graph, node, params, obstacles, tan_max, improve=False) -> bool:
    """Move `node` (and its foot) sideways to the nearest spot clearing its segments. With
    `improve`, settle for the spot that gains the most room when none clears them."""
    foot = _foot_below(graph, node)
    edges = _incident(graph, node)
    if foot >= 0:
        edges += _incident(graph, foot)
    edges = list(dict.fromkeys(edges))
    start = graph.pos[node].copy()
    foot_start = graph.pos[foot].copy() if foot >= 0 else None
    best_gap, best_off = _worst_gap(graph, edges, params, obstacles), None

    def place(off):
        graph.pos[node] = start + off
        if foot >= 0:
            graph.pos[foot] = foot_start + off

    for off in _offsets(graph.radius[node] + BRANCH_HARD_CLEARANCE):
        place(off)
        if not _lean_ok(graph, edges, tan_max):
            continue
        gap = _worst_gap(graph, edges, params, obstacles)
        if gap >= _limit(params):
            return True
        if gap > best_gap + 1e-3:
            best_gap, best_off = gap, off
    if improve and best_off is not None:
        place(best_off)
        return True
    place(np.zeros(3))
    return False


def _split_gap(a, ra, p, r, b, rb, child_leans, tan_max, obstacles) -> float:
    """Worst gap of a -> p -> b, or -inf if a segment leans past the cone."""
    worst = np.inf
    for (pa, rpa), (pb, rpb), lean in (((a, ra), (p, r), child_leans), ((p, r), (b, rb), True)):
        d = pa - pb
        if lean and np.hypot(d[0], d[1]) > tan_max * max(d[2], 0.0) + 1e-6:
            return -np.inf
        worst = min(worst, _segment_gap(pa, pb, rpa, rpb, obstacles)[0])
    return worst


def _try_split(graph, child, parent, params, obstacles, tan_max) -> bool:
    """Insert a node where the segment is worst and push it out sideways (to clear it, or
    failing that to gain the most room, so later rounds can keep going)."""
    a, b = graph.pos[child], graph.pos[parent]
    ra, rb = graph.radius[child], graph.radius[parent]
    now, t = _segment_gap(a, b, ra, rb, obstacles)
    t = float(np.clip(t, 0.2, 0.8))
    mid = a + (b - a) * t
    r = float(ra + (rb - ra) * t)
    leans = graph.kind[child] in MOVABLE
    best_gap, best = now, None
    for off in _offsets(r + BRANCH_HARD_CLEARANCE):
        gap = _split_gap(a, ra, mid + off, r, b, rb, leans, tan_max, obstacles)
        if gap >= _limit(params):
            best = mid + off
            break
        if gap > best_gap + 1e-3:
            best_gap, best = gap, mid + off
    if best is None:
        return False
    _insert(graph, child, parent, best, r)
    return True


_FIELDS = ("pos", "kind", "radius", "parent", "tree", "tip", "target", "dir", "sphere", "locked", "flags")


def _insert(graph: SupportGraph, child: int, parent: int, pos, radius: float) -> None:
    """Append a branch node between child and parent (arrays grow by one row)."""
    n = len(graph)
    for name in _FIELDS:
        arr = getattr(graph, name)
        setattr(graph, name, np.concatenate([arr, arr[parent : parent + 1]]))
    graph.pos[n] = pos
    graph.kind[n] = BRANCH
    graph.radius[n] = radius
    graph.parent[n] = parent
    graph.tip[n] = -1
    graph.target[n] = -1
    graph.dir[n] = 0.0
    graph.sphere[n] = 0.0
    graph.locked[n] = False
    graph.flags[n] = 0
    graph.tree[n] = graph.tree[child]
    graph.parent[child] = n


def _fix_edge(graph, child, parent, params, obstacles, tan_max, may_split: bool) -> bool:
    movable = [n for n in (child, parent) if _movable(graph, n)]
    for node in movable:
        if _try_move(graph, node, params, obstacles, tan_max):
            return True
    if may_split and _try_split(graph, child, parent, params, obstacles, tan_max):
        return True
    return any(_try_move(graph, n, params, obstacles, tan_max, improve=True) for n in movable)
