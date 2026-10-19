# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from handycactus.core.mesh_data import TargetMesh, combine, connected_components
from handycactus.core.spatial import NumpyInsideTester

from .shapes import uv_sphere, voxels


def test_voxel_box_is_closed_with_outward_normals():
    box = voxels([((0, 0, 0), (2, 3, 4))])
    mesh = combine([box])
    assert mesh.non_manifold_edge_count(0) == 0
    outward = np.einsum("ij,ij->i", mesh.face_normals, mesh.face_centroids - [1, 1.5, 2])
    assert np.all(outward > 0)
    assert mesh.face_areas.sum() == pytest.approx(2 * (2 * 3 + 2 * 4 + 3 * 4))


def test_open_mesh_has_non_manifold_edges():
    box = voxels([((0, 0, 0), (2, 2, 2))])
    open_box = TargetMesh("open", box.verts, box.tris[2:])
    assert combine([open_box]).non_manifold_edge_count(0) > 0


def test_combine_offsets_indices_and_tags_targets():
    a = voxels([((0, 0, 0), (1, 1, 1))], name="a")
    b = uv_sphere((5, 0, 5), 1, name="b")
    mesh = combine([a, b])
    assert mesh.names == ["a", "b"]
    assert len(mesh.verts) == len(a.verts) + len(b.verts)
    assert mesh.tris[mesh.face_target == 1].min() == len(a.verts)
    assert np.all(mesh.vert_target[: len(a.verts)] == 0)


def test_weights_length_is_checked():
    box = voxels([((0, 0, 0), (1, 1, 1))])
    box.force_weights = np.zeros(3)
    with pytest.raises(ValueError):
        combine([box])


def test_face_edge_pairs_on_a_closed_mesh_cover_every_edge_once():
    mesh = combine([uv_sphere((0, 0, 0), 1, segments=8, rings=4)])
    fa, fb, ev = mesh.face_edge_pairs()
    edges, _ = mesh.edges()
    assert len(fa) == len(edges)
    for a, b, e in zip(fa, fb, ev, strict=True):
        assert set(e) <= set(mesh.tris[a]) and set(e) <= set(mesh.tris[b])


def test_connected_components():
    labels = connected_components(6, np.array([0, 1, 4]), np.array([1, 2, 5]))
    assert list(labels) == [0, 0, 0, 3, 4, 4]
    chain = np.arange(999)
    labels = connected_components(1000, chain[::-1].copy(), chain[::-1] + 1)
    assert np.all(labels == 0)


def test_numpy_inside_tester():
    box = voxels([((0, 0, 0), (2, 2, 2))])
    tester = NumpyInsideTester(box.verts, box.tris)
    pts = np.array([[1, 1, 1], [3, 1, 1], [0.1, 1.9, 1.0], [1, 1, -0.1]])
    assert list(tester.is_inside(pts)) == [True, False, True, False]
