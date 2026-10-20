# HandyCactus

A Blender extension that generates **resin-style tree supports for FDM
miniature printing**: thin tapered tips that touch the model at points and
snap off cleanly, knuckle joints, and branches that merge into trunks
standing on the build plate.

- Target: Blender **5.2 LTS** and later (latest stable is tracked)
- Defaults: 42 mm scale minis on a 0.2 mm nozzle
- License: GPL-3.0-or-later

How to use it: the [user guide](docs/USER_GUIDE.md). The full design is in
[`docs/SPEC.md`](docs/SPEC.md).

## Status

**M6: release preparation** (0.10.0). Version 1.0 follows the golden prints and
the extensions.blender.org review ([release checklist](docs/release/RELEASE.md)).

The **HandyCactus** tab in the 3D viewport sidebar (N panel) has presets, every
parameter and a **Units** setting (default: 1 Blender unit = 1 mm, which is how
STLs import). **My Presets** (the list icon next to the preset menu) saves every
parameter under a name and loads it back; presets live in the extension's user
folder, so updates keep them. Also in the tab:

- **Place on Bed**: moves the selected meshes together so their lowest point
  sits at the lift height.
- **Overhang Heatmap**: colours the faces of the selected meshes that need
  support (yellow to red). Also available in the viewport Overlays popover.
  With a support set (or its model) selected, faces its tips hold up turn
  green and the rest magenta. A tip covers faces near it and at or above its
  height, never below it.
- **Preview Contacts**: shows where tips will touch the model (green
  samples, blue islands, magenta uncovered faces on the heatmap).
- **Contacts and markers** follow the selection: only selected support sets
  (or sets whose model is selected) show them. The eye button next to the
  heatmap toggle hides them.
- **Generate Supports**: builds tree supports for the selected meshes (progress
  shows in the status bar), as
  one `<name>_HC_Supports` object driven by Geometry Nodes. It is made of
  tips, knuckle balls, branches merging into trunks, and flared feet.
  Branches steer around the model to reach the bed, merge into thick trunks
  as in Bambu Studio's tree supports, and thicken with the tips they carry.
  Branches that cannot reach the bed merge and stand on the model, at most
  **Max Landing Diameter** (4 mm) thick. Branches are smoothed as in
  OrcaSlicer and PrusaSlicer organic supports
  ([notes](docs/research/tree-supports.md)). A contact with no room for a
  tip moves to the nearest spot on the same overhang that has room. Tiny
  self-supporting wrinkles get no contacts, and detection fills any gap the
  heatmap would show. Run it again to rebuild the set in place. Red dots mark contacts that could
  not reach the bed.
- **Clear Supports**: deletes the selected support sets.
- **Check Supports**: printability checks (also run after every Generate).
  They flag unreachable contacts, coverage gaps, thin or steep branches,
  branches hitting the model, slender trunks and tiny feet. Findings are
  listed in the panel and marked in the viewport (red errors, orange
  warnings). **Frame Finding** jumps to one.
- **Land on Model** (Branches settings, on by default): a branch that
  cannot reach the bed lands on an upward-facing part of the model with a
  small breakaway tip. Landings are listed as info findings.
- **Edit Supports**: click the model to add a tip, Ctrl+click a tip to
  delete it, drag a tip along the model or drag a branch node, **L** locks
  or unlocks the hovered tree, **T** opens per-tip settings, **Ctrl+Z** /
  **Ctrl+Shift+Z** undo and redo, Esc or right click exits. Every edit is
  one undo step and locks its tree.
- **Lock All / Unlock All**: locked trees are kept when you Generate again.
  **Clear Unlocked** deletes every unlocked tree and keeps the locked ones.
- **Export 3MF / Export STL** (in the support set's panel and File ›
  Export): the model and its supports, in mm. 3MF keeps them as one object
  with a part per mesh, so the supports can get their own settings. Choose
  the slicer layout: **Bambu Studio / OrcaSlicer** (default),
  **PrusaSlicer**, or **Generic**. STL writes one file, or one per part.
  **Bake Watertight** remeshes the supports into one closed shell.
  **Add Riser** (on by default) merges a small 0.5 mm pad into each model part
  at the supports' floor. That way a slicer will not drop the floating model
  onto the bed, even after you split the object into parts.
  Bambu Studio v02.08.02.60/.61 shows "The 3mf file has invalid config" for
  any 3MF it did not write. That is a Bambu bug (fixed in v02.08.03.65): the
  object and parts load normally, so click OK.

Paint a vertex group named `HC_Force` to force supports on an area, or
`HC_Block` to keep them off it.

Next: golden prints ([checklist](docs/release/GOLDEN_PRINTS.md)), the slicer
import matrix, and the extensions.blender.org submission.

## Layout

```
handycactus/          the extension (this folder is what gets zipped)
  blender_manifest.toml
  core/               pure Python + numpy, never imports bpy
  blender/            properties, operators, panels
tests/core/           pytest, no Blender needed
tests/blender/        pytest inside Blender (the `bpy` module)
tools/extension.py    validate / build / install-check without a Blender binary
tools/golden_prints.py  the golden-print 3MFs (32/42/50 mm) and renders
docs/USER_GUIDE.md
docs/SPEC.md
docs/release/         release checklist, listing text, golden-print record
```

## Development

Core tests only need Python 3.11+:

```sh
pip install pytest ruff
ruff check . && ruff format --check .
pytest tests/core
```

Blender tests and the extension build use Blender as a Python module. It
needs Python 3.13 and is a ~400 MB download. The overlay draw test also
needs an offscreen GPU (on Linux: `libegl1 libegl-mesa0 libgl1-mesa-dri`)
and skips itself without one:

```sh
pip install bpy==5.2.0 pytest
pytest tests
python tools/extension.py validate
python tools/extension.py build            # -> dist/handycactus-<version>.zip
python tools/extension.py install-check dist/handycactus-0.10.0.zip
```

### Installing for development

To install a build: in Blender, go to **Edit → Preferences → Get Extensions →
⌄ → Install from Disk…** and pick the zip from `dist/`.

For a live-editing setup, symlink the `handycactus/` folder into a local
extensions repository, e.g.
`~/.config/blender/5.2/extensions/user_default/handycactus`, then reload
scripts (F3 → *Reload Scripts*).

## Test models

`tests/fixtures/yks-lich-base-54mm/`: lich sculpt by
[YKS Miniatures](https://www.myminifactory.com/users/YKSMiniatures), included with
permission for testing. The sculpt is not covered by this repository's licence.
