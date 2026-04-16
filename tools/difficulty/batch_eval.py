"""
Batch difficulty evaluation script.

Usage:
    # Tier 1 only (fast screening, ~15s per case):
    python -m tools.difficulty.batch_eval --def-dir ~/cases/def/ --tier 1

    # Tier 2 (full profile, ~2min per case for TritonRoute):
    python -m tools.difficulty.batch_eval --def-dir ~/cases/def/ --tier 2

    # Force full profile on all cases (skip Tier 1 screening):
    python -m tools.difficulty.batch_eval --def-dir ~/cases/def/ --tier 2 --force-full

Output: results JSON file at --output (default: difficulty_results.json)
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from tools.difficulty.scorer import compute_profile
from tools.difficulty.tritonroute_runner import run_tritonroute


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def evaluate_case(
    def_path: str,
    config: dict,
    tier: int,
    force_full: bool = False,
) -> dict:
    """Evaluate a single case through Tier 1 or Tier 2.

    Returns a difficulty profile dict.
    """
    tr_config = config["tritonroute"]
    scoring_config = config["scoring"]
    tier1_config = scoring_config["tier1"]

    case_id = Path(def_path).stem

    # --- Tier 1: fast screening ---
    tr_result = run_tritonroute(
        def_path=def_path,
        lef_path=tr_config["lef_path"],
        openroad_bin=tr_config["openroad_bin"],
        iterations=tr_config["tier1_iterations"],
        bottom_layer=tr_config.get("bottom_layer", "M4"),
        top_layer=tr_config.get("top_layer", "M5"),
    )

    if not tr_result["iterations"]:
        print(f"  WARNING: No DRV data for {case_id}", file=sys.stderr)
        return compute_profile(case_id, {}, scoring_config, tier="error")

    drv_iter0 = tr_result["iterations"][0]["drv_total"]
    shorts_iter0 = tr_result["iterations"][0]["shorts"]

    # Tier 1 decision
    is_likely_easy = drv_iter0 < tier1_config["drv_iter0_threshold"] and shorts_iter0 <= tier1_config["shorts_threshold"]

    if tier == 1 or (is_likely_easy and not force_full):
        metrics = {
            "conv_rate": None,  # not available in Tier 1
            "drv_iter0": drv_iter0,
            "shorts": shorts_iter0,
            "avg_per_net": None,
            "drv_i0_cov": None,
        }
        profile = compute_profile(case_id, metrics, scoring_config, tier="tier1")
        profile["tier1_screening"] = "Likely Easy" if is_likely_easy else "Needs Tier 2"
        profile["raw_tritonroute"] = {
            "drv_iter0": drv_iter0,
            "shorts_iter0": shorts_iter0,
        }
        return profile

    # --- Tier 2: full TritonRoute convergence ---
    tr_result_full = run_tritonroute(
        def_path=def_path,
        lef_path=tr_config["lef_path"],
        openroad_bin=tr_config["openroad_bin"],
        iterations=tr_config["tier2_iterations"],
        bottom_layer=tr_config.get("bottom_layer", "M4"),
        top_layer=tr_config.get("top_layer", "M5"),
    )

    metrics = {
        "conv_rate": tr_result_full["conv_rate"],
        "drv_iter0": (tr_result_full["iterations"][0]["drv_total"] if tr_result_full["iterations"] else None),
        "shorts": (tr_result_full["iterations"][0]["shorts"] if tr_result_full["iterations"] else None),
        "avg_per_net": None,  # requires AI router — run separately
        "drv_i0_cov": None,  # requires multiple variants — run separately
    }

    profile = compute_profile(case_id, metrics, scoring_config, tier="tier2_partial")
    profile["raw_tritonroute"] = {
        "drv_iter0": (tr_result_full["iterations"][0]["drv_total"] if tr_result_full["iterations"] else None),
        "drv_final": (tr_result_full["iterations"][-1]["drv_total"] if tr_result_full["iterations"] else None),
        "conv_rate": tr_result_full["conv_rate"],
        "gr_usage": tr_result_full["gr_usage"],
        "wire_length_um": tr_result_full["wire_length_um"],
        "iterations": tr_result_full["iterations"],
    }
    return profile


def main():
    parser = argparse.ArgumentParser(description="Batch difficulty evaluation")
    parser.add_argument("--def-dir", required=True, help="Directory containing DEF files")
    parser.add_argument(
        "--tier",
        type=int,
        default=2,
        choices=[1, 2],
        help="Evaluation tier (1=fast, 2=full)",
    )
    parser.add_argument("--force-full", action="store_true", help="Force Tier 2 on all cases")
    parser.add_argument(
        "--config",
        default=None,
        help="Config YAML path (default: tools/difficulty/config.yaml)",
    )
    parser.add_argument(
        "--output",
        default="difficulty_results.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    config_path = args.config or str(Path(__file__).parent / "config.yaml")
    config = load_config(config_path)

    def_dir = Path(args.def_dir).expanduser()
    def_files = sorted(def_dir.glob("*.def"))

    if not def_files:
        print(f"No DEF files found in {def_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Evaluating {len(def_files)} cases (Tier {args.tier})...")
    results = []

    for i, def_path in enumerate(def_files, 1):
        print(f"[{i}/{len(def_files)}] {def_path.name}...", end=" ", flush=True)
        try:
            profile = evaluate_case(str(def_path), config, args.tier, args.force_full)
            results.append(profile)
            print(f"→ {profile['overall_level']}")
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            results.append(
                {
                    "case_id": def_path.stem,
                    "error": str(e),
                    "overall_level": "Error",
                }
            )

    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to {output_path}")
    print(f"Summary: {len(results)} cases evaluated")
    for level in ["Easy", "Medium", "Hard", "Very Hard", "N/A", "Error"]:
        count = sum(1 for r in results if r.get("overall_level") == level)
        if count > 0:
            print(f"  {level}: {count}")


if __name__ == "__main__":
    main()
