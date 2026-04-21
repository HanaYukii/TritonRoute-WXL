# TritonRoute-WXL Architecture Overview

## Top-Level Flow

```
main() → FlexRoute::main()
  ├─ init()         Read LEF/DEF, pin access extraction
  ├─ gr()           Global routing (if no guide provided)
  ├─ prep()         Routing pattern preparation
  ├─ ta()           Track assignment
  ├─ dr()           Detailed routing (24 iterations of search-repair)
  └─ endFR()        Write output DEF
```

## Directory Structure

```
src/
├── main.cpp              Entry point, CLI argument parsing
├── FlexRoute.h/.cpp      Top-level orchestrator
├── frDesign.h            Central design data structure
├── global.h/.cpp         Global config (layer bounds, flags)
├── frRegionQuery.h       R-tree spatial indexing
│
├── io/                   I/O: LEF/DEF parsing and writing
│   ├── io.h/.cpp           Parser & Writer classes
│   ├── io_guide.cpp        Routing guide file handling
│   └── defw.cpp            DEF output writer
│
├── gr/                   Global Routing
│   ├── FlexGR.h/.cpp       Main GR class
│   ├── FlexGR_maze.cpp     GR maze routing
│   ├── FlexGRCMap.h/.cpp   Congestion map
│   └── flute/              Steiner tree library
│
├── dr/                   Detailed Routing (most important)
│   ├── FlexDR.h            FlexDR + FlexDRWorker class defs
│   ├── FlexDR.cpp          Main DR loop (searchRepair x24)
│   ├── FlexDR_init.cpp     Worker initialization (239KB)
│   ├── FlexDR_maze.cpp     A* maze routing (173KB)
│   ├── FlexDR_conn.cpp     Connectivity checking
│   ├── FlexGridGraph.h     3D grid graph for pathfinding
│   └── FlexWavefront.h     Priority queue (A* wavefront)
│
├── gc/                   Geometry Constraint (DRC) checking
│   ├── FlexGC.h            GC worker class
│   └── FlexGC_main.cpp     DRC rule checking (162KB)
│
├── pa/                   Pin Access computation
├── ta/                   Track Assignment
├── rp/                   Routing Pattern generation
└── db/                   Database objects
    ├── obj/                frNet, frBlock, frInst, ...
    ├── drObj/              drNet, drConnFig, drVia, ...
    └── tech/              Layer/via/constraint definitions
```

## Detailed Routing Flow (FlexDR)

### Entry: `FlexDR::main()` (FlexDR.cpp:2392)

Runs **24 iterations** of `searchRepair()` with progressively stronger repair:

```
Iter  0-2:  ripupMode=2 (full ripup), mazeEndIter=3, low DRC cost
Iter  3-16: ripupMode=0 (fix only),   mazeEndIter=8, marker-guided
Iter 17-23: ripupMode=0/1,            mazeEndIter=8, 4x DRC cost
```

### searchRepair() Parameters
| Param | Meaning |
|-------|---------|
| `iter` | Iteration number (0-23) |
| `size` | GCell batch size (7x7 region) |
| `offset` | Stagger offset to avoid boundary artifacts |
| `mazeEndIter` | Max A* expansion iterations |
| `workerDRCCost` | Penalty for DRC violations (1x → 4x) |
| `workerMarkerCost` | Cost of existing violation markers |
| `ripupMode` | 0=fix-only, 1=partial ripup, 2=full ripup |

### Rip-Up & Reroute Mechanism

1. Run DRC (GC) → produce **markers** (violation locations)
2. For each marker, identify involved nets
3. `route_2_ripupNet(net)` — remove net's existing route segments
4. Push net into reroute queue with elevated costs
5. Re-run A* maze routing with penalty at marker locations
6. Repeat until violations resolved or iteration limit

Key methods in `FlexDRWorker`:
- `route_2()` — initial routing queue
- `route_queue()` — marker-based rerouting
- `route_2_ripupNet()` — remove existing route
- `route_2_x2()` — reroute with penalties

## A* Maze Routing

### Priority Queue: `FlexWavefront` (FlexWavefront.h)

```cpp
class FlexWavefrontGrid {
  frMIdx xIdx, yIdx, zIdx;   // 3D grid position
  frCost pathCost;            // g: actual cost from source
  frCost cost;                // f: total = g + h
  frCoord dist;               // h: heuristic distance to target

  // Ordering (min-heap):
  // 1. Lower total cost (f)
  // 2. Closer to target (smaller dist)
  // 3. Upper layer preferred
  // 4. DFS-style (larger pathCost for tie)
};
```

### Maze Routing Algorithm (FlexDR_maze.cpp)

```
routeNet_astar():
  1. Mark source/destination cells on grid
  2. Push source cells into wavefront PQ
  3. While PQ not empty:
     a. Pop lowest-cost cell
     b. If at destination → backtrack, done
     c. Explore 6 neighbors (E, W, N, S, Up, Down)
     d. Compute edge cost (base + DRC + congestion + via)
     e. Push unvisited neighbors into PQ
  4. Backtrack via prevDirs[] to extract path
  5. Convert path to routing segments + vias
```

### Cost Components
- **Base cost**: Manhattan distance
- **GRIDCOST**: Congestion penalty
- **DRCCOST**: Design rule violation penalty (increases per iteration)
- **MARKERCOST**: Penalty at prior violation locations
- **VIACOST**: Layer transition cost

## Key Data Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `frDesign` | frDesign.h | Top-level: tech + topBlock + regionQuery |
| `frNet` | db/obj/ | Logical net (pins, connections) |
| `drNet` | db/drObj/ | DR-level net (route segments) |
| `FlexGridGraph` | dr/FlexGridGraph.h | 3D grid: costs, edges, prev-dirs |
| `FlexWavefrontGrid` | dr/FlexWavefront.h | PQ element for A* |
| `frMarker` | db/obj/ | DRC violation marker |
| `FlexDRWorker` | dr/FlexDR.h:259 | Per-region routing worker (parallel) |

## Where PQ Entries Happen

To count per-net PQ entries, instrument these locations:
1. **`FlexWavefront::push()`** in FlexWavefront.h — every PQ insertion
2. **`FlexDRWorker::routeNet_*()`** in FlexDR_maze.cpp — per-net routing start/end
3. **`FlexDRWorker::route_2_ripupNet()`** — track re-entry count per net

Each `push()` to the wavefront PQ = one cell expansion in A*. More pushes per net = harder net to route.
