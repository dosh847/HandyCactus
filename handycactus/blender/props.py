# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender properties, generated from core.params.PARAM_SPECS.

`Scene.handycactus` holds the working settings used for the next Generate.
Generate records them on the supports object it creates
(`Object.handycactus_set`), together with the target set, so a support set
remembers what it was made from and with (spec section 5.1).
"""

from __future__ import annotations

import json

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

from ..core.params import DEFAULT_PRESET_ID, PARAM_SPECS, PRESETS, PRESETS_BY_ID, SPECS_BY_NAME, Params


def apply_preset(settings, preset_id: str) -> None:
    for name, value in PRESETS_BY_ID[preset_id].params().as_dict().items():
        setattr(settings, name, bool(value) if SPECS_BY_NAME[name].unit == "bool" else value)


def settings_to_params(settings) -> Params:
    values = {}
    for spec in PARAM_SPECS:
        value = getattr(settings, spec.name)
        values[spec.name] = int(value) if spec.unit == "bool" else value
    return Params().with_values(values)


def _on_preset_changed(self, _context):
    apply_preset(self, self.preset)


def _on_units_changed(self, context):
    from . import overlay  # overlay imports this module

    overlay.invalidate()


def _on_heatmap_toggled(self, context):
    from . import overlay  # overlay imports this module

    overlay.sync(context.scene)


def _param_property(spec):
    if spec.unit == "bool":
        return BoolProperty(name=spec.label, description=spec.description, default=bool(spec.default))
    description = f"{spec.description} ({spec.unit})" if spec.unit else spec.description
    if spec.is_int:
        return IntProperty(
            name=spec.label, description=description, default=spec.default, min=spec.min, max=spec.max
        )
    return FloatProperty(
        name=spec.label,
        description=description,
        default=spec.default,
        min=spec.min,
        max=spec.max,
        precision=2,
        step=1,
    )


def _build_settings_class():
    annotations = {
        "preset": EnumProperty(
            name="Preset",
            description="Parameter preset; choosing one overwrites every parameter",
            items=[(p.id, p.label, p.description) for p in PRESETS],
            default=DEFAULT_PRESET_ID,
            update=_on_preset_changed,
        ),
        "units": EnumProperty(
            name="Units",
            description="How Blender units map to millimetres",
            items=[
                ("MM", "1 Unit = 1 mm", "One Blender unit is one millimetre (how STL files import)"),
                ("SCENE", "Scene Units", "Follow the scene's Unit Scale"),
            ],
            default="MM",
            update=_on_units_changed,
        ),
        "show_heatmap": BoolProperty(
            name="Overhang Heatmap",
            description="Colour the faces of the selected meshes that need support",
            default=False,
            update=_on_heatmap_toggled,
        ),
    }
    for spec in PARAM_SPECS:
        annotations[spec.name] = _param_property(spec)
    return type("HC_Settings", (PropertyGroup,), {"__annotations__": annotations})


HC_Settings = _build_settings_class()


def _is_mesh(_self, obj):
    return obj.type == "MESH"


class HC_TargetRef(PropertyGroup):
    obj: PointerProperty(type=bpy.types.Object, poll=_is_mesh)


class HC_Finding(PropertyGroup):
    """One printability check result (core.checks.Finding)."""

    code: StringProperty()
    severity: StringProperty()  # ERROR or WARNING
    message: StringProperty()
    location: FloatVectorProperty(size=3, description="Where, in mm (world)")


class HC_SupportSet(PropertyGroup):
    """Stored on a supports object: what it supports and how it was made."""

    is_support: BoolProperty(default=False)
    targets: CollectionProperty(type=HC_TargetRef)
    params_json: StringProperty(description="Parameters used for the last generation (mm)")
    tip_count: IntProperty()
    tree_count: IntProperty()
    landing_count: IntProperty()
    unreachable_count: IntProperty()
    seconds: FloatProperty()
    unreachable_json: StringProperty(description="Contacts that could not reach the bed (mm)")
    findings: CollectionProperty(type=HC_Finding)
    findings_index: IntProperty(name="Finding")
    checked: BoolProperty(default=False)

    def unreachable_points(self):
        import numpy as np

        return np.array(json.loads(self.unreachable_json) if self.unreachable_json else [], float).reshape(
            -1, 3
        )

    def set_findings(self, findings) -> None:
        self.findings.clear()
        for f in findings:
            item = self.findings.add()
            item.code, item.severity, item.message, item.location = f.code, f.severity, f.message, f.location
        self.findings_index = 0
        self.checked = True

    def counts(self) -> tuple[int, int]:
        """(errors, warnings); INFO findings such as landings are not counted."""
        errors = sum(1 for f in self.findings if f.severity == "ERROR")
        warnings = sum(1 for f in self.findings if f.severity == "WARNING")
        return errors, warnings

    def target_objects(self) -> list:
        return [t.obj for t in self.targets if t.obj is not None]

    def set_params(self, params: Params) -> None:
        self.params_json = json.dumps(params.as_dict())

    def params(self) -> Params | None:
        return Params().with_values(json.loads(self.params_json)) if self.params_json else None


def is_support(obj) -> bool:
    return obj is not None and obj.handycactus_set.is_support


classes = (HC_Settings, HC_TargetRef, HC_Finding, HC_SupportSet)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.handycactus = PointerProperty(type=HC_Settings)
    bpy.types.Object.handycactus_set = PointerProperty(type=HC_SupportSet)


def unregister():
    del bpy.types.Object.handycactus_set
    del bpy.types.Scene.handycactus
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
