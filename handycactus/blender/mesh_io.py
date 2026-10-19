# SPDX-License-Identifier: GPL-3.0-or-later
"""Read target objects into core TargetMesh data (mm, world space)."""

from __future__ import annotations

import numpy as np

from ..core.mesh_data import TargetMesh
from ..core.units import mm_per_bu

FORCE_GROUP = "HC_Force"
BLOCK_GROUP = "HC_Block"


def scene_mm_per_bu(scene) -> float:
    return mm_per_bu(scene.unit_settings.scale_length)


def read_target(obj, depsgraph, scale_mm: float, with_weights: bool = True) -> TargetMesh:
    """Evaluated (modifiers applied) triangles of a mesh object in world-space mm."""
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()
    try:
        mesh.calc_loop_triangles()
        n_verts = len(mesh.vertices)
        # Native dtypes make foreach_get a plain copy; convert once afterwards.
        co = np.empty(n_verts * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3).astype(np.float64)
        tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", tris)
        tris = tris.reshape(-1, 3).astype(np.int64)

        m = np.array(obj_eval.matrix_world, dtype=np.float64)
        verts = (co @ m[:3, :3].T + m[:3, 3]) * scale_mm
        if np.linalg.det(m[:3, :3]) < 0:
            tris = tris[:, [0, 2, 1]]  # mirrored object: keep normals outward

        force = block = None
        if with_weights:
            force = _group_weights(obj_eval, mesh, FORCE_GROUP)
            block = _group_weights(obj_eval, mesh, BLOCK_GROUP)
        return TargetMesh(obj.name, verts, tris, force, block)
    finally:
        obj_eval.to_mesh_clear()


def _group_weights(obj_eval, mesh, name: str) -> np.ndarray | None:
    group = obj_eval.vertex_groups.get(name)
    if group is None:
        return None
    index = group.index
    weights = np.zeros(len(mesh.vertices))
    # Vertex groups have no bulk accessor; only painted objects pay for this loop.
    for v in mesh.vertices:
        for g in v.groups:
            if g.group == index:
                weights[v.index] = g.weight
                break
    return weights


def mesh_targets(objects) -> list:
    """Mesh objects from a selection, in a stable (name) order."""
    return sorted((ob for ob in objects if ob.type == "MESH"), key=lambda ob: ob.name)
