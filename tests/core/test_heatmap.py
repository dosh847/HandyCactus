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
    # Horizontal ceiling is the red end of the ramp, offset just below the surface.
    assert np.allclose(h.colors, heatmap.HIGH)
    assert np.allclose(h.positions[:, 2], 3 - heatmap.OFFSET_MM)


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
