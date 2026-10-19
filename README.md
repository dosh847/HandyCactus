# HandyCactus

A Blender extension that generates **resin-style tree supports for FDM
miniature printing**: thin tapered tips that touch the model at points and
snap off cleanly, knuckle joints, and branches that merge into trunks
standing on the build plate.

- Target: Blender **5.2 LTS** and later (latest stable is tracked)
- Defaults: 42 mm scale minis on a 0.2 mm nozzle
- License: GPL-3.0-or-later

The full design is in [`docs/SPEC.md`](docs/SPEC.md).

## Status

**M2: support generation.** The **HandyCactus** tab in the 3D viewport
sidebar (N panel) has presets, every parameter and a **Units** setting
(default: 1 Blender unit = 1 mm, which is how STLs import). Also in the tab:

- **Place on Bed**: moves the selected meshes together so their lowest point
  sits at the lift height.
- **Overhang Heatmap**: colours the faces of the selected meshes that need
  support (yellow to red). Also available in the viewport Overlays popover.
- **Preview Contacts**: shows where tips will touch the model (green
  samples, blue islands, magenta uncovered faces on the heatmap).
- **Generate Supports**: builds tree supports for the selected meshes, as
  one `<name>_HC_Supports` object driven by Geometry Nodes. It is made of
  tips, knuckle balls, branches merging into trunks, and flared feet. Run
  it again to rebuild the set in place. Red dots mark contacts that could
  not reach the bed.
- **Clear Supports**: deletes the selected support sets.

Paint a vertex group named `HC_Force` to force supports on an area, or
`HC_Block` to keep them off it.

Next: printability checks (M3), then manual editing (M4) and 3MF/STL export
(M5).

## Layout

```
handycactus/          the extension (this folder is what gets zipped)
  blender_manifest.toml
  core/               pure Python + numpy, never imports bpy
  blender/            properties, operators, panels
tests/core/           pytest, no Blender needed
tests/blender/        pytest inside Blender (the `bpy` module)
tools/extension.py    validate / build / install-check without a Blender binary
docs/SPEC.md
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
python tools/extension.py install-check dist/handycactus-0.3.0.zip
```

### Installing for development

To install a build: in Blender, go to **Edit → Preferences → Get Extensions →
⌄ → Install from Disk…** and pick the zip from `dist/`.

For a live-editing setup, symlink the `handycactus/` folder into a local
extensions repository, e.g.
`~/.config/blender/5.2/extensions/user_default/handycactus`, then reload
scripts (F3 → *Reload Scripts*).
