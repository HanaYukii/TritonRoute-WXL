# Difficulty Scoring System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a semi-automatic Two-Tier difficulty scoring pipeline that evaluates routing test cases across three dimensions (physical density, abstract complexity, track assignment quality) and outputs multi-dimensional difficulty profiles.

**Architecture:** A Python package (`tools/difficulty/`) in the Dr-RL repo on eda17. The scoring module is pure logic (no external dependencies, fully testable). The evaluation runner wraps TritonRoute via OpenROAD CLI and collects metrics from log output. A batch script orchestrates Tier 1 (fast screening) and Tier 2 (full profile) evaluation, outputting results as JSON + CSV summary table.

**Tech Stack:** Python 3.8+, PyYAML, OpenROAD/TritonRoute (on eda17), pytest

**Target machine:** eda17 (`~/Dr-RL/tools/difficulty/`)

---

## File Structure

```
Dr-RL/tools/difficulty/
├── __init__.py
├── config.yaml                  # Scoring thresholds, normalization ranges, tier settings
├── scorer.py                    # Pure scoring logic: raw metrics → difficulty profile
├── tritonroute_runner.py        # Wraps OpenROAD/TritonRoute: DEF file → metrics dict
├── batch_eval.py                # CLI: orchestrates Tier 1/Tier 2 for a batch of cases
├── analyze_results.py           # CLI: reads results JSON, prints summary table + CSV
└── tests/
    ├── __init__.py
    ├── test_scorer.py           # Unit tests for scoring logic
    └── test_tritonroute_parser.py  # Tests for TritonRoute log parsing
```

---

### Task 1: Scoring Config

**Files:**
- Create: `tools/difficulty/config.yaml`

- [ ] **Step 1: Create config file with all scoring parameters**

```yaml
# Difficulty scoring configuration
# Values are initial estimates from grid shrink + lxp32c experiments.
# Refine after Phase A data collection.

scoring:
  dimensions:
    density:
      metric: conv_rate          # DRV convergence rate (iter0 → iter5)
      min_val: 0.27              # 27% = hardest observed (grid 100)
      max_val: 0.99              # 99% = easiest observed (grid 400)
      invert: true               # higher convergence = easier
      secondary_metric: shorts   # short count at iter0 (informational)
    complexity:
      metric: avg_per_net        # AI router avg route/net
      min_val: 1.0               # 1.0 = routed first try
      max_val: 5.0               # placeholder — update after Phase A
      invert: false              # higher avg/net = harder
    track:
      metric: drv_i0_cov         # DRV@iter0 coefficient of variation across variants
      min_val: 0.0               # 0% = perfectly stable
      max_val: 0.46              # 46% = orig_s0 outlier level
      invert: false              # higher variance = harder

  levels:
    - name: Easy
      max_score: 0.25
    - name: Medium
      max_score: 0.50
    - name: Hard
      max_score: 0.75
    - name: Very Hard
      max_score: 1.0

  tier1:
    drv_iter0_threshold: 2000    # DRV@iter0 < this AND shorts=0 → "Likely Easy"
    shorts_threshold: 0

tritonroute:
  openroad_bin: ~/OpenROAD/build/bin/openroad
  lef_path: /tmp/n16_simplified.tlef
  tier1_iterations: 1            # 1 iteration for fast screening
  tier2_iterations: 5            # 5 iterations for full convergence
```

- [ ] **Step 2: Commit**

```bash
git add tools/difficulty/config.yaml
git commit -m "feat: add difficulty scoring config with initial thresholds"
```

---

### Task 2: Difficulty Scorer — Tests

**Files:**
- Create: `tools/difficulty/tests/__init__.py`
- Create: `tools/difficulty/tests/test_scorer.py`

- [ ] **Step 1: Create test init file**

```python
# tools/difficulty/tests/__init__.py
```

- [ ] **Step 2: Write scorer unit tests**

These test the pure scoring logic with no external dependencies.

```python
# tools/difficulty/tests/test_scorer.py
import pytest
from tools.difficulty.scorer import (
    normalize_metric,
    score_to_level,
    compute_profile,
    LEVELS,
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
            "conv_rate": 0.691,    # 69.1% convergence
            "shorts": 8,
            "avg_per_net": 2.1,
            "drv_i0_cov": 0.14,    # 14% CoV
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
            "conv_rate": None,      # N/A — grid too sparse
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
            "conv_rate": 0.30,     # very low convergence → Very Hard
            "shorts": 100,
            "avg_per_net": 1.2,    # low rip-up → Easy
            "drv_i0_cov": 0.05,   # low variance → Easy
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
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
cd ~/Dr-RL
python -m pytest tools/difficulty/tests/test_scorer.py -v
```

Expected: `ModuleNotFoundError: No module named 'tools.difficulty.scorer'`

- [ ] **Step 4: Commit test file**

```bash
git add tools/difficulty/tests/
git commit -m "test: add scorer unit tests for difficulty scoring system"
```

---

### Task 3: Difficulty Scorer — Implementation

**Files:**
- Create: `tools/difficulty/__init__.py`
- Create: `tools/difficulty/scorer.py`

- [ ] **Step 1: Create package init**

```python
# tools/difficulty/__init__.py
```

- [ ] **Step 2: Implement scorer**

```python
# tools/difficulty/scorer.py
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
                dim_metrics[dim_config["secondary_metric"]] = metrics.get(
                    dim_config["secondary_metric"]
                )
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
            dim_metrics[dim_config["secondary_metric"]] = metrics.get(
                dim_config["secondary_metric"]
            )

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
```

- [ ] **Step 3: Run tests — verify they pass**

```bash
cd ~/Dr-RL
python -m pytest tools/difficulty/tests/test_scorer.py -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tools/difficulty/__init__.py tools/difficulty/scorer.py
git commit -m "feat: implement difficulty scorer with normalize + profile computation"
```

---

### Task 4: TritonRoute Log Parser — Tests

**Files:**
- Create: `tools/difficulty/tests/test_tritonroute_parser.py`

- [ ] **Step 1: Write parser tests with realistic log samples**

TritonRoute outputs metrics to stdout via OpenROAD. The parser needs to extract DRV counts per iteration, short counts, wire length, and GR usage from this output.

```python
# tools/difficulty/tests/test_tritonroute_parser.py
import pytest
from tools.difficulty.tritonroute_runner import parse_tritonroute_log


# Realistic log snippet from TritonRoute detailed routing output
SAMPLE_LOG_ITER0 = """\
[INFO DRT-0195]  Number of violations = 2493.
[INFO DRT-0196]    Met spacing: 2437
[INFO DRT-0196]    Met short:   13
[INFO DRT-0196]    Other:       43
"""

SAMPLE_LOG_5ITER = """\
[INFO GRT-0018] Total GR usage: 12.38%
[INFO GRT-0019] Total wire length: 4166 um
[INFO DRT-0195] Iteration 0: Number of violations = 2493.
[INFO DRT-0196]    Met spacing: 2437
[INFO DRT-0196]    Met short:   13
[INFO DRT-0196]    Other:       43
[INFO DRT-0195] Iteration 1: Number of violations = 1800.
[INFO DRT-0196]    Met spacing: 1750
[INFO DRT-0196]    Met short:   10
[INFO DRT-0196]    Other:       40
[INFO DRT-0195] Iteration 2: Number of violations = 1200.
[INFO DRT-0196]    Met spacing: 1160
[INFO DRT-0196]    Met short:   8
[INFO DRT-0196]    Other:       32
[INFO DRT-0195] Iteration 3: Number of violations = 900.
[INFO DRT-0196]    Met spacing: 870
[INFO DRT-0196]    Met short:   5
[INFO DRT-0196]    Other:       25
[INFO DRT-0195] Iteration 4: Number of violations = 700.
[INFO DRT-0196]    Met spacing: 680
[INFO DRT-0196]    Met short:   3
[INFO DRT-0196]    Other:       17
[INFO DRT-0195] Iteration 5: Number of violations = 638.
[INFO DRT-0196]    Met spacing: 620
[INFO DRT-0196]    Met short:   2
[INFO DRT-0196]    Other:       16
"""


class TestParseTritonRouteLog:
    def test_parse_single_iteration(self):
        result = parse_tritonroute_log(SAMPLE_LOG_ITER0)
        assert result["iterations"][0]["drv_total"] == 2493
        assert result["iterations"][0]["shorts"] == 13

    def test_parse_five_iterations(self):
        result = parse_tritonroute_log(SAMPLE_LOG_5ITER)
        assert len(result["iterations"]) == 6  # iter 0-5
        assert result["iterations"][0]["drv_total"] == 2493
        assert result["iterations"][5]["drv_total"] == 638
        assert result["iterations"][0]["shorts"] == 13
        assert result["iterations"][5]["shorts"] == 2

    def test_convergence_rate(self):
        result = parse_tritonroute_log(SAMPLE_LOG_5ITER)
        # (2493 - 638) / 2493 = 74.4%
        assert result["conv_rate"] == pytest.approx(0.744, abs=0.01)

    def test_gr_usage(self):
        result = parse_tritonroute_log(SAMPLE_LOG_5ITER)
        assert result["gr_usage"] == pytest.approx(12.38)

    def test_wire_length(self):
        result = parse_tritonroute_log(SAMPLE_LOG_5ITER)
        assert result["wire_length_um"] == pytest.approx(4166.0)

    def test_empty_log_returns_none_metrics(self):
        result = parse_tritonroute_log("")
        assert result["iterations"] == []
        assert result["conv_rate"] is None
        assert result["gr_usage"] is None
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd ~/Dr-RL
python -m pytest tools/difficulty/tests/test_tritonroute_parser.py -v
```

Expected: `ImportError: cannot import name 'parse_tritonroute_log'`

- [ ] **Step 3: Commit**

```bash
git add tools/difficulty/tests/test_tritonroute_parser.py
git commit -m "test: add TritonRoute log parser tests with realistic samples"
```

---

### Task 5: TritonRoute Runner — Implementation

**Files:**
- Create: `tools/difficulty/tritonroute_runner.py`

- [ ] **Step 1: Implement log parser and runner**

```python
# tools/difficulty/tritonroute_runner.py
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
    drv_pattern = re.compile(
        r"\[INFO DRT-0195\]\s+(?:Iteration \d+: )?Number of violations = (\d+)"
    )
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

        iterations.append({
            "drv_total": drv_total,
            "shorts": int(short_match.group(1)) if short_match else 0,
            "spacing": int(spacing_match.group(1)) if spacing_match else 0,
            "other": drv_total - (
                (int(short_match.group(1)) if short_match else 0)
                + (int(spacing_match.group(1)) if spacing_match else 0)
            ),
        })

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
) -> dict:
    """Run TritonRoute on a DEF file and return parsed metrics.

    Args:
        def_path: Path to the input DEF file.
        lef_path: Path to the simplified LEF file.
        openroad_bin: Path to the OpenROAD binary.
        iterations: Number of detailed routing iterations (1 for Tier 1, 5 for Tier 2).

    Returns:
        Parsed metrics dict from parse_tritonroute_log().

    Raises:
        FileNotFoundError: If def_path, lef_path, or openroad_bin doesn't exist.
        subprocess.CalledProcessError: If OpenROAD exits with error.
    """
    for path, name in [(def_path, "DEF"), (lef_path, "LEF"), (openroad_bin, "OpenROAD")]:
        if not Path(path).expanduser().exists():
            raise FileNotFoundError(f"{name} not found: {path}")

    tcl_script = f"""
read_lef {lef_path}
read_def {def_path}
global_route
detailed_route -iterations {iterations} -bottom_routing_layer M4 -top_routing_layer M5
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
```

- [ ] **Step 2: Run parser tests — verify they pass**

```bash
cd ~/Dr-RL
python -m pytest tools/difficulty/tests/test_tritonroute_parser.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tools/difficulty/tritonroute_runner.py
git commit -m "feat: implement TritonRoute runner with log parser"
```

---

### Task 6: Batch Evaluation Script

**Files:**
- Create: `tools/difficulty/batch_eval.py`

- [ ] **Step 1: Implement batch evaluation CLI**

This is the main entry point — takes a directory of DEF files, runs Tier 1/Tier 2 evaluation, outputs results JSON.

```python
# tools/difficulty/batch_eval.py
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
from datetime import datetime, timezone
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
    )

    if not tr_result["iterations"]:
        print(f"  WARNING: No DRV data for {case_id}", file=sys.stderr)
        return compute_profile(case_id, {}, scoring_config, tier="error")

    drv_iter0 = tr_result["iterations"][0]["drv_total"]
    shorts_iter0 = tr_result["iterations"][0]["shorts"]

    # Tier 1 decision
    is_likely_easy = (
        drv_iter0 < tier1_config["drv_iter0_threshold"]
        and shorts_iter0 <= tier1_config["shorts_threshold"]
    )

    if tier == 1 or (is_likely_easy and not force_full):
        metrics = {
            "conv_rate": None,  # not available in Tier 1
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
    )

    metrics = {
        "conv_rate": tr_result_full["conv_rate"],
        "shorts": tr_result_full["iterations"][0]["shorts"] if tr_result_full["iterations"] else None,
        "avg_per_net": None,     # requires AI router — run separately
        "drv_i0_cov": None,      # requires multiple variants — run separately
    }

    profile = compute_profile(case_id, metrics, scoring_config, tier="tier2_partial")
    profile["raw_tritonroute"] = {
        "drv_iter0": tr_result_full["iterations"][0]["drv_total"] if tr_result_full["iterations"] else None,
        "drv_final": tr_result_full["iterations"][-1]["drv_total"] if tr_result_full["iterations"] else None,
        "conv_rate": tr_result_full["conv_rate"],
        "gr_usage": tr_result_full["gr_usage"],
        "wire_length_um": tr_result_full["wire_length_um"],
        "iterations": tr_result_full["iterations"],
    }
    return profile


def main():
    parser = argparse.ArgumentParser(description="Batch difficulty evaluation")
    parser.add_argument("--def-dir", required=True, help="Directory containing DEF files")
    parser.add_argument("--tier", type=int, default=2, choices=[1, 2], help="Evaluation tier (1=fast, 2=full)")
    parser.add_argument("--force-full", action="store_true", help="Force Tier 2 on all cases")
    parser.add_argument("--config", default=None, help="Config YAML path (default: tools/difficulty/config.yaml)")
    parser.add_argument("--output", default="difficulty_results.json", help="Output JSON path")
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
            results.append({
                "case_id": def_path.stem,
                "error": str(e),
                "overall_level": "Error",
            })

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
```

- [ ] **Step 2: Commit**

```bash
git add tools/difficulty/batch_eval.py
git commit -m "feat: add batch evaluation CLI with Tier 1/Tier 2 flow"
```

---

### Task 7: Results Analyzer

**Files:**
- Create: `tools/difficulty/analyze_results.py`

- [ ] **Step 1: Implement results analysis script**

Reads the JSON output from batch_eval and produces a human-readable summary table + CSV export for further analysis.

```python
# tools/difficulty/analyze_results.py
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
import sys
from pathlib import Path


def print_summary_table(results: list[dict]):
    """Print a formatted summary table to stdout."""
    # Header
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
        "case_id", "overall_level", "tier",
        "density_score", "density_level", "conv_rate", "shorts",
        "complexity_score", "complexity_level", "avg_per_net",
        "track_score", "track_level", "drv_i0_cov",
        "drv_iter0", "drv_final", "gr_usage", "wire_length_um",
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
```

- [ ] **Step 2: Commit**

```bash
git add tools/difficulty/analyze_results.py
git commit -m "feat: add results analyzer with summary table and CSV export"
```

---

### Task 8: Phase A Experiment — Generator Cases

**Files:**
- Create: `tools/difficulty/experiments/phase_a_generator.sh`

- [ ] **Step 1: Create experiment directory**

```bash
mkdir -p tools/difficulty/experiments
```

- [ ] **Step 2: Write Phase A generator experiment script**

This script generates test cases across grid/wb/seed combinations and runs Tier 2 evaluation on each.

```bash
#!/bin/bash
# tools/difficulty/experiments/phase_a_generator.sh
#
# Phase A: Baseline data collection for generator cases.
# Generates cases across grid/wb/seed combinations, runs TritonRoute evaluation.
#
# Usage: bash tools/difficulty/experiments/phase_a_generator.sh
#
# Prerequisites:
#   - Dr-RL pipeline working (tools/full_layout/layout_pipeline.py)
#   - OpenROAD built (~/OpenROAD/build/bin/openroad)
#   - Simplified LEF (/tmp/n16_simplified.tlef)
#
# Output: ~/phase_a_results/ with DEF files + difficulty_results.json

set -euo pipefail

WORKDIR=~/phase_a_results
DRRL_DIR=~/Dr-RL
NETS=1280
WL=30
SCALE=2.40

GRIDS=(100 150 200 300 500)
WALK_BIASES=(0.50 0.60 0.70 0.75)
SEEDS=(42 123 456)

mkdir -p "$WORKDIR"

echo "=== Phase A: Generator Case Generation ==="
echo "Grids: ${GRIDS[*]}"
echo "Walk biases: ${WALK_BIASES[*]}"
echo "Seeds: ${SEEDS[*]}"
echo "Total cases: $((${#GRIDS[@]} * ${#WALK_BIASES[@]} * ${#SEEDS[@]}))"
echo ""

for GRID in "${GRIDS[@]}"; do
    for WB in "${WALK_BIASES[@]}"; do
        for SEED in "${SEEDS[@]}"; do
            CASE_NAME="grid${GRID}_wb${WB}_seed${SEED}"
            CASE_DIR="$WORKDIR/$CASE_NAME"

            if [ -f "$CASE_DIR/def/${CASE_NAME}.def" ]; then
                echo "SKIP $CASE_NAME (already exists)"
                continue
            fi

            echo "=== Generating $CASE_NAME ==="

            # Generate test case using Dr-RL pipeline
            cd "$DRRL_DIR"
            python tools/generator/generate.py \
                --grid "$GRID" \
                --nets "$NETS" \
                --walk-bias "$WB" \
                --seed "$SEED" \
                --wl "$WL" \
                --scale "$SCALE" \
                --output "$CASE_DIR/case.txt" \
                2>&1 | tail -1

            # Convert to DEF
            python tools/serializer/drrl_to_def.py \
                --input "$CASE_DIR/case.txt" \
                --output "$CASE_DIR/def/${CASE_NAME}.def" \
                --layermap M4 M5 \
                --snap-to-track \
                2>&1 | tail -1

            echo "  Done: $CASE_DIR/def/${CASE_NAME}.def"
        done
    done
done

echo ""
echo "=== Running Tier 2 evaluation on all cases ==="

# Collect all DEF files into a flat directory for batch_eval
EVAL_DIR="$WORKDIR/all_defs"
mkdir -p "$EVAL_DIR"

for GRID in "${GRIDS[@]}"; do
    for WB in "${WALK_BIASES[@]}"; do
        for SEED in "${SEEDS[@]}"; do
            CASE_NAME="grid${GRID}_wb${WB}_seed${SEED}"
            DEF="$WORKDIR/$CASE_NAME/def/${CASE_NAME}.def"
            if [ -f "$DEF" ]; then
                ln -sf "$DEF" "$EVAL_DIR/${CASE_NAME}.def"
            fi
        done
    done
done

cd "$DRRL_DIR"
python -m tools.difficulty.batch_eval \
    --def-dir "$EVAL_DIR" \
    --tier 2 \
    --force-full \
    --output "$WORKDIR/difficulty_results.json"

echo ""
echo "=== Analyzing results ==="
python -m tools.difficulty.analyze_results \
    "$WORKDIR/difficulty_results.json" \
    --csv "$WORKDIR/difficulty_results.csv"

echo ""
echo "Done. Results at:"
echo "  JSON: $WORKDIR/difficulty_results.json"
echo "  CSV:  $WORKDIR/difficulty_results.csv"
```

- [ ] **Step 3: Make executable and commit**

```bash
chmod +x tools/difficulty/experiments/phase_a_generator.sh
git add tools/difficulty/experiments/phase_a_generator.sh
git commit -m "feat: add Phase A generator experiment script"
```

**Note:** The exact generator and serializer CLI arguments may need adjustment based on the current Dr-RL pipeline interface. Check `python tools/generator/generate.py --help` and `python tools/serializer/drrl_to_def.py --help` on eda17 before running.

---

### Task 9: Integration Smoke Test

**Files:** None new — uses existing scripts.

- [ ] **Step 1: Verify scorer works end-to-end with sample data**

Create a quick manual test on eda17 using one existing DEF file:

```bash
cd ~/Dr-RL

# Pick any existing DEF file from previous experiments
DEF_FILE=~/phase4_adjrad3/def/top_case_orig_s0.def  # or any available DEF

# Run single-case Tier 1 evaluation
python -c "
from tools.difficulty.tritonroute_runner import run_tritonroute
from tools.difficulty.scorer import compute_profile
import yaml, json

config = yaml.safe_load(open('tools/difficulty/config.yaml'))
tr = run_tritonroute(
    '${DEF_FILE}',
    config['tritonroute']['lef_path'],
    config['tritonroute']['openroad_bin'],
    iterations=1,
)
print('TritonRoute result:', json.dumps(tr, indent=2, default=str))

metrics = {
    'conv_rate': tr['conv_rate'],
    'shorts': tr['iterations'][0]['shorts'] if tr['iterations'] else None,
    'avg_per_net': None,
    'drv_i0_cov': None,
}
profile = compute_profile('smoke_test', metrics, config['scoring'])
print('Profile:', json.dumps(profile, indent=2))
"
```

Expected: TritonRoute runs, metrics are parsed, profile is computed with density score.

- [ ] **Step 2: Run unit tests one final time**

```bash
cd ~/Dr-RL
python -m pytest tools/difficulty/tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit any fixes needed**

```bash
git add -u tools/difficulty/
git commit -m "fix: integration fixes from smoke test"
```

---

## Verification Checklist

After all tasks are complete, verify:

- [ ] `python -m pytest tools/difficulty/tests/ -v` — all tests pass
- [ ] `python -m tools.difficulty.batch_eval --help` — shows usage
- [ ] `python -m tools.difficulty.analyze_results --help` — shows usage
- [ ] Scorer correctly handles N/A dimensions (None metrics)
- [ ] Scorer correctly applies max-rule for overall level
- [ ] TritonRoute log parser extracts DRV, shorts, convergence rate
- [ ] Config values match spec (conv_rate range 27-99%, CoV range 0-46%)

## Spec Coverage Mapping

| Spec Section | Covered By |
|---|---|
| 3. Difficulty Dimensions | Task 1 (config), Task 2-3 (scorer) |
| 4. Scoring System | Task 2-3 (scorer) |
| 5.1 Tier 1 Fast Screening | Task 6 (batch_eval tier 1 logic) |
| 5.2 Tier 2 Full Profile | Task 6 (batch_eval tier 2 logic) |
| 5.3 Innovus Calibration | Manual process — not automated in Stage 1 |
| 6. Phase A Validation | Task 8 (experiment script) |
| 7. Stage 1 Semi-Automatic | Tasks 6-7 (batch_eval + analyzer) |
| 10. Success Criteria | Task 9 (smoke test) + Phase A results |

## Adaptation Notes

The TritonRoute runner (Task 5) uses a TCL script with `global_route` + `detailed_route`. The exact OpenROAD commands and layer names may vary based on the eda17 setup. Key things to verify on eda17 before running:

1. **Routing layers in TCL script** — The config uses M4/M5 for TN16 generator cases. For ASAP7 lxp32c tiles, change to M2/M3. Consider making this a config parameter.
2. **Generator CLI** — The `tools/generator/generate.py` and `tools/serializer/drrl_to_def.py` paths and arguments are approximations. Check actual CLI interface on eda17.
3. **LEF path** — `/tmp/n16_simplified.tlef` may need to be recreated if eda17 was rebooted. The LEF simplification script should be documented.
