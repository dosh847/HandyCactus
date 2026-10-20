# SPDX-License-Identifier: GPL-3.0-or-later
"""The user's own presets (spec section 9): every parameter, saved as JSON in the
extension's user directory (outside the add-on, so updates keep them)."""

from __future__ import annotations

import json
import os
import re

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Menu, Operator

from ..core.params import PARAM_SPECS
from .props import settings_to_params

SUFFIX = ".json"


def preset_dir() -> str:
    """<extension user dir>/presets; for a plain add-on (tests), Blender's config dir."""
    package = __package__.rsplit(".", 1)[0]  # handycactus.blender -> handycactus
    try:
        return bpy.utils.extension_path_user(package, path="presets", create=True)
    except (ValueError, KeyError, RuntimeError):
        path = bpy.utils.user_resource("CONFIG", path=os.path.join("handycactus", "presets"), create=True)
        return path


def preset_names() -> list[str]:
    folder = preset_dir()
    if not os.path.isdir(folder):
        return []
    return sorted(f[: -len(SUFFIX)] for f in os.listdir(folder) if f.endswith(SUFFIX))


def _path(name: str) -> str:
    return os.path.join(preset_dir(), safe(name) + SUFFIX)


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "Preset"


def save(name: str, settings) -> str:
    values = settings_to_params(settings).as_dict()
    path = _path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "params": values}, f, indent=1, sort_keys=True)
    return path


def load(name: str, settings) -> None:
    with open(_path(name), encoding="utf-8") as f:
        values = json.load(f)["params"]
    for spec in PARAM_SPECS:  # unknown or missing keys (older/newer presets) are skipped
        if spec.name in values:
            value = values[spec.name]
            setattr(settings, spec.name, bool(value) if spec.unit == "bool" else type(spec.default)(value))


def delete(name: str) -> None:
    os.remove(_path(name))


class HANDYCACTUS_OT_save_preset(Operator):
    """Save every parameter as one of your own presets"""

    bl_idname = "handycactus.save_preset"
    bl_label = "Save Preset"
    bl_options = {"REGISTER"}

    name: StringProperty(name="Name", default="My Preset")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            save(self.name, context.scene.handycactus)
        except OSError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f'Saved preset "{self.name}"')
        return {"FINISHED"}


class HANDYCACTUS_OT_load_preset(Operator):
    """Set every parameter from one of your presets"""

    bl_idname = "handycactus.load_preset"
    bl_label = "Load Preset"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty()

    def execute(self, context):
        try:
            load(self.name, context.scene.handycactus)
        except (OSError, ValueError, KeyError) as exc:
            self.report({"ERROR"}, f'Cannot load preset "{self.name}": {exc}')
            return {"CANCELLED"}
        return {"FINISHED"}


def _names_enum(_self, _context):
    names = preset_names()
    return [(n, n, "") for n in names] or [("", "(none)", "")]


class HANDYCACTUS_OT_delete_preset(Operator):
    """Delete one of your presets"""

    bl_idname = "handycactus.delete_preset"
    bl_label = "Delete Preset"
    bl_options = {"REGISTER"}

    name: EnumProperty(name="Preset", items=_names_enum)

    @classmethod
    def poll(cls, context):
        return bool(preset_names())

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if not self.name:
            return {"CANCELLED"}
        try:
            delete(self.name)
        except OSError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f'Deleted preset "{self.name}"')
        return {"FINISHED"}


class HANDYCACTUS_MT_user_presets(Menu):
    bl_label = "My Presets"

    def draw(self, context):
        layout = self.layout
        names = preset_names()
        for name in names:
            layout.operator(HANDYCACTUS_OT_load_preset.bl_idname, text=name).name = name
        if not names:
            layout.label(text="No saved presets yet")
        layout.separator()
        layout.operator(HANDYCACTUS_OT_save_preset.bl_idname, icon="ADD")
        layout.operator(HANDYCACTUS_OT_delete_preset.bl_idname, icon="REMOVE")


classes = (
    HANDYCACTUS_OT_save_preset,
    HANDYCACTUS_OT_load_preset,
    HANDYCACTUS_OT_delete_preset,
    HANDYCACTUS_MT_user_presets,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
