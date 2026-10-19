# SPDX-License-Identifier: GPL-3.0-or-later
"""Generation parameters (spec section 9).

This module is the single source of truth for every parameter: its group,
default, range, unit and description. The Blender property group is
generated from PARAM_SPECS, so a parameter is added in one place only.

All lengths are millimetres and all angles are degrees. Nothing here
imports bpy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace

# Display order of the parameter groups in the UI.
GROUPS: tuple[tuple[str, str], ...] = (
    ("printer", "Printer"),
    ("placement", "Placement"),
    ("detection", "Detection"),
    ("tips", "Tips"),
    ("branches", "Branches"),
    ("feet", "Feet"),
    ("display", "Display"),
)


@dataclass(frozen=True)
class ParamSpec:
    name: str
    group: str
    label: str
    default: float | int
    min: float | int
    max: float | int
    unit: str  # "mm", "deg", "mm/mm", "x" or "" (plain count)
    description: str

    @property
    def is_int(self) -> bool:
        return isinstance(self.default, int)


PARAM_SPECS: tuple[ParamSpec, ...] = (
    # Printer
    ParamSpec(
        "nozzle_diameter", "printer", "Nozzle Diameter", 0.2, 0.1, 0.8, "mm", "Nozzle diameter of the printer"
    ),
    ParamSpec(
        "layer_height",
        "printer",
        "Layer Height",
        0.08,
        0.04,
        0.3,
        "mm",
        "Layer height the model will be sliced at",
    ),
    # Placement
    ParamSpec(
        "lift_height",
        "placement",
        "Lift Height",
        3.0,
        0.0,
        20.0,
        "mm",
        "Height of the lowest point of the target set above the bed",
    ),
    # Detection
    ParamSpec(
        "overhang_angle",
        "detection",
        "Overhang Angle",
        45.0,
        20.0,
        80.0,
        "deg",
        "Faces tilted further than this from vertical need support",
    ),
    ParamSpec(
        "tip_spacing",
        "detection",
        "Tip Spacing",
        1.5,
        0.4,
        10.0,
        "mm",
        "Distance between contact points on overhangs",
    ),
    ParamSpec(
        "steepness_density",
        "detection",
        "Steepness Density",
        1.5,
        1.0,
        3.0,
        "x",
        "How much denser contacts get as a face approaches horizontal",
    ),
    ParamSpec(
        "max_bridge",
        "detection",
        "Max Bridge",
        2.0,
        0.0,
        20.0,
        "mm",
        "Overhang span that can bridge between supported edges without contacts",
    ),
    ParamSpec(
        "bed_ignore_height",
        "detection",
        "Bed Ignore Height",
        0.2,
        0.0,
        2.0,
        "mm",
        "Faces whose lowest point is below this height get no contacts",
    ),
    # Tips
    ParamSpec(
        "contact_diameter",
        "tips",
        "Contact Diameter",
        0.3,
        0.1,
        1.5,
        "mm",
        "Diameter where the tip touches the model",
    ),
    ParamSpec(
        "penetration", "tips", "Penetration", 0.15, 0.0, 0.6, "mm", "How far the tip sinks into the model"
    ),
    ParamSpec(
        "tip_length",
        "tips",
        "Tip Length",
        1.5,
        0.4,
        6.0,
        "mm",
        "Length of the tip cone from contact to knuckle",
    ),
    ParamSpec(
        "min_tip_length",
        "tips",
        "Min Tip Length",
        0.6,
        0.2,
        3.0,
        "mm",
        "Shortest a tip may be shortened to when clearing obstacles",
    ),
    ParamSpec(
        "max_tip_angle",
        "tips",
        "Max Tip Angle",
        45.0,
        0.0,
        80.0,
        "deg",
        "Largest angle of a tip from straight down",
    ),
    ParamSpec(
        "knuckle_diameter",
        "tips",
        "Knuckle Diameter",
        0.6,
        0.3,
        3.0,
        "mm",
        "Diameter of the ball joint between tip and branch",
    ),
    # Branches
    ParamSpec(
        "min_radius",
        "branches",
        "Min Radius",
        0.2,
        0.05,
        4.0,
        "mm",
        "Smallest branch radius; keep at least half the nozzle diameter",
    ),
    ParamSpec("max_radius", "branches", "Max Radius", 2.0, 0.4, 8.0, "mm", "Largest trunk radius"),
    ParamSpec(
        "taper_rate",
        "branches",
        "Taper Rate",
        0.03,
        0.0,
        0.2,
        "mm/mm",
        "Radius growth per millimetre of descent",
    ),
    ParamSpec(
        "max_branch_angle",
        "branches",
        "Max Branch Angle",
        40.0,
        0.0,
        60.0,
        "deg",
        "Largest angle of a branch from vertical",
    ),
    ParamSpec(
        "merge_distance",
        "branches",
        "Merge Distance",
        3.0,
        0.0,
        20.0,
        "mm",
        "How far a branch looks for another branch to merge with",
    ),
    ParamSpec(
        "merge_growth",
        "branches",
        "Merge Growth",
        1.0,
        0.8,
        1.3,
        "x",
        "Radius multiplier applied when two branches merge",
    ),
    ParamSpec(
        "model_clearance",
        "branches",
        "Model Clearance",
        0.8,
        0.2,
        5.0,
        "mm",
        "Gap kept between branches and the model",
    ),
    ParamSpec(
        "step_z",
        "branches",
        "Step Height",
        0.4,
        0.1,
        2.0,
        "mm",
        "Vertical step of the downward routing sweep",
    ),
    # Feet
    ParamSpec(
        "foot_radius", "feet", "Foot Radius", 1.5, 0.0, 6.0, "mm", "Radius of the flared foot on the bed"
    ),
    ParamSpec("foot_height", "feet", "Foot Height", 0.4, 0.0, 3.0, "mm", "Height of the flared foot"),
    # Display
    ParamSpec(
        "profile_resolution",
        "display",
        "Profile Resolution",
        12,
        6,
        32,
        "",
        "Number of sides on branch tubes",
    ),
)

SPECS_BY_NAME: Mapping[str, ParamSpec] = {s.name: s for s in PARAM_SPECS}


def _default(name: str):
    return field(default=SPECS_BY_NAME[name].default)


@dataclass(frozen=True)
class Params:
    """One full parameter set. Defaults are the Mini 42 mm preset."""

    nozzle_diameter: float = _default("nozzle_diameter")
    layer_height: float = _default("layer_height")
    lift_height: float = _default("lift_height")
    overhang_angle: float = _default("overhang_angle")
    tip_spacing: float = _default("tip_spacing")
    steepness_density: float = _default("steepness_density")
    max_bridge: float = _default("max_bridge")
    bed_ignore_height: float = _default("bed_ignore_height")
    contact_diameter: float = _default("contact_diameter")
    penetration: float = _default("penetration")
    tip_length: float = _default("tip_length")
    min_tip_length: float = _default("min_tip_length")
    max_tip_angle: float = _default("max_tip_angle")
    knuckle_diameter: float = _default("knuckle_diameter")
    min_radius: float = _default("min_radius")
    max_radius: float = _default("max_radius")
    taper_rate: float = _default("taper_rate")
    max_branch_angle: float = _default("max_branch_angle")
    merge_distance: float = _default("merge_distance")
    merge_growth: float = _default("merge_growth")
    model_clearance: float = _default("model_clearance")
    step_z: float = _default("step_z")
    foot_radius: float = _default("foot_radius")
    foot_height: float = _default("foot_height")
    profile_resolution: int = _default("profile_resolution")

    def with_values(self, values: Mapping[str, float | int]) -> Params:
        unknown = set(values) - SPECS_BY_NAME.keys()
        if unknown:
            raise KeyError(f"unknown parameters: {sorted(unknown)}")
        return replace(self, **values)

    def as_dict(self) -> dict[str, float | int]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def problems(self) -> list[str]:
        """Out-of-range values and inconsistent combinations, as messages."""
        out = []
        for spec in PARAM_SPECS:
            value = getattr(self, spec.name)
            if not spec.min <= value <= spec.max:
                out.append(f"{spec.label} {value} is outside {spec.min}–{spec.max}")
        if self.min_radius > self.max_radius:
            out.append("Min Radius is larger than Max Radius")
        if self.min_tip_length > self.tip_length:
            out.append("Min Tip Length is longer than Tip Length")
        if self.min_radius < self.nozzle_diameter / 2:
            out.append("Min Radius is below half the nozzle diameter")
        return out


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    description: str
    values: Mapping[str, float | int]

    def params(self) -> Params:
        # Presets are full parameter sets: unspecified values are defaults.
        return Params().with_values(self.values)


PRESETS: tuple[Preset, ...] = (
    Preset(
        "MINI_32",
        "Mini 32 mm",
        "32 mm scale miniature, 0.2 mm nozzle",
        {
            "tip_spacing": 1.3,
            "contact_diameter": 0.25,
            "tip_length": 1.3,
            "knuckle_diameter": 0.5,
            "max_radius": 1.6,
            "foot_radius": 1.2,
        },
    ),
    Preset("MINI_42", "Mini 42 mm", "42 mm scale miniature, 0.2 mm nozzle (default)", {}),
    Preset(
        "MINI_50",
        "Mini 50 mm",
        "50 mm scale miniature, 0.2 mm nozzle",
        {
            "tip_spacing": 1.8,
            "contact_diameter": 0.35,
            "tip_length": 1.8,
            "knuckle_diameter": 0.7,
            "max_radius": 2.4,
            "foot_radius": 1.8,
        },
    ),
    Preset(
        "MINI_42_NOZZLE_04",
        "Mini 42 mm, 0.4 mm Nozzle",
        "42 mm scale miniature on a 0.4 mm nozzle",
        {
            "nozzle_diameter": 0.4,
            "min_radius": 0.4,
            "contact_diameter": 0.4,
        },
    ),
)

PRESETS_BY_ID: Mapping[str, Preset] = {p.id: p for p in PRESETS}
DEFAULT_PRESET_ID = "MINI_42"
