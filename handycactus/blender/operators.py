# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: presets (M0), contact preview and placement (M1). Generation arrives in M2."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..core.detection import TooManyContacts
from . import overlay, pipeline
from .mesh_io import BLOCK_GROUP, mesh_targets
from .props import apply_preset, settings_to_params


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


classes = (
    HANDYCACTUS_OT_apply_preset,
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
