# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np

from handycactus.core import checks
from handycactus.core import graph as G
from handycactus.core.mesh_data import combine
from handycactus.core.overlap import repair_overlaps
from handycactus.core.params import Params
from handycactus.core.spatial import NumpyObstacles

from .shapes import voxels
from .test_checks import single_tree


def collisions(g, obstacles):
    return [f for f in checks.run_checks(g, Params(), obstacles=obstacles) if f.code == "collision"]


def test_segment_through_a_thin_bar_is_pushed_around_it():
    # A thin bar between two clear nodes: only the segment between them hits it.
    mesh = combine([voxels([((-0.5, -3, 4), (0.5, 3, 5))])])
    obstacles = NumpyObstacles(mesh.verts, mesh.tris)
    g = single_tree([(0, 0, 9), (0, 0, 8), (0, 0, 7), (0, 0, 2), (0, 0, 0.4), (0, 0, 0)],
                    [0.15, 0.2, 0.3, 0.35, 0.4, 1.5])  # fmt: skip
    assert collisions(g, obstacles)
    left = repair_overlaps(g, Params(), obstacles)
    assert left == 0
    assert not collisions(g, obstacles)
    assert not g.problems()


def test_clear_graph_is_untouched():
    mesh = combine([voxels([((5, 5, 4), (6, 6, 5))])])
    obstacles = NumpyObstacles(mesh.verts, mesh.tris)
    g = single_tree([(0, 0, 6), (0, 0, 5), (0, 0, 0.4), (0, 0, 0)], [0.15, 0.2, 0.3, 1.5])
    before = g.pos.copy()
    assert repair_overlaps(g, Params(), obstacles) == 0
    assert np.array_equal(g.pos, before) and len(g) == 4
