# SPDX-License-Identifier: GPL-3.0-or-later
"""Branch routing by greedy cone merging (spec section 6.4).

A level sweeps down from the highest knuckle to the bed in `step_z` steps.
Each branch head "wakes" when the level reaches it and then moves down one
level per step. It leans toward the nearest head it could merge with, or
straight down when nothing is near (or toward the most open direction when
straight down is blocked). It never leans more than `max_branch_angle` from
vertical. A move that would bring it within `radius + model_clearance` of
the model is replaced by the best clear direction. Heads that touch at a
level merge into a junction with an area-conserving radius. At `foot_height`
every head drops to a flared foot on the bed. A head with no clear move is
stuck, and its whole tree is dropped (its tips are reported unreachable).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .graph import BRANCH, CONTACT, FOOT, JUNCTION, KNUCKLE, GraphBuilder, SupportGraph
from .params import Params
from .spatial import Obstacles
from .tips import Tips

MERGE_SLACK = 0.3  # mm beyond the larger radius at which two heads merge
LOOKAHEAD = 30.0  # mm: how far down a head looks for the model in its way
AVOID_AZIMUTHS = 16
DECIMATE_DEGREES = 3.0  # straight-enough runs of branch nodes collapse to one segment


@dataclass
class RoutingResult:
    graph: SupportGraph
    unreachable_tips: np.ndarray  # tip indices that got no support (tips + routing)


@dataclass
class _Head:
    node: int  # graph node at the head's current position
    pos: np.ndarray
    radius: float
    awake: bool = False
    prev: np.ndarray | None = None  # position before this level's move
    reach: float = 0.0  # horizontal reach of this level's move (cone limit)


def route(tips: Tips, params: Params, obstacles: Obstacles) -> RoutingResult:
    b = GraphBuilder()
    r_contact = params.contact_diameter / 2
    r_knuckle = params.knuckle_diameter / 2
    heads: list[_Head] = []
    for i in np.flatnonzero(tips.ok):
        k = b.add(
            tips.knuckle[i], KNUCKLE, params.min_radius, tip=int(i), target=int(tips.target[i]),
            direction=tips.axis[i], sphere=r_knuckle,
        )  # fmt: skip
        b.add(
            tips.contact[i], CONTACT, r_contact, parent=k, tip=int(i), target=int(tips.target[i]),
            direction=tips.axis[i],
        )  # fmt: skip
        heads.append(_Head(k, tips.knuckle[i].copy(), params.min_radius))

    stuck_nodes: list[int] = []
    tan = np.tan(np.radians(params.max_branch_angle))
    floor = max(params.foot_height, 0.0)
    level = max((h.pos[2] for h in heads), default=floor)

    while heads and level > floor + 1e-9:
        new_level = max(level - params.step_z, floor)
        for h in heads:
            if not h.awake and h.pos[2] > new_level:
                h.awake = True
        awake = [h for h in heads if h.awake]
        targets = _desired_xy(awake, heads, params, obstacles, tan)
        moved: list[_Head] = []
        for h, target_xy in zip(awake, targets, strict=True):
            new_pos = _move(h, target_xy, new_level, params, obstacles, tan)
            if new_pos is None:
                stuck_nodes.append(h.node)
                continue
            dz = h.pos[2] - new_level
            h.radius = min(params.max_radius, h.radius + params.taper_rate * dz)
            h.prev, h.reach = h.pos, dz * tan
            h.pos = new_pos
            moved.append(h)
        stuck = {id(h) for h in awake} - {id(h) for h in moved}
        heads = [h for h in heads if id(h) not in stuck]
        _merge_and_extend(b, [h for h in heads if h.awake], heads, params)
        level = new_level

    for h in heads:  # flared feet
        foot_r = max(params.foot_radius, h.radius)
        if floor > 1e-9:
            foot = b.add((h.pos[0], h.pos[1], 0.0), FOOT, foot_r)
            b.set_parent(h.node, foot)
        else:
            b.set_kind(h.node, FOOT)

    graph = b.build()
    graph = _drop_rootless(graph)
    graph = _decimate(graph)
    graph.renumber_trees()
    junction = graph.kind == JUNCTION
    graph.sphere[junction] = graph.radius[junction]

    supported = np.unique(graph.tip[graph.kind == CONTACT])
    all_tips = np.arange(len(tips))
    return RoutingResult(graph, np.setdiff1d(all_tips, supported))


def _desired_xy(awake, heads, params, obstacles, tan) -> list[np.ndarray]:
    """Where each awake head would like to go (xy), before collision checks."""
    if not awake:
        return []
    a_pos = np.array([h.pos for h in awake])
    all_pos = np.array([h.pos for h in heads])
    d_xy = np.linalg.norm(a_pos[:, None, :2] - all_pos[None, :, :2], axis=2)
    below_or_level = all_pos[None, :, 2] <= a_pos[:, None, 2] + 1e-9
    index_of = {id(h): i for i, h in enumerate(heads)}
    awake_idx = np.array([index_of[id(h)] for h in awake])
    same = awake_idx[:, None] == np.arange(len(heads))[None, :]
    candidate = below_or_level & ~same & (d_xy <= params.merge_distance)
    d_xy = np.where(candidate, d_xy, np.inf)
    partner = np.argmin(d_xy, axis=1)
    has_partner = np.isfinite(d_xy[np.arange(len(awake)), partner])

    out = []
    blocked_idx = []
    for i, h in enumerate(awake):
        if has_partner[i]:
            other = heads[partner[i]]
            if other.awake:  # meet halfway; the other head walks toward us too
                out.append((h.pos[:2] + other.pos[:2]) / 2)
            else:
                out.append(other.pos[:2].copy())
        else:
            out.append(h.pos[:2].copy())
            blocked_idx.append(i)

    # Heads going straight down: if the model is in the way, head for open space.
    if blocked_idx:
        origins = a_pos[blocked_idx]
        down = np.tile([0.0, 0.0, -1.0], (len(origins), 1))
        hit = obstacles.ray_distance(origins, down, LOOKAHEAD)
        for j, i in enumerate(blocked_idx):
            if np.isfinite(hit[j]) and hit[j] < origins[j, 2]:
                out[i] = _open_direction(awake[i], params, obstacles, tan)
    return out


def _open_direction(h, params, obstacles, tan) -> np.ndarray:
    """xy target in the cone direction whose downward-slanted ray runs free the longest."""
    phis = 2 * np.pi * np.arange(AVOID_AZIMUTHS) / AVOID_AZIMUTHS
    horiz = np.stack([np.cos(phis), np.sin(phis), np.zeros_like(phis)], axis=1)
    dirs = horiz * np.sin(np.arctan(tan)) + np.array([0, 0, -np.cos(np.arctan(tan))])
    origins = np.tile(h.pos, (AVOID_AZIMUTHS, 1))
    free = obstacles.ray_distance(origins, dirs, LOOKAHEAD)
    free = np.where(np.isfinite(free), free, LOOKAHEAD * 2)
    best = int(np.argmax(free))
    return h.pos[:2] + horiz[best, :2] * LOOKAHEAD


def _move(h, target_xy, new_level, params, obstacles, tan) -> np.ndarray | None:
    dz = h.pos[2] - new_level
    reach = dz * tan
    want = target_xy - h.pos[:2]
    dist = np.linalg.norm(want)
    step_xy = want if dist <= reach else want / dist * reach
    desired = np.array([h.pos[0] + step_xy[0], h.pos[1] + step_xy[1], new_level])

    need = h.radius + params.model_clearance
    d_old = float(obstacles.nearest_distance(h.pos[None])[0])
    d_new = float(obstacles.nearest_distance(desired[None])[0])
    if d_new >= need or d_new >= d_old:
        return desired

    # Try the cone's rim and half-rim plus straight down; prefer clear moves near the wish.
    phis = 2 * np.pi * np.arange(AVOID_AZIMUTHS) / AVOID_AZIMUTHS
    rim = np.stack([np.cos(phis), np.sin(phis)], axis=1)
    offsets = np.vstack([[0.0, 0.0], rim * reach, rim * reach * 0.5])
    cands = np.column_stack([h.pos[:2] + offsets, np.full(len(offsets), new_level)])
    d = obstacles.nearest_distance(cands)
    ok = (d >= need) | (d >= d_old)
    if ok.any():
        score = np.linalg.norm(cands[:, :2] - desired[:2], axis=1)
        score[~ok] = np.inf
        return cands[int(np.argmin(score))]
    best = int(np.argmax(d))
    if d[best] > d_old - 1e-6:
        return cands[best]
    return None


def _merge_and_extend(b: GraphBuilder, awake, heads, params) -> None:
    """Add this level's nodes: junctions for touching heads, plain branch nodes otherwise."""
    if not awake:
        return
    pos = np.array([h.pos for h in awake])
    rad = np.array([h.radius for h in awake])
    d = np.linalg.norm(pos[:, None, :2] - pos[None, :, :2], axis=2)
    limit = np.maximum(rad[:, None], rad[None, :]) + MERGE_SLACK
    group = list(range(len(awake)))

    def find(i):
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    prev = np.array([h.prev[:2] for h in awake])
    reach = np.array([h.reach for h in awake]) + 1e-6

    def junction(idx):
        w = rad[idx] ** 2
        return (pos[idx] * w[:, None]).sum(axis=0) / w.sum()

    def within_cone(idx, p):
        # Every member must reach the junction from its previous node inside its cone.
        return bool(np.all(np.linalg.norm(prev[idx] - p[:2], axis=1) <= reach[idx]))

    pairs = np.argwhere(np.triu(d <= limit, k=1))
    for i, j in sorted(pairs.tolist(), key=lambda ij: d[ij[0], ij[1]]):
        gi, gj = find(i), find(j)
        if gi == gj:
            continue
        idx = np.array([k for k in range(len(awake)) if find(k) in (gi, gj)])
        if within_cone(idx, junction(idx)):
            group[gi] = gj
    members: dict[int, list[int]] = {}
    for i in range(len(awake)):
        members.setdefault(find(i), []).append(i)

    dead: set[int] = set()
    for idx in members.values():
        if len(idx) == 1:
            h = awake[idx[0]]
            node = b.add(h.pos, BRANCH, h.radius)
            b.set_parent(h.node, node)
            h.node = node
            continue
        p = junction(np.array(idx))
        r = min(params.max_radius, float(np.sqrt((rad[idx] ** 2).sum())) * params.merge_growth)
        node = b.add(p, JUNCTION, r)
        for i in idx:
            b.set_parent(awake[i].node, node)
        keep = awake[idx[0]]
        keep.node, keep.pos, keep.radius = node, p, r
        dead.update(id(awake[i]) for i in idx[1:])
    heads[:] = [h for h in heads if id(h) not in dead]


def _drop_rootless(graph: SupportGraph) -> SupportGraph:
    """Remove every tree whose root is not a foot (stuck branches)."""
    if len(graph) == 0:
        return graph
    roots = graph.roots()
    return graph.subset(graph.kind[roots] == FOOT)


def _decimate(graph: SupportGraph) -> SupportGraph:
    """Collapse straight runs of single-child branch nodes into one segment."""
    n = len(graph)
    if n == 0:
        return graph
    children = graph.children_count()
    child_of = np.full(n, -1)
    has_parent = graph.parent >= 0
    child_of[graph.parent[has_parent]] = np.flatnonzero(has_parent)  # valid where children == 1
    parent = graph.parent.copy()
    removed = np.zeros(n, bool)
    cos_limit = np.cos(np.radians(DECIMATE_DEGREES))
    for node in np.flatnonzero((graph.kind == BRANCH) & (children == 1)):
        c, p = child_of[node], parent[node]
        if p < 0 or c < 0:
            continue
        a = graph.pos[node] - graph.pos[c]
        bvec = graph.pos[p] - graph.pos[node]
        la, lb = np.linalg.norm(a), np.linalg.norm(bvec)
        if la < 1e-9 or lb < 1e-9 or (a @ bvec) / (la * lb) >= cos_limit:
            parent[c] = p
            child_of[p] = c if children[p] == 1 else child_of[p]
            removed[node] = True
    graph.parent = parent
    return graph.subset(~removed)
