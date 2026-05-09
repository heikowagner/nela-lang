# NELA — Net-based Executable Logic Automaton

A programming language designed for LLMs, not humans.

NELA eliminates human-centric syntactic overhead and encodes computation as a formally
verifiable, locally-executing graph. The goal is a representation that an LLM can
read, write, and reason about more reliably than text-based languages — while retaining
the formal guarantees of Interaction Net theory (strong confluence, linearity, no
global state).

_The renunciation of human readability is the price for a software generation with a mathematical guarantee of stability. In a world in which AI systems control critical infrastructures, formal security is a necessity. In the end, the human understanding of the source code is irrelevant; the provable correctness of the machine logic is the only factor._

---

## Architecture: Two Layers

```
You describe intent in English
           │
           ▼
  ┌─────────────────┐
  │  NELA Architect │  Translate intent → TypedSpec
  └────────┬────────┘
           │
           ▼
  ┌──────────────────────────┐
  │  NELA Constructor        │  Write NELA-S: ML/Haskell-like syntax (.nela files)
  │  (what LLMs produce)     │  — ops: def / match / let / if / :: / ++ / ...
  └────────┬─────────────────┘
           │  examples/quicksort.nela
           │  examples/mergesort.nela
           │  examples/stack_vm.nela
           ▼
  ┌──────────────────────────┐
  │  NELA Compiler (future)  │  Lower NELA-S → NELA-C (interaction nets)
  │  — never hand-written    │  — used for: formal verification, parallel reduction
  └────────┬─────────────────┘
           ▼
  ┌──────────────────────────┐
  │  NELA Runtime            │  Tree-walking interpreter (current)
  │  src/runtime.py          │  or interaction net reducer (compiled path)
  └──────────────────────────┘
```

**Key rule:** LLMs write **NELA-S** (surface layer). The interaction net layer (NELA-C) is a
compiler backend — the formal semantic foundation, never hand-authored.

---

## Theoretical Foundation

| Rank | Theory | Why |
|------|--------|-----|
| 1 | **Interaction Nets** (Lafont, 1990) | Strong confluence, linearity, Turing-complete in 3 combinators |
| 2 | **Symmetric Interaction Combinators** (Lafont, 1997) | Minimal universal substrate (γ, δ, ε) |
| 3 | **Linear Logic** (Girard, 1987) | Resource semantics: every value used exactly once unless `!`-promoted |
| 4 | **Dependent Type Theory** (Martin-Löf, 1984) | Programs = proofs; ill-typed nets are unrepresentable |
| 5 | **Von Neumann CA** | Locality metaphor; conceptual ancestor |

See [.gh/skills/nela-foundations/SKILL.md](.gh/skills/nela-foundations/SKILL.md) for full
mathematical derivations.

---

## Working Examples

### Quicksort — `examples/quicksort.nela`

Classic divide-and-conquer sort. Single function, recursive `call`, `filter`, `append`, list
`match`. The baseline example that validated the NELA-S design.

```
Input:  [5, 3, 8, 1, 9, 2, 7, 4, 6]
Output: [1, 2, 3, 4, 5, 6, 7, 8, 9]   ✓
```

### Mergesort — `examples/mergesort.nela`

Three cooperating functions: `split` (interleave into halves), `merge` (merge sorted lists),
`mergesort` (base cases + recursive divide). Demonstrates:
- Multi-def programs with function calls across definitions
- **Pair ADT**: `{"op": "pair"}` / `{"op": "fst"}` / `{"op": "snd"}` to return two values
- Multi-argument function calls (`merge` takes two list parameters)
- Nested pattern matching

```
Input:  [20, 19, ..., 1]       (20 elements)
Output: [1, 2, ..., 20]        ✓
```

### Wolf Grid — `examples/wolf_grid.nela`

Pure integer raycasting engine, ported from
[`maksimKorzh/wolfenstein-pygame`](https://github.com/maksimKorzh/wolfenstein-pygame).
Discrete DDA (cardinal directions), BFS reachability, no trig needed.

| Function | Logic |
|---|---|
| `map_get map idx` | Index flat grid list via built-in `head (drop idx map)` |
| `is_wall map x y w` | Wall check: `map_get map (x + y * w)` |
| `cast_ray map x y dx dy w` | DDA: step `(dx,dy)` until wall; return step count |
| `wall_height dist` | Projected height: `19200 / dist` |
| `scan_4 map px py w` | Cast in ±x, ±y; return `[right, down, left, up]` |
| `reachable map sx sy gx gy w` | BFS; returns 1 if open path exists, 0 otherwise |

```
scan_4 map 1 1 5  →  [3, 3, 1, 1]   (open corridor right/down, wall left/up)
```

### Wolf Game — `examples/wolf_game.nela` + `src/wolf_player.py`  *(v0.9 — full game loop in NELA-S)*

Fully playable Wolfenstein raycaster. **All game logic is pure NELA-S.** Python is strictly I/O:
raw keyboard and `print`. Zero precomputed data — no trig tables, no constants.

Float positions: 1 unit = 1 grid cell. Angles 0–359° (integer). Direct `sin`/`cos` builtins.
State: `[px, py, angle]` where `px`, `py` are floats.

| NELA-S function | Logic |
|---|---|
| `deg_to_rad a` | `a * 0.017453292519943295` (π/180 as float literal) |
| `norm_angle a` | Normalize to 0–359 (handles negatives by recursion) |
| `is_wall map x y w` | `map_get map ((floor x) + (floor y) * w)` |
| `ray_march map w rx ry dx dy limit` | Float DDA, 400-step limit, step 0.05 cells |
| `render_col ...` | Single column wall height: `floor(21 / ((dist+1)*0.05))` |
| `render_cols ...` | 40 columns across ±20° FOV → list of heights |
| `shade_of h screen_h` | Shade level integer (0=ceil, 1=floor, 2–4=wall distance) |
| `frame_cell row h mid screen_h` | Pixel decision: ceiling / floor / wall shade |
| `frame_row`, `make_frame` | Assemble 21×40 frame as `list[list[int]]` |
| `render_frame ...` | Heights + full frame in one call |
| `move_forward`, `move_back` | Float movement (0.2 cells/step) with collision |
| `turn state delta` | Rotate by delta degrees; normalise |
| `update state key map w` | Dispatch on key 0–3 → new state (no trig table args) |
| `use_door state map w` | Step 1 cell in facing direction; `aset map idx 0` if wall, else noop |
| `key_action c` | Maps char → action code (0=fwd, 1=back, 2=left, 3=right, 4=door, 5=quit) |
| `game_loop state map w token` | Full IOToken loop: io_print → io_key → update → recurse until quit |

**Run the game:** `cd src && python3 wolf_player.py`  (W/S = move, A/D = turn, Q = quit)

---

### Stack Machine — `examples/stack_vm.nela`

A complete stack-based virtual machine — the same execution model as CPython, the JVM, and
WebAssembly. Two functions: `vm_eval` (entry point) and `vm_run` (recursive execution loop).

Instruction set encoding:

| Encoding | Instruction | Semantics |
|----------|-------------|-----------|
| `[0, n]` | PUSH n | push n onto stack |
| `[1]` | ADD | pop a, b → push b+a |
| `[2]` | SUB | pop a (top), b → push b−a |
| `[3]` | MUL | pop a, b → push a×b |
| `[4]` | NEG | pop a → push −a |
| `[5]` | DUP | duplicate top of stack |
| `[6]` | SWAP | exchange top two stack elements |

| Function | Logic |
|---|---|
| `vm_eval program` | Entry point: start with empty stack, dispatch to `vm_run program [] 0` |
| `vm_run program stack pc` | Recursive interpreter: fetch opcode at program counter `pc`, execute, advance |
| Opcode dispatch | All 7 ops via chained `if`/`eq`; handle each case with stack manipulation |
| Stack manipulation | Use `let` bindings to name popped values; use `::` cons to construct new stack |
| Program termination | When `pc >= len(program)`, return top of stack (or 0 if stack empty) |

Sample programs:

```python
[[0,3], [0,4], [1]]                         # 3 + 4 = 7
[[0,4], [5], [3]]                           # 4 × 4 = 16  (DUP then MUL)
[[0,3],[0,4],[1],[0,5],[0,2],[2],[3]]       # (3+4)×(5-2) = 21
[[0,10],[0,5],[2],[0,2],[3]]                # (10−5)×2 = 10
```

Example execution: `[[0,3], [0,4], [1]]` (compute 3+4)

```
pc=0: PUSH 3   →  stack = [3]
pc=1: PUSH 4   →  stack = [4, 3]
pc=2: ADD      →  pop 4, pop 3 → push 7  →  stack = [7]
pc=3: end      →  return 7
```

What makes this non-trivial in NELA-S:
- Two runtime types in play simultaneously (program list of instructions + integer stack)
- Fetch-decode-execute loop with 7-way opcode dispatch via chained `if`/`eq` conditionals
- Stack operations require careful `let`-binding for popped values and `::` reconstruction
- Test matrix: arithmetic, DUP/SWAP interaction, nested operations, stack underflow edge cases

---

---

## NELA-S Syntax (Quick Reference)

NELA-S programs are written in ML/Haskell-like syntax and saved as `.nela` files.
`src/parser.py` parses `.nela` source into the dict AST evaluated by the runtime.

```haskell
-- Program = one or more def forms
def name param ... = body

-- Expr
INT | FLOAT | CHAR             -- literals: 42, 3.14, 'x'
name                           -- variables
[]                             -- nil list
[x]                            -- singleton
e :: e                         -- cons (right-assoc)
e ++ e                         -- append
(e, e)                         -- pair
[x <- list | pred]             -- list comprehension (filter)
match e | pat = body | pat = body   -- exhaustive pattern match
let x = e in body              -- local binding
let (a, b) = e in body         -- tuple destructuring
if e then e else e             -- conditional
e op e                         -- + - * == < <= > >=
f e e ...                      -- function application

-- Builtins (unary)
head tail fst snd not          -- list / pair / logic
sin cos sqrt floor ceil round abs  -- math (float)
ord chr                        -- char <-> int

-- Builtins (binary)
take n lst   drop n lst   get lst n   -- list slicing / indexing (get is O(1))

-- Pattern (inside match cases)
[]             -- nil
[h]            -- singleton
h :: t         -- cons
h :: h2 :: t   -- nested cons (3-spine)
(p1, p2)       -- tuple decomposition
_              -- wildcard
name           -- catch-all variable
```

---

## Running the Tests

```bash
python3 src/runtime.py
```

Expected output:

```
# QUICKSORT     9/9  PASS
# MERGESORT     9/9  PASS
# STACK VM     12/12 PASS
# WOLF GRID    17/17 PASS
# WOLF GAME    14/14 PASS
# V0.7         15/15 PASS
# V0.8          9/9  PASS
# V0.9          7/7  PASS
Overall: ALL TESTS PASSED
```

92 total test cases. Requires Python 3.10+. No external dependencies.

---

## Project Structure

```
llm_coder/
├── README.md
├── examples/
│   ├── quicksort.nela           NELA-S: recursive quicksort
│   ├── mergesort.nela           NELA-S: three-function mergesort with Pair ADT
│   ├── stack_vm.nela            NELA-S: complete stack-based virtual machine
│   ├── wolf_grid.nela           NELA-S: discrete DDA grid engine + BFS reachability
│   ├── wolf_game.nela           NELA-S: angle raycaster, frame assembly, game update
│   └── *.nela.json              Legacy IR (JSON AST — still loadable)
├── src/
│   ├── parser.py                ML/Haskell-like syntax parser (.nela → dict AST)
│   ├── runtime.py               Surface language interpreter + test harness (92 tests)
│   ├── compiler.py              NELA-C compiler: AST → interaction net → .nelac bytecode
│   └── wolf_player.py           I/O-only harness: keyboard input and terminal output only
└── .gh/
    ├── agents/
    │   └── llm-lang.agent.md    VS Code agent: LLM Language Architect
    └── skills/
        ├── nela-foundations/
        │   └── SKILL.md         Mathematical foundations (Interaction Nets, LL, DTT)
        ├── nela-headers/
        │   └── SKILL.md         Reusable section-header and synchronization standard
        └── nela-tools/
            └── SKILL.md         Toolchain spec (interpreter, compiler, type checker)
```

---

## Start a New NELA Project

Use the **starter pack template** in `starter_pack_nela_s/` to bootstrap new NELA projects.
The starter pack is a copy-ready structure with all necessary agent/skills and a frozen
Python runtime—no modification to runtime files is permitted.

⚠️ **Temporary limitation:** The starter pack currently embeds the Python toolchain in the project.
The final solution will strip out embedded tools and fetch the compiler pipeline from an external package/service.
This bootstrap approach is for now only.

### 1) Copy the Starter Pack

```bash
cp -r starter_pack_nela_s/ my-nela-project
cd my-nela-project
```

The starter pack includes:

- `.gh/agents/nela-s-authoring.agent.md` — NELA-S authoring agent (LLM-focused)
- `.gh/skills/nela-s-writing/SKILL.md` — Complete NELA-S syntax and operator reference
- `.gh/skills/nela-runtime-immutable/SKILL.md` — Immutability policy for embedded toolchain (temporary)
- `_t/` — Embedded compiler, parser, runtime (frozen, immutable; to be removed in final solution)
- `tools/validate_nela_header.py`, `Makefile` — Build and validation scripts

**Critical:** All files in `_t/` are frozen copies. Do not modify them.
When the final dependency system is ready, this folder will be removed.

### 2) Create Project-Specific Files Fresh

In your new repo, create:

- `.instructions.md` (project workflow and constraints)
- `README.md` (project mission and architecture)
- `src/*.nela` (new NELA-S source programs — **all project source here**)
- `.copilot-instructions.md` (optional: agent customization)
- Optional host harness files (standalone Python/other scripts, if needed for I/O or external integration)

**Directory layout (current bootstrap structure):**

```
my-nela-project/
├── .gh/
│   ├── agents/
│   │   └── nela-s-authoring.agent.md      (from starter pack)
│   └── skills/
│       ├── nela-s-writing/
│       ├── nela-runtime-immutable/
│       └── ...
├── src/
│   ├── module_a.nela                      (new NELA-S source)
│   ├── module_b.nela
│   └── ...
├── _t/                                    (embedded toolchain; temporary)
│   ├── parser.py
│   ├── runtime.py
│   ├── compiler.py
│   └── ...
├── .instructions.md                       (new: project policy)
├── README.md                              (new: project overview)
├── Makefile                               (frozen from starter pack)
└── my_host.py                             (optional: custom I/O harness)
```

**Future structure (when toolchain becomes a managed dependency):**

```
my-nela-project/
├── .gh/
│   └── ...
├── src/
│   └── *.nela                             (all NELA-S source)
├── .nela.toml or pyproject.toml           (reference external toolchain version)
├── .instructions.md
├── README.md
└── ...
```

**LLM-optimized naming and structure:**

Path references directly impact token consumption. Consider these abbreviations:

| Full name | Abbrev | Savings per ref | Rationale |
|---|---|---|---|
| `_nela_tools/` | `_t/` | 10 chars | Immutable toolchain is unambiguous in context |
| `.github/` | `.gh/` | 5 chars | Standard abbreviation (GitHub → gh) |
| `nela_parser.py` | `parser.py` | 5 chars | Module name is redundant in context |
| `nela_runtime.py` | `runtime.py` | 6 chars | Module name is redundant in context |
| `nela_compiler.py` | `compiler.py` | 7 chars | Module name is redundant in context |

Example: a project with 50 path references per LLM context window saves 500–1500 tokens by abbreviating aggressively.

**Structure principles for LLM efficiency:**

- Flatten directory trees; avoid `_tools/subdir/subdir/file.py` — use `_t/file.py` instead
- Group by semantic function (source, tools, metadata) rather than by file type
- Single source directory (`src/`) with all `.nela` files minimizes context bloat
- Separate immutable tools (`_t/`) from mutable source (`src/`) — allows LLM to skip reasoning about frozen code
- Use hyphens in paths (`_src-nela`, `_t-validate`) if disambiguation needed; avoid double underscores

**Recommended abbreviated structure:**

```
my-nela-project/
├── .gh/agents/
│   └── nela-s-authoring.agent.md
├── .gh/skills/
│   ├── nela-s-writing/
│   └── nela-runtime-immutable/
├── src/
│   ├── module_a.nela
│   └── module_b.nela
├── _t/                         (immutable toolchain; _t = _tools abbreviation)
│   ├── parser.py
│   ├── runtime.py
│   ├── compiler.py
│   └── disasm.py
├── .instructions.md
├── README.md
├── Makefile
└── my_host.py
```

### 3) Add Your First NELA Module

Create a new file in `src/` and follow the header standard from
`.gh/skills/nela-s-writing/SKILL.md`.

Minimum workflow:

```bash
make check-header
make fix-header
make test
```

### 4) Authoring with the Starter Pack Agent

Use the `nela-s-authoring.agent.md` (invoked via VS Code Chat) to write NELA-S programs.
The agent has access to:

- `.gh/skills/nela-s-writing/SKILL.md` — Complete syntax, operators, and builtins
- `.gh/skills/nela-runtime-immutable/SKILL.md` — Enforcement rules for immutable runtime
- Your `.instructions.md` — Project-specific constraints

The agent will **not permit** modifications to frozen runtime files.

---

## Theory Alignment

NELA's theoretical foundation is **Interaction Nets** (Lafont, 1990), supported by **Linear Logic**
and **Dependent Type Theory**. This section explains how the v0.4 surface syntax connects to those
ideas — and where the gaps remain.

### What the syntax gets right

| Surface feature | Theoretical basis |
|---|---|
| `def f x y = body` — fixed-arity named functions | Interaction net agents have a fixed number of ports; each `def` maps to an agent signature |
| Exhaustive `match` | Linear Logic: every value must be consumed; a non-exhaustive match would leave a resource dangling |
| `let (a, b) = split t in …` | Tensor product `A ⊗ B` — both components are consumed exactly once |
| `h::t` cons / `[]` nil | Standard inductive list type; compiles to Con/Mat agent pairs in NELA-C |
| No mutation, no global state | Strong confluence: reductions are local and order-independent; the same result is reached regardless of evaluation order |
| `h::h2::t` 3-spine pattern | Matches multi-level structure in one step, mirroring multi-port agent matching in interaction nets |

### Why v0.4 ML syntax is more aligned than v0.3 S-expressions

The S-expression syntax (v0.3) was structurally similar to untyped Lisp — familiar, but without
inherent directionality or arity discipline. The ML/Haskell-like style makes the type-theoretic
structure explicit: each `def` declares a function with a fixed signature, pattern matching is
exhaustive and structurally recursive, and the `let … in` binding form matches the proof-term
notation of the linear sequent calculus. This is a closer match to how interaction net agents and
their rewrite rules are actually specified.

### Known gaps (future compiler work)

| Gap | Explanation |
|---|---|
| **Linearity not enforced** | `def f x = x + x` duplicates `x`, violating the `A -o A` linear function type. The runtime does not check this; it is a guideline enforced at the (future) type checker layer. |
| **No type signatures** | Dependent types are aspirational. The surface language is untyped; type inference belongs to the future `nela.types` tool. |
| **Tuples are a surface convenience** | `(a, b)` / `fst` / `snd` are parsed into `pair`/`fst`/`snd` ops. In NELA-C they should be the tensor product `A ⊗ B`; currently the connection is by convention, not enforced. |
| **NELA-C compiler not yet built** | The interaction net layer remains the formal semantic foundation and the planned compiler target. The runtime is a tree-walking interpreter that validates semantic correctness now. |

---

## Design Decisions Log

| Decision | Rationale |
|----------|-----------|
| ML/Haskell-like syntax (v0.4) instead of S-expressions (v0.3) | Further token reduction; maximally familiar to LLMs trained on Haskell/OCaml/ML; `def f x = body`, `match e \| pat = ...`, `h::t`, `++`, list comprehensions |
| S-expression syntax (v0.3) instead of JSON (v0.2) | ~8× fewer tokens; familiar to LLMs from Lisp/Scheme training data; balanced parens are much easier to generate than nested JSON key-value dicts; JSON remains the IR |
| Tree-walking interpreter first, interaction net compiler later | Validated design without compiler dependency; tests prove semantic correctness now |
| Interaction nets as compiler backend, not surface | v0.1 hand-writing of 17-node nets was unworkable: more tokens than Python, ambiguous port conventions, 0 successful reductions |
| `"a": [Expr]` for all function arguments | Uniform; supports single-arg and multi-arg functions identically |
| Lists are Python lists internally | Avoids a spurious cons-cell heap; the formal `Cons`/`Nil` ADT is the semantic model, Python list is the runtime carrier |
| `else_` (with underscore) for else branch | Avoids collision with Python `else` keyword in dicts |

---

## v0.5 — Completed

| Feature | Status | Notes |
|---|---|---|
| `%` modulo operator | ✅ done | `_parse_mul` + `op=="mod"` in runtime |
| `/` integer division | ✅ done | `op=="div"` in runtime |
| Unary `-` (negation) | ✅ done | `_parse_unary()` → `{"op":"neg","e":...}` |
| Neg literals in arg position | ✅ done | Use `(-1)` paren syntax (Haskell convention) |
| Fixed-point trig raycasting | ✅ done | Sin/cos×64 tables passed as list args from Python |
| Frame assembly in NELA-S | ✅ done | `shade_of`, `frame_cell`, `make_frame`, `render_frame` |
| I/O-only Python harness | ✅ done | `wolf_player.py` — keyboard + print only |

## v0.6 — Completed

Float literals and math builtins added. Python harness is now **strictly I/O** — zero precomputed data.

| Feature | Status | Notes |
|---|---|---|
| `float` literals | ✅ done | `3.14`, `0.017453` etc. parsed to `{"op":"float","v":...}` |
| `sin`/`cos`/`sqrt` builtins | ✅ done | Direct `math.sin`, `math.cos`, `math.sqrt` in runtime |
| `floor`/`ceil`/`round`/`abs` builtins | ✅ done | Return Python `int` where applicable |
| Eliminate sin/cos tables | ✅ done | `deg_to_rad` + `sin`/`cos` replace all `get_nth sin_tab a` calls |
| Float positions in wolf_game | ✅ done | 1 unit = 1 cell; `is_wall` uses `floor` for grid lookup |
| I/O-only Python harness | ✅ done | `wolf_player.py` — zero precomputed data, keyboard + print only |
| Wolf Game test suite | ✅ done | 14 test cases covering trig, raycasting, game update |

## v0.7 — Completed

O(1) list indexing, character literals, and char↔int conversion.

| Feature | Status | Notes |
|---|---|---|
| `get lst n` builtin | ✅ done | O(1) list index: `get lst 2` → `lst[2]`; replaces `head (drop n lst)` |
| `char` literals `'x'` | ✅ done | Single-quoted: `'A'`, `'0'`, `' '`; stored as Python `str` |
| `ord c` builtin | ✅ done | `ord 'A'` → `65` (char → int) |
| `chr n` builtin | ✅ done | `chr 65` → `'A'` (int → char) |
| wolf_game.nela O(1) map lookup | ✅ done | `map_get map idx = get map idx` (was `head (drop idx map)`) |

## v0.8 — Completed

Array builtins (`array`, `aset`, `len`) and live map mutation via `use_door`.

| Feature | Status | Notes |
|---|---|---|
| `array n v` builtin | ✅ done | Creates list of length `n` filled with `v`; `array 3 0` → `[0,0,0]` |
| `aset arr i v` builtin | ✅ done | Functional update: returns copy with `arr[i] = v` |
| `len arr` builtin | ✅ done | `len [1,2,3]` → `3` |
| `use_door state map w` | ✅ done | Steps 1 cell forward; opens (sets 0) wall tile if present |

## v0.9 — Completed

IOToken linear I/O. The entire game loop is now pure NELA-S.

| Feature | Status | Notes |
|---|---|---|
| `io_key token` builtin | ✅ done | Reads one keypress; returns `(char, token')` pair; linear: consumes token |
| `io_print frame token` builtin | ✅ done | Calls Python print callback; returns `token'`; linear: consumes token |
| `IOToken` class in runtime | ✅ done | Wraps `read_key` + `print_frame` callbacks; `.fresh()` produces successor token |
| `key_action c` in wolf_game.nela | ✅ done | Maps char → action code in NELA-S (was Python dict) |
| `game_loop` in wolf_game.nela | ✅ done | Full recurse-until-quit loop in NELA-S; Python harness reduced to 2 lines |
| wolf_player.py mission compliance | ✅ done | Python provides only: 2 callbacks + `IOToken(...)` + `run_program(...)` |

## v0.10 — Completed

NELA-C compiler: NELA-S → interaction net bytecode (`.nelac`).

| Feature | Status | Notes |
|---|---|---|
| `compiler.py` | ✅ done | NELA-S AST → interaction net graph → `.nelac` binary |
| Agent vocabulary | ✅ done | 25 agents: CON/DUP/ERA/PAR/INT/FLT/STR/BOO/APP/LAM + arithmetic + list ops |
| Bytecode format | ✅ done | `NELAC` magic + version(u8) + node_count(u32) + node table + root(u32); stable v3/v4 include explicit node IDs so decode is record-order independent |
| Serialise / deserialise | ✅ done | `net_to_bytes` / `bytes_to_net` / `bytes_to_py` roundtrip |
| Disassembler | ✅ done | `disassemble(bytes)` → human-readable node listing |
| `compile_and_run` API | ✅ done | Compiles, reduces, serialises; returns `(python_result, bytes)` |
| 19 compiler tests | ✅ done | qs, mergesort, wolf_game (deg_to_rad, norm_angle, is_wall, key_action, use_door, …) |

Compatibility note:

- Legacy v1/v2 `.nelac` files are still supported.
- Stable v3/v4 format removes node record order dependency by storing explicit node IDs.

Run compiler: `python3 src/compiler.py`

## v0.11 Roadmap

| Feature | Motivation | Theory |
|---|---|---|
| Lazy (unreduced) net compilation | Compile without evaluating; emit active pairs; run the SIC reducer | Full interaction net graph rewriting; strong confluence |

