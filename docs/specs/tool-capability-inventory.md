# Difficulty Scoring — Tool Capability Inventory

> **Purpose:** This document should be read *before* the scoring rules (Section 3–4 of the design spec). It explains what each tool in the pipeline can and cannot measure, so the rationale behind metric selection, normalization ranges, and N/A policies is grounded in observable evidence rather than assumption.
>
> **Scope:** seven tools that participate in difficulty data generation, measurement, or post-processing.
>
> **Evidence base:** experiments documented in `docs/research-report-difficulty-and-augmentation.md`, `docs/difficulty_scoring_validation_2026-04-13.md`, `docs/tritonroute_experiment.md` (DR-RL-all), and related GitHub issues (#382 #395 #396 #398 #403 #407 #408).

---

## A. Compact Reference Table

| Tool | Provides | What it tells you | Best used for | Main limitation |
|------|----------|-------------------|---------------|-----------------|
| **Generator** | `.txt` abstract cases (grid, nets, pins, obstacles) | How *structurally* complex the input is (net count, walk_bias shape, obstacle layout). Does **not** tell you how hard it is to route. | Data generation; controlling experimental variables (grid size, walk_bias, seed). | Output is an abstract problem definition, not a difficulty ground truth. Two cases with identical generator params can differ wildly in routing difficulty due to random seed. |
| **serializer / DEF converter** | `.def` files consumable by physical routers | A faithful coordinate-space mapping of the abstract case to physical metal layers (M4/M5 or M2/M3). | Bridging abstract → physical domain so TritonRoute / Innovus can route the case. | Introduces track-pitch dependency; incorrect pitch (e.g., 240 nm instead of 80 nm) silently inflates or deflates DRV. Must match LEF exactly. |
| **LXP augment pipeline** | Augmented `.txt` tiles (mirror, rotate, offset, drop-nets) from real layout | Realistic routing cases with controlled difficulty variation via `drop` ratio (net removal) and spatial transforms. | Generating training-set levels (Benson's 8-level set) and validating augmentation quality (DRV-free requirement). | `offset_pins()` can introduce artificial DRV; mitigated by `--adj-radius 3 --offset-axis x`. Drop ratio is the primary difficulty knob but it only varies net *count*, not net *shape*. |
| **TritonRoute** | DRV@iter0, DRV@iter5, conv_rate, shorts, GR Usage, wire length, vias | Physical routing resource pressure: how many violations exist initially, how many the router can fix, and how congested the global routes are. | Physical Density dimension (main scoring); Tier 1 quick-scan; Track Assignment Quality (CoV across seeds). | Not a gold-standard router — simplified LEF (no LEF58/MINSTEP) means DRV counts are proxies, not production-accurate. Cannot see abstract complexity (walk_bias S/N < 0.5). |
| **AI router (`eval.py`)** | `avg_per_net` (average rip-up & reroute count per net) | Abstract routing complexity: how many attempts the RL agent needs to resolve each net on the *abstract* grid. Higher = the net shapes / congestion pattern is harder for the learned policy. | Abstract Complexity dimension (main scoring); identifying cases the AI model cannot solve (`avg/net = 11.0`). | Measures the *model's* difficulty, not the *problem's* inherent difficulty. Biased by training distribution (VIA mode untrained, DRV-free mode over-trained). Failure sentinel (`11.0`) is a timeout artifact, not a calibrated value. |
| **Innovus** | Innovus DRV count (authoritative physical DRC) | Production-grade design-rule compliance with full LEF58/MINSTEP support. The closest available proxy to manufacturing sign-off. | Gold-standard calibration reference; spot-checking TritonRoute-based levels; final validation of augmentation quality. | ~15 min/case; requires Cadence license; available on specific machines (eda17). Not practical for batch scoring. |
| **scorer / batch_eval / analyze_results** | Difficulty profiles (per-dimension score + level + overall), threshold sweep tables, distribution summaries | Post-processed interpretation of raw metrics against configurable normalization ranges. | Automated difficulty labeling; threshold calibration; generating spec-ready validation artifacts. | Garbage-in-garbage-out: profile quality is bounded by input metric quality. Does not generate new data — only transforms existing metrics. |

---

## B. Detailed Tool Profiles

### B.1 Generator (`tools/generator/build/generator`)

**Input:** CLI parameters (grid size, net count, walk_bias, seed, obstacle config, etc.)
**Output:** `.txt` abstract routing case

**What it can see:**
The Generator controls the *structural parameters* of a routing problem — how many nets exist, how tortuous their paths are (walk_bias), how big the grid is, and where obstacles sit. These parameters determine the *potential* for difficulty, but they are not difficulty itself. A 500×500 grid with 1,280 nets at walk_bias=0.7 is structurally identical whether the resulting routing is trivial or catastrophic — that depends on the random pin placement governed by the seed.

**What it cannot see:**
- Physical routing resource pressure (that requires a physical router)
- Whether the AI model can solve the case (that requires eval.py)
- Whether the case is DRC-clean in production (that requires Innovus)

**Bias / failure modes:**
- `walk_bias` changes net *shape* on the abstract grid but is invisible to physical routers (TritonRoute S/N < 0.5; see `docs/research-report-difficulty-and-augmentation.md`, Exp 4).
- Generator capacity limits can silently cap net count (e.g., 150×150 grid cannot fit 2,560 nets with certain obstacle configs).
- Same parameters + different seed → large variance in AI router `avg/net` (1.36–2.54 for training_n1280 baseline; see `results/overnight_v2/overnight_v2_summary.log`).

**Role in scoring:** Data generation only. Generator parameters are *metadata* attached to the profile, not scoring inputs. The scoring system deliberately measures difficulty *after* generation, not *from* generation parameters.

---

### B.2 serializer / DEF Converter (`serializer.py`, `drrl_to_def.py`)

**Input:** `.txt` abstract case (or `.out` AI router solution)
**Output:** `.def` file (Design Exchange Format) with physical coordinates, pins, nets, and tracks

**What it can see:**
The converter maps abstract grid coordinates to physical nanometer-scale coordinates using `x_scale` / `y_scale`, assigns metal layers via `--layermap`, and generates pin geometries with `--pin_sizes`. The resulting DEF is what TritonRoute and Innovus actually route.

**What it cannot see:**
- Whether the track pitch matches the LEF (a mismatch silently produces ~90K false-positive MinStep violations).
- Whether the physical density is sufficient for meaningful TritonRoute metrics (that depends on grid size and scale factor).

**Bias / failure modes:**
- **Track pitch mismatch** is the single most dangerous failure mode. Early experiments used 240 nm pitch (abstract grid step) instead of 80 nm (TN16 LEF pitch), causing all DRV comparisons to be invalid. Fix: dedicated `fix_tracks.py` / `fix_tracks_150.py` scripts that regenerate `TRACKS` from LEF-verified pitches across all 10 metal layers.
- **LEF simplification** is required: TritonRoute does not fully support LEF58 properties or MINSTEP rules. Simplified LEFs (`n16_simplified.tlef`, `asap7_simplified.lef`) strip these rules. This means TritonRoute DRV counts are *proxies* — they will not match production Innovus DRV counts exactly, but the *relative ordering* is preserved (validated: R11 case converges to DRV=0 on both TritonRoute and Innovus).
- **ASAP7 vs TN16 routing layers:** Generator cases use M4/M5, lxp32c uses M2/M3. The `config_lxp32c.yaml` file carries `bottom_layer: M2`, `top_layer: M3` to handle this.

**Role in scoring:** Infrastructure / plumbing. Not a scoring tool — but incorrect conversion silently invalidates all downstream metrics.

---

### B.3 LXP Augment Pipeline (`layout_pipeline.py`, `augment.py`)

**Input:** Real layout tile (`.txt` from Lyra), augmentation parameters (`--drop-nets`, `--target`, `--max-offset`, `--adj-radius`, `--offset-axis`)
**Output:** Augmented `.txt` cases (mirrored, rotated, offset, with subset of nets)

**What it can see:**
The pipeline creates controlled difficulty variation from a single real layout tile. The `drop` parameter directly controls net count (drop=0.7 → keep 30% of nets → Level 1; drop=0.0 → keep all nets → Level 8). Spatial transforms (mirror, rotate) produce structurally equivalent cases for measuring metric stability.

**What it cannot see:**
- Whether augmented cases are DRC-clean (depends on `offset_pins()` behavior and physical router).
- Absolute difficulty — the `drop` parameter creates a *relative* difficulty ordering within one tile, not a universal scale.

**Bias / failure modes:**
- **`offset_pins()` is the sole source of augmentation-induced DRV.** Phase 3 experiments proved: 0 DRV without offset, 93+ DRV with default offset. Production fix: `--adj-radius 3 --offset-axis x` achieves DRV=0 (see `docs/research-report-difficulty-and-augmentation.md`, Part 2).
- Drop ratio controls net *count* but not net *shape* or *congestion pattern*. Two augmented tiles at the same drop ratio can differ in DRV@iter0 if the removed nets happened to be the congestion-causing ones.
- Augmentation diversity vs DRC safety is a tension: higher `--max-offset` or `--move-ratio` increases training data diversity but risks reintroducing DRV.

**Role in scoring:** Data generation for lxp32c cases. The `drop` ratio defines Benson's 8-level training set, which serves as the *ground truth ordering* for cross-validation of the scoring system. The augmentation pipeline itself does not produce scoring metrics.

---

### B.4 TritonRoute (via OpenROAD)

**Input:** `.def` file + simplified `.lef`
**Output:** DRV@iter0, DRV@iterN, shorts, spacing violations, GR Usage%, GR Overflow, wire length, vias

**What it can see:**
TritonRoute performs global routing (GR) followed by detailed routing (DR), producing a rich set of physical metrics:

| Metric | Physical meaning | Scoring role |
|--------|------------------|--------------|
| `DRV@iter0` | How many violations exist after the first detailed-routing pass. Reflects initial routing difficulty before any repair iterations. | **LXP density primary metric** (`config_lxp32c.yaml`); Tier 1 quick-scan input. |
| `DRV@iter5` (or `DRV@final`) | How many violations remain after 5 repair iterations. Reflects residual, unfixable conflicts. | Used to compute `conv_rate`. |
| `conv_rate` | `1 - DRV@final / DRV@iter0`. How much of the initial difficulty the router can resolve. 99% = almost all violations fixable (easy). 27% = most violations persist (very hard). | **Generator density primary metric** (`config.yaml`). Strongest cross-grid-size discriminator (monotonic 99% → 27% across grid 400 → 100). |
| `shorts` | Number of short-circuit violations at iter0. Shorts indicate *real* routing resource conflicts (overlapping wires), not just spacing rule violations. | **Secondary density metric** (informational). Shorts > 0 is a strong "hard" signal; appeared at GR Usage ≥ 12%. |
| `GR Usage%` | Percentage of global routing resources consumed. | Not directly scored, but used to determine whether density scoring is *applicable*. GR Usage < 7% → density metrics lose discriminating power → density dimension should be N/A. |
| `drv_i0_cov` | Coefficient of variation of DRV@iter0 across multiple seeds or augmentation variants of the same case. | **Track Assignment Quality** dimension. High CoV = the case is sensitive to track seed, meaning it sits in a physically tricky region. |
| `wire_length`, `vias` | Total routed wire length and via count. | Not scored. Weak difficulty signals (WL has ~5% cross-wb variation, dominated by seed noise). |

**What it cannot see:**
- **Abstract routing complexity (walk_bias).** TritonRoute operates on physical coordinates; it does not know or care about the abstract grid structure that makes a case hard for the AI router. Walk-bias sweep at 150×150: S/N ratio for DRV@iter0 = 0.50, DRV@iter5 = 0.32. Seed variance exceeds walk-bias variance. This is a *fundamental measurement boundary*, not a density issue.
- **Production-accurate DRC.** Simplified LEF means some rules (LEF58, MINSTEP) are omitted. TritonRoute DRV counts are systematically lower than what a full-rule router would report, but the *relative ordering* is preserved.

**Bias / failure modes:**
- **Low-density regime:** At GR Usage < 7% (e.g., Generator 500×500 / 1,280 nets, Usage ~1.2%), all TritonRoute metrics are effectively flat. `conv_rate` ≈ 0.96–1.00 regardless of walk_bias. The scoring system handles this by setting density = N/A for these cases.
- **Fixed-density wb/seed sweep:** Even at 12% GR Usage (150×150), TritonRoute still cannot separate walk_bias (S/N < 0.5). This confirms the issue is fundamental, not merely a density threshold problem.
- **LEF simplification artifacts:** Removing MINSTEP rules eliminates ~90K false-positive violations but may also suppress a small number of true violations. Validated by comparing R11 case: TritonRoute DRV=0, Innovus DRV=0 — consistent.
- **Non-preferred direction routing:** TritonRoute can route against a layer's preferred direction (e.g., horizontal on a vertical-preferred layer). DR-RL's AI router cannot. This means TritonRoute may find solutions that are unavailable to the AI router, systematically underestimating abstract difficulty.

**Role in scoring:**
- **Main scoring:** Physical Density dimension (via `conv_rate` for Generator, `drv_iter0` for LXP) and Track Assignment Quality dimension (via `drv_i0_cov`).
- **Tier 1 quick-scan:** DRV@iter0 + shorts used for fast "Likely Easy" filtering (~15s).
- **Not used for:** Abstract Complexity (proven unable to measure it).

**Key evidence:**
- Grid Shrink experiment: `conv_rate` 99% → 27% across grid 400 → 100 (`docs/research-report-difficulty-and-augmentation.md`, Exp 3).
- Walk-bias S/N analysis: inter-wb range 87, avg seed variance 176, S/N 0.50 (`docs/tritonroute_experiment.md`, Walk-bias Sweep section).
- LXP 8-level cross-validation: `drv_iter0` perfectly correlates with Benson levels (ρ=1.0) (`lxp32c_triton_eval/lxp32c_rescore_drv_iter0.json`).
- Tier 1 threshold sweep: `drv_iter0_threshold=100, shorts_threshold=50` achieves L1-L4 filtered=100%, L5-L8 retained=100% (`docs/difficulty_scoring_validation_2026-04-13.md`, Section 1).

---

### B.5 AI Router (`eval.py`)

**Input:** `.txt` abstract case + trained RL model (`.zip`)
**Output:** `avg_per_net` (average routes per net), `is_success`, `.out` solution file

**What it can see:**
The AI router solves the routing problem on the *abstract* grid using a learned policy. `avg_per_net` reflects how many rip-up-and-reroute iterations the model needs per net on average. This directly measures the *abstract complexity* of the case — how tangled, congested, or conflicting the net shapes are from the perspective of the learned routing strategy.

| Metric | Meaning | Scoring role |
|--------|---------|--------------|
| `avg_per_net` (success, value < 11.0) | Average rip-up count. 1.0 = every net routed on first try (trivial). 2.0+ = significant rerouting needed. | **Abstract Complexity primary metric**. Range calibrated per dataset: [1.0, 12.0] for Generator, [1.0, 3.5] for LXP. |
| `avg_per_net = 11.0` | Routing failure sentinel. The model exhausted its iteration budget (11 attempts per net) without achieving DRV=0. | **Should be treated as `routing_failed` / N/A**, not as a valid difficulty value. See validation analysis below. |
| `is_success` | Whether the model achieved DRV=0 within the episode. | Not directly scored. Used to flag `routing_failed` cases. |

**What it cannot see:**
- **Physical routing resource pressure.** The AI router operates on the abstract grid; it does not know about physical track pitches, spacing rules, or GR congestion. A case that is trivial on the abstract grid (avg/net ≈ 1.0) can still be physically hard if the DEF-level density is high.
- **Production DRC compliance.** The AI router's "DRV=0" means abstract-grid conflict-free, not physical-rule-clean.

**Bias / failure modes:**
- **Training distribution bias.** The current model was trained primarily on `training_n1280` cases (500×500, ~1,280 nets, walk_bias=0.5, `max_wl=30`). Cases that deviate from this distribution (different grid size, different `max_wl`, different obstacle config) may show artificially inflated or deflated `avg/net`. Example: all 42 `overnight_v2` cases with `max_wl=70` had `avg/net ≈ 1.0` (model trivially solved them), while `training_n1280` baseline cases with `max_wl=30` had `avg/net` 1.36–2.54 or 11.0 (much harder). The difficulty difference is real, but the *magnitude* of `avg/net` is model-dependent.
- **VIA mode untrained.** The model has limited VIA routing capability, which Prof. Li identified as a bias source. Cases requiring VIA-heavy routing may show inflated `avg/net` that reflects model weakness rather than inherent case difficulty.
- **`avg/net = 11.0` is a timeout sentinel, not a calibrated difficulty value.** When the model fails to converge, `avg/net` is capped at exactly 11.0 regardless of *how* hard the case actually is. Treating 11.0 as a valid difficulty value heavily skews the complexity distribution toward "Very Hard" — 75% of Grid Shrink cases and 30% of training baseline cases hit this sentinel. The scoring system should treat 11.0 as `routing_failed` / N/A.
- **Throughput.** Successful cases take ~3 min. Failure-candidate cases take ~14 min mean (up to 25 min). This makes synchronous AI-router-in-batch prohibitively slow (4.7x–16.8x slowdown vs TritonRoute core).

**Role in scoring:**
- **Main scoring (offline enrichment):** Abstract Complexity dimension. Best run selectively — on Tier 2 cases or sampled anchors — rather than synchronously in batch.
- **Not used for:** Physical Density (cannot see it) or Track Assignment Quality (single-seed metric).

**Key evidence:**
- Walk-bias separation: D3_wb07_s100 `avg/net=3.787` (outlier) vs D1-D4 average ≈ 2.0 — AI router *can* detect abstract complexity differences that TritonRoute cannot (`docs/tritonroute_experiment.md`, D-series).
- Failure sentinel analysis: 6/8 Grid Shrink cases, 1/16 LXP samples, 6/20 training baseline cases hit 11.0 (`docs/difficulty_scoring_validation_2026-04-13.md`, Section 2).
- Throughput: AI success mean 200s, failure mean 855s (`docs/difficulty_scoring_validation_2026-04-13.md`, Section 4).

---

### B.6 Innovus

**Input:** `.def` file + full production LEF + technology mapping
**Output:** Innovus DRV count (with full LEF58/MINSTEP support), routed `.def`, timing/power reports

**What it can see:**
Innovus is a commercial, production-grade detailed router with full design-rule support. Its DRV count is the closest available proxy to manufacturing sign-off quality. When Innovus says DRV=0, the design is (with high confidence) physically realizable.

**What it cannot see:**
- Abstract routing complexity (same blind spot as TritonRoute — operates on physical coordinates only).
- DR-RL model-specific difficulty (it does not know what the AI router finds hard).

**Bias / failure modes:**
- **MinStp (jog) violations** are Innovus-specific: they arise from the router's internal jog insertion strategy and may not reflect case difficulty. In scale=2.40 experiments, residual DRV was 100% MinStp — not a difficulty signal.
- **Cost:** ~15 min/case, requires Cadence license, available only on specific machines.
- **Not practical for batch scoring.** At 15 min/case and license constraints, Innovus cannot be the primary scoring tool for hundreds of cases.

**Role in scoring:**
- **Calibration reference (gold standard).** Sample 10–20% of cases per batch for Innovus verification. If TritonRoute says "Easy" but Innovus says "Hard" → adjust TritonRoute thresholds.
- **Not in main scoring loop.** Too slow and license-constrained for routine use.
- **Validation of LEF simplification.** Innovus confirms that simplified-LEF TritonRoute results preserve relative ordering (R11 case: both converge to DRV=0).

**Key evidence:**
- R11 validation: TritonRoute DRV=0, Innovus DRV=0 — consistent (`docs/research-report-difficulty-and-augmentation.md`, Exp 1 validation).
- Augmentation DRV fix: `--adj-radius 3 --offset-axis x` achieves DRV=0 on Innovus (`docs/research-report-difficulty-and-augmentation.md`, Part 2).

---

### B.7 Scorer / batch_eval / analyze_results (`tools/difficulty/`)

**Input:** Raw metrics (from TritonRoute + AI router) + config YAML (normalization ranges, thresholds, level boundaries)
**Output:** Per-case difficulty profiles (JSON), batch summaries, threshold sweep tables

**What it can see:**
The scoring toolchain is a pure post-processing layer. It takes raw numeric metrics and maps them to normalized scores (0.0–1.0), difficulty levels (Easy / Medium / Hard / Very Hard), and an overall level (max across dimensions). It also implements Tier 1 screening logic (DRV@iter0 < threshold AND shorts ≤ threshold → "Likely Easy").

**What it cannot see:**
- Anything beyond what its input metrics tell it. If TritonRoute cannot distinguish walk_bias, the scorer cannot either.
- Whether the config ranges are well-calibrated (that requires external validation experiments).

**Bias / failure modes:**
- **Garbage-in-garbage-out.** If `avg_per_net = 11.0` is fed as a valid value, the scorer will happily normalize it to score ≈ 0.91 (Very Hard), which is misleading. The scorer does not inherently know that 11.0 is a failure sentinel.
- **Config-dependent.** Normalization ranges (`min_val`, `max_val`) and tier 1 thresholds are manually calibrated. Wrong ranges → wrong levels. Example: `complexity.max_val` was initially 5.0 (placeholder), causing all cases with `avg/net > 5.0` to be clamped to Very Hard. Calibrated to 12.0 after observing `case_300` at 11.0.
- **Dataset-specific configs.** Generator cases use `config.yaml` (density via `conv_rate`), LXP cases use `config_lxp32c.yaml` (density via `drv_iter0`). Using the wrong config for a case type silently produces incorrect scores.

**Role in scoring:**
- **Automation layer.** Transforms raw metrics into human-readable and machine-processable profiles.
- **Threshold calibration.** The threshold sweep facility (`difficulty_scoring_validation_2026-04-13_threshold_sweep.csv`) is the primary tool for LXP Tier 1 threshold selection.
- **Not a measurement tool.** The scorer adds no new information — it only formats and interprets.

---

## C. Metric Dictionary

| Metric ID | Full name | Source tool | Physical / algorithmic meaning | Main scoring? | Failure mode |
|-----------|-----------|-------------|-------------------------------|:---:|--------------|
| `conv_rate` | DRV convergence rate | TritonRoute | Fraction of initial violations the router can fix in 5 iterations. Higher = easier. | **Yes** (Generator density) | Meaningless at GR Usage < 7%. |
| `drv_iter0` | DRV count at iteration 0 | TritonRoute | Number of violations after first routing pass. Higher = harder. | **Yes** (LXP density) | Absolute value depends on LEF simplification; use for relative comparison only. |
| `shorts` | Short-circuit violations at iter0 | TritonRoute | Real routing resource conflicts (overlapping wires). 0 = no conflict. | **Secondary** (informational) | Can be 0 even on moderately hard cases if spacing violations dominate. |
| `drv_i0_cov` | DRV@iter0 coefficient of variation | TritonRoute × N | Sensitivity of routing difficulty to track seed / augmentation variant. High = physically tricky region. | **Yes** (Track quality) | Requires ≥ 3 variants to be statistically meaningful. |
| `avg_per_net` | Average routes per net | AI router | Average rip-up count per net. 1.0 = trivial. Higher = harder for the learned policy. | **Yes** (Complexity, offline) | Model-dependent; 11.0 is a timeout sentinel, not a calibrated value. |
| `routing_failed` (11.0) | AI router timeout sentinel | AI router | Model exhausted iteration budget without achieving abstract DRV=0. | **No** → treat as N/A | Not a difficulty *value*; it means "the model gave up." Cases at 11.0 can range from moderately hard to impossible. |
| `GR Usage%` | Global routing utilization | TritonRoute | Fraction of GR capacity consumed. | **No** (applicability gate) | Used to decide whether density scoring is applicable, not as a scoring input itself. |
| `wire_length` | Total routed wire length | TritonRoute | Total metal used. Weakly correlated with difficulty (~5% cross-wb variation). | **No** (diagnostic only) | Dominated by net count and grid size, not difficulty. |
| `Innovus DRV` | Innovus DRV count | Innovus | Production-grade DRC violation count with full rule support. | **No** (calibration ref) | Too slow for batch scoring; used for sampling validation. |

---

## D. Summary: What Goes Where

### Main scoring inputs (feed directly into `compute_profile`)

| Metric | Dimension | Config key | Source | When applicable |
|--------|-----------|------------|--------|----------------|
| `conv_rate` | Physical Density | `config.yaml → density.metric` | TritonRoute Tier 2 | Generator cases where GR Usage ≥ 7% |
| `drv_iter0` | Physical Density | `config_lxp32c.yaml → density.metric` | TritonRoute Tier 2 | LXP cases (GR Usage 10–30%) |
| `avg_per_net` (< 11.0) | Abstract Complexity | `complexity.metric` | AI router (offline) | All cases where routing succeeded |
| `drv_i0_cov` | Track Assignment Quality | `track.metric` | TritonRoute × N variants | Cases with ≥ 3 seed/variant samples |

### Validation / calibration / diagnostics (do not feed into profile score)

| Metric | Purpose | When to use |
|--------|---------|-------------|
| Innovus DRV | Gold-standard DRC reference | Sample 10–20% per batch to validate TritonRoute thresholds |
| `GR Usage%` | Applicability gate for density dimension | Check before scoring; if < 7%, set density = N/A |
| `shorts` | Hard-case confirmation signal | Annotate in profile as secondary metric; shorts > 0 strongly suggests Hard+ |
| `wire_length`, `vias` | Diagnostic metadata | Attach to profile for traceability; not scored |
| Generator params (grid, wb, seed) | Case metadata | Attach to profile for experiment tracking; not scored |

### Not recommended as difficulty values

| Item | Reason |
|------|--------|
| `avg_per_net = 11.0` | Timeout sentinel, not a calibrated difficulty value. Treat as `routing_failed` / N/A. Evidence: 75% of Grid Shrink cases hit this value; treating it as valid inflates complexity distribution to 75% "Very Hard" (`docs/difficulty_scoring_validation_2026-04-13.md`, Section 2). |
| Generator parameters as direct difficulty inputs | Generator params are *potential* for difficulty, not difficulty itself. Same params + different seed → `avg/net` range 1.36–2.54 (`results/overnight_v2/overnight_v2_summary.log`). |
| `conv_rate` at fixed grid/net-count with varying walk_bias | S/N < 0.5 for walk_bias differentiation at any density level. Physical density metrics cannot separate abstract complexity variations (`docs/tritonroute_experiment.md`, Walk-bias Sweep). |
| `conv_rate` or `drv_iter0` at GR Usage < 7% | Metrics are effectively flat; no discriminating power. Example: Generator 500×500 `conv_rate` range 0.990–1.000 (`docs/difficulty_scoring_validation_2026-04-13.md`, Section 3). |
| Raw TritonRoute DRV counts as absolute values | Depend on LEF simplification level. Valid for *relative* comparison within one LEF configuration, not across configurations or vs Innovus. |

---

## E. Tool Interaction Diagram

```
Generator (.txt)
  │
  ├──→ serializer / DEF converter ──→ .def
  │                                      │
  │                              ┌───────┴──────────┐
  │                              │                   │
  │                        TritonRoute           Innovus
  │                        (Tier 1 / 2)       (calibration)
  │                              │                   │
  │                     DRV, conv_rate,         Innovus DRV
  │                     shorts, GR Usage     (gold standard)
  │                              │
  │                              ▼
  │                     scorer / batch_eval
  │                     ┌──────────────────┐
  │                     │ Physical Density │
  │                     │ Track Quality    │
  │                     └──────────────────┘
  │
  └──→ AI router (eval.py) ──→ avg_per_net
                                    │
                                    ▼
                            scorer / batch_eval
                            ┌──────────────────┐
                            │ Abstract          │
                            │ Complexity        │
                            └──────────────────┘
                                    │
                                    ▼
                            Difficulty Profile
                            (overall_level = max)

LXP augment pipeline (.txt tiles)
  │
  └──→ [same flow as above, with config_lxp32c.yaml]
```
