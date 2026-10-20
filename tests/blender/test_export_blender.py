# SPDX-License-Identifier: GPL-3.0-or-later
"""M5: 3MF and STL export of a support set with its model."""

import os

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")

import handycactus  # noqa: E402
from handycactus.blender import export_ops  # noqa: E402
from handycactus.export.stl import read_stl  # noqa: E402


@pytest.fixture
def support():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    handycactus.register()
    bpy.ops.mesh.primitive_cube_add(size=8, location=(0, 0, 14))  # underside at z = 10
    box = bpy.context.active_object
    box.name = "box"
    mod = box.modifiers.new("subdiv", "SUBSURF")
    mod.subdivision_type, mod.levels = "SIMPLE", 2
    bpy.ops.handycactus.generate()
    support = next(ob for ob in bpy.context.scene.objects if ob.handycactus_set.is_support)
    yield support
    handycactus.unregister()


def load_3mf(path):
    lib3mf = pytest.importorskip("lib3mf")
    model = lib3mf.get_wrapper().CreateModel()
    reader = model.QueryReader("3mf")
    reader.SetStrictModeActive(True)
    reader.ReadFromFile(str(path))
    assert reader.GetWarningCount() == 0
    meshes = model.GetMeshObjects()
    out = {}
    while meshes.MoveNext():
        m = meshes.GetCurrentMeshObject()
        verts = np.array([tuple(v.Coordinates) for v in m.GetVertices()])
        out[m.GetName()] = (verts, m.GetTriangleCount(), m.IsManifoldAndOriented())
    return out


def test_parts_are_world_mm_and_named(support):
    parts = export_ops.collect_parts(bpy.context, support)
    assert [p.name for p in parts] == ["box", support.name]
    box, sup = parts
    assert len(box.tris) == 6 * 16 * 2  # subdivided twice, triangulated
    assert box.verts[:, 2].min() == pytest.approx(10.0) and box.verts[:, 2].max() == pytest.approx(18.0)
    assert sup.is_support and sup.verts[:, 2].min() == pytest.approx(0.0, abs=1e-3)
    assert sup.verts[:, 2].max() < 10.0 + 0.3  # tips reach just into the model

    plain = export_ops.collect_parts(bpy.context, support, apply_modifiers=False)
    assert len(plain[0].tris) == 12  # the cube without its modifier
    only = export_ops.collect_parts(bpy.context, support, include_model=False)
    assert [p.name for p in only] == [support.name]


def test_export_3mf_operator(support, tmp_path):
    path = tmp_path / "box.3mf"
    assert bpy.ops.handycactus.export_3mf(filepath=str(path)) == {"FINISHED"}
    meshes = load_3mf(path)
    assert list(meshes) == ["box", support.name]
    box_verts, box_tris, box_ok = meshes["box"]
    assert box_ok and box_tris == 6 * 16 * 2 + 44  # with its riser pad (on by default)
    sup_verts, _, _ = meshes[support.name]
    assert sup_verts[:, 2].min() == pytest.approx(0.0, abs=1e-3)
    # The model part reaches the bed through its riser, so a slicer will not drop it.
    assert box_verts[:, 2].min() == pytest.approx(0.0, abs=1e-3)
    assert np.sort(np.unique(box_verts[:, 2].round(3)))[:2].tolist() == [0.0, 0.5]

    plain = tmp_path / "no_riser.3mf"
    assert bpy.ops.handycactus.export_3mf(filepath=str(plain), riser=False) == {"FINISHED"}
    assert load_3mf(plain)["box"][0][:, 2].min() == pytest.approx(10.0)


def test_export_stl_merged_and_separate(support, tmp_path):
    parts = export_ops.collect_parts(bpy.context, support)
    merged = tmp_path / "box.stl"
    assert bpy.ops.handycactus.export_stl(filepath=str(merged), mode="MERGED") == {"FINISHED"}
    assert len(read_stl(str(merged))) == sum(len(p.tris) for p in parts) + 44  # riser pad

    separate = tmp_path / "sep.stl"
    assert bpy.ops.handycactus.export_stl(filepath=str(separate), mode="SEPARATE") == {"FINISHED"}
    files = sorted(os.listdir(tmp_path))
    assert "sep_box.stl" in files and f"sep_{support.name}.stl" in files
    assert len(read_stl(str(tmp_path / "sep_box.stl"))) == len(parts[0].tris) + 44


def test_scene_units_export_in_mm(support):
    scene = bpy.context.scene
    scene.handycactus.units = "SCENE"
    scene.unit_settings.scale_length = 0.001  # 1 BU = 1 mm through the scene scale too
    parts = export_ops.collect_parts(bpy.context, support)
    assert parts[0].verts[:, 2].max() == pytest.approx(18.0)
    scene.unit_settings.scale_length = 0.01  # 1 BU = 10 mm
    parts = export_ops.collect_parts(bpy.context, support)
    assert parts[0].verts[:, 2].max() == pytest.approx(180.0)


def test_bake_watertight(support, tmp_path):
    sup = export_ops.collect_parts(bpy.context, support, include_model=False, bake=True, voxel_mm=0.1)[0]
    assert len(sup.tris) > 1000
    assert sup.verts[:, 2].min() == pytest.approx(0.0, abs=0.1)
    path = tmp_path / "baked.3mf"
    assert bpy.ops.handycactus.export_3mf(
        filepath=str(path), include_model=False, bake=True, voxel_size=0.1
    ) == {"FINISHED"}
    meshes = load_3mf(path)
    ((_, tris, manifold),) = meshes.values()
    assert manifold and tris == len(sup.tris)
    assert bpy.data.objects.get("HC_bake_temp") is None  # the temporary object is gone


def test_export_3mf_for_prusaslicer(support, tmp_path):
    path = tmp_path / "prusa.3mf"
    assert bpy.ops.handycactus.export_3mf(filepath=str(path), layout="PRUSA") == {"FINISHED"}
    parts = export_ops.collect_parts(bpy.context, support)
    meshes = load_3mf(path)
    (name,) = meshes
    assert name == "box" and meshes[name][1] == sum(len(p.tris) for p in parts) + 44
