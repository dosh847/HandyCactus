# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np

from handycactus.core import heatmap
from handycactus.core.mesh_data import combine
from handycactus.core.params import Params

from .shapes import uv_sphere, voxels


def test_only_overhang_faces_are_drawn():
    mesh = combine([voxels([((0, 0, 3), (4, 4, 6))])])
    h = heatmap.build(mesh, Params())
    bottom = int((mesh.face_normals[:, 2] < -0.99).sum())
    assert h.face_count == bottom
    assert h.positions.shape == (3 * bottom, 3)
    assert h.colors.shape == (3 * bottom, 4)
    # Horizontal ceiling is the red end of the ramp, drawn on the surface.
    assert np.allclose(h.colors, heatmap.HIGH)
    assert np.allclose(h.positions[:, 2], 3)


def test_ramp_runs_from_yellow_to_red():
    mesh = combine([uv_sphere((0, 0, 10), 5, segments=48, rings=48)])
    h = heatmap.build(mesh, Params())
    reds = h.colors[:, 1]
    assert reds.min() < 0.2 and reds.max() > 0.75  # green channel spans the ramp


def test_faces_near_the_bed_are_skipped():
    mesh = combine([voxels([((0, 0, 0), (4, 4, 4))])])
    assert heatmap.build(mesh, Params()).face_count == 0


def test_coverage_gaps():
    mesh = combine([voxels([((0, 0, 3), (10, 10, 6))])])
    h = heatmap.build(mesh, Params(), contacts=np.array([[1.0, 1.0, 3.0]]))
    assert 0 < h.gap_face_count < h.face_count
    assert np.any(np.all(h.colors == heatmap.GAP, axis=1))
    none = heatmap.build(mesh, Params(), contacts=np.zeros((0, 3)))
    assert none.gap_face_count == none.face_count


def test_within_matches_brute_force():
    rng = np.random.default_rng(2)
    pts = rng.uniform(-20, 20, (3000, 3))
    targets = rng.uniform(-20, 20, (200, 3))
    d = np.linalg.norm(pts[:, None] - targets[None], axis=2).min(axis=1)
    assert np.array_equal(heatmap.within(pts, targets, 2.5), d <= 2.5)


def test_tips_cover_only_faces_at_or_above_them():
    # Two ceilings: z = 3 and z = 5, overlapping in plan; one contact on the upper one.
    mesh = combine([voxels([((0, 0, 3), (2, 2, 4)), ((0, 0, 5), (2, 2, 6))])])
    h = heatmap.build(mesh, Params(), contacts=np.array([[1.0, 1.0, 5.0]]))
    face_z = h.positions.reshape(-1, 3, 3)[:, :, 2].mean(axis=1)
    face_colors = h.colors[::3]
    upper, lower = face_z > 4.5, face_z < 3.5
    assert np.all(face_colors[upper] == heatmap.COVERED)
    assert np.all(face_colors[lower] == heatmap.GAP)  # 2 mm below the tip: not supported by it


def test_within_below_limit():
    pts = np.array([[0.0, 0, 0], [0, 0, 1]])
    targets = np.array([[0.0, 0, 0.5]])
    assert list(heatmap.within(pts, targets, 1.0, below=0.0)) == [False, True]
    near = Params().with_values({"tip_spacing": 1.0})
    assert list(heatmap.covered(pts, targets, near)) == [False, True]
