"""
Analyze difficulty evaluation results.

Usage:
    python -m tools.difficulty.analyze_results difficulty_results.json

    # Export to CSV:
    python -m tools.difficulty.analyze_results difficulty_results.json --csv results.csv
"""

import argparse
import csv
import json


def print_summary_table(results: list[dict]):
    """Print a formatted summary table to stdout."""
    print(f"{'Case ID':<30} {'Overall':<12} {'Density':<12} {'Complexity':<12} {'Track':<12} {'Tier':<10}")
    print("-" * 88)

    for r in results:
        if "error" in r:
            print(f"{r['case_id']:<30} {'ERROR':<12} {r['error']}")
            continue

        profile = r.get("profile", {})
        density = profile.get("density", {})
        complexity = profile.get("complexity", {})
        track = profile.get("track", {})

        def fmt_dim(dim):
            if not dim:
                return "—"
            score = dim.get("score")
            level = dim.get("level", "—")
            if score is None:
                return "N/A"
            return f"{level}({score:.2f})"

        print(
            f"{r['case_id']:<30} "
            f"{r['overall_level']:<12} "
            f"{fmt_dim(density):<12} "
            f"{fmt_dim(complexity):<12} "
            f"{fmt_dim(track):<12} "
            f"{r.get('tier', '—'):<10}"
        )

    # Level distribution
    print("\n--- Level Distribution ---")
    for level in ["Easy", "Medium", "Hard", "Very Hard", "N/A", "Error"]:
        count = sum(1 for r in results if r.get("overall_level") == level)
        if count > 0:
            pct = count / len(results) * 100
            print(f"  {level:<12} {count:>4}  ({pct:.1f}%)")


def export_csv(results: list[dict], csv_path: str):
    """Export results to CSV for further analysis."""
    fieldnames = [
        "case_id",
        "overall_level",
        "tier",
        "density_score",
        "density_level",
        "conv_rate",
        "shorts",
        "complexity_score",
        "complexity_level",
        "avg_per_net",
        "track_score",
        "track_level",
        "drv_i0_cov",
        "drv_iter0",
        "drv_final",
        "gr_usage",
        "wire_length_um",
    ]

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for r in results:
            if "error" in r:
                writer.writerow({"case_id": r["case_id"], "overall_level": "Error"})
                continue

            profile = r.get("profile", {})
            raw_tr = r.get("raw_tritonroute", {})

            row = {
                "case_id": r["case_id"],
                "overall_level": r["overall_level"],
                "tier": r.get("tier", ""),
                "density_score": profile.get("density", {}).get("score"),
                "density_level": profile.get("density", {}).get("level"),
                "conv_rate": profile.get("density", {}).get("metrics", {}).get("conv_rate"),
                "shorts": profile.get("density", {}).get("metrics", {}).get("shorts"),
                "complexity_score": profile.get("complexity", {}).get("score"),
                "complexity_level": profile.get("complexity", {}).get("level"),
                "avg_per_net": profile.get("complexity", {}).get("metrics", {}).get("avg_per_net"),
                "track_score": profile.get("track", {}).get("score"),
                "track_level": profile.get("track", {}).get("level"),
                "drv_i0_cov": profile.get("track", {}).get("metrics", {}).get("drv_i0_cov"),
                "drv_iter0": raw_tr.get("drv_iter0"),
                "drv_final": raw_tr.get("drv_final"),
                "gr_usage": raw_tr.get("gr_usage"),
                "wire_length_um": raw_tr.get("wire_length_um"),
            }
            writer.writerow(row)

    print(f"CSV exported to {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="Analyze difficulty evaluation results")
    parser.add_argument("results_json", help="Path to difficulty_results.json")
    parser.add_argument("--csv", default=None, help="Export to CSV file")
    args = parser.parse_args()

    with open(args.results_json) as f:
        results = json.load(f)

    print_summary_table(results)

    if args.csv:
        export_csv(results, args.csv)


if __name__ == "__main__":
    main()
