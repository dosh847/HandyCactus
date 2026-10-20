# SPDX-License-Identifier: GPL-3.0-or-later
"""Golden-print kit (spec section 13): the test sculpt at 32, 42 and 50 mm, each with its
preset's supports, as a Bambu/Orca 3MF ready to slice, plus a stats table.

    python tools/golden_prints.py [--out dist/golden] [--model PATH] [--render]

Runs with the `bpy` module (no Blender binary). --render also writes a Workbench PNG of
each set, which doubles as the extensions.blender.org screenshots. Print the 3MFs and
fill in docs/release/GOLDEN_PRINTS.md.
"""

from __future__ import annotations

import argparse
import math
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_MODEL = ROOT / "tests" / "fixtures" / "yks-lich-base-54mm" / "lich-base-54mm_reduced.stl"
SOURCE_SCALE_MM = 54.0  # the sculpt's scale; sizes scale by <preset scale> / this
SCALES = (("MINI_32", 32.0), ("MINI_42", 42.0), ("MINI_50", 50.0))


def build(model: pathlib.Path, preset: str, scale_mm: float, source_mm: float = SOURCE_SCALE_MM):
    import bpy

    from handycactus.blender import pipeline
    from handycactus.blender.props import apply_preset

    bpy.ops.wm.read_factory_settings(use_empty=True)
    import handycactus

    handycactus.register()
    bpy.ops.wm.stl_import(filepath=str(model))
    ob = bpy.context.selected_objects[0]
    ob.scale = [scale_mm / source_mm] * 3
    bpy.context.view_layer.update()
    settings = bpy.context.scene.handycactus
    settings.preset = preset
    apply_preset(settings, preset)
    pipeline.place_on_bed(bpy.context, [ob], settings.lift_height)
    bpy.context.view_layer.update()
    result = pipeline.generate(bpy.context, [ob])
    return ob, result


def export(ob, result, path: pathlib.Path) -> None:
    import bpy

    from handycactus.blender.export_ops import collect_parts
    from handycactus.export.threemf import write_3mf

    parts = collect_parts(bpy.context, result.support, riser=True)
    write_3mf(str(path), ob.name, parts, "BAMBU")


def render(path: pathlib.Path) -> None:
    import bpy
    from mathutils import Vector

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_cavity = True
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("HC_World")
    scene.world = world
    world.color = (0.05, 0.05, 0.06)
    for ob in scene.objects:
        if ob.type == "MESH":
            support = ob.handycactus_set.is_support
            ob.color = (0.35, 0.75, 0.45, 1.0) if support else (0.78, 0.74, 0.68, 1.0)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    corners = [
        ob.evaluated_get(depsgraph).matrix_world @ Vector(c)
        for ob in scene.objects
        if ob.type == "MESH"
        for c in ob.evaluated_get(depsgraph).bound_box
    ]
    lo = Vector([min(c[i] for c in corners) for i in range(3)])
    hi = Vector([max(c[i] for c in corners) for i in range(3)])
    centre, size = (lo + hi) / 2, (hi - lo).length
    cam_data = bpy.data.cameras.new("HC_Camera")
    cam_data.lens = 50
    cam = bpy.data.objects.new("HC_Camera", cam_data)
    scene.collection.objects.link(cam)
    direction = Vector((math.cos(math.radians(-60)), math.sin(math.radians(-60)), 0.45)).normalized()
    cam.location = centre + direction * size * 2.1
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.clip_end = size * 10
    scene.camera = cam
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def one(
    model: pathlib.Path, preset: str, scale_mm: float, source_mm: float, out: pathlib.Path, with_render: bool
) -> str:
    """One scale; returns its stats row."""
    ob, result = build(model, preset, scale_mm, source_mm)
    info = result.support.handycactus_set
    stem = f"{model.stem}_{int(scale_mm)}mm"
    export(ob, result, out / f"{stem}.3mf")
    if with_render:
        render(out / f"{stem}.png")
    return (
        f"| {int(scale_mm)} mm | {preset} | {info.tip_count} | {info.unreachable_count} | "
        f"{info.tree_count - info.landing_count} | {info.landing_count} | {result.seconds:.1f} s |"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "dist" / "golden"))
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--source-scale", type=float, default=SOURCE_SCALE_MM, help="the model's scale in mm")
    parser.add_argument("--render", action="store_true", help="also write a PNG of each set")
    parser.add_argument("--only", help=argparse.SUPPRESS)  # one preset, in a child process
    args = parser.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    model = pathlib.Path(args.model)
    if args.only:
        scale_mm = dict(SCALES)[args.only]
        print("ROW " + one(model, args.only, scale_mm, args.source_scale, out, args.render), flush=True)
        return
    rows = []
    for preset, _ in SCALES:
        # A fresh process per scale: Blender's offscreen GPU context does not survive
        # a factory reset between renders.
        child = [sys.executable, __file__, "--only", preset, "--out", str(out), "--model", str(model)]
        child += ["--source-scale", str(args.source_scale)] + (["--render"] if args.render else [])
        run = subprocess.run(child, check=True, capture_output=True, text=True)
        rows += [line[4:] for line in run.stdout.splitlines() if line.startswith("ROW ")]
        print(rows[-1], flush=True)
    table = "\n".join(
        [
            "| Scale | Preset | Tips | Unreachable | Feet | Landings | Generate |",
            "|---|---|---|---|---|---|---|",
            *rows,
        ]
    )
    (out / "stats.md").write_text(table + "\n", encoding="utf-8")
    print(f"\nWrote {out}\n\n{table}")


if __name__ == "__main__":
    main()
