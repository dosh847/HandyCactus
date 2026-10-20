# Golden prints (spec section 13)

Before each release, print the test sculpt at 32, 42 and 50 mm with a 0.2 mm nozzle,
using the supports as generated: no hand edits. The release is 1.0 once all three
print and snap off cleanly.

## 1. Build the kit

```sh
pip install bpy==5.2.0
python tools/golden_prints.py --render      # -> dist/golden/
```

For each scale it imports the lich (`tests/fixtures/yks-lich-base-54mm`, tilted and
floating as prepared for supports), scales it by `<scale> / 54`, applies the matching
preset, places it on the bed, generates, and writes:

- `lich-base-54mm_reduced_<scale>mm.3mf`: Bambu Studio / OrcaSlicer layout, riser on;
- `lich-base-54mm_reduced_<scale>mm.png` (with `--render`);
- `stats.md`: the table below.

Use `--model` for another mini and `--source-scale` for its scale.

Kit for 0.10.0 (Blender 5.2, in CI's container):

| Scale | Preset | Tips | Unreachable | Feet | Landings | Generate |
|---|---|---|---|---|---|---|
| 32 mm | MINI_32 | 704 | 11 | 24 | 133 | 7.0 s |
| 42 mm | MINI_42 | 903 | 12 | 29 | 163 | 8.1 s |
| 50 mm | MINI_50 | 912 | 14 | 52 | 195 | 11.0 s |

## 2. Slice

- Printer: any FDM printer with a **0.2 mm nozzle**. Layer height 0.08 mm (the
  presets' value).
- Slicer supports: **off**. The supports part: 2 walls, 10% infill. The model: your
  usual mini profile.
- Note the time and filament the supports cost: slice once as is, then delete the
  supports part and slice again; the difference is the supports. For comparison, also
  slice the bare model with the slicer's own tree supports.

## 3. Print and record

Fill in one row per print. Snap-off and scarring: 1 (bad) to 5 (clean).

| Date | Version | Scale | Printer / filament | Print OK? | Failed tips | Snap-off | Scarring | Supports time / g | Slicer trees time / g | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| | 0.10.0 | 32 mm | | | | | | | | |
| | 0.10.0 | 42 mm | | | | | | | | |
| | 0.10.0 | 50 mm | | | | | | | | |

**Pass** for each print: the print finished, no area sagged or detached, no more
than about 5 failed tips, snap-off and scarring 3 or better.

What to look at:

- **Failed tips**: tips that came loose during the print (stringy blobs under an
  overhang). If many fail, raise **Contact Diameter** by 0.05 mm.
- **Snap-off**: do the tips break at the knuckle, leaving a nub you can trim?
- **Scarring**: pits or bumps where tips touched. If deep, lower **Penetration**.
- **Landings**: marks where branches stood on the model; easy to clean?
- **Stability**: did any trunk wobble (ripples on the model above it) or fall?
- **Unreachable contacts**: the red markers in Blender. Did the areas they mark
  print well enough without support?

Put photos (front, back, underside with supports on, and after removal) in the
release notes or the issue tracker. Report each failure with its scale, the area, and
the parameter you changed to fix it.

## 4. After the prints

- Tuning that fixed a failure goes into the preset values (`core/params.py`, spec
  section 9) and a re-run of all three prints.
- Fill in the slicer import matrix in [RELEASE.md](RELEASE.md).
- When everything passes: bump the version to 1.0.0 and submit (RELEASE.md).
