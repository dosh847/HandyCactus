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

from dataclasses import dataclass, field

import numpy as np

from .graph import BRANCH, CONTACT, FOOT, JUNCTION, KNUCKLE, LANDING, ROOT_KINDS, GraphBuilder, SupportGraph
from .params import Params
from .spatial import Obstacles
from .tips import BRANCH_HARD_CLEARANCE, Tips

# Landing on the model: a landing tip is at most LANDING_REACH × tip_length long and
# needs a surface facing up at least this much (normal z).
LANDING_REACH = 2.0
LANDING_MIN_UP = 0.3
MERGE_SLACK = 0.3  # mm beyond the larger radius at which two heads merge
# Branches aim for radius + model_clearance from the model, but may pass as close as
# radius + HARD_CLEARANCE when that is the only way down.
HARD_CLEARANCE = BRANCH_HARD_CLEARANCE
AVOID_AZIMUTHS = 16
DECIMATE_DEGREES = 3.0  # straight-enough runs of branch nodes collapse to one segment


@dataclass
class RoutingResult:
    graph: SupportGraph
    unreachable_tips: np.ndarray  # tip indices that got no support (tips + routing)
    # With `attach_to`: (node in `graph`, node in attach_to) pairs; the first is a root in
    # `graph` that continues into the existing graph (see editing.merge_routed).
    attachments: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class _Head:
    node: int  # graph node at the head's current position
    pos: np.ndarray
    radius: float
    awake: bool = False
    prev: np.ndarray | None = None  # position before this level's move
    reach: float = 0.0  # horizontal reach of this level's move (cone limit)
    blocked: bool = False  # the model is straight below: escaping, not merging
    escape: np.ndarray | None = None  # unit direction of a straight free path to the bed
    landing: np.ndarray | None = None  # model surface point this head is heading for


def route(
    tips: Tips, params: Params, obstacles: Obstacles, attach_to: SupportGraph | None = None
) -> RoutingResult:
    """Route tips to the bed (or the model). With `attach_to`, heads may also end by joining
    a reachable unlocked branch or junction of that existing graph (incremental routing,
    spec 6.5)."""
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

    anchors = _attach_candidates(attach_to)
    attachments: list[tuple[int, int]] = []
    while heads and level > floor + 1e-9:
        new_level = max(level - params.step_z, floor)
        for h in heads:
            if not h.awake and h.pos[2] > new_level:
                h.awake = True
        if anchors is not None:
            for h in [h for h in heads if h.awake]:
                target = _attach(h, anchors, attach_to, params, obstacles, tan)
                if target is not None:
                    attachments.append((h.node, target))
                    heads.remove(h)
        awake = [h for h in heads if h.awake]
        targets = _desired_xy(awake, heads, params, obstacles, tan, new_level)
        moved: list[_Head] = []
        landing = bool(params.land_on_model)
        for h, target_xy in zip(awake, targets, strict=True):
            if landing and h.landing is not None and _land(b, h, h.landing, params, obstacles, tan):
                continue  # done: this head ends on the model
            new_pos = _move(h, target_xy, new_level, params, obstacles, tan)
            if new_pos is None:
                spot = (
                    _find_landing(h.pos, obstacles, tan, LANDING_REACH * params.tip_length)
                    if landing
                    else None
                )
                if spot is None or not _land(b, h, spot, params, obstacles, tan):
                    stuck_nodes.append(h.node)
                continue
            dz = h.pos[2] - new_level
            # Grow with the taper, but never into the hard clearance next to the model.
            room = float(obstacles.nearest_distance(new_pos[None])[0]) - HARD_CLEARANCE
            grown = min(params.max_radius, h.radius + params.taper_rate * dz)
            h.radius = max(min(grown, room), min(h.radius, grown), params.min_radius)
            h.prev, h.reach = h.pos, dz * tan
            h.pos = new_pos
            moved.append(h)
        stuck = {id(h) for h in awake} - {id(h) for h in moved}
        heads = [h for h in heads if id(h) not in stuck]
        _merge_and_extend(b, [h for h in heads if h.awake], heads, params, obstacles)
        level = new_level

    for h in heads:  # flared feet
        foot_r = max(params.foot_radius, h.radius)
        if floor > 1e-9:
            foot = b.add((h.pos[0], h.pos[1], 0.0), FOOT, foot_r)
            b.set_parent(h.node, foot)
        else:
            b.set_kind(h.node, FOOT)

    graph = b.build()
    attached = np.zeros(len(graph), bool)
    for node, _ in attachments:
        attached[node] = True
    keep = _rooted(graph, attached)  # drops stuck trees
    remap = np.cumsum(keep) - 1
    attachments = [(int(remap[n]), t) for n, t in attachments if keep[n]]
    graph = graph.subset(keep)
    graph = _decimate(graph)  # attached roots have no parent, so they are never removed
    attachments = _remap_after_decimate(graph, attachments)
    graph.renumber_trees()
    junction = graph.kind == JUNCTION
    graph.sphere[junction] = graph.radius[junction]

    supported = np.unique(graph.tip[graph.kind == CONTACT])
    all_tips = np.arange(len(tips))
    return RoutingResult(graph, np.setdiff1d(all_tips, supported), attachments)


def _attach_candidates(graph: SupportGraph | None) -> np.ndarray | None:
    """Existing nodes a new branch may join: unlocked branch and junction nodes."""
    if graph is None or len(graph) == 0:
        return None
    ok = np.isin(graph.kind, (BRANCH, JUNCTION)) & ~graph.locked
    return np.flatnonzero(ok)


def _attach(h, anchors, graph, params, obstacles, tan) -> int | None:
    """An existing node this head can join now: below it, inside its cone, within
    merge_distance, with a clear line to it. The nearest wins."""
    if len(anchors) == 0:
        return None
    pos = graph.pos[anchors]
    dz = h.pos[2] - pos[:, 2]
    d_xy = np.linalg.norm(pos[:, :2] - h.pos[:2], axis=1)
    dist = np.linalg.norm(pos - h.pos, axis=1)
    ok = (dz > 1e-6) & (d_xy <= dz * tan + 1e-9) & (dist <= params.merge_distance)
    for i in np.flatnonzero(ok)[np.argsort(dist[ok])][:4]:
        direction = (pos[i] - h.pos) / dist[i]
        hit = obstacles.ray_distance(h.pos[None], direction[None], dist[i])[0]
        if not np.isfinite(hit):
            return int(anchors[i])
    return None


def _rooted(graph: SupportGraph, attached: np.ndarray) -> np.ndarray:
    """Nodes whose root is a foot, a landing, or attaches to an existing graph."""
    if len(graph) == 0:
        return np.zeros(0, bool)
    roots = graph.roots()
    return np.isin(graph.kind[roots], ROOT_KINDS) | attached[roots]


def _remap_after_decimate(graph: SupportGraph, attachments):
    """Decimation keeps every root, so attached roots are still the roots with no parent;
    match them back by order."""
    if not attachments:
        return attachments
    roots = np.flatnonzero((graph.parent < 0) & ~np.isin(graph.kind, ROOT_KINDS))
    return [(int(r), t) for r, (_, t) in zip(roots, sorted(attachments), strict=True)]


def _desired_xy(awake, heads, params, obstacles, tan, new_level) -> list[np.ndarray]:
    """Where each awake head would like to go (xy), before collision checks.

    A head with the model straight below it is *blocked*: it follows a
    straight path to the bed found inside its cone (the most vertical one),
    and takes no part in merging until it is clear. Free heads lean toward
    the nearest free head they could merge with, or go straight down.
    """
    if not awake:
        return []
    a_pos = np.array([h.pos for h in awake])
    down = np.tile([0.0, 0.0, -1.0], (len(awake), 1))
    hit = obstacles.ray_distance(a_pos, down, float(a_pos[:, 2].max()) + 1.0)
    for h, d in zip(awake, hit, strict=True):
        h.blocked = bool(np.isfinite(d) and d < h.pos[2])

    all_pos = np.array([h.pos for h in heads])
    free = np.array([not h.blocked for h in heads])
    d_xy = np.linalg.norm(a_pos[:, None, :2] - all_pos[None, :, :2], axis=2)
    below_or_level = all_pos[None, :, 2] <= a_pos[:, None, 2] + 1e-9
    index_of = {id(h): i for i, h in enumerate(heads)}
    awake_idx = np.array([index_of[id(h)] for h in awake])
    same = awake_idx[:, None] == np.arange(len(heads))[None, :]
    candidate = below_or_level & ~same & free[None, :] & (d_xy <= params.merge_distance)
    d_xy = np.where(candidate, d_xy, np.inf)
    partner = np.argmin(d_xy, axis=1)
    has_partner = np.isfinite(d_xy[np.arange(len(awake)), partner])

    out = []
    for i, h in enumerate(awake):
        if h.blocked:
            direction = _escape(h, obstacles, tan)
            if h.escape is not None:
                h.landing = None
            elif params.land_on_model:
                # No way to the bed: head for a spot on the model to land on.
                if h.landing is None:
                    h.landing = _find_landing(h.pos, obstacles, tan, np.inf)
                if h.landing is not None:
                    out.append(h.landing[:2].copy())
                    continue
            dz = h.pos[2] - new_level
            out.append(h.pos[:2] + direction[:2] / max(-direction[2], 1e-6) * dz)
        elif has_partner[i]:
            h.escape = None
            other = heads[partner[i]]
            if other.awake:  # meet halfway; the other head walks toward us too
                out.append((h.pos[:2] + other.pos[:2]) / 2)
            else:
                out.append(other.pos[:2].copy())
        else:
            h.escape = None
            out.append(h.pos[:2].copy())
    return out


def _escape(h, obstacles, tan) -> np.ndarray:
    """A unit direction inside the branch cone whose ray reaches the bed unobstructed.

    Keeps the previous escape while it stays free. Otherwise tries cone
    directions from most vertical outward; with no free one, takes the
    direction that runs free the longest.
    """
    if h.escape is not None and _reaches_bed(h.pos, h.escape[None], obstacles)[0]:
        return h.escape
    dirs = _cone_dirs(tan)
    free = _reaches_bed(h.pos, dirs, obstacles)
    if free.any():
        h.escape = dirs[int(np.argmax(free))]  # dirs are ordered most vertical first
        return h.escape
    h.escape = None
    length = h.pos[2] / np.maximum(-dirs[:, 2], 1e-6)
    dist = obstacles.ray_distance(np.tile(h.pos, (len(dirs), 1)), dirs, float(length.max()) + 1.0)
    return dirs[int(np.argmax(np.where(np.isfinite(dist), dist, length)))]


def _find_landing(pos, obstacles, tan, max_distance) -> np.ndarray | None:
    """The nearest-to-vertical upward-facing model surface point inside the cone, if any."""
    dirs = _cone_dirs(tan)
    reach = min(max_distance, pos[2] + 1.0) if np.isfinite(max_distance) else pos[2] + 1.0
    dist, normals = obstacles.ray_hit(np.tile(pos, (len(dirs), 1)), dirs, float(reach))
    ok = np.isfinite(dist) & (dist <= max_distance) & (normals[:, 2] >= LANDING_MIN_UP)
    if not ok.any():
        return None
    i = int(np.argmax(ok))  # dirs are ordered most vertical first
    return pos + dirs[i] * dist[i]


def _land(b: GraphBuilder, h, spot, params, obstacles, tan) -> bool:
    """End the head with a landing tip onto `spot` if it is close, inside the cone and in sight."""
    v = spot - h.pos
    dist = float(np.linalg.norm(v))
    if dist > LANDING_REACH * params.tip_length or dist < 1e-6:
        return False
    direction = v / dist
    if -direction[2] < np.cos(np.arctan(tan)) - 1e-9:
        return False  # would lean more than max_branch_angle
    hit, normal = obstacles.ray_hit(h.pos[None], direction[None], dist + 0.5)
    if not np.isfinite(hit[0]) or hit[0] < dist - 0.05 or normal[0, 2] < LANDING_MIN_UP:
        return False  # something else is in the way, or the surface faces down
    b.set_sphere(h.node, params.knuckle_diameter / 2)  # break point above the landing tip
    land = b.add(
        h.pos + direction * (hit[0] + params.penetration), LANDING, params.contact_diameter / 2,
        direction=-direction,
    )  # fmt: skip
    b.set_parent(h.node, land)
    return True


def _cone_dirs(tan) -> np.ndarray:
    dirs = _ESCAPE_DIRS_CACHE.get(tan)
    if dirs is None:
        dirs = _cone_directions(np.degrees(np.arctan(tan)))
        _ESCAPE_DIRS_CACHE[tan] = dirs
    return dirs


def _reaches_bed(pos, dirs, obstacles) -> np.ndarray:
    length = pos[2] / np.maximum(-dirs[:, 2], 1e-6)
    dist = obstacles.ray_distance(np.tile(pos, (len(dirs), 1)), dirs, float(length.max()) + 1.0)
    return ~np.isfinite(dist) | (dist >= length)


def _cone_directions(max_angle_deg: float) -> np.ndarray:
    """Unit directions in the downward cone, most vertical first (rings of 16)."""
    out = [(0.0, 0.0, -1.0)]
    for frac in (0.25, 0.5, 0.75, 1.0):
        a = np.radians(max_angle_deg * frac)
        for k in range(AVOID_AZIMUTHS):
            phi = 2 * np.pi * k / AVOID_AZIMUTHS
            out.append((np.sin(a) * np.cos(phi), np.sin(a) * np.sin(phi), -np.cos(a)))
    return np.array(out)


_ESCAPE_DIRS_CACHE: dict[float, np.ndarray] = {}


def _move(h, target_xy, new_level, params, obstacles, tan) -> np.ndarray | None:
    dz = h.pos[2] - new_level
    reach = dz * tan
    want = target_xy - h.pos[:2]
    dist = np.linalg.norm(want)
    step_xy = want if dist <= reach else want / dist * reach
    desired = np.array([h.pos[0] + step_xy[0], h.pos[1] + step_xy[1], new_level])

    need = h.radius + params.model_clearance
    hard = h.radius + HARD_CLEARANCE
    d_old = float(obstacles.nearest_distance(h.pos[None])[0])
    d_new = float(obstacles.nearest_distance(desired[None])[0])
    if d_new >= need or d_new >= min(d_old, need):
        return desired

    # Try the cone's rim and half-rim plus straight down. Prefer moves that keep the full
    # clearance (or do not lose any), nearest the wish; failing that, any move that keeps
    # the hard minimum, the farthest from the model first.
    phis = 2 * np.pi * np.arange(AVOID_AZIMUTHS) / AVOID_AZIMUTHS
    rim = np.stack([np.cos(phis), np.sin(phis)], axis=1)
    offsets = np.vstack([[0.0, 0.0], rim * reach, rim * reach * 0.5])
    cands = np.vstack([desired, np.column_stack([h.pos[:2] + offsets, np.full(len(offsets), new_level)])])
    d = np.concatenate([[d_new], obstacles.nearest_distance(cands[1:])])
    ok = (d >= need) | (d >= min(d_old, need))
    if ok.any():
        score = np.linalg.norm(cands[:, :2] - desired[:2], axis=1)
        score[~ok] = np.inf
        return cands[int(np.argmin(score))]
    tight = d >= hard
    if tight.any():
        # Near the wish, among the moves that keep the most room (within 0.1 mm of the best).
        roomy = tight & (d >= d[tight].max() - 0.1)
        score = np.where(roomy, np.linalg.norm(cands[:, :2] - desired[:2], axis=1), np.inf)
        return cands[int(np.argmin(score))]
    return None


def _merge_and_extend(b: GraphBuilder, awake, heads, params, obstacles) -> None:
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

    def has_room(idx, p):
        # The merged (thicker) branch must keep the hard clearance at the junction.
        r = min(params.max_radius, float(np.sqrt((rad[idx] ** 2).sum())) * params.merge_growth)
        return float(obstacles.nearest_distance(p[None])[0]) >= r + HARD_CLEARANCE

    blocked = np.array([h.blocked for h in awake])
    pairs = np.argwhere(np.triu((d <= limit) & ~blocked[:, None] & ~blocked[None, :], k=1))
    for i, j in sorted(pairs.tolist(), key=lambda ij: d[ij[0], ij[1]]):
        gi, gj = find(i), find(j)
        if gi == gj:
            continue
        idx = np.array([k for k in range(len(awake)) if find(k) in (gi, gj)])
        p = junction(idx)
        if within_cone(idx, p) and has_room(idx, p):
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
        if p < 0 or c < 0 or graph.kind[p] in ROOT_KINDS:
            continue  # the node above a foot or landing sets where the flare / tip starts
        a = graph.pos[node] - graph.pos[c]
        bvec = graph.pos[p] - graph.pos[node]
        la, lb = np.linalg.norm(a), np.linalg.norm(bvec)
        if la < 1e-9 or lb < 1e-9 or (a @ bvec) / (la * lb) >= cos_limit:
            parent[c] = p
            child_of[p] = c if children[p] == 1 else child_of[p]
            removed[node] = True
    graph.parent = parent
    return graph.subset(~removed)
