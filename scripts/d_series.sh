#!/bin/bash
set -uo pipefail

# =============================================
# D-series: TritonRoute Difficulty Benchmark
# walk_bias sweep (0.5/0.6/0.7/0.75) × 3 seeds
# Grid=500×500, n=1280, max_wl=30, scale=2.40
# =============================================

DRRL=~/Dr-RL
GEN="$DRRL/tools/generator/build/generator"
GENDIR="$DRRL/tools/generator"
CASEDIR="$GENDIR/cases/d_series"
RESULTDIR="$DRRL/results/d_series"
DEFDIR="$RESULTDIR/def"
DEF_WRITER="$DRRL/tools/full_layout/drrl_to_def.py"
MODEL="$DRRL/releases/v1.0.0/rl_model_27200000_steps_level_15.zip"
LEF_INNOVUS=~/TN16-tutorial/Innovus_Usage/PRTF_Innovus_N16_10M_2Xa1Xd4Xe2Z_UTRDL_9T_PODE.17_1a.tlef
LEF_TRITON=/tmp/n16_simplified.tlef
OPENROAD=~/OpenROAD/build/bin/openroad
SCALE=2.40
EVAL_DB="$RESULTDIR/eval.db"
LOG="$RESULTDIR/d_series.log"
CSV="$RESULTDIR/d_series_triton.csv"

export LD_LIBRARY_PATH=~/local_clean/lib:~/local_clean/lib64:${LD_LIBRARY_PATH:-}
source "$DRRL/.venv/bin/activate"
mkdir -p "$CASEDIR" "$RESULTDIR" "$DEFDIR"

echo "=========================================" | tee "$LOG"
echo "D-series TritonRoute Benchmark — $(date)" | tee -a "$LOG"
echo "=========================================" | tee -a "$LOG"

for f in "$GEN" "$MODEL" "$LEF_INNOVUS" "$LEF_TRITON" "$DEF_WRITER" "$OPENROAD"; do
  [ ! -f "$f" ] && echo "MISSING: $f" | tee -a "$LOG" && exit 1
done

configs=(
  "D1_wb05"
  "D2_wb06"
  "D3_wb07"
  "D4_wb075"
)

declare -A wb_map=( [D1_wb05]=0.5 [D2_wb06]=0.6 [D3_wb07]=0.7 [D4_wb075]=0.75 )
seeds=(100 110 120)

# ===========================================
# STEP 1: Generate cases
# ===========================================
echo "" | tee -a "$LOG"
echo "=== STEP 1: Generate ===" | tee -a "$LOG"
for cfg in "${configs[@]}"; do
  wb=${wb_map[$cfg]}
  for s in "${seeds[@]}"; do
    name="${cfg}_s${s}"
    casedir="$CASEDIR/${name}"
    mkdir -p "$casedir"
    outfile="$casedir/${name}.txt"

    if [ -f "$outfile" ]; then
      echo "SKIP gen (exists): $name" | tee -a "$LOG"
      continue
    fi

    tmpfile="/tmp/${name}.cfg"
    cat > "$tmpfile" << CFGEOF
width=500
height=500
layers=2
pitch=100
obs_num=16
min_obs_size=3
max_obs_size=20
net_num=1280
min_wl=5
max_wl=30
momentum=0.85
max_pin_num=5
pin_dist=50,4,33,4
max_retry_per_net=50
difficulty=0.3
congestion_cap=3
walk_bias=${wb}
seed=${s}
output=${outfile}
CFGEOF

    echo -n "Gen: $name ... " | tee -a "$LOG"
    "$GEN" "$tmpfile" 2>&1 | tail -1 | tee -a "$LOG"
    rm -f "$tmpfile"

    if [ -f "$outfile" ]; then
      n_act=$(grep 'Net_num' "$outfile" | awk '{print $2}' || echo "?")
      echo "  n_actual=$n_act" | tee -a "$LOG"
    else
      echo "  FAILED" | tee -a "$LOG"
    fi
  done
done

# ===========================================
# STEP 2: AI Router Eval
# ===========================================
echo "" | tee -a "$LOG"
echo "=== STEP 2: Eval (AI Router) ===" | tee -a "$LOG"
for cfg in "${configs[@]}"; do
  for s in "${seeds[@]}"; do
    name="${cfg}_s${s}"
    casedir="$CASEDIR/${name}"
    outdir="$RESULTDIR/${name}"
    mkdir -p "$outdir"

    if ls "$outdir"/*.out 1>/dev/null 2>&1; then
      echo "SKIP eval (exists): $name" | tee -a "$LOG"
      continue
    fi

    echo -n "Eval: $name ... " | tee -a "$LOG"
    cd "$DRRL"
    timeout 900 python eval.py \
      --dir "$casedir" \
      --out_dir "$outdir" \
      --name "$name" \
      --load "$MODEL" \
      --device cuda \
      --dump_db "$EVAL_DB" \
      --db_table d_series \
      --skip_exist \
      2>&1 | grep -E "case:|success:|Avg\.|runtime:" | tee -a "$LOG" || true
    echo "  done ($(date '+%H:%M'))" | tee -a "$LOG"
  done
done

# ===========================================
# STEP 3: DEF conversion
# ===========================================
echo "" | tee -a "$LOG"
echo "=== STEP 3: DEF (scale=$SCALE, M4/M5) ===" | tee -a "$LOG"
for cfg in "${configs[@]}"; do
  for s in "${seeds[@]}"; do
    name="${cfg}_s${s}"
    outdir="$RESULTDIR/${name}"
    deffile="$DEFDIR/${name}.def"

    [ -f "$deffile" ] && echo "SKIP DEF (exists): $name" | tee -a "$LOG" && continue

    outfile=""
    for ff in "$outdir"/*.out; do [ -f "$ff" ] && outfile="$ff" && break; done
    [ -z "$outfile" ] && echo "SKIP DEF (no .out): $name" | tee -a "$LOG" && continue

    echo -n "DEF: $name ... " | tee -a "$LOG"
    cd "$DRRL"
    python "$DEF_WRITER" \
      -i "$outfile" -o "$deffile" \
      --layermap M4 M5 --dbu_microns 1000 \
      --x_scale $SCALE --y_scale $SCALE \
      --pin_sizes 80,80 80,80 2>/dev/null && echo "ok" | tee -a "$LOG" || echo "FAIL" | tee -a "$LOG"
  done
done

# ===========================================
# STEP 4: Fix DEF tracks (80nm pitch, all layers)
# ===========================================
echo "" | tee -a "$LOG"
echo "=== STEP 4: Fix tracks ===" | tee -a "$LOG"

fix_tracks() {
  local src="$1" dst="$2"
  python3 - "$src" "$dst" << 'PYEOF'
import sys, re

src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    content = f.read()

content = re.sub(r'TRACKS [^\n]*\n', '', content)

m = re.search(r'DIEAREA\s*\(\s*-?\d+\s+-?\d+\s*\)\s*\(\s*(\d+)\s+(\d+)\s*\)', content)
if not m:
    print(f"ERROR: no DIEAREA in {src}")
    sys.exit(1)
x_max, y_max = int(m.group(1)), int(m.group(2))

layers = [
    ("M1",  "V",  90, 64),
    ("M2",  "H",  90, 64),
    ("M3",  "V",  70, 70),
    ("M4",  "H",  80, 80),
    ("M5",  "V",  80, 80),
    ("M6",  "H",  80, 80),
    ("M7",  "V",  80, 80),
    ("M8",  "H",  80, 80),
    ("M9",  "V", 720, 720),
    ("M10", "H", 720, 720),
]

tracks = "\n"
for layer, pref, pp, npp in layers:
    if pref == "V":
        nx = x_max // pp
        ny = x_max // npp
        tracks += f"TRACKS X 0 DO {nx} STEP {pp} LAYER {layer} ;\n"
        tracks += f"TRACKS Y 0 DO {ny} STEP {npp} LAYER {layer} ;\n"
    else:
        nx = x_max // npp
        ny = x_max // pp
        tracks += f"TRACKS X 0 DO {nx} STEP {npp} LAYER {layer} ;\n"
        tracks += f"TRACKS Y 0 DO {ny} STEP {pp} LAYER {layer} ;\n"
tracks += "\n"

content = re.sub(
    r'(DIEAREA[^\n]*;\s*\n)',
    r'\1' + tracks,
    content
)

with open(dst, 'w') as f:
    f.write(content)
PYEOF
}

for cfg in "${configs[@]}"; do
  for s in "${seeds[@]}"; do
    name="${cfg}_s${s}"
    deffile="$DEFDIR/${name}.def"
    triton_def="$DEFDIR/${name}_triton.def"

    [ ! -f "$deffile" ] && continue
    [ -f "$triton_def" ] && echo "SKIP track fix (exists): $name" | tee -a "$LOG" && continue

    echo -n "Fix tracks: $name ... " | tee -a "$LOG"
    fix_tracks "$deffile" "$triton_def" && echo "ok" | tee -a "$LOG" || echo "FAIL" | tee -a "$LOG"
  done
done

# ===========================================
# STEP 5: TritonRoute Evaluation
# ===========================================
echo "" | tee -a "$LOG"
echo "=== STEP 5: TritonRoute ===" | tee -a "$LOG"

cat > /tmp/triton_benchmark.tcl << 'TCLEOF'
read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)
set_routing_layers -signal M4-M5
global_route -congestion_report_file $::env(CONGESTION_RPT) -verbose
detailed_route -droute_end_iter 3 -verbose 1
puts "RESULT_DRV=[detailed_route_num_drvs]"
exit
TCLEOF

export LEF_FILE="$LEF_TRITON"
echo "case,walk_bias,seed,gr_usage_pct,gr_overflow,drv_iter0,drv_iter3,wl_um,vias" > "$CSV"

for cfg in "${configs[@]}"; do
  wb=${wb_map[$cfg]}
  for s in "${seeds[@]}"; do
    name="${cfg}_s${s}"
    triton_def="$DEFDIR/${name}_triton.def"

    [ ! -f "$triton_def" ] && echo "SKIP TR (no def): $name" | tee -a "$LOG" && continue

    export DEF_FILE="$triton_def"
    export CONGESTION_RPT="/tmp/congestion_${name}.rpt"

    echo "=== TR: $name (wb=$wb, s=$s) ===" | tee -a "$LOG"
    TRLOG="/tmp/triton_${name}.log"
    "$OPENROAD" -no_init /tmp/triton_benchmark.tcl 2>&1 | tee "$TRLOG" | \
      grep -E "Usage|Overflow|violations|RESULT|wire length|vias" | tee -a "$LOG"

    # Extract metrics
    GR_USAGE=$(grep "^Total" "$TRLOG" | grep -oP '[\d.]+(?=%)' | head -1 || echo "")
    GR_OVERFLOW=$(grep "^Total" "$TRLOG" | awk '{print $NF}' | head -1 || echo "")
    # First "Number of violations" = DRV@iter0
    DRV_ITER0=$(grep -oP 'Number of violations = \K\d+' "$TRLOG" | head -1 || echo "")
    # RESULT_DRV = DRV@iter3
    DRV_ITER3=$(grep -oP 'RESULT_DRV=\K\d+' "$TRLOG" || echo "")
    WL=$(grep "^Total wire length =" "$TRLOG" | tail -1 | grep -oP '[\d.]+(?= um)' || echo "")
    VIAS=$(grep "^Total number of vias" "$TRLOG" | tail -1 | grep -oP '\d+(?=\.)' || echo "")

    echo "${name},${wb},${s},${GR_USAGE},${GR_OVERFLOW},${DRV_ITER0},${DRV_ITER3},${WL},${VIAS}" >> "$CSV"
    echo "---" | tee -a "$LOG"
  done
done

echo "" | tee -a "$LOG"
echo "=========================================" | tee -a "$LOG"
echo "=== D-series COMPLETE — $(date) ===" | tee -a "$LOG"
echo "=========================================" | tee -a "$LOG"

echo ""
echo "=== TritonRoute Benchmark Results ==="
column -t -s, "$CSV"
echo ""
echo "CSV saved: $CSV"
