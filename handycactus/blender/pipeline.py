# SPDX-License-Identifier: GPL-3.0-or-later
"""Glue between Blender objects and the core pipeline."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import bpy
import numpy as np
from mathutils import Matrix

from ..core import checks, detection, editing, reach, relocate, tips
from ..core.graph import CONTACT, FOOT, LANDING
from ..core.mesh_data import MeshData, combine
from . import gn_builder, graph_io
from .mesh_io import mesh_targets, read_target, scene_mm_per_bu
from .props import settings_to_params
from .spatial_bvh import BVHInsideTester, BVHObstacles, world_bvh

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

    Every closed target needs one: besides overlaps between parts, the
    bridging test asks whether a target's own material lies below a sample.
    """
    depsgraph = context.evaluated_depsgraph_get()
    scale = scene_mm_per_bu(context.scene)
    testers = []
    for i, ob in enumerate(objects):
        if not mesh.is_nearly_closed(i):
            testers.append(None)
            continue
        lo, hi = _bounds(mesh, i)
        testers.append(BVHInsideTester.from_object(ob, depsgraph, scale, lo, hi))
    return testers


def _bounds(mesh: MeshData, i: int):
    v = mesh.verts[mesh.vert_target == i]
    return v.min(axis=0), v.max(axis=0)


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


class BelowBed(ValueError):
    pass


@dataclass
class GenerateResult:
    support: object  # the supports bpy Object
    tips: int
    trees: int
    unreachable: int
    unreachable_points: np.ndarray  # (U, 3) mm, contacts that got no support
    seconds: float


def support_name(objects) -> str:
    return f"{objects[0].name}_HC_Supports"


def generate(context, objects, support=None) -> GenerateResult:
    """Detect, build tips, route and write a supports object for the target set.

    `support` is an existing supports object to rebuild in place (Regenerate). Its
    locked trees (manually edited ones, or locked by hand) are kept as they are; new
    contacts within half a tip spacing of a kept tip are dropped.
    """
    start = time.perf_counter()
    objects = mesh_targets(objects)
    if not objects:
        raise ValueError("no mesh targets")
    scene = context.scene
    scale = scene_mm_per_bu(scene)
    params = settings_to_params(scene.handycactus)
    problems = params.problems()
    if problems:
        raise ValueError(problems[0])

    mesh = read_set(context, objects)
    lowest = lowest_point_mm(mesh)
    if lowest < -BELOW_BED_TOLERANCE_MM:
        raise BelowBed(f"Targets are {-lowest:.2f} mm below the bed; use Place on Bed first")

    depsgraph = context.evaluated_depsgraph_get()
    trees = [world_bvh(ob, depsgraph, scale) for ob in objects]
    found = detection.detect(mesh, params, _testers_from(trees, mesh))  # may raise TooManyContacts
    obstacles = BVHObstacles(trees)
    kept = editing.locked_part(read_support_graph(context, support)) if support is not None else None
    contacts = found.contacts
    if kept is not None and len(kept):
        kept_tips = [
            editing.tip_surface(kept, t, params)[0] for t in np.unique(kept.tip[kept.kind == CONTACT])
        ]
        contacts = editing.drop_contacts_near(
            contacts, np.array(kept_tips).reshape(-1, 3), 0.5 * params.tip_spacing
        )
    built = tips.build_tips(contacts, params, obstacles)
    settled = relocate.relocate_failed(contacts, built, mesh, found.support_faces, params, obstacles)
    built = settled.tips
    routed = reach.route_with_retry(built, params, obstacles)
    graph = routed.graph
    if kept is not None and len(kept):
        graph.tip = np.where(graph.tip >= 0, graph.tip + editing.next_tip_id(kept), -1)
        graph = editing.concat(kept, graph)
        graph.renumber_trees()

    if support is None:
        support = _new_support_object(context, objects)
    graph_io.write_graph(support.data, graph, scale)
    mod = gn_builder.ensure_modifier(support)
    gn_builder.set_input(mod, "Profile Resolution", params.profile_resolution)

    info = support.handycactus_set
    info.is_support = True
    info.targets.clear()
    for ob in objects:
        info.targets.add().obj = ob
    info.set_params(params)
    unreachable = built.surface[routed.unreachable_tips]
    _store_stats(info, graph, unreachable)
    info.set_findings(checks.run_checks(graph, params, mesh, obstacles, unreachable, found.support_faces))
    remember_support_faces(support, objects, found.support_faces)
    info.seconds = time.perf_counter() - start
    return GenerateResult(
        support=support,
        tips=info.tip_count,
        trees=info.tree_count,
        unreachable=info.unreachable_count,
        unreachable_points=built.surface[routed.unreachable_tips],
        seconds=info.seconds,
    )


def _store_stats(info, graph, unreachable) -> None:
    info.tip_count = int((graph.kind == CONTACT).sum())
    info.tree_count = int((graph.kind == FOOT).sum() + (graph.kind == LANDING).sum())
    info.landing_count = int((graph.kind == LANDING).sum())
    info.locked_count = len(np.unique(graph.tree[graph.locked])) if len(graph) else 0
    info.unreachable_count = len(unreachable)
    info.unreachable_json = json.dumps(np.round(np.asarray(unreachable).reshape(-1, 3), 4).tolist())


class EditSession:
    """What edits of one support set need, built once: targets, params and the BVHs.

    The Edit Supports tool keeps one for its whole run; standalone operators build one
    per call.
    """

    def __init__(self, context, support):
        info = support.handycactus_set
        self.support_name = support.name
        self.objects = info.target_objects()
        if not self.objects:
            raise ValueError("This support set's targets no longer exist")
        self.scale = scene_mm_per_bu(context.scene)
        self.params = info.params() or settings_to_params(context.scene.handycactus)
        depsgraph = context.evaluated_depsgraph_get()
        self.obstacles = BVHObstacles([world_bvh(ob, depsgraph, self.scale) for ob in self.objects])

    def target_index(self, obj) -> int:
        names = [ob.name for ob in self.objects]
        return names.index(obj.name) if obj is not None and obj.name in names else -1


_active_session: EditSession | None = None


def set_active_session(session: EditSession | None) -> None:
    global _active_session
    _active_session = session


def session_for(context, support) -> EditSession:
    if _active_session is not None and _active_session.support_name == support.name:
        return _active_session
    return EditSession(context, support)


def apply_edit(context, support, edit) -> editing.EditResult:
    """Run `edit(graph, session) -> EditResult` on a support set and store the result.

    On success the graph is written back, stats refreshed and the quick checks re-run
    (coverage needs a full detection pass; Check Supports runs it).
    """
    session = session_for(context, support)
    graph = read_support_graph(context, support)
    result = edit(graph, session)
    if not result.ok:
        return result
    info = support.handycactus_set
    graph_io.write_graph(support.data, result.graph, session.scale)
    unreachable = info.unreachable_points()
    contacts = result.graph.pos[result.graph.kind == CONTACT]
    if len(unreachable) and len(contacts):
        # A tip added where a contact was unreachable resolves it.
        near = np.linalg.norm(unreachable[:, None] - contacts[None], axis=2).min(axis=1) < 0.5
        unreachable = unreachable[~near]
    _store_stats(info, result.graph, unreachable)
    info.set_findings(checks.run_checks(result.graph, session.params, None, session.obstacles, unreachable))
    return result


def _testers_from(trees, mesh: MeshData) -> list:
    """Inside testers on world-space BVHs; None for open targets."""
    testers = []
    for i, tree in enumerate(trees):
        if not mesh.is_nearly_closed(i):
            testers.append(None)
        else:
            lo, hi = _bounds(mesh, i)
            testers.append(BVHInsideTester(tree, lo, hi))
    return testers


def _new_support_object(context, objects):
    name = support_name(objects)
    mesh = bpy.data.meshes.new(name)
    support = bpy.data.objects.new(name, mesh)
    collections = objects[0].users_collection
    (collections[0] if collections else context.scene.collection).objects.link(support)
    return support


def remove_support(support) -> None:
    mesh = support.data
    bpy.data.objects.remove(support, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def check_support(context, support) -> list:
    """Re-run the printability checks on an existing support set against its targets."""
    info = support.handycactus_set
    objects = info.target_objects()
    scale = scene_mm_per_bu(context.scene)
    params = info.params() or settings_to_params(context.scene.handycactus)
    graph = read_support_graph(context, support)
    mesh = support_faces = obstacles = None
    if objects:
        mesh = read_set(context, objects)
        depsgraph = context.evaluated_depsgraph_get()
        trees = [world_bvh(ob, depsgraph, scale) for ob in objects]
        obstacles = BVHObstacles(trees)
        # Detection's face mask (not the angle-only estimate) so buried faces are not gaps.
        support_faces = detection.detect(mesh, params, _testers_from(trees, mesh)).support_faces
        remember_support_faces(support, objects, support_faces)
    findings = checks.run_checks(graph, params, mesh, obstacles, info.unreachable_points(), support_faces)
    info.set_findings(findings)
    return findings


def read_support_graph(context, support):
    return graph_io.read_graph(support.data, scene_mm_per_bu(context.scene))


def read_support_graph_scaled(support, scale: float):
    return graph_io.read_graph(support.data, scale)


# Detection's face mask per support set (by name), with the target names it was made for.
# In memory only: after a reload the heatmap falls back to the angle-only estimate.
_support_faces: dict[str, tuple[list[str], np.ndarray]] = {}


def remember_support_faces(support, objects, mask: np.ndarray) -> None:
    _support_faces[support.name] = ([ob.name for ob in objects], mask)


def support_faces_for(support, names: list[str]) -> np.ndarray | None:
    """The remembered face mask of a support set, if it was made for exactly `names`."""
    entry = _support_faces.get(support.name)
    return entry[1] if entry is not None and entry[0] == list(names) else None
