# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators. M0 has only preset handling; generation arrives in M2."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from .props import apply_preset


class HANDYCACTUS_OT_apply_preset(Operator):
    """Reset every parameter to the values of the selected preset"""

    bl_idname = "handycactus.apply_preset"
    bl_label = "Reset to Preset"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.handycactus
        apply_preset(settings, settings.preset)
        return {"FINISHED"}


classes = (HANDYCACTUS_OT_apply_preset,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
