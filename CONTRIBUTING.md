# Contributing to Nela-Lang

Thanks for contributing.

This project currently has two major parts:
- NELA-S language tooling in Python (`src/nela_parser.py`, `src/nela_runtime.py`, `src/nela_compiler.py`)
- NELA-C runtime/reducer in C (`src/nelac_runtime.c`)

## Ways to Contribute

- Fix bugs in parser/runtime/compiler behavior
- Improve tests and coverage
- Improve C runtime performance and safety
- Improve documentation and examples
- Help design the C-first frontend roadmap (lexer/parser/AST in C)

## Development Setup

Prerequisites:
- Python 3.10+
- C compiler (`cc` on macOS/Linux)
- `make`

Optional (for Wolf GPU demo only):
- `pygame`

From repo root:

```bash
make
make test
```

This builds `nelac` and runs the Python test suite.

## Core Validation Commands

Run these before opening a PR:

```bash
make check-header
make test
python3 src/nela_compiler.py
```

Notes:
- `make check-header` validates NELA section-header sync in `examples/wolf/wolf_game.nela`.
- If header sync fails after legitimate edits, run:

```bash
make fix-header
```

Then re-run checks.

## Project Layout

- `examples/` — NELA-S programs and demos
- `examples/wolf/` — Wolf demo assets, source, and tools
- `src/` — parser/runtime/compiler (Python) and `nelac` runtime (C)
- `tools/` — maintenance utilities (header validator)
- `.github/skills/` and `.github/agents/` — repo guidance for AI-assisted workflows

## Coding Guidelines

- Prefer small, focused PRs.
- Do not mix unrelated refactors with feature changes.
- Keep existing file conventions and naming style.
- Avoid introducing new dependencies unless clearly justified.
- Keep generated/transient files out of commits (for example `__pycache__` artifacts).

## Tests

When adding or changing behavior:
- Add or update tests close to the affected area.
- Include edge cases and failure cases when relevant.
- Keep output deterministic.

If a change affects Wolf rendering/game logic, also test:

```bash
python3 examples/wolf/src/wolf_player.py
```

## Commit and PR Guidance

Suggested commit style:
- `feat: ...`
- `fix: ...`
- `refactor: ...`
- `docs: ...`
- `test: ...`

PR checklist:
- [ ] `make check-header` passes
- [ ] `make test` passes
- [ ] `python3 src/nela_compiler.py` passes
- [ ] Documentation updated (if behavior or structure changed)
- [ ] No unrelated files changed

## Reporting Issues

Useful issue reports include:
- Reproduction steps
- Expected vs actual behavior
- Platform and Python/C compiler versions
- Minimal input/example that demonstrates the problem

## New Contributors

If you are new to compiler/runtime projects, start with:
- docs improvements
- test additions for existing behavior
- small parser/runtime bugfixes

If you want a starter task, open an issue and ask for a "good first task".
