# Issue #382: Generator Difficulty Control — Final Research Report

**Date:** 2026-04-04
**Branch:** `research/382-difficulty-experiment`
**Author:** Yuki (pipeline, experiment design) + AI assistant (implementation)

---

## 1. Problem Statement

The generator produces test cases that are too easy for DR-RL training. Baseline cases have `avg_route/net = 1.0` (every net routed once, zero rip-up), while real designs like lxp32c require `avg_route/net ≈ 1.35`. The goal is to produce cases that are **routable but require multiple rip-up/reroute cycles**, without creating design rule violations (DRVs).

## 2. Implemented Mechanisms

### 2.1 Start-Selection Difficulty (`--difficulty`, `--congestion_cap`)

- Maintains a 2D congestion map (bounding-box overlap count per cell)
- With probability = `difficulty`, each new net's start position is sorted by congestion (highest first)
- `congestion_cap`: cells at/above cap are treated as congestion=0 during sorting, preventing extreme hotspots

### 2.2 Walk Direction Bias (`--walk_bias`)

- During random walk, with probability = `walk_bias`, the next step prefers directions toward higher-congestion cells
- Creates **path-level crossing** (net routes share tracks) without **pin-level clustering** (which causes DRV)
- Complements `--difficulty` which only biases pin start position
- **Key finding:** walk_bias is the primary driver of DR-RL difficulty, even at low net counts

### 2.3 Net Count Increase (`--net_num`)

- Directly increases routing resource competition
- Effective but constrained by DRV: n≥1300 causes seed-dependent DRV explosion

## 3. Experiment Summary

### 3.1 Experiments Conducted

| Version | Cases | Focus |
|---------|-------|-------|
| v3 | 78 | Difficulty sweep, cap sweep, WL sweep, combos |
| v4 | 75 | walk_bias, cap=4, momentum, gradual net count |
| v5 | 36 | Push net count to 1600-1800, d=0.2 probe |
| v6 | 63 | Seed stability, DRV boundary (n=1250-1480), walk_bias effect |
| DR-RL eval | 5 | avg_route/net measurement on key configs |
| **Total** | **257** | |

Note: Case files for ineffective configs were cleaned up. Only reference cases and recommended config cases are retained.

### 3.2 Key Parameters

| Parameter | Effective Value | Notes |
|-----------|:-:|-------|
| difficulty | **0.3** | Only value that works cleanly with cap=3 |
| congestion_cap | **3** | cap=2 trivial, cap≥4 massive DRV |
| walk_bias | **0.5** | Primary DR-RL difficulty driver; alone increases avg/net by 81% |
| net_num | **1280** | DRV-free ceiling; 8/8 seeds DRV=0 |

Eliminated approaches (no effect or catastrophic):
- **WL increase** (min_wl/max_wl): zero effect on routing difficulty at any range
- **Momentum reduction** (0.50-0.70): 73-253 DRV avg, unusable
- **walk_bias alone** (without difficulty): zero effect
- **cap=4**: massive DRV at all difficulty levels
- **d=0.2 cap=3**: Cut_Short violations, less stable than d=0.3
- **n≥1300** with wb=0.5: DRV cliff — 1/8 seeds clean at n=1300, 0/8 at n=1350+

## 4. Results

### 4.1 DRV Boundary (Innovus, v6)

| n | DRV=0 / 8 seeds | DRV avg | Status |
|---|:-:|:-:|---|
| 1250 | 6/8 | 0.2 | Clean (2 marginal DRV=1) |
| 1260 | 7/8 | 0.1 | Clean (1 marginal DRV=1) |
| **1280** | **8/8** | **0.0** | **Perfect — all seeds DRV=0** |
| 1300 | 1/8 | 22.6 | Cliff begins |
| 1350 | 0/8 | 25.1 | All fail |
| 1400+ | 0/5 | 28.8+ | All fail |

The cliff is steep between n=1280 and n=1300 — a phase transition with no gradual middle ground.

**Seed sensitivity:** The H4 (n=1400) DRV≈0 result from v4 was seed-specific luck (s100-102 only). Generator output confirmed identical for same seed (md5 match). DRV variance is inherent to the routing problem at higher net counts.

### 4.2 DR-RL Evaluation

| Config | nets | wb | Innovus DRV | DR-RL avg_route/net | Success |
|--------|:---:|:---:|:---:|:---:|:---:|
| G0 baseline | 1251 | 0 | 0 | **1.000** | True |
| B2 d=0.3 cap=3 | 1251 | 0 | 0 | **1.030** | True |
| **U s100 d=0.3 cap=3 wb=0.5** | **1250** | **0.5** | **0** | **1.865** | **True** |
| H4 d=0.3 cap=3 wb=0.5 | 1400 | 0.5 | 0-2 | 2.009 | True |
| H2 d=0.3 cap=3 | 1500 | 0 | 1-7 | 11.000 | **False** |

**Critical findings:**

1. **walk_bias is the primary DR-RL difficulty driver.** Same net count (n≈1250), adding wb=0.5: avg/net 1.030 → 1.865 (+81%). The difficulty comes from path crossing, not net density.

2. **Innovus opt-iters does NOT predict DR-RL difficulty.** B2 has Innovus opt=2-4 but DR-RL avg/net=1.03. H4 has Innovus opt=1-16 but DR-RL avg/net=2.009.

3. **n=1280 is the DRV-free ceiling.** 8/8 seeds DRV=0. n=1300 cliff: only 1/8 seeds clean.

4. **n=1400→n=1500 is a DR-RL cliff.** avg/net jumps from 2.009 (success) to 11.0 (failure).

## 5. Recommended Configuration

```
--net_num=1280
--difficulty=0.3
--congestion_cap=3
--walk_bias=0.5
--width=500 --height=500 --layers=2 --pitch=100
--obs_num=16 --min_obs_size=3 --max_obs_size=20
--max_pin_num=5 --pin_dist=50,4,33,4
--max_retry_per_net=50
```

| Metric | Value | vs lxp32c (1.35) |
|--------|-------|:-:|
| Innovus DRV | **0** (8/8 seeds) | — |
| DR-RL avg_route/net (est.) | **~1.9-2.0** | +38-48% |
| DR-RL success | True | — |

Why n=1280 over n=1400 (previous recommendation):
- n=1400: DRV=0 only for 3 specific seeds (37%), avg DRV=28.8 on others
- n=1280: DRV=0 for **all 8 tested seeds** (100%), with comparable DR-RL difficulty via walk_bias

## 6. Difficulty Ordering (DR-RL Perspective)

```
G0 (n=1251, baseline)      : avg/net = 1.000  — trivial
B2 (n=1251, d=0.3 cap=3)   : avg/net = 1.030  — trivial
U  (n=1250, d=0.3 cap=3 wb=0.5) : avg/net = 1.865  — target zone
H4 (n=1400, d=0.3 cap=3 wb=0.5) : avg/net = 2.009  — target zone (but DRV-unstable)
H2 (n=1500, d=0.3 cap=3)   : avg/net = 11.0   — too hard (agent fails)
```

## 7. Next Steps

1. **Optional:** DR-RL eval on Y_n1280_s100 to confirm exact avg/net at n=1280
2. **Generate training dataset:** 100-500 cases using n=1280 config with seeds 100-299+
3. **DR-RL training:** Train new model on dataset, evaluate on lxp32c
4. **Curriculum learning:** Consider progressive difficulty (n=1251 → 1280)

## 8. Files

| Path | Description |
|------|-------------|
| `tools/generator/cases/difficulty_v3/` | G0 baseline + B2 reference (2 cases) |
| `tools/generator/cases/difficulty_v4/` | H4 n=1400 (3 seeds) + H2 n=1500 reference (4 cases) |
| `tools/generator/cases/difficulty_v6/` | v6 DRV boundary + walk_bias probes (63 cases) |
| `tools/generator/config.hpp` | All config parameters |
| `tools/generator/core/net_gen.cpp` | Difficulty + walk_bias implementation |

## 9. Code Changes (on this branch)

- `config.hpp`: Added `difficulty`, `congestion_cap`, `walk_bias` fields with parsing and validation
- `net_gen.cpp`: Congestion map, `sortByCongestion()`, `updateCongestion()`, walk direction bias in `searchEngine()`
- `grid.hpp/cpp`: Added `pin_net_` tracking for min-area checks
- `main.cpp`: Updated log output

Note: Min-area prevention (`--min_area_length`) is on a separate branch `feat/381-min-area-prevention` (PR #387).
