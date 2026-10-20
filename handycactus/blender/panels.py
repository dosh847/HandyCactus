# SPDX-License-Identifier: GPL-3.0-or-later
"""The "HandyCactus" tab in the 3D viewport sidebar."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core.params import GROUPS, PARAM_SPECS
from . import overlay
from .mesh_io import set_size_mm
from .operators import (
    HANDYCACTUS_OT_apply_preset,
    HANDYCACTUS_OT_check_supports,
    HANDYCACTUS_OT_clear_preview,
    HANDYCACTUS_OT_clear_supports,
    HANDYCACTUS_OT_frame_finding,
    HANDYCACTUS_OT_generate,
    HANDYCACTUS_OT_place_on_bed,
    HANDYCACTUS_OT_preview_contacts,
)
from .props import is_support, settings_to_params

# Bigger than any miniature: probably a units mismatch.
LARGE_MODEL_MM = 500.0

UNIT_SUFFIX = {"mm": "mm", "deg": "°", "mm/mm": "mm/mm", "x": "×", "bool": "", "": ""}


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

        targets = overlay.current_set(context)
        box = layout.box()
        box.label(text=f"Targets: {len(targets)} selected mesh object(s)", icon="OBJECT_DATA")
        box.prop(settings, "units", text="Units")
        size = set_size_mm(targets, context.scene)
        if size is not None:
            box.label(text="Size: " + " × ".join(f"{v:,.1f}" for v in size) + " mm")
            if max(size) > LARGE_MODEL_MM:
                box.label(text="Larger than a mini; check Units", icon="ERROR")
        box.operator(HANDYCACTUS_OT_place_on_bed.bl_idname, icon="TRIA_DOWN_BAR")

        box = layout.box()
        row = box.row(align=True)
        row.prop(settings, "show_heatmap", icon="SHADING_SOLID")
        row.prop(settings, "show_markers", text="", icon="HIDE_OFF" if settings.show_markers else "HIDE_ON")
        if settings.show_heatmap:
            text = f"{overlay.state.face_count} faces need support"
            if overlay.state.gap_face_count:
                text += f", {overlay.state.gap_face_count} uncovered"
            box.label(text=text)
        row = box.row(align=True)
        row.operator(HANDYCACTUS_OT_preview_contacts.bl_idname, icon="POINTCLOUD_DATA")
        row.operator(HANDYCACTUS_OT_clear_preview.bl_idname, text="", icon="X")
        if overlay.state.preview is not None:
            box.label(text=overlay.contact_summary(overlay.state.preview))
        box = layout.box()
        row = box.row(align=True)
        row.scale_y = 1.4
        row.operator(HANDYCACTUS_OT_generate.bl_idname, icon="OUTLINER_OB_POINTCLOUD")
        box.operator(HANDYCACTUS_OT_clear_supports.bl_idname, icon="TRASH")
        active = context.active_object
        if is_support(active):
            info = active.handycactus_set
            box.label(text=f"{active.name}", icon="MOD_SKIN")
            box.label(text=f"{info.tip_count} tips, {info.tree_count} trees ({info.seconds:.2f} s)")
            if info.landing_count:
                box.label(text=f"{info.landing_count} branch(es) land on the model", icon="INFO")
            if info.unreachable_count:
                box.label(text=f"{info.unreachable_count} contact(s) unreachable", icon="ERROR")
            box.label(text="Targets: " + ", ".join(ob.name for ob in info.target_objects()))
            _draw_editing(box, info)
            _draw_checks(box, info)

        for problem in settings_to_params(settings).problems():
            layout.label(text=problem, icon="ERROR")


def _draw_editing(layout, info) -> None:
    col = layout.column(align=True)
    col.operator("handycactus.edit_supports", icon="EDITMODE_HLT")
    row = col.row(align=True)
    row.operator("handycactus.lock_all", text="Lock All", icon="LOCKED").locked = True
    row.operator("handycactus.lock_all", text="Unlock All", icon="UNLOCKED").locked = False
    if info.locked_count:
        layout.label(text=f"{info.locked_count} locked tree(s) kept on Generate", icon="LOCKED")


def _draw_checks(layout, info) -> None:
    row = layout.row(align=True)
    row.operator(HANDYCACTUS_OT_check_supports.bl_idname, icon="CHECKMARK")
    if not info.checked:
        return
    errors, warnings = info.counts()
    if not len(info.findings):
        layout.label(text="All checks passed", icon="CHECKMARK")
        return
    layout.label(text=f"{errors} error(s), {warnings} warning(s)", icon="ERROR" if errors else "INFO")
    layout.template_list("HANDYCACTUS_UL_findings", "", info, "findings", info, "findings_index", rows=4)
    layout.operator(HANDYCACTUS_OT_frame_finding.bl_idname, icon="VIEWZOOM")


class HANDYCACTUS_UL_findings(UIList):
    """Check findings of a support set: errors first, then warnings."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        icon = {"ERROR": "CANCEL", "WARNING": "ERROR"}.get(item.severity, "INFO")
        layout.label(text=item.message, icon=icon)


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


classes = (
    HANDYCACTUS_UL_findings,
    HANDYCACTUS_PT_main,
    *(_make_group_panel(gid, label) for gid, label in GROUPS),
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
