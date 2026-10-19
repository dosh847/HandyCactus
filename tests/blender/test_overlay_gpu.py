# SPDX-License-Identifier: GPL-3.0-or-later
"""Draws the overlay offscreen and reads pixels back.

Needs a GPU context: on Linux CI that is Mesa's software EGL
(libegl1, libegl-mesa0, libgl1-mesa-dri). Skipped where none is available.
"""

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")
gpu = pytest.importorskip("gpu")

from mathutils import Matrix  # noqa: E402

import handycactus  # noqa: E402
from handycactus.blender import overlay  # noqa: E402

SIZE = 64


@pytest.fixture(scope="module")
def gpu_ready():
    try:
        gpu.init()
    except Exception as exc:  # no display/EGL in this environment
        pytest.skip(f"no GPU context: {exc}")


@pytest.fixture
def scene(gpu_ready):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    handycactus.register()
    scene = bpy.context.scene
    scene.unit_settings.scale_length = 0.001
    yield scene
    handycactus.unregister()


def render_overlay() -> np.ndarray:
    offscreen = gpu.types.GPUOffScreen(SIZE, SIZE)
    try:
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
            # Orthographic view straight down onto x, y in [-10, 10].
            projection = Matrix.Diagonal((0.1, 0.1, -0.02, 1.0))
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_identity()
                gpu.matrix.load_projection_matrix(projection)
                overlay._draw()
            pixels = fb.read_color(0, 0, SIZE, SIZE, 4, 0, "FLOAT")
        pixels.dimensions = SIZE * SIZE * 4
        return np.array(pixels).reshape(SIZE, SIZE, 4)
    finally:
        offscreen.free()


def test_heatmap_and_contacts_draw(scene):
    bpy.ops.mesh.primitive_cube_add(size=10, location=(0, 0, 8))
    scene.handycactus.show_heatmap = True
    image = render_overlay()
    covered = image[..., 3] > 0
    # The 10 mm underside covers the middle half of a 20 mm wide view.
    assert 0.15 < covered.mean() < 0.35
    centre = image[SIZE // 2, SIZE // 2]
    assert centre[0] > 0.5 and centre[1] < 0.3  # red end of the ramp

    bpy.ops.handycactus.preview_contacts()
    scene.handycactus.show_heatmap = False
    image = render_overlay()
    green = (image[..., 1] > 0.5) & (image[..., 0] < 0.5)
    assert green.sum() > 20  # contact points
