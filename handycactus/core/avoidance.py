# SPDX-License-Identifier: GPL-3.0-or-later
"""Where a branch can still reach the bed from (spec section 6.4.4).

The idea of OrcaSlicer/PrusaSlicer/Cura tree supports (`TreeModelVolumes`): per height
and branch radius, the region from which some path, leaning at most the branch angle at
every step and keeping clear of the model, reaches the bed. A branch steered inside it
never gets trapped; one outside it cannot reach the bed at all (it must land, or join a
branch that can).

On a regular grid (AVOID_CELL in XY and Z, so one dilation step is the same distance
across and up):
1. occupancy from vertical-ray winding numbers (overlapping shells count once);
2. an approximate distance to the model, in cells, by repeated 3D dilation (alternating
   6- and 26-neighbourhoods, close to a sphere);
3. free space for a radius bucket: farther than radius + HARD_CLEARANCE;
4. reachable, built upward from the bed: free at the bottom level, and at each level up
   free cells above, or next to (4-neighbourhood), a reachable cell one level down. The
   sideways step is only taken on the levels where the branch cone (at 95% of the branch
   angle) has gained a whole cell, so the grid never promises a path steeper than real
   moves can follow; those still check clearance and angle exactly.
"""

from __future__ import annotations

import numpy as np

from .params import Params
from .tips import BRANCH_HARD_CLEARANCE

AVOID_CELL = 0.4  # mm, in XY and Z
RADIUS_BUCKETS = (0.2, 0.4, 0.6, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0)


class Avoidance:
    def __init__(self, obstacles, lo_xy, hi_xy, top_z: float, params: Params):
        cell = AVOID_CELL
        self.cell = cell
        self.max_radius = params.max_radius
        # Horizontal cells a branch can cover per level at the branch angle (a little under,
        # so the grid never promises a path the real moves cannot follow).
        self.cells_per_level = 0.95 * np.tan(np.radians(params.max_branch_angle))
        margin = params.max_radius + BRANCH_HARD_CLEARANCE + 2 * cell
        self.origin = np.asarray(lo_xy, float)[:2] - margin
        size = np.asarray(hi_xy, float)[:2] + margin - self.origin
        self.shape_xy = tuple(int(n) for n in np.ceil(size / cell).astype(int) + 1)
        self.levels = int(np.ceil(top_z / cell)) + 1
        nx, ny = self.shape_xy
        xs = self.origin[0] + cell * np.arange(nx)
        ys = self.origin[1] + cell * np.arange(ny)
        gx, gy = np.meshgrid(xs, ys, indexing="ij")
        zs = cell * np.arange(self.levels)
        occ = obstacles.column_occupancy(np.column_stack([gx.ravel(), gy.ravel()]), zs)
        self.occupied = occ.reshape(self.levels, nx, ny)
        self.distance = _distance_steps(self.occupied, int(np.ceil((params.max_radius + 1.0) / cell)) + 2)
        self.buckets = [r for r in RADIUS_BUCKETS if r < params.max_radius] + [params.max_radius]
        self._reach: dict[float, np.ndarray] = {}

    def _bucket(self, radius: float) -> float:
        for r in self.buckets:
            if radius <= r + 1e-9:
                return r
        return self.buckets[-1]

    def reachable_grid(self, radius: float) -> np.ndarray:
        r = self._bucket(radius)
        grid = self._reach.get(r)
        if grid is None:
            free = self.distance * self.cell > r + BRANCH_HARD_CLEARANCE
            grid = np.zeros_like(free)
            grid[0] = free[0]
            for k in range(1, self.levels):
                below = grid[k - 1]
                spread = below.copy()
                # One cell sideways on the levels where the cone has gained a whole cell.
                if int(k * self.cells_per_level) == int((k - 1) * self.cells_per_level):
                    grid[k] = free[k] & spread
                    continue
                spread[1:] |= below[:-1]
                spread[:-1] |= below[1:]
                spread[:, 1:] |= below[:, :-1]
                spread[:, :-1] |= below[:, 1:]
                grid[k] = free[k] & spread
            self._reach[r] = grid
        return grid

    def _index(self, xyz):
        p = np.asarray(xyz, float).reshape(-1, 3)
        ij = np.rint((p[:, :2] - self.origin) / self.cell).astype(int)
        k = np.clip(np.rint(p[:, 2] / self.cell).astype(int), 0, self.levels - 1)
        inside = (
            (ij[:, 0] >= 0) & (ij[:, 0] < self.shape_xy[0]) & (ij[:, 1] >= 0) & (ij[:, 1] < self.shape_xy[1])
        )
        return ij, k, inside

    def can_reach_bed(self, xyz, radius: float) -> np.ndarray:
        """(N,) bool. Outside the grid there is no model: reachable."""
        grid = self.reachable_grid(radius)
        ij, k, inside = self._index(xyz)
        out = np.ones(len(ij), bool)
        out[inside] = grid[k[inside], ij[inside, 0], ij[inside, 1]]
        return out

    def steer(self, pos, z_new: float, wish_xy, reach: float, radius: float) -> np.ndarray | None:
        """The XY at height z_new, within `reach` of pos, from which the bed is still
        reachable, nearest to `wish_xy` (the wish itself if it qualifies). None if none."""
        pos = np.asarray(pos, float)
        wish = np.asarray(wish_xy, float)
        step = wish - pos[:2]
        dist = float(np.linalg.norm(step))
        if dist > reach:
            wish = pos[:2] + step / dist * reach
        if self.can_reach_bed([wish[0], wish[1], z_new], radius)[0]:
            return wish
        grid = self.reachable_grid(radius)
        k = int(np.clip(round(z_new / self.cell), 0, self.levels - 1))
        n = int(np.ceil(reach / self.cell)) + 1
        c = np.rint((pos[:2] - self.origin) / self.cell).astype(int)
        i0, i1 = max(c[0] - n, 0), min(c[0] + n + 1, self.shape_xy[0])
        j0, j1 = max(c[1] - n, 0), min(c[1] + n + 1, self.shape_xy[1])
        if i0 >= i1 or j0 >= j1:
            return None
        ii, jj = np.nonzero(grid[k, i0:i1, j0:j1])
        if len(ii) == 0:
            return None
        cells = self.origin + self.cell * np.column_stack([ii + i0, jj + j0])
        within = np.linalg.norm(cells - pos[:2], axis=1) <= reach + 0.5 * self.cell
        if not within.any():
            return None
        cells = cells[within]
        return cells[int(np.argmin(np.linalg.norm(cells - wish, axis=1)))]


def _distance_steps(occupied: np.ndarray, max_steps: int) -> np.ndarray:
    """Approximate distance to the nearest occupied cell, in cells (capped at max_steps):
    the dilation step at which a cell is first covered. Alternating 6- and 26-neighbour
    steps approximate a sphere."""
    dist = np.full(occupied.shape, max_steps, dtype=np.int16)
    dist[occupied] = 0
    cur = occupied.copy()
    for step in range(1, max_steps):
        grown = cur.copy()
        _dilate(cur, grown, full=step % 2 == 0)
        new = grown & ~cur
        dist[new] = step
        cur = grown
        if cur.all():
            break
    return dist


def _dilate(src: np.ndarray, dst: np.ndarray, full: bool) -> None:
    offsets = (
        [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1) if (a, b, c) != (0, 0, 0)]
        if full
        else [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    )
    for off in offsets:
        s = tuple(slice(max(o, 0), n + min(o, 0)) for o, n in zip(off, src.shape, strict=True))
        d = tuple(slice(max(-o, 0), n + min(-o, 0)) for o, n in zip(off, src.shape, strict=True))
        dst[d] |= src[s]
