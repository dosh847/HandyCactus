# SPDX-License-Identifier: GPL-3.0-or-later
"""The user's own presets: save, load, delete (spec section 9)."""

import pytest

bpy = pytest.importorskip("bpy")

import handycactus  # noqa: E402
from handycactus.blender import user_presets  # noqa: E402


@pytest.fixture
def settings(tmp_path, monkeypatch):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    handycactus.register()
    monkeypatch.setattr(user_presets, "preset_dir", lambda: str(tmp_path))
    yield bpy.context.scene.handycactus
    handycactus.unregister()


def test_save_load_delete(settings):
    settings.tip_spacing, settings.land_on_model = 2.25, False
    assert bpy.ops.handycactus.save_preset(name="Big minis / PETG") == {"FINISHED"}
    assert user_presets.preset_names() == ["Big minis _ PETG"]  # file-safe name
    settings.tip_spacing, settings.land_on_model = 1.0, True
    assert bpy.ops.handycactus.load_preset(name="Big minis _ PETG") == {"FINISHED"}
    assert settings.tip_spacing == pytest.approx(2.25) and settings.land_on_model is False
    assert bpy.ops.handycactus.delete_preset(name="Big minis _ PETG") == {"FINISHED"}
    assert user_presets.preset_names() == []
    assert not bpy.ops.handycactus.delete_preset.poll()


def test_presets_tolerate_unknown_and_missing_keys(settings, tmp_path):
    (tmp_path / "old.json").write_text('{"params": {"tip_spacing": 1.75, "no_such_param": 3}}')
    settings.max_radius = 2.5
    assert bpy.ops.handycactus.load_preset(name="old") == {"FINISHED"}
    assert settings.tip_spacing == pytest.approx(1.75) and settings.max_radius == pytest.approx(2.5)


def test_preset_dir_is_writable_without_an_extension(settings, monkeypatch):
    monkeypatch.undo()  # the real lookup: a plain add-on falls back to Blender's config dir
    import os

    assert os.path.isdir(user_presets.preset_dir())
