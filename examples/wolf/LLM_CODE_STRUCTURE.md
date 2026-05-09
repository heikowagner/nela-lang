# LLM-Optimized Code Structure for NELA Wolf Game

This document describes the code organization strategy specifically designed for LLM-assisted development of the Wolf3D raycaster.

## Design Principle

**Single Monolithic File + Algorithmic Index = LLM-Friendly Codebase**

The NELA game lives in one file (`examples/wolf/wolf_game.nela`) with a machine-parseable index header. This avoids:
- Module import complexity (NELA has no import system)
- Token context loss from fragmentation
- LLM confusion about file boundaries

**Trade-off:** Single file gets large (~540 lines), but structured navigation compensates.

---

## File Organization Strategy

### 1. The Index Header (Lines 1-50)

Located at the very top of `examples/wolf/wolf_game.nela`:

```
-- SECTION INDEX FOR LLM NAVIGATION (auto-maintained):
--   SECTION_TRIG        [ 51- 65]      deg_to_rad | norm_angle
--   SECTION_MAP         [ 67- 80]      map_get | is_wall
--   SECTION_STATE       [ 82- 90]      state_px | state_py | state_angle
--   ... (complete index)
```

**Purpose:** OPT (One-Plus-Tactics pattern)
- LLM reads first 50 lines once → learns entire architecture
- Exact line numbers enable direct "jump to section" retrieval
- Function names enable fast "find all uses of X" searches
- No preprocessing needed; works with simple regex

**Format Invariants:**
- Always `--   SECTION_NAME  [ start- end]      func1 | func2 | func3`
- Sections are **sorted by line number** (ascending)
- Line numbers are **inclusive** on both ends
- Function lists use `|` separator with spaces

### 2. Section Markers (Throughout File)

Each section is prefixed with a metadata comment:

```nela
-- ── @SECTION_TRIG [51-65] ────────────────────────────────────────────────────
-- Functions: deg_to_rad, norm_angle
-- Purpose: Angle conversion (degrees ↔ radians); normalize angles to [0,359]
-- Called_by: render_col_data, move_forward, move_back, use_door, game_loop_rec
-- Key_vars: [a, rad]
```

**Metadata Fields:**
- `Functions:` — All public functions in this section
- `Purpose:` — Human-readable 1-line description
- `Called_by:` — Functions/sections that use these (call graph for LLM understanding)
- `Key_vars:` — Important variable names or patterns (helps LLM predict arg names)
- `Details:` — Optional deep-dive for complex sections (raycasting, LOS, etc)

**LLM Usage:**
- When editing section X, LLM reads the metadata to understand dependencies
- When fixing a bug in section Y, LLM follows `Called_by` to find affected code
- Key_vars speeds up variable naming consistency

### 3. Function Declaration Order (Strict)

**Rule: All functions must be declared before use.** No forward references.

This constraint:
- Ensures parsing is single-pass (no symbol table needed)
- Simplifies comprehension for LLMs (no surprise late-binding)
- Prevents circular dependencies

**Validation:** The header validator checks that all `Called_by` functions appear after the section that calls them.

---

## How LLMs Use This Structure

### Use Case 1: Fix a Bug in Enemy LOS

1. **Prompt:** "Enemies are visible through walls. Fix `los_clear`."

2. **LLM Actions:**
   - Reads header → finds `SECTION_ENEMIES [397-470]`
   - Reads `Called_by: visible_enemies, minimap_cell` → understands scope
   - Reads `Details: los_clear uses fat-wall checks...` → understands algorithm
   - Jumps to line 397-470, fixes `los_clear`
   - Runs `make check-header` → validates header still matches

3. **No wasted tokens** on raycasting/frame/minimap sections

### Use Case 2: Add New Feature (e.g., ranged weapons)

1. **Plan phase:**
   - Read header → understand where game state is (`SECTION_STATE`)
   - Read `SECTION_GAME` → understand input handling
   - Read `SECTION_MAIN` → understand render packet

2. **Implementation:**
   - Add bullet state to player state (modify `SECTION_STATE`)
   - Add key 'r' handler in `SECTION_GAME`
   - Add bullet rendering in `SECTION_FRAME` (before minimap)
   - Run `make fix-header` → auto-regenerate index with new functions

3. **Keeps context tight** — only read/edit relevant sections

### Use Case 3: Integrate New Asset Pipeline

**Status quo:**
```bash
# New developer generates textures
python3 examples/wolf/tools/build_wolf_textures.py

# NELA constants are injected into examples/wolf/wolf_game.nela
# But: human developer must manually update the file's @SECTION_TEXTURES marker
# Risk: header goes out of sync → future LLM queries are incorrect
```

**New Workflow:**
```bash
# New developer generates textures
python3 examples/wolf/tools/build_wolf_textures.py

# Auto-check header sync
make check-header

# If out of sync:
make fix-header

# Now header is guaranteed accurate for next LLM iteration
```

---

## Enforcement Mechanism: Header Validator

Located in `tools/validate_nela_header.py`.

### How It Works

1. **Extract sections:**
   - Scan for `@SECTION_*` markers in code
   - For each marker, scan forward to next marker, extracting function `def` statements
   - Build map: `{SECTION_NAME: (line_start, line_end, [functions])}`

2. **Extract header index:**
   - Parse lines 1-50 for `--   SECTION_*` pattern
   - Build map: `{SECTION_NAME: (claimed_start, claimed_end, func_string)}`

3. **Cross-check:**
   - All declared sections exist in code ✓
   - All code sections declared in header ✓
   - Line numbers match ✓
   - Functions list matches ✓

4. **Report errors:**
   - Mismatch → exit code 1, list all errors
   - All OK → exit code 0

### Usage

```bash
# Check header validity (CI/CD gate)
make check-header

# Auto-repair (test-only; use after validated changes)
make fix-header

# Programmatic check (in scripts)
python3 tools/validate_nela_header.py examples/wolf/wolf_game.nela  # exit code 1 if bad
```

---

## Invariants and Constraints (LLM Must Enforce)

When editing `wolf_game.nela`, LLMs must verify:

1. **No forward references:** All functions are defined before first use
   - Validator catches this via call graph in header metadata

2. **All game logic in NELA-S:** No Python sprite/physics code
   - Python (`examples/wolf/src/wolf_player.py`) is framebuffer + input only

3. **Texture sampling is in `frame_cell`:** No external rendering
   - Keep texture reads localized to frame assembly

4. **Enemy LOS uses `is_wall_fat`:** Not raw raycasting
   - Maintains performance (fat wall checks are faster than full ray march)

5. **Player state is always `[px, py, angle]`:**
   - 2 floats (position), 1 int (angle normalized to [0,359])
   - State is threaded as immutable value through `game_loop_rec`

6. **Map is flat 1D list:**
   - Index: `x + y*w` (not 2D array)
   - Faster access in functional code

7. **Coordinate system is consistent:**
   - Float position units (1.0 = 1 grid cell)
   - Integer degrees [0, 359]:
     - 0 = south (+y), 90 = east (+x), 180 = north (-y), 270 = west (-x)

---

## MCP/VS Code Integration (Future)

When using a Model Context Protocol (MCP) server or VS Code Copilot integration:

```yaml
# .copilot-instructions.md
When editing examples/wolf/wolf_game.nela:
1. Always start by reading lines 1-50 to understand section organization
2. Use section line numbers in your responses (e.g., "Edit SECTION_ENEMIES [397-470]")
3. Never suggest cross-file imports; NELA has no module system
4. After any refactor, verify: `make check-header` passes
5. If header is out of sync, run: `make fix-header`
```

---

## Workflow Example: Add Minimap Rotation

**Scenario:** Current minimap is axis-aligned. User wants it to rotate with player.

**LLM Workflow:**

```bash
# Step 1: Check current state
make check-header      # Passes ✓

# Step 2: Identify affected sections
# Read header → SECTION_MINIMAP is responsible for minimap generation
# Read metadata → called by render_packet in SECTION_MAIN

# Step 3: Edit the code
# Edit examples/wolf/wolf_game.nela
# - Modify minimap_cell to apply rotation relative to player angle
# - Update all minimap_row and build_minimap functions as needed

# Step 4: Validate
make check-header     # If line numbers changed, lists errors
make fix-header       # If out of sync, regenerate
python3 src/nela_runtime.py   # Run tests

# Step 5: Commit
git add examples/wolf/wolf_game.nela tools/validate_nela_header.py
git commit -m "feat: rotate minimap with player angle; updated header index"
```

---

## Summary

| Aspect | Strategy |
|--------|----------|
| **File Structure** | Single monolithic file + algorithmic index |
| **Navigation** | Grep-able `@SECTION_*` markers + line numbers |
| **Validation** | Automated header ↔ code sync checker (`make check-header`) |
| **LLM Efficiency** | Header read (50 lines) → knows entire architecture |
| **Enforced Invariants** | No forward refs, NELA-S ownership, flat map, consistent coords |
| **Debugging** | `make fix-header` auto-regenerates index after bulk changes |

This structure optimizes for **LLM comprehension** over **human readability**.

