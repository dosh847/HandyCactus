# HandyCactus User Guide

HandyCactus adds resin-style tree supports to a miniature in Blender, then exports
the model and supports for an FDM slicer. The tips are thin, so they snap off and leave
small marks. The trees merge into a few trunks, which saves material and print time.

This guide covers HandyCactus 0.10 on Blender 5.2 or later.

## 1. Install

1. Download `handycactus-<version>.zip` (or get it from **Edit › Preferences › Get
   Extensions** once it is listed on extensions.blender.org).
2. In Blender: **Edit › Preferences › Get Extensions › ⌄ › Install from Disk…**, pick
   the zip.
3. Open the 3D viewport sidebar (**N**) and choose the **HandyCactus** tab.

HandyCactus needs no internet access. It writes files only when you export, or when
you save one of your own presets.

## 2. The short version

1. Import the mini (File › Import › STL or OBJ). Select it.
2. Pick a **preset** that matches the mini's scale (32, 42 or 50 mm) and your nozzle.
3. Rotate the mini the way you want to print it, then click **Place on Bed**.
4. Click **Generate Supports**.
5. Look at the **Check Supports** findings; fix what matters with **Edit Supports**.
6. **Export 3MF**, open it in your slicer, slice and print.

## 3. Preparing the model

**Units.** STL files have no units. HandyCactus assumes 1 Blender unit = 1 mm, which
is how Blender imports STLs. If the **Size** line in the panel shows a mini 1000×
too big or too small, change **Units**. The panel warns you when a target is larger
than a miniature.

**Several parts.** Select every part that prints together (body, arms, base). They
are one *target set*: their supports share trunks and avoid all the parts. Overlapping
parts are fine.

**Orientation.** Tilting the mini 30–45° backwards is common: fewer flat overhangs
face straight down, and faces and front details stay free of marks. Rotate the objects
yourself. HandyCactus does not choose an orientation.

**Place on Bed** moves the selected parts together so their lowest point sits at
**Lift Height** (3 mm) above the bed. That gap leaves room for trunks under the
lowest point. A mini on its base can sit on the bed instead (Lift Height 0).

## 4. Seeing what needs support

- **Overhang Heatmap** (the toggle in the panel, also in the viewport Overlays
  popover) colours faces that need support, yellow to red by how steep they are.
  With supports generated, faces the tips hold up turn **green** and the rest
  **magenta**. A tip covers faces near it, at or above its height, never below.
- **Preview Contacts** shows where the tips will go before you generate: green
  points on overhangs, blue on *islands* (lowest points that would print in mid-air).
- The eye button next to the heatmap toggle hides contacts and markers. They also hide
  when no support set (or its model) is selected.

**Paint where you want supports.** In Weight Paint mode, make a vertex group named
`HC_Force` to always put tips on an area, or `HC_Block` to keep them off it (faces,
fine details).

## 5. Generating

**Generate Supports** builds a support set named `<model>_HC_Supports`. It is a mesh
driven by Geometry Nodes, so it stays light until you export. Progress shows in the
status bar. On a typical 42 mm mini it takes 5–10 seconds.

A support set has:

- **Tips**: tapered cones that touch the model at **Contact Diameter** and sink
  **Penetration** into it, so they print attached and snap off clean.
- **Knuckles**: small balls between tips and branches. They are the break point.
- **Branches** lean at most **Max Branch Angle** from vertical. They steer around the
  model and merge into thicker trunks (as in Bambu Studio and OrcaSlicer tree
  supports). A branch thickens with the number of tips it carries.
- **Feet**: flared pads on the bed.
- **Landings**: a branch that has no way to the bed lands on an upward-facing part of
  the model with a small breakaway tip (**Land on Model**, on by default). Landings
  are at most **Max Landing Diameter** (4 mm) thick. A thicker tree goes to the bed, or
  is slimmed and flagged when it has no way there.

Generate again after you change parameters: the set is rebuilt in place. Trees you
locked are kept (see §7).

## 6. Checking

**Check Supports** runs after every Generate, and you can run it again any time. The
panel lists the findings. Red markers show errors and orange markers show warnings in
the viewport. **Frame Finding** zooms to the selected one.

| Finding | What to do |
|---|---|
| Contact unreachable (error) | No branch could reach it. Add a tip nearby by hand, or accept it if the area bridges. |
| Coverage gap (warning) | A magenta patch on the heatmap. Add tips, or lower **Tip Spacing**. |
| Thin or steep branch (warning) | Raise **Min Radius** or lower **Max Branch Angle**. |
| Branch hits the model (error) | Drag the branch node away in Edit Supports. |
| Slender trunk (warning) | A tall thin trunk can wobble. Raise **Max Radius**. |
| Thick landing (warning) | A tree on the model is thicker than **Max Landing Diameter** would allow. Expect a bigger mark there. |
| Landing (info) | Where a branch stands on the model. Clean up the mark after printing. |

A few unreachable contacts on a complex sculpt (1–2%) are normal: they sit in
crevices too narrow for any branch.

## 7. Editing by hand

Select the support set and click **Edit Supports**. While the tool runs:

| Input | Action |
|---|---|
| Click on the model | Add a tip there. It joins the nearest tree or gets its own. |
| Ctrl+click a tip | Delete it. |
| Drag a tip | Slide it along the model. |
| Drag a branch node | Move it; the branches re-fit. |
| **L** | Lock or unlock the tree under the mouse. |
| **T** | Settings for the tip under the mouse: contact and knuckle diameter, length, penetration, angle. |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo. |
| Esc or right click | Leave the tool. |

Every edit is one undo step and locks the tree it touched. **Lock All** and **Unlock
All** lock or unlock every tree. **Clear Unlocked** deletes every unlocked tree, which
is a quick way to keep your hand-placed trees and regenerate the rest. **Clear
Supports** deletes the whole set.

## 8. Exporting

Select the support set (or its model) and click **Export 3MF** or **Export STL**
(also under File › Export). The options:

- **Slicer** (3MF): **Bambu Studio / OrcaSlicer** (default), **PrusaSlicer**, or
  **Generic 3MF**. All three write one object with a part per mesh: the model parts
  and the supports. You can give the supports their own settings in the slicer.
- **Include Model**: off exports the supports only.
- **Apply Modifiers**: exports the model as shown in the viewport.
- **Add Riser** (on by default): merges a small 0.5 mm pad into each model part at the
  supports' floor. A slicer drops a floating part onto the bed when you split the
  object; the pad keeps it at its height.
- **Bake Watertight**: remeshes the supports into one closed shell. Slicers handle
  overlapping shells fine, so you only need this for tools that require a manifold.
- STL: **One File**, or **One per Part**.

**In the slicer**: turn the slicer's own supports **off**, and slice. Suggested
settings for the supports part: 2 walls, 0–10% infill. For the model: your usual
settings.

Bambu Studio v02.08.02.60 and .61 say "The 3mf file has invalid config, load geometry
data only" for any 3MF they did not write. That is a Bambu bug, fixed in
v02.08.03.65. The object and its parts load normally: click OK.

## 9. Presets

The preset menu at the top of the panel sets every parameter at once:

| Preset | For |
|---|---|
| Mini 32 mm | 28–32 mm scale minis: thinner tips, closer spacing |
| Mini 42 mm (default) | 35–42 mm scale minis |
| Mini 50 mm | 50 mm+ and large single figures |
| Mini 42 mm, 0.4 mm Nozzle | A standard 0.4 mm nozzle: thicker branches and tips |

The ↺ button re-applies the chosen preset. The **My Presets** menu (the list icon)
saves every parameter under a name, loads one back, or deletes one. Presets are JSON
files in the extension's user folder, so updates keep them.

## 10. Tuning

Change one thing at a time, generate, and look at the heatmap and findings.

| Problem after printing | Try |
|---|---|
| Tips broke off during the print | Raise **Contact Diameter** (0.05 mm at a time) or **Knuckle Diameter**. |
| Tips are hard to remove, or scar the surface | Lower **Contact Diameter** or **Penetration**. |
| Overhangs sagged between tips | Lower **Tip Spacing**, or raise **Steepness Density**. |
| Supports wobbled or fell | Raise **Max Radius** and **Foot Radius**. |
| Too much support material | Raise **Merge Distance**, raise **Overhang Angle** a little. |
| Branches too close to the model | Raise **Model Clearance**. |

Every parameter has a tooltip. The groups: Printer, Placement, Detection, Tips,
Branches, Feet and Display.

## 11. Troubleshooting

- **The Generate button is greyed out**: select at least one mesh object (or a
  support set, to rebuild it).
- **"Larger than a mini; check Units"**: the model was imported in metres or inches.
  Fix **Units**, or scale the objects.
- **Tips on hidden or inner surfaces, or many unreachable contacts on a model with
  holes**: HandyCactus works on open meshes, but it can only tell inside from outside
  on a (nearly) closed one. Close the holes first (*Mesh › Clean Up › Fill Holes*).
- **Generate is slow**: decimate very dense sculpts to about 500k triangles first.
  Support quality is the same.
- **A slicer puts the model on the bed**: keep **Add Riser** on, or don't split the
  object into parts in the slicer.

Report bugs at <https://github.com/dosh847/HandyCactus/issues>. Attach the .blend if
you can.
