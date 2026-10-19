# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np

from handycactus.core import picking


def ortho(scale=0.1):
    """Looking down -Z; x, y in [-1/scale, 1/scale] fill the region."""
    return np.diag([scale, scale, -scale, 1.0])


def test_project_centre_and_corner():
    px, vis = picking.project([[0, 0, 0], [10, 10, 0]], ortho(), 200, 100)
    assert vis.all()
    assert np.allclose(px[0], [100, 50])
    assert np.allclose(px[1], [200, 100])


def test_points_behind_a_perspective_camera_are_hidden():
    persp = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, -1, -0.2], [0, 0, -1, 0]], float)
    _, vis = picking.project([[0, 0, -5], [0, 0, 5]], persp, 100, 100)
    assert list(vis) == [True, False]


def test_pick_nearest_within_radius():
    px = np.array([[10, 10], [50, 50], [55, 52]], float)
    vis = np.array([True, True, True])
    assert picking.pick(px, vis, (54, 51)) == 2
    assert picking.pick(px, vis, (200, 200)) == -1
    assert picking.pick(px, np.array([True, True, False]), (54, 51)) == 1
    assert picking.pick(np.zeros((0, 2)), np.zeros(0, bool), (0, 0)) == -1
