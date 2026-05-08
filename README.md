# NELA — Net-based Executable Logic Automaton

A programming language designed for LLMs, not humans.

NELA eliminates human-centric syntactic overhead and encodes computation as a formally
verifiable, locally-executing graph. The goal is a representation that an LLM can
read, write, and reason about more reliably than text-based languages — while retaining
the formal guarantees of Interaction Net theory (strong confluence, linearity, no
global state).

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
  │  src/nela_runtime.py     │  or interaction net reducer (compiled path)
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

See [.github/skills/nela-foundations/SKILL.md](.github/skills/nela-foundations/SKILL.md) for full
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

### Wolf Game — `examples/wolf_game.nela` + `src/wolf_player.py`  *(v0.6 — playable)*

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

**Run the game:** `cd src && python3 wolf_player.py`  (W/S = move, A/D = turn, Q = quit)

---



A complete stack-based virtual machine — the same execution model as CPython, the JVM, and
WebAssembly. Two functions: `vm_run` (recursive execution loop) and `vm_eval` (entry point).

**Instruction set:**

| Encoding | Instruction | Semantics |
|----------|-------------|-----------|
| `[0, n]` | PUSH n | push n |
| `[1]` | ADD | pop a, b → push a+b |
| `[2]` | SUB | pop a (top), b → push b−a |
| `[3]` | MUL | pop a, b → push a×b |
| `[4]` | NEG | pop a → push 0−a |
| `[5]` | DUP | push copy of top |
| `[6]` | SWAP | swap top two |

**Sample programs:**

```python
[[0,3], [0,4], [1]]              # 3 + 4 = 7
[[0,4], [5], [3]]                # 4² = 16  (DUP then MUL)
[[0,3],[0,4],[1],[0,5],[0,2],[2],[3]]  # (3+4)*(5-2) = 21
```

What makes this non-trivial in NELA-S:
- Two runtime types in play simultaneously (program list + integer stack)
- 7 opcodes dispatched at runtime via chained `if`/`eq`
- SWAP/DUP require nested `let`-bindings to name intermediate stack values

---

## NELA-S Syntax (Quick Reference)

NELA-S programs are written in ML/Haskell-like syntax and saved as `.nela` files.
`src/nela_parser.py` parses `.nela` source into the dict AST evaluated by the runtime.

```haskell
-- Program = one or more def forms
def name param ... = body

-- Expr
INT | name                          -- literals / variables
[]                                  -- nil list
[x]                                 -- singleton
e :: e                              -- cons (right-assoc)
e ++ e                              -- append
(e, e)                              -- pair
[x <- list | pred]                  -- list comprehension (filter)
match e | pat = body | pat = body   -- exhaustive pattern match
let x = e in body                   -- local binding
let (a, b) = e in body              -- tuple destructuring
if e then e else e                  -- conditional
e op e                              -- + - * == < <= > >=
f e e ...                           -- function application

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
python3 src/nela_runtime.py
```

Expected output:

```
# QUICKSORT     9/9  PASS
# MERGESORT     9/9  PASS
# STACK VM     12/12 PASS
# WOLF GRID    17/17 PASS
# WOLF GAME    14/14 PASS
Overall: ALL TESTS PASSED
```

67 total test cases. Requires Python 3.10+. No external dependencies.

Requires Python 3.10+. No external dependencies.

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
│   ├── nela_parser.py           ML/Haskell-like syntax parser (.nela → dict AST)
│   ├── nela_runtime.py          Surface language interpreter + test harness
│   └── wolf_player.py           I/O-only harness: keyboard input and terminal output only
└── .github/
    ├── agents/
    │   └── llm-lang.agent.md    VS Code agent: LLM Language Architect
    └── skills/
        ├── nela-foundations/
        │   └── SKILL.md         Mathematical foundations (Interaction Nets, LL, DTT)
        └── nela-tools/
            └── SKILL.md         Toolchain spec (interpreter, compiler, type checker)
```

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

## v0.7 Roadmap

| Feature | Motivation | Theory |
|---|---|---|
| `char` / `atom` type | Typed map cells (`'S'`, `'B'`, `' '`); string keys | Tagged integer / interned symbol; fits `⊕` sum type |
| `IOToken` linear I/O | Mutable player state, door state, render side effects | `IO(A)` from Linear Logic; linear token enforces sequencing |
| `Array n A` with O(1) index | Replace `head (drop n lst)` O(n) lookup | Sigma type `Σ(i:Fin n). A`; compiler maps to buffer |
| NELA-C compiler | Formal verification + optimal parallel reduction | Interaction net graph; lowers NELA-S → NELA-C |

