# SPDX-License-Identifier: GPL-3.0-or-later
"""Routing with a retry pass (spec section 6.4.3).

A stuck head drops its whole tree, so tips that could have reached the bed on their
own are lost when they merged into a branch that later got trapped. After the first
pass, the tips it lost are routed again on their own (no merging among them), and may
end by joining any tree that made it (incremental routing, spec 6.5).
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


def route_with_retry(tips: Tips, params: Params, obstacles: Obstacles) -> RoutingResult:
    av = build_avoidance(obstacles, tips, params)
    first = route(tips, params, obstacles, avoidance=av)
    retry = np.intersect1d(first.unreachable_tips, np.flatnonzero(tips.ok))
    if len(retry) == 0 or len(first.graph) == 0:
        return first
    second = route(
        subset_tips(tips, retry),
        params,  # merging again: retried tips are often many neighbours (a dropped tree)
        obstacles,
        attach_to=first.graph,
        avoidance=av,
    )
    graph, _ = merge_routed(first.graph, second, params, tip_ids=retry)

    supported = np.unique(graph.tip[graph.kind == CONTACT])
    return RoutingResult(graph, np.setdiff1d(np.arange(len(tips)), supported))
