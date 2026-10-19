# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate, build and install-check the extension with the `bpy` module.

Uses the same extension CLI that `blender --command extension` runs, so no
Blender binary is needed:

    python tools/extension.py validate
    python tools/extension.py build [--output-dir dist]
    python tools/extension.py install-check dist/handycactus-<version>.zip
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "handycactus"


def _blender_ext() -> str:
    import bpy

    scripts = bpy.utils.system_resource("SCRIPTS")
    return os.path.join(scripts, "addons_core", "bl_pkg", "cli", "blender_ext.py")


def _run_ext(*args: str) -> None:
    subprocess.run([sys.executable, _blender_ext(), *args], check=True)


def _install_check(zip_path: str) -> None:
    # Isolated user resources, so the check never touches a real Blender config.
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, BLENDER_USER_RESOURCES=tmp)
        code = (
            "import sys, bpy\n"
            "r = bpy.ops.extensions.package_install_files(\n"
            "    filepath=sys.argv[1], repo='user_default', enable_on_install=True)\n"
            "assert r == {'FINISHED'}, r\n"
            "assert 'bl_ext.user_default.handycactus' in bpy.context.preferences.addons, 'not enabled'\n"
            "assert bpy.context.scene.handycactus.preset == 'MINI_42'\n"
            "assert hasattr(bpy.types, 'HANDYCACTUS_PT_main')\n"
            "print('install-check: OK')\n"
        )
        subprocess.run([sys.executable, "-I", "-c", code, zip_path], check=True, env=env, cwd=tmp)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    build = sub.add_parser("build")
    build.add_argument("--output-dir", default=str(ROOT / "dist"))
    check = sub.add_parser("install-check")
    check.add_argument("zip")
    args = parser.parse_args()

    if args.cmd == "validate":
        _run_ext("validate", str(SOURCE))
    elif args.cmd == "build":
        os.makedirs(args.output_dir, exist_ok=True)
        _run_ext("build", "--source-dir", str(SOURCE), "--output-dir", args.output_dir)
    else:
        _install_check(os.path.abspath(args.zip))


if __name__ == "__main__":
    main()
