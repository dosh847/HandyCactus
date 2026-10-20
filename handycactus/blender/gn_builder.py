# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds the HC_SupportGeometry node group in code (spec section 5.3).

graph edges --Mesh to Curve--> polylines --Curve to Mesh (circle profile,
Scale = hc_radius, caps)--> tubes. Edges between two branch/junction nodes become
Catmull-Rom splines (Curve Smoothness samples per segment) so bends render round;
the curve still passes through every node. Tips and feet stay straight. Points
with hc_sphere > 0 get UV spheres
(knuckles and junctions). Tips are tubes whose contact end has the contact
radius, i.e. cones. Feet are tubes flaring to the foot radius.
"""

from __future__ import annotations

import bpy

GROUP_NAME = "HC_SupportGeometry"
VERSION = 3  # bump when the node layout changes; stale groups are rebuilt
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
    smooth_res = ng.interface.new_socket("Curve Smoothness", in_out="INPUT", socket_type="NodeSocketInt")
    smooth_res.default_value, smooth_res.min_value, smooth_res.max_value = 4, 1, 16
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    n, link = ng.nodes, ng.links.new
    gin = n.new("NodeGroupInput")
    gout = n.new("NodeGroupOutput")

    # Branch edges: both ends are branch (2) or junction (3) nodes. The test runs on the
    # points (an int attribute read on edges would be averaged: foot 4 + branch 2 = 3);
    # its 0/1 value is then averaged onto the edge, so 1.0 means both ends.
    kind = n.new("GeometryNodeInputNamedAttribute")
    kind.data_type = "INT"
    kind.inputs["Name"].default_value = "hc_kind"
    is_low = n.new("FunctionNodeCompare")
    is_low.data_type, is_low.operation = "INT", "GREATER_EQUAL"
    link(kind.outputs["Attribute"], is_low.inputs["A"])
    is_low.inputs["B"].default_value = 2
    is_high = n.new("FunctionNodeCompare")
    is_high.data_type, is_high.operation = "INT", "LESS_EQUAL"
    link(kind.outputs["Attribute"], is_high.inputs["A"])
    is_high.inputs["B"].default_value = 3
    both = n.new("FunctionNodeBooleanMath")
    both.operation = "AND"
    link(is_low.outputs["Result"], both.inputs[0])
    link(is_high.outputs["Result"], both.inputs[1])
    as_float = n.new("ShaderNodeMath")
    as_float.operation = "ADD"
    link(both.outputs["Boolean"], as_float.inputs[0])
    as_float.inputs[1].default_value = 0.0
    on_points = n.new("GeometryNodeFieldOnDomain")
    on_points.data_type, on_points.domain = "FLOAT", "POINT"
    link(as_float.outputs["Value"], on_points.inputs[0])
    is_branch_edge = n.new("FunctionNodeCompare")
    is_branch_edge.data_type, is_branch_edge.operation = "FLOAT", "GREATER_THAN"
    link(on_points.outputs[0], is_branch_edge.inputs["A"])
    is_branch_edge.inputs["B"].default_value = 0.99
    not_branch_edge = n.new("FunctionNodeBooleanMath")
    not_branch_edge.operation = "NOT"
    link(is_branch_edge.outputs["Result"], not_branch_edge.inputs[0])

    branch_curve = n.new("GeometryNodeMeshToCurve")
    link(gin.outputs["Graph"], branch_curve.inputs["Mesh"])
    link(is_branch_edge.outputs["Result"], branch_curve.inputs["Selection"])
    spline_type = n.new("GeometryNodeCurveSplineType")
    spline_type.spline_type = "CATMULL_ROM"
    link(branch_curve.outputs["Curve"], spline_type.inputs["Curve"])
    resolution = n.new("GeometryNodeSetSplineResolution")
    link(spline_type.outputs["Curve"], resolution.inputs["Curve"])
    link(gin.outputs["Curve Smoothness"], resolution.inputs["Resolution"])

    to_curve = n.new("GeometryNodeMeshToCurve")  # tips, feet, landing tips: straight
    link(gin.outputs["Graph"], to_curve.inputs["Mesh"])
    link(not_branch_edge.outputs["Boolean"], to_curve.inputs["Selection"])
    curves = n.new("GeometryNodeJoinGeometry")
    link(resolution.outputs["Curve"], curves.inputs["Geometry"])
    link(to_curve.outputs["Curve"], curves.inputs["Geometry"])

    radius = n.new("GeometryNodeInputNamedAttribute")
    radius.data_type = "FLOAT"
    radius.inputs["Name"].default_value = "hc_radius"

    circle = n.new("GeometryNodeCurvePrimitiveCircle")
    circle.inputs["Radius"].default_value = 1.0
    link(gin.outputs["Profile Resolution"], circle.inputs["Resolution"])

    tubes = n.new("GeometryNodeCurveToMesh")
    tubes.inputs["Fill Caps"].default_value = True
    link(curves.outputs["Geometry"], tubes.inputs["Curve"])
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
    # Nothing below the bed: a thick foot's ring, tilted a few degrees by the branch above,
    # can reach under z = 0. Flatten it onto the bed (supports objects sit at the origin).
    position = n.new("GeometryNodeInputPosition")
    xyz = n.new("ShaderNodeSeparateXYZ")
    link(position.outputs["Position"], xyz.inputs["Vector"])
    floor = n.new("ShaderNodeMath")
    floor.operation = "MAXIMUM"
    link(xyz.outputs["Z"], floor.inputs[0])
    floor.inputs[1].default_value = 0.0
    clamped = n.new("ShaderNodeCombineXYZ")
    link(xyz.outputs["X"], clamped.inputs["X"])
    link(xyz.outputs["Y"], clamped.inputs["Y"])
    link(floor.outputs["Value"], clamped.inputs["Z"])
    on_bed = n.new("GeometryNodeSetPosition")
    link(join.outputs["Geometry"], on_bed.inputs["Geometry"])
    link(clamped.outputs["Vector"], on_bed.inputs["Position"])
    smooth = n.new("GeometryNodeSetShadeSmooth")
    link(on_bed.outputs["Geometry"], smooth.inputs["Mesh"])
    link(smooth.outputs["Mesh"], gout.inputs["Geometry"])
    for i, node in enumerate((position, xyz, floor, clamped, on_bed)):
        node.location = (1100 + i * 180, -700)

    for i, node in enumerate((gin, branch_curve, spline_type, resolution, curves, tubes, join, smooth, gout)):
        node.location = (i * 220, 0)
    selection = (kind, is_low, is_high, both, as_float, on_points, is_branch_edge, not_branch_edge, to_curve)
    for i, node in enumerate(selection):
        node.location = (220 * i, 300)
    for i, node in enumerate((radius, circle, sphere_r, has_sphere, ball, rings, balls, realize)):
        node.location = (220 + (i % 4) * 220, -260 - (i // 4) * 220)
