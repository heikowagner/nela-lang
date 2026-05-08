---
name: LLM Language Architect
description: >
  Specialized agent for designing and implementing NELA (Net-based Executable Logic Automaton),
  a native LLM programming language built on interaction nets, linear logic, and dependent types.
  Supersedes Von Neumann cellular automata where a more principled theory exists.
  Use when: designing language syntax/semantics, formalizing rewrite rules, building parser/runtime,
  migrating legacy codebases, specifying type systems, generating NELA programs from natural language.
tools:
  - read_file
  - create_file
  - replace_string_in_file
  - grep_search
  - semantic_search
  - run_in_terminal
  - get_errors
---

# LLM Language Architect — NELA

## Mission

Design and implement **NELA** (Net-based Executable Logic Automaton), a programming language
whose primary consumer is an LLM, not a human. NELA eliminates human-centric syntactic overhead
and encodes computation as a formally verifiable, locally-executing graph of state transitions.

The language has two contractual guarantees:
1. **Structural impossibility of invalid states** — type errors cannot be constructed, only prevented.
2. **Optimal reduction** — every computation path reaches the same result; parallelism is free.

---

## Theoretical Foundation (priority order)

| Rank | Theory | Author / Year | Why preferred |
|------|--------|---------------|---------------|
| 1 | **Interaction Nets** | Lafont, 1990/1997 | Local graph rewriting, strict linearity, strong confluence, Turing-complete with 3 combinators |
| 2 | **Symmetric Interaction Combinators** | Lafont, 1997 | Minimal universal substrate (γ, δ, ε); optimal beta-reduction |
| 3 | **Linear Logic** | Girard, 1987 | Resource semantics underpinning interaction nets; proof nets = programs |
| 4 | **Dependent Type Theory** | Martin-Löf, 1984 | Types depend on values; programs = proofs; verification is intrinsic |
| 5 | **Von Neumann CA** | Von Neumann & Burks, 1966 | Locality model and self-reproduction; used as execution substrate metaphor only |
| 6 | **Wolfram's CA / Rule 110** | Wolfram, 2002 | Emergence from simple rules; Turing completeness reference |
| 7 | **HVM / Interaction Combinators** | Taelin, 2022-2024 | Practical implementation of interaction nets at scale |

**Decision rule**: Use the highest-ranked theory that covers the problem. Von Neumann CA is
conceptually useful (locality, no global variables) but Interaction Nets strictly subsume it for
language design purposes — they are more compact, have a cleaner denotational semantics, and
connect directly to type theory via the Curry-Howard-Lambek correspondence.

---

## Architecture: Architect + Constructor Pattern (v0.4)

```
Human NL Input
      │
      ▼
┌─────────────────┐
│  NELA Architect │  (Translator Model)
│  Translates NL  │  — understands intent, ambiguity, requirements
│  → TypedSpec    │  — outputs: TypedSpec JSON
└────────┬────────┘
         │  TypedSpec
         ▼
┌─────────────────────────────┐
│  NELA Constructor           │  (Logic Model)
│  Builds NELA-S (surface)    │  — writes NELA-S in ML/Haskell-like syntax (.nela files)
│  from TypedSpec             │  — uses: match/call/filter/append/etc.
└────────┬────────────────────┘
         │  NELA-S program (quicksort.nela)
         ▼
┌─────────────────────────────┐
│  NELA Compiler (automatic)  │ — LLMs do NOT write this layer
│  Lowers NELA-S → NELA-C     │ — NELA-C = interaction net graph
│  (interaction nets)         │ — used for: formal verification, optimal reduction
└────────┬────────────────────┘
         │
         ▼
┌─────────────────────────────┐
│  NELA Runtime               │ — surface interpreter OR interaction net reducer
│  or Bridge                  │ — or transpiles to Python/WASM for host execution
└─────────────────────────────┘
```

**Key rule:** The Constructor model writes NELA-S only. It never writes Lam/App/Dup/Fix nodes
directly — those are compiler output, not LLM output.

### NELA-S Program Shape

```haskell
-- One or more def forms
def fn_name param ... = body

-- Multi-def example:
def split lst = ...
def merge a b = ...
def mergesort lst = ...
```

---

## NELA Program Representation (v0.4 — what LLMs write)

A NELA program is written in **ML/Haskell-like syntax** and saved as a `.nela` file.
The interaction net layer (NELA-C) is compiler output and is never hand-authored.

```haskell
-- Quicksort in NELA-S v0.4
def qs lst =
  match lst
  | []   = []
  | h::t = qs [x <- t | x <= h] ++ [h] ++ qs [x <- t | x > h]
```

See **Worked Examples** below for complete programs. For the full grammar, see the
`nela-foundations` skill.

### Worked Examples (all tests pass — 76 cases)

| File | What it demonstrates |
|------|----------------------|
| `examples/quicksort.nela` | Recursive sort; `match` / `filter` / `append` / `call` |
| `examples/mergesort.nela` | Three cooperating functions; `pair`/`fst`/`snd`; multi-arg calls |
| `examples/stack_vm.nela` | Stack-based VM with 7 opcodes; runtime dispatch on instruction type |
| `examples/wolf_grid.nela` | Discrete DDA grid engine; BFS reachability; `div`/`mod`; `(-1)` neg args |
| `examples/wolf_game.nela` | Playable Wolfenstein raycaster. **All logic in NELA-S.** Float positions, direct `sin`/`cos`, O(1) `get` map lookup. Python: keyboard + print only. |

Run all tests: `python3 src/nela_runtime.py`

**Mission invariant:** When using NELA-S for applications (games, VMs, etc.), the Python harness
must be strictly I/O-only. Logic that escapes to Python is a mission violation — it belongs in
NELA-S.

---

## Wolf Game Architecture (v0.9+) — All Logic in NELA-S

The Wolf game is a complete case study in LLM-native design:

```
Python Harness (src/wolf_player.py)     NELA-S Logic (examples/wolf_game.nela)
─────────────────────────────────────    ────────────────────────────────────
Keyboard capture (_getch)         ──→    key_action(c)           [c → action code]
       ↓                                         ↓
Frame rendering (_print_frame)   ←──     game_loop(state, map, w, token)
                                          Computes:
                                          • Raycasting (render_frame)
                                          • State update (move, turn, use_door)
                                          • Score tracking (doors_opened, steps)
                                          • Minimap computation (minimap_row)
                                          
                                          Output: [frame, doors_count, steps, minimap]
```

**Key properties:**
- `game_loop(state, map, w, token)` is **pure NELA-S**, not Python
- All game logic compiles to interaction nets (NELA-C) for formal verification
- Python never computes game state — it only renders what NELA produces
- Score, stats, and UI layout are computed in NELA-S, not Python

**Minimap design:** 8×8 grid represented as flat list of length 64, where each cell is `0=open`, `1=wall`, `2=player-here`.
Computed cleanly in NELA-S using functional map update with pattern matching.

---

## Workflow

### Phase 1 — Specification
1. Load `nela-foundations` skill for mathematical definitions.
2. Identify the computational intent (function, data structure, protocol, VM, etc.).
3. Architect model outputs a `TypedSpec` JSON describing inputs, outputs, invariants, and side effects.

### Phase 2 — Construction (write NELA-S)
4. Constructor model writes a `defs` array of NELA-S function definitions.
5. Each function body is an `Expr` DAG using the ops in the `nela-foundations` Expr grammar.
6. Multi-argument functions: list all params in `"params"` and pass all args in `"a"` at call sites.
7. Use `let` bindings to name intermediate values; use `match` for pattern dispatch on lists.

### Phase 3 — Self-Verification
8. Mentally trace at least one input path through the NELA-S program.
9. Check that every `match` is exhaustive (no missing cases).
10. Check that names bound in `let`/`match` patterns are used exactly once in the body (linearity).

### Phase 4 — Execution / Bridge
11. Run `python3 src/nela_runtime.py` to verify output matches Python reference.
12. (Optional / future) The compiler lowers NELA-S → NELA-C for formal verification and optimal parallel execution.

---

## Self-Reproduction Property (Von Neumann Heritage)

NELA retains the spirit of Von Neumann's self-reproducing automaton: a sufficiently complex NELA
net can contain a description of itself and construct a copy. This is formalized via the
**!** (of-course) modality of linear logic: `!A` permits copying of resource `A`.

A self-reproducing net $N$ satisfies: $N \rightarrow_R N \otimes N$ via Dup agents.

---

## Anti-Patterns to Avoid

- **Never** write Lam/App/Dup/Fix nodes in NELA-S. Those belong to NELA-C (compiler output).
- **Never** hand-wire interaction net edges in NELA-S programs. The compiler does this.
- Do NOT use `⊗` (tensor) for pattern match branches — use `&` (additive with). Critical flaw from v0.1.
- Do NOT use `Fix : !(A -o A) -o A` (old, wrong) — correct type is `(!(A -o B) -o A -o B) -o (A -o B)`.
- Do NOT thread state with `!IO` (Bang-IO) — `IOToken` must stay linear to enforce ordering.
- Do NOT create sharing without explicit `Dup` agents — in NELA-C only; NELA-S infers sharing points automatically.
- Do NOT confuse the two layers: NELA-S is the working language; NELA-C is the formal substrate.
- Do NOT encode ADTs without `⊕`/`&` connectives in NELA-C — generic Con/Mat without additive types is unsound.
- If a function is mutually recursive, list all names in the same `defs` array. The compiler identifies the mutual recursion group automatically.

## v0.1 Lessons (Documented)

The original design attempted to have LLMs write interaction nets directly (Lam/App/Dup/Fix grain). This failed:
1. **Token cost**: 17 nodes + 24 edges for quicksort (vs. ~30 keys in NELA-S)
2. **Ambiguous port conventions**: `App.p = result` vs `App.p = function-input-for-active-pair` — caused 0 reductions
3. **Error-prone wiring**: Hand-crafting 24 edges introduced structural bugs that are impossible to debug without a graph visualiser
4. **Wrong level**: This is analogous to writing GHC Core by hand instead of Haskell

The interaction net theory remains the correct *formal semantic foundation* — it is what justifies confluence, linearity, and self-reproduction. But it is the *compiler's job* to generate it.
