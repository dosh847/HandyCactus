# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender properties, generated from core.params.PARAM_SPECS.

`Scene.handycactus` holds the working settings used for the next Generate.
From M2 on, Generate copies them onto the supports object it creates, so a
support set keeps the parameters it was made with (spec section 5.1).
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from bpy.types import PropertyGroup

from ..core.params import DEFAULT_PRESET_ID, PARAM_SPECS, PRESETS, PRESETS_BY_ID, Params


def apply_preset(settings, preset_id: str) -> None:
    for name, value in PRESETS_BY_ID[preset_id].params().as_dict().items():
        setattr(settings, name, value)


def settings_to_params(settings) -> Params:
    return Params().with_values({spec.name: getattr(settings, spec.name) for spec in PARAM_SPECS})


def _on_preset_changed(self, _context):
    apply_preset(self, self.preset)


def _on_heatmap_toggled(self, context):
    from . import overlay  # overlay imports this module

    overlay.sync(context.scene)


def _param_property(spec):
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


def register():
    bpy.utils.register_class(HC_Settings)
    bpy.types.Scene.handycactus = PointerProperty(type=HC_Settings)


def unregister():
    del bpy.types.Scene.handycactus
    bpy.utils.unregister_class(HC_Settings)
