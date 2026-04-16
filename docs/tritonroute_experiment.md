# 用 TritonRoute 衡量 DR-RL Test Case 路由難度

## 摘要

在 eda17 上成功建立 OpenROAD/TritonRoute pipeline，可對 DR-RL 產生的 DEF
進行 global routing 和 detailed routing，提取多層次的**路由難度指標**。

已驗證：對 R11 case（500×500, 1280 nets），TritonRoute 最終達到 DRV=0，
與 Innovus 結果一致。

---

## 可提取的難度指標

以 `exp_R11_500_n1280_wb07_wl30_s100.def` 為例：

### Level 1: Global Route Congestion（< 1 秒）

只跑 `global_route`，不跑 detailed routing。最快。

```
Layer    Resource   Demand   Usage(%)   Overflow
M4       148,196    2,005     1.35%       0
M5       148,095    1,551     1.05%       0
Total    296,291    3,556     1.20%       0
```

| 指標 | 值 | 說明 |
|---|---|---|
| **GR Overflow** | 0 | routing 需求是否超過資源。> 0 表示 congestion |
| **GR Usage** | 1.20% | routing 資源使用率，越高越難 |
| **GR Wirelength** | 15,184 µm | 全域繞線長度 |
| GR Runtime | < 1s | |

### Level 2: Initial Detailed Route DRV（~15 秒）

跑 `detailed_route -droute_end_iter 1`，取第 0 次 iteration 的 DRV。

| 指標 | 值 | 說明 |
|---|---|---|
| **DRV@iter0** | 1,800 | 初始繞線的 violation 數，直接反映難度 |
| Wire length | 4,618 µm | DR wire length |
| Vias | 2,119 | M4-M5 via 數量 |
| Runtime | ~15s | |

### Level 3: Converged DRV（~1 分鐘）

跑 `detailed_route -droute_end_iter 5`，看修復後剩多少 violations。

| 指標 | 值 | 說明 |
|---|---|---|
| **DRV@iter3** | 87 | 3 次迭代修復後的殘留 violations |
| **DRV@iter5** | 18 | 5 次迭代後 |
| **Short count** | 1 | routing conflict（最直接的難度指標） |
| Runtime | ~1 min | |

### 收斂曲線（完整參考）

```
Iter   DRV      Time    Phase
0th    1,800    13s     Initial routing
1st      682    27s     Optimization
2nd      617    36s     Optimization
3rd       87    51s     Optimization
4th       30    53s     Guides tiles
5th       18    54s     Guides tiles
...       ↓
37th       0    32min   DRV clean
```

---

## 建議的難度衡量策略

### 快速掃描（大量 cases）

用 **GR Overflow + GR Usage**，每個 case < 1 秒：
- Overflow > 0 → 明確的高難度 case
- Usage > 50% → 可能有 congestion
- Usage < 10% → 輕鬆 case

### 精確評估（重點 cases）

用 **DRV@iter0**（~15 秒）或 **DRV@iter3**（~1 分鐘）：
- DRV@iter0 = 0 → 極簡單
- DRV@iter0 < 1000 → easy
- DRV@iter0 > 5000 → 有挑戰
- DRV@iter3 有 Short > 0 → 真正的 routing conflict

### 指標選擇建議

| 場景 | 建議指標 | 速度 |
|---|---|---|
| 篩選大量 case | GR Overflow / Usage | < 1s |
| 快速排序難度 | DRV@iter0 | ~15s |
| 確認 routing 品質 | DRV@iter3 + Short count | ~1min |
| 完整 baseline | DRV@convergence | ~5-30min |

---

## 執行方法

### 前置準備（一次性）

**1. 簡化 LEF**

N16 LEF 包含 309 個 LEF58 擴展規則，TritonRoute 不支援但仍會 check，
造成 90,000+ 假陽性 violations。必須去除：

```python
# simplify_lef.py — 去除 LEF58/MINSTEP 規則
import re

with open(src_lef) as f:
    content = f.read()

# 去除 PROPERTY LEF58_* "..." ; (multi-line)
content = re.sub(
    r'\s*PROPERTY\s+LEF5[78]_\w+\s+"[^"]*"\s*;',
    '', content, flags=re.DOTALL)

# 去除 MINSTEP
content = re.sub(r'\n\s*MINSTEP[^\n]*;', '', content)

# 去除 PROPERTYDEFINITIONS 中的 type 定義
content = re.sub(
    r'\n\s*(?:LAYER|MACRO|LIBRARY)\s+LEF5[78]_\w+\s+STRING\s*;',
    '', content)
content = re.sub(
    r'\n\s*(?:LAYER|MACRO|LIBRARY)\s+LEF5[78]_\w+\s+STRING\s+"[^"]*"\s*;',
    '', content, flags=re.DOTALL)

with open(dst_lef, 'w') as f:
    f.write(content)
```

已產生的檔案：`/tmp/n16_simplified.tlef`

**2. DEF Track Pitch 修正**

DEF 的 TRACKS 必須使用 LEF 定義的真實 pitch（80nm），不能用
DR-RL 的 240nm abstract grid step。DR-RL pin 座標是 240 的倍數，
也自動是 80 的倍數（240 = 3 × 80），所以 pin 不會 off-grid。

```
# 正確（LEF pitch = 80nm = 80 DBU, UNITS 1000）
TRACKS X 0 DO 1500 STEP 80 LAYER M4 ;
TRACKS Y 0 DO 1500 STEP 80 LAYER M4 ;

# 錯誤（DR-RL grid step）
TRACKS X 0 DO 500 STEP 240 LAYER M4 ;
```

還需補齊所有 10 層的 track 定義（原始 DEF 只有 M4/M5）：

| Layer | Preferred Dir | Pref Pitch | Non-pref Pitch |
|---|---|---|---|
| M1 | V | 90 | 64 |
| M2 | H | 90 | 64 |
| M3 | V | 70 | 70 |
| M4 | H | 80 | 80 |
| M5 | V | 80 | 80 |
| M6 | H | 80 | 80 |
| M7 | V | 80 | 80 |
| M8 | H | 80 | 80 |
| M9 | V | 720 | 720 |
| M10 | H | 720 | 720 |

### 批量執行腳本

**GR only（快速掃描）：**

```tcl
# triton_gr_only.tcl
read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)
set_routing_layers -signal M4-M5
global_route -congestion_report_file $::env(REPORT_FILE) -verbose
exit
```

**DR with fixed iterations（精確評估）：**

```tcl
# triton_eval.tcl
read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)
set_routing_layers -signal M4-M5
global_route -guide_file /tmp/guides.txt
detailed_route -droute_end_iter 3 -verbose 1
puts "DRV=[detailed_route_num_drvs]"
exit
```

**執行：**

```bash
export LD_LIBRARY_PATH=~/local_clean/lib:~/local_clean/lib64:$LD_LIBRARY_PATH
export LEF_FILE=/tmp/n16_simplified.tlef

for def in results/*/def/*.def; do
    export DEF_FILE="$def"
    export REPORT_FILE="/tmp/gr_$(basename $def .def).rpt"
    ~/OpenROAD/build/bin/openroad -no_init triton_gr_only.tcl 2>&1 \
        | grep -E "Usage|Overflow|wirelength" >> results.csv
done
```

---

## 為什麼需要這些步驟（排雷記錄）

### 問題 1：完整 N16 LEF → 90,000 violations

| 原因 | 影響 |
|---|---|
| LEF 有 309 個 LEF58 擴展規則 | TritonRoute 不支援但 DRC engine 會 check |
| 27 個 MINSTEP 規則 | 佔 violations 的 87-89% |
| ISPD contest LEF 有 0 個 LEF58 | TritonRoute 設計目標 |

**解法**：去除所有 LEF58/MINSTEP property → DRV 正常收斂至 0。

### 問題 2：240nm track pitch → routing 異常

| 原因 | 影響 |
|---|---|
| DR-RL grid step = 240nm | Track 只有實際的 1/3 |
| LEF M4/M5 pitch = 80nm | DRC 規則基於 80nm grid |

**解法**：DEF TRACKS 使用 80nm step。Pin 位置不需改（240 是 80 的倍數）。

### 問題 3：TritonRoute-WXL 不相容

| 原因 | 影響 |
|---|---|
| WXL 只處理 instance/cell pins | 我們的 DEF 只有 I/O pins |
| WXL repo 已停更（1 commit） | 無後續支援 |

**解法**：使用 OpenROAD 主線的整合版 TritonRoute（支援 I/O pins）。

---

## 環境與 Build 資訊

| 項目 | 值 |
|---|---|
| Server | eda17, CentOS 8 |
| OpenROAD | `d34d035` (main branch) |
| Binary | `~/OpenROAD/build/bin/openroad` (+GPU -GUI -Python) |
| Compiler | GCC 13.2.1 (`gcc-toolset-13`) |
| Dependencies | `~/local_clean/` (abseil, spdlog, yaml-cpp, boost, CUDD, LEMON) |
| Disabled modules | gpl, mpl, par（OR-Tools 與 CentOS 8 glibc 2.28 不相容）|
| 簡化 LEF | `/tmp/n16_simplified.tlef` |
| 測試 DEF（已修正） | `/tmp/triton_test_v2.def` |

### 驗證結果

| Case | GR Overflow | GR Usage | DRV@iter0 | DRV@iter3 | DRV final |
|---|---|---|---|---|---|
| R11 (500×500, 1280 nets) | 0 | 1.20% | 1,800 | 87 | **0** |
| Innovus 對照 | - | - | - | - | **0** |

---

## D-series Benchmark 結果

### 實驗設計

固定 grid=500×500, n=1280, max_wl=30, scale=2.40。
變動 `walk_bias`（0.5/0.6/0.7/0.75），各 3 seeds。

### 完整數據

| Case | wb | seed | GR Usage% | GR Overflow | DRV@iter0 | DRV@iter3 | WL(µm) | Vias | AI avg/net |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| D1_wb05_s100 | 0.5 | 100 | 1.17 | 0 | 1,779 | 18 | 4,311 | 2,624 | 2.00 |
| D1_wb05_s110 | 0.5 | 110 | 1.16 | 0 | 2,037 | 89 | 4,320 | 2,587 | 2.17 |
| D1_wb05_s120 | 0.5 | 120 | 1.11 | 0 | 1,775 | 40 | 4,146 | 2,490 | 2.00 |
| D2_wb06_s100 | 0.6 | 100 | 1.17 | 0 | 1,861 | 109 | 4,309 | 2,561 | 2.01 |
| D2_wb06_s110 | 0.6 | 110 | 1.16 | 0 | 1,791 | 85 | 4,339 | 2,658 | 2.00 |
| D2_wb06_s120 | 0.6 | 120 | 1.21 | 0 | 1,868 | 40 | 4,460 | 2,720 | 1.00 |
| D3_wb07_s100 | 0.7 | 100 | 1.20 | 0 | 1,800 | 87 | 4,468 | 2,632 | 3.79 |
| D3_wb07_s110 | 0.7 | 110 | 1.23 | 0 | 1,941 | 44 | 4,564 | 2,700 | 2.00 |
| D3_wb07_s120 | 0.7 | 120 | 1.21 | 0 | 2,006 | 20 | 4,463 | 2,715 | 2.00 |
| D4_wb075_s100 | 0.75 | 100 | 1.22 | 0 | 1,742 | 28 | 4,524 | 2,649 | 2.00 |
| D4_wb075_s110 | 0.75 | 110 | 1.20 | 0 | 1,807 | 25 | 4,481 | 2,735 | 2.00 |
| D4_wb075_s120 | 0.75 | 120 | 1.20 | 0 | 1,878 | 84 | 4,479 | 2,698 | 1.83 |

### 各 walk_bias 平均

| wb | GR Usage% | DRV@iter0 | DRV@iter3 | WL(µm) | Vias | AI avg/net |
|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 0.50 | 1.15 | 1,864 | 49 | 4,259 | 2,567 | 2.06 |
| 0.60 | 1.18 | 1,840 | 78 | 4,369 | 2,646 | 1.67 |
| 0.70 | 1.21 | 1,916 | 50 | 4,498 | 2,682 | 2.60 |
| 0.75 | 1.21 | 1,809 | 46 | 4,495 | 2,694 | 1.94 |

### 分析

1. **GR Overflow = 0（所有 cases）**：在 500×500 / 1280 nets / 2 layers 的設定下，
   routing 資源遠大於需求（usage 只有 ~1.2%）。GR Overflow 無法區分這些 cases 的難度。
   需要更高 net density 或更小 grid 才會出現 GR overflow。

2. **GR Usage 微弱正相關**：wb 從 0.5 到 0.75，Usage 從 1.15% → 1.21%。
   趨勢存在但差異太小（< 0.1%），不足以作為 difficulty metric。

3. **DRV@iter0 無顯著區分**：所有 cases 都在 1,742 ~ 2,037 範圍，跨 wb 的
   variance 與 seed 間的 variance 相當。DRV@iter0 受初始 routing 隨機性影響大。

4. **DRV@iter3 也無顯著趨勢**：wb=0.5 平均 49, wb=0.75 平均 46，甚至略低。
   3 次 iteration 後大部分 violations 都被修復了。

5. **Wire length 正相關**：wb 0.5 → 0.75, WL 從 4,259 → 4,495 µm（+5.5%）。
   這是最穩定的 difficulty signal，因為 higher walk_bias = longer nets on average。

6. **AI avg/net 有 outlier 但整體平坦**：D3_wb07_s100 的 3.79 是 outlier，
   其他 cases 大多在 2.0 附近。AI router 在這些 cases 上表現穩定。

### 結論

**在 500×500 / 1280 nets 的設定下，TritonRoute 的各項 metrics 對 walk_bias
的區分力不足。** 可能原因：

- **Routing utilization 太低**（~1.2%）→ 即使 walk_bias 增加 pin density，
  routing 資源仍然充裕
- **Net 數量不夠**（1280 nets 佔 500×500 grid 的 routing capacity 很小）
- **walk_bias 0.5→0.75 的範圍不夠大**

### 後續建議

1. **提高 net density**：嘗試 n=5000+ 或 grid=200×200 讓 utilization > 30%
2. **加大 walk_bias 範圍**：嘗試 wb=0.9 或 wb=0.95
3. **多 seed 平均**：seed 間 variance 大，需 5+ seeds 才能看出 trend
4. **用 Wire Length 作為 proxy**：WL 是最穩定的 difficulty signal

---

## lxp32c Full-Chip Benchmark 結果

### 背景

D-series 發現 generator cases（500×500, 1280 nets）的 routing utilization 僅 1.2%，
TritonRoute 無法區分不同 walk_bias 的難度。lxp32c 是真實 IC layout（ASAP7 M2/M3），
有 ~140K nets / full-chip（500K pins），routing density 遠高於 generator cases。

### 實驗設計

使用 lxp32c full-chip 的 5 個 data augmentation 變體：

| Case | 變換方式 | 說明 |
|---|---|---|
| orig | 無 | 原始 layout |
| mh | 水平鏡射 (mirror-H) | 水平翻轉 |
| mv | 垂直鏡射 (mirror-V) | 垂直翻轉 |
| r180 | 旋轉 180° | 180 度旋轉 |
| orig_s0 | 不同 seed 的 track 分配 | 改變 non-pref track offset |

技術參數：
- LEF: ASAP7 4x (`asap7_tech_4x_181009_basic_rule.lef`)，已簡化（去除 LEF58）
- DEF: `~/Dr-RL/asap7_full_design/def/`，已修正全 9 層 tracks
- Die area: 2,333,376 × 2,160,288 DBU (UNITS 4000)
- Net 數: 139,926 (OpenROAD parsed)
- Routing layers: M2 (H) + M3 (V)

### 完整數據

| Case | GR Usage% | GR Overflow | DRV@iter0 | DRV@iter1 | DRV@iter2 | DRV@iter3 | WL(µm) | GR Time |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| orig | 26.54 | 0 | 7,546 | 906 | 494 | 1 | 1,071,863 | 33s |
| mh (mirror-H) | 26.52 | 0 | 7,910 | 1,026 | 488 | 2 | 1,071,392 | 33s |
| mv (mirror-V) | 26.54 | 0 | 7,507 | 848 | 428 | 0 | 1,071,883 | 34s |
| r180 (rot-180) | 26.52 | 0 | 7,776 | 950 | 438 | 0 | 1,071,489 | 30s |
| orig_s0 (seed0) | 26.68 | 0 | **10,954** | **1,694** | **1,057** | **8** | **1,088,813** | 35s |

### 分析

#### D-series vs lxp32c 比較

| 指標 | D-series (generator) | lxp32c (real layout) |
|---|---|---|
| Net count | 1,280 | 139,926 |
| GR Usage | 1.2% | **26.5%** |
| DRV@iter0 | ~1,800 | **7,500 - 10,900** |
| DRV@iter3 | 0 | 0-8 |
| WL | ~4,500 µm | **~1,072,000 µm** |

lxp32c 的 **GR Usage 26.5%** 遠高於 D-series 的 1.2%，確認了 real layout
有更高的 routing density。

#### Augmentation 間的差異

**orig_s0 是明確的 outlier**：
- DRV@iter0: 10,954（比其他高 **39-46%**）
- DRV@iter1: 1,694（比其他高 66-100%）
- DRV@iter2: 1,057（比其他高 114-147%）
- DRV@iter3: 8（其他 0-2）
- WL: 1,088,813（比其他高 **+1.6%**）

orig_s0 使用不同的 track offset seed，改變了 non-preferred direction 的
track pattern。這證明 **track assignment 對 TritonRoute DRV 有顯著影響**，
即使 net topology 完全相同。

**其他 4 個空間變換（orig, mh, mv, r180）差異小**：
- DRV@iter0 範圍: 7,507 - 7,910（±3%）
- DRV@iter3: 0-2
- WL: 1,071,392 - 1,071,883（±0.05%）
- 空間翻轉/旋轉不改變 routing 難度，符合預期

### 關鍵發現

1. **TritonRoute 在 real layout 上能區分 track pattern 差異**：
   orig_s0 的 DRV@iter0 顯著高於其他 4 個 augmentation，說明
   DRV@iter0 可以反映 track assignment quality，而非只是 noise。

2. **GR Usage 26.5% → DRV 仍可收斂至 0-8**：
   即使在中度 congestion 下，TritonRoute 3 iterations 就能幾乎清零 DRV。
   要讓 DRV 無法收斂（真正的 routing failure），可能需要 usage > 50%。

3. **DRV@iter0 是最敏感的 difficulty metric**：
   - GR Usage 對 augmentation 幾乎無差異（26.52-26.68%）
   - DRV@iter3 差異小（0-8）
   - **DRV@iter0 差異大（7507 vs 10954 = 46% variation）**
   - DRV@iter0 反映的是 initial routing conflict，不受 rip-up-reroute 修復影響

4. **需要 tile-level 比較來驗證 density 差異**：
   目前 5 個 case 都是同一個 chip 的變換。要真正驗證 TritonRoute
   能否區分不同 routing density，需要 132 個 tiles（不同 region，
   不同 net density）。tiles 不在 eda17，需要從其他來源取得。

### 後續建議

1. **取得 lxp32c tiles（132 tiles）**跑 per-tile TritonRoute，
   比較不同 tiles 的 DRV@iter0 vs AI router avg/net 的 correlation
2. **在 generator 上提高 density**：n=5000+, grid=200×200，
   讓 GR usage > 30% 後重跑 D-series
3. **嘗試更多 track seed 變體**：既然 orig_s0 差異顯著，
   可以用更多 seed 來量化 track assignment 對 DRV 的影響

---

## Grid Shrink Experiment 結果

### 背景

D-series 發現 500×500 / 1280 nets 的 GR Usage 僅 1.2%，TritonRoute 無法區分
walk_bias 的難度。本實驗縮小 grid size（同時維持 1280 nets），提高 routing density，
找到 TritonRoute 能有效衡量難度的 grid 配置。同時嘗試了更極端的配置（grid=100、
net 數量加倍至 2560）。

### 實驗設計

固定參數：n=1280, wb=0.7, wl=30, seed=100, scale=2.40, layers=2 (M4/M5)
變動：grid size 500/400/300/200/150/100
額外：grid 150 + n=2560, grid 200 + n=2560

### 完整數據

| Config | Grid | Nets | GR Usage% | GR Overflow | DRV@i0 | DRV@i1 | DRV@i3 | DRV@i5 | WL(µm) | Vias | Final DRV |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| baseline | 500 | 1280 | 1.20 | 0 | 1,801 | 739 | 97 | 66 | 4,511 | 2,119 | 66 |
| | 400 | 1280 | 1.83 | 0 | 1,834 | 774 | 31 | 8 | 4,320 | 2,076 | 8 |
| | 300 | 1280 | 3.23 | 0 | 1,957 | 1,018 | 96 | 11 | 4,395 | 2,089 | 11 |
| | 200 | 1280 | 6.93 | 0 | 2,044 | 1,473 | 481 | 118 | 4,206 | 2,080 | 118 |
| | **150** | 1280 | **12.38** | 0 | **2,493** | **2,148** | **1,344** | **638** | 4,179 | 2,234 | **638** |
| | **100** | 1280 | **25.58** | 0 | **3,588** | **3,453** | **2,936** | **2,601** | 3,959 | 2,372 | **2,601** |
| 2× nets | **150** | 2560 | **23.42** | 0 | **6,397** | **6,053** | **4,775** | **4,172** | 8,234 | 5,024 | **4,172** |
| 2× nets | **200** | 2560 | **13.59** | 0 | **4,790** | **4,498** | **3,024** | **2,050** | 8,340 | 4,475 | **2,050** |

### Per-Layer GR Usage

| Config | Grid | Nets | M4 Usage% | M5 Usage% | M4 Overflow | M5 Overflow |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| | 500 | 1280 | 1.35 | 1.05 | 0 | 0 |
| | 400 | 1280 | 2.02 | 1.64 | 0 | 0 |
| | 300 | 1280 | 3.59 | 2.86 | 0 | 0 |
| | 200 | 1280 | 7.60 | 6.25 | 0 | 0 |
| | 150 | 1280 | 12.69 | 12.07 | 0 | 0 |
| | 100 | 1280 | 24.83 | 26.34 | 0 | 0 |
| 2× | 150 | 2560 | 22.75 | 24.09 | 0 | 0 |
| 2× | 200 | 2560 | 14.25 | 12.92 | 0 | 0 |

### DRV 類型分析（iter0）

| Config | Grid | Nets | M4 Spacing | M5 Spacing | Shorts | Other | Total |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| | 500 | 1280 | 1,162 | 572 | 2 | 65 | 1,801 |
| | 400 | 1280 | 1,211 | 554 | 0 | 69 | 1,834 |
| | 300 | 1280 | 1,232 | 681 | 2 | 42 | 1,957 |
| | 200 | 1280 | 1,321 | 666 | 0 | 57 | 2,044 |
| | 150 | 1280 | 1,593 | 844 | 13 | 43 | 2,493 |
| | 100 | 1280 | 2,203 | 1,242 | 102 | 41 | 3,588 |
| 2× | 150 | 2560 | 3,824 | 2,321 | 92 | 160 | 6,397 |
| 2× | 200 | 2560 | 2,928 | 1,723 | 13 | 126 | 4,790 |

### 分析

1. **GR Usage 隨 grid 縮小呈平方增長**：
   500→100 從 1.2% → 25.6%（×21）。面積比 (500/100)² = 25×，吻合。
   **即使 25% usage 也沒有 GR Overflow** — TritonRoute GR 的 routing capacity
   確實非常大（10 layers 都算進去）。

2. **DRV@iter0 的有效區分門檻在 GR Usage ≈ 7%（grid ≤ 200）**：
   - 500~300 (1-3%): DRV@i0 ≈ 1,800-1,957（flat，+8%）
   - 200 (7%): DRV@i0 = 2,044（+13%，開始拉開）
   - 150 (12%): DRV@i0 = 2,493（+38%）
   - 100 (26%): DRV@i0 = 3,588（+99%，翻倍）

3. **DRV 收斂能力是最強的 difficulty signal**：
   - 500/400/300: 5 iter 後 DRV 收斂到 <70（可修復）
   - **200: 5 iter → 118（修復困難）**
   - **150: 5 iter → 638（嚴重無法收斂）**
   - **100: 5 iter → 2,601（完全無法收斂）**

4. **Shorts 在 grid ≤ 150 開始出現**：
   500~200 基本 0 shorts，150 出現 13 shorts，100 出現 102 shorts。
   Shorts 代表路徑衝突（不只是 spacing 太近），是真正的 routing conflict。

5. **Net 數量加倍（2560）的效果 ≈ grid 縮半**：
   - 150/2560 (23%) vs 100/1280 (26%): 類似 usage，類似 DRV 水準
   - 200/2560 (14%) vs 150/1280 (12%): 也是類似

### 結論與建議

**推薦的 TritonRoute difficulty benchmark 配置**：

| 用途 | Grid | Nets | GR Usage | 特點 |
|---|:-:|:-:|:-:|---|
| 快速掃描 | 200 | 1280 | ~7% | DRV 開始有區分，<2min/case |
| **標準 benchmark** | **150** | **1280** | **~12%** | **DRV 有效區分，無法收斂** |
| 壓力測試 | 100 | 1280 | ~26% | 大量 shorts，完全無法收斂 |
| 高密度 | 150 | 2560 | ~23% | 最接近 real layout density |

**GR Overflow 在這些配置下都不會出現**，因為 TritonRoute 的 GR 考慮全部 10 layers
的 capacity（即使只限 M4-M5 routing，GR 仍用所有 layer 計算資源）。
要觸發 overflow 需要 >50% usage，即 grid 70×70 或 n>5000。

**DRV 收斂曲線（DRV@i0 → DRV@i5 的降幅比例）是最有效的指標**：
- 降幅 > 95%（如 500/400）= easy
- 降幅 50-80% = medium
- 降幅 < 30%（如 100）= hard

---

## Walk-Bias Sweep at Grid 150×150

### 背景

Grid Shrink 確認 150×150 / 1280 nets（GR Usage ~12%）是有效的 benchmark 配置。
本實驗驗證：在這個 density 下，TritonRoute 能否區分不同 walk_bias 的難度差異。
D-series（500×500）時 TritonRoute 完全無法區分 wb 0.5~0.75。

### 實驗設計

固定 grid=150×150, n=1280, wl=30, scale=2.40, layers=2 (M4/M5)
變動 walk_bias（0.5/0.6/0.7/0.75），各 3 seeds（100/110/120）= 12 cases

### 完整數據

| Case | wb | seed | GR Use% | DRV@i0 | DRV@i1 | DRV@i3 | DRV@i5 | Conv% | Shorts | WL(µm) |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| wb050_s100 | 0.50 | 100 | 12.06 | 2,493 | 2,160 | 1,383 | 784 | 68.6 | 14 | 4,103 |
| wb050_s110 | 0.50 | 110 | 12.33 | 2,368 | 2,020 | 1,246 | 729 | 69.2 | 7 | 4,210 |
| wb050_s120 | 0.50 | 120 | 11.99 | 2,314 | 2,080 | 1,365 | 828 | 64.2 | 6 | 4,073 |
| wb060_s100 | 0.60 | 100 | 12.38 | 2,585 | 2,344 | 1,440 | 840 | 67.5 | 13 | 4,210 |
| wb060_s110 | 0.60 | 110 | 12.19 | 2,468 | 2,186 | 1,364 | 690 | 72.0 | 4 | 4,131 |
| wb060_s120 | 0.60 | 120 | 12.43 | 2,384 | 2,048 | 1,481 | 766 | 67.9 | 7 | 4,156 |
| wb070_s100 | 0.70 | 100 | 12.38 | 2,493 | 2,148 | 1,344 | 638 | 74.4 | 13 | 4,179 |
| wb070_s110 | 0.70 | 110 | 12.56 | 2,469 | 2,167 | 1,266 | 669 | 72.9 | 12 | 4,239 |
| wb070_s120 | 0.70 | 120 | 12.88 | 2,471 | 2,152 | 1,524 | 850 | 65.6 | 8 | 4,355 |
| wb075_s100 | 0.75 | 100 | 12.54 | 2,428 | 2,083 | 1,381 | 784 | 67.7 | 13 | 4,226 |
| wb075_s110 | 0.75 | 110 | 12.12 | 2,290 | 2,013 | 1,399 | 600 | 73.8 | 3 | 4,143 |
| wb075_s120 | 0.75 | 120 | 12.56 | 2,589 | 2,287 | 1,448 | 915 | 64.7 | 12 | 4,228 |

### 各 wb 平均

| wb | avg GR Use% | avg DRV@i0 | avg DRV@i5 | avg Conv% | avg Shorts | avg WL(µm) |
|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 0.50 | 12.13 | 2,392 | 780 | 67.4 | 9.0 | 4,129 |
| 0.60 | 12.33 | 2,479 | 765 | 69.1 | 8.0 | 4,166 |
| 0.70 | 12.61 | 2,478 | 719 | 71.0 | 11.0 | 4,258 |
| 0.75 | 12.41 | 2,436 | 766 | 68.5 | 9.3 | 4,199 |

### 統計分析

**Signal-to-Noise Ratio**：

| 指標 | Inter-wb range | Avg intra-wb range (seed var) | S/N ratio |
|---|:-:|:-:|:-:|
| DRV@i0 | 87 (2,392-2,479) | 176 | **0.50** |
| DRV@i5 | 61 (719-780) | 194 | **0.32** |

S/N < 1 代表 **seed 間的 variance > wb 間的差異**，指標不穩定。

### 結論

**TritonRoute 在 150×150 下仍然無法有效區分 walk_bias 0.5~0.75 的難度差異。**

具體問題：
1. **DRV@i0 平均值幾乎 flat**：2,392 ~ 2,479（inter-wb 差異僅 3.6%）
2. **seed variance 遠大於 wb 差異**：S/N ratio 0.50（DRV@i0）、0.32（DRV@i5）
3. **收斂率無趨勢**：wb=0.70 反而收斂最好（71.0%），但差異在 noise range 內
4. **WL 依然有微弱正相關**：4,129 → 4,258（+3.1%），跟 D-series 一致

**根本原因**：walk_bias 0.5→0.75 改變的是 **net 形狀的直線性**，不是 net density
或 pin 位置分佈。在相同 grid/net 數下，routing resource 需求變化太小（GR Usage
12.1→12.6%），TritonRoute 看不出差異。

**Walk-bias 影響的是 abstract routing complexity（AI router rip-up 次數），
不是 physical routing complexity（track 佔用率）。** 這與 D-series 的結論完全一致，
僅僅提高 density 並不能讓 TritonRoute 區分 walk_bias。

### 適用場景

TritonRoute metrics 適合用來：
- 區分 **不同 grid size / net density** 的難度（Grid Shrink 實驗已驗證）
- 區分 **不同 track assignment** 的影響（lxp32c orig_s0 實驗已驗證）
- 驗證 **物理合法性**（替代 Innovus DRC check）

TritonRoute metrics **不適合**用來：
- 區分 **walk_bias 0.5~0.75** 的差異（signal 太弱，noise 太大）
- 衡量 **abstract routing difficulty**（應使用 AI router avg/net）

---

## Difficulty Scoring System（已實作）

### 概述

基於以上 4 輪實驗的結論，已實作 **Two-Tier Difficulty Scoring System**。

Branch: `feat/difficulty-scoring-system`
Module: `tools/difficulty/`
Tests: **23/23 passed**
Smoke test: 真實 Grid 150 log → 正確輸出 profile

### 三個正交維度

| 維度 | 指標 | 來源 | 適用場景 |
|------|------|------|----------|
| **Physical Density** | DRV convergence rate (iter0→iter5) | TritonRoute | 不同 grid/net density |
| **Abstract Complexity** | AI router avg route/net | DR-RL eval | walk_bias, net shape |
| **Track Assignment** | DRV@iter0 CoV across seeds | TritonRoute | track offset 影響 |

### 兩級評估

| Tier | 速度 | 做什麼 | 用途 |
|------|------|--------|------|
| **Tier 1** | ~15s/case | DRV@iter0 only | 快速篩選 obviously easy cases |
| **Tier 2** | ~2min/case | 全 3 維度，5 iter 收斂 | 完整 difficulty profile |

### 使用方式

```bash
cd ~/Dr-RL
git checkout feat/difficulty-scoring-system

# Tier 1 快速篩選
python -m tools.difficulty.batch_eval --def-dir ~/cases/def/ --tier 1

# Tier 2 完整評估
python -m tools.difficulty.batch_eval --def-dir ~/cases/def/ --tier 2 --force-full

# 分析結果
python -m tools.difficulty.analyze_results difficulty_results.json --csv results.csv
```

### Smoke Test 結果

```
Case: grid150_wb07_s100
Overall: Medium
  density:    score=0.342, level=Medium  (conv_rate=0.744, shorts=13)
  complexity: score=0.275, level=Medium  (avg_per_net=2.1)
  track:      score=0.304, level=Medium  (drv_i0_cov=0.14)
```

### Scoring 參數校準（來自實驗數據）

| 維度 | 指標 | Easy 端 | Hard 端 | 來源 |
|------|------|---------|---------|------|
| density | conv_rate | 0.99 (Grid 400) | 0.27 (Grid 100) | Grid Shrink |
| complexity | avg_per_net | 1.0 | 5.0 (placeholder) | 待 Phase A |
| track | drv_i0_cov | 0.0 | 0.46 (orig_s0) | lxp32c |

### 待辦

| 項目 | 狀態 | 依賴 |
|------|------|------|
| lxp32c 132 tiles 上 eda17 | **Blocked** | 需確認 tile 位置 |
| Cross-metric correlation（TritonRoute vs AI avg/net） | 待 tiles | #1 |
| avg_per_net scoring 上限校準 | 待 Phase A data | — |
| Grid 150×150 寫入 Golden Test Policy | Ready | — |
