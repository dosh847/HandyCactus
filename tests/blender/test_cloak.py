# SPDX-License-Identifier: GPL-3.0-or-later
"""A cloaked figure: body, arms and an axe, rib bars, hanging straps, and a flared
cape with deep folds and fine wrinkles (sculpt-like detail).

Regression for the v0.6 field report (441 of 830 contacts unreachable on a cloaked
mini): on this model 14% of contacts were unreachable, almost all island contacts on
the rims of tiny wrinkle patches, where no tip fits.
"""

import math

import pytest

bpy = pytest.importorskip("bpy")
bmesh = pytest.importorskip("bmesh")

import handycactus  # noqa: E402
from handycactus.blender import pipeline  # noqa: E402


@pytest.fixture(scope="module")
def cloaked():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    handycactus.register()
    tex = bpy.data.textures.new("noise", "CLOUDS")
    tex.noise_scale = 0.4
    fine = bpy.data.textures.new("fine", "CLOUDS")
    fine.noise_scale = 0.12
    fine.noise_depth = 3

    def wrinkles(ob, s):
        m = ob.modifiers.new("w", "DISPLACE")
        m.texture, m.strength = fine, s

    parts = []

    def disp(ob, s):
        m = ob.modifiers.new("d", "DISPLACE")
        m.texture, m.strength = tex, s

    # body and head
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1, location=(0, 0, 20))
    b = bpy.context.active_object
    b.scale = (5, 4, 11)
    disp(b, 0.8)
    wrinkles(b, 0.35)
    parts.append(b)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=3.2, location=(0, -1, 34))
    h = bpy.context.active_object
    h.scale = (1, 1.2, 1)
    disp(h, 0.5)
    parts.append(h)
    # jaw / brow overhang
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1, location=(0, -3.6, 32.5))
    j = bpy.context.active_object
    j.scale = (2.2, 1.4, 0.8)
    parts.append(j)
    # legs
    for x in (-2.2, 2.2):
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=1.8, depth=11, location=(x, 0, 8.5))
        parts.append(bpy.context.active_object)
    # arms forward + weapon
    for x in (-5.5, 5.5):
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=24, radius=1.4, depth=9, location=(x, -4, 25), rotation=(math.radians(80), 0, 0)
        )
        a = bpy.context.active_object
        disp(a, 0.3)
        parts.append(a)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(5.5, -9, 22))
    w = bpy.context.active_object
    w.scale = (0.8, 0.8, 14)
    parts.append(w)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(5.5, -9, 30))
    ax = bpy.context.active_object
    ax.scale = (0.6, 7, 4)
    parts.append(ax)
    # cape: flared folded sheet over the back, 220 degrees
    bm = bmesh.new()
    rings, segs = 120, 360
    verts = []
    for i in range(rings + 1):
        t = i / rings
        z = 30 - 24 * t
        r = 6 + 5.5 * t
        row = []
        for k in range(segs + 1):
            phi = math.radians(-20 + 220 * k / segs)
            rr = r + (0.6 + 1.6 * t) * math.sin(22 * phi) + 0.3
            row.append(bm.verts.new((rr * math.cos(phi), rr * math.sin(phi) + 0.5, z)))
        verts.append(row)
    for i in range(rings):
        for k in range(segs):
            bm.faces.new((verts[i][k], verts[i][k + 1], verts[i + 1][k + 1], verts[i + 1][k]))
    me = bpy.data.meshes.new("cape")
    bm.to_mesh(me)
    cape = bpy.data.objects.new("cape", me)
    bpy.context.scene.collection.objects.link(cape)
    sol = cape.modifiers.new("s", "SOLIDIFY")
    sol.thickness = 0.8
    sol.use_even_offset = True
    disp(cape, 0.4)
    wrinkles(cape, 0.5)
    parts.append(cape)
    # ribs / bones: thin bars across the chest with gaps between
    for i in range(6):
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=16,
            radius=0.45,
            depth=9,
            location=(0, -4.3, 15 + 1.6 * i),
            rotation=(0, math.radians(90), 0),
        )
        parts.append(bpy.context.active_object)
    # belt pouches and a skirt of hanging straps
    for k in range(9):
        phi = math.radians(200 + 16 * k)
        bpy.ops.mesh.primitive_cube_add(size=1, location=(5.6 * math.cos(phi), 4.6 * math.sin(phi), 11))
        s_ = bpy.context.active_object
        s_.scale = (1.4, 0.5, 4.5)
        s_.rotation_euler = (0.25, 0, phi)
        parts.append(s_)
    for ob in parts:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = b
    bpy.ops.object.convert(target="MESH")
    bpy.ops.object.join()
    mini = bpy.context.active_object
    mini.name = "cloaked"
    mini.rotation_euler = (math.radians(8), 0, 0)
    bpy.context.view_layer.update()
    pipeline.place_on_bed(bpy.context, [mini], 3.0)
    bpy.context.view_layer.update()
    yield mini
    handycactus.unregister()


def test_cloaked_figure_contacts_reach(cloaked):
    result = pipeline.generate(bpy.context, [cloaked])
    total = result.tips + result.unreachable
    assert result.unreachable <= 0.03 * total, (result.unreachable, total)
    stats = result.support.handycactus_set
    assert stats.tip_count > 150  # still well supported
