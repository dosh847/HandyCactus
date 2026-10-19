# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from handycactus.core import detection as det
from handycactus.core import graph as G
from handycactus.core import routing, tips
from handycactus.core.mesh_data import combine
from handycactus.core.params import Params
from handycactus.core.spatial import NumpyInsideTester, NumpyObstacles

from .shapes import uv_sphere, voxels


def generate(*targets, params=None):
    params = params or Params()
    mesh = combine(targets)
    testers = []
    for i in range(mesh.target_count):
        v = np.flatnonzero(mesh.vert_target == i)
        testers.append(NumpyInsideTester(mesh.verts[v], mesh.tris[mesh.face_target == i] - v[0]))
    found = det.detect(mesh, params, testers)
    obstacles = NumpyObstacles(mesh.verts, mesh.tris)
    t = tips.build_tips(found.contacts, params, obstacles)
    return mesh, t, routing.route(t, params, obstacles), obstacles


def branch_angles(g):
    """Angle from vertical of every edge that is not a tip (contact -> knuckle)."""
    e = g.edges
    e = e[g.kind[e[:, 0]] != G.CONTACT]
    v = g.pos[e[:, 1]] - g.pos[e[:, 0]]
    length = np.linalg.norm(v, axis=1)
    keep = length > 1e-9
    return np.degrees(np.arccos(np.clip(-v[keep, 2] / length[keep], -1, 1)))


def test_lifted_box_every_tip_reaches_the_bed():
    # 10 mm up leaves room to merge; at a 3 mm lift most tips need their own foot.
    _, t, r, _ = generate(voxels([((0, 0, 10), (8, 8, 14))]))
    g = r.graph
    assert t.ok.all()
    assert len(r.unreachable_tips) == 0
    assert g.problems() == []
    assert (g.kind == G.CONTACT).sum() == len(t)
    assert branch_angles(g).max() <= Params().max_branch_angle + 1e-6
    # Merging happened: far fewer feet than tips.
    assert (g.kind == G.FOOT).sum() < len(t) / 3


def test_feet_are_flared_and_on_the_bed():
    _, _, r, _ = generate(voxels([((0, 0, 3), (8, 8, 8))]))
    g = r.graph
    feet = np.flatnonzero(g.kind == G.FOOT)
    assert np.allclose(g.pos[feet, 2], 0)
    assert np.all(g.radius[feet] >= Params().foot_radius - 1e-9)
    # The node above each foot sits at foot_height, straight above it.
    above = np.array([np.flatnonzero(g.parent == f)[0] for f in feet])
    assert np.allclose(g.pos[above, 2], Params().foot_height)
    assert np.allclose(g.pos[above, :2], g.pos[feet, :2])


def test_radii_grow_and_respect_limits():
    _, _, r, _ = generate(voxels([((0, 0, 3), (8, 8, 8))]))
    g = r.graph
    p = Params()
    branch = (g.kind == G.BRANCH) | (g.kind == G.JUNCTION)
    assert np.all(g.radius[branch] >= p.min_radius - 1e-9)
    assert np.all(g.radius[branch] <= p.max_radius + 1e-9)
    # Down every edge (excluding feet and tips) the radius never shrinks.
    e = g.edges
    e = e[(g.kind[e[:, 0]] != G.CONTACT) & (g.kind[e[:, 1]] != G.FOOT)]
    assert np.all(g.radius[e[:, 1]] >= g.radius[e[:, 0]] - 1e-9)


def test_branches_keep_clear_of_the_model():
    # A ledge under an arm: branches from the arm's underside must go around it.
    shape = voxels([((0, 0, 0), (6, 6, 3)), ((0, 0, 3), (6, 6, 10)), ((6, 0, 7), (14, 6, 9))])
    _, _, r, obstacles = generate(shape)
    g = r.graph
    assert g.problems() == []
    body = (g.kind == G.BRANCH) | (g.kind == G.JUNCTION)
    d = obstacles.nearest_distance(g.pos[body])
    # Allowed to sit closer only where it started closer (right under a knuckle).
    assert np.percentile(d - g.radius[body], 5) > 0.2


def test_trapped_tips_are_reported_unreachable():
    # A canopy over a wide plate lying on the bed: under the canopy there is no bed to reach,
    # and v1 never lands on the model.
    shape = voxels([((0, 0, 0), (12, 12, 1)), ((5, 5, 1), (7, 7, 6)), ((2, 2, 6), (10, 10, 7))])
    _, t, r, _ = generate(shape)
    assert len(r.unreachable_tips) > 0
    assert r.graph.problems() == []


def test_multi_target_trees_may_merge():
    a = voxels([((0, 0, 4), (4, 4, 8))], name="a")
    b = voxels([((4.5, 0, 4), (8.5, 4, 8))], name="b")
    _, _, r, _ = generate(a, b)
    g = r.graph
    targets_per_tree = {}
    contacts = np.flatnonzero(g.kind == G.CONTACT)
    for c in contacts:
        targets_per_tree.setdefault(g.tree[c], set()).add(int(g.target[c]))
    assert any(len(s) == 2 for s in targets_per_tree.values())


def test_routing_is_deterministic():
    shape = uv_sphere((0, 0, 9), 5)
    _, _, a, _ = generate(shape)
    _, _, b, _ = generate(shape)
    assert np.array_equal(a.graph.pos, b.graph.pos)


def test_graph_problems_detects_bad_graphs():
    b = G.GraphBuilder()
    foot = b.add((0, 0, 0), G.FOOT, 1.0)
    k = b.add((0, 0, 5), G.KNUCKLE, 0.2, parent=foot)
    b.add((0, 0, 6), G.CONTACT, 0.1, parent=k)
    g = b.build()
    assert g.problems() == []
    bad = b.build()
    bad.parent[foot] = k  # cycle
    assert "cycle in parent pointers" in bad.problems()
    off = b.build()
    off.pos[foot, 2] = 1.0
    assert "a foot is not on the bed" in off.problems()


@pytest.mark.parametrize("foot_height", [0.0, 0.4])
def test_zero_foot_height(foot_height):
    p = Params().with_values({"foot_height": foot_height})
    _, _, r, _ = generate(voxels([((0, 0, 3), (4, 4, 6))]), params=p)
    assert r.graph.problems() == []
