# SPDX-License-Identifier: GPL-3.0-or-later
"""Screen-space picking for the Edit Supports tool (spec section 7)."""

from __future__ import annotations

import numpy as np

PICK_RADIUS_PX = 12.0


def project(
    points: np.ndarray, perspective: np.ndarray, width: int, height: int
) -> tuple[np.ndarray, np.ndarray]:
    """World points (N, 3) through a 4x4 view-projection matrix to region pixels.

    Returns (pixels (N, 2), visible (N,)); points behind the camera are not visible.
    """
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    h = np.hstack([points, np.ones((len(points), 1))]) @ np.asarray(perspective, dtype=np.float64).T
    w = h[:, 3]
    visible = w > 1e-9
    ndc = h[:, :2] / np.where(visible, w, 1.0)[:, None]
    px = np.column_stack([(ndc[:, 0] + 1) * 0.5 * width, (ndc[:, 1] + 1) * 0.5 * height])
    return px, visible


def pick(pixels: np.ndarray, visible: np.ndarray, mouse, radius: float = PICK_RADIUS_PX) -> int:
    """Index of the visible pixel position nearest the mouse within `radius`, else -1."""
    if len(pixels) == 0:
        return -1
    d = np.linalg.norm(pixels - np.asarray(mouse, dtype=np.float64), axis=1)
    d[~visible] = np.inf
    i = int(np.argmin(d))
    return i if d[i] <= radius else -1
