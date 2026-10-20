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

from .avoidance import Avoidance
from .graph import (
    BRANCH,
    CONTACT,
    FLAG_THICK_LANDING,
    FOOT,
    JUNCTION,
    KNUCKLE,
    LANDING,
    ROOT_KINDS,
    GraphBuilder,
    SupportGraph,
)
from .params import Params, radius_at
from .smoothing import smooth
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
# Trunks run straight up from the foot for this long (Orca keeps trunks vertical near
# the bed): no leaning toward merge partners below it, and smoothing leaves it alone.
TRUNK_BASE_MM = 0.4
JUNCTION_PROJECTIONS = 8
FOOT_FLARE = 1.3  # a foot is at least this × its trunk's radius
DECIMATE_TOLERANCE = 0.03  # mm a collapsed segment may stray (or fatten) past a skipped node


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
    top: float = 0.0  # height of the highest knuckle this head carries (sets its radius)
    tips: int = 1  # how many tips this head carries (sets its radius too)
    awake: bool = False
    prev: np.ndarray | None = None  # position before this level's move
    reach: float = 0.0  # horizontal reach of this level's move (cone limit)
    blocked: bool = False  # the model is straight below: escaping, not merging
    escape: np.ndarray | None = None  # unit direction of a straight free path to the bed
    landing: np.ndarray | None = None  # model surface point this head is heading for
    steered: bool = False  # this level's target was redirected to keep the bed reachable
    mate: _Head | None = None  # the head this one is walking to meet (kept until they merge)
    host: _Head | None = None  # a free head this blocked head is steering to, to merge into


def route(
    tips: Tips,
    params: Params,
    obstacles: Obstacles,
    attach_to: SupportGraph | None = None,
    avoidance: Avoidance | str | None = "auto",
    progress=None,
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
        heads.append(_Head(k, tips.knuckle[i].copy(), params.min_radius, top=float(tips.knuckle[i][2])))

    stuck_nodes: list[int] = []
    tan = np.tan(np.radians(params.max_branch_angle))
    tan_pref = np.tan(np.radians(min(params.preferred_branch_angle, params.max_branch_angle)))
    floor = max(params.foot_height, 0.0)
    level = max((h.pos[2] for h in heads), default=floor)
    av = build_avoidance(obstacles, tips, params) if avoidance == "auto" else avoidance

    anchors = _attach_candidates(attach_to)
    attachments: list[tuple[int, int]] = []
    top = level
    while heads and level > floor + 1e-9:
        if progress is not None:
            progress((top - level) / max(top - floor, 1e-9))
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
        targets = _desired_xy(awake, heads, params, obstacles, tan, new_level, av)
        moved: list[_Head] = []
        landing = bool(params.land_on_model)
        for h, target_xy in zip(awake, targets, strict=True):
            if landing and h.landing is not None and _land(b, h, h.landing, params, obstacles, tan):
                continue  # done: this head ends on the model
            new_pos = _move(h, target_xy, new_level, params, obstacles, tan, tan_pref, av)
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
            # The radius follows the height below the highest tip carried (as in Cura and
            # Orca: no jump at merges), but never grows into the hard clearance next to the
            # model, and never shrinks going down.
            room = float(obstacles.nearest_distance(new_pos[None])[0]) - HARD_CLEARANCE
            grown = _radius_at(params, h.top - new_level, h.tips)
            radius = max(min(grown, room), min(h.radius, grown), params.min_radius)
            if av is not None and radius > h.radius and not h.blocked:
                # Never thicken past the point where the bed is no longer reachable.
                if not av.can_reach_bed(new_pos, radius)[0]:
                    radius = h.radius
            h.radius = radius
            h.prev, h.reach = h.pos, dz * tan
            h.pos = new_pos
            moved.append(h)
        stuck = {id(h) for h in awake} - {id(h) for h in moved}
        heads = [h for h in heads if id(h) not in stuck]
        _merge_and_extend(b, [h for h in heads if h.awake], heads, params, obstacles, av)
        level = new_level

    for h in heads:  # flared feet
        foot_r = max(params.foot_radius, FOOT_FLARE * h.radius)  # always wider than the trunk
        if floor > 1e-9:
            foot = b.add((h.pos[0], h.pos[1], 0.0), FOOT, foot_r)
            b.set_parent(h.node, foot)
        else:
            b.set_kind(h.node, FOOT)

    graph = b.build()
    attached = np.zeros(len(graph), bool)
    for node, _ in attachments:
        attached[node] = True
    _slim_landing_trees(graph, params)
    keep = _rooted(graph, attached)  # drops stuck trees
    remap = np.cumsum(keep) - 1
    attachments = [(int(remap[n]), t) for n, t in attachments if keep[n]]
    graph = graph.subset(keep)
    fixed = attached[keep] | (graph.pos[:, 2] < _base_height(params) - 1e-9)
    smooth(graph, params, obstacles, fixed, HARD_CLEARANCE)
    graph = _decimate(graph)  # attached roots have no parent, so they are never removed
    attachments = _remap_after_decimate(graph, attachments)
    graph.renumber_trees()
    junction = graph.kind == JUNCTION
    graph.sphere[junction] = graph.radius[junction]
    # The ball above a landing tip fills the joint between the branch and the tip, which
    # leans another way: as wide as the branch (a tip-sized ball left a wedge-shaped gap).
    above_landing = np.flatnonzero((graph.parent >= 0) & (graph.kind[np.maximum(graph.parent, 0)] == LANDING))
    graph.sphere[above_landing] = graph.radius[above_landing]
    # A ball never reaches below the bed.
    graph.sphere = np.minimum(graph.sphere, np.maximum(graph.pos[:, 2], 0.0))

    supported = np.unique(graph.tip[graph.kind == CONTACT])
    all_tips = np.arange(len(tips))
    return RoutingResult(graph, np.setdiff1d(all_tips, supported), attachments)


def _slim_landing_trees(graph: SupportGraph, params: Params) -> None:
    """Trees standing on the model are at most max_landing_diameter thick. A landing only
    happens with no way to the bed, so a thicker tree is slimmed to land anyway and its
    landing is flagged (checks report it as a warning)."""
    landings = np.flatnonzero(graph.kind == LANDING)
    if len(landings) == 0:
        return
    cap = params.max_landing_diameter / 2
    roots = graph.roots()
    for land in landings:
        tree = roots == land
        tree[land] = False  # the landing tip keeps its contact radius
        if (graph.radius[tree] > cap + 1e-9).any():
            graph.radius[tree] = np.minimum(graph.radius[tree], cap)
            graph.sphere[tree] = np.minimum(graph.sphere[tree], cap)
            graph.flags[land] |= FLAG_THICK_LANDING


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


def build_avoidance(obstacles, tips, params) -> Avoidance | None:
    """The avoidance grid for these tips, when the obstacles can report column occupancy
    and their bounds (else None: routing falls back to straight escape paths)."""
    bounds = getattr(obstacles, "bounds", None)
    if bounds is None or not hasattr(obstacles, "column_occupancy") or not tips.ok.any():
        return None
    lo = np.minimum(bounds[0][:2], tips.knuckle[tips.ok, :2].min(axis=0))
    hi = np.maximum(bounds[1][:2], tips.knuckle[tips.ok, :2].max(axis=0))
    top = float(tips.knuckle[tips.ok, 2].max())
    return Avoidance(obstacles, lo, hi, top, params)


def _desired_xy(awake, heads, params, obstacles, tan, new_level, av=None) -> list[np.ndarray]:
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
        if av is not None and h.blocked:
            # With the model below, blocked means no way to the bed at all (the grid's
            # say); with nothing below, the head is free whatever the (conservative) grid
            # says next to the model.
            h.blocked = not bool(av.can_reach_bed(h.pos, h.radius)[0])

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
    # Mutual pairs first, closest first: both heads of a pair aim for the same midpoint, so
    # they meet exactly and merge. (Nearest-partner choices alone are not mutual; in a
    # bundle each chases another and they run down side by side, never meeting.)
    mate = _pair_up(awake, awake_idx, heads, d_xy)
    # Trapped heads (no way to the bed) look for each other: they merge into fewer trees
    # before standing on the model.
    trapped = ~free
    d_trap = np.linalg.norm(a_pos[:, None, :2] - all_pos[None, :, :2], axis=2)
    cand_trap = below_or_level & ~same & trapped[None, :] & (d_trap <= params.merge_distance)
    d_trap = np.where(cand_trap, d_trap, np.inf)
    trap_partner = np.argmin(d_trap, axis=1)
    has_trap_partner = np.isfinite(d_trap[np.arange(len(awake)), trap_partner])

    base = _base_height(params)
    out = []
    for i, h in enumerate(awake):
        if h.blocked:
            # The grid is a little conservative: a straight escape is still worth a look. A
            # free branch to join cannot exist when the grid says no way to the bed.
            direction = _escape(h, obstacles, tan)
            if h.escape is not None:
                h.landing = h.host = None
            elif av is None and (host := _host(h, heads, params, obstacles, tan)) is not None:
                # No straight way to the bed, but a free branch it can reach: join that tree
                # (Bambu/Orca trees funnel trapped branches into trunks rather than landing).
                h.landing = None
                out.append(host.pos[:2].copy())
                continue
            elif has_trap_partner[i] and h.pos[2] - new_level > 0:
                # Another trapped head nearby: meet it, and land together later.
                other = heads[trap_partner[i]]
                h.landing = None  # found again from wherever the merged head ends up
                out.append((h.pos[:2] + other.pos[:2]) / 2 if other.awake else other.pos[:2].copy())
                continue
            elif params.land_on_model:
                # No way to the bed: head for a spot on the model to land on.
                if h.landing is None:
                    h.landing = _find_landing(h.pos, obstacles, tan, np.inf)
                if h.landing is not None:
                    out.append(h.landing[:2].copy())
                    continue
            dz = h.pos[2] - new_level
            out.append(h.pos[:2] + direction[:2] / max(-direction[2], 1e-6) * dz)
        elif new_level < base:
            h.escape = None  # the vertical trunk base: straight down
            out.append(h.pos[:2].copy())
        elif mate[i] >= 0:
            h.escape = None
            out.append((h.pos[:2] + heads[mate[i]].pos[:2]) / 2)  # it aims here too
        elif has_partner[i]:
            h.escape = None
            other = heads[partner[i]]
            if other.awake:  # it has a mate of its own: walk toward it, it may wait for us
                out.append(other.pos[:2].copy())
            else:
                out.append(other.pos[:2].copy())
        else:
            h.escape = None
            out.append(h.pos[:2].copy())
    if av is not None:
        # Free heads go where they wish only as far as the bed stays reachable from there.
        for i, h in enumerate(awake):
            h.steered = False
            # Steer only heads the grid can speak for (it is conservative next to the model).
            # Mated heads are not steered apart: the merged head steers on the next level.
            mated = mate[i] >= 0 and not h.blocked
            if not h.blocked and not mated and new_level >= base and av.can_reach_bed(h.pos, h.radius)[0]:
                dz = h.pos[2] - new_level
                steered = av.steer(h.pos, new_level, out[i], dz * tan, h.radius)
                if steered is not None:
                    h.steered = bool(np.linalg.norm(steered - out[i]) > 1e-6)
                    out[i] = steered
    return out


def _pair_up(awake, awake_idx: np.ndarray, heads, d_xy: np.ndarray) -> np.ndarray:
    """Mutual pairing of awake heads (d_xy: awake x all heads, inf where not a candidate).
    Pairs from earlier levels are kept while still valid, so they keep walking toward each
    other until they meet; the rest pair greedily, closest first. Returns, per awake head,
    its mate's index in `heads`, or -1 (and records it in head.mate)."""
    n = len(awake_idx)
    mate = np.full(n, -1)
    slot = {id(h): i for i, h in enumerate(awake)}
    sub = d_xy[:, awake_idx]  # awake x awake
    for i, h in enumerate(awake):
        j = slot.get(id(h.mate)) if h.mate is not None else None
        if j is not None and mate[i] < 0 and mate[j] < 0 and awake[j].mate is h and np.isfinite(sub[i, j]):
            mate[i], mate[j] = awake_idx[j], awake_idx[i]
    iu = np.argwhere(np.isfinite(sub))
    if len(iu):
        order = np.argsort(sub[iu[:, 0], iu[:, 1]], kind="stable")
        for a, b in iu[order]:
            if a == b or mate[a] >= 0 or mate[b] >= 0:
                continue
            mate[a], mate[b] = awake_idx[b], awake_idx[a]
    for i, h in enumerate(awake):
        h.mate = heads[mate[i]] if mate[i] >= 0 else None
    return mate


HOST_CANDIDATES = 8  # nearest free heads checked for a clear way to them
HOST_SAMPLES = 6  # clearance samples along the way


def _host(h, heads, params, obstacles, tan):
    """A free head below `h` that `h` can reach inside its cone with a clear line, nearest
    first; keeps the current host while it is still free and in reach."""
    base = _base_height(params)
    if (
        h.host is not None
        and h.host in heads
        and not h.host.blocked
        and _can_reach(h, h.host, base, obstacles, tan)
    ):
        return h.host
    h.host = None
    free = [o for o in heads if o is not h and not o.blocked and o.pos[2] <= h.pos[2] + 1e-9]
    if not free:
        return None
    pos = np.array([o.pos for o in free])
    d = np.linalg.norm(pos[:, :2] - h.pos[:2], axis=1)
    reach = (h.pos[2] - base) * tan
    for k in np.argsort(d)[:HOST_CANDIDATES]:
        if d[k] > reach:
            break
        if _can_reach(h, free[k], base, obstacles, tan):
            h.host = free[k]
            return h.host
    return None


def _can_reach(h, other, base, obstacles, tan) -> bool:
    """Is there a straight branch, inside the cone, from `h` to the column above `other`
    that keeps the hard clearance?"""
    d = float(np.linalg.norm(other.pos[:2] - h.pos[:2]))
    meet_z = h.pos[2] - d / max(tan, 1e-9)
    if meet_z < max(other.pos[2], base) - 1e-9:
        return False
    end = np.array([other.pos[0], other.pos[1], meet_z])
    t = np.linspace(0.0, 1.0, HOST_SAMPLES + 1)[1:]
    samples = h.pos + (end - h.pos) * t[:, None]
    return bool(np.all(obstacles.nearest_distance(samples) >= h.radius + HARD_CLEARANCE))


def _base_height(params: Params) -> float:
    return max(params.foot_height, 0.0) + TRUNK_BASE_MM


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


def _radius_at(params: Params, below_top: float, tips: int) -> float:
    """Branch radius at `below_top` mm under the highest of the `tips` it carries."""
    return radius_at(params, below_top, tips)


def _move(h, target_xy, new_level, params, obstacles, tan, tan_pref, av=None) -> np.ndarray | None:
    """This level's position for a head. Ordinary moves (straight down, toward a merge
    partner) lean at most the preferred angle; escaping, landing and dodging the model
    may use the full branch angle (PrusaSlicer/Orca's slow and fast moves)."""
    dz = h.pos[2] - new_level
    reach = dz * tan
    # Redirected moves may use the full branch angle: the reachable path can need it.
    steering = h.blocked or h.landing is not None or h.steered
    want = target_xy - h.pos[:2]
    dist = np.linalg.norm(want)
    wish = reach if steering else dz * tan_pref
    step_xy = want if dist <= wish else want / dist * wish
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
    if av is not None and not h.blocked and av.can_reach_bed(h.pos, h.radius)[0]:
        # Among clear moves, keep the bed reachable if any of them does.
        stays = av.can_reach_bed(cands, h.radius)
        if (ok & stays).any():
            ok &= stays
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


def _merge_and_extend(b: GraphBuilder, awake, heads, params, obstacles, av=None) -> None:
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
        """The area-weighted mean of the members, moved to the nearest point every member
        can reach from its previous node inside its cone (alternating projections onto
        those discs); within_cone then says whether such a point exists."""
        w = rad[idx] ** 2
        p = (pos[idx] * w[:, None]).sum(axis=0) / w.sum()
        xy = p[:2].copy()
        for _ in range(JUNCTION_PROJECTIONS):
            moved = False
            for k in idx:
                v = xy - prev[k]
                n = float(np.linalg.norm(v))
                if n > reach[k]:
                    xy = prev[k] + v / n * reach[k]
                    moved = True
            if not moved:
                break
        return np.array([xy[0], xy[1], p[2]])

    def within_cone(idx, p):
        # Every member must reach the junction from its previous node inside its cone.
        return bool(np.all(np.linalg.norm(prev[idx] - p[:2], axis=1) <= reach[idx] + 1e-6))

    def merged_radius(idx):
        # What the merged branch would carry, but never thinner than its thickest member.
        top = max(awake[i].top for i in idx)
        n = sum(awake[i].tips for i in idx)
        r = max(float(rad[idx].max()), _radius_at(params, top - float(pos[idx, 2].mean()), n))
        return min(params.max_radius, r * params.merge_growth)

    def fitted_radius(idx, p):
        """The merged radius, thinned to the room at the junction (like a single head's
        growth) but never below its thickest member; None if even that does not fit or
        the members could reach the bed and the merged branch could not."""
        thickest = float(rad[idx].max())
        room = float(obstacles.nearest_distance(p[None])[0]) - HARD_CLEARANCE
        if room < thickest:
            return None
        r = max(thickest, min(merged_radius(idx), room))
        # Keep the bed reachable (by the grid) if the thickest member has it there.
        if av is not None and not blocked[idx].all() and av.can_reach_bed(p, thickest)[0]:
            while not av.can_reach_bed(p, r)[0]:
                if r <= thickest + 1e-9:
                    return None
                r = max(thickest, r - 0.25)
        return r

    def has_room(idx, p):
        return fitted_radius(idx, p) is not None

    blocked = np.array([h.blocked for h in awake])
    # A blocked head may merge into the free head it is steering to.
    hosted = np.zeros((len(awake), len(awake)), bool)
    index = {id(h): i for i, h in enumerate(awake)}
    for i, h in enumerate(awake):
        if h.host is not None and id(h.host) in index:
            j = index[id(h.host)]
            hosted[i, j] = hosted[j, i] = True
    # No merging inside the vertical trunk base: every trunk stands straight on its own
    # foot (a junction there would put its ball, and a leaning branch, into the bed).
    in_base = pos[:, 2] < _base_height(params) - 1e-9
    blocked = blocked | in_base
    trapped = np.array([h.blocked for h in awake])
    both_trapped = trapped[:, None] & trapped[None, :]
    free_pair = (~blocked[:, None] & ~blocked[None, :]) | (
        (hosted | both_trapped) & ~in_base[:, None] & ~in_base[None, :]
    )
    pairs = np.argwhere(np.triu((d <= limit) & free_pair, k=1))
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
        r = fitted_radius(np.array(idx), p)
        r = float(rad[idx].max()) if r is None else r
        node = b.add(p, JUNCTION, r)
        for i in idx:
            b.set_parent(awake[i].node, node)
        keep = awake[idx[0]]
        keep.node, keep.pos, keep.radius = node, p, r
        keep.top = max(awake[i].top for i in idx)
        keep.tips = sum(awake[i].tips for i in idx)
        dead.update(id(awake[i]) for i in idx[1:])
    heads[:] = [h for h in heads if id(h) not in dead]


def _decimate(graph: SupportGraph) -> SupportGraph:
    """Collapse runs of single-child branch nodes into fewer, longer segments.

    A run is walked top-down; a segment is extended over the next node while every node
    it skips stays within DECIMATE_TOLERANCE of it, counting both the position and how
    much fatter the segment's (linearly interpolated) radius is than the node's own. So
    the tubes never get closer to the model than the nodes routing checked.
    """
    n = len(graph)
    if n == 0:
        return graph
    children = graph.children_count()
    child_of = np.full(n, -1)
    has_parent = graph.parent >= 0
    child_of[graph.parent[has_parent]] = np.flatnonzero(has_parent)  # valid where children == 1
    parent = graph.parent.copy()
    p_kind = graph.kind[np.maximum(parent, 0)]
    # The node above a foot or landing sets where the flare / tip starts: never removed.
    removable = (graph.kind == BRANCH) & (children == 1) & has_parent & ~np.isin(p_kind, ROOT_KINDS)
    removed = np.zeros(n, bool)
    starts = np.flatnonzero(removable & ~removable[np.maximum(child_of, 0)])
    for s in starts:
        run = [int(s)]
        while removable[parent[run[-1]]]:
            run.append(int(parent[run[-1]]))
        chain = [int(child_of[s]), *run, int(parent[run[-1]])]
        kept = [chain[0]]
        anchor = 0
        for j in range(2, len(chain)):
            if not _straight_enough(graph, chain, anchor, j):
                anchor = j - 1
                kept.append(chain[anchor])
        kept.append(chain[-1])
        keep_set = set(kept)
        for node in run:
            removed[node] = node not in keep_set
        for upper, lower in zip(kept[:-1], kept[1:], strict=True):
            parent[upper] = lower
    graph.parent = parent
    return graph.subset(~removed)


def _straight_enough(graph: SupportGraph, chain: list[int], i: int, j: int) -> bool:
    """Can chain[i] -> chain[j] replace the nodes between them (see _decimate)."""
    a, b = graph.pos[chain[i]], graph.pos[chain[j]]
    ra, rb = graph.radius[chain[i]], graph.radius[chain[j]]
    ab = b - a
    length2 = float(ab @ ab)
    if length2 < 1e-18:
        return True
    mid = np.array(chain[i + 1 : j])
    t = np.clip((graph.pos[mid] - a) @ ab / length2, 0.0, 1.0)
    off = np.linalg.norm(a + t[:, None] * ab - graph.pos[mid], axis=1)
    fatter = np.maximum(ra + (rb - ra) * t - graph.radius[mid], 0.0)
    return bool(np.all(off + fatter <= DECIMATE_TOLERANCE))
