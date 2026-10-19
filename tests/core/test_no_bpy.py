# SPDX-License-Identifier: GPL-3.0-or-later
"""core/ must stay importable without Blender (spec section 12)."""

import ast
import pathlib

CORE = pathlib.Path(__file__).parents[2] / "handycactus" / "core"


def test_core_never_imports_bpy():
    for path in CORE.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                assert root not in {"bpy", "bmesh", "gpu", "mathutils"}, f"{path.name} imports {name}"
