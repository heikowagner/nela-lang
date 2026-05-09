# Running Wolf Game — NELA-S v0.11 (Separated Wolf Demo)

## 🏗️ Architecture: LLM-Friendly Organization

**examples/wolf/wolf_game.nela** is structured as a single file with algorithmic organization for fast LLM navigation:

```
SECTION_TRIG        [51-65]      — Trigonometry & angle normalization
SECTION_MAP         [67-80]      — Terrain queries
SECTION_STATE       [82-90]      — Player state accessors
SECTION_RAYCASTING  [92-165]     — Core 3D rendering engine
SECTION_TEXTURES    [167-240]    — Texture assets & sampling
SECTION_FRAME       [242-300]    — Frame buffer assembly
SECTION_MOVEMENT    [302-355]    — Player movement & collision
SECTION_GAME        [357-380]    — Input & action mapping
SECTION_DOOR        [382-395]    — Door mechanics
SECTION_ENEMIES     [397-470]    — Enemy AI, LOS, visibility
SECTION_MINIMAP     [472-510]    — 8×8 minimap generation
SECTION_MAIN        [512-541]    — Game loop & I/O threading
```

**Design Goals:**
- Single file (no module imports) avoids NELA parser complications
- Commented index header enables fast needle-in-haystack code retrieval
- Marked sections auto-validate before each build (prevents header staleness)
- All game logic in NELA-S; Python is framebuffer + input only

---

## Quick Start (One Command)

```bash
cd /Users/heikowagner/llm_coder

# 🎮 RECOMMENDED: Python runtime — fully interactive
python3 examples/wolf/src/wolf_player.py

# Verify LLM file structure is valid before playing:
make check-header
make
python3 << 'COMPILE'
import sys; sys.path.insert(0, 'src')
from nela_parser import parse_file
from nela_compiler import compile_program
prog = parse_file('examples/wolf/wolf_game.nela')
bytecode = compile_program(prog, "game_loop", [1.5, 1.5, 90], [1,1,1,1,1,1,1,1,1,0,0,0,0,0,0,1,1,0,1,1,0,1,0,1,1,0,1,0,0,0,0,1,1,0,0,0,1,0,0,1,1,0,1,0,0,1,0,1,1,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1], 8)
open('wolf_game.nelac', 'wb').write(bytecode)
COMPILE
echo 'q' | ./nelac wolf_game.nelac --game  # Test-only; press 'q' to quit
```

## 🧪 LLM Header Validation (ENFORCED for Code Quality)

The file header must always stay synchronized with actual code sections.

**Check header validity:**
```bash
make check-header
```

**Auto-sync if out of date:**
```bash
make fix-header    # Regenerates header from actual code
```

**When to validate:**
- Before any git commit (run `make check-header`)
- After major refactors (use `make fix-header`)
- After LLM-assisted code generation (auto-check before merge)

**What the validator does:**
- Scans for `@SECTION_*` markers in wolf_game.nela
- Extracts all function names and their line ranges
- Cross-checks against header index (first 50 lines)
- Reports any line number mismatches or missing sections
- Re-generates header if `--regenerate` flag is set

**Enforced Invariants** (LLM code must follow these):
- Functions declared before use (no forward references)
- All game logic in NELA-S (no Python sprite rendering)
- Texture sampling contained in `frame_cell` function
- Enemy LOS uses `is_wall_fat` (5-point conservative checks)
- Player state is always `[px, py, angle]` (2 floats, 1 int)
- Map is flat 1D list indexed as `x + y*w`

---

## ✅ Python Runtime (RECOMMENDED - Fully Interactive)

```bash
python3 examples/wolf/src/wolf_player.py
```

**This is the full interactive Wolfenstein experience:**
- Real-time raycasting rendering
- Smooth I/O with keyboard input
- Complete game loop running in NELA-S interpreter
- No compilation needed; plays immediately

**Controls**: **W/A/S/D** = move/turn, **E** = door, **Q** = quit

---

## 🖼️ Optional: Build PNG Textures (Internet → NELA-S)

This downloads CC0 texture packs from ambientCG, converts them to compact PNG previews,
and generates NELA-S texture tables.

```bash
/Users/heikowagner/llm_coder/.venv/bin/python examples/wolf/tools/build_wolf_textures.py
```

Generated assets:
- `examples/wolf/assets/textures/*.png` (16x16 previews)
- `examples/wolf/wolf_textures_generated.nela` (generated texture constants)

Important architecture note:
- Texture sampling and enemy-in-render logic run in **NELA-S** (`examples/wolf/wolf_game.nela`)
- Python (`examples/wolf/src/wolf_player.py`) only performs framebuffer display + input

---

### Build C Runtime (Optional)
```bash
make
# Output: ./nelac (52K C runtime)
```

### Compile NELA to Bytecode
```bash
python3 << 'COMPILE'
import sys; sys.path.insert(0, 'src')
from nela_parser import parse_file
from nela_compiler import compile_program

prog = parse_file('examples/wolf/wolf_game.nela')
bytecode = compile_program(
    prog, "game_loop",
    [1.5, 1.5, 90],  # state
    [1,1,1,1,1,1,1,1,1,0,0,0,0,0,0,1,1,0,1,1,0,1,0,1,
     1,0,1,0,0,0,0,1,1,0,0,0,1,0,0,1,1,0,1,0,0,1,0,1,
     1,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1], 8  # map, width
)
open('wolf_game.nelac', 'wb').write(bytecode)
print(f"✅ Compiled: {len(bytecode)} bytes")
COMPILE
```

### Test Bytecode Execution
```bash
echo 'q' | ./nelac wolf_game.nelac --game
```

---

## What's Happening?

**Python Path** (RECOMMENDED):
```
Parse NELA-S source
    ↓ [src/nela_parser.py]
Typed expression DAG
    ↓ [src/nela_runtime.py interpreter + IOToken callbacks]
Direct evaluation with frame rendering + key I/O
    ↓ [game_loop recurses in Python memory]
Interactive gameplay
```

**C Path** (For reference / testing):
```
Parse NELA-S source
    ↓ [src/nela_parser.py]
Typed expression DAG
    ↓ [src/nela_compiler.py]
Compiled interaction net bytecode
    ↓  [nelac C runtime]
Bytecode execution (I/O callbacks not yet fully integrated)
```

---

## Verify Everything Works

```bash
# Full test suite (Python interpreter)
make test

# Expected: "Overall: ALL TESTS PASSED"
```

---
