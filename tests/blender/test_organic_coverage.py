# SPDX-License-Identifier: GPL-3.0-or-later
"""Coverage on a sculpt-like model (bumpy body, layered feathers, tilted 45°).

Regression for M2 field testing: on organic minis the old bridging rule
dropped ~90% of overhang samples and left most of the overhang uncovered.
"""

import math

import pytest

bpy = pytest.importorskip("bpy")

import handycactus  # noqa: E402
from handycactus.blender import pipeline  # noqa: E402
from handycactus.blender.props import settings_to_params  # noqa: E402
from handycactus.core import heatmap  # noqa: E402


@pytest.fixture(scope="module")
def organic():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    handycactus.register()
    tex = bpy.data.textures.new("noise", "CLOUDS")
    tex.noise_scale = 0.35
    parts = []
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=9)
    body = bpy.context.active_object
    body.scale = (1, 0.8, 1.6)
    parts.append(body)
    for side in (-1, 1):
        for i in range(4):
            bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=20, radius=1)
            f = bpy.context.active_object
            f.scale = (7 + i, 1.6, 0.35)
            f.location = (side * (9 + i * 1.6), 0, 6 - i * 2.2)
            f.rotation_euler = (0.15 * side, -0.35 * side, 0)
            parts.append(f)
    for ob, strength in zip(parts, [1.2] + [0.25] * (len(parts) - 1), strict=True):
        mod = ob.modifiers.new("displace", "DISPLACE")
        mod.texture, mod.strength = tex, strength
        ob.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.convert(target="MESH")
    bpy.ops.object.join()
    mini = bpy.context.active_object
    mini.rotation_euler = (math.radians(45), 0, 0)
    bpy.context.view_layer.update()
    pipeline.place_on_bed(bpy.context, [mini], 3.0)
    bpy.context.view_layer.update()
    yield mini
    handycactus.unregister()


def test_overhangs_are_covered(organic):
    run = pipeline.run_detection(bpy.context, [organic])
    stats = run.result.stats
    # Bridging keeps most samples (it dropped ~90% before the fix).
    assert stats["samples_after_bridging"] >= 0.5 * stats["samples"], stats
    params = settings_to_params(bpy.context.scene.handycactus)
    h = heatmap.build(run.mesh, params, run.result.contacts.positions, run.result.support_faces)
    assert h.gap_face_count <= 0.12 * h.face_count, (h.gap_face_count, h.face_count)


def test_most_contacts_reach_the_bed(organic):
    result = pipeline.generate(bpy.context, [organic])
    # 21% of contacts got stuck here at M2; buried-shell detection and landing on the
    # model (M4) bring it to ~0.
    assert result.unreachable <= 0.03 * (result.tips + result.unreachable), (result.unreachable, result.tips)
