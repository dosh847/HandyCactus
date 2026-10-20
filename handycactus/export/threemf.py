# SPDX-License-Identifier: GPL-3.0-or-later
"""3MF writer (spec section 11): `zipfile` + plain XML text, no dependencies.

The parts (each target, named after its Blender object, then the supports) are written
as one object with one part each, in millimetres, so the supports can get their own
filament or settings. Slicers disagree on how a multi-part object is stored, so there
are three layouts (read from their importers' source):

- BAMBU (Bambu Studio, OrcaSlicer): one build item, a parent object whose components
  are one mesh object per part, plus `Metadata/model_settings.config` naming the object
  and its parts (part id = component object id). Orca reads that file from any vendor's
  3MF and turns each component into a part (bbs_3mf.cpp, _generate_volumes_new).
- PRUSA (PrusaSlicer): one mesh object holding every part's triangles, plus
  `Metadata/Slic3r_PE_model.config` listing each part as a triangle range. PrusaSlicer
  turns components into separate objects and drops each onto the bed on its own, which
  would pull the model off its supports (3mf.cpp, _create_object_instance).
- CORE: the BAMBU layout without the config file; a plain core 3MF.
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import quoteattr

import numpy as np

from .parts import Part

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
MODEL_PATH = "3D/3dmodel.model"
CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="text/xml"/>
</Types>
"""
RELS = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/{MODEL_PATH}" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


LAYOUTS = ("BAMBU", "PRUSA", "CORE")


def write_3mf(path: str, object_name: str, parts: list[Part], layout: str = "BAMBU") -> None:
    """Write `parts` as one multi-part object in the given layout (see module docstring)."""
    if layout not in LAYOUTS:
        raise ValueError(f"unknown 3MF layout {layout!r}")
    parts = [p for p in parts if len(p.tris)]
    if not parts:
        raise ValueError("nothing to export")
    # Level 1: a 1.2M-triangle export compresses in 1 s instead of 3.4 s, 25% larger.
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        if layout == "PRUSA":
            z.writestr(MODEL_PATH, single_mesh_xml(object_name, parts))
            z.writestr("Metadata/Slic3r_PE_model.config", prusa_config(object_name, parts))
            return
        z.writestr(MODEL_PATH, model_xml(object_name, parts))
        if layout == "BAMBU":
            z.writestr("Metadata/model_settings.config", bambu_config(object_name, parts))


def parent_id(parts: list[Part]) -> int:
    return len(parts) + 1  # parts are objects 1..N


def model_xml(object_name: str, parts: list[Part]) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}">\n',
        ' <metadata name="Application">HandyCactus</metadata>\n',
        " <resources>\n",
    ]
    for i, p in enumerate(parts, start=1):
        out.append(f' <object id="{i}" type="model" name={quoteattr(p.name)}>\n  <mesh>\n   <vertices>\n')
        out.append(_vertices(p.verts))
        out.append("   </vertices>\n   <triangles>\n")
        out.append(_triangles(p.tris))
        out.append("   </triangles>\n  </mesh>\n </object>\n")
    pid = parent_id(parts)
    out.append(f' <object id="{pid}" type="model" name={quoteattr(object_name)}>\n  <components>\n')
    out.extend(f'   <component objectid="{i}"/>\n' for i in range(1, len(parts) + 1))
    out.append("  </components>\n </object>\n </resources>\n")
    out.append(f' <build>\n  <item objectid="{pid}"/>\n </build>\n</model>\n')
    return "".join(out)


def single_mesh_xml(object_name: str, parts: list[Part]) -> str:
    """One mesh object holding every part, parts back to back (vertices and triangles)."""
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}">\n',
        ' <metadata name="Application">HandyCactus</metadata>\n',
        " <resources>\n",
        f' <object id="1" type="model" name={quoteattr(object_name)}>\n  <mesh>\n   <vertices>\n',
    ]
    out.extend(_vertices(p.verts) for p in parts)
    out.append("   </vertices>\n   <triangles>\n")
    offset = 0
    for p in parts:
        out.append(_triangles(p.tris + offset))
        offset += len(p.verts)
    out.append('   </triangles>\n  </mesh>\n </object>\n </resources>\n <build>\n  <item objectid="1"/>\n')
    out.append(" </build>\n</model>\n")
    return "".join(out)


def prusa_config(object_name: str, parts: list[Part]) -> str:
    """PrusaSlicer's object name and one volume (part) per triangle range."""
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n',
        ' <object id="1" instances_count="1">\n',
        f'  <metadata type="object" key="name" value={quoteattr(object_name)}/>\n',
    ]
    first = 0
    for p in parts:
        last = first + len(p.tris) - 1
        out.append(f'  <volume firstid="{first}" lastid="{last}">\n')
        out.append(f'   <metadata type="volume" key="name" value={quoteattr(p.name)}/>\n')
        out.append('   <metadata type="volume" key="volume_type" value="ModelPart"/>\n')
        out.append('   <metadata type="volume" key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n')
        out.append("  </volume>\n")
        first = last + 1
    out.append(" </object>\n</config>\n")
    return "".join(out)


def _vertices(verts: np.ndarray) -> str:
    # %.6g keeps ~0.1 µm on a 50 mm mini, and drops trailing zeros.
    return _rows('    <vertex x="%.6g" y="%.6g" z="%.6g"/>', np.asarray(verts, np.float64))


def _triangles(tris: np.ndarray) -> str:
    return _rows('    <triangle v1="%d" v2="%d" v3="%d"/>', np.asarray(tris, np.int64))


def _rows(fmt: str, values: np.ndarray) -> str:
    if len(values) == 0:
        return ""
    buf = io.StringIO()
    np.savetxt(buf, values, fmt=fmt)
    return buf.getvalue()


def bambu_config(object_name: str, parts: list[Part]) -> str:
    """Bambu Studio / OrcaSlicer object and part names (part id = component object id)."""
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n',
        f' <object id="{parent_id(parts)}">\n',
        f'  <metadata key="name" value={quoteattr(object_name)}/>\n',
        '  <metadata key="extruder" value="1"/>\n',
    ]
    for i, p in enumerate(parts, start=1):
        out.append(f'  <part id="{i}" subtype="normal_part">\n')
        out.append(f'   <metadata key="name" value={quoteattr(p.name)}/>\n')
        out.append('   <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n')
        out.append(f'   <mesh_stat face_count="{len(p.tris)}"/>\n')
        out.append("  </part>\n")
    out.append(" </object>\n</config>\n")
    return "".join(out)
