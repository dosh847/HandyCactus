# SPDX-License-Identifier: GPL-3.0-or-later
"""3MF and STL writers. The 3MF is validated with lib3mf (the 3MF Consortium's
reference library) when it is installed."""

import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest

from handycactus.export.parts import Part, drop_degenerate, merge
from handycactus.export.stl import read_stl, write_stl
from handycactus.export.threemf import CORE_NS, write_3mf

from .shapes import uv_sphere, voxels


def parts():
    body = voxels([((0, 0, 3), (4, 4, 8))], name="body")
    arm = uv_sphere((6, 2, 6), 1.5, segments=16, rings=8, name="arm & shield")
    supports = voxels([((1, 1, 0), (2, 2, 3))], name="supports")
    return [
        Part(body.name, body.verts, body.tris),
        Part(arm.name, arm.verts, arm.tris),
        Part("mini_supports", supports.verts, supports.tris, is_support=True),
    ]


def test_3mf_structure(tmp_path):
    path = tmp_path / "mini.3mf"
    ps = parts()
    write_3mf(str(path), "mini", ps)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"} <= names
        assert "Metadata/model_settings.config" in names
        model = ET.fromstring(z.read("3D/3dmodel.model"))
        config = ET.fromstring(z.read("Metadata/model_settings.config"))
    ns = {"m": CORE_NS}
    assert model.get("unit") == "millimeter"
    objects = model.findall("m:resources/m:object", ns)
    assert len(objects) == 4  # three parts + the parent
    for obj, p in zip(objects[:3], ps, strict=True):
        assert obj.get("name") == p.name
        verts = obj.findall("m:mesh/m:vertices/m:vertex", ns)
        tris = obj.findall("m:mesh/m:triangles/m:triangle", ns)
        assert len(verts) == len(p.verts) and len(tris) == len(p.tris)
        xyz = np.array([[float(v.get(a)) for a in "xyz"] for v in verts])
        assert np.allclose(xyz, p.verts, atol=1e-4)
    parent = objects[3]
    assert [c.get("objectid") for c in parent.findall("m:components/m:component", ns)] == ["1", "2", "3"]
    assert model.find("m:build/m:item", ns).get("objectid") == parent.get("id")
    # Bambu/Orca names: the object and one part per component.
    obj = config.find("object")
    assert obj.get("id") == parent.get("id")
    assert [part.get("id") for part in obj.findall("part")] == ["1", "2", "3"]
    names = [part.find("metadata[@key='name']").get("value") for part in obj.findall("part")]
    assert names == [p.name for p in ps]


def test_3mf_without_bambu_metadata(tmp_path):
    path = tmp_path / "plain.3mf"
    write_3mf(str(path), "mini", parts(), layout="CORE")
    with zipfile.ZipFile(path) as z:
        assert "Metadata/model_settings.config" not in z.namelist()


def test_3mf_prusa_layout(tmp_path):
    """One mesh; Slic3r_PE_model.config splits it into the parts by triangle range."""
    path = tmp_path / "prusa.3mf"
    ps = parts()
    write_3mf(str(path), "mini", ps, layout="PRUSA")
    with zipfile.ZipFile(path) as z:
        model = ET.fromstring(z.read("3D/3dmodel.model"))
        config = ET.fromstring(z.read("Metadata/Slic3r_PE_model.config"))
    ns = {"m": CORE_NS}
    (obj,) = model.findall("m:resources/m:object", ns)
    verts = np.array(
        [[float(v.get(a)) for a in "xyz"] for v in obj.findall("m:mesh/m:vertices/m:vertex", ns)]
    )
    tris = np.array(
        [
            [int(t.get(k)) for k in ("v1", "v2", "v3")]
            for t in obj.findall("m:mesh/m:triangles/m:triangle", ns)
        ]
    )
    cfg_obj = config.find("object")
    assert cfg_obj.get("id") == obj.get("id")
    volumes = cfg_obj.findall("volume")
    assert len(volumes) == len(ps)
    for vol, p in zip(volumes, ps, strict=True):
        first, last = int(vol.get("firstid")), int(vol.get("lastid"))
        assert vol.find("metadata[@key='name']").get("value") == p.name
        assert vol.find("metadata[@key='volume_type']").get("value") == "ModelPart"
        # The range holds exactly this part's triangles (PrusaSlicer rebases vertex ids to
        # the range's min..max, so each part's vertices must be contiguous too).
        assert np.allclose(verts[tris[first : last + 1]], p.verts[p.tris], atol=1e-4)
    assert int(volumes[-1].get("lastid")) == len(tris) - 1


@pytest.mark.parametrize("layout", ["BAMBU", "PRUSA", "CORE"])
def test_3mf_reads_back_in_lib3mf(tmp_path, layout):
    lib3mf = pytest.importorskip("lib3mf")
    path = tmp_path / "mini.3mf"
    ps = parts()
    write_3mf(str(path), "mini", ps, layout=layout)
    wrapper = lib3mf.get_wrapper()
    model = wrapper.CreateModel()
    reader = model.QueryReader("3mf")
    reader.SetStrictModeActive(True)  # spec violations are errors, not warnings
    reader.ReadFromFile(str(path))
    assert reader.GetWarningCount() == 0
    meshes = model.GetMeshObjects()
    counts = []
    while meshes.MoveNext():
        m = meshes.GetCurrentMeshObject()
        counts.append((m.GetName(), m.GetTriangleCount(), m.IsManifoldAndOriented()))
    if layout == "PRUSA":
        assert counts == [("mini", sum(len(p.tris) for p in ps), True)]
    else:
        assert counts == [(p.name, len(p.tris), True) for p in ps]
    items = model.GetBuildItems()
    assert items.Count() == 1
    assert model.GetUnit() == lib3mf.ModelUnit.MilliMeter


def test_3mf_rejects_an_empty_export(tmp_path):
    with pytest.raises(ValueError):
        write_3mf(str(tmp_path / "x.3mf"), "x", [Part("empty", np.zeros((0, 3)), np.zeros((0, 3)))])


def test_stl_round_trip(tmp_path):
    p = parts()[1]
    path = tmp_path / "arm.stl"
    assert write_stl(str(path), p) == len(p.tris)
    back = read_stl(str(path))
    assert np.allclose(back, p.verts[p.tris], atol=1e-5)
    assert path.stat().st_size == 84 + 50 * len(p.tris)


def test_merge_and_drop_degenerate():
    a, b, _ = parts()
    m = merge([a, b], "both")
    assert len(m.tris) == len(a.tris) + len(b.tris)
    assert np.allclose(m.verts[m.tris[len(a.tris) :]], b.verts[b.tris])
    bad = Part(
        "bad",
        np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [2, 0, 0], [9, 9, 9]]),
        [[0, 1, 2], [0, 1, 3], [0, 0, 2]],
    )
    clean = drop_degenerate(bad)
    assert len(clean.tris) == 1 and len(clean.verts) == 3  # collinear and repeated-index ones go
