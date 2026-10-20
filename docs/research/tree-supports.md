# Tree supports in OrcaSlicer, PrusaSlicer and Cura

Research notes (2026-10-20) behind spec §6.4.2. Sources read:

- **OrcaSlicer** `main`: `Support/TreeSupport3D.cpp/.hpp`, `TreeSupportCommon.hpp`,
  `TreeModelVolumes.cpp`, `TreeSupport.cpp/.hpp`, `SupportParameters.hpp` and
  `PrintConfig.cpp`.
- **PrusaSlicer** `version_2.8.1`: `Support/TreeSupport.cpp`,
  `Support/OrganicSupport.cpp` and `PrintConfig.cpp`.
- **CuraEngine/Cura** `main`: `TreeSupport.cpp`, `TreeSupportSettings.h` and
  `fdmprinter.def.json`.

Orca's `TreeSupport3D.cpp` is a near-verbatim port of Prusa's `TreeSupport.cpp`.

## 1. Radius growth (`TreeSupportSettings::getRadius`)

```
tip_layers = max((branch_r - tip_r) / (line_w/3), branch_r / layer_h)
r(dtt) = dtt <= tip_layers ? tip_r + (branch_r - tip_r)*dtt/tip_layers      // tip cone
                           : branch_r + (dtt - tip_layers)*tan(diam_angle)*layer_h
```

`dtt` is the distance to the top, in layers.

- **Merging is not area-additive.** `merge_support_element_states` takes the
  maximum `distance_to_top` of the two branches, so a trunk's radius depends only
  on its tallest tip and never jumps at a merge.
- **PrusaSlicer defaults:** tip diameter 0.8 mm, branch diameter 2.0 mm, branch
  diameter angle 5° (about +0.087 mm radius per mm). Double walls start above 3 mm
  diameter. Orca organic uses the same values.
- **Cura defaults:** branch diameter 5 mm, tip diameter 0.8 mm, trunk at most
  25 mm, diameter angle 7° (about +0.12 mm radius per mm).
- **Wider base:** a 7.5 mm base diameter, reached by flaring about 40° near the
  bed where the avoidance allows it.

## 2. Smoothness

**Two passes.** The top-down pass propagates an *influence area* per branch:
every centre position that can still reach all its tips. The bottom-up pass then
places each node at the point of its area closest to the node below
(`move_inside_if_outside`). The result is straight vertical runs that bend only
where they must.

**`organic_smooth_branches_avoid_collisions`** (Prusa and Orca):

- Up to 100 iterations of `new = 0.5·old + 0.5·avg`, capped at 0.2 mm per step.
- In the average, parents are weighted by radius, and the node below gets as
  much weight as all parents together.
- A colliding node is pushed out by `min(depth + 0.1, 0.5)` mm.
- Tips stay put, and so do bottoms resting on the model.

**Mesh output:**

- One tube per segment between bifurcations, with rings on bisector planes and
  hemispherical end caps at each node's own radius.
- No separate junction spheres. A branch is never thicker than the trunk below
  it, so its cap stays inside the trunk.

**Cura extras:**

- The radius changes by at most `line_w/2` per layer.
- Each layer's circle is stretched into an ellipse that also covers the
  neighbouring centres.

## 3. Merging and angles

- **When branches merge:** grow the smaller branch's influence area by
  `r_big − r_small`, then merge if it overlaps the bigger branch's area. There is
  no fixed merge distance.
- **Where the merged node goes:** the point of the overlap closest to the
  previous merge, not the midpoint.
- **Angles:**
  - Orca/Prusa: maximum 40°, preferred 25°.
  - Cura: maximum 60°, preferred ⅔ of that.
- **Move order per layer:**
  1. Slow (preferred angle), with radius growth.
  2. Slow, without growth.
  3. Fast (maximum angle), hole-free avoidance, with growth.
  4. Fast, without growth.
  5. Error case: 1.5× the fast move.
- Orca's non-organic style keeps trunks vertical below 5 mm.

## 4. Avoidance

- **Collision(r, layer):** the model outline offset by `r + xy_distance`.
- **Avoidance:** `avoid[l] = collision[l] ∪ erode(avoid[l−1], max_move)`, built
  bottom-up and cached per radius. The erosion runs in steps of at most
  `1.9·max(r, xy)`, so a branch can't tunnel through thin walls.
- **Resting on the model:** branches may rest on top surfaces, eroded by
  `r + xy`.
  - A branch that can't rest gracefully keeps going down; at the end it is cut
    at the highest layer where it can be placed.
  - Branches resting on the model are dropped if shorter than 1 mm (Prusa/Orca)
    or 3 mm (Cura).
- **XY distance:** Orca 0.35 mm, Cura 0.7 mm.

## Adopted in HandyCactus v0.4

- Radius by height below the tallest tip: tip cone 0.2 → 0.6 mm, then
  0.07 mm/mm. A merge takes the thicker branch's radius.
- Laplacian smoothing with clearance and angle constraints.
- A preferred angle of 35° for ordinary moves (25° cost too many merges on
  minis lifted 3 mm).
- A vertical trunk base.
- Catmull-Rom rendering of branch curves.

## Candidates for later

- Two-pass influence-area placement.
- Avoidance volumes per level and radius, to cut unreachable contacts.
- Lower model clearance (ours is `r + 0.8` mm against Orca's 0.35 mm).
- A limit on radius gained from merges for branches resting on the model.
- Brims under trunks.
