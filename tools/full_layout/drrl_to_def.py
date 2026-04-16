"""Convert DR-RL test case (.txt) to DEF format for Innovus routing.

Supports both uniform and track_num (non-uniform) track formats.
Uses serializer.Testcase as the unified data model.

Usage:
    # Basic (default scale 0.25, auto layer names)
    python tools/full_layout/drrl_to_def.py \
        -i /tmp/lxp32c_top_drrl.txt \
        -o /tmp/lxp32c_top.def

    # With layer mapping for M2/M3 routing
    python tools/full_layout/drrl_to_def.py \
        -i /tmp/lxp32c_top_drrl.txt \
        -o /tmp/lxp32c_top.def \
        --layermap M2 M3 \
        --pin_sizes 72,72 72,72
"""

import argparse
import sys
import time
from pathlib import Path

# Allow importing serializer from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from serializer import Testcase


def _parse_tuple(s):
    try:
        return tuple(int(p) for p in s.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid tuple format '{s}'")


def main():
    parser = argparse.ArgumentParser(description="Convert DR-RL .txt to DEF for Innovus (supports track_num format)")
    parser.add_argument("-i", "--input", required=True, help="Input DR-RL .txt file")
    parser.add_argument("-o", "--output", required=True, help="Output DEF file")
    parser.add_argument("--layermap", nargs="+", help="Layer names (e.g. M2 M3)")
    parser.add_argument(
        "--x_scale", type=float, default=1.0, help="X-axis scale factor (default 1.0, input coords already in target DBU)"
    )
    parser.add_argument(
        "--y_scale", type=float, default=1.0, help="Y-axis scale factor (default 1.0, input coords already in target DBU)"
    )
    parser.add_argument(
        "--pin_sizes",
        type=_parse_tuple,
        nargs="+",
        help="Pin sizes per layer as 'w,h' in DBU (e.g. 72,72 72,72 for 18nm pin at MICRONS 4000)",
    )
    parser.add_argument("--dbu_microns", type=int, default=4000, help="UNITS DISTANCE MICRONS value (default 4000)")
    args = parser.parse_args()

    print(f"Parsing: {args.input}")
    t0 = time.time()
    tc = Testcase.deserialize(args.input)
    print(f"  Parsed in {time.time() - t0:.1f}s")

    # Defaults
    num_layers = len(tc.tracks)
    layer_map = args.layermap if args.layermap else [f"M{i}" for i in range(num_layers)]
    if args.pin_sizes:
        pin_sizes = [tuple(p) for p in args.pin_sizes]
    else:
        pin_sizes = []
        for groups in tc.tracks:
            p = min(t.spacing for t in groups)
            s = max(p // 4, 1)
            pin_sizes.append((s, s))

    print(f"Writing DEF: {args.output}")
    t0 = time.time()
    tc.to_def(args.output, layer_map, args.x_scale, args.y_scale, pin_sizes, args.dbu_microns)
    elapsed = time.time() - t0

    total_pins = sum(len(net.pins) for net in tc.nets)
    track_groups = sum(len(groups) for groups in tc.tracks)
    print(f"  Written in {elapsed:.1f}s")
    print(f"  Nets: {len(tc.nets)}, Pins: {total_pins}, Obstacles: {len(tc.obstacles)}, Track groups: {track_groups}")


if __name__ == "__main__":
    main()
