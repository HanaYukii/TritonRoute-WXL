# Design Spec: Comprehensive Routing Difficulty Scoring System

> **Date:** 2026-04-10
> **Author:** Yuki Lu (design), Claude (documentation)
> **Status:** Approved — pending implementation plan
> **Related issues:** #382 #395 #396 #398

---

## 1. Problem Statement

DR-RL's existing test cases (easy/medium/hard) are too simple to provide sufficient training pressure for VIA mode (RUR). We need to:

1. **Generate harder test cases** that stress the AI router's VIA mode
2. **Objectively prove they are hard** using metrics independent of the AI router itself

The AI router's `avg route/net` is the current difficulty signal, but Prof. Li identified bias (VIA mode untrained, DRV-free mode over-trained). A comprehensive scoring system using multiple routers is needed.

## 2. Design Overview

**Approach: Two-Tier Profile System**

A multi-dimensional difficulty profile where each dimension is scored independently, with a fast screening tier and a full evaluation tier. No weighted sum — the overall difficulty level is the maximum across all dimensions.

### Why this approach

- Current data is insufficient for reliable weight calibration
- Multi-dimensional profiles preserve information for domain knowledge building
- Max-rule is conservative, which suits the goal (prove cases are hard enough)
- Two-tier saves time: most cases can be screened in ~15s

## 3. Difficulty Dimensions

Three orthogonal dimensions, each measuring a distinct aspect of routing difficulty:

### 3.1 Physical Density

**What it measures:** How tight the routing resources are in the physical domain.

| Property | Value |
|----------|-------|
| Primary metric | DRV convergence rate (DRV@iter0 → DRV@iter5 reduction %) |
| Secondary metric | Short count at iter0 |
| Source | TritonRoute (~2min for convergence, ~15s for iter0 only) |
| Applicable to | Generator cases (grid ≤ 200, GR Usage ≥ 7%) + lxp32c tiles |
| Not applicable to | Generator 500×500 (GR Usage 1.2%, no discriminating power) |

**Experimental basis:** Grid shrink experiment — convergence rate drops from 99% (easy, grid 400) to 27% (very hard, grid 100). Shorts appear at GR Usage ≥ 12%.

### 3.2 Abstract Complexity

**What it measures:** How difficult the net shapes are to route on the abstract grid (walk_bias, net topology).

| Property | Value |
|----------|-------|
| Primary metric | AI router avg route/net (average rip-up count per net) |
| Source | DR-RL (~5min) |
| Applicable to | All case types |
| Known bias | VIA mode untrained — use as complementary signal, not sole basis |

**Experimental basis:** Walk-bias sweep — TritonRoute S/N < 0.5 for walk_bias differentiation, confirming this dimension is only measurable by the AI router.

### 3.3 Track Assignment Quality

**What it measures:** How sensitive routing difficulty is to track seed / augmentation variant choices.

| Property | Value |
|----------|-------|
| Primary metric | DRV@iter0 coefficient of variation (CoV) across seeds/variants |
| Source | TritonRoute (~15s x N variants) |
| Applicable to | lxp32c augmented variants, generator cases with multiple seeds |
| Interpretation | High CoV = track assignment is sensitive = region is inherently tricky |

**Experimental basis:** lxp32c full-chip — orig_s0 (different track seed) showed 46% higher DRV@iter0 vs spatial transforms (±3%). Track assignment is an independent difficulty signal.

## 4. Scoring System

### 4.1 Per-Dimension Normalization (0.0 – 1.0)

Each metric is normalized to a 0–1 score using min-max scaling based on experimental data:

| Dimension | Metric | 0.0 (Easiest) | 1.0 (Hardest) | Transform |
|-----------|--------|:-:|:-:|-----------|
| Physical Density | DRV convergence rate | 99% | 27% | Inverted: `score = 1 - (conv - 27) / (99 - 27)` |
| Abstract Complexity | AI avg route/net | 1.0 | TBD (need data) | Linear: `score = (avg - 1.0) / (max - 1.0)` |
| Track Assignment | DRV@iter0 CoV (coefficient of variation) across variants | 0% | 46% | Linear: `score = cov / 46` |

**Notes:**
- Convergence rate is inverted (higher convergence = easier)
- Abstract Complexity upper bound to be set after Phase A data collection
- Values exceeding range are clamped to 0.0 or 1.0

### 4.2 Per-Dimension Level Mapping

| Score Range | Level |
|:-----------:|:-----:|
| 0.00 – 0.25 | Easy |
| 0.25 – 0.50 | Medium |
| 0.50 – 0.75 | Hard |
| 0.75 – 1.00 | Very Hard |

### 4.3 Overall Level

```
overall_level = max(density_level, complexity_level, track_level)
```

No weighted sum. The rationale: for training data generation, a case that is hard on *any* dimension provides training value. Max-rule avoids the need for weight calibration with limited data.

### 4.4 Profile Output Format

```json
{
  "case_id": "grid150_wb060_seed1",
  "profile": {
    "density": {
      "score": 0.72,
      "level": "Hard",
      "metrics": { "conv_rate": 0.691, "shorts": 8 }
    },
    "complexity": {
      "score": 0.45,
      "level": "Medium",
      "metrics": { "avg_per_net": 2.1 }
    },
    "track": {
      "score": 0.31,
      "level": "Easy",
      "metrics": { "drv_i0_cov": 0.14 }
    }
  },
  "overall_level": "Hard",
  "tier": "full",
  "timestamp": "2026-04-10T12:00:00Z"
}
```

### 4.5 Generator vs lxp32c Handling

| Case Type | Density | Complexity | Track Quality |
|-----------|:-------:|:----------:|:-------------:|
| Generator (grid ≤ 200) | Available | Available | Available (if multiple seeds) |
| Generator (grid 500) | N/A (GR Usage < 7%) | Available | Available (if multiple seeds) |
| lxp32c tiles | Available (GR Usage 26%+) | Available | Available |

When a dimension is N/A, it is excluded from the max-rule. The normalization ranges may differ between generator and lxp32c — this is acceptable since comparisons are within-type (generator vs generator, tile vs tile).

## 5. Two-Tier Evaluation Flow

### 5.1 Tier 1: Fast Screening (~15s per case)

**Tool:** TritonRoute, 1 iteration only (DRV@iter0 + short count)

**Decision logic:**
- DRV@iter0 < 2,000 AND shorts = 0 → "Likely Easy" → skip Tier 2 (unless full profile requested)
- Otherwise → proceed to Tier 2

**Note:** The 2,000 threshold is preliminary, based on grid shrink data (grid 500 = ~1,800 DRV@iter0). To be refined in Phase C.

**Use case:** Batch screening of many generator cases. Saves ~7min per case that screens out as easy.

### 5.2 Tier 2: Full Profile (~7min per case)

| Step | Tool | Time | Output |
|------|------|:----:|--------|
| 1 | TritonRoute, 5 iterations | ~2min | DRV convergence rate, short count, DRV@iter0 |
| 2 | AI router evaluation | ~5min | avg route/net |
| 3 | (Optional) N seed variants via TritonRoute | ~15s × N | DRV@iter0 variance for track quality |

Outputs the full difficulty profile (Section 4.4).

### 5.3 Innovus Calibration Layer

- Sample 10–20% of cases per batch for Innovus verification
- Purpose: validate that TritonRoute-based levels are consistent with Innovus DRV counts
- If TritonRoute says Easy but Innovus says Hard → adjust thresholds
- Long-term goal: establish TritonRoute-Innovus correlation to reduce Innovus dependency

## 6. Validation Plan

### Phase A: Baseline Data Collection (highest priority)

**Generator cases:**
- Configs: grid {100, 150, 200, 300, 500} × wb {0.50, 0.60, 0.70, 0.75} × 3 seeds = ~60 cases
- Per case: TritonRoute convergence (5 iter) + AI router avg/net
- Goal: establish normalization ranges, verify scoring makes sense

**lxp32c tiles (pending availability on eda17):**
- 132 tiles × (TritonRoute DRV@iter0 + AI router avg/net)
- Goal: cross-metric correlation analysis (does density score correlate with complexity score?)

**Questions to clarify with team:**
1. Are the 132 tiles already cut? Format (.txt / .def)?
2. Which machine are they on?
3. Does each tile have AI router avg/net data?
4. How much do tiles vary in net count / density?

### Phase B: Innovus Calibration

- Select 15–20 cases from Phase A spanning all difficulty levels
- Run Innovus routing on each
- Compare: does Innovus DRV count agree with TritonRoute-based level?

### Phase C: Threshold Refinement

- Use Phase A + B data to adjust:
  - Normalization ranges (min/max values)
  - Level thresholds (0.25/0.50/0.75 boundaries)
  - Tier 1 screening threshold (DRV@iter0 < 2,000 cutoff)
- Hold out 20% of cases for validation
- Re-run scoring on holdout set to verify consistency

## 7. Automation Roadmap

### Stage 1: Semi-Automatic (current target)

- Script runs TritonRoute + AI router, outputs metrics table (CSV/JSON)
- Human reviews table, decides difficulty levels
- Thresholds are config values, easy to adjust
- Innovus runs are manual

### Stage 2: Fully Automatic (after Phase C)

- Pipeline: input test case → auto-run Tier 1 → conditional Tier 2 → output profile + level
- Thresholds hardcoded from validated Phase C values
- Innovus sampling automated on a cron schedule or per-batch trigger
- Integration with Golden Test Policy (#403)

## 8. Constraints & Assumptions

| Constraint | Detail |
|-----------|--------|
| Infrastructure | eda17: OpenROAD/TritonRoute, DR-RL, Innovus all available |
| PDK | Generator cases: TN16 (M4/M5); lxp32c: ASAP7 (M2/M3) |
| Time budget | TritonRoute ~15s–2min, AI router ~5min, Innovus ~15min per case |
| Data volume | Generator: can generate unlimited; lxp32c tiles: 132; Innovus results: ~tens |
| Augmentation | Production setting: `--adj-radius 3 --offset-axis x` (DRV=0 verified) |
| Iterative process | Thresholds and normalization ranges will be refined as data accumulates |

## 9. Out of Scope

- Modifying TritonRoute source code for per-net rip-up counts (future work, depends on correlation results)
- Walk_bias differentiation via TritonRoute (proven impossible, S/N < 0.5)
- Weighted sum scoring (insufficient data for weight calibration; max-rule used instead)
- Real-time difficulty estimation during DR-RL training (this is an offline scoring system)

## 10. Success Criteria

1. **Profile coverage:** ≥ 90% of generated cases get a full difficulty profile
2. **Innovus consistency:** TritonRoute-based level agrees with Innovus in ≥ 80% of sampled cases
3. **Discrimination power:** the system can distinguish at least 3 difficulty levels within generator cases AND within lxp32c tiles
4. **Reproducibility:** same case, same profile (deterministic scoring given fixed metrics)
5. **Actionable output:** Prof. Li and team can use profiles to evaluate whether new cases are "hard enough" for VIA mode training
