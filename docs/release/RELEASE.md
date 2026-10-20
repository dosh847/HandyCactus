# Releasing HandyCactus

Release 1.0 is the M6 exit (spec section 15): golden prints pass, the slicer import
matrix passes, and the extension is published on extensions.blender.org. Until then,
releases are 0.x.

## 1. Readiness review (0.10.0)

Checked against Blender 5.2's own manifest validator (`blender_ext.py`, which
`tools/extension.py validate` runs in strict mode, as the site does on upload) and
the extensions.blender.org add-on guidelines. Re-read the
[guidelines](https://developer.blender.org/docs/handbook/extensions/moderation/guidelines/)
before submitting: this container could not reach the site, so this list is from the
policy as known at the time.

| Requirement | Status |
|---|---|
| Manifest validates strictly (tagline ≤ 64 characters with no end punctuation, tags from the add-on list, permission reasons) | OK, in CI |
| GPL-3.0-or-later licence, LICENSE in the repository | OK |
| `copyright` in the manifest | OK (`2026 dosh847`) |
| Self-contained: no bundled wheels, nothing downloaded; numpy comes with Blender | OK |
| No network access | OK (none used, none declared) |
| File access declared and explained | OK: `files = "Export the model and supports as 3MF or STL"`. Presets are written to the extension's own user folder, which needs no permission |
| Writes only to the extension's user folder or files the user picks | OK (`extension_path_user`; export through the file browser) |
| No changes to Blender preferences, keymaps outside its own tool, or other add-ons | OK. The Edit Supports keys work only while the tool runs |
| Installs, enables and unregisters cleanly | OK (`install-check` in CI; tests register and unregister repeatedly) |
| Works on all platforms | Pure Python + numpy + Geometry Nodes: no platform code. Tested on Linux only. **Test once on Windows and macOS before 1.0** |
| `blender_version_min` | 5.2.0 |
| Name not taken on the site | **Check** "HandyCactus" on extensions.blender.org |
| `website` reachable by the public | **Check**: the GitHub repository must be public, or point `website` at a public page |
| `maintainer` | `dosh847`. The site shows it as the author; "Name <email>" is also allowed |
| Images: icon 256×256, featured image, screenshots | **To do** (section 3) |

## 2. Listing text

**Name:** HandyCactus

**Tagline (52 characters):** Resin-style tree supports for FDM miniature printing

**Tags:** Mesh, Geometry Nodes, Import-Export

**Description:**

> HandyCactus generates tree supports for printing miniatures on FDM printers, in
> the style of resin supports: thin tapered tips that touch the model at a point
> and snap off cleanly, knuckle joints, and branches that merge into a few trunks.
>
> - **Overhang heatmap** that shows what needs support and, once supports exist,
>   which faces they hold up.
> - **Automatic supports** for one part or a whole multi-part mini. Branches steer
>   around the model and merge into trunks like Bambu Studio and OrcaSlicer tree
>   supports, and thicken with the load they carry. Branches with no way to the
>   bed land on the model with a small breakaway tip.
> - **Printability checks** with viewport markers: unreachable contacts, coverage
>   gaps, thin or steep branches, collisions, slender trunks.
> - **Hand editing**: add, delete and drag tips and branches, per-tip settings, lock
>   trees so they survive regeneration, undo and redo.
> - **Export** the model and supports as one 3MF object (Bambu Studio / OrcaSlicer,
>   PrusaSlicer or generic layout) or STL, with an optional riser pad that keeps the
>   model at its height.
> - **Presets** for 32, 42 and 50 mm scale minis and 0.2 or 0.4 mm nozzles, plus
>   your own.
>
> Open the sidebar (N) › HandyCactus. A user guide is at
> https://github.com/dosh847/HandyCactus/blob/main/docs/USER_GUIDE.md.

## 3. Images

The site asks for an icon (256×256 PNG), a featured image (1920×1080) and
screenshots (1920×1080 recommended).

`python tools/golden_prints.py --render` writes 1920×1080 Workbench renders of the
lich with supports. **They show the YKS Miniatures sculpt.** Permission covers the
sculpt as a test model in this repository; using it in a store listing needs the
creator's OK. The alternative is to render a CC0 mini with `--model`.

Shots to capture in Blender (1920×1080, default theme, sidebar open):

1. **Featured:** a mini with its supports, viewport in Solid mode with cavity on.
2. The heatmap on a mini without supports (yellow to red).
3. The heatmap with supports: green covered faces, one magenta gap.
4. The HandyCactus panel with a preset menu and the findings list.
5. Edit Supports: dragging a tip, with the status-bar key hints visible.
6. The exported 3MF in Bambu Studio or OrcaSlicer, sliced, showing the support part.

## 4. Slicer import matrix (spec section 13)

Import the golden-print 3MFs and an STL, then slice. Fill in the version tested.

| Slicer | Version | 3MF loads as one object with parts | Supports part named | Model at its height after split (riser) | STL loads | Notes |
|---|---|---|---|---|---|---|
| Bambu Studio | | | | | | "invalid config" dialog on v02.08.02.60/.61 is expected |
| OrcaSlicer | | | | | | |
| PrusaSlicer | | | | | | Use the PrusaSlicer layout |
| Cura | | | | | | Generic layout |

## 5. Steps

1. Golden prints pass ([GOLDEN_PRINTS.md](GOLDEN_PRINTS.md)), the matrix above
   passes, and the open checks in section 1 are done.
2. Set `version = "1.0.0"` in `handycactus/blender_manifest.toml`, and update the
   README status, the spec's milestone table, and the guide's version line.
3. `ruff check . && pytest tests && python tools/extension.py validate &&
   python tools/extension.py build`. CI green on main.
4. Tag `v1.0.0` and make a GitHub release with `dist/handycactus-1.0.0.zip`.
5. Log in to extensions.blender.org, choose **Upload Extension**, pick the zip, then
   fill in the description, tags and images from sections 2–3. Moderators review new
   extensions, which can take days. Answer their notes on the listing's page.
6. Later releases: upload the new zip as a new version of the listing.
