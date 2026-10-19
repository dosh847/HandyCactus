# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport overlay: overhang heatmap and contact preview (spec section 8).

Until M2 creates support sets, the heatmap follows the selected mesh
objects. Recomputation happens on a 0.25 s timer (at most 4 times a second),
never inside the draw callback, which only uploads finished arrays.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import bpy
import numpy as np
from bpy.app.handlers import persistent

from ..core import detection, heatmap
from .mesh_io import mesh_targets, scene_mm_per_bu
from .pipeline import read_set
from .props import settings_to_params

TICK_SECONDS = 0.25
# The heatmap lies exactly on the surface; pull it toward the camera by this
# fraction of its view distance so it wins the depth test at any model scale
# and zoom without showing through nearby geometry.
DEPTH_PULL = 1e-3
POINT_SIZE = 6.0
SAMPLE_COLOR = (0.2, 0.9, 0.3, 1.0)
ISLAND_COLOR = (0.1, 0.7, 1.0, 1.0)
BLOCKED_COLOR = (0.55, 0.55, 0.55, 1.0)
UNREACHABLE_COLOR = (1.0, 0.1, 0.1, 1.0)


@dataclass
class _State:
    draw_handle: object = None
    timer_running: bool = False
    signature: tuple = ()
    dirty: bool = True
    geometry_dirty: bool = True
    preview: object = None  # pipeline.DetectionRun
    heat_positions: np.ndarray | None = None  # Blender units
    heat_colors: np.ndarray | None = None
    point_positions: np.ndarray | None = None
    unreachable: np.ndarray | None = None  # (U, 3) mm, from the last Generate
    point_colors: np.ndarray | None = None
    batches: dict = field(default_factory=dict)
    batches_stale: bool = True
    face_count: int = 0
    gap_face_count: int = 0
    mesh: object = None  # cached MeshData for the current set
    mesh_key: tuple = ()


state = _State()


def set_preview(run) -> None:
    state.preview = run
    state.dirty = state.geometry_dirty = True
    _ensure_running()
    _rebuild()


def set_unreachable(points_mm) -> None:
    """Red markers for contacts the last Generate could not support (None clears)."""
    state.unreachable = None if points_mm is None else np.asarray(points_mm, dtype=np.float64).reshape(-1, 3)
    if state.unreachable is not None and len(state.unreachable):
        _ensure_running()
    state.dirty = True
    _rebuild()
    _stop_if_idle()


def clear_preview() -> None:
    state.preview = None
    state.point_positions = state.point_colors = None
    state.dirty = True
    state.batches_stale = True
    _rebuild()
    _stop_if_idle()


def invalidate() -> None:
    """Units changed: cached geometry and any preview are stale."""
    state.preview = None
    state.point_positions = state.point_colors = None
    state.mesh = None
    state.dirty = state.geometry_dirty = True
    state.batches_stale = True
    if state.timer_running:
        _rebuild()


def sync(scene) -> None:
    """Start or stop the overlay to match the scene's toggle."""
    if scene.handycactus.show_heatmap:
        state.dirty = state.geometry_dirty = True
        _ensure_running()
        _rebuild()
    else:
        state.heat_positions = state.heat_colors = None
        state.face_count = state.gap_face_count = 0
        state.batches_stale = True
        _tag_redraw()
        _stop_if_idle()


def current_set(context=None) -> list:
    context = context or bpy.context
    view_layer = getattr(context, "view_layer", None)
    if view_layer is None:
        return []
    return mesh_targets(view_layer.objects.selected)


def _has_points() -> bool:
    return state.preview is not None or (state.unreachable is not None and len(state.unreachable) > 0)


def _enabled(scene) -> bool:
    return scene is not None and scene.handycactus.show_heatmap


def _signature(scene, objects) -> tuple:
    p = settings_to_params(scene.handycactus)
    return (
        tuple(ob.name for ob in objects),
        p.overhang_angle,
        p.bed_ignore_height,
        p.tip_spacing,
        scene_mm_per_bu(scene),
    )


def _rebuild() -> None:
    context = bpy.context
    scene = context.scene
    objects = current_set(context)
    state.signature = _signature(scene, objects)
    geometry_dirty = state.geometry_dirty
    state.dirty = state.geometry_dirty = False
    scale = scene_mm_per_bu(scene)

    preview = state.preview
    positions, colors = [], []
    if preview is not None:
        c = preview.result.contacts
        blocked = preview.result.blocked_islands
        positions += [c.positions, blocked]
        colors += [
            np.where(c.is_island[:, None], ISLAND_COLOR, SAMPLE_COLOR),
            np.tile(BLOCKED_COLOR, (len(blocked), 1)),
        ]
    if state.unreachable is not None and len(state.unreachable):
        positions.append(state.unreachable)
        colors.append(np.tile(UNREACHABLE_COLOR, (len(state.unreachable), 1)))
    if positions:
        state.point_positions = np.concatenate(positions) / scale
        state.point_colors = np.concatenate(colors)
    else:
        state.point_positions = state.point_colors = None

    if _enabled(scene) and objects:
        # Geometry is re-read only when the set or the scale changed or an object was edited;
        # parameter changes reuse the cached mesh.
        mesh_key = (state.signature[0], scale)
        if state.mesh is None or state.mesh_key != mesh_key or geometry_dirty:
            state.mesh = read_set(context, objects)
            state.mesh_key = mesh_key
        mesh = state.mesh
        same_set = preview is not None and preview.names == [ob.name for ob in objects]
        contacts = preview.result.contacts.positions if same_set else None
        h = heatmap.build(mesh, settings_to_params(scene.handycactus), contacts)
        state.heat_positions = h.positions / scale
        state.heat_colors = h.colors
        state.face_count, state.gap_face_count = h.face_count, h.gap_face_count
    else:
        state.heat_positions = state.heat_colors = None
        state.face_count = state.gap_face_count = 0
        state.mesh = None
    state.batches_stale = True
    _tag_redraw()


def _tick():
    scene = bpy.context.scene
    if not (_enabled(scene) or _has_points()):
        state.timer_running = False
        _stop_if_idle(from_timer=True)
        return None  # returning None unregisters this timer
    if state.dirty or _signature(scene, current_set()) != state.signature:
        _rebuild()
    return TICK_SECONDS


@persistent
def _on_depsgraph_update(scene, depsgraph):
    if not state.timer_running:
        return
    names = set(state.signature[0]) if state.signature else set()
    if state.preview is not None:
        names |= set(state.preview.names)
    for update in depsgraph.updates:
        ob = update.id
        if isinstance(ob, bpy.types.Object) and ob.name in names:
            if update.is_updated_geometry or update.is_updated_transform:
                state.dirty = state.geometry_dirty = True
                if state.preview is not None and ob.name in state.preview.names:
                    # The contacts no longer match the model.
                    state.preview = None
                    state.point_positions = state.point_colors = None
                return


@persistent
def _on_load_post(_filepath):
    state.preview = None
    state.point_positions = state.point_colors = None
    state.heat_positions = state.heat_colors = None
    state.batches_stale = True
    if bpy.context.scene is not None:
        sync(bpy.context.scene)


def _ensure_running() -> None:
    if state.draw_handle is None:
        state.draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")
    if not state.timer_running:
        state.timer_running = True
        bpy.app.timers.register(_tick, first_interval=TICK_SECONDS, persistent=True)


def _stop_if_idle(from_timer: bool = False) -> None:
    scene = bpy.context.scene
    if _enabled(scene) or _has_points():
        return
    if state.draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(state.draw_handle, "WINDOW")
        state.draw_handle = None
    if not from_timer and bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    state.timer_running = False
    state.batches.clear()


def _tag_redraw() -> None:
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


_heat_shader = None


def _get_heat_shader():
    """FLAT_COLOR with a view-relative depth pull (see DEPTH_PULL)."""
    global _heat_shader
    if _heat_shader is None:
        import gpu

        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant("MAT4", "modelViewMatrix")
        info.push_constant("MAT4", "projectionMatrix")
        info.push_constant("FLOAT", "depthPull")
        info.push_constant("FLOAT", "isPerspective")
        info.vertex_in(0, "VEC3", "pos")
        info.vertex_in(1, "VEC4", "color")
        iface = gpu.types.GPUStageInterfaceInfo("hc_heat_iface")
        iface.flat("VEC4", "finalColor")
        info.vertex_out(iface)
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source(
            "void main() {"
            "  vec4 p = modelViewMatrix * vec4(pos, 1.0);"
            # Perspective: slide along the eye ray (screen position unchanged).
            # Orthographic: move along view Z only.
            "  if (isPerspective > 0.5) { p.xyz *= (1.0 - depthPull); }"
            "  else { p.z += depthPull * abs(p.z); }"
            "  gl_Position = projectionMatrix * p;"
            "  finalColor = color;"
            "}"
        )
        info.fragment_source("void main() { fragColor = finalColor; }")
        _heat_shader = gpu.shader.create_from_info(info)
    return _heat_shader


def _make_batches() -> None:
    import gpu
    from gpu_extras.batch import batch_for_shader

    state.batches.clear()
    if state.heat_positions is not None and len(state.heat_positions):
        shader = _get_heat_shader()
        batch = batch_for_shader(
            shader,
            "TRIS",
            {"pos": state.heat_positions.astype(np.float32), "color": state.heat_colors.astype(np.float32)},
        )
        state.batches["heat"] = (shader, batch)
    if state.point_positions is not None and len(state.point_positions):
        shader = gpu.shader.from_builtin("POINT_FLAT_COLOR")
        batch = batch_for_shader(
            shader,
            "POINTS",
            {"pos": state.point_positions.astype(np.float32), "color": state.point_colors.astype(np.float32)},
        )
        state.batches["points"] = (shader, batch)
    state.batches_stale = False


def _draw() -> None:
    import gpu

    if state.batches_stale:
        _make_batches()
    if not state.batches:
        return
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    if "heat" in state.batches:
        shader, batch = state.batches["heat"]
        projection = gpu.matrix.get_projection_matrix()
        shader.bind()
        shader.uniform_float("modelViewMatrix", gpu.matrix.get_model_view_matrix())
        shader.uniform_float("projectionMatrix", projection)
        shader.uniform_float("depthPull", DEPTH_PULL)
        shader.uniform_float("isPerspective", 1.0 if abs(projection[3][3]) < 0.5 else 0.0)
        batch.draw(shader)
    if "points" in state.batches:
        gpu.state.point_size_set(POINT_SIZE)
        gpu.state.depth_test_set("ALWAYS")  # contacts sit on the surface; always show them
        shader, batch = state.batches["points"]
        batch.draw(shader)
        gpu.state.point_size_set(1.0)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


def _draw_overlay_popover(self, context):
    layout = self.layout
    layout.label(text="HandyCactus")
    layout.prop(context.scene.handycactus, "show_heatmap", text="Overhang Heatmap")


def contact_summary(run) -> str:
    c = run.result.contacts
    islands = int(np.count_nonzero(c.kind != detection.SAMPLE))
    return f"{len(c)} contacts ({islands} islands) in {run.seconds:.2f} s"


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.types.VIEW3D_PT_overlay.append(_draw_overlay_popover)


def unregister():
    bpy.types.VIEW3D_PT_overlay.remove(_draw_overlay_popover)
    for handlers, fn in (
        (bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
        (bpy.app.handlers.load_post, _on_load_post),
    ):
        if fn in handlers:
            handlers.remove(fn)
    if state.draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(state.draw_handle, "WINDOW")
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    state.__init__()
