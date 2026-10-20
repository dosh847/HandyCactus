# SPDX-License-Identifier: GPL-3.0-or-later
"""Fewer unreachable contacts: failed tips are moved or dropped as covered, small
self-supporting patches need no contact, and lost tips get a second routing pass."""

import numpy as np

from handycactus.core import detection as det
from handycactus.core import graph as G
from handycactus.core import reach, relocate, tips
from handycactus.core.mesh_data import TargetMesh, combine
from handycactus.core.params import Params
from handycactus.core.spatial import NumpyObstacles

from .shapes import cone_down, voxels
from .test_routing import generate


def crease():
    """A ceiling at z = 8 (x 0..6) meeting a wall that runs down to the bed at x = 6."""
    return combine([voxels([((0, 0, 8), (6, 6, 10)), ((6, 0, 0), (7, 6, 10))])])


def contacts_at(points):
    points = np.asarray(points, float).reshape(-1, 3)
    return det.Contacts(
        positions=points,
        normals=np.tile([0.0, 0.0, -1.0], (len(points), 1)),
        target=np.zeros(len(points), np.int64),
        kind=np.full(len(points), det.PATCH_LOWEST),
    )


# Vertical tips only, so a contact against the wall cannot lean its tip away from it.
STRAIGHT = Params().with_values({"max_tip_angle": 0.0})


def test_a_contact_in_a_crease_moves_to_room_nearby():
    mesh = crease()
    obstacles = NumpyObstacles(mesh.verts, mesh.tris)
    params = STRAIGHT
    c = contacts_at([(5.97, 3.0, 8.0)])  # right against the wall: no room for the cone
    built = tips.build_tips(c, params, obstacles)
    assert not built.ok[0]
    support = det.needs_support(mesh.face_normals, params.overhang_angle)
    r = relocate.relocate_failed(c, built, mesh, support, params, obstacles)
    assert r.moved == 1 and r.covered == 0 and r.tips.ok.all()
    moved = r.contacts.positions[0]
    assert np.linalg.norm(moved - c.positions[0]) <= relocate.RELOCATE_FACTOR * params.tip_spacing + 1e-9
    assert moved[2] == np.float64(8.0) and moved[0] < 5.97  # still on the ceiling, away from the wall


def test_a_failed_contact_next_to_a_working_tip_is_covered():
    mesh = crease()
    obstacles = NumpyObstacles(mesh.verts, mesh.tris)
    params = STRAIGHT
    c = contacts_at([(5.97, 3.0, 8.0), (5.4, 3.0, 8.0)])
    built = tips.build_tips(c, params, obstacles)
    assert list(built.ok) == [False, True]
    support = det.needs_support(mesh.face_normals, params.overhang_angle)
    r = relocate.relocate_failed(c, built, mesh, support, params, obstacles)
    assert r.covered == 1 and len(r.contacts) == 1 and r.tips.ok.all()
    assert np.allclose(r.contacts.positions[0], (5.4, 3.0, 8.0))


def ledge(width):
    """An open wall at x = 0 with a small shallow ledge at z = 5: its underside rises
    0.3 mm over 0.4 mm (about 53° from vertical), `width` mm long in y."""
    profile = [(0.0, 0.0), (0.0, 5.0), (0.4, 5.3), (0.4, 5.5), (0.0, 5.8), (0.0, 10.0)]
    verts, tris = [], []
    for y in (0.0, width):
        verts += [(x, y, z) for x, z in profile]
    n = len(profile)
    for i in range(n - 1):
        a, b, c, d = i, i + 1, n + i + 1, n + i
        (x0, z0), (x1, z1) = profile[i], profile[i + 1]
        outward = np.array([z1 - z0, 0.0, -(x1 - x0)])  # material on the -x side
        v = np.array(verts, float)
        normal = np.cross(v[b] - v[a], v[c] - v[a])
        tris += [[a, b, c], [a, c, d]] if normal @ outward > 0 else [[a, c, b], [a, d, c]]
    return TargetMesh("ledge", np.array(verts, float), np.array(tris))


def test_small_shallow_patches_growing_from_below_need_no_contact():
    params = Params()
    small = det.detect(combine([ledge(1.0)]), params, [None])  # 0.5 mm² underside
    assert len(small.contacts) == 0 and not small.support_faces.any()
    assert small.stats["self_supporting_patches"] == 1
    wide = det.detect(combine([ledge(3.0)]), params, [None])  # 1.5 mm²: still supported
    assert len(wide.contacts) > 0 and wide.support_faces.any()


def test_islands_are_still_supported():
    # A small downward cone is a true island (its point starts in mid-air).
    found = det.detect(combine([cone_down((0, 0, 6), 0.6, 0.4)]), Params(), [None])
    assert (found.contacts.kind == det.MINIMUM).sum() == 1


def test_route_with_retry_never_loses_tips():
    mesh, t, first, obstacles = generate(voxels([((0, 0, 10), (8, 8, 14)), ((-2, -2, 6), (10, 10, 7))]))
    params = Params()
    second = reach.route_with_retry(t, params, obstacles)
    assert second.graph.problems() == []
    assert set(second.unreachable_tips) <= set(first.unreachable_tips)
    supported = np.unique(second.graph.tip[second.graph.kind == G.CONTACT])
    assert len(supported) + len(second.unreachable_tips) == len(t)
