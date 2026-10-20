# SPDX-License-Identifier: GPL-3.0-or-later
"""M1 inside Blender: mesh reading, BVH inside test, operators, overlay state."""

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")

import handycactus  # noqa: E402
from handycactus.blender import mesh_io, overlay, pipeline  # noqa: E402
from handycactus.blender.spatial_bvh import BVHInsideTester  # noqa: E402
from handycactus.core.spatial import NumpyInsideTester  # noqa: E402


@pytest.fixture
def scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    handycactus.register()
    scene = bpy.context.scene
    scene.unit_settings.scale_length = 0.001  # 1 Blender unit = 1 mm
    yield scene
    handycactus.unregister()


def add_box(size, location, name, subdivide=3):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    ob = bpy.context.active_object
    ob.name = name
    if subdivide:
        mod = ob.modifiers.new("subdiv", "SUBSURF")
        mod.subdivision_type = "SIMPLE"
        mod.levels = subdivide
    return ob


def select_only(*objects):
    bpy.ops.object.select_all(action="DESELECT")
    for ob in objects:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def evaluated_target(ob):
    return mesh_io.read_target(
        ob, bpy.context.evaluated_depsgraph_get(), mesh_io.scene_mm_per_bu(bpy.context.scene)
    )


def test_read_target_world_space_mm_with_modifiers(scene):
    ob = add_box(10, (0, 0, 8), "box")
    ob.rotation_euler = (0, 0, 0.3)
    t = evaluated_target(ob)
    assert len(t.verts) > 8  # subdivision applied
    assert t.verts[:, 2].min() == pytest.approx(3.0)
    assert t.verts[:, 2].max() == pytest.approx(13.0)


def test_read_target_respects_scene_scale(scene):
    scene.handycactus.units = "SCENE"
    scene.unit_settings.scale_length = 1.0  # metres: 1 BU = 1000 mm
    ob = add_box(0.01, (0, 0, 0.008), "box", subdivide=0)
    t = evaluated_target(ob)
    assert t.verts[:, 2].min() == pytest.approx(3.0)


def test_mirrored_object_keeps_outward_normals(scene):
    from handycactus.core.mesh_data import combine

    ob = add_box(4, (0, 0, 5), "box", subdivide=0)
    ob.scale = (-1, 1, 1)
    mesh = combine([evaluated_target(ob)])
    outward = np.einsum("ij,ij->i", mesh.face_normals, mesh.face_centroids - [0, 0, 5])
    assert np.all(outward > 0)


def test_vertex_group_weights(scene):
    ob = add_box(4, (0, 0, 5), "box", subdivide=0)
    group = ob.vertex_groups.new(name=mesh_io.FORCE_GROUP)
    group.add([0, 1], 1.0, "REPLACE")
    t = evaluated_target(ob)
    assert t.force_weights is not None and t.force_weights.sum() == pytest.approx(2.0)
    assert t.block_weights is None


def test_bvh_inside_tester_matches_numpy(scene):
    ob = add_box(6, (0, 0, 5), "box", subdivide=2)
    ob.rotation_euler = (0.2, 0.0, 0.5)
    ob.scale = (1.5, -1.0, 0.8)  # mirrored and non-uniform
    bpy.context.view_layer.update()
    t = evaluated_target(ob)
    rng = np.random.default_rng(1)
    pts = rng.uniform(-5, 10, size=(400, 3))
    pts[:50] = t.verts[:50]  # on the surface (ambiguous on purpose)
    bvh = BVHInsideTester.from_arrays(t.verts, t.tris).is_inside(pts[50:])
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_tester = BVHInsideTester.from_object(ob, depsgraph, 1.0, t.verts.min(axis=0), t.verts.max(axis=0))
    assert np.array_equal(obj_tester.is_inside(pts[50:]), bvh)
    ref = NumpyInsideTester(t.verts, t.tris).is_inside(pts[50:])
    assert np.array_equal(bvh, ref)


def test_preview_contacts_on_a_lifted_box(scene):
    ob = add_box(10, (0, 0, 8), "box")
    select_only(ob)
    assert bpy.ops.handycactus.preview_contacts() == {"FINISHED"}
    run = overlay.state.preview
    assert run is not None and run.names == ["box"]
    c = run.result.contacts
    assert len(c) > 20
    assert np.allclose(c.positions[:, 2], 3.0, atol=1e-4)  # Blender stores float32
    assert overlay.state.point_positions.shape == (len(c), 3)
    assert bpy.ops.handycactus.clear_preview() == {"FINISHED"}
    assert overlay.state.preview is None


def test_preview_refuses_a_set_below_the_bed(scene):
    ob = add_box(10, (0, 0, 2), "box")
    select_only(ob)
    with pytest.raises(RuntimeError, match="below the bed"):
        bpy.ops.handycactus.preview_contacts()


def test_overlapping_parts_in_blender(scene):
    body = add_box(10, (5, 0, 5), "body")  # x 0..10, z 0..10
    arm = add_box(4, (12, 0, 6.5), "arm")  # x 10..14 outside, plus overlap below
    arm.scale = (3, 1, 0.75)  # x 6..18, z 5..8
    select_only(body, arm)
    run = pipeline.run_detection(bpy.context, [body, arm])
    c = run.result.contacts
    assert len(c) > 0
    assert set(c.target.tolist()) == {run.names.index("arm")}
    assert c.positions[:, 0].min() > 10.0


def test_place_on_bed_moves_the_set_rigidly(scene):
    a = add_box(10, (0, 0, -1), "a")  # bottom at -6
    b = add_box(2, (20, 0, 4), "b")
    child = add_box(2, (0, 0, 20), "child")
    child.parent = a
    bpy.context.view_layer.update()
    child_z = child.matrix_world.translation.z
    select_only(a, b, child)
    lift = scene.handycactus.lift_height
    assert bpy.ops.handycactus.place_on_bed() == {"FINISHED"}
    bpy.context.view_layer.update()
    mesh = pipeline.read_set(bpy.context, [a, b, child])
    assert mesh.verts[:, 2].min() == pytest.approx(lift)
    assert b.matrix_world.translation.z == pytest.approx(4 + lift + 6)
    # The child moved once, with its parent, not twice.
    assert child.matrix_world.translation.z == pytest.approx(child_z + lift + 6)


def test_heatmap_toggle_builds_and_clears_overlay_data(scene):
    ob = add_box(10, (0, 0, 8), "box")
    select_only(ob)
    scene.handycactus.show_heatmap = True
    assert overlay.state.face_count > 0
    assert overlay.state.draw_handle is not None
    assert overlay.state.heat_positions[:, 2].max() == pytest.approx(3.0, abs=1e-4)  # on the underside
    scene.handycactus.show_heatmap = False
    assert overlay.state.face_count == 0
    assert overlay.state.draw_handle is None


def test_timer_tick_rebuilds_on_parameter_change_and_stops_when_idle(scene):
    ob = add_box(10, (0, 0, 8), "box")
    select_only(ob)
    scene.handycactus.show_heatmap = True
    before = overlay.state.face_count
    scene.handycactus.overhang_angle = 80.0  # only near-horizontal faces still count
    assert overlay._tick() == overlay.TICK_SECONDS
    assert overlay.state.signature[2] == pytest.approx(80.0)
    assert overlay.state.face_count == before  # the flat underside still needs support
    scene.handycactus.show_heatmap = False
    assert overlay._tick() is None  # idle: the timer stops itself without error
    assert overlay.state.draw_handle is None


def test_default_units_are_one_unit_per_mm(scene):
    scene.unit_settings.scale_length = 1.0  # a default metre scene
    assert scene.handycactus.units == "MM"
    ob = add_box(10, (0, 0, 8), "box", subdivide=0)
    t = evaluated_target(ob)
    assert t.verts[:, 2].min() == pytest.approx(3.0)
    assert mesh_io.set_size_mm([ob], scene) == pytest.approx((10, 10, 10))


def test_preview_refuses_a_model_that_is_not_in_mm(scene):
    scene.handycactus.units = "SCENE"
    scene.unit_settings.scale_length = 1.0  # 1 unit = 1 m: a 10-unit box is 10 m
    ob = add_box(10, (0, 0, 8), "box")
    select_only(ob)
    with pytest.raises(RuntimeError, match="Units"):
        bpy.ops.handycactus.preview_contacts()
