# SPDX-License-Identifier: GPL-3.0-or-later
"""HandyCactus: resin-style tree supports for FDM miniatures."""

# The Blender modules are imported inside register/unregister so that
# `handycactus.core` can be imported (and tested) without bpy.


def _modules():
    from .blender import operators, panels, props

    return (props, operators, panels)


def register():
    for module in _modules():
        module.register()


def unregister():
    for module in reversed(_modules()):
        module.unregister()
