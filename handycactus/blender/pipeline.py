# SPDX-License-Identifier: GPL-3.0-or-later
"""Glue between Blender objects and the core pipeline."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from mathutils import Matrix

from ..core import detection
from ..core.mesh_data import MeshData, combine
from .mesh_io import mesh_targets, read_target, scene_mm_per_bu
from .props import settings_to_params
from .spatial_bvh import BVHInsideTester

BELOW_BED_TOLERANCE_MM = 0.01


@dataclass
class DetectionRun:
    names: list[str]
    mesh: MeshData
    result: detection.DetectionResult
    seconds: float


def read_set(context, objects) -> MeshData:
    scale = scene_mm_per_bu(context.scene)
    depsgraph = context.evaluated_depsgraph_get()
    return combine([read_target(ob, depsgraph, scale) for ob in objects])


def inside_testers(context, objects, mesh: MeshData) -> list:
    """One BVH inside tester per closed target (None for open meshes).

    Only targets whose bounds overlap another target's can ever be asked,
    so the others skip the BVH build.
    """
    depsgraph = context.evaluated_depsgraph_get()
    scale = scene_mm_per_bu(context.scene)
    bounds = [_bounds(mesh, i) for i in range(mesh.target_count)]
    testers = []
    for i, ob in enumerate(objects):
        if mesh.non_manifold_edge_count(i):
            testers.append(None)
            continue
        lo, hi = bounds[i]
        overlaps = any(
            np.all(lo <= hi_j) and np.all(lo_j <= hi) for j, (lo_j, hi_j) in enumerate(bounds) if j != i
        )
        testers.append(
            BVHInsideTester.from_object(ob, depsgraph, scale, lo, hi) if overlaps else _NeverInside(lo, hi)
        )
    return testers


def _bounds(mesh: MeshData, i: int):
    v = mesh.verts[mesh.vert_target == i]
    return v.min(axis=0), v.max(axis=0)


class _NeverInside:
    """A closed target no other target reaches; it is never asked."""

    def __init__(self, lo, hi):
        self.bounds_min, self.bounds_max = lo, hi

    def is_inside(self, points):
        return np.zeros(len(points), dtype=bool)


def lowest_point_mm(mesh: MeshData) -> float:
    return float(mesh.verts[:, 2].min()) if len(mesh.verts) else 0.0


def run_detection(context, objects) -> DetectionRun:
    start = time.perf_counter()
    objects = mesh_targets(objects)
    mesh = read_set(context, objects)
    params = settings_to_params(context.scene.handycactus)
    result = detection.detect(mesh, params, inside_testers(context, objects, mesh))
    return DetectionRun([ob.name for ob in objects], mesh, result, time.perf_counter() - start)


def place_on_bed(context, objects, lift_mm: float) -> float:
    """Move the set rigidly so its lowest point sits at `lift_mm`. Returns the Z move in mm."""
    objects = mesh_targets(objects)
    if not objects:
        return 0.0
    mesh = read_set(context, objects)
    delta_mm = lift_mm - lowest_point_mm(mesh)
    delta_bu = delta_mm / scene_mm_per_bu(context.scene)
    chosen = set(objects)
    move = Matrix.Translation((0.0, 0.0, delta_bu))
    for ob in objects:
        if _has_ancestor_in(ob, chosen):
            continue  # moves with its parent
        ob.matrix_world = move @ ob.matrix_world
    return delta_mm


def _has_ancestor_in(ob, chosen) -> bool:
    parent = ob.parent
    while parent is not None:
        if parent in chosen:
            return True
        parent = parent.parent
    return False
