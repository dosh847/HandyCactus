# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from handycactus.core import detection as det
from handycactus.core.mesh_data import combine
from handycactus.core.params import Params
from handycactus.core.spatial import NumpyInsideTester

from .shapes import cone_down, uv_sphere, voxels


def run(*targets, params=None, closed=True, seed=0):
    mesh = combine(targets)
    testers = [
        NumpyInsideTester(mesh.verts[mesh.vert_target == i], _local_tris(mesh, i)) if closed else None
        for i in range(mesh.target_count)
    ]
    return mesh, det.detect(mesh, params or Params(), testers, seed=seed)


def _local_tris(mesh, i):
    offset = np.flatnonzero(mesh.vert_target == i)[0]
    return mesh.tris[mesh.face_target == i] - offset


def min_pair_distance(p):
    d = np.linalg.norm(p[:, None] - p[None], axis=2)
    d[np.diag_indices(len(p))] = np.inf
    return d.min()


def test_lifted_box_gets_contacts_on_its_underside_only():
    mesh, r = run(voxels([((0, 0, 3), (10, 10, 13))]))
    c = r.contacts
    assert 40 <= len(c) <= 140
    assert np.allclose(c.positions[:, 2], 3.0)
    assert np.allclose(c.normals, [0, 0, -1])
    # A horizontal ceiling is the steepest case: spacing = tip_spacing / steepness_density.
    assert min_pair_distance(c.positions) >= 1.5 / 1.5 - 1e-9
    assert (c.kind == det.PATCH_LOWEST).sum() == 1
    assert r.support_faces.sum() == (mesh.face_normals[:, 2] < -0.99).sum()


def test_box_on_the_bed_needs_nothing():
    _, r = run(voxels([((0, 0, 0), (10, 10, 10))]))
    assert len(r.contacts) == 0


def test_lifted_sphere_contacts_are_on_the_overhang_and_bottom_pole():
    sphere = uv_sphere((0, 0, 8), 5)
    _, r = run(sphere)
    c = r.contacts
    assert len(c) > 5
    samples = c.kind == det.SAMPLE
    assert np.all(c.normals[samples, 2] < -np.sin(np.radians(45)) + 1e-9)
    pole = np.array([0, 0, 3.0])
    assert np.any(np.linalg.norm(c.positions - pole, axis=1) < 1e-9)
    assert c.kind[np.argmin(c.positions[:, 2])] == det.MINIMUM


def test_steep_downward_cone_gets_exactly_its_tip():
    # Walls 20 degrees from vertical are self-supporting; the apex is not.
    height = 10.0
    radius = height * np.tan(np.radians(20))
    _, r = run(cone_down((0, 0, 3), height, radius))
    c = r.contacts
    assert len(c) == 1
    assert c.kind[0] == det.MINIMUM
    assert np.allclose(c.positions[0], [0, 0, 3])


def _bridge(gap):
    return voxels(
        [
            ((0, 0, 0), (2, 4, 4)),
            ((2 + gap, 0, 0), (4 + gap, 4, 4)),
            ((0, 0, 4), (4 + gap, 4, 5)),
        ]
    )


def test_short_bridge_needs_no_contacts():
    _, r = run(_bridge(1.5))
    assert len(r.contacts) == 0


def test_short_bridge_is_supported_without_bridging():
    _, r = run(_bridge(1.5), params=Params().with_values({"max_bridge": 0.0}))
    assert len(r.contacts) > 0


def test_long_bridge_gets_contacts_away_from_the_pillars():
    gap = 6.0
    _, r = run(_bridge(gap))
    x = r.contacts.positions[:, 0]
    assert len(x) > 0
    assert np.all((x > 2 + 1.0 - 1e-9) & (x < 2 + gap - 1.0 + 1e-9))


def test_cantilever_root_bridges_but_the_arm_is_supported():
    tbar = voxels([((0, 0, 0), (2, 4, 7)), ((2, 0, 6), (10, 4, 7))])
    _, r = run(tbar)
    x = r.contacts.positions[:, 0]
    assert len(x) > 5
    assert x.min() > 3.0 - 1e-9  # within max_bridge/2 of the stem is bridged


def test_overlapping_part_gets_no_contacts_inside_the_other():
    body = voxels([((0, 0, 0), (10, 4, 10))], name="body")
    arm = voxels([((8, 0, 5), (20, 4, 8))], name="arm")
    _, r = run(body, arm)
    c = r.contacts
    assert len(c) > 0
    assert np.all(c.target == 1)
    # The arm's underside inside the body is dropped, and the edge where it
    # enters the body counts as supported, so the first mm bridges.
    assert c.positions[:, 0].min() > 11.0 - 1e-9


def test_open_target_skips_the_overlap_test_with_a_warning():
    body = voxels([((0, 0, 0), (10, 4, 10))], name="body")
    arm = voxels([((8, 0, 5), (20, 4, 8))], name="arm")
    _, r = run(body, arm, closed=False)
    assert len(r.warnings) == 2
    assert r.contacts.positions[:, 0].min() < 10.0


def test_force_paint_adds_contacts_on_a_wall():
    box = voxels([((0, 0, 3), (10, 10, 13))])
    box.force_weights = (box.verts[:, 0] == 10).astype(float)
    _, r = run(box)
    wall = np.isclose(r.contacts.positions[:, 0], 10) & (r.contacts.positions[:, 2] > 3.5)
    assert wall.sum() > 10


def test_block_paint_removes_contacts_and_reports_islands():
    box = voxels([((0, 0, 3), (10, 10, 13))])
    box.block_weights = (box.verts[:, 2] == 3).astype(float)
    _, r = run(box)
    assert len(r.contacts) == 0
    assert len(r.blocked_islands) == 1


def test_bed_ignore_height():
    box = voxels([((0, 0, 0.5), (10, 10, 5))])
    _, r = run(box)
    assert len(r.contacts) > 0
    _, r = run(box, params=Params().with_values({"bed_ignore_height": 1.0}))
    assert len(r.contacts) == 0


def test_detection_is_deterministic():
    sphere = uv_sphere((0, 0, 8), 5)
    _, a = run(sphere, seed=3)
    _, b = run(sphere, seed=3)
    assert np.array_equal(a.contacts.positions, b.contacts.positions)


def test_wrong_tester_count_is_rejected():
    mesh = combine([voxels([((0, 0, 3), (2, 2, 5))])])
    with pytest.raises(ValueError):
        det.detect(mesh, Params(), [])


def test_severity():
    n = np.array([[0, 0, -1.0], [0, 0, 1.0], [1.0, 0, 0]])
    s = det.overhang_severity(n, 45)
    assert s[0] == pytest.approx(1.0)
    assert s[1] < 0 and s[2] < 0
    assert list(det.needs_support(n, 45)) == [True, False, False]


def test_float_noise_on_flat_faces_is_not_a_minimum():
    box = voxels([((0, 0, 3), (10, 10, 13))])
    rng = np.random.default_rng(0)
    box.verts = box.verts + rng.uniform(-1e-6, 1e-6, box.verts.shape)  # float32-like noise
    _, r = run(box)
    minima = r.contacts.positions[r.contacts.kind == det.MINIMUM]
    assert len(minima) == 0
    assert np.allclose(r.contacts.positions[:, 2], 3.0, atol=1e-5)


def test_metre_sized_model_is_refused_with_a_units_hint():
    # A 10 m box (a mini imported as metres) would need ~100k contacts on its underside.
    huge = voxels([((0, 0, 1000), (10000, 10000, 2000))], voxel=1000.0)
    with pytest.raises(det.TooManyContacts, match="Units"):
        run(huge)
