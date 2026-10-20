# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoothing of routed branches (spec section 6.4.2).

After the level sweep, branch paths zig-zag where heads chased merge partners
or dodged the model. This is the Laplacian smoothing pass of PrusaSlicer's
organic supports (`organic_smooth_branches_avoid_collisions`, also used by
OrcaSlicer), adapted to the support graph:

- Each movable node moves halfway toward the weighted average of its
  neighbours, in XY only (nodes stay on their levels). Children are weighted
  by their radius; the parent gets as much weight as all children together,
  so a thin side branch does not drag a trunk.
- A step is at most SMOOTH_MAX_STEP per iteration.
- A move is kept only if the node keeps its clearance to the model (see
  `_room_ok`) and no segment at it leans past the branch angle (or past its
  current lean, if that is already steeper).
- Tips, knuckles, landing tips and the node above a landing stay put. Feet
  follow the node above them, so trunks stay vertical at the bed.
"""

from __future__ import annotations

import numpy as np

from .graph import CONTACT, FOOT, KNUCKLE, LANDING, SupportGraph
from .params import Params
from .spatial import Obstacles

SMOOTH_ITERATIONS = 60
SMOOTH_FACTOR = 0.5
SMOOTH_MAX_STEP = 0.2  # mm per iteration
SMOOTH_MIN_MOVE = 1e-3  # mm: nodes moving less than this are done
ANGLE_SLACK = 1e-6


def smooth(
    graph: SupportGraph, params: Params, obstacles: Obstacles, fixed: np.ndarray, hard_clearance: float
) -> SupportGraph:
    """Smooth branch node positions in place (and return the graph).

    `fixed` marks extra nodes that must not move (e.g. roots attached to an
    existing graph). Branch radii are left as they are.
    """
    n = len(graph)
    if n == 0:
        return graph
    parent = graph.parent
    kind = graph.kind
    has_parent = parent >= 0
    child = np.flatnonzero(has_parent)
    par = parent[child]

    # Feet ride on the trunk's bottom node (the foot is its parent); it moves for both.
    foot_of = np.full(n, -1)
    on_foot = kind[par] == FOOT
    foot_of[child[on_foot]] = par[on_foot]

    movable = ~np.isin(kind, (CONTACT, KNUCKLE, LANDING, FOOT)) & ~fixed & has_parent
    # The node above a landing tip is its break point: keep the landing tip's axis.
    movable[child[kind[par] == LANDING]] = False
    # Nothing to average toward without children.
    n_children = np.bincount(par, minlength=n)
    movable &= n_children > 0
    idx = np.flatnonzero(movable)
    if len(idx) == 0:
        return graph

    tan_max = np.tan(np.radians(params.max_branch_angle))
    w_child = np.maximum(graph.radius[child], 1e-6)
    radius = graph.radius
    pos = graph.pos
    need = radius + params.model_clearance
    hard = radius + hard_clearance
    room = np.full(n, np.inf)  # lower bounds of each node's distance to the model
    with_foot = idx[foot_of[idx] >= 0]
    room[idx] = obstacles.nearest_distance(pos[idx])
    if len(with_foot):
        room[foot_of[with_foot]] = obstacles.nearest_distance(pos[foot_of[with_foot]])
    active = np.ones(len(idx), bool)

    for _ in range(SMOOTH_ITERATIONS):
        if not active.any():
            break
        # Weighted neighbour average (XY): children by radius, parent by their total.
        sum_xy = np.zeros((n, 2))
        np.add.at(sum_xy, par, pos[child, :2] * w_child[:, None])
        w_sum = np.bincount(par, weights=w_child, minlength=n)
        p_xy = pos[np.maximum(parent, 0), :2]
        # A foot is not a neighbour to average toward; use the node's own XY instead.
        p_is_foot = has_parent & (kind[np.maximum(parent, 0)] == FOOT)
        p_xy = np.where(p_is_foot[:, None], pos[:, :2], p_xy)
        avg = (sum_xy + p_xy * w_sum[:, None]) / np.maximum(2 * w_sum, 1e-12)[:, None]

        a = idx[active]
        step = SMOOTH_FACTOR * (avg[a] - pos[a, :2])
        length = np.linalg.norm(step, axis=1)
        step *= np.minimum(1.0, SMOOTH_MAX_STEP / np.maximum(length, 1e-12))[:, None]
        length = np.minimum(length, SMOOTH_MAX_STEP)
        moving = length >= SMOOTH_MIN_MOVE
        active[np.flatnonzero(active)[~moving]] = False
        a, step, length = a[moving], step[moving], length[moving]
        if len(a) == 0:
            break
        new = pos[a].copy()
        new[:, :2] += step

        ok = _angles_ok(graph, a, new, tan_max)
        ok_room, d_new = _room_ok(a, new, length, room, need[a], hard[a], obstacles)
        ok &= ok_room
        f = foot_of[a]
        fa = np.flatnonzero((f >= 0) & ok)
        d_foot = np.zeros(0)
        if len(fa):
            fnew = pos[f[fa]].copy()
            fnew[:, :2] += step[fa]
            fok, d_foot = _room_ok(f[fa], fnew, length[fa], room, need[a[fa]], hard[a[fa]], obstacles)
            ok[fa[~fok]] = False
            fa, d_foot = fa[fok], d_foot[fok]
        pos[a[ok], :2] += step[ok]
        room[a[ok]] = d_new[ok]
        pos[f[fa], :2] += step[fa]
        room[f[fa]] = d_foot
    return graph


def _angles_ok(graph: SupportGraph, nodes: np.ndarray, new: np.ndarray, tan_max: float) -> np.ndarray:
    """No segment at a moved node leans past the branch angle, or past its old lean.

    Neighbours are taken at their current positions (moves within one
    iteration are small and checked again in the next)."""
    pos = graph.pos
    ok = np.ones(len(nodes), bool)
    parent = graph.parent
    kind = graph.kind
    # Segment to the parent (unless it is a foot, which moves along).
    p = parent[nodes]
    check = (p >= 0) & (kind[np.maximum(p, 0)] != FOOT)
    if check.any():
        ok[check] &= _lean_ok(new[check], pos[p[check]], pos[nodes[check]], pos[p[check]], tan_max)
    # Segments to children.
    is_moved = np.full(len(graph), -1)
    is_moved[nodes] = np.arange(len(nodes))
    child = np.flatnonzero((parent >= 0) & (is_moved[np.maximum(parent, 0)] >= 0))
    if len(child):
        k = is_moved[parent[child]]
        good = _lean_ok(pos[child], new[k], pos[child], pos[nodes[k]], tan_max)
        bad = np.zeros(len(nodes), bool)
        np.logical_or.at(bad, k, ~good)
        ok &= ~bad
    return ok


def _lean_ok(top_new, bottom_new, top_old, bottom_old, tan_max) -> np.ndarray:
    """Lean (horizontal / vertical) of the new segment within max(tan_max, old lean)."""

    def lean(top, bottom):
        dz = np.maximum(top[:, 2] - bottom[:, 2], 1e-9)
        return np.linalg.norm(top[:, :2] - bottom[:, :2], axis=1) / dz

    return lean(top_new, bottom_new) <= np.maximum(tan_max, lean(top_old, bottom_old)) + ANGLE_SLACK


def _room_ok(nodes, new, step_len, room, need, hard, obstacles) -> tuple[np.ndarray, np.ndarray]:
    """Can `nodes` move to `new`: keep the hard clearance, and keep the full clearance or
    lose none. Returns (ok, distance lower bounds at the new positions).

    Distance to the model changes by at most the step length, so only nodes
    that could fall below their full clearance are queried."""
    d_new = room[nodes] - step_len  # a lower bound
    sure = d_new >= need
    ok = sure.copy()
    query = np.flatnonzero(~sure)
    if len(query):
        d = obstacles.nearest_distance(new[query])
        ok[query] = (d >= hard[query]) & ((d >= need[query]) | (d >= room[nodes[query]] - 1e-9))
        d_new[query] = d
    return ok, d_new
