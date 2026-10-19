# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from handycactus.core import detection as det
from handycactus.core import editing
from handycactus.core import graph as G
from handycactus.core.params import Params

from .shapes import voxels
from .test_routing import generate


@pytest.fixture(scope="module")
def base():
    # A 6 x 6 mm block 10 mm up, plus a separate block far to the side for new trees.
    mesh, tips, r, obstacles = generate(voxels([((0, 0, 10), (6, 6, 12)), ((20, 0, 8), (24, 4, 9))]))
    return r.graph, obstacles


def leaves_are_contacts(g):
    has_child = np.zeros(len(g), bool)
    has_child[g.parent[g.parent >= 0]] = True
    return np.all(g.kind[~has_child] == G.CONTACT)


def test_add_tip_locks_only_its_own_tree(base):
    g, obstacles = base
    r = editing.add_tip(g, (22, 2, 8), (0, 0, -1), 1, Params(), obstacles)
    assert r.ok, r.message
    assert r.graph.problems() == [] and leaves_are_contacts(r.graph)
    assert (r.graph.kind == G.CONTACT).sum() == (g.kind == G.CONTACT).sum() + 1
    new = r.graph.tip == r.tip
    assert r.tip == editing.next_tip_id(g)
    assert r.graph.locked[new].all() and np.all(r.graph.flags[new] & G.FLAG_MANUAL)
    # Only the tree holding the new tip is locked (it may have joined an existing tree).
    roots = r.graph.roots()
    own_tree = roots == roots[np.flatnonzero(new)[0]]
    assert np.array_equal(r.graph.locked, own_tree)


def test_add_tip_next_to_a_tree_joins_it_and_thickens_it(base):
    g, obstacles = base
    r = editing.add_tip(g, (3.1, 2.9, 10), (0, 0, -1), 0, Params(), obstacles)
    assert r.ok, r.message
    out = r.graph
    assert out.problems() == []
    feet_before = np.isin(g.kind, G.ROOT_KINDS).sum()
    assert np.isin(out.kind, G.ROOT_KINDS).sum() == feet_before  # joined, no new foot
    # The tree it joined is now locked and at least one existing node got thicker.
    assert out.locked[: len(g)].any()
    assert np.any(out.radius[: len(g)] > g.radius + 1e-9)


def test_add_tip_where_there_is_no_room(base):
    g, obstacles = base
    r = editing.add_tip(g, (3, 3, 12), (0, 0, 1), 0, Params(), obstacles)  # top face, tip straight into it
    assert not r.ok and r.graph is g


def test_delete_tip_prunes_its_branch(base):
    g, _ = base
    tip = int(g.tip[g.kind == G.CONTACT][0])
    r = editing.delete_tip(g, tip)
    assert r.ok
    assert r.graph.problems() == [] and leaves_are_contacts(r.graph)
    assert (r.graph.kind == G.CONTACT).sum() == (g.kind == G.CONTACT).sum() - 1
    assert not (r.graph.tip == tip).any()


def test_deleting_the_last_tip_of_a_tree_removes_the_tree(base):
    g, obstacles = base
    lonely = editing.add_tip(g, (22, 2, 8), (0, 0, -1), 1, Params(), obstacles)
    r = editing.delete_tip(lonely.graph, lonely.tip)
    assert len(r.graph) == len(g)
    assert np.isin(r.graph.kind, G.ROOT_KINDS).sum() == np.isin(g.kind, G.ROOT_KINDS).sum()


def test_move_node(base):
    g, _ = base
    node = int(np.flatnonzero(g.kind == G.BRANCH)[0])
    r = editing.move_node(g, node, g.pos[node] + [0.3, 0, 0])
    assert r.ok and np.allclose(r.graph.pos[node], g.pos[node] + [0.3, 0, 0])
    assert r.graph.locked[node]
    contact = int(np.flatnonzero(g.kind == G.CONTACT)[0])
    assert not editing.move_node(g, contact, g.pos[contact]).ok
    assert not editing.move_node(g, node, g.pos[node] * [1, 1, -1]).ok  # below the bed


def test_move_tip(base):
    g, obstacles = base
    tip = int(g.tip[g.kind == G.CONTACT][0])
    r = editing.move_tip(g, tip, (22.5, 1.5, 8), (0, 0, -1), 1, Params(), obstacles)
    assert r.ok, r.message
    surface, _, target = editing.tip_surface(r.graph, r.tip, Params())
    assert np.allclose(surface, (22.5, 1.5, 8), atol=1e-6) and target == 1
    assert (r.graph.kind == G.CONTACT).sum() == (g.kind == G.CONTACT).sum()


def test_rebuild_tip_with_overrides(base):
    g, obstacles = base
    tip = int(g.tip[g.kind == G.CONTACT][0])
    r = editing.rebuild_tip(g, tip, {"contact_diameter": 0.5, "tip_length": 2.0}, Params(), obstacles)
    assert r.ok, r.message
    c = np.flatnonzero((r.graph.tip == r.tip) & (r.graph.kind == G.CONTACT))[0]
    assert r.graph.radius[c] == pytest.approx(0.25)
    assert r.graph.flags[c] & G.FLAG_OVERRIDE
    assert not editing.rebuild_tip(g, tip, {"max_radius": 1.0}, Params(), obstacles).ok


def test_lock_toggle_and_locked_part(base):
    g, _ = base
    node = int(np.flatnonzero(g.kind == G.FOOT)[0])
    locked = editing.set_tree_locked(g, node, True)
    tree = locked.roots() == node
    assert locked.locked[tree].all() and not locked.locked[~tree].any()
    kept = editing.locked_part(locked)
    assert kept.problems() == [] and len(kept) == tree.sum()
    assert not editing.set_tree_locked(locked, node, False).locked.any()


def test_drop_contacts_near_kept_tips():
    contacts = det.Contacts(
        positions=np.array([[0, 0, 5.0], [3, 0, 5], [6, 0, 5]]),
        normals=np.tile([0, 0, -1.0], (3, 1)),
        target=np.zeros(3, np.int64),
        kind=np.zeros(3, np.int64),
    )
    kept = editing.drop_contacts_near(contacts, np.array([[0.2, 0, 5.0]]), 0.75)
    assert len(kept) == 2 and kept.positions[0, 0] == 3


def test_concat_keeps_a_valid_forest(base):
    g, _ = base
    both = editing.concat(g, g)
    assert both.problems() == [] and len(both) == 2 * len(g)
