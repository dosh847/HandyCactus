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


def draw_occluder(z: float, fan: bool = False) -> None:
    """An opaque grey square at height z, written to depth.

    `fan` triangulates it as a fan around an off-centre point, so its depth
    interpolation differs from the mesh drawn on top of it.
    """
    from gpu_extras.batch import batch_for_shader

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    quad = [(-20, -20, z), (20, -20, z), (20, 20, z), (-20, -20, z), (20, 20, z), (-20, 20, z)]
    if fan:
        c = (1.3, -0.7, z)
        ring = [(-20, -20, z), (20, -20, z), (20, 20, z), (-20, 20, z)]
        quad = [p for i in range(4) for p in (c, ring[i], ring[(i + 1) % 4])]
    batch = batch_for_shader(shader, "TRIS", {"pos": quad})
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(True)
    shader.uniform_float("color", (0.5, 0.5, 0.5, 1.0))
    batch.draw(shader)
    gpu.state.depth_mask_set(False)


def render_overlay(occluder_z: float | None = None) -> np.ndarray:
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
                if occluder_z is not None:
                    draw_occluder(occluder_z)
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


def _perspective(fov_deg=50.0, near=0.01, far=1000.0) -> Matrix:
    f = 1.0 / np.tan(np.radians(fov_deg) / 2)
    return Matrix(
        (
            (f, 0, 0, 0),
            (0, f, 0, 0),
            (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)),
            (0, 0, -1, 0),
        )
    )


def render_perspective(occluder_z: float) -> np.ndarray:
    """Camera 40 units above, looking down at an angle, like the viewport (not orthographic)."""
    view = Matrix.Rotation(np.radians(25), 4, "X") @ Matrix.Translation((0, -15, -40))
    offscreen = gpu.types.GPUOffScreen(SIZE * 2, SIZE * 2)
    try:
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_matrix(view)
                gpu.matrix.load_projection_matrix(_perspective())
                draw_occluder(occluder_z, fan=True)
                overlay._draw()
            pixels = fb.read_color(0, 0, SIZE * 2, SIZE * 2, 4, 0, "FLOAT")
        pixels.dimensions = SIZE * SIZE * 16
        return np.array(pixels).reshape(SIZE * 2, SIZE * 2, 4)
    finally:
        offscreen.free()


def heat_and_grey(image):
    heat = (image[..., 0] > 0.6) & (image[..., 1] < 0.4)
    grey = np.all(np.abs(image[..., :3] - 0.5) < 0.02, axis=-1)
    return heat, grey


def test_heatmap_wins_against_the_surface_it_lies_on(scene):
    # Subdivided underside at z = 3, triangulated unlike the occluder: in perspective
    # their depths differ by rounding, which is what striped the heatmap in the viewport.
    bpy.ops.mesh.primitive_cube_add(size=10, location=(0, 0, 8))
    mod = bpy.context.active_object.modifiers.new("subdiv", "SUBSURF")
    mod.subdivision_type, mod.levels = "SIMPLE", 3
    scene.handycactus.show_heatmap = True
    image = render_perspective(occluder_z=3.0)
    heat, grey = heat_and_grey(image)
    assert heat.sum() > 200
    # Inside the underside's footprint nothing grey may poke through.
    rows, cols = np.nonzero(heat)
    box = (slice(rows.min() + 2, rows.max() - 2), slice(cols.min() + 2, cols.max() - 2))
    assert grey[box].sum() == 0


def test_heatmap_does_not_show_through_nearer_geometry(scene):
    bpy.ops.mesh.primitive_cube_add(size=10, location=(0, 0, 8))
    scene.handycactus.show_heatmap = True
    heat, _ = heat_and_grey(render_perspective(occluder_z=3.5))  # 0.5 units in front
    assert heat.sum() == 0
