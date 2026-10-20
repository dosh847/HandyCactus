# SPDX-License-Identifier: GPL-3.0-or-later
"""Manual editing (spec section 7): one operator per edit, and the modal Edit Supports
tool that drives them from the mouse.

Every edit locks the tree it touches, so Regenerate keeps it, and is one undo step.
"""

from __future__ import annotations

import bpy
import numpy as np
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras import view3d_utils

from ..core import editing, picking
from ..core.graph import BRANCH, CONTACT, JUNCTION, KNUCKLE
from . import overlay, pipeline
from .props import is_support
from .spatial_bvh import first_hit


def edit_target(context):
    """The support set to edit: the active supports object, or the set whose targets
    include the active object, or the only set in the scene."""
    active = context.active_object
    if is_support(active):
        return active
    sets = [ob for ob in context.scene.objects if is_support(ob)]
    if active is not None:
        for ob in sets:
            if active.name in {t.name for t in ob.handycactus_set.target_objects()}:
                return ob
    return sets[0] if len(sets) == 1 else None


class _EditOperator:
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return edit_target(context) is not None

    def run(self, context, edit) -> set:
        support = edit_target(context)
        try:
            result = pipeline.apply_edit(context, support, edit)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not result.ok:
            self.report({"WARNING"}, result.message)
            return {"CANCELLED"}
        overlay.refresh()
        self.report({"INFO"}, result.message)
        return {"FINISHED"}


def _target_index(session, name: str) -> int:
    obj = bpy.data.objects.get(name) if name else None
    return session.target_index(obj)


class HANDYCACTUS_OT_add_tip(_EditOperator, Operator):
    """Add a support tip at a point on a target (world location and surface normal)"""

    bl_idname = "handycactus.add_tip"
    bl_label = "Add Tip"

    location: FloatVectorProperty(size=3, subtype="XYZ")
    normal: FloatVectorProperty(size=3, default=(0.0, 0.0, -1.0))
    target: StringProperty(description="Target object the point lies on")

    def execute(self, context):
        def edit(graph, s):
            index = _target_index(s, self.target)
            if index < 0:
                return editing.EditResult(graph, False, "Click on one of this support set's targets")
            point = np.array(self.location) * s.scale
            return editing.add_tip(graph, point, np.array(self.normal), index, s.params, s.obstacles)

        return self.run(context, edit)


class HANDYCACTUS_OT_delete_tip(_EditOperator, Operator):
    """Delete a support tip and prune its branch"""

    bl_idname = "handycactus.delete_tip"
    bl_label = "Delete Tip"

    tip: IntProperty(default=-1)

    def execute(self, context):
        return self.run(context, lambda graph, s: editing.delete_tip(graph, self.tip))


class HANDYCACTUS_OT_move_tip(_EditOperator, Operator):
    """Move a support tip to another point on a target, re-routing its branch"""

    bl_idname = "handycactus.move_tip"
    bl_label = "Move Tip"

    tip: IntProperty(default=-1)
    location: FloatVectorProperty(size=3, subtype="XYZ")
    normal: FloatVectorProperty(size=3, default=(0.0, 0.0, -1.0))
    target: StringProperty()

    def execute(self, context):
        def edit(graph, s):
            index = _target_index(s, self.target)
            if index < 0:
                return editing.EditResult(graph, False, "Drop the tip on one of this support set's targets")
            point = np.array(self.location) * s.scale
            return editing.move_tip(
                graph, self.tip, point, np.array(self.normal), index, s.params, s.obstacles
            )

        return self.run(context, edit)


class HANDYCACTUS_OT_move_node(_EditOperator, Operator):
    """Move a branch or junction node"""

    bl_idname = "handycactus.move_node"
    bl_label = "Move Node"

    node: IntProperty(default=-1)
    location: FloatVectorProperty(size=3, subtype="XYZ")

    def execute(self, context):
        return self.run(
            context, lambda graph, s: editing.move_node(graph, self.node, np.array(self.location) * s.scale)
        )


class HANDYCACTUS_OT_toggle_lock(_EditOperator, Operator):
    """Lock or unlock the tree containing a node (locked trees survive Regenerate)"""

    bl_idname = "handycactus.toggle_lock"
    bl_label = "Toggle Lock"

    node: IntProperty(default=-1)

    def execute(self, context):
        def edit(graph, s):
            if not 0 <= self.node < len(graph):
                return editing.EditResult(graph, False, "No such node")
            locked = not bool(graph.locked[self.node])
            g = editing.set_tree_locked(graph, self.node, locked)
            return editing.EditResult(g, True, "Tree locked" if locked else "Tree unlocked")

        return self.run(context, edit)


class HANDYCACTUS_OT_lock_all(_EditOperator, Operator):
    """Lock or unlock every tree of the support set"""

    bl_idname = "handycactus.lock_all"
    bl_label = "Lock All"

    locked: BoolProperty(default=True)

    def execute(self, context):
        def edit(graph, s):
            g = graph.subset(np.ones(len(graph), bool))
            g.locked[:] = self.locked
            return editing.EditResult(g, True, "All trees locked" if self.locked else "All trees unlocked")

        return self.run(context, edit)


class HANDYCACTUS_OT_rebuild_tip(_EditOperator, Operator):
    """Rebuild one tip with its own settings (per-support override)"""

    bl_idname = "handycactus.rebuild_tip"
    bl_label = "Tip Settings"

    tip: IntProperty(default=-1)
    contact_diameter: FloatProperty(name="Contact Diameter (mm)", min=0.1, max=1.5, default=0.3)
    tip_length: FloatProperty(name="Tip Length (mm)", min=0.4, max=6.0, default=1.5)
    penetration: FloatProperty(name="Penetration (mm)", min=0.0, max=0.6, default=0.15)
    knuckle_diameter: FloatProperty(name="Knuckle Diameter (mm)", min=0.3, max=3.0, default=0.6)
    max_tip_angle: FloatProperty(name="Max Tip Angle (°)", min=0.0, max=80.0, default=45.0)

    def invoke(self, context, event):
        params = edit_target(context).handycactus_set.params()
        if params is not None:
            for name in editing.TIP_OVERRIDES:
                setattr(self, name, getattr(params, name))
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        overrides = {name: getattr(self, name) for name in editing.TIP_OVERRIDES}
        return self.run(
            context, lambda graph, s: editing.rebuild_tip(graph, self.tip, overrides, s.params, s.obstacles)
        )


# --- the modal tool -------------------------------------------------------------------

PICKABLE = (CONTACT, KNUCKLE, BRANCH, JUNCTION)
HELP = (
    "Click model: add tip  |  Drag tip: move  |  Drag branch node: move  |  "
    "Ctrl+click tip: delete  |  L: lock/unlock tree  |  T: tip settings  |  "
    "Ctrl+Z / Ctrl+Shift+Z: undo / redo  |  Esc/Right-click: done"
)


class HANDYCACTUS_OT_edit_supports(Operator):
    """Edit supports with the mouse: add, delete and drag tips, drag branch nodes, lock trees"""

    bl_idname = "handycactus.edit_supports"
    bl_label = "Edit Supports"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None and context.area.type == "VIEW_3D" and edit_target(context) is not None
        )

    def invoke(self, context, event):
        support = edit_target(context)
        try:
            self.session = pipeline.EditSession(context, support)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        pipeline.set_active_session(self.session)
        self.support_name = support.name
        self.hover = -1
        self.drag = None  # ("tip" | "node", node index)
        self._reload(context)
        context.workspace.status_text_set(HELP)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _support(self):
        return bpy.data.objects.get(self.support_name)

    def _reload(self, context):
        self.graph = pipeline.read_support_graph(context, self._support())
        self.pickable = np.flatnonzero(np.isin(self.graph.kind, PICKABLE))

    def _finish(self, context):
        pipeline.set_active_session(None)
        overlay.set_edit_feedback(None, None)
        context.workspace.status_text_set(None)
        return {"FINISHED"}

    def _hovered(self, context, event) -> int:
        region, rv3d = context.region, context.region_data
        bu = self.graph.pos[self.pickable] / self.session.scale
        px, visible = picking.project(bu, np.array(rv3d.perspective_matrix), region.width, region.height)
        i = picking.pick(px, visible, (event.mouse_region_x, event.mouse_region_y))
        return int(self.pickable[i]) if i >= 0 else -1

    def _ray(self, context, event):
        """Hit on the targets under the mouse: (location BU, normal, target name) or None."""
        region, rv3d = context.region, context.region_data
        mouse = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse)
        trees = self.session.obstacles.trees  # world mm
        hit = first_hit(trees, np.array(origin) * self.session.scale, np.array(direction))
        if hit is None:
            return None
        _, location, normal, i = hit
        return location / self.session.scale, normal, self.session.objects[i].name

    def _feedback(self):
        hover = self.graph.pos[self.hover] if self.hover >= 0 else None
        overlay.set_edit_feedback(hover, self.graph.pos[self.drag[1]] if self.drag else None)

    def _undo(self, context, redo: bool):
        """Step Blender's undo stack from inside the tool. Every edit pushed one step, so
        this undoes one edit at a time; undoing past the tool's start is allowed (undoing
        Generate removes the supports and ends the tool)."""
        op = bpy.ops.ed.redo if redo else bpy.ops.ed.undo
        if op.poll():
            op()
        self.drag = None
        if self._support() is not None:
            self._reload(context)
        self.hover = -1
        self._feedback()

    def _edit(self, context, op, message, **kwargs):
        result = op(**kwargs)
        if "FINISHED" in result:
            bpy.ops.ed.undo_push(message=message)
        self._reload(context)
        self.hover = -1
        self._feedback()

    def modal(self, context, event):
        if self._support() is None:
            return self._finish(context)
        context.area.tag_redraw()
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM"}:
            return {"PASS_THROUGH"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            return self._finish(context)

        if event.type == "Z" and event.value == "PRESS" and (event.ctrl or event.oskey):
            self._undo(context, redo=event.shift)
            if self._support() is None:
                return self._finish(context)
            return {"RUNNING_MODAL"}
        if event.type == "Y" and event.value == "PRESS" and event.ctrl:
            self._undo(context, redo=True)
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            if self.drag is None:
                self.hover = self._hovered(context, event)
            self._feedback()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            node = self._hovered(context, event)
            kind = self.graph.kind[node] if node >= 0 else -1
            if event.ctrl:
                if kind in (CONTACT, KNUCKLE):
                    tip = int(self.graph.tip[node])
                    self._edit(context, bpy.ops.handycactus.delete_tip, "Delete Tip", tip=tip)
            elif kind in (CONTACT, KNUCKLE):
                self.drag = ("tip", node)
            elif kind in (BRANCH, JUNCTION):
                self.drag = ("node", node)
            else:
                hit = self._ray(context, event)
                if hit is not None:
                    location, normal, name = hit
                    self._edit(
                        context, bpy.ops.handycactus.add_tip, "Add Tip",
                        location=location, normal=normal, target=name,
                    )  # fmt: skip
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self.drag is not None:
            what, node = self.drag
            self.drag = None
            if what == "tip":
                hit = self._ray(context, event)
                if hit is not None:
                    location, normal, name = hit
                    tip = int(self.graph.tip[node])
                    self._edit(
                        context, bpy.ops.handycactus.move_tip, "Move Tip",
                        tip=tip, location=location, normal=normal, target=name,
                    )  # fmt: skip
            else:
                depth = self.graph.pos[node] / self.session.scale
                mouse = (event.mouse_region_x, event.mouse_region_y)
                location = view3d_utils.region_2d_to_location_3d(
                    context.region, context.region_data, mouse, depth
                )
                self._edit(context, bpy.ops.handycactus.move_node, "Move Node", node=node, location=location)
            return {"RUNNING_MODAL"}

        if event.type == "L" and event.value == "PRESS" and self.hover >= 0:
            self._edit(context, bpy.ops.handycactus.toggle_lock, "Toggle Lock", node=self.hover)
            return {"RUNNING_MODAL"}

        if (
            event.type == "T"
            and event.value == "PRESS"
            and self.hover >= 0
            and self.graph.tip[self.hover] >= 0
        ):
            bpy.ops.handycactus.rebuild_tip("INVOKE_DEFAULT", tip=int(self.graph.tip[self.hover]))
            self._reload(context)
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}


classes = (
    HANDYCACTUS_OT_add_tip,
    HANDYCACTUS_OT_delete_tip,
    HANDYCACTUS_OT_move_tip,
    HANDYCACTUS_OT_move_node,
    HANDYCACTUS_OT_toggle_lock,
    HANDYCACTUS_OT_lock_all,
    HANDYCACTUS_OT_rebuild_tip,
    HANDYCACTUS_OT_edit_supports,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
