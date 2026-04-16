"""
Pure scoring logic for the difficulty scoring system.

Takes raw metrics (from TritonRoute + AI router) and produces
a multi-dimensional difficulty profile with per-dimension scores
and an overall difficulty level.

No external dependencies — fully testable without eda17 tools.
"""

from datetime import datetime, timezone

LEVEL_ORDER = ["Easy", "Medium", "Hard", "Very Hard"]


def normalize_metric(
    value: float,
    min_val: float,
    max_val: float,
    invert: bool = False,
) -> float:
    """Normalize a metric value to [0.0, 1.0] using min-max scaling.

    Args:
        value: Raw metric value.
        min_val: Value that maps to the "easy" end (0.0 or 1.0 depending on invert).
        max_val: Value that maps to the "hard" end.
        invert: If True, higher raw values map to lower scores (e.g., convergence rate).

    Returns:
        Score in [0.0, 1.0], clamped if value is outside [min_val, max_val].
    """
    if max_val == min_val:
        return 0.0

    if invert:
        score = 1.0 - (value - min_val) / (max_val - min_val)
    else:
        score = (value - min_val) / (max_val - min_val)

    return max(0.0, min(1.0, score))


def score_to_level(score: float, levels: list[dict] | None = None) -> str:
    """Map a 0-1 score to a difficulty level name.

    Args:
        score: Normalized score in [0.0, 1.0].
        levels: List of {"name": str, "max_score": float} sorted by max_score.
                Defaults to Easy/Medium/Hard/Very Hard at 0.25 intervals.

    Returns:
        Level name string.
    """
    if levels is None:
        levels = [
            {"name": "Easy", "max_score": 0.25},
            {"name": "Medium", "max_score": 0.50},
            {"name": "Hard", "max_score": 0.75},
            {"name": "Very Hard", "max_score": 1.0},
        ]

    for level in levels:
        if score < level["max_score"]:
            return level["name"]
    return levels[-1]["name"]


def compute_profile(
    case_id: str,
    metrics: dict,
    config: dict,
    tier: str = "full",
) -> dict:
    """Compute a full difficulty profile from raw metrics.

    Args:
        case_id: Identifier for the test case.
        metrics: Dict of raw metric values. Use None for unavailable metrics.
        config: Scoring config dict with "dimensions" and "levels" keys.
        tier: "tier1" or "full" — indicates which evaluation tier produced this.

    Returns:
        Profile dict matching the spec format (Section 4.4).
    """
    dimensions = config["dimensions"]
    levels = config["levels"]
    profile = {}
    active_levels = []

    for dim_name, dim_config in dimensions.items():
        primary_metric = dim_config["metric"]
        raw_value = metrics.get(primary_metric)

        if raw_value is None:
            # Dimension not applicable (e.g., density on sparse grid)
            dim_metrics = {primary_metric: None}
            if "secondary_metric" in dim_config:
                dim_metrics[dim_config["secondary_metric"]] = metrics.get(dim_config["secondary_metric"])
            profile[dim_name] = {
                "score": None,
                "level": "N/A",
                "metrics": dim_metrics,
            }
            continue

        score = normalize_metric(
            raw_value,
            min_val=dim_config["min_val"],
            max_val=dim_config["max_val"],
            invert=dim_config.get("invert", False),
        )
        level = score_to_level(score, levels)
        active_levels.append(level)

        dim_metrics = {primary_metric: raw_value}
        if "secondary_metric" in dim_config:
            dim_metrics[dim_config["secondary_metric"]] = metrics.get(dim_config["secondary_metric"])

        profile[dim_name] = {
            "score": round(score, 3),
            "level": level,
            "metrics": dim_metrics,
        }

    # Overall = max level across all active dimensions
    if active_levels:
        overall_level = max(active_levels, key=lambda l: LEVEL_ORDER.index(l))
    else:
        overall_level = "N/A"

    return {
        "case_id": case_id,
        "profile": profile,
        "overall_level": overall_level,
        "tier": tier,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
