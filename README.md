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
  │  NELA Architect │  Translate intent → TypedSpec JSON
  └────────┬────────┘
           │
           ▼
  ┌──────────────────────────┐
  │  NELA Constructor        │  Write NELA-S: typed expression DAG in JSON
  │  (what LLMs produce)     │  — ops: match / call / let / if / pair / ...
  └────────┬─────────────────┘
           │  examples/quicksort.nela.json
           │  examples/mergesort.nela.json
           │  examples/stack_vm.nela.json
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

### Quicksort — `examples/quicksort.nela.json`

Classic divide-and-conquer sort. Single function, recursive `call`, `filter`, `append`, list
`match`. The baseline example that validated the NELA-S design.

```
Input:  [5, 3, 8, 1, 9, 2, 7, 4, 6]
Output: [1, 2, 3, 4, 5, 6, 7, 8, 9]   ✓
```

### Mergesort — `examples/mergesort.nela.json`

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

### Stack VM — `examples/stack_vm.nela.json`

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

A NELA-S program is a JSON document `{nela_version, program, defs: [...]}`.  
Each function definition: `{name, params, type, body: Expr}`.

```
Expr :=
  {"op": "var",    "n": name}
  {"op": "int",    "v": number}
  {"op": "bool",   "v": bool}
  {"op": "nil"}                                      list []
  {"op": "cons",   "head": Expr, "tail": Expr}       x : xs
  {"op": "match",  "e": Expr, "cases": [Case]}       exhaustive pattern match
  {"op": "call",   "fn": name, "a": [Expr]}          function call (recursive ok)
  {"op": "let",    "x": name, "e": Expr, "in": Expr} local binding
  {"op": "if",     "cond": Expr, "then": Expr, "else_": Expr}
  {"op": "pair",   "l": Expr, "r": Expr}             (a, b)
  {"op": "fst",    "e": Expr}                        fst (a, b)
  {"op": "snd",    "e": Expr}                        snd (a, b)
  {"op": "head",   "e": Expr}                        head of list
  {"op": "tail",   "e": Expr}                        tail of list
  {"op": "add"|"sub"|"mul", "l": Expr, "r": Expr}
  {"op": "eq"|"lt"|"le"|"gt"|"ge", "l": Expr, "r": Expr}
  {"op": "and"|"or", "l": Expr, "r": Expr}
  {"op": "not",    "e": Expr}
  {"op": "filter", "pred": "<="|">"|"<"|">="|"==", "pivot": Expr, "list": Expr}
  {"op": "append", "l": Expr, "r": Expr}

Case :=
  {"pat": "nil", "body": Expr}
  {"pat": {"tag": "cons", "x": name, "xs": name}, "body": Expr}
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
│   ├── quicksort.nela.json      NELA-S: recursive quicksort
│   ├── mergesort.nela.json      NELA-S: three-function mergesort with Pair ADT
│   └── stack_vm.nela.json       NELA-S: complete stack-based virtual machine
├── src/
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
| JSON expression DAG instead of text syntax | LLMs parse and produce structured JSON more reliably than context-sensitive grammars |
| Tree-walking interpreter first, interaction net compiler later | Validated design without compiler dependency; tests prove semantic correctness now |
| Interaction nets as compiler backend, not surface | v0.1 hand-writing of 17-node nets was unworkable: more tokens than Python, ambiguous port conventions, 0 successful reductions |
| `"a": [Expr]` for all function arguments | Uniform; supports single-arg and multi-arg functions identically |
| Lists are Python lists internally | Avoids a spurious cons-cell heap; the formal `Cons`/`Nil` ADT is the semantic model, Python list is the runtime carrier |
| `else_` (with underscore) for else branch | Avoids collision with Python `else` keyword in dicts |
