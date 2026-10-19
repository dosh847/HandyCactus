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

**M0: skeleton.** The extension installs and shows a **HandyCactus** tab in
the 3D viewport sidebar (N panel), with presets and every parameter.
Support generation arrives in M2; see the milestones in the spec.

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
needs Python 3.13 and is a ~400 MB download:

```sh
pip install bpy==5.2.0 pytest
pytest tests
python tools/extension.py validate
python tools/extension.py build            # -> dist/handycactus-<version>.zip
python tools/extension.py install-check dist/handycactus-0.1.0.zip
```

### Installing for development

To install a build: in Blender, go to **Edit → Preferences → Get Extensions →
⌄ → Install from Disk…** and pick the zip from `dist/`.

For a live-editing setup, symlink the `handycactus/` folder into a local
extensions repository, e.g.
`~/.config/blender/5.2/extensions/user_default/handycactus`, then reload
scripts (F3 → *Reload Scripts*).
