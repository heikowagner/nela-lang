---
name: nela-s-writing
description: >
  Practical guidance to write valid NELA-S files.
  Use this skill in new projects created from the starter pack.
applyTo: "**/*.nela"
---

# NELA-S Writing Skill

## Scope

In scope:
- authoring `.nela` functions
- refactoring `.nela` sections
- preserving section metadata and ordering

Out of scope:
- compiler internals
- bytecode formats
- runtime implementation internals

## Required Header Definition

Every `.nela` file should start with a section index and use section markers.

Header index pattern:

```nela
-- SECTION INDEX FOR LLM NAVIGATION (auto-maintained):
--   SECTION_NAME      [start-end]      func1 | func2 | func3
```

Section marker pattern:

```nela
-- ── @SECTION_NAME [start-end] ───────────────────────────────────────────────
-- Functions: func1, func2
-- Purpose: one-line responsibility
-- Called_by: caller1, caller2
-- Key_vars: [x, y, acc]
-- Details: optional implementation notes
```

Header rules:
- Keep sections listed in source order.
- Keep line ranges inclusive.
- Keep function lists synchronized with actual `def` blocks.
- Run `make check-header` after structural changes.
- Run `make fix-header` if out of sync.

## Core Patterns

```nela
def f lst =
  match lst
  | [] = []
  | h::t = ...
```

```nela
def update state action =
  if action == 0 then ...
  else if action == 1 then ...
  else state
```

## Operator Reference (Authoring)

### Arithmetic

- `+` addition
- `-` subtraction
- `*` multiplication
- `/` integer division
- `%` modulo

### Comparison

- `==` equals
- `<` less than
- `<=` less than or equal
- `>` greater than
- `>=` greater than or equal

### Boolean

- `and`
- `or`
- `not` (builtin)

### List / Structure

- `::` cons (prepend)
- `++` append

### Precedence (practical)

1. unary (`not`, unary negation)
2. `*`, `/`, `%`
3. `+`, `-`
4. comparisons (`==`, `<`, `<=`, `>`, `>=`)
5. boolean (`and`, `or`)

Use parentheses when in doubt.

## Builtins Reference

### Unary builtins

- `head lst`
- `tail lst`
- `fst pair`
- `snd pair`
- `not b`
- `sin x`
- `cos x`
- `sqrt x`
- `floor x`
- `ceil x`
- `round x`
- `abs x`
- `ord c`
- `chr n`

### Binary builtins

- `take n lst`
- `drop n lst`
- `get lst n`

### Optional runtime I/O builtins (project-dependent)

- `io_key token`
- `io_print payload token`

Only use I/O builtins if the host runtime exposes them.

## Syntax Quick Reference

```nela
def name arg1 arg2 = expr

[]
h::t
a ++ b

(a, b)
fst p
snd p

let x = e in body
if cond then a else b

match e
| [] = ...
| h::t = ...

[x <- list | pred]
```

## Editing Rules

1. Keep helper definitions above callers.
2. Handle base cases early.
3. Keep function names stable unless rename is requested.
4. Run project checks after structural edits.

## Validation Commands

```bash
make check-header
make fix-header
make test
```
