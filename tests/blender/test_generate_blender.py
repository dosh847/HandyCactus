# SPDX-License-Identifier: GPL-3.0-or-later
"""M2 inside Blender: Generate / Clear, graph I/O, the Geometry Nodes build."""

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")

import handycactus  # noqa: E402
from handycactus.blender import graph_io, overlay, pipeline  # noqa: E402
from handycactus.core import graph as G  # noqa: E402
from handycactus.core import heatmap  # noqa: E402
from handycactus.core.params import Params  # noqa: E402


@pytest.fixture
def scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    handycactus.register()
    yield bpy.context.scene  # default units: 1 Blender unit = 1 mm
    handycactus.unregister()


def add_box(size, location, name, scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = scale
    mod = ob.modifiers.new("subdiv", "SUBSURF")
    mod.subdivision_type, mod.levels = "SIMPLE", 3
    return ob


def select_only(*objects):
    bpy.ops.object.select_all(action="DESELECT")
    for ob in objects:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def supports_in_scene():
    return [ob for ob in bpy.context.scene.objects if ob.handycactus_set.is_support]


def evaluated_verts(ob):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = ob.evaluated_get(depsgraph).to_mesh()
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    ob.evaluated_get(depsgraph).to_mesh_clear()
    return co.reshape(-1, 3)


def test_generate_creates_a_valid_support_set(scene):
    box = add_box(8, (0, 0, 14), "box")  # underside at z = 10
    select_only(box)
    assert bpy.ops.handycactus.generate() == {"FINISHED"}
    (support,) = supports_in_scene()
    assert support.name == "box_HC_Supports"
    info = support.handycactus_set
    assert [t.name for t in info.target_objects()] == ["box"]
    assert info.tip_count > 20 and info.tree_count > 0 and info.unreachable_count == 0
    stored, expected = info.params().as_dict(), Params().as_dict()
    assert stored == pytest.approx(expected, rel=1e-6)  # scene properties are float32

    g = pipeline.read_support_graph(bpy.context, support)
    assert g.problems() == []
    assert (g.kind == G.CONTACT).sum() == info.tip_count

    # The Geometry Nodes result: solid geometry from the bed up to the contacts.
    v = evaluated_verts(support)
    assert len(v) > 1000
    assert v[:, 2].min() == pytest.approx(0.0, abs=1e-3)
    assert 10.0 < v[:, 2].max() < 10.0 + Params().penetration + 0.2


def test_feet_have_the_foot_radius(scene):
    box = add_box(8, (0, 0, 14), "box")
    select_only(box)
    bpy.ops.handycactus.generate()
    (support,) = supports_in_scene()
    g = pipeline.read_support_graph(bpy.context, support)
    v = evaluated_verts(support)
    foot = g.pos[np.flatnonzero(g.kind == G.FOOT)[0]]
    on_bed = v[np.abs(v[:, 2]) < 1e-3]
    d = np.linalg.norm(on_bed[:, :2] - foot[:2], axis=1)
    r = d[d < 1.2 * Params().foot_radius].max()  # this foot's rim (neighbours lie farther out)
    assert r == pytest.approx(Params().foot_radius, rel=0.05)


def test_generate_again_rebuilds_the_same_object(scene):
    box = add_box(8, (0, 0, 14), "box")
    select_only(box)
    bpy.ops.handycactus.generate()
    scene.handycactus.tip_spacing = 3.0
    select_only(box)
    bpy.ops.handycactus.generate()
    (support,) = supports_in_scene()
    assert support.handycactus_set.params().tip_spacing == 3.0
    # Selecting only the supports object rebuilds it from its own targets.
    select_only(support)
    assert bpy.ops.handycactus.generate() == {"FINISHED"}
    assert len(supports_in_scene()) == 1


def test_clear_supports(scene):
    box = add_box(8, (0, 0, 14), "box")
    select_only(box)
    bpy.ops.handycactus.generate()
    (support,) = supports_in_scene()
    mesh_name = support.data.name
    select_only(support)
    assert bpy.ops.handycactus.clear_supports() == {"FINISHED"}
    assert supports_in_scene() == []
    assert mesh_name not in bpy.data.meshes


def test_multi_part_set(scene):
    body = add_box(10, (5, 0, 10), "body")  # x 0..10, z 5..15
    arm = add_box(4, (12, 0, 11.5), "arm", scale=(3, 1, 0.75))  # x 6..18, z 10..13, enters the body
    select_only(body, arm)
    assert bpy.ops.handycactus.generate() == {"FINISHED"}
    (support,) = supports_in_scene()
    assert {t.name for t in support.handycactus_set.target_objects()} == {"body", "arm"}
    g = pipeline.read_support_graph(bpy.context, support)
    assert g.problems() == []
    assert set(g.target[g.kind == G.CONTACT].tolist()) == {0, 1}
    # Nothing inside the body (x < 10 at the arm's underside height).
    arm_contacts = g.pos[(g.kind == G.CONTACT) & (g.target == 0)]
    assert arm_contacts[:, 0].min() > 10.0


def test_unreachable_contacts_are_marked(scene):
    plate = add_box(12, (0, 0, 0.5), "plate", scale=(1, 1, 1 / 12))  # 12 x 12 x 1, on the bed
    post = add_box(2, (0, 0, 3.5), "post", scale=(1, 1, 2.5))  # z 1..6
    canopy = add_box(8, (0, 0, 6.5), "canopy", scale=(1, 1, 1 / 8))  # z 6..7, over the plate
    scene.handycactus.land_on_model = False
    select_only(plate, post, canopy)
    bpy.ops.handycactus.generate()
    (support,) = supports_in_scene()
    assert support.handycactus_set.unreachable_count > 0
    assert overlay.state.marker_count > 0


def test_generate_refuses_below_the_bed(scene):
    box = add_box(8, (0, 0, 2), "box")
    select_only(box)
    with pytest.raises(RuntimeError, match="below the bed"):
        bpy.ops.handycactus.generate()


def test_graph_round_trip(scene):
    b = G.GraphBuilder()
    foot = b.add((1, 2, 0), G.FOOT, 1.5)
    k = b.add((1, 2, 5), G.KNUCKLE, 0.2, parent=foot, tip=0, target=0, direction=(0, 0, -1), sphere=0.3)
    b.add((1, 2, 6.65), G.CONTACT, 0.15, parent=k, tip=0, target=0, direction=(0, 0, -1))
    g = b.build()
    mesh = bpy.data.meshes.new("rt")
    graph_io.write_graph(mesh, g, mm_per_bu=0.5)
    back = graph_io.read_graph(mesh, mm_per_bu=0.5)
    for field in ("pos", "radius", "sphere", "dir"):
        assert np.allclose(getattr(back, field), getattr(g, field), atol=1e-5), field
    for field in ("kind", "parent", "tree", "tip", "target", "locked", "flags"):
        assert np.array_equal(getattr(back, field), getattr(g, field)), field
    assert back.problems() == []


def test_generate_runs_checks_and_shows_markers(scene):
    box = add_box(8, (0, 0, 14), "box")
    select_only(box)
    bpy.ops.handycactus.generate()
    (support,) = supports_in_scene()
    info = support.handycactus_set
    assert info.checked
    errors, _ = info.counts()
    assert errors == 0, [f.message for f in info.findings]


def test_check_and_frame_unreachable_findings(scene):
    plate = add_box(12, (0, 0, 0.5), "plate", scale=(1, 1, 1 / 12))
    post = add_box(2, (0, 0, 3.5), "post", scale=(1, 1, 2.5))
    canopy = add_box(8, (0, 0, 6.5), "canopy", scale=(1, 1, 1 / 8))
    scene.handycactus.land_on_model = False
    select_only(plate, post, canopy)
    bpy.ops.handycactus.generate()
    (support,) = supports_in_scene()
    select_only(support)
    assert bpy.ops.handycactus.check_supports() == {"FINISHED"}
    info = support.handycactus_set
    unreachable = [f for f in info.findings if f.code == "unreachable"]
    assert len(unreachable) == info.unreachable_count > 0
    assert overlay.state.marker_count == len(info.findings)
    # Errors are listed first; framing one puts the cursor on it.
    assert info.findings[0].severity == "ERROR"
    assert bpy.ops.handycactus.frame_finding(index=0) == {"FINISHED"}
    cursor = tuple(bpy.context.scene.cursor.location)
    assert cursor == pytest.approx(tuple(info.findings[0].location), abs=1e-4)
    select_only(support)
    bpy.ops.handycactus.clear_supports()
    assert overlay.state.marker_count == 0


def test_trapped_branches_land_on_the_model(scene):
    plate = add_box(12, (0, 0, 0.5), "plate", scale=(1, 1, 1 / 12))
    post = add_box(2, (0, 0, 3.5), "post", scale=(1, 1, 2.5))
    canopy = add_box(8, (0, 0, 6.5), "canopy", scale=(1, 1, 1 / 8))
    select_only(plate, post, canopy)
    bpy.ops.handycactus.generate()
    (support,) = supports_in_scene()
    info = support.handycactus_set
    assert info.unreachable_count == 0
    assert info.landing_count > 0
    landings = [f for f in info.findings if f.code == "landing"]
    assert len(landings) == info.landing_count and all(f.severity == "INFO" for f in landings)
    assert info.counts() == (0, 0)
    g = pipeline.read_support_graph(bpy.context, support)
    assert g.problems() == []
    # The plate covers the bed under the canopy: every branch lands on the plate's top (z = 1),
    # its tip sunk `penetration` into it.
    v = evaluated_verts(support)
    assert v[:, 2].min() == pytest.approx(1.0 - Params().penetration, abs=0.05)


def test_markers_follow_the_selection_and_toggle(scene):
    plate = add_box(12, (0, 0, 0.5), "plate", scale=(1, 1, 1 / 12))
    post = add_box(2, (0, 0, 3.5), "post", scale=(1, 1, 2.5))
    canopy = add_box(8, (0, 0, 6.5), "canopy", scale=(1, 1, 1 / 8))
    scene.handycactus.land_on_model = False
    select_only(plate, post, canopy)
    bpy.ops.handycactus.generate()
    (support,) = supports_in_scene()
    shown = len(support.handycactus_set.findings)
    assert overlay.state.marker_count == shown > 0  # targets selected: their set's markers
    bpy.ops.object.select_all(action="DESELECT")
    overlay._tick()
    assert overlay.state.marker_count == 0  # nothing selected: nothing shown
    select_only(support)
    overlay._tick()
    assert overlay.state.marker_count == shown
    scene.handycactus.show_markers = False
    assert overlay.state.marker_count == 0
    scene.handycactus.show_markers = True
    assert overlay.state.marker_count == shown


def test_support_tips_colour_the_heatmap(scene):
    box = add_box(8, (0, 0, 14), "box")
    select_only(box)
    bpy.ops.handycactus.generate()
    (support,) = supports_in_scene()
    select_only(support)  # the set's targets get the heatmap, coloured by its tips
    scene.handycactus.show_heatmap = True
    colors = overlay.state.heat_colors[::3]
    covered = np.all(np.isclose(colors, heatmap.COVERED), axis=1)
    assert overlay.state.face_count > 0 and covered.mean() > 0.95
    assert overlay.state.gap_face_count == (~covered).sum()

    # Without tips the underside is uncovered (the tips cover only what is above them).
    bpy.ops.handycactus.lock_all(locked=False)
    graph = pipeline.read_support_graph(bpy.context, support)
    tip = int(graph.tip[graph.kind == G.CONTACT][0])
    for t in np.unique(graph.tip[graph.kind == G.CONTACT]):
        bpy.ops.handycactus.delete_tip(tip=int(t))
    overlay._tick()
    assert tip >= 0 and overlay.state.gap_face_count == overlay.state.face_count
    scene.handycactus.show_heatmap = False
