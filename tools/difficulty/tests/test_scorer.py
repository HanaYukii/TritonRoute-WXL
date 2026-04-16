import pytest

from tools.difficulty.scorer import (
    compute_profile,
    normalize_metric,
    score_to_level,
)


class TestNormalizeMetric:
    """Test min-max normalization with optional inversion."""

    def test_midpoint(self):
        # 63% convergence rate, range [27%, 99%], inverted
        score = normalize_metric(0.63, min_val=0.27, max_val=0.99, invert=True)
        assert score == pytest.approx(0.5, abs=0.01)

    def test_easiest_value(self):
        # 99% convergence = easiest → score 0.0 (inverted)
        score = normalize_metric(0.99, min_val=0.27, max_val=0.99, invert=True)
        assert score == pytest.approx(0.0)

    def test_hardest_value(self):
        # 27% convergence = hardest → score 1.0 (inverted)
        score = normalize_metric(0.27, min_val=0.27, max_val=0.99, invert=True)
        assert score == pytest.approx(1.0)

    def test_non_inverted(self):
        # avg_per_net = 3.0, range [1.0, 5.0], not inverted
        score = normalize_metric(3.0, min_val=1.0, max_val=5.0, invert=False)
        assert score == pytest.approx(0.5)

    def test_clamp_above_max(self):
        # Value exceeds range → clamp to 1.0
        score = normalize_metric(0.10, min_val=0.27, max_val=0.99, invert=True)
        assert score == 1.0

    def test_clamp_below_min(self):
        # Value below range → clamp to 0.0
        score = normalize_metric(1.10, min_val=0.27, max_val=0.99, invert=True)
        assert score == 0.0

    def test_zero_range_returns_zero(self):
        # Edge case: min == max → return 0.0
        score = normalize_metric(5.0, min_val=5.0, max_val=5.0, invert=False)
        assert score == 0.0


class TestScoreToLevel:
    def test_easy(self):
        assert score_to_level(0.10) == "Easy"

    def test_medium(self):
        assert score_to_level(0.30) == "Medium"

    def test_hard(self):
        assert score_to_level(0.60) == "Hard"

    def test_very_hard(self):
        assert score_to_level(0.80) == "Very Hard"

    def test_boundary_easy_medium(self):
        # 0.25 is the boundary — should be Medium (exclusive lower bound)
        assert score_to_level(0.25) == "Medium"

    def test_exact_one(self):
        assert score_to_level(1.0) == "Very Hard"

    def test_exact_zero(self):
        assert score_to_level(0.0) == "Easy"


class TestComputeProfile:
    """Test full profile computation from raw metrics."""

    def test_full_profile_all_dimensions(self):
        metrics = {
            "conv_rate": 0.691,  # 69.1% convergence
            "shorts": 8,
            "avg_per_net": 2.1,
            "drv_i0_cov": 0.14,  # 14% CoV
        }
        config = {
            "dimensions": {
                "density": {
                    "metric": "conv_rate",
                    "min_val": 0.27,
                    "max_val": 0.99,
                    "invert": True,
                    "secondary_metric": "shorts",
                },
                "complexity": {
                    "metric": "avg_per_net",
                    "min_val": 1.0,
                    "max_val": 5.0,
                    "invert": False,
                },
                "track": {
                    "metric": "drv_i0_cov",
                    "min_val": 0.0,
                    "max_val": 0.46,
                    "invert": False,
                },
            },
            "levels": [
                {"name": "Easy", "max_score": 0.25},
                {"name": "Medium", "max_score": 0.50},
                {"name": "Hard", "max_score": 0.75},
                {"name": "Very Hard", "max_score": 1.0},
            ],
        }
        profile = compute_profile("test_case_1", metrics, config)

        assert profile["case_id"] == "test_case_1"
        # density: 1 - (0.691 - 0.27) / (0.99 - 0.27) = 1 - 0.585 = 0.415 → Medium
        assert profile["profile"]["density"]["level"] == "Medium"
        assert profile["profile"]["density"]["score"] == pytest.approx(0.415, abs=0.01)
        assert profile["profile"]["density"]["metrics"]["shorts"] == 8
        # complexity: (2.1 - 1.0) / (5.0 - 1.0) = 0.275 → Medium
        assert profile["profile"]["complexity"]["level"] == "Medium"
        # track: 0.14 / 0.46 = 0.304 → Medium
        assert profile["profile"]["track"]["level"] == "Medium"
        # overall = max(Medium, Medium, Medium) = Medium
        assert profile["overall_level"] == "Medium"

    def test_missing_dimension_excluded(self):
        """When a metric is None (N/A), that dimension is excluded."""
        metrics = {
            "conv_rate": None,  # N/A — grid too sparse
            "shorts": None,
            "avg_per_net": 3.5,
            "drv_i0_cov": 0.05,
        }
        config = {
            "dimensions": {
                "density": {
                    "metric": "conv_rate",
                    "min_val": 0.27,
                    "max_val": 0.99,
                    "invert": True,
                    "secondary_metric": "shorts",
                },
                "complexity": {
                    "metric": "avg_per_net",
                    "min_val": 1.0,
                    "max_val": 5.0,
                    "invert": False,
                },
                "track": {
                    "metric": "drv_i0_cov",
                    "min_val": 0.0,
                    "max_val": 0.46,
                    "invert": False,
                },
            },
            "levels": [
                {"name": "Easy", "max_score": 0.25},
                {"name": "Medium", "max_score": 0.50},
                {"name": "Hard", "max_score": 0.75},
                {"name": "Very Hard", "max_score": 1.0},
            ],
        }
        profile = compute_profile("sparse_case", metrics, config)

        assert profile["profile"]["density"]["score"] is None
        assert profile["profile"]["density"]["level"] == "N/A"
        # overall should only consider complexity and track
        # complexity: (3.5 - 1.0) / (5.0 - 1.0) = 0.625 → Hard
        assert profile["overall_level"] == "Hard"

    def test_max_rule_picks_worst(self):
        """Overall level is the maximum across all dimensions."""
        metrics = {
            "conv_rate": 0.30,  # very low convergence → Very Hard
            "shorts": 100,
            "avg_per_net": 1.2,  # low rip-up → Easy
            "drv_i0_cov": 0.05,  # low variance → Easy
        }
        config = {
            "dimensions": {
                "density": {
                    "metric": "conv_rate",
                    "min_val": 0.27,
                    "max_val": 0.99,
                    "invert": True,
                    "secondary_metric": "shorts",
                },
                "complexity": {
                    "metric": "avg_per_net",
                    "min_val": 1.0,
                    "max_val": 5.0,
                    "invert": False,
                },
                "track": {
                    "metric": "drv_i0_cov",
                    "min_val": 0.0,
                    "max_val": 0.46,
                    "invert": False,
                },
            },
            "levels": [
                {"name": "Easy", "max_score": 0.25},
                {"name": "Medium", "max_score": 0.50},
                {"name": "Hard", "max_score": 0.75},
                {"name": "Very Hard", "max_score": 1.0},
            ],
        }
        profile = compute_profile("mixed_case", metrics, config)

        assert profile["profile"]["density"]["level"] == "Very Hard"
        assert profile["profile"]["complexity"]["level"] == "Easy"
        assert profile["overall_level"] == "Very Hard"
