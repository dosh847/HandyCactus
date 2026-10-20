# SPDX-License-Identifier: GPL-3.0-or-later
"""Avoidance grid (where the bed is still reachable), landing limits and load-based radius."""

import numpy as np
import pytest

from handycactus.core import graph as G
from handycactus.core.avoidance import AVOID_CELL, Avoidance
from handycactus.core.mesh_data import combine
from handycactus.core.params import Params, radius_at
from handycactus.core.spatial import NumpyObstacles

from .shapes import voxels
from .test_routing import CANOPY, generate


def obstacles_of(boxes):
    mesh = combine([voxels(boxes)])
    return NumpyObstacles(mesh.verts, mesh.tris), mesh


def test_column_occupancy_matches_the_shape():
    obs, _ = obstacles_of([((0, 0, 2), (4, 4, 6)), ((1, 1, 6), (3, 3, 8))])
    xy = np.array([[2.0, 2.0], [0.5, 0.5], [5.0, 5.0]])
    z = np.array([1.0, 3.0, 7.0])
    occ = obs.column_occupancy(xy, z)
    assert occ.tolist() == [[False, False, False], [True, True, False], [True, False, False]]
    av = Avoidance(obs, (0, 0), (4, 4), 10.0, Params())
    volume = av.occupied.sum() * AVOID_CELL**3
    assert volume == pytest.approx(4 * 4 * 4 + 2 * 2 * 2, rel=0.15)


def test_reachable_set_under_a_canopy():
    # A canopy (2..10, z 6..7) on a post over a plate covering the bed (0..12, z 0..1).
    obs, _ = obstacles_of(CANOPY)
    av = Avoidance(obs, (0, 0), (12, 12), 9.0, Params())
    assert not av.can_reach_bed([6.0, 3.0, 5.0], 0.2)[0]  # under the canopy, over the plate
    assert av.can_reach_bed([14.0, 6.0, 5.0], 0.2)[0]  # off the plate
    assert av.can_reach_bed([30.0, 30.0, 5.0], 0.2)[0]  # far outside the grid: no model
    # Above the canopy's edge, a branch can lean out past the plate.
    assert av.can_reach_bed([11.5, 6.0, 8.5], 0.2)[0]


def test_landing_trees_are_capped_and_flagged():
    params = Params().with_values({"max_landing_diameter": 1.0})
    _, _, r, _ = generate(voxels(CANOPY), params=params)
    g = r.graph
    roots = g.roots()
    landings = np.flatnonzero(g.kind == G.LANDING)
    assert len(landings)
    for land in landings:
        tree = (roots == land) & (g.kind != G.LANDING)
        assert g.radius[tree].max() <= 0.5 + 1e-9
    # The ball above each landing tip is as wide as the branch there (no gap at the joint).
    above = np.array([np.flatnonzero(g.parent == n)[0] for n in landings])
    assert np.allclose(g.sphere[above], g.radius[above])


def test_thick_landings_are_flagged():
    from handycactus.core.checks import check_landings

    params = Params().with_values({"max_landing_diameter": 0.4})  # thinner than any branch
    _, _, r, _ = generate(voxels(CANOPY), params=params)
    g = r.graph
    flagged = (g.flags[g.kind == G.LANDING] & G.FLAG_THICK_LANDING) > 0
    assert flagged.any()
    severities = {f.severity for f in check_landings(g)}
    assert "WARNING" in severities


def test_radius_follows_the_load():
    p = Params()
    lone, merged = radius_at(p, 30.0, 1), radius_at(p, 30.0, 16)
    assert lone == pytest.approx(max(p.branch_radius, 30.0 / 80))  # thin, but not slender
    assert merged > 3 * lone  # cross-sections add up
    assert radius_at(p, 30.0, 1000) == pytest.approx(radius_at(p, 30.0))  # the height rule bounds it
    assert radius_at(p, 60.0, 1000) == p.max_radius
    assert radius_at(p, 200.0, 1) == pytest.approx(min(p.max_radius, 200.0 / 80))
