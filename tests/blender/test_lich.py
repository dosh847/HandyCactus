# SPDX-License-Identifier: GPL-3.0-or-later
"""A real sculpt: the lich by YKS Miniatures (tests/fixtures/yks-lich-base-54mm), tilted and
floating as its owner prepares it. Decimated, so it has a few pinholes (138 open or
4-face edges of 149k), which used to switch off the inside test: 13% of contacts were
unreachable (250 of 1,891)."""

from pathlib import Path

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
