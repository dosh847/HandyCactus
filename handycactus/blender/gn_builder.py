# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds the HC_SupportGeometry node group in code (spec section 5.3).

graph edges --Mesh to Curve--> polylines --Curve to Mesh (circle profile,
Scale = hc_radius, caps)--> tubes; points with hc_sphere > 0 get UV spheres
(knuckles and junctions). Tips are tubes whose contact end has the contact
radius, i.e. cones. Feet are tubes flaring to the foot radius.
"""

from __future__ import annotations

import bpy

GROUP_NAME = "HC_SupportGeometry"
VERSION = 1  # bump when the node layout changes; stale groups are rebuilt
VERSION_KEY = "hc_gn_version"


def ensure_node_group() -> bpy.types.NodeTree:
    ng = bpy.data.node_groups.get(GROUP_NAME)
    if ng is not None and ng.get(VERSION_KEY) == VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    _build(ng)
    ng[VERSION_KEY] = VERSION
    return ng


def ensure_modifier(obj) -> bpy.types.Modifier:
    mod = obj.modifiers.get("HandyCactus")
    if mod is None:
        mod = obj.modifiers.new("HandyCactus", "NODES")
    mod.node_group = ensure_node_group()
    return mod


def set_input(mod, name: str, value) -> None:
    """Set a modifier input by its interface socket name."""
    for item in mod.node_group.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == name:
            # Blender 5.x: inputs live on mod.properties.inputs, not as ID properties.
            getattr(mod.properties.inputs, item.identifier).value = value
            return
    raise KeyError(name)


def _build(ng) -> None:
    ng.nodes.clear()
    ng.interface.clear()
    ng.interface.new_socket("Graph", in_out="INPUT", socket_type="NodeSocketGeometry")
    res = ng.interface.new_socket("Profile Resolution", in_out="INPUT", socket_type="NodeSocketInt")
    res.default_value, res.min_value, res.max_value = 12, 3, 64
    seg = ng.interface.new_socket("Sphere Segments", in_out="INPUT", socket_type="NodeSocketInt")
    seg.default_value, seg.min_value, seg.max_value = 12, 3, 64
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    n, link = ng.nodes, ng.links.new
    gin = n.new("NodeGroupInput")
    gout = n.new("NodeGroupOutput")

    to_curve = n.new("GeometryNodeMeshToCurve")
    link(gin.outputs["Graph"], to_curve.inputs["Mesh"])

    radius = n.new("GeometryNodeInputNamedAttribute")
    radius.data_type = "FLOAT"
    radius.inputs["Name"].default_value = "hc_radius"

    circle = n.new("GeometryNodeCurvePrimitiveCircle")
    circle.inputs["Radius"].default_value = 1.0
    link(gin.outputs["Profile Resolution"], circle.inputs["Resolution"])

    tubes = n.new("GeometryNodeCurveToMesh")
    tubes.inputs["Fill Caps"].default_value = True
    link(to_curve.outputs["Curve"], tubes.inputs["Curve"])
    link(circle.outputs["Curve"], tubes.inputs["Profile Curve"])
    link(radius.outputs["Attribute"], tubes.inputs["Scale"])

    sphere_r = n.new("GeometryNodeInputNamedAttribute")
    sphere_r.data_type = "FLOAT"
    sphere_r.inputs["Name"].default_value = "hc_sphere"
    has_sphere = n.new("FunctionNodeCompare")
    has_sphere.data_type, has_sphere.operation = "FLOAT", "GREATER_THAN"
    has_sphere.inputs["B"].default_value = 0.0
    link(sphere_r.outputs["Attribute"], has_sphere.inputs["A"])

    ball = n.new("GeometryNodeMeshUVSphere")
    ball.inputs["Radius"].default_value = 1.0
    link(gin.outputs["Sphere Segments"], ball.inputs["Segments"])
    rings = n.new("ShaderNodeMath")
    rings.operation = "DIVIDE"
    rings.inputs[1].default_value = 2.0
    link(gin.outputs["Sphere Segments"], rings.inputs[0])
    link(rings.outputs["Value"], ball.inputs["Rings"])

    balls = n.new("GeometryNodeInstanceOnPoints")
    link(gin.outputs["Graph"], balls.inputs["Points"])
    link(has_sphere.outputs["Result"], balls.inputs["Selection"])
    link(ball.outputs["Mesh"], balls.inputs["Instance"])
    link(sphere_r.outputs["Attribute"], balls.inputs["Scale"])
    realize = n.new("GeometryNodeRealizeInstances")
    link(balls.outputs["Instances"], realize.inputs["Geometry"])

    join = n.new("GeometryNodeJoinGeometry")
    link(realize.outputs["Geometry"], join.inputs["Geometry"])
    link(tubes.outputs["Mesh"], join.inputs["Geometry"])
    smooth = n.new("GeometryNodeSetShadeSmooth")
    link(join.outputs["Geometry"], smooth.inputs["Mesh"])
    link(smooth.outputs["Mesh"], gout.inputs["Geometry"])

    for i, node in enumerate((gin, to_curve, tubes, join, smooth, gout)):
        node.location = (i * 220, 0)
    for i, node in enumerate((radius, circle, sphere_r, has_sphere, ball, rings, balls, realize)):
        node.location = (220 + (i % 4) * 220, -260 - (i // 4) * 220)
