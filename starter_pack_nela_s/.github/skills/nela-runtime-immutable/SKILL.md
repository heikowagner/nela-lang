---
name: nela-runtime-immutable
description: >
  Enforces immutable runtime/tooling policy for projects bootstrapped from this starter pack.
applyTo: "src/*.py,tools/*.py,Makefile"
---

# Immutable Runtime Policy (Starter-Pack Projects)

This skill is repository-template specific.
The starter pack itself contains only authoring assets under `.github/`.
Apply this policy only after runtime/tool files are copied into the target project.

## Rule

If runtime/tool files were copied from the starter pack, they must not be modified during normal feature work.

Protected paths:
- `src/*.py`
- `tools/*.py`
- `Makefile`

## Reason

NELA-S project logic must evolve in `.nela` sources.
Host/runtime infrastructure should remain stable and versioned as a fixed base.

## Allowed Exception

Only modify protected files when the user explicitly requests a runtime/tooling change in the current prompt.

## Decision Procedure

Before editing protected files:
1. Check whether user asked explicitly for runtime/tool change.
2. If not explicit, stop and keep changes in `.nela` files.
3. If explicit, keep edits minimal and document why.
