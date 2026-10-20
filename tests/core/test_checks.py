# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from handycactus.core import checks
from handycactus.core import graph as G
from handycactus.core.mesh_data import combine
from handycactus.core.params import Params
from handycactus.core.spatial import NumpyObstacles

from .shapes import voxels
from .test_routing import generate


def codes(findings):
    return sorted({f.code for f in findings})


def single_tree(points, radii):
    """A chain, points and radii top to bottom: contact, knuckle, branch nodes..., foot."""
    b = G.GraphBuilder()
    nodes = [b.add(points[-1], G.FOOT, radii[-1])]
    for p, r in zip(points[-2:1:-1], radii[-2:1:-1], strict=True):
        nodes.append(b.add(p, G.BRANCH, r, parent=nodes[-1]))
    k = b.add(points[1], G.KNUCKLE, radii[1], parent=nodes[-1], tip=0, target=0, sphere=0.3)
    b.add(points[0], G.CONTACT, radii[0], parent=k, tip=0, target=0, direction=(0, 0, -1))
    return b.build()


@pytest.fixture(scope="module")
def generated():
    mesh, _, r, obstacles = generate(voxels([((0, 0, 10), (8, 8, 14))]))
    return mesh, r.graph, obstacles


def test_a_generated_set_has_no_errors(generated):
    mesh, graph, obstacles = generated
    findings = checks.run_checks(graph, Params(), mesh, obstacles)
    assert [f for f in findings if f.is_error] == []
    assert "coverage" not in codes(findings)


def test_invalid_graph_reports_only_that():
    g = single_tree([(0, 0, 6), (0, 0, 5), (0, 0, 0.4), (0, 0, 0)], [0.15, 0.2, 0.3, 1.5])
    g.parent[np.flatnonzero(g.kind == G.FOOT)[0]] = 1  # cycle
    findings = checks.run_checks(g, Params())
    assert codes(findings) == ["graph"]


def test_unreachable_contacts_are_errors():
    g = single_tree([(0, 0, 6), (0, 0, 5), (0, 0, 0.4), (0, 0, 0)], [0.15, 0.2, 0.3, 1.5])
    findings = checks.run_checks(g, Params(), unreachable=np.array([[1.0, 2.0, 3.0], [4, 5, 6]]))
    bad = [f for f in findings if f.code == "unreachable"]
    assert len(bad) == 2 and all(f.is_error for f in bad)
    assert bad[0].location == (1.0, 2.0, 3.0)


def test_thin_segments_on_a_bigger_nozzle():
    g = single_tree([(0, 0, 6), (0, 0, 5), (0, 0, 0.4), (0, 0, 0)], [0.15, 0.2, 0.3, 1.5])
    assert "thin" not in codes(checks.run_checks(g, Params()))
    p = Params().with_values({"nozzle_diameter": 0.4})
    thin = [f for f in checks.run_checks(g, p) if f.code == "thin"]
    assert len(thin) == 2  # the segments, and the contact diameter under 0.75 x nozzle


def test_steep_segment():
    g = single_tree([(0, 0, 6), (0, 0, 5), (3, 0, 4), (3, 0, 0.4), (3, 0, 0)], [0.15, 0.2, 0.25, 0.3, 1.5])
    steep = [f for f in checks.run_checks(g, Params()) if f.code == "steep"]
    assert len(steep) == 1
    assert "72" in steep[0].message  # atan(3 / 1) = 71.6 degrees


def test_branch_through_the_model_is_a_collision():
    block = voxels([((-2, -2, 2), (2, 2, 3))])
    mesh = combine([block])
    obstacles = NumpyObstacles(mesh.verts, mesh.tris)
    g = single_tree([(0, 0, 6), (0, 0, 5), (0, 0, 0.4), (0, 0, 0)], [0.15, 0.2, 0.3, 1.5])
    hits = [f for f in checks.run_checks(g, Params(), obstacles=obstacles) if f.code == "collision"]
    assert len(hits) == 1 and hits[0].is_error and "runs into" in hits[0].message


def test_slender_trunk():
    g = single_tree([(0, 0, 31), (0, 0, 30), (0, 0, 0.4), (0, 0, 0)], [0.15, 0.2, 0.3, 1.5])
    slender = [f for f in checks.run_checks(g, Params()) if f.code == "slender"]
    assert len(slender) == 1  # ~30 mm on a ~0.5 mm trunk


def test_tiny_foot():
    g = single_tree([(0, 0, 6), (0, 0, 5), (0, 0, 0.4), (0, 0, 0)], [0.15, 0.2, 0.3, 0.05])
    assert "foot" in codes(checks.run_checks(g, Params()))


def test_coverage_gap_regions(generated):
    mesh, graph, _ = generated
    # Drop every contact on the right half of the box's underside: one gap region.
    contact = graph.kind == G.CONTACT
    right = contact & (graph.pos[:, 0] > 4)
    trimmed = graph.subset(~right)
    trimmed.kind[trimmed.kind == G.KNUCKLE] = G.KNUCKLE  # knuckles without contacts are fine here
    gaps = checks.check_coverage(trimmed, Params(), mesh)
    assert len(gaps) == 1
    assert gaps[0].location[0] > 4


def test_branch_buried_in_the_model_is_a_collision():
    # The whole branch runs through the middle of a thick block, far from every face:
    # unsigned distances alone call it clear.
    block = voxels([((-5, -5, 1), (5, 5, 9))])
    mesh = combine([block])
    obstacles = NumpyObstacles(mesh.verts, mesh.tris)
    g = single_tree([(0, 0, 7.2), (0, 0, 7), (0, 0, 6), (0, 0, 4), (0, 0, 0.4), (0, 0, 0)],
                    [0.15, 0.2, 0.3, 0.3, 0.3, 1.5])  # fmt: skip
    hits = [f for f in checks.run_checks(g, Params(), obstacles=obstacles) if f.code == "collision"]
    assert len(hits) == 1 and "runs into" in hits[0].message
