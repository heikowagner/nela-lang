---
name: nela-foundations
description: >
  Mathematical foundations for NELA language design. Covers Interaction Nets (primary theory),
  Von Neumann Cellular Automata (locality substrate), Linear Logic (resource semantics),
  and Dependent Type Theory (verification). Load this skill before designing any NELA
  language construct, rewrite rule, or type system component.
---

# NELA Mathematical Foundations

## Critical Architecture Decision: Two-Layer Design

> **Lesson from implementation (v0.1 → v0.2):** Hand-writing interaction nets at the Lam/App/Dup/Fix
> grain is NOT the right surface language for LLMs. Quicksort required 17 nodes and 24 wired edges
> vs. 4 lines in Python — worse, not better, for LLM comprehension. The v0.1 runtime produced
> zero reductions because principal-port wiring conventions were ambiguous at that grain.
>
> **The correct design is a two-layer architecture:**
>
> | Layer | Name | What LLMs do | Representation |
> |-------|------|-------------|----------------|
> | Surface | **NELA-S** | Read, write, reason | Typed expression DAG (JSON) |
> | Core | **NELA-C** | Never touch directly | Interaction net graph |
>
> LLMs write NELA-S. A compiler lowers NELA-S → NELA-C for formal verification and optimal
> parallel execution. The interaction net layer is the *semantic foundation*, not the *working medium*.
> This is the same relation as: Haskell → GHC Core → STG → machine code.

---

## NELA Surface Language (NELA-S) — What LLMs Write

A NELA-S program is a JSON document with:
- `defs`: array of named, typed function definitions
- Each function body is a typed expression DAG (`Expr`)

### Expr grammar

```
Expr :=
  | {"op": "var",    "n": name}                                 -- variable
  | {"op": "int",    "v": number}                               -- integer literal
  | {"op": "bool",   "v": bool}                                 -- boolean literal
  | {"op": "nil"}                                               -- empty list  []
  | {"op": "cons",   "head": Expr, "tail": Expr}                -- list cons  x : xs
  | {"op": "match",  "e": Expr, "cases": [Case]}                -- exhaustive pattern match
  | {"op": "call",   "fn": name, "a": [Expr]}                   -- call (single or multi-arg; recursive ok)
  | {"op": "let",    "x": name, "e": Expr, "in": Expr}          -- local binding
  | {"op": "if",     "cond": Expr, "then": Expr, "else_": Expr} -- conditional
  -- Pair ADT  (return two values from a single function)
  | {"op": "pair",   "l": Expr, "r": Expr}                      -- construct (a, b)
  | {"op": "fst",    "e": Expr}                                 -- project first
  | {"op": "snd",    "e": Expr}                                 -- project second
  -- List accessors
  | {"op": "head",   "e": Expr}                                 -- unsafe head (non-empty only)
  | {"op": "tail",   "e": Expr}                                 -- unsafe tail
  | {"op": "take",   "n": Expr, "e": Expr}                      -- first n elements
  | {"op": "drop",   "n": Expr, "e": Expr}                      -- drop first n
  -- Arithmetic & comparison
  | {"op": "add"|"sub"|"mul", "l": Expr, "r": Expr}
  | {"op": "eq"|"lt"|"le"|"gt"|"ge", "l": Expr, "r": Expr}
  -- Boolean combinators
  | {"op": "and"|"or", "l": Expr, "r": Expr}
  | {"op": "not",    "e": Expr}
  -- List combinators (prefer over hand-written recursion where applicable)
  | {"op": "filter", "pred": "<="|">"|"<"|">="|"==", "pivot": Expr, "list": Expr}
  | {"op": "append", "l": Expr, "r": Expr}

Case :=
  | {"pat": "nil",                                    "body": Expr}
  | {"pat": {"tag": "cons", "x": name, "xs": name},   "body": Expr}
  | {"pat": {"tag": ConstructorName, ...bindings},     "body": Expr}
```

### Token cost comparison (quicksort)

| Representation | Nodes/keys | Approx tokens |
|----------------|-----------|---------------|
| NELA-S (surface) | ~30 keys | ~70 tokens |
| NELA-C (interaction nets, v0.1) | 17 nodes + 24 edges | ~250 tokens |
| Python quicksort | 4 lines | ~45 tokens |

NELA-S is ~3.5× more compact than the interaction net representation and within 2× of Python
while being formally typed and structurally safe.

---

> **Theoretical hierarchy**: Interaction Nets > Linear Logic > Dependent Types > Von Neumann CA.
> Von Neumann CA is included for historical grounding and the locality/quiescence metaphor.
> Where the theories conflict on expressiveness or elegance, prefer Interaction Nets.
> **These theories justify the semantics of NELA-C (compiler backend), not the surface grammar.**

---

## Part 1 — Cellular Automata (Von Neumann, 1966)

### 1.1 Classical Cellular Automaton

**Definition 1.1 (CA).** A *cellular automaton* is a 4-tuple

$$\mathcal{A} = (\mathbb{Z}^d,\; Q,\; \mathcal{N},\; \delta)$$

where:
- $\mathbb{Z}^d$ is the $d$-dimensional integer lattice of *cells*
- $Q$ is a finite set of *states* with a distinguished *quiescent* state $q_0 \in Q$
- $\mathcal{N} = (n_1,\ldots,n_k) \in (\mathbb{Z}^d)^k$ is the *neighborhood vector*
- $\delta: Q^k \rightarrow Q$ is the *local transition function* satisfying $\delta(q_0,\ldots,q_0) = q_0$

A *configuration* is a function $c: \mathbb{Z}^d \rightarrow Q$ with finite support (finitely many non-quiescent cells).

The *global transition* $\Delta: Q^{\mathbb{Z}^d} \rightarrow Q^{\mathbb{Z}^d}$ is defined by:

$$\Delta(c)(x) = \delta\bigl(c(x+n_1),\ldots,c(x+n_k)\bigr)$$

**Key property (Locality):** $\Delta(c)(x)$ depends only on the finite neighborhood $\{x + n_i\}$. No cell has access to global state.

### 1.2 Von Neumann Neighborhood (2D)

For $d = 2$, the *Von Neumann neighborhood* of radius 1 is:

$$\mathcal{N}_{VN} = \{(0,0),\; (1,0),\; (-1,0),\; (0,1),\; (0,-1)\}$$

giving $k = 5$ (center + 4 cardinal neighbors). The transition function is thus $\delta: Q^5 \rightarrow Q$.

### 1.3 Von Neumann's 29-State Automaton

Von Neumann constructed $Q_{VN}$ with $|Q_{VN}| = 29$ to demonstrate self-reproduction. The state set decomposes as:

| Class | Count | Description |
|-------|-------|-------------|
| Ground state $U$ | 1 | Quiescent; no activity |
| Ordinary transmission (OT) | 8 | 4 directions × {resting, excited}; propagate signals |
| Special transmission (ST) | 8 | 4 directions × {resting, excited}; transmit in specific conditions |
| Confluent states (C) | 4 | $C_{00}, C_{01}, C_{10}, C_{11}$; AND/OR logic gates |
| Sensitized states (S) | 8 | Intermediate states during construction; become T or C after stimulation |

**Total:** $1 + 8 + 8 + 4 + 8 = 29$ ✓

**Transition logic (informal):**
- An OT cell in direction $d$ excited at time $t$ transmits excitation to its $d$-neighbor at $t+1$
- A Confluent cell $C_{ij}$ becomes excited iff the logical rule for state $ij$ is satisfied by its neighbors
- A Sensitized cell transitions to a specific T or C state based on stimulation sequence

**Formal construction schema (Thatcher, 1970 simplification):** The Universal Constructor $UC$ can be described as a finite initial configuration $c_0$ such that:

$$\Delta^T(c_0) = c_0 \cup \text{copy}(c_0) \quad \text{for some } T$$

meaning that after $T$ steps, the automaton has produced a copy of itself adjacent to the original.

### 1.4 Why Von Neumann CA is Insufficient Alone

| Limitation | Impact on LLM language design |
|------------|-------------------------------|
| 29 states are ad hoc | No principled derivation from type theory |
| Grid geometry is fixed | Cannot represent arbitrary graph topologies |
| No type system | Structural errors not statically prevented |
| Non-compositional | Hard to decompose large programs modularly |
| Verbose state encoding | Wastes LLM context tokens |

**Conclusion:** Retain the *locality* and *quiescence* metaphors from CA. Replace the state machine with Interaction Nets for actual language semantics.

---

## Part 2 — Interaction Nets (Lafont, 1990/1997)

### 2.1 Signature and Agents

**Definition 2.1 (Signature).** A *signature* $\Sigma$ is a set of *agent names* $\alpha$ each equipped with an *arity* $ar(\alpha) \in \mathbb{N}$.

An *agent* $\alpha$ of arity $n$ is drawn as a node with:
- 1 **principal port** (marked with a dot `•`)
- $n$ **auxiliary ports** $x_1, \ldots, x_n$ (ordered)

Total ports per agent: $ar(\alpha) + 1$.

### 2.2 Nets

**Definition 2.2 (Net).** A *net* $N$ over $\Sigma$ is a graph where:
- Nodes are agents from $\Sigma$ (each occurrence is distinct)
- Edges connect ports; each port is connected to **at most one** other port
- Ports with no connection are *free ports* forming the *interface* $\text{Int}(N)$
- No edge connects two auxiliary ports of the same agent

A net is *closed* if $\text{Int}(N) = \emptyset$.

### 2.3 Active Pairs

**Definition 2.3 (Active pair).** Two agents $\alpha, \beta$ form an *active pair* (or *redex*) when their principal ports are connected:

$$\alpha \bowtie \beta$$

This is the ONLY location where computation can occur.

### 2.4 Interaction Rules

**Definition 2.4 (Rule).** An *interaction rule* for the pair $(\alpha, \beta)$ is:

$$\alpha \bowtie \beta \;\longrightarrow\; N_{\alpha\beta}$$

where $N_{\alpha\beta}$ is a net whose free ports are exactly the auxiliary ports of $\alpha$ and $\beta$.

**Determinism constraint:** Each pair $(\alpha, \beta)$ has **at most one** rule (commutativity: $(\alpha,\beta)$ and $(\beta,\alpha)$ share the same rule).

**Locality:** A rule application affects only the active pair and the net $N_{\alpha\beta}$; the rest of the net is untouched.

### 2.5 Strong Confluence

**Theorem 2.1 (Strong Confluence, Lafont 1990).** For any interaction system $(\Sigma, R)$, if $N \rightarrow_R N_1$ and $N \rightarrow_R N_2$ arise from **different** active pairs, then there exists $N_3$ such that:

$$N_1 \rightarrow_R N_3 \quad \text{and} \quad N_2 \rightarrow_R N_3$$

in exactly **one step each**.

**Corollary:** All reduction strategies (sequential, parallel, random) reach the same normal form. Parallelism is free.

**Proof sketch:** Different active pairs share no ports (by locality). Thus their reductions are completely independent; the resulting nets can be composed to yield $N_3$.

### 2.6 Symmetric Interaction Combinators

**Definition 2.5.** The *Symmetric Interaction Combinators* use signature:

$$\Sigma_{SIC} = \{\gamma,\; \delta,\; \varepsilon\}$$

with arities $ar(\gamma) = 2$, $ar(\delta) = 2$, $ar(\varepsilon) = 0$.

The **six interaction rules**:

| Active pair | Rule name | Effect |
|-------------|-----------|--------|
| $\gamma \bowtie \gamma$ | Annihilation | Aux ports connected crosswise: $x_1 \leftrightarrow y_1$, $x_2 \leftrightarrow y_2$ |
| $\delta \bowtie \delta$ | Annihilation | Same structure |
| $\varepsilon \bowtie \varepsilon$ | Annihilation | Both nodes deleted, no new net |
| $\gamma \bowtie \delta$ | Commutation | Each $\gamma$ copies $\delta$, each $\delta$ copies $\gamma$; 4 nodes created |
| $\gamma \bowtie \varepsilon$ | Erasure | $\varepsilon$ erases $\gamma$; $\gamma$'s aux ports each get their own $\varepsilon$ |
| $\delta \bowtie \varepsilon$ | Erasure | Same as above for $\delta$ |

**Theorem 2.2 (Universality, Lafont 1997).** The symmetric combinators $(\Sigma_{SIC}, R_{SIC})$ are computationally universal: any interaction system can be translated into $(\Sigma_{SIC}, R_{SIC})$.

**Corollary:** Any algorithm expressible in lambda calculus (or equivalently Turing machines) has a representation in NELA using only $\gamma$, $\delta$, $\varepsilon$.

### 2.7 Comparison to Von Neumann CA

| Dimension | Von Neumann CA | Interaction Nets |
|-----------|----------------|-----------------|
| Computation model | State transitions on grid | Graph rewriting |
| Locality | Yes (neighborhood) | Yes (active pair only) |
| Self-reproduction | Provable, ad hoc | Derivable via `!` modality |
| Type system | None | Derived from Linear Logic |
| Confluence | Not guaranteed | Strong confluence (Theorem 2.1) |
| Turing completeness | Yes (29 states) | Yes (3 symbols) |
| Compositional | Limited | Yes (tensor product of nets) |
| LLM token cost | High (29 states × grid) | Low (3 symbols + edges) |

---

## Part 3 — Linear Logic (Girard, 1987)

### 3.1 Resource Semantics

Classical logic has *weakening* ($A \vdash A \wedge A$) and *contraction* ($A \wedge A \vdash A$), meaning propositions can be copied and discarded freely. **Linear Logic** removes these structural rules, making every resource *exactly once usable*.

**Linear implication:** $A \multimap B$ means "consuming $A$ produces $B$" (no copying of $A$).

### 3.2 Connectives and Their Interaction Net Counterparts

| LL connective | Symbol | Interaction net | Meaning |
|---------------|--------|-----------------|---------|
| Tensor | $A \otimes B$ | $\gamma$ (constructor) | Use both $A$ and $B$ once |
| Par | $A \parr B$ | dual of $\gamma$ | Co-tensor |
| Of course | $!A$ | $\delta$ (duplicator) | May copy or erase $A$ |
| Why not | $?A$ | dual of $\delta$ | Co-duplication |
| One | $\mathbf{1}$ | wire | Unit of tensor |
| Bottom | $\bot$ | wire | Unit of par |
| Zero | $0$ | $\varepsilon$ (eraser) | Empty resource |
| With | $A \mathbin{\&} B$ | `Mat_T` (case selector) | Additive pair: offer both branches; consumer picks exactly one |
| Plus | $A \oplus B$ | `Con_i` (tagged injector) | Additive sum: exactly one variant is provided; used for ADT constructors |

> **Correction (from case study translations, Part 6):** The original spec omitted `&` and `⊕`. Their absence caused `Mat_T`'s type to be stated with $\parr$ (par), which is wrong: par cannot select exactly one branch. `&` and `⊕` are required for sound pattern matching over algebraic data types.

**Key identity:** Cut elimination in linear logic proof nets corresponds exactly to interaction net reduction. A proof = a NELA program.

### 3.3 Proof Nets

A *proof net* is a graph representation of a LL proof where:
- Formulas are edges (hyperedges)
- Inference rules are nodes (agents)
- The *correctness criterion* (Girard's long trip criterion) ensures the net is a valid proof

**Consequence for NELA:** A well-typed NELA net IS a proof net. Type checking = proof verification.

---

## Part 4 — Dependent Type Theory (Martin-Löf, 1984)

### 4.1 Judgment Forms

Martin-Löf Type Theory (MLTT) has four basic judgments:

| Judgment | Meaning |
|----------|---------|
| $A \;\mathsf{type}$ | $A$ is a type |
| $a : A$ | $a$ is an element of type $A$ |
| $A = B \;\mathsf{type}$ | $A$ and $B$ are equal types |
| $a = b : A$ | $a$ and $b$ are equal elements of $A$ |

### 4.2 Dependent Products (Π-types)

$$\frac{\Gamma \vdash A \;\mathsf{type} \quad \Gamma, x:A \vdash B(x) \;\mathsf{type}}{\Gamma \vdash \Pi_{x:A} B(x) \;\mathsf{type}}$$

This generalizes function types: when $B$ does not depend on $x$, $\Pi_{x:A} B = A \to B$.

In NELA: a Π-type corresponds to a parameterized agent family $\alpha_v$ where the agent type depends on the value $v$ flowing through a port.

### 4.3 Dependent Sums (Σ-types)

$$\frac{\Gamma \vdash A \;\mathsf{type} \quad \Gamma, x:A \vdash B(x) \;\mathsf{type}}{\Gamma \vdash \Sigma_{x:A} B(x) \;\mathsf{type}}$$

Pairs $(a, b)$ where $b : B(a)$. Used in NELA to encode records and existential types.

### 4.4 Identity Types

$$\frac{\Gamma \vdash a : A \quad \Gamma \vdash b : A}{\Gamma \vdash \mathsf{Id}_A(a,b) \;\mathsf{type}}$$

Inhabitants of $\mathsf{Id}_A(a,b)$ are *proofs that $a$ equals $b$*. In NELA, these are typed edges that enforce program equalities at construction time.

### 4.5 Curry-Howard-Lambek Correspondence

| Logic | Programs | Categories |
|-------|----------|------------|
| Proposition $A$ | Type $A$ | Object $A$ |
| Proof of $A$ | Program of type $A$ | Morphism $1 \to A$ |
| $A \Rightarrow B$ | Function $A \to B$ | Arrow |
| $A \wedge B$ | Pair $(A, B)$ | Product |
| $A \multimap B$ (linear) | Linear function | Monoidal arrow |
| $A \oplus B$ (plus) | Tagged ADT variant / `Con_i` | Coproduct |
| $A \mathbin{\&} B$ (with) | Branch offer / `Mat_T` | Biproduct projection |
| Cut elimination | Beta reduction | Composition |

**NELA consequence:** Writing a NELA program = constructing a proof. An ill-typed NELA net = an unprovable proposition = structurally impossible to construct.

---

## Part 5 — NELA Language Specification (Derived)

### 5.1 Core Signature

NELA's base signature extends $\Sigma_{SIC}$ with typed agents:

> ⚠️ **Revisions from case study translations (Part 6):** `Mat` type changed — was `⊗`/`⊸`, now uses `&` (additive branch offer). `Fix` type changed — function now receives a copyable recursive handle `!(A -o B)`. New agents `Inl`, `Inr`, `IOToken`, `FixMutual` added.

| Agent | Arity | Type | Role |
|-------|-------|------|------|
| `Lam` | 2 | $(A \multimap B) \multimap A \multimap B$ | Lambda abstraction (binder) |
| `App` | 2 | $(A \multimap B) \otimes A \multimap B$ | Application |
| `Dup` | 2 | $!A \multimap A \otimes A$ | Explicit duplication (requires `!A`) |
| `Era` | 0 | $!A \multimap \mathbf{1}$ | Explicit erasure (requires `!A`) |
| `Con_i` | $n_i$ | $A_1 \otimes \cdots \otimes A_{n_i} \multimap T$ | i-th constructor of ADT $T$; injects into $\oplus$ |
| `Mat_T` | $k$ | $(B \mathbin{\&} (A_1 \multimap B) \mathbin{\&} \cdots \mathbin{\&} (A_k \multimap B)) \multimap T \multimap B$ | Pattern match on ADT $T$; selects from `&` branch offer |
| `Inl` | 1 | $A \multimap A \oplus B$ | Left injection into binary sum |
| `Inr` | 1 | $B \multimap A \oplus B$ | Right injection into binary sum |
| `Fix` | 1 | $(!(A \multimap B) \multimap A \multimap B) \multimap (A \multimap B)$ | Fixed-point; function receives copyable self-reference $!(A \multimap B)$ |
| `FixMutual` | $2n$ | $\bigotimes_{i=1}^n (!(A_i \multimap B_i) \multimap A_i \multimap B_i) \multimap \bigotimes_{i=1}^n (A_i \multimap B_i)$ | Mutually recursive function families |
| `IOToken` | 0 | $\mathbf{IO}$ | Linear world token; must be threaded through all effectful nodes |
| `Cell` | 4 | $Q^4 \multimap Q$ | Von Neumann CA cell (locality substrate) |

### 5.2 Rewrite Rules (Core)

**Beta reduction** (`Lam` ⊳ `App`):
```
Lam(body, var) ⊳ App(func, arg)
─────────────────────────────────
body[var := arg]   (substitute via port rewiring)
```

**Duplication** (`Dup` ⊳ `Lam`):
```
Dup(a, b) ⊳ Lam(body, var)
───────────────────────────
Lam(body1, var1) ── a
Lam(body2, var2) ── b
Dup(body1, body2) ⊳ body
Dup(var1,  var2)  ⊳ var
```

**Erasure** (`Era` ⊳ `Lam`):
```
Era ⊳ Lam(body, var)
─────────────────────
Era ⊳ body
Era ⊳ var
```

**Constructor dispatch** (`Con_i` ⊳ `Mat_T`):
```
Con_i(a₁,...,aₙᵢ) ⊳ Mat_T(br₁, ..., brₖ)
────────────────────────────────────────────
Fire branch brᵢ with arguments (a₁ ⊗ ... ⊗ aₙᵢ)
Era ⊳ brⱼ  for all j ≠ i   (erase all other branches)
```
Exactly one branch fires; no duplication occurs. The `&`-type of the branch set
guarantees this: `&` is a non-duplicating offer of multiple possibilities; exactly one
is consumed by annihilation with `Con_i`.

**Sum injection dispatch** (`Inl`/`Inr` ⊳ `Case`):
```
Inl(a) ⊳ Case(left_fn, right_fn)     Inr(b) ⊳ Case(left_fn, right_fn)
──────────────────────────────────    ──────────────────────────────────
App(left_fn, a); Era ⊳ right_fn       App(right_fn, b); Era ⊳ left_fn
```

**CA Cell transition** (`Cell` ⊳ `Cell`): local Von Neumann rule application between adjacent Cell agents (only at CA execution substrate level, not at language semantics level).

### 5.3 Type System

NELA types are **dependent linear types**: dependent products and sums over a linear base.

**Typing judgment:** $\Gamma \vdash N : T$ means net $N$ with free ports typed by context $\Gamma$ has output type $T$.

**Linear context:** $\Gamma = x_1 : A_1, \ldots, x_n : A_n$ where each $x_i$ appears **exactly once** in the derivation.

**Port typing:** Each free port $p$ of net $N$ is assigned a type. Principal ports have output types; auxiliary ports have input types.

**Correctness:** A closed net $N$ is *well-typed* iff:
1. All active pairs $\alpha \bowtie \beta$ have a matching rule in $R_\Sigma$
2. All port connections respect the linear type assignments
3. No proof net correctness violation (Girard's criterion)

### 5.4 Program Serialization Format

> **Version note:** v0.1 used raw interaction net JSON (signature / nodes / edges). This was
> abandoned — see the two-layer design note at the top of this file. **v0.2 and later use
> NELA-S (surface language).** The NELA-C interaction net is the compiler output, not the
> authoring format.

A **NELA-S v0.2** program serializes as:

```json
{
  "nela_version": "0.2",
  "program": "my_program",
  "defs": [
    {
      "name": "fn_name",
      "params": ["x"],
      "type": "List(Nat) -> List(Nat)",
      "body": { "op": "...", "..." : "..." }
    },
    {
      "name": "helper",
      "params": ["a", "b"],
      "type": "List(Nat) -> List(Nat) -> List(Nat)",
      "body": { "op": "...", "..." : "..." }
    }
  ]
}
```

No parentheses. No indentation. No reserved keywords. Pure JSON structure.

The NELA-C format (signature / nodes / edges / interface) is generated by the compiler from
NELA-S and is never hand-authored.

### 5.5 Self-Reproduction (Von Neumann Property, Restated)

A NELA net $N$ is *self-reproducing* iff there exists a finite reduction sequence:

$$N \;\rightarrow_R^*\; N \otimes N$$

This is constructible using `Dup` agents on all top-level `!`-typed ports. The self-reproducing net is the direct analog of Von Neumann's Universal Constructor, but formalized via the `!A` modality of Linear Logic.

---

## Part 5.6 — Standard Library ADTs

All ADTs are $\mu$-recursive types (isorecursive or inductive) encoded as $\oplus$-sums of their constructors. Each is handled with `Con_i` and `Mat_T` agents.

### Boolean
$$\mathbf{Bool} = \mathbf{1} \oplus \mathbf{1}$$

| Agent | Type | Role |
|-------|------|------|
| `True` (`Con_0`) | $\mathbf{Bool}$ | True constant |
| `False` (`Con_1`) | $\mathbf{Bool}$ | False constant |
| `If_Bool` (`Mat_Bool`) | $(B \mathbin{\&} B) \multimap \mathbf{Bool} \multimap B$ | Branch on boolean |

### Natural Numbers (Peano)
$$\mathbf{Nat} = \mu X.\; \mathbf{1} \oplus X$$

| Agent | Type | Role |
|-------|------|------|
| `Zero` | $\mathbf{Nat}$ | Base case |
| `Succ` | $\mathbf{Nat} \multimap \mathbf{Nat}$ | Successor |
| `Mat_Nat` | $(B \mathbin{\&} (\mathbf{Nat} \multimap B)) \multimap \mathbf{Nat} \multimap B$ | Zero/succ split |
| `Add` | $!\mathbf{Nat} \otimes \mathbf{Nat} \multimap \mathbf{Nat}$ | Addition (first arg promoted) |
| `LtEq` | $!\mathbf{Nat} \otimes !\mathbf{Nat} \multimap \mathbf{Bool}$ | Comparison |

> **Note (from quicksort translation):** A `Nat` used as pivot must be explicitly promoted to `!Nat` before duplication. Promotion `promote : Nat -o !Nat` is valid only when the value originates from a `!`-context. All comparison operations therefore require `!Nat` arguments.

### List
$$\mathbf{List}(A) = \mu X.\; \mathbf{1} \oplus (A \otimes X)$$

| Agent | Type | Role |
|-------|------|------|
| `Nil` | $\mathbf{List}(A)$ | Empty list |
| `Cons` | $A \otimes \mathbf{List}(A) \multimap \mathbf{List}(A)$ | Prepend |
| `Mat_List` | $(B \mathbin{\&} (A \multimap \mathbf{List}(A) \multimap B)) \multimap \mathbf{List}(A) \multimap B$ | Case split |
| `Append` | $\mathbf{List}(A) \otimes \mathbf{List}(A) \multimap \mathbf{List}(A)$ | Concatenation |
| `Filter` | $!(A \multimap \mathbf{Bool}) \otimes \mathbf{List}(A) \multimap \mathbf{List}(A)$ | Predicate filter |

### Option
$$\mathbf{Option}(A) = \mathbf{1} \oplus A$$

| Agent | Type | Role |
|-------|------|------|
| `None` | $\mathbf{Option}(A)$ | Absent value |
| `Some` | $A \multimap \mathbf{Option}(A)$ | Present value |
| `Mat_Option` | $(B \mathbin{\&} (A \multimap B)) \multimap \mathbf{Option}(A) \multimap B$ | Safe extraction |

Used for HTTP router route lookup (see Part 6, Case Study 2).

### Result
$$\mathbf{Result}(A, E) = A \oplus E$$

| Agent | Type | Role |
|-------|------|------|
| `Ok` | $A \multimap \mathbf{Result}(A,E)$ | Success |
| `Err` | $E \multimap \mathbf{Result}(A,E)$ | Failure |
| `Mat_Result` | $((A \multimap B) \mathbin{\&} (E \multimap B)) \multimap \mathbf{Result}(A,E) \multimap B$ | Dispatch |

### Stream (linear sequential cursor)
$$\mathbf{Stream}(A) = \mu X.\; \mathbf{1} \oplus (A \otimes X)$$

Isomorphic to `List(A)` structurally, but strict-spine by convention: each element is consumed before the tail is examined. A `Stream` cannot be rewound without explicit `Dup`. Used as parser input (see Part 6, Case Study 3).

### IO (linear world token)
$$\mathbf{IO}(A) = \mathbf{IOToken} \multimap A \otimes \mathbf{IOToken}$$

All effectful nodes have type $A \otimes \mathbf{IO} \multimap B \otimes \mathbf{IO}$, threading `IOToken` linearly. The token cannot be `Dup`-ed (not `!IO`) — this enforces sequential I/O ordering and eliminates data races structurally.

---

## Part 6 — Translation Case Studies and Framework Corrections

> These three translations exposed structural flaws in the original specification. Each flaw is documented with its root cause and the correction applied.

### Case Study 1: Quicksort (recursive divide-and-conquer)

**Source intent** (Python, TheAlgorithms pattern):
```python
def quicksort(lst):
    if not lst: return []
    pivot, *rest = lst
    left  = [x for x in rest if x <= pivot]
    right = [x for x in rest if x >  pivot]
    return quicksort(left) + [pivot] + quicksort(right)
```

**NELA TypedSpec:**
```json
{
  "name": "quicksort",
  "inputs":  [{"name": "lst", "type": "List(!Nat)", "banged": false}],
  "outputs": [{"type": "List(!Nat)"}],
  "invariants": ["sorted(output)", "permutation(input, output)"],
  "side_effects": []
}
```

**NELA net (key active pairs):**
```
Fix ⊳ QS              → unroll once; QS receives !(List(!Nat) -o List(!Nat)) self-handle
QS ⊳ Nil              → return Nil
QS ⊳ Cons(h, t)       → promote h to !Nat; Dup(!h) → h1 ⊗ h2
                         Filter(≤ h1)(t) → left;  Filter(> h2)(t) → right
                         Append(App(QS, left), Cons(h_orig, App(QS, right)))
```

**Flaws exposed:**
| # | Flaw | Root cause | Fix |
|---|------|-----------|-----|
| F1 | `Mat` type used `⊗` for branches | `⊗` consumes all branches; must select one | Changed `Mat_T` to use `&` (additive) |
| F2 | `&` and `⊕` absent from LL section | LL connective table was incomplete | Added With/Plus rows to Part 3.2 |
| F3 | `Fix : !(A -o A) -o A` disallows multiple recursive calls per step | One unrolling can only call itself once | Changed to `(!(A -o B) -o A -o B) -o (A -o B)` |
| F4 | TypedSpec had no `!`-promotion annotation | No way to express duplicatable inputs | Added `"banged": bool` to TypedSpec input schema |

### Case Study 2: HTTP Router (partial matching, side effects)

**Source intent** (Express.js / Flask pattern):
```python
routes = [
    {"method": "GET",  "path": "/users",     "handler": get_users},
    {"method": "POST", "path": "/users",     "handler": create_user},
    {"method": "GET",  "path": "/users/:id", "handler": get_user},
]
def router(req, io):
    match = next((r for r in routes if r matches req), None)
    return match.handler(req, io) if match else (not_found_response, io)
```

**NELA TypedSpec:**
```json
{
  "name": "router",
  "inputs": [
    {"name": "req", "type": "Request", "banged": false},
    {"name": "io",  "type": "IO",      "banged": false}
  ],
  "outputs": [{"type": "Response"}, {"type": "IO"}],
  "side_effects": ["database_read", "database_write"]
}
```

**NELA net structure:**
```
Mat_Request(method, path) dispatches via chain of Inl/Inr agents
Each branch: Mat_Option(Route) → Some → handler(req, io) / None → not_found(req, io)
IO token threaded through every handler node linearly
```

**Flaws exposed:**
| # | Flaw | Root cause | Fix |
|---|------|-----------|-----|
| F5 | No `IO` type for side effects | Framework was purely functional | Added `IOToken` agent and `IO(A)` type pattern |
| F6 | No `Option` ADT for failed match | No standard library existed | Added `Option`, `Result`, and full standard library (Part 5.6) |
| F7 | No `Inl`/`Inr`/`Case` agents | Only generic `Con` — no binary sum injection | Added `Inl`, `Inr`, `Case` with explicit dispatch rules in Part 5.2 |

### Case Study 3: JSON Parser (mutual recursion, stream consumption)

**Source intent** (recursive descent, Python tiny-json pattern):
```python
def parse_value(stream):
    tok = peek(stream)
    if   tok == '"': return parse_string(stream)
    elif tok == '{': return parse_object(stream)  # mutually recursive
    elif tok == '[': return parse_array(stream)   # mutually recursive
    elif tok.isdigit(): return parse_number(stream)
    else: raise ParseError(tok)
```

**NELA TypedSpec:**
```json
{
  "name": "parse_value",
  "inputs": [{"name": "stream", "type": "Stream(Token)", "banged": false}],
  "outputs": [{"type": "Result(JSON, ParseError)"}, {"type": "Stream(Token)"}],
  "mutual_with": ["parse_object", "parse_array"],
  "side_effects": []
}
```

**NELA net structure:**
```
FixMutual(parse_value, parse_object, parse_array)
  each receives !(respective recursive handle)
Mat_Stream → peek first token
Inl/Inr dispatch tree on Token type
Result(Ok(json), Err(e)) threaded as output
Stream(Token) threaded linearly as residual input
```

**Flaws exposed:**
| # | Flaw | Root cause | Fix |
|---|------|-----------|-----|
| F8 | No `FixMutual` for mutually recursive definitions | Only single `Fix` existed | Added `FixMutual` with $\Pi$-typed joint fixed-point |
| F9 | No `Stream` sequential cursor type | No way to consume tokens one-at-a-time | Added `Stream(A)` to standard library (Part 5.6) |
| F10 | TypedSpec missing `mutual_with` field | Assumed single-function scope | Added `"mutual_with": [str]` to TypedSpec schema |

### Summary of All Corrections

| Flaw | Severity | Corrected in |
|------|----------|--------------|
| F1: `Mat` used `⊗` not `&` | **Critical** — type system unsound | Part 5.1, Part 3.2, Tool 3 |
| F2: Missing `&`/`⊕` in LL section | **Critical** — theory incomplete | Part 3.2; Tool 3 type grammar |
| F3: `Fix` type wrong (single self-call) | **High** — no multi-call recursion | Part 5.1; Tool 3 typing rules |
| F4: No `!`-annotation in TypedSpec | Medium | Tool 5 TypedSpec schema |
| F5: No `IO` linear token | **High** — no real effectful programs | Part 5.1; Part 5.6; Tool 3 |
| F6: No standard library ADTs | **High** — language unusable in practice | Part 5.6 (new section) |
| F7: No `Inl`/`Inr`/`Case` agents | **High** — binary sum types broken | Part 5.1; Part 5.2 |
| F8: No `FixMutual` | Medium | Part 5.1 |
| F9: No `Stream` type | Medium | Part 5.6 |
| F10: TypedSpec missing `mutual_with` | Low | Tool 5 |

---

## Part 7 — References and Further Reading

| Reference | Relevance |
|-----------|-----------|
| Von Neumann, J. & Burks, A.W. (1966). *Theory of Self-Reproducing Automata*. Univ. of Illinois Press. | Original 29-state CA; self-reproduction proof; Universal Constructor |
| Lafont, Y. (1990). *Interaction Nets*. POPL 1990. | Primary theory for NELA; locality, strong confluence |
| Lafont, Y. (1997). *Interaction Combinators*. Information and Computation 137(1). | Symmetric combinators; minimality; universality proof |
| Girard, J.-Y. (1987). *Linear Logic*. Theoretical Computer Science 50. | Resource semantics; proof nets; cut elimination |
| Martin-Löf, P. (1984). *Intuitionistic Type Theory*. Bibliopolis. | Dependent types; programs = proofs |
| Wolfram, S. (2002). *A New Kind of Science*. Wolfram Media. | Cellular automata survey; Rule 110 universality |
| Taelin, V. (2022). *Higher-order Virtual Machine (HVM)*. GitHub. | Practical interaction net implementation; optimal reduction at scale |
| Jacobs, B. (1999). *Categorical Logic and Type Theory*. Elsevier. | Curry-Howard-Lambek; categorical semantics |
| Sangiorgi, D. & Walker, D. (2001). *The π-Calculus*. Cambridge. | Mobile processes; alternative for concurrent NELA fragments |
| Thatcher, J.W. (1970). *Self-describing Turing machines and self-reproducing cellular automata*. | Simplified account of Von Neumann's construction |
