# SPDX-License-Identifier: GPL-3.0-or-later
"""Tip construction (spec section 6.3).

For each contact: choose the tip axis (the surface normal clamped to within
`max_tip_angle` of straight down), place the knuckle `tip_length` along it,
and check the tip clears the model. Failing that, try other axes inside the
clamp cone, then shorter tips, then give up and mark the contact unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .detection import Contacts
from .params import Params
from .spatial import Obstacles

DOWN = np.array([0.0, 0.0, -1.0])
TIP_CLEARANCE = 0.1  # mm between the tip cone (or knuckle ball) and the model
NEAR_CONTACT = 0.5  # mm of the tip next to the contact that may touch the model
HIT_TOLERANCE = 0.05  # mm: the ray from the knuckle must first hit at the contact
AZIMUTHS = 8
TILT_STEPS = (1 / 3, 2 / 3, 1.0)
LENGTH_STEPS = (1.0, 0.75, 0.5)
LONG_STEPS = (1.5, 2.0)  # longer tips, tried last, to reach out of crevices
# A branch must be able to start at the knuckle: it needs min_radius plus this much air
# around it (the hard minimum branches keep while routing; two 0.2 mm lines).
BRANCH_HARD_CLEARANCE = 0.4


@dataclass
class Tips:
    surface: np.ndarray  # (K, 3) contact point on the surface
    contact: np.ndarray  # (K, 3) contact node, `penetration` inside the model
    knuckle: np.ndarray  # (K, 3)
    axis: np.ndarray  # (K, 3) unit, contact -> knuckle
    length: np.ndarray  # (K,)
    ok: np.ndarray  # (K,) False = unreachable
    target: np.ndarray  # (K,)

    def __len__(self) -> int:
        return len(self.surface)


def clamp_to_cone(n: np.ndarray, max_angle_deg: float) -> np.ndarray:
    """Rotate unit vectors toward straight down until within max_angle of it."""
    n = np.atleast_2d(n).astype(np.float64)
    cos_max = np.cos(np.radians(max_angle_deg))
    out = n.copy()
    too_wide = -n[:, 2] < cos_max
    if np.any(too_wide):
        h = n[too_wide].copy()
        h[:, 2] = 0.0
        hl = np.linalg.norm(h, axis=1)
        h = np.where(hl[:, None] > 1e-9, h / np.maximum(hl, 1e-12)[:, None], 0.0)
        a = np.radians(max_angle_deg)
        out[too_wide] = DOWN * np.cos(a) + h * np.sin(a)
        flat = hl <= 1e-9  # pointing straight up: no horizontal hint, go straight down
        if np.any(flat):
            out[np.flatnonzero(too_wide)[flat]] = DOWN
    return out / np.linalg.norm(out, axis=1)[:, None]


def candidate_axes(normal: np.ndarray, max_angle_deg: float) -> np.ndarray:
    """Preferred axis first, then the cone's ring directions sorted by closeness to the normal."""
    first = clamp_to_cone(normal, max_angle_deg)[0]
    rings = [DOWN]
    for frac in TILT_STEPS:
        a = np.radians(max_angle_deg * frac)
        for k in range(AZIMUTHS):
            phi = 2 * np.pi * k / AZIMUTHS
            rings.append(np.array([np.sin(a) * np.cos(phi), np.sin(a) * np.sin(phi), -np.cos(a)]))
    rings = np.array(rings)
    rings = rings[np.argsort(-(rings @ normal), kind="stable")]
    return np.vstack([first, rings])


def build_tips(contacts: Contacts, params: Params, obstacles: Obstacles) -> Tips:
    k = len(contacts)
    surface = contacts.positions.astype(np.float64)
    axis = np.zeros((k, 3))
    length = np.zeros(k)
    ok = np.zeros(k, bool)
    r_contact = params.contact_diameter / 2
    r_knuckle = params.knuckle_diameter / 2
    lengths = [params.tip_length * f for f in LENGTH_STEPS if params.tip_length * f >= params.min_tip_length]
    if params.min_tip_length not in lengths:
        lengths.append(params.min_tip_length)
    lengths += [params.tip_length * f for f in LONG_STEPS]
    # Room for the branch at the knuckle: the full branch clearance if possible, else the
    # hard minimum. A knuckle tucked into a crevice would leave its branch stuck.
    rooms = (
        params.min_radius + params.model_clearance,
        params.min_radius + BRANCH_HARD_CLEARANCE,
    )

    for i in range(k):
        p, n = surface[i], contacts.normals[i]
        axes = candidate_axes(n, params.max_tip_angle)
        for room in rooms:
            for length_i in lengths:
                # The preferred axis almost always works; only test the cone ring if it doesn't.
                for chunk in (axes[:1], axes[1:]):
                    valid = _valid(p, chunk, length_i, r_contact, r_knuckle, room, obstacles)
                    if valid.any():
                        axis[i], length[i], ok[i] = chunk[int(np.argmax(valid))], length_i, True
                        break
                if ok[i]:
                    break
            if ok[i]:
                break

    knuckle = surface + axis * length[:, None]
    _separate_knuckles(knuckle, axis, length, ok, r_knuckle)
    knuckle = surface + axis * length[:, None]
    contact = surface - axis * params.penetration
    return Tips(surface, contact, knuckle, axis, length, ok, contacts.target.copy())


def _valid(p, axes, length, r_contact, r_knuckle, room, obstacles) -> np.ndarray:
    """Which candidate axes give a clear tip of this length at contact p, with `room` mm
    of air around the knuckle for its branch."""
    m = len(axes)
    knuckles = p + axes * length
    valid = knuckles[:, 2] >= r_knuckle  # knuckle above the bed
    # The knuckle must look straight back at its own contact.
    hit = obstacles.ray_distance(knuckles, -axes, length + 1.0)
    valid &= hit >= length - HIT_TOLERANCE
    # Cone and ball clear of the model, except right at the contact.
    ts = np.linspace(min(NEAR_CONTACT, length / 2), length, 4)
    samples = (p + axes[:, None, :] * ts[None, :, None]).reshape(-1, 3)
    radius = r_contact + (r_knuckle - r_contact) * (ts / length)
    need_per_t = radius + TIP_CLEARANCE
    need_per_t[-1] = max(need_per_t[-1], room)  # the last sample is the knuckle
    need = np.tile(need_per_t, m)
    idx = np.flatnonzero(np.repeat(valid, len(ts)))
    clear = np.zeros(len(samples), bool)
    if len(idx):
        clear[idx] = obstacles.nearest_distance(samples[idx]) >= need[idx] - 1e-9
    valid &= clear.reshape(m, len(ts)).all(axis=1)
    return valid


def _separate_knuckles(knuckle, axis, length, ok, r_knuckle) -> None:
    """Lengthen the lower of two overlapping knuckles along its own axis (spec 6.3).

    Solves |k_lower + s·axis - k_other| = 2·r for the extension s.
    """
    idx = np.flatnonzero(ok)
    if len(idx) < 2:
        return
    pts = knuckle[idx]
    d = np.linalg.norm(pts[:, None] - pts[None], axis=2)
    d[np.diag_indices(len(idx))] = np.inf
    gap = 2 * r_knuckle + 0.01
    for a, b in zip(*np.nonzero(np.triu(d < 2 * r_knuckle)), strict=True):
        lower, other = (idx[a], idx[b]) if pts[a, 2] <= pts[b, 2] else (idx[b], idx[a])
        w = knuckle[lower] - knuckle[other]
        wa = w @ axis[lower]
        s = -wa + np.sqrt(max(wa * wa - (w @ w - gap * gap), 0.0))
        length[lower] += max(s, 0.0)
        knuckle[lower] = knuckle[lower] + axis[lower] * max(s, 0.0)
