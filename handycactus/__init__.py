# SPDX-License-Identifier: GPL-3.0-or-later
"""HandyCactus: resin-style tree supports for FDM miniatures."""

# The Blender modules are imported inside register/unregister so that
# `handycactus.core` can be imported (and tested) without bpy.


def _modules():
    from .blender import edit_tool, export_ops, operators, overlay, panels, props, user_presets

    return (props, overlay, operators, edit_tool, export_ops, user_presets, panels)


def register():
    for module in _modules():
        module.register()


def unregister():
    for module in reversed(_modules()):
        module.unregister()
