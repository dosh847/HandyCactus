# SPDX-License-Identifier: GPL-3.0-or-later
"""Export a support set with its targets as 3MF or STL (spec section 11)."""

from __future__ import annotations

import os
import re

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper

from ..export.parts import Part, drop_degenerate, merge
from ..export.stl import write_stl
from ..export.threemf import write_3mf
from .mesh_io import read_target, scene_mm_per_bu

BAKE_GROUP = "HC_BakeWatertight"


def export_set(context) -> object | None:
    """The support set to export: the active supports object, or the set whose targets
    include the active object, or the only set in the scene."""
    from .edit_tool import edit_target  # edit_tool imports operators that import this

    return edit_target(context)


def collect_parts(
    context, support, include_model=True, apply_modifiers=True, bake=False, voxel_mm=0.05
) -> list[Part]:
    """World-space mm meshes: each target (named after its object), then the supports."""
    scale = scene_mm_per_bu(context.scene)
    depsgraph = context.evaluated_depsgraph_get()
    parts = []
    if include_model:
        for ob in support.handycactus_set.target_objects():
            parts.append(_read(ob, depsgraph, scale, evaluated=apply_modifiers))
    if bake:
        sup = bake_watertight(context, support, voxel_mm)
    else:
        sup = _read(support, depsgraph, scale, evaluated=True)
    sup.name, sup.is_support = f"{support.name}", True
    parts.append(sup)
    return [drop_degenerate(p) for p in parts]


def _read(ob, depsgraph, scale, evaluated: bool) -> Part:
    if evaluated:
        t = read_target(ob, depsgraph, scale, with_weights=False)
        return Part(ob.name, t.verts, t.tris)
    mesh = ob.data
    mesh.calc_loop_triangles()
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    m = np.array(ob.matrix_world, dtype=np.float64)
    verts = (co.reshape(-1, 3).astype(np.float64) @ m[:3, :3].T + m[:3, 3]) * scale
    tris = tris.reshape(-1, 3).astype(np.int64)
    if np.linalg.det(m[:3, :3]) < 0:
        tris = tris[:, [0, 2, 1]]
    return Part(ob.name, verts, tris)


def bake_watertight(context, support, voxel_mm: float) -> Part:
    """The supports as one closed shell: Mesh to Volume, Volume to Mesh (voxel_mm)."""
    scale = scene_mm_per_bu(context.scene)
    depsgraph = context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(support.evaluated_get(depsgraph))
    temp = bpy.data.objects.new("HC_bake_temp", mesh)
    temp.matrix_world = support.matrix_world
    context.scene.collection.objects.link(temp)
    try:
        mod = temp.modifiers.new("bake", "NODES")
        mod.node_group = _bake_group()
        voxel_bu = voxel_mm / scale
        for item in mod.node_group.interface.items_tree:
            if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == "Voxel Size":
                getattr(mod.properties.inputs, item.identifier).value = voxel_bu
        context.view_layer.update()
        t = read_target(temp, context.evaluated_depsgraph_get(), scale, with_weights=False)
        return Part(support.name, t.verts, t.tris, is_support=True)
    finally:
        bpy.data.objects.remove(temp, do_unlink=True)
        bpy.data.meshes.remove(mesh)


def _bake_group():
    ng = bpy.data.node_groups.get(BAKE_GROUP)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(BAKE_GROUP, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    size = ng.interface.new_socket("Voxel Size", in_out="INPUT", socket_type="NodeSocketFloat")
    size.default_value, size.min_value = 0.05, 0.001
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gin, gout = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    to_volume = ng.nodes.new("GeometryNodeMeshToVolume")
    to_volume.inputs["Resolution Mode"].default_value = "Size"
    to_mesh = ng.nodes.new("GeometryNodeVolumeToMesh")
    to_mesh.inputs["Resolution Mode"].default_value = "Grid"
    ng.links.new(gin.outputs["Geometry"], to_volume.inputs["Mesh"])
    ng.links.new(gin.outputs["Voxel Size"], to_volume.inputs["Voxel Size"])
    ng.links.new(to_volume.outputs["Volume"], to_mesh.inputs["Volume"])
    ng.links.new(to_mesh.outputs["Mesh"], gout.inputs["Geometry"])
    for i, node in enumerate((gin, to_volume, to_mesh, gout)):
        node.location = (i * 220, 0)
    return ng


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name).strip("_") or "part"


def _default_name(context) -> str:
    support = export_set(context)
    targets = support.handycactus_set.target_objects() if support is not None else []
    return safe_name(targets[0].name if targets else support.name if support else "supports")


class _ExportBase(ExportHelper):
    include_model: BoolProperty(
        name="Include Model", description="Export the targets along with the supports", default=True
    )
    apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Export the targets as evaluated (modifiers applied)",
        default=True,
    )
    bake: BoolProperty(
        name="Bake Watertight",
        description="Remesh the supports into one closed shell (slow). Slicers union overlapping "
        "shells anyway; this is for tools that need a single manifold",
        default=False,
    )
    voxel_size: FloatProperty(
        name="Voxel Size (mm)", description="Detail of the watertight bake", default=0.05, min=0.01, max=0.5
    )

    @classmethod
    def poll(cls, context):
        return export_set(context) is not None

    def invoke(self, context, event):
        self.filepath = _default_name(context) + self.filename_ext
        return super().invoke(context, event)

    def parts(self, context) -> list[Part]:
        support = export_set(context)
        return collect_parts(
            context, support, self.include_model, self.apply_modifiers, self.bake, self.voxel_size
        )


class HANDYCACTUS_OT_export_3mf(_ExportBase, Operator):
    """Export the model and its supports as one 3MF object with a part per mesh"""

    bl_idname = "handycactus.export_3mf"
    bl_label = "Export 3MF"
    bl_options = {"REGISTER"}

    filename_ext = ".3mf"
    filter_glob: StringProperty(default="*.3mf", options={"HIDDEN"})
    layout: EnumProperty(
        name="Slicer",
        description="How the parts are stored; slicers read multi-part objects differently",
        items=[
            (
                "BAMBU",
                "Bambu Studio / OrcaSlicer",
                "Components plus Bambu part names (model_settings.config)",
            ),
            ("PRUSA", "PrusaSlicer", "One mesh with PrusaSlicer volumes (Slic3r_PE_model.config)"),
            ("CORE", "Generic 3MF", "Components only, no slicer-specific metadata"),
        ],
        default="BAMBU",
    )

    def execute(self, context):
        support = export_set(context)
        try:
            parts = self.parts(context)
            name = parts[0].name if self.include_model and len(parts) > 1 else support.name
            write_3mf(self.filepath, name, parts, self.layout)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        tris = sum(len(p.tris) for p in parts)
        self.report({"INFO"}, f"Exported {len(parts)} part(s), {tris:,} triangles to {self.filepath}")
        return {"FINISHED"}


class HANDYCACTUS_OT_export_stl(_ExportBase, Operator):
    """Export the model and its supports as binary STL (one file, or one per part)"""

    bl_idname = "handycactus.export_stl"
    bl_label = "Export STL"
    bl_options = {"REGISTER"}

    filename_ext = ".stl"
    filter_glob: StringProperty(default="*.stl", options={"HIDDEN"})
    mode: EnumProperty(
        name="Files",
        items=[
            ("MERGED", "One File", "Model and supports in one STL"),
            ("SEPARATE", "One per Part", "<name>_<part>.stl for each target and the supports"),
        ],
        default="MERGED",
    )

    def execute(self, context):
        try:
            parts = self.parts(context)
            written = write_stl_files(self.filepath, parts, self.mode)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"}, f"Exported {len(written)} STL file(s): {', '.join(map(os.path.basename, written))}"
        )
        return {"FINISHED"}


def write_stl_files(filepath: str, parts: list[Part], mode: str) -> list[str]:
    if not parts:
        raise ValueError("nothing to export")
    if mode == "MERGED":
        write_stl(filepath, merge(parts, os.path.splitext(os.path.basename(filepath))[0]))
        return [filepath]
    stem, ext = os.path.splitext(filepath)
    out = []
    for p in parts:
        path = f"{stem}_{safe_name(p.name)}{ext or '.stl'}"
        write_stl(path, p)
        out.append(path)
    return out


def _menu_export(self, context):
    self.layout.operator(HANDYCACTUS_OT_export_3mf.bl_idname, text="HandyCactus Model + Supports (.3mf)")
    self.layout.operator(HANDYCACTUS_OT_export_stl.bl_idname, text="HandyCactus Model + Supports (.stl)")


classes = (HANDYCACTUS_OT_export_3mf, HANDYCACTUS_OT_export_stl)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
