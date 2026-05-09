---
name: nela-headers
description: >
  Reusable LLM-first header and section metadata standard for NELA-S source files.
  Defines the section index format, per-section metadata blocks, synchronization rules,
  and validation workflow. Load when creating or refactoring any .nela file.
applyTo: "**/*.nela"
---

# NELA Header Standard

Use this skill for any NELA project, independent of domain (games, algorithms, VMs, tooling).

## Purpose

NELA projects keep each module in a single file. To make large files LLM-navigable, every
`.nela` file uses:

1. A section index at the top of the file.
2. Section markers with metadata before each logical block.

## File Header Format

```nela
-- SECTION INDEX FOR LLM NAVIGATION (auto-maintained):
--   SECTION_NAME      [start-end]      func1 | func2 | func3
```

Rules:

- Section lines are inclusive (`[start-end]`).
- Sections are listed in source order.
- Function lists use `|` separators.
- Keep names stable (`SECTION_MAP`, `SECTION_RUNTIME`, etc.) where possible.

## Section Marker Format

```nela
-- ── @SECTION_NAME [start-end] ───────────────────────────────────────────────
-- Functions: func1, func2
-- Purpose: one-line responsibility
-- Called_by: caller1, caller2
-- Key_vars: [x, y, acc]
-- Details: optional implementation notes
```

Rules:

- Keep function declarations inside their documented section.
- Keep dependency order (no forward references).
- Update metadata when behavior or dependencies change.

## Validation Workflow

```bash
make check-header
make fix-header
make test
```

Interpretation:

- `make check-header`: verify section index and section markers are synchronized.
- `make fix-header`: regenerate header/index after structural edits.
- `make test`: confirm no semantic regressions.

## When To Regenerate Headers

- Added/removed/renamed functions.
- Moved code between sections.
- Changed section boundaries.

Avoid manual edits of line ranges unless absolutely necessary; prefer regeneration.
