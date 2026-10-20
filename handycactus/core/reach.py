# SPDX-License-Identifier: GPL-3.0-or-later
"""Routing with a retry pass (spec section 6.4.3).

A stuck head drops its whole tree, so tips that could have reached the bed on their
own are lost when they merged into a branch that later got trapped. After the first
pass, the tips it lost are routed again (merging), then whatever is still lost one by
one; either may end by joining any tree that made it (incremental routing, spec 6.5).
"""

from __future__ import annotations

import numpy as np

from .editing import merge_routed
from .graph import CONTACT
from .params import Params
from .routing import RoutingResult, build_avoidance, route
from .spatial import Obstacles
from .tips import Tips


def subset_tips(tips: Tips, idx: np.ndarray) -> Tips:
    return Tips(
        *(
            getattr(tips, name)[idx]
            for name in ("surface", "contact", "knuckle", "axis", "length", "ok", "target")
        )
    )


def route_with_retry(tips: Tips, params: Params, obstacles: Obstacles, progress=None) -> RoutingResult:
    """`progress(fraction)`: the first pass reports 0..0.9, the retry the rest."""
    av = build_avoidance(obstacles, tips, params)
    first = route(
        tips, params, obstacles, avoidance=av, progress=(lambda f: progress(0.9 * f)) if progress else None
    )
    graph, lost = first.graph, first.unreachable_tips
    # Pass 2 merges again (retried tips are often many neighbours of one dropped tree).
    # Pass 3 routes what is still lost one by one: a merged group can get trapped the
    # same way twice, while its tips alone fit through.
    for merge_distance in (params.merge_distance, 0.0):
        retry = np.intersect1d(lost, np.flatnonzero(tips.ok))
        if len(retry) == 0 or len(graph) == 0:
            break
        second = route(
            subset_tips(tips, retry),
            params.with_values({"merge_distance": merge_distance}),
            obstacles,
            attach_to=graph,
            avoidance=av,
        )
        graph, _ = merge_routed(graph, second, params, tip_ids=retry)
        supported = np.unique(graph.tip[graph.kind == CONTACT])
        lost = np.setdiff1d(np.arange(len(tips)), supported)
    return RoutingResult(graph, lost)
