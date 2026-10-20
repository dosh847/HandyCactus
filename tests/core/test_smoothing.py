# SPDX-License-Identifier: GPL-3.0-or-later
"""Tree shape: smoothing, radius by height below the tallest tip, vertical trunk base."""

import numpy as np
import pytest

from handycactus.core import graph as G
from handycactus.core import routing
from handycactus.core.params import BRANCH_CONE_RATE, Params, radius_at
from handycactus.core.smoothing import smooth
from handycactus.core.spatial import NumpyObstacles

from .shapes import uv_sphere, voxels
from .test_routing import generate


def far_away():
    """Obstacles nowhere near the test graphs."""
    v = np.array([[100.0, 100, 100], [101, 100, 100], [100, 101, 100]])
    return NumpyObstacles(v, np.array([[0, 1, 2]]))


def zigzag(n=12, amplitude=0.15, step=0.4):
    """Foot, a zig-zagging trunk, a knuckle and a contact on top."""
    b = G.GraphBuilder()
    node = b.add((0, 0, 0), G.FOOT, 1.5)
    for i in range(1, n + 1):
        x = amplitude if i % 2 else -amplitude
        node = b.add((x, 0, i * step), G.BRANCH, 0.5, parent=node)
    k = b.add((0, 0, (n + 1) * step), G.KNUCKLE, 0.2, parent=node, tip=0, target=0, direction=(0, 0, -1))
    b.add((0, 0, (n + 1) * step + 1.5), G.CONTACT, 0.15, parent=k, tip=0, target=0, direction=(0, 0, -1))
    return b.build()


def test_smoothing_straightens_a_zigzag():
    g = zigzag()
    before = g.pos.copy()
    fixed = np.zeros(len(g), bool)
    fixed[1] = True  # the bottom trunk node, as the vertical base would
    smooth(g, Params(), far_away(), fixed, routing.HARD_CLEARANCE)
    branch = np.flatnonzero(g.kind == G.BRANCH)[1:]
    assert np.abs(g.pos[branch, 0]).max() < 0.5 * np.abs(before[branch, 0]).max()
    # Heights, tips, knuckles, feet and fixed nodes do not move.
    assert np.allclose(g.pos[:, 2], before[:, 2])
    still = np.isin(g.kind, (G.CONTACT, G.KNUCKLE, G.FOOT)) | fixed
    assert np.allclose(g.pos[still], before[still])
    assert g.problems() == []


def test_smoothing_keeps_clear_of_the_model():
    # A wall right next to the zig-zag's +x side: nodes must not move into its clearance.
    wall = voxels([((0.9, -5, 0), (3, 5, 8))])
    obstacles = NumpyObstacles(wall.verts, wall.tris)
    g = zigzag(amplitude=-0.3)  # starts on the far side; smoothing pulls toward x = 0
    before_room = obstacles.nearest_distance(g.pos)
    smooth(g, Params(), obstacles, np.zeros(len(g), bool), routing.HARD_CLEARANCE)
    room = obstacles.nearest_distance(g.pos)
    branch = g.kind == G.BRANCH
    hard = g.radius[branch] + routing.HARD_CLEARANCE
    assert np.all((room[branch] >= hard - 1e-9) | (room[branch] >= before_room[branch] - 1e-9))


def test_radius_follows_height_below_the_tallest_tip():
    p = Params()
    assert radius_at(p, 0.0) == pytest.approx(p.min_radius)
    # A short cone to the branch radius, then the taper on top.
    cone = (p.branch_radius - p.min_radius) / BRANCH_CONE_RATE
    assert radius_at(p, cone) == pytest.approx(p.branch_radius + p.taper_rate * cone)
    assert radius_at(p, 1000.0) == pytest.approx(p.max_radius)
    assert radius_at(p, 5.0) < radius_at(p, 6.0)


def test_merges_do_not_jump_in_radius():
    _, _, r, _ = generate(voxels([((0, 0, 10), (8, 8, 14))]))
    g = r.graph
    p = Params()
    for j in np.flatnonzero(g.kind == G.JUNCTION):
        kids = np.flatnonzero(g.parent == j)
        dz = g.pos[kids, 2].max() - g.pos[j, 2]
        # The thicker branch's radius, grown by at most the cone + taper over the drop.
        assert g.radius[j] <= g.radius[kids].max() + (BRANCH_CONE_RATE + p.taper_rate) * dz + 1e-6


def test_trunks_stand_vertical_at_the_bed():
    _, _, r, _ = generate(voxels([((0, 0, 10), (8, 8, 14))]))
    g = r.graph
    base = Params().foot_height + routing.TRUNK_BASE_MM
    for foot in np.flatnonzero(g.kind == G.FOOT):
        node = int(np.flatnonzero(g.parent == foot)[0])
        # Walk up the trunk while below the base: straight above the foot.
        while g.pos[node, 2] < base - 1e-9:
            assert np.allclose(g.pos[node, :2], g.pos[foot, :2], atol=1e-9)
            kids = np.flatnonzero(g.parent == node)
            if len(kids) != 1:
                break
            node = int(kids[0])
        # The segment that leaves the base starts at the foot's XY.
        assert np.linalg.norm(g.pos[g.parent[node], :2] - g.pos[foot, :2]) < 1e-9


def turning_per_mm(g):
    """Total bend angle at single-child branch nodes per mm of branch."""
    total = 0.0
    for n in np.flatnonzero(np.isin(g.kind, (G.BRANCH, G.JUNCTION)) & (g.parent >= 0)):
        kids = np.flatnonzero(g.parent == n)
        if len(kids) != 1:
            continue
        a = g.pos[kids[0]] - g.pos[n]
        b = g.pos[n] - g.pos[g.parent[n]]
        c = a @ b / (np.linalg.norm(a) * np.linalg.norm(b))
        total += np.degrees(np.arccos(np.clip(c, -1, 1)))
    e = g.parent >= 0
    return total / np.linalg.norm(g.pos[e] - g.pos[g.parent[e]], axis=1).sum()


def test_smoothing_reduces_turning(monkeypatch):
    shape = uv_sphere((0, 0, 14), 6, segments=32, rings=32)
    _, _, smoothed, obstacles = generate(shape)
    monkeypatch.setattr(routing, "smooth", lambda graph, *args, **kwargs: graph)
    _, _, raw, _ = generate(shape)
    assert turning_per_mm(smoothed.graph) < turning_per_mm(raw.graph)
    # And smoothing kept every branch node off the model.
    g = smoothed.graph
    branch = np.isin(g.kind, (G.BRANCH, G.JUNCTION))
    room = obstacles.nearest_distance(g.pos[branch]) - g.radius[branch]
    assert room.min() >= routing.HARD_CLEARANCE - 1e-6
