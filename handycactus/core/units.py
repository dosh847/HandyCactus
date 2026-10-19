# SPDX-License-Identifier: GPL-3.0-or-later
"""Millimetre <-> Blender unit conversion (spec section 4).

Parameters are always millimetres. The scene's unit scale says how many
metres one Blender unit is.
"""

from __future__ import annotations


def mm_per_bu(scale_length: float) -> float:
    """Millimetres in one Blender unit for a scene unit scale."""
    if scale_length <= 0:
        raise ValueError("scale_length must be positive")
    return scale_length * 1000.0


def mm_to_bu(mm: float, scale_length: float) -> float:
    return mm / mm_per_bu(scale_length)


def bu_to_mm(bu: float, scale_length: float) -> float:
    return bu * mm_per_bu(scale_length)
