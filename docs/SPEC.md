# HandyCactus — Tree Supports for FDM in Blender (Spec v0.3)

> Status: v0.3. Adds landing on the model and manual editing as built in M4 (2026-10-19).

HandyCactus is a Blender extension that generates **resin-style tree supports
for FDM printing**: thin, tapered tips that touch the model at points and snap
off cleanly, joined through knuckle balls into branches that merge into trunks
standing on the build plate. Reference tools: Trestle (trestlefdm.pages.dev)
and PandaProps (github.com/PandaProps/releases).

---

## 1. Decisions from the interview

| Topic | Decision |
|---|---|
| Home | New dedicated repository (this spec moves there) |
| Process | FDM, with resin-style contact tips |
| Primary use case | Tabletop miniatures at 32–50 mm scale (42 mm most common), **0.2 mm nozzle** by default |
| Workflow | Automatic generation, then manual editing in the viewport |
| Blender | Latest stable release, for the newest Geometry Nodes: `blender_version_min = "5.2.0"` (5.2 LTS). Raise the minimum when a later release adds a GN feature we use |
| Output | A separate support object next to the models (never merged into them) |
| Multiple objects | One generate pass covers several target objects (e.g. a multi-part mini), producing one shared supports object |
| Feet | Yes: small flared feet on every trunk |
| Junction look | Plain overlapping tubes and spheres |
| Cross-bracing | v2 |
| Representation | Python computes a support graph; a Geometry Nodes modifier turns it into a mesh |
| Language | Pure Python + numpy + `mathutils` (BVH tree, KD-tree). No native code |
| Anchoring | Build plate only. Trunks never land on the model |
| Tips | Cone to a small contact disc, ball/knuckle joint, oriented along the surface normal (clamped) |
| Detection | Overhang angle, islands/local minima, bridging heuristic, paint masks |
| Branching | Greedy cone merging with model avoidance |
| Editing | Click to add/delete tips, drag tips and junctions, per-support overrides and locks, live overhang heatmap |
| Regenerate | Manual button only. Locked and hand-edited supports are kept |
| Checks (v1) | Built-in printability checks |
| Export (v1) | 3MF (model and supports as parts of one object) and STL |
| Units | Always millimetres, converted using the scene unit scale |
| License | GPL-3.0-or-later, distributed on extensions.blender.org |
| Landing | Build plate first; a branch with no way to the bed may land on an upward-facing part of the model (pulled into v1 in M4) |
| Deferred to v2+ | Bambu/Orca layer check, auto-orientation, rafts, cross-bracing |

## 2. Goals and non-goals

**Goals (v1)**
- One-click supports for a 32–50 mm mini that slice and print on a 0.2 mm
  nozzle without manual fixes in most cases.
- One pass supports a multi-part mini (body, arms, weapon, base) as a set.
- Supports snap off and leave small marks that are easy to clean up.
- Interactive editing that survives regeneration (locks).
- Exports that open cleanly in Bambu Studio, OrcaSlicer, PrusaSlicer and Cura.

**Non-goals (v1)**
- Resin (SLA/MSLA) supports, rafts, or peel-force modelling.
- Orienting the model. The user orients it; we only offer a "place on bed with
  lift" helper.
- Running slicers.
- Cross-bracing between trunks (v2).
- Smooth, organic blending at junctions. Only the optional watertight bake
  in §5.3 merges the shells.

## 3. Terms

- **Contact point**: a point on the model surface that needs support.
- **Tip**: the cone from the contact disc (on the model) to the knuckle.
- **Knuckle**: a sphere at the narrow end of a tip, where the tip joins its
  branch. It is the intended break point.
- **Branch / trunk**: tapered segments from knuckles down to the bed.
  "Trunk" means the segment that reaches the bed.
- **Junction**: a node where two or more branches merge.
- **Foot**: the bottom node of a trunk, at Z = 0.
- **Tree**: everything connected to one foot.
- **Support graph**: the data structure described in §5.

## 4. Coordinate and unit conventions

- The build plate is the world XY plane at Z = 0, with +Z up.
- All parameters are in mm. A **Units** setting says how Blender units map
  to millimetres:
  - **1 Unit = 1 mm** (the default) treats one Blender unit as 1 mm,
    whatever the scene's unit settings are. This is how STL files land in a
    default Blender scene, so a 42 mm mini is 42 units tall.
  - **Scene Units** follows the scene's Unit Scale:
    `mm_per_bu = scale_length × 1000`.
- The panel shows the selected set's size in mm and warns above 500 mm. If
  detection would need more than 20,000 contacts, it stops and points at the
  Units setting, because a model that size is almost always a units mismatch.
- Targets are read **evaluated** (with modifiers applied) and **in world
  space**. A target below Z < 0 is an error that offers "Place on bed".
- **Place on bed with lift** (helper operator): moves the whole target set
  together, keeping the parts' relative positions, so the set's lowest point
  sits at `lift_height` (default 3 mm). This leaves room for knuckles under
  the lowest surfaces.

## 5. Data model

### 5.1 Objects

**Targets** are one or more of the user's mesh objects. HandyCactus never
changes their mesh data; it only reads it.

The supports are one object, `<set name>_HC_Supports` (named after the first
target by default), holding the **support graph** as a mesh:
- Vertices are graph nodes and edges are graph segments.
- A GN modifier (`HC_SupportGeometry` node group) turns it into solid
  geometry.

The supports object stores:
- the **target set**: a CollectionProperty of object pointers, in a fixed
  order;
- all generation parameters.

Settings therefore stay with the file and travel with duplication. To create
a set, select the targets and press **Generate**. As built in M2:
- the parameters are recorded as JSON (`params_json`);
- Generate on the same targets rebuilds their existing set in place, so
  there are never duplicate sets;
- Generate with only a supports object selected rebuilds it from its
  recorded targets. A set can be edited later
(add or remove a target). Removing a target deletes the tips that touch it.
**Regenerate** keeps any locked tree, even if some of its tips are on a
removed target; those tips are flagged in checks.

### 5.2 Node attributes (POINT domain)

| Attribute | Type | Meaning |
|---|---|---|
| `hc_kind` | INT | 0 contact, 1 knuckle, 2 branch, 3 junction, 4 foot |
| `hc_radius` | FLOAT | Tube radius at this node (stored in Blender units, so the GN build needs no unit handling) |
| `hc_parent` | INT | Index of the next node toward the bed (-1 for a foot) |
| `hc_tree` | INT | Tree id |
| `hc_tip` | INT | Tip id (contact and knuckle nodes; -1 otherwise) |
| `hc_target` | INT | Index into the target set of the object a contact touches (-1 for non-contact nodes) |
| `hc_dir` | FLOAT_VECTOR | Tip axis, unit vector from contact to knuckle |
| `hc_sphere` | FLOAT | Ball radius drawn at the node (knuckles, junctions); 0 = none |
| `hc_locked` | BOOL | Node belongs to a locked tree |
| `hc_flags` | INT | Bitfield: manual, override, check-failed, unreachable |

Per-tip overrides (contact diameter, tip length, tip angle, knuckle diameter,
penetration) live in a CollectionProperty on the supports object, keyed by
`hc_tip`. Unset fields fall back to the global parameters.

The graph is a **forest**: every node has at most one parent, every tree ends
at exactly one foot, and there are no cycles. Edges always go from a node to
its `hc_parent`. Every graph write must keep this true; the validator in §10
asserts it.

### 5.3 Geometry Nodes build (`HC_SupportGeometry`)

1. **Segments**: Mesh to Curve on all graph edges, then Curve to Mesh with
   a unit circle profile (`profile_resolution`, default 12) and capped ends.
   Its `Scale` input is fed `hc_radius`; Blender 5.x scales the profile by
   this input rather than by the curve radius.
2. **Tips** need no special case. The contact node's `hc_radius` is
   `contact_diameter / 2`, so the contact→knuckle tube is a cone. Contact
   nodes sit `penetration` inside the model along `-hc_dir`.
3. **Knuckles and junctions**: Instance on Points (a UV sphere scaled by
   `hc_sphere`, on points where it is > 0), then Realize Instances.
4. **Feet**: the trunk's last node sits at `foot_height` straight above the
   foot. The foot node at z = 0 has `hc_radius = foot_radius`, so the last
   tube flares into a cone. Straight-run decimation never removes that
   node, or the whole trunk would become one tall cone (this happened in
   M2 testing). Feet are always generated, for bed adhesion.
5. **Output**: overlapping closed shells. Slicers union overlapping shells
   within one part, so this is enough for slicing. A **Bake watertight**
   option (Mesh to Volume → Volume to Mesh, voxel size
   `0.05 mm`) produces a single manifold shell for STL users. It is off by
   default because it is slow.

All GN inputs that are purely visual (profile resolution, sphere segments)
are exposed on the modifier, so they update live with no regeneration. In
Blender 5.x these inputs are set through
`mod.properties.inputs.<socket identifier>.value`; they are no longer ID
properties on the modifier.

## 6. Generation pipeline

`Generate` runs these stages. Each stage is a pure function in `core/` that
takes and returns numpy arrays, plus a thin `mathutils` BVH/KD-tree adapter.
Locked trees are loaded first and are never changed by the pipeline.

### 6.1 Preprocess
- Get every target's evaluated mesh and concatenate them into one set of
  numpy arrays: vertex positions (mm, world space), triangles (from loop
  triangles), face normals, face areas, vertex adjacency, and a per-face
  `target_index`.
- **Generate** builds one world-space BVH in mm per target, in C:
  `bmesh.from_object`, then `bmesh.ops.transform`, then
  `BVHTree.FromBMesh`. This takes about 0.8 s for 500k triangles, against
  about 2 s from Python lists. The same trees serve the inside test and the
  obstacle queries (`Obstacles` protocol: nearest distance and first ray
  hit, taking the minimum over targets), so every target is an obstacle for
  every other target's tips and branches.
- **Preview Contacts** needs only the inside test. It builds an object-space
  BVH (`BVHTree.FromObject`) for every closed target and maps query points
  into object space (inside/outside is unchanged by an affine map). Every
  target needs one, not just overlapping ones, because the bridging test
  (§6.2 step 3) asks about a target's own material.
- **Overlapping parts**: multi-part minis often intersect (an arm pushed into
  the body). Any face, sample or contact that lies **inside another target**
  is discarded, because it will never print as a surface. "Inside" is decided
  with a BVH nearest-point query on the other target: the point is inside if
  its offset to the nearest point lies on the inner side of that face's
  normal. When the nearest point is ambiguous (|cos| between the offset and
  the normal < 0.3, i.e. near an edge or vertex), fall back to an odd/even
  ray-count test on 3 skewed rays, taking the majority. Faces are tested at
  their centroids, and samples and contacts at their own position. Targets
  that are not closed meshes produce a warning, and this test is skipped for
  them. The core sees this only as an `InsideTester` protocol
  (`core/spatial.py`), so it stays free of bpy; tests use an exact numpy
  ray-parity version.

### 6.2 Contact detection
A face **needs support** when `normal.z < -sin(overhang_angle)`.
`overhang_angle` is measured from vertical, default 45°. Faces with a lowest
vertex below `bed_ignore_height` (default 0.2 mm) are skipped. Detection runs
per target, using the shared BVH for occlusion and the inside test.
Connected patches and local minima never span two targets. A part's lowest
point always needs a support, even if it touches another part, unless that
point is inside the other part.

1. **Overhang sampling**: group faces that need support into connected
   patches. Sample each patch with area-weighted Poisson-disk sampling at
   `tip_spacing` (default 1.5 mm). Spacing is scaled down by
   `steepness_density` as the face approaches horizontal.
2. **Islands / local minima**: a vertex becomes a forced `MINIMUM` contact
   when it is at least `0.01 mm` below every 1-ring neighbour **and** its
   vertex normal points down. The depth stops float32 noise on flat faces
   reading as minima. A shallow real minimum lies inside an overhang patch
   and is caught by the next rule instead.

   Each overhang patch also gets a `PATCH_LOWEST` contact at its lowest
   vertex, using that patch face's normal rather than the vertex normal,
   which leans into the walls on a rim. The one exception: the point is
   skipped when it is within `max_bridge / 2` of one of the patch's
   supported boundaries, because the patch hangs from that support (a
   bridge, or a short cantilever root).

   Forced contacts are never dropped by the bridging step. Minima win over
   patch-lowest points when deduplicating.
3. **Bridging** (revised in M3): a sample is dropped as bridgeable only
   when it is held from **two sides**.
   - Just below it there must be model material in two directions at least
     120° apart (12 azimuth bins, 4 bins apart).
   - The nearest such material on the two sides must be no more than
     `max_bridge` apart in total (default 2 mm), left gap plus right gap.
   - Material is probed by the inside test at `0.4 × max_bridge` below the
     sample (0.8 mm) **and** at half that depth (0.4 mm), on rings out to
     `max_bridge`. So bumps shallower than that, and a separate part across
     an air gap, do not anchor. Deep probes are tested first; shallow ones
     only where the deep ones found material.
   - The bed never anchors. Open targets have no inside test, so they never
     anchor, which errs toward more supports. `HC_Force` samples are never
     dropped.

   Material on one side only is the overhang's own sloped continuation, or
   a wall the overhang cantilevers from. The overhang angle already decides
   those, so a cantilever gets contacts right up to its root. Each patch's
   lowest point uses the same test.

   **Why it changed.** The M1 rule counted a patch edge as supported when
   the neighbouring triangle sloped down. On a sculpted 42 mm mini (field
   test, M2) that dropped about 90% of samples: every bump on a sculpt
   slopes down, so nearly every sample was within 1 mm of a "supported"
   edge. The result was 189 contacts (177 of them islands) with most of the
   overhang uncovered. On the synthetic organic test model the rule went
   from 61% of overhang faces uncovered to 5%.

   `DetectionResult.stats` records counts after each stage (samples, after
   bridging, after the inside filter) for diagnosing cases like this.
4. **Paint masks**: vertex group `HC_Force` (weight > 0.5) forces sampling
   at the patch spacing even below the overhang threshold. `HC_Block`
   (weight > 0.5) removes all contacts, including islands, and the check
   step reports them. Each target uses its own vertex groups.
5. **Merge near-duplicates**: contacts closer than `0.5 × tip_spacing` are
   merged into one. Islands win.

### 6.3 Tip construction
For each contact `p` with surface normal `n`:
- The tip axis `d` starts as `n`, then is clamped to lie within
  `max_tip_angle` (default 45°) of `-Z`.
- The knuckle position is `k = p + d × tip_length` (default 1.5 mm).
- **Clearance**: cast a ray from `k` toward `p`. It must hit within
  `0.05 mm` of `p`. Four samples along the axis, from 0.5 mm out to the
  knuckle, must each stay at least `cone radius + 0.1 mm` from the model.
  The knuckle check uses its ball radius. `model_clearance` (0.8 mm) is for
  branches; on a 1.5 mm tip it would reject almost every contact on a
  curved surface. The knuckle must also be above the bed.
  - If that fails, retry `d` at 8 azimuths × 3 tilts inside the clamp cone,
    plus straight down, ordered by closeness to `n`. The ring is only
    evaluated when the preferred axis fails, which keeps tips at about
    0.3 s for 400 contacts.
  - If those fail, shorten the tip down to `min_tip_length`, then try
    longer tips (1.5× and 2× `tip_length`) to reach out of crevices.
  - **Branch room (M3).** The knuckle must leave room for its branch:
    `min_radius + model_clearance` of air if possible, otherwise at least
    `min_radius + 0.4 mm` (`BRANCH_HARD_CLEARANCE`). Each pass tries every
    axis and length. Before this rule, knuckles tucked into crevices were
    accepted, and their branches were stuck on the first move. That was
    all 82 stuck branches on the organic test model.
  - If that fails, mark the contact *unreachable* (reported by checks, not
    built).
- The knuckle must not overlap another knuckle. When two do, the lower tip
  is lengthened along its own axis by exactly the amount that gives
  `2 × knuckle radius + 0.01 mm` between them (solved, not estimated).

### 6.4 Branch routing: greedy cone merging
The approach follows Meshmixer and PrusaSlicer organic supports.

State: a set of **active nodes** (initially all knuckles, plus the existing
nodes of locked trees as passive obstacles), each with a position and a
radius.

Sweep downward in steps of `step_z` (default 0.5 mm) from the highest
active node:
1. **Candidate move** for each active node: a target point at
   `z - step_z`, chosen by blending these pulls:
   - straight down;
   - toward the nearest merge partner (KD-tree query, within
     `merge_distance`, default 3 mm);
   - away from the model, along the gradient of the BVH nearest distance
     whenever `distance < radius + model_clearance` (default 0.8 mm).

   The move direction is clamped to `max_branch_angle` from vertical
   (default 40°).
2. **Collision**: the swept segment must keep `radius + model_clearance`
   from the model, checked with BVH nearest queries at segment samples. If
   it fails, try up to 16 directions in the clamp cone. If all fail, the
   node is *stuck* (it is trapped under the model).
3. **Merge**: two active nodes merge into a junction when they are within
   `max(r1, r2) + merge_slack` of each other and the merge direction for both
   stays inside the cone. The new radius is
   `min(max_radius, sqrt(r1² + r2²) × merge_growth)`. Merging into a
   **locked** tree is not allowed in v1, because it would change that tree.
   Branches from different targets **may** merge into one tree.
4. **Taper**: the radius grows by `taper_rate` mm per mm of descent
   (default 0.03), clamped to `[min_radius, max_radius]`. Defaults are
   0.2–2.0 mm radius.
5. **Landing**: a node that reaches `z ≤ 0` becomes a foot. A node with no
   way to the bed may instead land on the model (§6.4.1).
6. **Stuck nodes**: a branch that can neither reach the bed nor land is
   removed. Its tips are marked *unreachable* and reported.

Ordering and determinism: process nodes sorted by `(z desc, x, y)` with
seeded RNG for Poisson sampling. The same input and parameters always give
the same output.

**As built in M2** (`core/routing.py`):
- **Level sweep.** A level falls from the highest knuckle in `step_z` steps
  (default 0.4 mm) to `foot_height`. A head wakes when the level passes it,
  and every awake head then moves down to the level, so heads meet at equal
  heights.
- **Where a head goes.** A head with the model straight below it is
  *blocked* (revised in M3).
  - A blocked head follows a straight free path to the bed found inside its
    cone. The search runs most vertical first: straight down, then rings at
    ¼, ½, ¾ and all of `max_branch_angle`, 16 directions each. The head
    keeps that path while it stays free; with no free path, it takes the
    direction that runs free the longest.
  - Blocked heads neither seek merge partners nor accept them, so a free
    branch is never pulled under the model.
  - A free head moves toward the nearest free head within `merge_distance`
    that is at or below its own height. If that head is also awake, both
    aim for the midpoint. With no partner, it goes straight down.
- **Clearance.** Branches aim for `radius + model_clearance` from the model.
  A move is accepted if it keeps that, or does not lose any of it. Otherwise
  the best of 33 cone candidates is used (the rim, the half-rim and
  straight down). Failing that, a candidate keeping at least
  `radius + 0.4 mm` (the hard minimum) is used, the one with the most room
  first. With no such candidate, the head is stuck.
- **Merging.** Heads within `max(r) + 0.3 mm` merge, but only when every
  member can reach the junction from its previous node inside its cone, so
  no branch ever exceeds `max_branch_angle`. Heads that aren't there yet
  merge on a later level. The junction sits at the area-weighted mean
  position.
- **Stuck heads.** Simplification: a stuck head drops its **whole tree**
  rather than only back to its last junction, and all the tips it carried
  are reported unreachable. They are drawn as red markers, and the panel
  shows the count.
- **Decimation.** After routing, runs of single-child branch nodes that
  turn less than 3° collapse into one segment (except the node above a
  foot).

### 6.4.1 Landing on the model (M4)
Setting `land_on_model` (default on). The bed is always preferred: a head
only lands when it has no free path to the bed.
- **Where.** A *blocked* head with no escape path looks for a landing spot:
  the most vertical ray in its cone that hits a model surface facing up
  (normal z ≥ 0.3, `LANDING_MIN_UP`). It then steers toward that spot as it
  descends.
- **When.** The head lands once the spot is within `2 × tip_length`
  (`LANDING_REACH`), inside its cone, and in clear sight. A head that gets
  stuck also tries a landing within that reach before giving up.
- **Shape.** The landing is a small tip: a break ball of `knuckle_diameter`
  on the branch end, then a cone tapering to `contact_diameter`, pushed
  `penetration` into the surface. Its node kind is `LANDING` (5). Like a
  foot, it is a tree root.
- **Reporting.** Landings leave a small mark, so each one is listed as an
  INFO finding (not counted as an error or warning), and the panel shows how
  many trees land on the model.

Measured on the three synthetic organic minis (layered shells, 276–449
tips): 5, 8 and 14 unreachable tips (1–5%), with 16, 1 and 0 landings.
Generation takes 1.5–1.9 s.

### 6.5 Incremental routing
`route(..., attach_to=graph)` is used for:
- adding a manual tip;
- moving a tip (delete, then add at the new spot);
- rebuilding a tip with overrides.

A head may end by joining an existing **unlocked** branch or junction node:
below it, inside its cone, within `merge_distance`, with a clear line to it.
The radii from the join down to the root grow area-conserving
(`sqrt(r² + r_new²)`, capped at `max_radius`). Locked trees are obstacles
only. Otherwise the head routes to the bed or lands, as in §6.4.

### 6.6 Write-back
Write nodes and attributes into the supports mesh in one `foreach_set` pass.
Make sure the GN modifier exists, and tag for depsgraph update. Each
operator is a single undo step.

## 7. Manual editing

**Edit Supports** is a modal operator, started from the panel.

| Input | Action |
|---|---|
| LMB on a target | Add a contact (raycast against all targets in the set). Builds its tip and routes it incrementally |
| Ctrl+LMB on a tip/knuckle | Delete the tip and prune its branch back to the first junction that still has other children. A tree left empty is removed |
| LMB drag on a contact | Slide the contact along the surface (continuous raycast). The tip rebuilds live; on release the tree re-routes |
| LMB drag on a junction or branch node | Move the node in the view plane (X/Y/Z constraint keys like `G`). Child and parent segments are re-checked for clearance and angle, and turn red if they violate it |
| L | Toggle lock on the hovered tree |
| T | Tip settings (per-tip overrides) for the hovered tip |
| Esc / RMB | Exit |

- Any manual edit to a tree sets the `manual` flag and **locks** the tree
  automatically. There is a preference to turn this off.
- **Per-support overrides**: when a tip is selected, the N-panel shows that
  tip's contact diameter, tip length, tip angle, knuckle diameter and
  penetration, with a "reset to global" button.
- Picking uses screen-space distance to node positions with a 12 px
  tolerance. Hover highlighting is drawn through the overlay layer.

**Regenerate** rebuilds every unlocked tree from scratch:
- Locked trees and their contacts are kept.
- New contacts within `tip_spacing × 0.5` of a locked contact are dropped.

**Clear Unlocked** and **Clear All** are separate operators. Clear All asks
for confirmation.

**As built in M4** (`core/editing.py`, `core/picking.py`,
`blender/edit_tool.py`):
- The tool works on the support set of the active object (the supports, or
  one of their targets). Hovered nodes are highlighted, and a dragged node
  shows where it will go.
- **Add**: LMB on the model away from any node adds a tip there, routed
  incrementally (§6.5). With no room for a tip, or no way down, nothing is
  added and a warning says why.
- **Drag a tip**: on release, the tip moves to the surface point under the
  mouse and re-routes. The live tip rebuild while dragging is not built.
- **Drag a node**: on release, the node moves in the view plane at its own
  depth, and must stay above the bed. Axis constraints and red violation
  segments are not built; **Check Supports** reports a steep or colliding
  result.
- **Tip settings** (T over a tip): a dialog with contact diameter, tip
  length, penetration, knuckle diameter and maximum tip angle. It opens with
  the set's global values, so confirming it unchanged resets the tip. The
  tip is rebuilt with those values and flagged `override`. Showing a tip's
  current overrides in the N-panel is not built.
- Every edit locks the tree it touches and is one undo step. Auto-lock is
  always on (no preference yet).
- **Lock All** / **Unlock All** in the panel, with the locked count.
- After each edit, the checks run again without the coverage check, which
  needs detection. **Check Supports** runs the full set.
- **Regenerate** (Generate on an existing set) keeps locked trees as built
  above; their tip ids are kept, and new tips are numbered after them.
- **Clear Unlocked** is not built yet. **Clear Supports** deletes the whole
  set.

## 8. Overhang heatmap overlay

- A viewport draw handler (`gpu` module, `POST_VIEW`) draws a colour ramp on
  the faces of every target in the active set, by severity: from "needs
  support" (yellow) to horizontal (red). Faces that need support but are
  farther than `tip_spacing` from any contact are drawn in a magenta gap
  colour, to show coverage gaps.
- **Until M2** there are no support sets yet, so the "active set" is the
  selected mesh objects.
- **Preview Contacts** runs detection (§6.2) on that set and draws the
  contacts as points:
  - green for samples;
  - blue for islands (minima and patch-lowest points);
  - grey for islands removed by `HC_Block`.

  The preview is dropped as soon as one of its objects is edited or moved.
- Unreachable contacts and failed checks are drawn as red markers (M2/M3).
- The overlay recomputes on a 0.25 s timer (at most 4 times a second),
  never inside the draw callback. Depsgraph updates of the set's objects
  mark it dirty.
  - The evaluated mesh is cached, so parameter changes only rebuild colours.
  - GPU batches are rebuilt only when the arrays change.
  - Recomputation uses numpy only (no BVH queries).
- Toggle it in the N-panel and in the viewport overlay popover.

## 9. Parameters and presets

Defaults are for a **42 mm-scale mini, 0.2 mm nozzle, 0.08 mm layers**.
Every value still needs tuning against test prints (M2/M6).

| Group | Parameter | Default | Range |
|---|---|---|---|
| Printer | `nozzle_diameter` | 0.2 mm | 0.1–0.8 |
| | `layer_height` | 0.08 mm | 0.04–0.3 |
| Placement | `lift_height` | 3.0 mm | 0–20 |
| Detection | `overhang_angle` (from vertical) | 45° | 20–80 |
| | `tip_spacing` | 1.5 mm | 0.4–10 |
| | `steepness_density` | 1.5× | 1–3 |
| | `max_bridge` | 2.0 mm | 0–20 |
| | `bed_ignore_height` | 0.2 mm | 0–2 |
| Tips | `contact_diameter` | 0.3 mm | 0.1–1.5 |
| | `penetration` | 0.15 mm | 0–0.6 |
| | `tip_length` | 1.5 mm | 0.4–6 |
| | `min_tip_length` | 0.6 mm | 0.2–3 |
| | `max_tip_angle` (from -Z) | 45° | 0–80 |
| | `knuckle_diameter` | 0.6 mm | 0.3–3 |
| Branches | `min_radius` | 0.2 mm (0.4 mm diameter = 2 × nozzle) | ≥ nozzle/2 |
| | `max_radius` | 2.0 mm | 0.4–8 |
| | `taper_rate` | 0.03 mm/mm | 0–0.2 |
| | `max_branch_angle` (from vertical) | 40° | 0–60 |
| | `merge_distance` | 3.0 mm | 0–20 |
| | `merge_growth` | 1.0 | 0.8–1.3 |
| | `model_clearance` | 0.8 mm | 0.2–5 |
| | `step_z` | 0.4 mm | 0.1–2 |
| Feet | `foot_radius` | 1.5 mm | 0–6 |
| | `foot_height` | 0.4 mm | 0–3 |
| Display | `profile_resolution` | 12 | 6–32 |

**Built-in presets**, all for a 0.2 mm nozzle:

| Preset | `tip_spacing` | `contact_diameter` | `tip_length` | `knuckle_diameter` | `max_radius` | `foot_radius` |
|---|---|---|---|---|---|---|
| *Mini 32 mm* | 1.3 | 0.25 | 1.3 | 0.5 | 1.6 | 1.2 |
| ***Mini 42 mm* (default)** | 1.5 | 0.3 | 1.5 | 0.6 | 2.0 | 1.5 |
| *Mini 50 mm* | 1.8 | 0.35 | 1.8 | 0.7 | 2.4 | 1.8 |

There is also a *0.4 mm nozzle* variant: it doubles `nozzle_diameter` and
`min_radius`, and raises the contact diameter to 0.4. Users can save their
own presets through the standard Blender preset menu (`AddPresetBase`).

## 10. Printability checks

**As built in M3** (`core/checks.py`):
- The checks run automatically at the end of **Generate**, and again on
  demand with **Check Supports**.
- Findings are stored on the supports object (`HC_SupportSet.findings`) and
  listed in the panel, errors first.
- **Frame Finding** puts the 3D cursor on the selected finding and centres
  the view on it.
- Every finding is drawn as a viewport marker: red for errors, orange for
  warnings.
- Findings are grouped so a bad setting does not produce thousands of rows:
  one per tree for thin, steep, collision and slender, and one per
  connected gap region for coverage.
- Generate also records the unreachable contacts on the supports object
  (`unreachable_json`), so Check Supports can report them later.

| Check | Rule | Severity |
|---|---|---|
| Graph validity | Forest invariant (§5.2), every tree has exactly one foot at Z = 0 | Error |
| Unsupported contacts | Contacts marked unreachable or blocked (including blocked islands) | Error |
| Coverage gaps | Connected regions of overhang faces farther than `1.5 × tip_spacing` from any contact, reported when at least `tip_spacing²` in area | Warning |
| Too thin | Segment diameter < `2 × nozzle_diameter` (tips excluded); contact diameter < `nozzle_diameter × 0.75` | Warning |
| Too steep | Segment angle from vertical > `max_branch_angle + 5°` (catches manual edits) | Warning |
| Model collision | Non-tip segment, sampled every 0.5 mm, with less than `min(0.5 × model_clearance, 0.4 mm) − 0.05 mm` of air to the model | Error |
| Slenderness | Trunk height / mean diameter > 40 (wobble risk) | Warning |
| Floating start | Foot radius < nozzle diameter (footprint < `π × nozzle²`) | Warning |

The checks are also exposed as a function, so the CI tests and a future
slicer-based layer check (v2) can reuse them.

## 11. Export

**Export 3MF** is pure Python (`zipfile` + `xml.etree`), with no
dependencies.
- It writes one build item: a parent object whose mesh **components** are
  one per target (named after the Blender object) plus `"<set>_supports"`,
  in mm (`unit="millimeter"`). The parts of a multi-part mini are
  physically tied together by shared trees, so they stay one object. Bambu
  Studio and OrcaSlicer import this as one object with N + 1 parts, so the
  user can assign support filament or interface settings to the supports
  part.
- It optionally adds `Metadata/model_settings.config` with part names, which
  Bambu/Orca read. Without it the file is still a valid core 3MF.
- Options: bake watertight before export, include the model (on by default),
  apply the model's modifiers (on by default).
- Must be verified by opening the output in Bambu Studio, OrcaSlicer,
  PrusaSlicer and Cura (see §13).

**Export STL** writes binary STL in mm, with one of two options:
- *merged*: all targets and supports in one file;
- *separate*: one file per target plus `_supports`.

## 12. Package layout

```
handycactus/
  blender_manifest.toml      # id "handycactus", blender_version_min "5.2.0",
                             # license ["SPDX:GPL-3.0-or-later"],
                             # permissions: files = "Export 3MF/STL"
  __init__.py                # register/unregister
  core/                      # NO bpy imports; numpy + small adapter protocol
    mesh_data.py             # numpy mesh container, adjacency
    spatial.py               # BVH/KD-tree protocol + mathutils adapter
    detection.py             # §6.2
    tips.py                  # §6.3
    routing.py               # §6.4–6.5
    graph.py                 # support graph, invariants, prune, radii
    checks.py                # §10
    params.py                # dataclass of all parameters (mm)
  blender/
    props.py                 # PropertyGroups, presets
    operators.py             # generate, regenerate, clear, place-on-bed, check, lock
    edit_tool.py             # §7 modal operator + WorkSpaceTool
    overlay.py               # §8 gpu draw handler
    gn_builder.py            # creates/updates HC_SupportGeometry node group in Python
    graph_io.py              # support graph <-> mesh attributes
    panels.py                # N-panel "HandyCactus"
  export/
    threemf.py
    stl.py
  presets/
tests/
  core/                      # pytest outside Blender (numpy + pure-python spatial stub)
  blender/                   # run via `blender -b --factory-startup --python-exit-code 1 -P`
  fixtures/                  # sample minis (CC0), overhang test shapes
```

- The node group is built in code (`gn_builder.py`), not appended from a
  `.blend`, so it can be diffed and versioned. It is rebuilt when the stored
  `hc_gn_version` is older than the add-on's.
- **CI**: GitHub Actions runs the core tests on Python 3.11. A headless
  Blender job runs the Blender tests on the newest stable release (5.2 LTS
  today) and builds the extension zip with
  `blender --command extension build`.
- **Version policy**: track the newest stable Blender release and use its
  Geometry Nodes features freely. When a new release ships with a node we
  want, raise `blender_version_min` to it. The add-on has a single target
  user on the latest Blender, so there is no backward-compatibility layer.

## 13. Performance targets and test plan

Reference machine: a 2023 mid-range laptop CPU.

| Case | Target |
|---|---|
| Heatmap update, 500k tris (parameter change) | < 100 ms |
| Heatmap update, 500k tris (geometry edit) | < 100 ms; see note |
| Detection, 500k tris | < 1.5 s |
| Tips + routing, 1,000 tips | < 4 s |
| Incremental add-tip | < 150 ms |
| Generate end to end, typical 42 mm mini (~500k tris over 1–5 parts, ~600 tips) | < 4 s |

**Measured at M3** on the synthetic organic model: a 125k-triangle sculpted
body with layered feather wings, tilted 45°, about 62 mm wide.

| | Before M3 | After M3 |
|---|---|---|
| Overhang faces uncovered | 61% | 5% |
| Contacts (islands) | 109 (82) | ~470 (~360) |
| Contacts unreachable | 18% | 11% |
| Generate | — | 4.1 s |
| Check Supports alone | — | 0.17 s |

**Measured at M2**: Generate on a three-part set (500k-triangle body, an arm
sphere and a box), about 420 tips and 113 trees:
- cold: 3.6–4.9 s;
- warm: 3.2 s, of which detect 1.3 s, routing 0.64 s, BVH builds 0.55 s,
  mesh read 0.3 s and tips 0.26 s.

That is at the < 4 s target, with noisy timings in this container. Most of
the remaining time is per-query Python overhead in the BVH adapter.

**Measured at M1** (Blender 5.2 `bpy` in the CI container, best of 3,
500k-triangle sphere plus an overlapping 8k-triangle part):

| Case | Time |
|---|---|
| Heatmap update, parameter change | 49 ms |
| Heatmap update, parameter change, with coverage gaps | 149 ms |
| Heatmap update, geometry edit | 304 ms |
| Detection end to end (read + inside BVH + detect) | 1.5 s |

Reading the evaluated mesh (about 250 ms) dominates geometry-edit updates.
Two fixes are planned for M2:
- cache each target separately, so editing one part does not re-read the
  others;
- skip the coverage-gap pass while the user is dragging.

Long operations show progress (`wm.progress_begin/update`) and can be
cancelled with Esc. They run in a modal timer operator that processes the
sweep in chunks, so the UI stays responsive.

**Tests**
- Unit tests in `core/`:
  - sampling spacing;
  - minima detection on synthetic shapes (sphere, T-bar, arch, cantilever);
  - tip clamp and clearance;
  - routing invariants (angle, clearance, forest);
  - multi-target sets: the inside test on intersecting parts, no patches
    spanning two targets, cross-target merging;
  - determinism;
  - checks.
- Blender tests: graph write and read back, GN build, undo, locks surviving
  regeneration, round-trip export.
- **Golden prints** (manual, before each release): CC0 minis at 32, 42 and
  50 mm, at least one of them multi-part, printed with a 0.2 mm nozzle.
  Record the success rate, snap-off quality and scarring.
- Slicer import matrix: Bambu Studio, OrcaSlicer, PrusaSlicer, Cura (latest
  stable versions).

## 14. Open questions

None. All six were resolved on 2026-10-19:

1. **Feet**: yes. Every trunk gets a flared foot (§5.3; the default
   `foot_radius` is 1.5 mm).
2. **Junction look**: plain overlapping tubes and spheres. The watertight
   bake is optional.
3. **Trestle/PandaProps parity**: the spec already covers what is needed.
4. **Multiple objects**: one pass covers a whole target set (§5.1, §6.1).
5. **Cross-bracing**: v2.
6. **Blender version**: the latest stable release (5.2 LTS today), tracked
   forward (§12).

New questions found during implementation go here:

7. **Contacts in sub-1 mm crevices** (M3; addressed in M4). On layered sculpts
   (feathers, scales, cloth folds), some contacts sit in gaps too narrow
   for any branch to leave: under 1 mm between two parts of the model.
   With build-plate-only routing they stay unreachable. That was about 11%
   of contacts on the organic test model, reported as errors with markers.
   Supporting them needs **landing on the model** (currently v2), or
   accepting them as unsupported and fixing by hand (M4).
   *Resolution (M4):* both. Landing on the model (§6.4.1) was pulled into
   v1, and contacts on buried surfaces (where shells overlap) are no longer
   generated: a surface point is skipped when the point 0.02 mm outside it
   is still inside a target. The inside test is now a ray winding number
   (exits minus entries, majority of 3 rays), which is correct for
   overlapping shells where crossing parity is not. The rest are fixed by
   hand with the Edit Supports tool.

## 15. Milestones

| M | Scope | Exit criteria |
|---|---|---|
| M0 | Repository, manifest, CI, core param dataclass, empty panel | Extension installs in Blender 5; CI green |
| M1 | Preprocess, detection, heatmap overlay | Synthetic shapes detect correctly; heatmap is responsive |
| M2 | Tips, routing, graph I/O, GN geometry, Generate/Clear, multi-target sets | Golden 42 mm multi-part mini generates in < 4 s and slices |
| M3 | Printability checks + result list | All §10 checks have unit tests |
| M4 | Manual editing tool, overrides, locks, Regenerate | Locks survive Regenerate; edits are undoable |
| M5 | 3MF and STL export | Slicer import matrix passes |
| M6 | Presets, docs, golden prints, extensions.blender.org submission | Published v1.0 |

**v2 backlog**:
- Bambu/Orca headless layer check (slicer CLI + parsed warnings).
- Auto-orientation.
- Rafts and pads.
- Cross-bracing (unless pulled into v1).
- Resin profile.
