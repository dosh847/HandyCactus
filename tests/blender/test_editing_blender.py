# SPDX-License-Identifier: GPL-3.0-or-later
"""M4 inside Blender: the edit operators the Edit Supports tool drives, and Regenerate
keeping locked trees. The modal tool runs on a simulated viewport and events."""

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")

import handycactus  # noqa: E402
from handycactus.blender import edit_tool, overlay, pipeline  # noqa: E402
from handycactus.blender.spatial_bvh import first_hit  # noqa: E402
from handycactus.core import graph as G  # noqa: E402


@pytest.fixture
def support():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    handycactus.register()
    bpy.ops.mesh.primitive_cube_add(size=8, location=(0, 0, 14))  # underside at z = 10
    box = bpy.context.active_object
    box.name = "box"
    mod = box.modifiers.new("subdiv", "SUBSURF")
    mod.subdivision_type, mod.levels = "SIMPLE", 3
    bpy.ops.handycactus.generate()
    support = next(ob for ob in bpy.context.scene.objects if ob.handycactus_set.is_support)
    bpy.ops.object.select_all(action="DESELECT")
    support.select_set(True)
    bpy.context.view_layer.objects.active = support
    yield support
    handycactus.unregister()


def graph_of(support):
    return pipeline.read_support_graph(bpy.context, support)


def test_edit_target_resolves_from_the_supports_or_their_target(support):
    assert edit_tool.edit_target(bpy.context) == support
    bpy.context.view_layer.objects.active = bpy.data.objects["box"]
    assert edit_tool.edit_target(bpy.context) == support


def test_add_tip_operator(support):
    before = graph_of(support)
    result = bpy.ops.handycactus.add_tip(location=(3.7, 3.7, 10.0), normal=(0, 0, -1), target="box")
    assert result == {"FINISHED"}
    g = graph_of(support)
    assert g.problems() == []
    assert (g.kind == G.CONTACT).sum() == (before.kind == G.CONTACT).sum() + 1
    info = support.handycactus_set
    assert info.tip_count == (g.kind == G.CONTACT).sum() and info.locked_count >= 1
    # Clicking something that is not a target does nothing.
    assert bpy.ops.handycactus.add_tip(location=(3, 3, 10), target="nope") == {"CANCELLED"}


def test_delete_move_lock_and_rebuild(support):
    g = graph_of(support)
    tip = int(g.tip[g.kind == G.CONTACT][0])
    assert bpy.ops.handycactus.delete_tip(tip=tip) == {"FINISHED"}
    assert not (graph_of(support).tip == tip).any()

    g = graph_of(support)
    node = int(np.flatnonzero(g.kind == G.BRANCH)[0])
    target = g.pos[node] + [0.2, 0.0, 0.0]
    assert bpy.ops.handycactus.move_node(node=node, location=target) == {"FINISHED"}
    assert np.allclose(graph_of(support).pos[node], target, atol=1e-4)

    g = graph_of(support)
    tip = int(g.tip[g.kind == G.CONTACT][-1])
    assert bpy.ops.handycactus.move_tip(tip=tip, location=(-3.6, -3.6, 10.0), target="box") == {"FINISHED"}

    g = graph_of(support)
    foot = int(np.flatnonzero(g.kind == G.FOOT)[0])
    was = bool(g.locked[foot])
    assert bpy.ops.handycactus.toggle_lock(node=foot) == {"FINISHED"}
    assert bool(graph_of(support).locked[foot]) != was

    assert bpy.ops.handycactus.lock_all(locked=False) == {"FINISHED"}
    assert not graph_of(support).locked.any() and support.handycactus_set.locked_count == 0

    g = graph_of(support)
    tip = int(g.tip[g.kind == G.CONTACT][0])
    assert bpy.ops.handycactus.rebuild_tip(tip=tip, contact_diameter=0.5) == {"FINISHED"}
    g = graph_of(support)
    contact = g.kind == G.CONTACT
    assert np.any(np.isclose(g.radius[contact], 0.25, atol=1e-4))
    assert g.problems() == []


def test_regenerate_keeps_locked_trees(support):
    g = graph_of(support)
    foot = int(np.flatnonzero(g.kind == G.FOOT)[0])
    bpy.ops.handycactus.toggle_lock(node=foot)
    g = graph_of(support)
    kept = g.pos[g.locked]
    bpy.context.scene.handycactus.tip_spacing = 2.5  # regenerate differently
    assert bpy.ops.handycactus.generate() == {"FINISHED"}
    after = graph_of(support)
    assert after.problems() == []
    # Every node of the locked tree is still there, unchanged and still locked.
    for p in kept:
        assert np.min(np.linalg.norm(after.pos[after.locked] - p, axis=1)) < 1e-4
    # No new tip sits right on top of a kept one.
    kept_tips = after.pos[(after.kind == G.CONTACT) & after.locked]
    new_tips = after.pos[(after.kind == G.CONTACT) & ~after.locked]
    if len(kept_tips) and len(new_tips):
        gap = np.linalg.norm(new_tips[:, None] - kept_tips[None], axis=2).min()
        assert gap > 0.5 * 2.5 - 0.2


def test_first_hit_reports_the_target(support):
    session = pipeline.EditSession(bpy.context, support)
    hit = first_hit(session.obstacles.trees, (1.0, 1.0, -5.0), (0, 0, 1))
    assert hit is not None
    distance, location, normal, index = hit
    assert location[2] == pytest.approx(10.0, abs=1e-3) and normal[2] < -0.9
    assert session.objects[index].name == "box"


def test_edit_tool_needs_a_3d_viewport(support):
    assert not bpy.ops.handycactus.edit_supports.poll()  # headless: no 3D view area


class _SimulatedTool:
    """The modal tool's methods on a plain object, driven by synthetic events."""


for _name in ("_support", "_reload", "_finish", "_hovered", "_ray", "_feedback", "_undo", "_edit", "modal"):
    setattr(_SimulatedTool, _name, getattr(edit_tool.HANDYCACTUS_OT_edit_supports, _name))


def _camera_below(size=400):
    """A perspective view from 25 units below the box, looking straight up."""
    from types import SimpleNamespace

    from mathutils import Matrix

    cam = Matrix.Translation((0, 0, -15)) @ Matrix.Rotation(np.pi, 4, "X")  # -Z axis looks up
    view = cam.inverted()
    near, far, f = 0.01, 1000.0, 1.0 / np.tan(np.radians(25))
    proj = Matrix(
        (
            (f, 0, 0, 0),
            (0, f, 0, 0),
            (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)),
            (0, 0, -1, 0),
        )
    )
    rv3d = SimpleNamespace(
        view_matrix=view, perspective_matrix=proj @ view, is_perspective=True, view_perspective="PERSP"
    )
    region = SimpleNamespace(width=size, height=size)
    return _FakeContext(
        region=region,
        region_data=rv3d,
        area=SimpleNamespace(tag_redraw=lambda: None),
        workspace=SimpleNamespace(status_text_set=lambda text: None),
    )


class _FakeContext:
    """A 3D-view context: the given view attributes, everything else from bpy.context."""

    def __init__(self, **view):
        self.__dict__.update(view)

    def __getattr__(self, name):
        return getattr(bpy.context, name)


def _event(type_, value="PRESS", at=(0, 0), ctrl=False, shift=False):
    from types import SimpleNamespace

    return SimpleNamespace(
        type=type_,
        value=value,
        ctrl=ctrl,
        shift=shift,
        oskey=False,
        mouse_region_x=at[0],
        mouse_region_y=at[1],
    )


def _pixel(context, point_bu):
    from handycactus.core import picking

    px, _ = picking.project(
        [point_bu],
        np.array(context.region_data.perspective_matrix),
        context.region.width,
        context.region.height,
    )
    return tuple(px[0])


def _start_tool(support):
    ctx = _camera_below()
    tool = _SimulatedTool()
    tool.session = pipeline.EditSession(bpy.context, support)
    pipeline.set_active_session(tool.session)
    tool.support_name, tool.hover, tool.drag = support.name, -1, None
    tool._reload(ctx)
    return ctx, tool


def test_modal_tool_add_drag_lock_delete(support):
    ctx, tool = _start_tool(support)
    tips_before = int((tool.graph.kind == G.CONTACT).sum())

    # Click an empty spot of the underside (no support node under the cursor): a tip is added.
    for xy in np.ndindex(9, 9):
        spot = _pixel(ctx, (xy[0] - 3.9, xy[1] - 3.9, 10.0))
        tool.modal(ctx, _event("MOUSEMOVE", "NOTHING", spot))
        if tool.hover == -1:
            break
    assert tool.hover == -1
    tool.modal(ctx, _event("LEFTMOUSE", "PRESS", spot))
    assert int((tool.graph.kind == G.CONTACT).sum()) == tips_before + 1

    # Drag a branch node sideways.
    node = int(np.flatnonzero(tool.graph.kind == G.BRANCH)[0])
    before = tool.graph.pos[node].copy()
    start = _pixel(ctx, before)
    tool.modal(ctx, _event("MOUSEMOVE", "NOTHING", start))
    assert tool.hover == node
    tool.modal(ctx, _event("LEFTMOUSE", "PRESS", start))
    assert tool.drag == ("node", node)
    tool.modal(ctx, _event("LEFTMOUSE", "RELEASE", (start[0] + 6, start[1])))
    stored = graph_of(support).pos[node]
    assert np.linalg.norm(stored - before) > 0.05 and stored[2] == pytest.approx(before[2], abs=1e-3)
    assert tool.graph.locked[node]
    assert tool.drag is None

    # L toggles the lock of the hovered tree.
    contact = int(np.flatnonzero(tool.graph.kind == G.CONTACT)[0])
    at = _pixel(ctx, tool.graph.pos[contact])
    tool.modal(ctx, _event("MOUSEMOVE", "NOTHING", at))
    was = bool(tool.graph.locked[contact])
    tool.modal(ctx, _event("L", "PRESS", at))
    assert bool(tool.graph.locked[contact]) != was

    # Ctrl+click a tip deletes it.
    tip = int(tool.graph.tip[contact])
    tool.modal(ctx, _event("MOUSEMOVE", "NOTHING", at))
    tool.modal(ctx, _event("LEFTMOUSE", "PRESS", at, ctrl=True))
    assert not (tool.graph.tip == tip).any()

    # Esc ends the tool.
    assert tool.modal(ctx, _event("ESC", "PRESS", at)) == {"FINISHED"}
    assert pipeline._active_session is None


def test_modal_tool_undo_and_redo(support):
    bpy.ops.ed.undo_push(message="Generate")
    ctx, tool = _start_tool(support)
    start = graph_of(support)
    tip = int(start.tip[start.kind == G.CONTACT][0])
    at = _pixel(ctx, start.pos[(start.tip == tip) & (start.kind == G.CONTACT)][0])
    tool.modal(ctx, _event("MOUSEMOVE", "NOTHING", at))
    tool.modal(ctx, _event("LEFTMOUSE", "PRESS", at, ctrl=True))
    deleted = graph_of(support)
    assert not (deleted.tip == tip).any()

    overlay.state.geometry_dirty = False
    tool.modal(ctx, _event("Z", "PRESS", at, ctrl=True))
    assert overlay.state.geometry_dirty  # the undo handler asks the overlay to redraw from the scene
    support = bpy.data.objects[tool.support_name]  # undo reloads the data
    assert (graph_of(support).tip == tip).any() and (tool.graph.tip == tip).any()
    assert len(tool.graph) == len(start)

    tool.modal(ctx, _event("Z", "PRESS", at, ctrl=True, shift=True))
    support = bpy.data.objects[tool.support_name]
    assert not (graph_of(support).tip == tip).any() and not (tool.graph.tip == tip).any()
    tool.modal(ctx, _event("ESC", "PRESS", at))
