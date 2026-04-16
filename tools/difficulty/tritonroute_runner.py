"""
TritonRoute evaluation wrapper.

Runs OpenROAD/TritonRoute on a DEF file and extracts routing metrics
from the log output. Supports both Tier 1 (1 iteration) and Tier 2
(5 iterations) evaluation modes.

Requires OpenROAD built on eda17:
  ~/OpenROAD/build/bin/openroad

Requires simplified LEF:
  /tmp/n16_simplified.tlef
"""

import re
import subprocess
import tempfile
from pathlib import Path


def parse_tritonroute_log(log_text: str) -> dict:
    """Parse TritonRoute stdout and extract routing metrics.

    Args:
        log_text: Full stdout from OpenROAD TritonRoute run.

    Returns:
        Dict with keys:
            iterations: list of {drv_total, shorts, spacing, other} per iteration
            conv_rate: (drv_iter0 - drv_last) / drv_iter0, or None
            gr_usage: GR usage percentage, or None
            wire_length_um: total wire length in microns, or None
    """
    iterations = []

    # Match iteration DRV lines: "Iteration N: Number of violations = X."
    # or just "Number of violations = X." for single-iteration runs
    drv_pattern = re.compile(r"\[INFO DRT-0195\]\s+(?:Iteration \d+: )?Number of violations = (\d+)")
    short_pattern = re.compile(r"\[INFO DRT-0196\]\s+Met short:\s+(\d+)")
    spacing_pattern = re.compile(r"\[INFO DRT-0196\]\s+Met spacing:\s+(\d+)")

    # Split into iteration blocks based on DRV-0195 lines
    drv_matches = list(drv_pattern.finditer(log_text))
    for i, drv_match in enumerate(drv_matches):
        drv_total = int(drv_match.group(1))

        # Find shorts and spacing after this DRV line, before the next one
        start = drv_match.end()
        end = drv_matches[i + 1].start() if i + 1 < len(drv_matches) else len(log_text)
        block = log_text[start:end]

        short_match = short_pattern.search(block)
        spacing_match = spacing_pattern.search(block)

        shorts = int(short_match.group(1)) if short_match else 0
        spacing = int(spacing_match.group(1)) if spacing_match else 0

        iterations.append(
            {
                "drv_total": drv_total,
                "shorts": shorts,
                "spacing": spacing,
                "other": drv_total - shorts - spacing,
            }
        )

    # Convergence rate
    conv_rate = None
    if len(iterations) >= 2 and iterations[0]["drv_total"] > 0:
        first = iterations[0]["drv_total"]
        last = iterations[-1]["drv_total"]
        conv_rate = (first - last) / first

    # GR Usage
    gr_match = re.search(r"\[INFO GRT-0018\]\s+Total GR usage:\s+([\d.]+)%", log_text)
    gr_usage = float(gr_match.group(1)) if gr_match else None

    # Wire length
    wl_match = re.search(r"\[INFO GRT-0019\]\s+Total wire length:\s+([\d.]+)\s*um", log_text)
    wire_length_um = float(wl_match.group(1)) if wl_match else None

    return {
        "iterations": iterations,
        "conv_rate": conv_rate,
        "gr_usage": gr_usage,
        "wire_length_um": wire_length_um,
    }


def run_tritonroute(
    def_path: str,
    lef_path: str,
    openroad_bin: str,
    iterations: int = 5,
    bottom_layer: str = "M4",
    top_layer: str = "M5",
) -> dict:
    """Run TritonRoute on a DEF file and return parsed metrics.

    Args:
        def_path: Path to the input DEF file.
        lef_path: Path to the simplified LEF file.
        openroad_bin: Path to the OpenROAD binary.
        iterations: Number of detailed routing iterations (1 for Tier 1, 5 for Tier 2).
        bottom_layer: Bottom routing layer name (M4 for TN16, M2 for ASAP7).
        top_layer: Top routing layer name (M5 for TN16, M3 for ASAP7).

    Returns:
        Parsed metrics dict from parse_tritonroute_log().

    Raises:
        FileNotFoundError: If def_path, lef_path, or openroad_bin doesn't exist.
        subprocess.CalledProcessError: If OpenROAD exits with error.
    """
    for path, name in [
        (def_path, "DEF"),
        (lef_path, "LEF"),
        (openroad_bin, "OpenROAD"),
    ]:
        if not Path(path).expanduser().exists():
            raise FileNotFoundError(f"{name} not found: {path}")

    tcl_script = f"""\
read_lef {lef_path}
read_def {def_path}
global_route
detailed_route -iterations {iterations} \
    -bottom_routing_layer {bottom_layer} \
    -top_routing_layer {top_layer}
exit
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl", delete=False) as f:
        f.write(tcl_script)
        tcl_path = f.name

    try:
        result = subprocess.run(
            [str(Path(openroad_bin).expanduser()), "-exit", tcl_path],
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
        )
        log_text = result.stdout + result.stderr
        return parse_tritonroute_log(log_text)
    finally:
        Path(tcl_path).unlink(missing_ok=True)
