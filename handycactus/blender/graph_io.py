# SPDX-License-Identifier: GPL-3.0-or-later
"""Support graph <-> mesh with point attributes (spec section 5.2).

Vertices are nodes, edges are node -> parent segments. Positions and the
two radius attributes are stored in Blender units, so the Geometry Nodes
modifier needs no unit handling. Everything else is unitless.
"""

from __future__ import annotations

import numpy as np

from ..core.graph import SupportGraph

# name -> (graph field, Blender data type, foreach key, numpy dtype, scaled by length)
ATTRIBUTES = {
    "hc_kind": ("kind", "INT", "value", np.int32, False),
    "hc_radius": ("radius", "FLOAT", "value", np.float32, True),
    "hc_parent": ("parent", "INT", "value", np.int32, False),
    "hc_tree": ("tree", "INT", "value", np.int32, False),
    "hc_tip": ("tip", "INT", "value", np.int32, False),
    "hc_target": ("target", "INT", "value", np.int32, False),
    "hc_dir": ("dir", "FLOAT_VECTOR", "vector", np.float32, False),
    "hc_sphere": ("sphere", "FLOAT", "value", np.float32, True),
    "hc_locked": ("locked", "BOOLEAN", "value", bool, False),
    "hc_flags": ("flags", "INT", "value", np.int32, False),
}


def write_graph(mesh, graph: SupportGraph, mm_per_bu: float) -> None:
    mesh.clear_geometry()
    n = len(graph)
    mesh.vertices.add(n)
    mesh.vertices.foreach_set("co", (graph.pos / mm_per_bu).astype(np.float32).ravel())
    edges = graph.edges
    mesh.edges.add(len(edges))
    mesh.edges.foreach_set("vertices", edges.astype(np.int32).ravel())
    for name, (field, data_type, key, dtype, scaled) in ATTRIBUTES.items():
        if name in mesh.attributes:
            mesh.attributes.remove(mesh.attributes[name])
        attr = mesh.attributes.new(name, data_type, "POINT")
        values = getattr(graph, field)
        if scaled:
            values = values / mm_per_bu
        attr.data.foreach_set(key, np.asarray(values, dtype=dtype).ravel())
    mesh.update()


def read_graph(mesh, mm_per_bu: float) -> SupportGraph:
    n = len(mesh.vertices)
    co = np.empty(n * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    fields = {"pos": co.reshape(-1, 3).astype(np.float64) * mm_per_bu}
    for name, (field, data_type, key, dtype, scaled) in ATTRIBUTES.items():
        width = 3 if data_type == "FLOAT_VECTOR" else 1
        values = np.zeros(n * width, dtype=dtype)
        if name in mesh.attributes:
            mesh.attributes[name].data.foreach_get(key, values)
        values = values.reshape(-1, 3) if width == 3 else values
        if dtype is np.float32:
            values = values.astype(np.float64) * (mm_per_bu if scaled else 1.0)
        elif dtype is np.int32:
            values = values.astype(np.int64)
        fields[field] = values
    return SupportGraph(**fields)
