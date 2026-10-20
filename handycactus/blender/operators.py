# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: presets, placement, contact preview and support generation."""

from __future__ import annotations

import bpy
from bpy.types import Operator
from mathutils import Vector

from ..core.detection import TooManyContacts
from . import overlay, pipeline
from .mesh_io import BLOCK_GROUP, mesh_targets, scene_mm_per_bu
from .props import apply_preset, is_support, settings_to_params


class HANDYCACTUS_OT_apply_preset(Operator):
    """Reset every parameter to the values of the selected preset"""

    bl_idname = "handycactus.apply_preset"
    bl_label = "Reset to Preset"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.handycactus
        apply_preset(settings, settings.preset)
        return {"FINISHED"}


class _NeedsMeshSelection:
    @classmethod
    def poll(cls, context):
        return bool(mesh_targets(context.selected_objects))


class HANDYCACTUS_OT_preview_contacts(_NeedsMeshSelection, Operator):
    """Detect where the selected meshes need support and show the contact points"""

    bl_idname = "handycactus.preview_contacts"
    bl_label = "Preview Contacts"
    bl_options = {"REGISTER"}

    def execute(self, context):
        problems = settings_to_params(context.scene.handycactus).problems()
        if problems:
            self.report({"ERROR"}, problems[0])
            return {"CANCELLED"}
        objects = mesh_targets(context.selected_objects)
        lowest = pipeline.lowest_point_mm(pipeline.read_set(context, objects))
        if lowest < -pipeline.BELOW_BED_TOLERANCE_MM:
            self.report({"ERROR"}, f"Selection is {-lowest:.2f} mm below the bed; use Place on Bed first")
            return {"CANCELLED"}

        try:
            run = pipeline.run_detection(context, objects)
        except TooManyContacts as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        overlay.set_preview(run)
        for warning in run.result.warnings:
            self.report({"WARNING"}, warning)
        blocked = len(run.result.blocked_islands)
        suffix = f"; {blocked} island(s) blocked by {BLOCK_GROUP}" if blocked else ""
        self.report({"INFO"}, overlay.contact_summary(run) + suffix)
        return {"FINISHED"}


class HANDYCACTUS_OT_clear_preview(Operator):
    """Hide the contact preview"""

    bl_idname = "handycactus.clear_preview"
    bl_label = "Clear Preview"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return overlay.state.preview is not None

    def execute(self, context):
        overlay.clear_preview()
        return {"FINISHED"}


class HANDYCACTUS_OT_place_on_bed(_NeedsMeshSelection, Operator):
    """Move the selected meshes together so their lowest point sits at the lift height"""

    bl_idname = "handycactus.place_on_bed"
    bl_label = "Place on Bed"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        lift = context.scene.handycactus.lift_height
        moved = pipeline.place_on_bed(context, context.selected_objects, lift)
        self.report({"INFO"}, f"Moved {moved:+.2f} mm; lowest point now at {lift:.2f} mm")
        return {"FINISHED"}


def selected_supports(context) -> list:
    sel = [ob for ob in context.selected_objects if is_support(ob)]
    if is_support(context.active_object) and context.active_object not in sel:
        sel.append(context.active_object)
    return sel


class HANDYCACTUS_OT_generate(Operator):
    """Generate tree supports for the selected meshes, or rebuild the selected support set"""

    bl_idname = "handycactus.generate"
    bl_label = "Generate Supports"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(mesh_targets(context.selected_objects) or selected_supports(context))

    def execute(self, context):
        targets = mesh_targets(context.selected_objects)
        support = None
        if not targets:  # rebuild a selected support set from its own targets
            support = selected_supports(context)[0]
            targets = support.handycactus_set.target_objects()
            if not targets:
                self.report({"ERROR"}, "This support set's targets no longer exist")
                return {"CANCELLED"}
        else:
            support = _existing_support_for(context, targets)
        try:
            result = pipeline.generate(context, targets, support)
        except (TooManyContacts, pipeline.BelowBed, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        overlay.clear_preview()
        info = result.support.handycactus_set
        overlay.refresh()
        errors, warnings = info.counts()
        msg = f"{result.tips} tips on {result.trees} trees in {result.seconds:.2f} s"
        if result.unreachable:
            msg += f"; {result.unreachable} contact(s) could not reach the bed"
        msg += f"; checks: {errors} error(s), {warnings} warning(s)"
        self.report({"WARNING"} if errors else {"INFO"}, msg)
        return {"FINISHED"}


def _existing_support_for(context, targets):
    """A support set in the scene built for exactly these targets, to rebuild in place."""
    wanted = {ob.name for ob in targets}
    for ob in context.scene.objects:
        if is_support(ob) and {t.name for t in ob.handycactus_set.target_objects()} == wanted:
            return ob
    return None


class HANDYCACTUS_OT_clear_supports(Operator):
    """Delete the selected support sets"""

    bl_idname = "handycactus.clear_supports"
    bl_label = "Clear Supports"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(selected_supports(context))

    def execute(self, context):
        supports = selected_supports(context)
        for ob in supports:
            pipeline.remove_support(ob)
        overlay.refresh()
        self.report({"INFO"}, f"Removed {len(supports)} support set(s)")
        return {"FINISHED"}


class HANDYCACTUS_OT_check_supports(Operator):
    """Run the printability checks on the selected support set"""

    bl_idname = "handycactus.check_supports"
    bl_label = "Check Supports"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(selected_supports(context))

    def execute(self, context):
        support = selected_supports(context)[0]
        pipeline.check_support(context, support)
        info = support.handycactus_set
        overlay.refresh()
        errors, warnings = info.counts()
        self.report({"WARNING"} if errors else {"INFO"}, f"{errors} error(s), {warnings} warning(s)")
        return {"FINISHED"}


class HANDYCACTUS_OT_frame_finding(Operator):
    """Put the 3D cursor on a check finding and centre the view on it"""

    bl_idname = "handycactus.frame_finding"
    bl_label = "Frame Finding"
    bl_options = {"REGISTER"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return (
            bool(selected_supports(context))
            and len(selected_supports(context)[0].handycactus_set.findings) > 0
        )

    def execute(self, context):
        info = selected_supports(context)[0].handycactus_set
        index = self.index if self.index >= 0 else info.findings_index
        if not 0 <= index < len(info.findings):
            return {"CANCELLED"}
        info.findings_index = index
        finding = info.findings[index]
        location = Vector(finding.location) / scene_mm_per_bu(context.scene)
        context.scene.cursor.location = location
        region_3d = getattr(context.space_data, "region_3d", None) if context.space_data else None
        if region_3d is not None:
            region_3d.view_location = location
            region_3d.view_distance = 25.0 / scene_mm_per_bu(context.scene)  # about a 25 mm view
        self.report({"INFO"}, finding.message)
        return {"FINISHED"}


classes = (
    HANDYCACTUS_OT_apply_preset,
    HANDYCACTUS_OT_generate,
    HANDYCACTUS_OT_clear_supports,
    HANDYCACTUS_OT_check_supports,
    HANDYCACTUS_OT_frame_finding,
    HANDYCACTUS_OT_preview_contacts,
    HANDYCACTUS_OT_clear_preview,
    HANDYCACTUS_OT_place_on_bed,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
