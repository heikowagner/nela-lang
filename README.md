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
  │  NELA Constructor        │  Write NELA-S: S-expression syntax (.nela files)
  │  (what LLMs produce)     │  — ops: match / call / let / if / pair / ...
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

### Stack VM — `examples/stack_vm.nela`

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

NELA-S programs are written in S-expression syntax and saved as `.nela` files.
`src/nela_parser.py` parses `.nela` source into the dict AST evaluated by the runtime.

```scheme
; Program = one or more (def ...) forms
(def name (param ...) body)

; Expr
INT | #t | #f | nil | name           ; literals and variables
(match e case ...)                   ; exhaustive pattern match
(let x e body)                       ; local binding
(if cond then else)                  ; conditional
(cons e e)                           ; list cons
(pair e e) (fst e) (snd e)           ; Pair ADT
(head e) (tail e)                    ; list accessors (unsafe)
(take n e) (drop n e)                ; list slices
(+ e e) (- e e) (* e e)              ; arithmetic
(= e e) (< e e) (<= e e) (> e e) (>= e e)   ; comparison
(and e e) (or e e) (not e)           ; boolean
(filter pred pivot list)             ; pred: <= > < >= =
(append e e)                         ; list concat
(fn arg ...)                         ; function call

; Pattern (inside match cases)
nil                                  ; matches []
(h :: t)                             ; matches cons; _ is wildcard
```

---

## Running the Tests

```bash
python3 src/nela_runtime.py
```

Expected output:

```
# QUICKSORT    9/9 PASS
# MERGESORT    9/9 PASS
# STACK VM    12/12 PASS
Overall: ALL TESTS PASSED
```

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
│   └── *.nela.json              Legacy IR (JSON AST — still loadable)
├── src/
│   ├── nela_parser.py           S-expression parser (.nela → dict AST)
│   └── nela_runtime.py          Surface language interpreter + test harness
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

## Design Decisions Log

| Decision | Rationale |
|----------|-----------|
| S-expression syntax (v0.3) instead of JSON (v0.2) | ~8× fewer tokens; familiar to LLMs from Lisp/Scheme training data; balanced parens are much easier to generate than nested JSON key-value dicts; JSON remains the IR |
| Tree-walking interpreter first, interaction net compiler later | Validated design without compiler dependency; tests prove semantic correctness now |
| Interaction nets as compiler backend, not surface | v0.1 hand-writing of 17-node nets was unworkable: more tokens than Python, ambiguous port conventions, 0 successful reductions |
| `"a": [Expr]` for all function arguments | Uniform; supports single-arg and multi-arg functions identically |
| Lists are Python lists internally | Avoids a spurious cons-cell heap; the formal `Cons`/`Nil` ADT is the semantic model, Python list is the runtime carrier |
| `else_` (with underscore) for else branch | Avoids collision with Python `else` keyword in dicts |
