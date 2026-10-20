# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from handycactus.core import detection as det
from handycactus.core import tips
from handycactus.core.mesh_data import combine
from handycactus.core.params import Params
from handycactus.core.spatial import NumpyObstacles, closest_point_on_triangles

from .shapes import uv_sphere, voxels


def contacts(points, normals, target=0):
    points = np.asarray(points, float)
    return det.Contacts(
        positions=points,
        normals=np.asarray(normals, float),
        target=np.full(len(points), target),
        kind=np.zeros(len(points), np.int64),
    )


def obstacles(*targets):
    mesh = combine(targets)
    return NumpyObstacles(mesh.verts, mesh.tris)


def test_clamp_to_cone():
    out = tips.clamp_to_cone(np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 0, 1.0]]), 45)
    assert np.allclose(out[0], [np.sqrt(0.5), 0, -np.sqrt(0.5)])
    assert np.allclose(out[1], [0, 0, -1])
    assert np.allclose(out[2], [0, 0, -1])


def test_flat_underside_tip_points_straight_down():
    box = voxels([((0, 0, 3), (10, 10, 8))])
    t = tips.build_tips(contacts([[5, 5, 3]], [[0, 0, -1]]), Params(), obstacles(box))
    assert t.ok.all()
    assert np.allclose(t.axis[0], [0, 0, -1])
    assert np.allclose(t.knuckle[0], [5, 5, 3 - 1.5])
    assert np.allclose(t.contact[0], [5, 5, 3 + 0.15])  # penetration into the model


def test_sloped_surface_tip_follows_the_normal_within_the_cone():
    sphere = uv_sphere((0, 0, 10), 5, segments=48, rings=48)
    n = np.array([np.sin(np.radians(30)), 0, -np.cos(np.radians(30))])
    p = np.array([0, 0, 10]) + 5 * n
    t = tips.build_tips(contacts([p], [n]), Params(), obstacles(sphere))
    assert t.ok.all()
    assert np.degrees(np.arccos(t.axis[0] @ n)) < 1.0


def test_steep_normal_is_clamped():
    sphere = uv_sphere((0, 0, 10), 5, segments=48, rings=48)
    n = np.array([np.sin(np.radians(70)), 0, -np.cos(np.radians(70))])
    p = np.array([0, 0, 10]) + 5 * n
    t = tips.build_tips(contacts([p], [n]), Params(), obstacles(sphere))
    assert t.ok.all()
    angle_from_down = np.degrees(np.arccos(-t.axis[0, 2]))
    assert angle_from_down <= 45 + 1e-6


def test_blocked_tip_finds_another_axis():
    # Contact on a ceiling with a low wall right below: straight down is blocked.
    shape = voxels([((0, 0, 3), (10, 10, 6)), ((4, 0, 2), (6, 10, 3))])
    p = np.array([6.25, 5.0, 3.0])  # 0.25 mm right of the wall: a straight tip's knuckle hits it
    t = tips.build_tips(contacts([p], [[0, 0, -1]]), Params(), obstacles(shape))
    assert t.ok.all()
    assert t.axis[0, 0] > 0.1  # leans away from the wall
    mesh = combine([shape])
    seg = p + t.axis[0] * np.linspace(0.5, t.length[0], 20)[:, None]
    q = closest_point_on_triangles(
        seg[:, None], mesh.verts[mesh.tris[:, 0]], mesh.verts[mesh.tris[:, 1]], mesh.verts[mesh.tris[:, 2]]
    )
    assert np.linalg.norm(q - seg[:, None], axis=2).min() > 0.15


def test_impossible_tip_is_unreachable():
    # A contact at the bottom of a narrow slot: nothing fits.
    shape = voxels([((0, 0, 0), (2, 4, 6)), ((2.5, 0, 0), (4.5, 4, 6)), ((2, 0, 5), (2.5, 4, 6))])
    t = tips.build_tips(contacts([[2.25, 2, 5]], [[0, 0, -1]]), Params(), obstacles(shape))
    assert not t.ok.any()


def test_overlapping_knuckles_are_separated():
    box = voxels([((0, 0, 3), (10, 10, 8))])
    t = tips.build_tips(contacts([[5, 5, 3], [5.3, 5, 3]], [[0, 0, -1]] * 2), Params(), obstacles(box))
    d = np.linalg.norm(t.knuckle[0] - t.knuckle[1])
    assert d >= Params().knuckle_diameter - 1e-9


@pytest.mark.parametrize("n", [[0, 0, 1.0], [1.0, 0, 0]])
def test_candidate_axes_start_with_the_clamped_normal(n):
    axes = tips.candidate_axes(np.array(n), 45)
    assert np.allclose(axes[0], tips.clamp_to_cone(np.array(n), 45)[0])
    assert np.all(np.degrees(np.arccos(np.clip(-axes[:, 2], -1, 1))) <= 45 + 1e-6)


def test_knuckles_leave_room_for_a_branch():
    # A contact under a plate with another plate 1.2 mm below: a knuckle there would trap
    # its branch. Every accepted knuckle keeps min_radius + the hard branch clearance.
    shape = voxels([((0, 0, 6), (10, 10, 7)), ((0, 0, 3), (6, 10, 4.8))])
    pts = [[x, 5.0, 6.0] for x in np.arange(1.0, 10.0, 1.0)]
    t = tips.build_tips(contacts(pts, [[0, 0, -1]] * len(pts)), Params(), obstacles(shape))
    p = Params()
    d = obstacles(shape).nearest_distance(t.knuckle[t.ok])
    assert np.all(d >= p.min_radius + tips.BRANCH_HARD_CLEARANCE - 1e-6)
    assert t.ok.any()


def test_points_inside_a_block():
    from handycactus.core.spatial import points_inside

    mesh = combine([voxels([((-2, -2, 2), (2, 2, 6))])])
    obstacles = NumpyObstacles(mesh.verts, mesh.tris)
    pts = np.array([(0, 0, 4), (1.5, -1.5, 2.5), (0, 0, 7), (3, 0, 4), (0, 0, 1)])
    assert points_inside(obstacles, pts).tolist() == [True, True, False, False, False]
