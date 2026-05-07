"""
NELA S-expression parser  (v0.3)

Converts .nela source text into the dict AST consumed by eval_expr()
in nela_runtime.py.

Grammar:
  Program  ::= Def+
  Def      ::= (def NAME (PARAM*) BODY)
  Expr     ::= INT | #t | #f | nil | NAME
             | (match EXPR CASE+)
             | (let NAME EXPR EXPR)
             | (if EXPR EXPR EXPR)
             | (cons EXPR EXPR)
             | (pair EXPR EXPR)
             | (fst|snd|head|tail|not EXPR)
             | (take|drop EXPR EXPR)
             | (append EXPR EXPR)
             | (filter PRED EXPR EXPR)     ; PRED = <= | > | < | >= | =
             | (+ | - | * EXPR EXPR)
             | (= | < | <= | > | >= EXPR EXPR)
             | (and | or EXPR EXPR)
             | (NAME EXPR*)                ; function call
  CASE     ::= (nil EXPR)
             | ((NAME :: NAME) EXPR)       ; cons pattern; _ is a wildcard
"""

import re
from typing import Any

# ── Tokenizer ──────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"""
    ;[^\n]*       |   # line comment — discarded
    [()\[\]]      |   # single-char delimiters
    [^\s()\[\];]+     # atom: name, number, operator, etc.
""", re.VERBOSE)


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text) if not t.startswith(";")]


# ── S-expression reader ────────────────────────────────────────────────────────

def _read_all(tokens: list[str]) -> list:
    pos, results = 0, []
    while pos < len(tokens):
        val, pos = _read_one(tokens, pos)
        results.append(val)
    return results


def _read_one(tokens: list[str], pos: int):
    if pos >= len(tokens):
        raise SyntaxError("Unexpected end of input")
    tok = tokens[pos]
    if tok in ("(", "["):
        pos += 1
        items = []
        while pos < len(tokens) and tokens[pos] not in (")", "]"):
            val, pos = _read_one(tokens, pos)
            items.append(val)
        if pos >= len(tokens):
            raise SyntaxError(f"Unclosed '{tok}'")
        return items, pos + 1
    if tok in (")", "]"):
        raise SyntaxError(f"Unexpected '{tok}'")
    return _parse_atom(tok), pos + 1


def _parse_atom(tok: str):
    if tok == "#t":
        return True
    if tok == "#f":
        return False
    try:
        return int(tok)
    except ValueError:
        pass
    return tok  # symbol (includes "nil", "<=", names, etc.)


# ── AST builder ────────────────────────────────────────────────────────────────

# Binary ops: S-expr head → runtime "op" string
_BINOP: dict[str, str] = {
    "+": "add",  "-": "sub",  "*": "mul",
    "=": "eq",   "<": "lt",   "<=": "le",  ">": "gt",  ">=": "ge",
    "and": "and", "or": "or",
    "append": "append",
}

# Unary ops: S-expr head → runtime "op" string
_UNOP: dict[str, str] = {
    "fst": "fst", "snd": "snd",
    "head": "head", "tail": "tail",
    "not": "not",
}

# Global counter for fresh wildcard names (resets per parse_program call)
_wc: list[int] = [0]


def build_expr(sx: Any) -> dict:
    """Recursively build an eval_expr-compatible dict from a raw S-expr value."""
    # bool must be checked before int (True/False are ints in Python)
    if isinstance(sx, bool):
        return {"op": "bool", "v": sx}
    if isinstance(sx, int):
        return {"op": "int", "v": sx}
    if isinstance(sx, str):
        if sx == "nil":
            return {"op": "nil"}
        return {"op": "var", "n": sx}
    if isinstance(sx, list) and len(sx) == 0:
        return {"op": "nil"}   # () ≡ nil

    if not isinstance(sx, list):
        raise SyntaxError(f"Cannot build expr from {sx!r}")

    head = sx[0]

    if head == "match":
        # (match scrutinee case...)
        return {
            "op": "match",
            "e": build_expr(sx[1]),
            "cases": [_build_case(c) for c in sx[2:]],
        }

    if head == "let":
        # (let x e body)
        return {"op": "let", "x": sx[1], "e": build_expr(sx[2]), "in": build_expr(sx[3])}

    if head == "if":
        # (if cond then else)
        return {
            "op": "if",
            "cond": build_expr(sx[1]),
            "then": build_expr(sx[2]),
            "else_": build_expr(sx[3]),
        }

    if head == "cons":
        return {"op": "cons", "head": build_expr(sx[1]), "tail": build_expr(sx[2])}

    if head == "pair":
        return {"op": "pair", "l": build_expr(sx[1]), "r": build_expr(sx[2])}

    if head in _UNOP:
        return {"op": _UNOP[head], "e": build_expr(sx[1])}

    if head in ("take", "drop"):
        return {"op": head, "n": build_expr(sx[1]), "e": build_expr(sx[2])}

    if head in _BINOP:
        return {"op": _BINOP[head], "l": build_expr(sx[1]), "r": build_expr(sx[2])}

    if head == "filter":
        # (filter pred pivot list)  — pred is a bare symbol: <=, >, etc.
        return {
            "op": "filter",
            "pred": sx[1],
            "pivot": build_expr(sx[2]),
            "list": build_expr(sx[3]),
        }

    # Fallthrough: function call  (fn arg...)
    if isinstance(head, str):
        return {"op": "call", "fn": head, "a": [build_expr(a) for a in sx[1:]]}

    raise SyntaxError(f"Cannot build expr from {sx!r}")


def _build_case(sx: list) -> dict:
    """
    sx is a 2-element list: [pattern, body-expr]

    Supported patterns:
      nil          — matches empty list
      (h :: t)     — cons; use _ for don't-care variables
    """
    pat_sx, body_sx = sx[0], sx[1]
    body = build_expr(body_sx)

    # nil pattern
    if pat_sx == "nil":
        return {"pat": "nil", "body": body}

    # cons pattern: [h :: t]
    if isinstance(pat_sx, list) and len(pat_sx) == 3 and pat_sx[1] == "::":
        h, _, t = pat_sx
        if h == "_":
            _wc[0] += 1; h = f"_w{_wc[0]}"
        if t == "_":
            _wc[0] += 1; t = f"_w{_wc[0]}"
        return {"pat": {"tag": "cons", "x": h, "xs": t}, "body": body}

    raise SyntaxError(f"Unrecognised pattern: {pat_sx!r}")


def _build_def(sx: list) -> dict:
    """
    (def name (p1 p2 ...) body)

    Param entries may be bare names or (name Type) pairs — the type is ignored
    at runtime but useful as documentation in source.
    """
    _, name, params_sx, body_sx = sx[0], sx[1], sx[2], sx[3]
    params = [p if isinstance(p, str) else p[0] for p in params_sx]
    return {"name": name, "params": params, "body": build_expr(body_sx)}


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_program(text: str) -> dict:
    """Parse a NELA S-expression source string into a program dict."""
    _wc[0] = 0   # reset wildcard counter for each fresh parse
    sexprs = _read_all(_tokenize(text))
    defs = [_build_def(sx) for sx in sexprs
            if isinstance(sx, list) and sx and sx[0] == "def"]
    if not defs:
        raise ValueError("No (def ...) forms found in NELA source")
    return {"nela_version": "0.3", "program": defs[0]["name"], "defs": defs}


def parse_file(path: str) -> dict:
    """Parse a .nela file into a program dict."""
    with open(path) as f:
        return parse_program(f.read())
