# SPDX-License-Identifier: GPL-3.0-or-later
"""The support graph (spec section 5.2): a forest of nodes, each pointing at
its parent toward the bed. Positions and radii are millimetres."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Node kinds (hc_kind).
CONTACT = 0
KNUCKLE = 1
BRANCH = 2
JUNCTION = 3
FOOT = 4
LANDING = 5  # a branch ending on the model instead of the bed (a reversed tip)
ROOT_KINDS = (FOOT, LANDING)

# hc_flags bits.
FLAG_MANUAL = 1
FLAG_OVERRIDE = 2
FLAG_CHECK_FAILED = 4
FLAG_UNREACHABLE = 8

FOOT_Z_TOLERANCE = 1e-3  # mm


@dataclass
class SupportGraph:
    pos: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    kind: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    radius: np.ndarray = field(default_factory=lambda: np.zeros(0))
    parent: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    tree: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    tip: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    target: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    dir: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    sphere: np.ndarray = field(default_factory=lambda: np.zeros(0))  # ball radius, 0 = none
    locked: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))
    flags: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))

    def __len__(self) -> int:
        return len(self.pos)

    @property
    def edges(self) -> np.ndarray:
        """(E, 2) node -> parent."""
        child = np.flatnonzero(self.parent >= 0)
        return np.stack([child, self.parent[child]], axis=1)

    def children_count(self) -> np.ndarray:
        counts = np.zeros(len(self), np.int64)
        np.add.at(counts, self.parent[self.parent >= 0], 1)
        return counts

    def roots(self, nodes: np.ndarray | None = None) -> np.ndarray:
        """Root (foot) of every node, by pointer jumping."""
        up = np.where(self.parent >= 0, self.parent, np.arange(len(self)))
        while True:
            nxt = up[up]
            if np.array_equal(nxt, up):
                break
            up = nxt
        return up if nodes is None else up[nodes]

    def problems(self) -> list[str]:
        """Forest invariant (spec 5.2). Empty list = valid."""
        out = []
        n = len(self)
        arrays = (self.kind, self.radius, self.parent, self.tree, self.tip, self.target, self.sphere)
        if any(len(a) != n for a in arrays) or self.dir.shape != (n, 3) or len(self.locked) != n:
            return ["attribute arrays have different lengths"]
        if n == 0:
            return out
        if np.any((self.parent < -1) | (self.parent >= n)):
            return ["parent index out of range"]
        if np.any(self.parent == np.arange(n)):
            return ["node is its own parent"]
        # Cycle check: walking up n steps from any node must reach a root.
        up = self.parent.copy()
        cur = np.arange(n)
        for _ in range(int(np.ceil(np.log2(n + 1))) + 1):
            alive = cur >= 0
            cur[alive] = up[cur[alive]]
            up = np.where(up >= 0, up[np.maximum(up, 0)], -1)
        if np.any(cur >= 0):
            out.append("cycle in parent pointers")
            return out
        root_nodes = np.flatnonzero(self.parent < 0)
        not_root_kind = ~np.isin(self.kind[root_nodes], ROOT_KINDS)
        if np.any(not_root_kind):
            out.append(f"{np.count_nonzero(not_root_kind)} root(s) are not feet or landings")
        feet = root_nodes[self.kind[root_nodes] == FOOT]
        if np.any(np.abs(self.pos[feet, 2]) > FOOT_Z_TOLERANCE):
            out.append("a foot is not on the bed")
        if np.any(np.isin(self.kind, ROOT_KINDS) & (self.parent >= 0)):
            out.append("a foot or landing has a parent")
        contacts = np.flatnonzero(self.kind == CONTACT)
        if np.any(self.children_count()[contacts] > 0):
            out.append("a contact has children")
        if len(contacts) and np.any(self.kind[self.parent[contacts]] != KNUCKLE):
            out.append("a contact's parent is not a knuckle")
        if np.any(self.radius <= 0):
            out.append("non-positive radius")
        return out

    def subset(self, keep: np.ndarray) -> SupportGraph:
        """Nodes where keep is True; parents remapped (a dropped parent becomes -1)."""
        keep = np.asarray(keep, bool)
        remap = np.full(len(self), -1, np.int64)
        remap[keep] = np.arange(np.count_nonzero(keep))
        parent = self.parent[keep]
        parent = np.where(parent >= 0, remap[np.maximum(parent, 0)], -1)
        return SupportGraph(
            pos=self.pos[keep],
            kind=self.kind[keep],
            radius=self.radius[keep],
            parent=parent,
            tree=self.tree[keep],
            tip=self.tip[keep],
            target=self.target[keep],
            dir=self.dir[keep],
            sphere=self.sphere[keep],
            locked=self.locked[keep],
            flags=self.flags[keep],
        )

    def renumber_trees(self) -> None:
        """Tree id = index of the tree's foot among all feet."""
        roots = self.roots()
        feet, ids = np.unique(roots, return_inverse=True)
        self.tree = ids.astype(np.int64)


class GraphBuilder:
    """Append-only construction with Python lists, then `build()` to arrays."""

    def __init__(self):
        self._rows: list[tuple] = []

    def add(
        self,
        pos,
        kind: int,
        radius: float,
        parent: int = -1,
        tip: int = -1,
        target: int = -1,
        direction=(0.0, 0.0, 0.0),
        sphere: float = 0.0,
    ) -> int:
        self._rows.append((tuple(pos), kind, radius, parent, tip, target, tuple(direction), sphere))
        return len(self._rows) - 1

    def set_parent(self, node: int, parent: int) -> None:
        row = list(self._rows[node])
        row[3] = parent
        self._rows[node] = tuple(row)

    def set_sphere(self, node: int, radius: float) -> None:
        row = list(self._rows[node])
        row[7] = radius
        self._rows[node] = tuple(row)

    def set_kind(self, node: int, kind: int) -> None:
        row = list(self._rows[node])
        row[1] = kind
        self._rows[node] = tuple(row)

    def build(self) -> SupportGraph:
        if not self._rows:
            return SupportGraph()
        pos, kind, radius, parent, tip, target, direction, sphere = zip(*self._rows, strict=True)
        n = len(self._rows)
        g = SupportGraph(
            pos=np.array(pos, dtype=np.float64),
            kind=np.array(kind, np.int64),
            radius=np.array(radius, dtype=np.float64),
            parent=np.array(parent, np.int64),
            tree=np.zeros(n, np.int64),
            tip=np.array(tip, np.int64),
            target=np.array(target, np.int64),
            dir=np.array(direction, dtype=np.float64),
            sphere=np.array(sphere, dtype=np.float64),
            locked=np.zeros(n, bool),
            flags=np.zeros(n, np.int64),
        )
        g.renumber_trees()
        return g
