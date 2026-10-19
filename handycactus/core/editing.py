# SPDX-License-Identifier: GPL-3.0-or-later
"""Manual edits of a support graph (spec sections 6.5 and 7).

Every edit returns a new graph. A tree touched by an edit is flagged manual
and locked, so Regenerate keeps it (spec 7).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .detection import SAMPLE, Contacts
from .graph import BRANCH, CONTACT, FLAG_MANUAL, FLAG_OVERRIDE, JUNCTION, KNUCKLE, ROOT_KINDS, SupportGraph
from .params import Params
from .routing import RoutingResult, route
from .spatial import Obstacles
from .tips import build_tips

MOVABLE_KINDS = (BRANCH, JUNCTION)
TIP_OVERRIDES = ("contact_diameter", "tip_length", "penetration", "knuckle_diameter", "max_tip_angle")


@dataclass
class EditResult:
    graph: SupportGraph
    ok: bool
    message: str
    tip: int = -1  # the tip added or rebuilt, if any


def concat(a: SupportGraph, b: SupportGraph) -> SupportGraph:
    """b's nodes appended after a's (b's parents offset; tip ids kept as given)."""
    n = len(a)
    return SupportGraph(
        pos=np.concatenate([a.pos, b.pos]),
        kind=np.concatenate([a.kind, b.kind]),
        radius=np.concatenate([a.radius, b.radius]),
        parent=np.concatenate([a.parent, np.where(b.parent >= 0, b.parent + n, -1)]),
        tree=np.concatenate([a.tree, b.tree]),
        tip=np.concatenate([a.tip, b.tip]),
        target=np.concatenate([a.target, b.target]),
        dir=np.concatenate([a.dir, b.dir]),
        sphere=np.concatenate([a.sphere, b.sphere]),
        locked=np.concatenate([a.locked, b.locked]),
        flags=np.concatenate([a.flags, b.flags]),
    )


def next_tip_id(graph: SupportGraph) -> int:
    return int(graph.tip.max()) + 1 if len(graph) and (graph.tip >= 0).any() else 0


def merge_routed(
    existing: SupportGraph, routed: RoutingResult, params: Params
) -> tuple[SupportGraph, np.ndarray]:
    """Add newly routed trees to an existing graph, joining attachments and thickening the
    branches below each join. Returns the graph and a mask of the new nodes."""
    new = routed.graph
    offset = next_tip_id(existing)
    new.tip = np.where(new.tip >= 0, new.tip + offset, -1)
    merged = concat(existing, new)
    n0 = len(existing)
    for local, target in routed.attachments:
        merged.parent[n0 + local] = target
        _thicken_below(merged, target, float(new.radius[local]), params)
    merged.renumber_trees()
    is_new = np.zeros(len(merged), bool)
    is_new[n0:] = True
    return merged, is_new


def _thicken_below(graph: SupportGraph, node: int, r_add: float, params: Params) -> None:
    """Area-conserving radius growth from `node` down to its root (feet keep their flare)."""
    while node >= 0 and graph.kind[node] not in ROOT_KINDS:
        r = graph.radius[node]
        graph.radius[node] = min(params.max_radius, max(r, float(np.sqrt(r * r + r_add * r_add))))
        if graph.kind[node] == JUNCTION:
            graph.sphere[node] = graph.radius[node]
        node = int(graph.parent[node])


def lock_trees(graph: SupportGraph, nodes: np.ndarray, manual: bool = True) -> None:
    """Lock (and flag manual) every tree containing any of `nodes`."""
    if len(nodes) == 0:
        return
    roots = graph.roots()
    trees = np.isin(roots, roots[np.asarray(nodes)])
    graph.locked[trees] = True
    if manual:
        graph.flags[trees] |= FLAG_MANUAL


def set_tree_locked(graph: SupportGraph, node: int, locked: bool) -> SupportGraph:
    g = graph.subset(np.ones(len(graph), bool))  # copy
    roots = g.roots()
    g.locked[roots == roots[node]] = locked
    return g


def add_tip(
    graph: SupportGraph,
    point,
    normal,
    target: int,
    params: Params,
    obstacles: Obstacles,
    overrides: dict | None = None,
) -> EditResult:
    """A new tip at a surface point, routed incrementally (it may join an unlocked tree)."""
    tip_params = params.with_values(overrides) if overrides else params
    contacts = Contacts(
        positions=np.asarray(point, float).reshape(1, 3),
        normals=np.asarray(normal, float).reshape(1, 3) / max(np.linalg.norm(normal), 1e-12),
        target=np.array([target]),
        kind=np.array([SAMPLE]),
    )
    tips = build_tips(contacts, tip_params, obstacles)
    if not tips.ok[0]:
        return EditResult(graph, False, "No room for a tip here")
    routed = route(tips, tip_params, obstacles, attach_to=graph)
    if len(routed.unreachable_tips):
        return EditResult(graph, False, "This tip cannot reach the bed or land on the model")
    merged, is_new = merge_routed(graph, routed, tip_params)
    new_nodes = np.flatnonzero(is_new)
    merged.flags[new_nodes] |= FLAG_MANUAL | (FLAG_OVERRIDE if overrides else 0)
    lock_trees(merged, new_nodes)
    tip = int(merged.tip[new_nodes][merged.kind[new_nodes] == CONTACT][0])
    return EditResult(merged, True, "Tip added", tip)


def delete_tip(graph: SupportGraph, tip: int) -> EditResult:
    """Remove a tip and prune its branch back to the first node that still carries others."""
    mine = graph.tip == tip
    if not mine.any():
        return EditResult(graph, False, f"No tip {tip}")
    knuckle = np.flatnonzero(mine & (graph.kind == KNUCKLE))
    below = int(graph.parent[knuckle[0]]) if len(knuckle) else -1
    keep = ~mine
    # Walk down: drop nodes left with no children (dangling branch ends, empty feet).
    children = np.zeros(len(graph), np.int64)
    child_parents = graph.parent[keep & (graph.parent >= 0)]
    np.add.at(children, child_parents, 1)
    node = below
    survivor = -1
    while node >= 0:
        if children[node] > 0:
            survivor = node
            break
        keep[node] = False
        parent = int(graph.parent[node])
        if parent >= 0:
            children[parent] -= 1
        node = parent
    g = graph.subset(keep)
    g.renumber_trees()
    if survivor >= 0:
        lock_trees(g, np.array([int(np.cumsum(keep)[survivor] - 1)]))
    return EditResult(g, True, "Tip deleted")


def move_node(graph: SupportGraph, node: int, position) -> EditResult:
    """Move a branch or junction node (tips move with move_tip)."""
    if graph.kind[node] not in MOVABLE_KINDS:
        return EditResult(
            graph, False, "Only branch and junction nodes can be moved; move tips along the model"
        )
    g = graph.subset(np.ones(len(graph), bool))  # copy
    g.pos[node] = np.asarray(position, float)
    if g.pos[node, 2] <= 0:
        return EditResult(graph, False, "A branch node must stay above the bed")
    lock_trees(g, np.array([node]))
    return EditResult(g, True, "Node moved")


def move_tip(
    graph: SupportGraph, tip: int, point, normal, target: int, params: Params, obstacles: Obstacles
) -> EditResult:
    """Move a tip to another surface point: delete it and add it there (re-routed)."""
    removed = delete_tip(graph, tip)
    if not removed.ok:
        return removed
    added = add_tip(removed.graph, point, normal, target, params, obstacles)
    if not added.ok:
        return EditResult(graph, False, added.message)
    return EditResult(added.graph, True, "Tip moved", added.tip)


def tip_surface(graph: SupportGraph, tip: int, params: Params) -> tuple[np.ndarray, np.ndarray, int] | None:
    """(surface point, tip axis, target) of a tip: the contact node sits `penetration` in."""
    contact = np.flatnonzero((graph.tip == tip) & (graph.kind == CONTACT))
    if len(contact) == 0:
        return None
    c = contact[0]
    return graph.pos[c] + graph.dir[c] * params.penetration, graph.dir[c].copy(), int(graph.target[c])


def rebuild_tip(
    graph: SupportGraph, tip: int, overrides: dict, params: Params, obstacles: Obstacles
) -> EditResult:
    """Rebuild one tip with per-tip overrides (contact diameter, tip length, ...)."""
    unknown = set(overrides) - set(TIP_OVERRIDES)
    if unknown:
        return EditResult(graph, False, f"Not a tip setting: {sorted(unknown)}")
    where = tip_surface(graph, tip, params)
    if where is None:
        return EditResult(graph, False, f"No tip {tip}")
    point, axis, target = where
    removed = delete_tip(graph, tip)
    added = add_tip(removed.graph, point, axis, target, params, obstacles, overrides)
    if not added.ok:
        return EditResult(graph, False, added.message)
    return EditResult(added.graph, True, "Tip rebuilt", added.tip)


def locked_part(graph: SupportGraph) -> SupportGraph:
    """The locked trees of a graph (what Regenerate keeps)."""
    return graph.subset(graph.locked)


def drop_contacts_near(contacts: Contacts, points: np.ndarray, distance: float) -> Contacts:
    """Contacts farther than `distance` from every point (new contacts near kept tips go)."""
    if len(points) == 0 or len(contacts) == 0:
        return contacts
    d = np.linalg.norm(contacts.positions[:, None, :] - points[None, :, :], axis=2).min(axis=1)
    keep = d > distance
    return Contacts(
        contacts.positions[keep], contacts.normals[keep], contacts.target[keep], contacts.kind[keep]
    )
