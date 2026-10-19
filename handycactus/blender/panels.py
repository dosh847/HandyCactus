# SPDX-License-Identifier: GPL-3.0-or-later
"""The "HandyCactus" tab in the 3D viewport sidebar."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.params import GROUPS, PARAM_SPECS
from .operators import HANDYCACTUS_OT_apply_preset
from .props import settings_to_params

UNIT_SUFFIX = {"mm": "mm", "deg": "°", "mm/mm": "mm/mm", "x": "×", "": ""}


class _HCPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HandyCactus"


class HANDYCACTUS_PT_main(_HCPanel, Panel):
    bl_idname = "HANDYCACTUS_PT_main"
    bl_label = "HandyCactus"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.handycactus

        row = layout.row(align=True)
        row.prop(settings, "preset", text="")
        row.operator(HANDYCACTUS_OT_apply_preset.bl_idname, text="", icon="LOOP_BACK")

        targets = [ob for ob in context.selected_objects if ob.type == "MESH"]
        box = layout.box()
        box.label(text=f"Targets: {len(targets)} selected mesh object(s)", icon="OBJECT_DATA")
        box.label(text="Generation arrives in M2", icon="INFO")

        for problem in settings_to_params(settings).problems():
            layout.label(text=problem, icon="ERROR")


def _make_group_panel(group_id: str, group_label: str) -> type:
    specs = [s for s in PARAM_SPECS if s.group == group_id]

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = context.scene.handycactus
        for spec in specs:
            suffix = UNIT_SUFFIX[spec.unit]
            text = f"{spec.label} ({suffix})" if suffix else spec.label
            layout.prop(settings, spec.name, text=text)

    return type(
        f"HANDYCACTUS_PT_{group_id}",
        (_HCPanel, Panel),
        {
            "bl_idname": f"HANDYCACTUS_PT_{group_id}",
            "bl_label": group_label,
            "bl_parent_id": HANDYCACTUS_PT_main.bl_idname,
            "bl_options": {"DEFAULT_CLOSED"},
            "draw": draw,
        },
    )


classes = (HANDYCACTUS_PT_main, *(_make_group_panel(gid, label) for gid, label in GROUPS))


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
