# Running Wolf Game — NELA-S v0.9

## Quick Start (One Command)

```bash
cd /Users/heikowagner/llm_coder

# 🎮 RECOMMENDED: Python runtime — fully interactive
python3 src/wolf_player.py

# C runtime — compiles NELA bytecode (non-interactive; for testing)
make
python3 << 'COMPILE'
import sys; sys.path.insert(0, 'src')
from nela_parser import parse_file
from nela_compiler import compile_program
prog = parse_file('examples/wolf_game.nela')
bytecode = compile_program(prog, "game_loop", [1.5, 1.5, 90], [1,1,1,1,1,1,1,1,1,0,0,0,0,0,0,1,1,0,1,1,0,1,0,1,1,0,1,0,0,0,0,1,1,0,0,0,1,0,0,1,1,0,1,0,0,1,0,1,1,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1], 8)
open('wolf_game.nelac', 'wb').write(bytecode)
COMPILE
echo 'q' | ./nelac wolf_game.nelac --game  # Test-only; press 'q' to quit
```

## ✅ Python Runtime (RECOMMENDED - Fully Interactive)

```bash
python3 src/wolf_player.py
```

**This is the full interactive Wolfenstein experience:**
- Real-time raycasting rendering
- Smooth I/O with keyboard input
- Complete game loop running in NELA-S interpreter
- No compilation needed; plays immediately

**Controls**: **W/A/S/D** = move/turn, **E** = door, **Q** = quit

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

prog = parse_file('examples/wolf_game.nela')
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
    ↓ [nela_parser.py]
Typed expression DAG
    ↓ [nela_runtime.py interpreter + IOToken callbacks]
Direct evaluation with frame rendering + key I/O
    ↓ [game_loop recurses in Python memory]
Interactive gameplay
```

**C Path** (For reference / testing):
```
Parse NELA-S source
    ↓ [nela_parser.py]
Typed expression DAG
    ↓ [nela_compiler.py]
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
