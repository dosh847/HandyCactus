# SPDX-License-Identifier: GPL-3.0-or-later
"""mathutils.bvhtree implementation of core.spatial.InsideTester."""

from __future__ import annotations

import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from ..core.spatial import PARITY_DIRECTIONS


class BVHInsideTester:
    """Inside test in the BVH's own space; `to_local` maps query points (world mm) into it.

    Inside-ness survives any invertible affine map, so testing in object
    space against Blender's C-built BVH gives the same answer as world space.
    """

    def __init__(self, bvh: BVHTree, bounds_min, bounds_max, to_local: Matrix | None = None):
        self.bvh = bvh
        self.bounds_min = np.asarray(bounds_min, dtype=np.float64)
        self.bounds_max = np.asarray(bounds_max, dtype=np.float64)
        self._to_local = to_local
        directions = [Vector(d) for d in PARITY_DIRECTIONS]
        if to_local is not None:
            linear = to_local.to_3x3()
            directions = [(linear @ d).normalized() for d in directions]
        self._directions = directions

    @classmethod
    def from_arrays(cls, verts: np.ndarray, tris: np.ndarray) -> BVHInsideTester:
        bvh = BVHTree.FromPolygons(verts.tolist(), tris.tolist(), all_triangles=True)
        return cls(bvh, verts.min(axis=0), verts.max(axis=0))

    @classmethod
    def from_object(cls, obj, depsgraph, scale_mm: float, bounds_min, bounds_max) -> BVHInsideTester:
        """BVH of the evaluated object in object space; queries arrive as world mm."""
        bvh = BVHTree.FromObject(obj.evaluated_get(depsgraph), depsgraph)
        world_from_local = Matrix.Scale(scale_mm, 4) @ obj.evaluated_get(depsgraph).matrix_world
        return cls(bvh, bounds_min, bounds_max, world_from_local.inverted())

    def is_inside(self, points: np.ndarray, rays: int = 3) -> np.ndarray:
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        out = np.zeros(len(points), dtype=bool)
        to_local = self._to_local
        directions = self._directions[:rays]
        for i, p in enumerate(points.tolist()):
            v = Vector(p)
            out[i] = self._inside(to_local @ v if to_local is not None else v, directions)
        return out

    def _inside(self, p: Vector, directions) -> bool:
        # Ray winding number: along a ray, surface exits minus entries is positive inside
        # (majority over the rays). Works for overlapping shells, where parity does not.
        votes = sum(self._winding(p, d) > 0 for d in directions)
        return votes * 2 > len(directions)

    def _winding(self, origin: Vector, direction: Vector) -> int:
        winding = 0
        o = origin.copy()
        last = -1
        for _ in range(MAX_CROSSINGS):
            hit, normal, index, _d = self.bvh.ray_cast(o, direction)
            if hit is None:
                break
            if index != last:  # float32 BVH: stepping off a hit can land on the same face
                winding += 1 if normal.dot(direction) > 0 else -1
                last = index
            o = hit + direction * _STEP
        return winding


_STEP = 1e-4  # Blender units to step past a hit before casting again (float32-safe)
MAX_CROSSINGS = 1000


def world_bvh(obj, depsgraph, scale_mm: float) -> BVHTree:
    """BVH of the evaluated object in world space, millimetres (built in C via bmesh)."""
    import bmesh

    bm = bmesh.new()
    try:
        bm.from_object(obj, depsgraph)
        bmesh.ops.transform(bm, matrix=Matrix.Scale(scale_mm, 4) @ obj.matrix_world, verts=bm.verts)
        return BVHTree.FromBMesh(bm)
    finally:
        bm.free()


class BVHObstacles:
    """core.spatial.Obstacles over several world-space BVHs (one per target)."""

    def __init__(self, trees: list[BVHTree], bounds=None):
        self.trees = trees
        self.bounds = bounds  # (min xyz, max xyz) mm of all targets; enables avoidance

    def nearest_distance(self, points: np.ndarray) -> np.ndarray:
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        out = np.full(len(points), np.inf)
        for i, p in enumerate(points.tolist()):
            v = Vector(p)
            for tree in self.trees:
                hit = tree.find_nearest(v)
                if hit[0] is not None and hit[3] < out[i]:
                    out[i] = hit[3]
        return out

    def ray_distance(self, origins: np.ndarray, directions: np.ndarray, max_distance: float) -> np.ndarray:
        return self.ray_hit(origins, directions, max_distance)[0]

    def ray_hit(self, origins, directions, max_distance):
        origins = np.asarray(origins, dtype=np.float64).reshape(-1, 3)
        directions = np.asarray(directions, dtype=np.float64).reshape(-1, 3)
        out = np.full(len(origins), np.inf)
        normals = np.zeros((len(origins), 3))
        for i, (o, d) in enumerate(zip(origins.tolist(), directions.tolist(), strict=True)):
            vo, vd = Vector(o), Vector(d)
            for tree in self.trees:
                hit = tree.ray_cast(vo, vd, max_distance)
                if hit[0] is not None and hit[3] < out[i]:
                    out[i] = hit[3]
                    normals[i] = hit[1].normalized()
        return out, normals

    def column_occupancy(self, xy: np.ndarray, z: np.ndarray) -> np.ndarray:
        """(K, N) bool: is (xy[n], z[k]) inside any target. One upward ray walk per column
        and target, counting entries (+1, face pointing down) and exits (-1)."""
        xy = np.asarray(xy, np.float64).reshape(-1, 2)
        z = np.asarray(z, np.float64)
        out = np.zeros((len(z), len(xy)), dtype=bool)
        up = Vector((0.0, 0.0, 1.0))
        z0 = float(z.min()) - 1.0
        z1 = float(z.max())
        for n, (x, y) in enumerate(xy.tolist()):
            for tree in self.trees:
                heights, signs = [], []
                o = Vector((x, y, z0))
                last = -1
                for _ in range(MAX_CROSSINGS):
                    hit, normal, index, _d = tree.ray_cast(o, up)
                    if hit is None or hit.z > z1:
                        break
                    if index != last:
                        heights.append(hit.z)
                        signs.append(1 if normal.z < 0 else -1)
                        last = index
                    o = hit + up * _STEP
                if not heights:
                    continue
                wind = np.cumsum(signs)  # winding just above each crossing
                k = np.searchsorted(np.asarray(heights), z, side="right") - 1
                out[:, n] |= (k >= 0) & (wind[np.maximum(k, 0)] > 0)
        return out


def first_hit(trees: list[BVHTree], origin, direction, max_distance: float = 1e9):
    """Nearest hit over several BVHs: (distance, location, normal, tree index) or None."""
    best = None
    o, d = Vector(origin), Vector(direction).normalized()
    for i, tree in enumerate(trees):
        location, normal, _index, distance = tree.ray_cast(o, d, max_distance)
        if location is not None and (best is None or distance < best[0]):
            best = (distance, np.array(location), np.array(normal.normalized()), i)
    return best
