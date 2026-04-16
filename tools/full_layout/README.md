# Full-Layout Augment Pipeline

Generate augmented training data from uncut full-layout routing files.

## Pipeline

```
uncut layout ─→ layout_to_drrl ─→ augment ─→ drrl_to_def ─→ Innovus verify ─→ tile cut
```

## Scripts

| Script | Purpose |
|--------|---------|
| `layout_to_drrl.py` | Convert uncut format → standard DR-RL input (adds missing via/segment lines) |
| `augment.py` | Geometric transforms: mirror, rotate, offset_pins. Pre-processing: snap_to_track, resolve_adjacency, drop_nets |
| `drrl_to_def.py` | Convert DR-RL `.txt` → DEF for Innovus routing |
| `layout_pipeline.py` | One-command orchestrator: chains all steps with parallel processing |

## Quick Start

```bash
# Full pipeline: uncut layout → 20 augmented cases
python tools/full_layout/layout_pipeline.py \
    --workdir /tmp/pipe_run \
    --top-input docs/lxp32c_top_sample.txt \
    --through augment --target 20

# With DEF export
python tools/full_layout/layout_pipeline.py \
    --workdir /tmp/pipe_run \
    --top-input docs/lxp32c_top_sample.txt \
    --through def --def-all --layermap M2 M3

# Individual scripts
python tools/full_layout/layout_to_drrl.py --input raw.txt --output drrl.txt
python tools/full_layout/augment.py --input-dirs /tmp/cases --output-dir /tmp/aug --target 100
python tools/full_layout/augment.py --input-dirs /tmp/cases --output-dir /tmp/aug --target 100 --drop-nets 0.2  # remove 20% of nets
python tools/full_layout/drrl_to_def.py -i drrl.txt -o output.def --layermap M2 M3
```

## Augment Transforms

- **mirror_h / mirror_v** — Flip along x/y axis (works with non-uniform tracks)
- **rotate_90 / 180 / 270** — Clockwise rotation (90/270 require uniform tracks)
- **offset_pins** — Randomly shift pins by ±k grid steps per axis. Tunable via:
  - `--adj-radius N` — Adjacency check radius (default 1). Higher = more spacing between different-net pins
  - `--offset-axis {both,x,y,cross-track}` — Restrict movement direction. `cross-track` moves each pin perpendicular to its layer's routing direction (M2→y, M3→x)
  - `--offset-scoring {random,max-distance}` — Candidate selection: random or pick furthest from other-net pins
  - `--move-ratio F` — Fraction of pins to attempt moving (0.0–1.0, default 0.5)

## Pre-processing (applied before augmentation, affects all variants)

- **snap_to_track** — Align off-track pins to nearest valid track position
- **resolve_adjacency** — Resolve same-layer pin adjacency violations by shifting pins
- **drop_nets** — Randomly remove a fraction of nets (`--drop-nets 0.0–1.0`); applied once on the tile before all variants are generated, so every output variant carries the same reduced net set

## Offset DRV Investigation

`offset_pins` is the sole source of routing DRV — snap-to-track and drop-nets produce 0 DRV on their own. The DRV root cause is different-net pins landing on the same M2 horizontal track, causing Metal_Short when Innovus cannot find non-overlapping paths.

Parameter effects (ASAP7 lxp32c, M2/M3 2-layer, drop 0.4, max-offset 2):

| Parameter | Best value | Effect |
|-----------|-----------|--------|
| `adj-radius` | 4 | 82 → 1 DRV. Most impactful single knob — enforces spacing between different-net pins |
| `offset-axis` | x | 82 → 6 DRV. M3 pins move along x without disturbing M2 track usage. cross-track is worse (124 DRV) because M2 y-movement changes track assignments |
| `offset-scoring` | max-distance | 82 → 37 DRV. Picks candidate furthest from other-net pins |
| `move-ratio` | 0.1–0.3 | 82 → 21 DRV at 0.1. Saturates around 0.3 (same DRV as 0.5) |

Best combinations:

| Settings | DRV | Moved pins |
|----------|----:|-----:|
| `--adj-radius 4 --offset-axis x --offset-scoring max-distance` | **0** | 20% |
| `--adj-radius 3 --offset-axis x --offset-scoring max-distance` | **1** | 29% |

Full experiment data (19 configs): [PR #341 comment](https://github.com/PulsarisAI/DR-RL/pull/341).

## See Also

- [Lyra format analysis](../../docs/lyra-to-drrl-analysis.md)
