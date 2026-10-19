# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs inside Blender (the `bpy` module from PyPI, matching the minimum version)."""

import pytest

bpy = pytest.importorskip("bpy")

import handycactus  # noqa: E402
from handycactus.core.params import PARAM_SPECS, PRESETS_BY_ID  # noqa: E402


@pytest.fixture
def registered():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    handycactus.register()
    yield bpy.context.scene.handycactus
    handycactus.unregister()


def test_blender_meets_minimum_version():
    assert bpy.app.version >= (5, 2, 0)


def test_settings_have_spec_defaults(registered):
    for spec in PARAM_SPECS:
        assert getattr(registered, spec.name) == pytest.approx(spec.default), spec.name
    assert registered.preset == "MINI_42"


def test_panels_and_operator_registered(registered):
    assert hasattr(bpy.types, "HANDYCACTUS_PT_main")
    assert hasattr(bpy.types, "HANDYCACTUS_PT_branches")
    assert hasattr(bpy.ops.handycactus, "apply_preset")


def test_choosing_a_preset_applies_it(registered):
    registered.preset = "MINI_32"
    assert registered.tip_spacing == pytest.approx(1.3)
    assert registered.foot_radius == pytest.approx(1.2)


def test_reset_to_preset_operator(registered):
    registered.preset = "MINI_50"
    registered.tip_spacing = 4.0
    assert bpy.ops.handycactus.apply_preset() == {"FINISHED"}
    assert registered.tip_spacing == pytest.approx(PRESETS_BY_ID["MINI_50"].params().tip_spacing)


def test_unregister_cleans_up_and_reregisters():
    handycactus.register()
    handycactus.unregister()
    assert not hasattr(bpy.types.Scene, "handycactus")
    assert not hasattr(bpy.types, "HANDYCACTUS_PT_main")
    handycactus.register()
    handycactus.unregister()
