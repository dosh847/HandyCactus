# SPDX-License-Identifier: GPL-3.0-or-later
"""A real sculpt: the lich by YKS Miniatures (tests/fixtures/yks-lich-base-54mm), tilted and
floating as its owner prepares it. Decimated, so it has a few pinholes (138 open or
4-face edges of 149k), which used to switch off the inside test: 13% of contacts were
unreachable (250 of 1,891)."""

from pathlib import Path

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")

import handycactus  # noqa: E402
from handycactus.blender import pipeline  # noqa: E402

FIXTURE = Path(__file__).parent.parent / "fixtures" / "yks-lich-base-54mm" / "lich-base-54mm_reduced.stl"


@pytest.fixture(scope="module")
def lich():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    handycactus.register()
    bpy.ops.wm.stl_import(filepath=str(FIXTURE))
    ob = bpy.context.selected_objects[0]
    bpy.context.view_layer.update()
    yield ob
    handycactus.unregister()


def test_lich_contacts_reach(lich):
    mesh = pipeline.read_set(bpy.context, [lich])
    assert mesh.non_manifold_edge_count(0) > 0 and mesh.is_nearly_closed(0)
    result = pipeline.generate(bpy.context, [lich])
    total = result.tips + result.unreachable
    assert result.unreachable <= 0.04 * total, (result.unreachable, total)
    # Bambu-like trees (v0.9): trapped branches merge, free ones steer around the model by
    # the avoidance grid. Was 486 landings and 64,600 mm³ of support.
    info = result.support.handycactus_set
    assert info.landing_count <= 380, info.landing_count
    g = pipeline.read_support_graph(bpy.context, result.support)
    e = g.parent >= 0
    length = np.linalg.norm(g.pos[e] - g.pos[g.parent[e]], axis=1)
    r = (g.radius[e] + g.radius[g.parent[e]]) / 2
    volume = float((np.pi * r**2 * length).sum())
    assert volume < 40000, volume


def test_lich_supports_stay_above_the_bed(lich):
    from handycactus.blender import export_ops

    pipeline.generate(bpy.context, [lich])
    support = next(ob for ob in bpy.context.scene.objects if ob.handycactus_set.is_support)
    graph = pipeline.read_support_graph(bpy.context, support)
    # No junction ball below the bed (a merge right above a foot had a 2 mm ball at 0.5 mm).
    assert (graph.pos[:, 2] - graph.sphere).min() >= -1e-6
    parts = export_ops.collect_parts(bpy.context, support, riser=True)
    sup = parts[-1]
    assert sup.verts[:, 2].min() == pytest.approx(0.0, abs=1e-4)  # flat on the bed, never under it
    assert parts[0].verts[:, 2].min() == pytest.approx(0.0, abs=1e-4)  # the riser reaches the bed
