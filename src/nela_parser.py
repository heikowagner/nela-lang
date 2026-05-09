"""
NELA Parser v0.9 — ML-style surface syntax

Grammar (informal):
  Program  ::= Def+
  Def      ::= 'def' NAME NAME* '=' Expr
  Expr     ::= LetExpr | IfExpr | MatchExpr | ConsExpr
  LetExpr  ::= 'let' Pat '=' Expr 'in' Expr
  IfExpr   ::= 'if' Expr 'then' Expr 'else' Expr
  MatchExpr::= 'match' Expr ('|' Pat '=' Expr)+
  ConsExpr ::= AppendExpr ('::' ConsExpr)?
  AppendExpr ::= CmpExpr ('++' AppendExpr)?
  CmpExpr  ::= AddExpr (('==' | '<=' | '>=' | '<' | '>') AddExpr)?
  AddExpr  ::= MulExpr (('+' | '-') MulExpr)*
  MulExpr  ::= UnaryExpr (('*' | '/' | '%') UnaryExpr)*
  UnaryExpr::= '-' ApplyExpr | ApplyExpr
  ApplyExpr::= Atom Atom*
  Atom     ::= FLOAT | INT | CHAR | BOOL | NAME | '(' Expr ')' | '(' Expr ',' Expr ')'
             | '[]' | '[' Expr (',' Expr)* ']'
             | '[' NAME '<-' Expr '|' CmpOp Expr ']'

v0.6 additions:
  FLOAT literals (e.g. 3.14, 0.05)
  math builtins: sin cos sqrt floor ceil round abs

v0.7 additions:
  CHAR literals: 'x'  (single-quoted, stored as Python str of length 1)
  get  lst n   — O(1) list index:  get lst 2  (replaces head (drop n lst))
  ord  c       — char -> int:      ord 'A' = 65
  chr  n       — int  -> char:     chr 65  = 'A'

v0.8 additions:
  len  arr     — list length:      len arr
  array n v    — fill list:        array 3 0  = [0, 0, 0]
  aset arr i v — functional set:   aset arr 1 9  (returns new list)
"""

import re
from typing import Any

# ── Tokenizer ──────────────────────────────────────────────────────────────────

_KEYWORDS = {"def", "match", "let", "in", "if", "then", "else", "True", "False"}

_TOKEN_RE = re.compile(r"""
    --[^\n]*           |   # line comment — discarded
    \[\]               |   # empty list token
    <-                 |   # comprehension arrow
    ::                 |   # cons operator
    \+\+               |   # append operator
    <=  | >=  | ==     |   # two-char comparisons
    [+\-*/%<>]         |   # single-char operators
    [()\|\[\],=]       |   # punctuation
    \d+\.\d+           |   # float literal  (must precede integer)
    -?\d+              |   # integer literal
    '[^']*'            |   # char literal  'x'
    [A-Za-z_][A-Za-z_0-9']*  # identifier / keyword
""", re.VERBOSE)


def _tokenize(src: str) -> list:
    return [t for t in _TOKEN_RE.findall(src) if not t.startswith("--")]


# ── Token stream ───────────────────────────────────────────────────────────────

class _TS:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self, offset=0):
        p = self.pos + offset
        return self.tokens[p] if p < len(self.tokens) else None

    def eat(self):
        t = self.tokens[self.pos]; self.pos += 1; return t

    def expect(self, val):
        t = self.eat()
        if t != val:
            ctx = self.tokens[max(0, self.pos-4):self.pos+3]
            raise SyntaxError(f"Expected {val!r} got {t!r}, context={ctx}")
        return t

    def at(self, val):   return self.peek() == val
    def eof(self):       return self.pos >= len(self.tokens)


# ── Wildcard counter ───────────────────────────────────────────────────────────

_wc = [0]
def _fresh():
    _wc[0] += 1; return f"_w{_wc[0]}"


# ── Pattern parsing ────────────────────────────────────────────────────────────

def _parse_simple_pat(ts):
    """Parse a simple name or _ for use inside tuple patterns."""
    tok = ts.eat()
    return _fresh() if tok == "_" else tok


def _parse_sub_pat(ts):
    """
    Sub-pattern inside a tuple: [], name::name, [name], name, _
    Returns a dict with tag for later desugaring.
    """
    tok = ts.peek()
    if tok == "[]":
        ts.eat()
        return {"tag": "nil"}
    if tok == "[":
        ts.eat()
        h = ts.eat()
        ts.expect("]")
        return {"tag": "cons", "x": h, "xs": _fresh()}
    if tok == "_":
        ts.eat()
        return {"tag": "wild", "n": _fresh()}
    name = ts.eat()
    if ts.at("::"):
        ts.eat()
        tail = _fresh() if ts.peek() == "_" else ts.eat()
        return {"tag": "cons", "x": name, "xs": tail}
    return {"tag": "var", "n": name}


def _parse_match_pat(ts):
    """
    Match patterns:
      []           nil
      [h]          singleton
      h::h2::t     nested cons
      h::t         cons
      (a, b)       tuple decomposition
      _            wildcard
      NAME         catch-all variable
    """
    tok = ts.peek()

    if tok == "[]":
        ts.eat(); return "nil"

    if tok == "[":
        ts.eat()
        h = ts.eat()
        ts.expect("]")
        return {"tag": "singleton", "x": h}

    if tok == "(":
        ts.eat()
        p1 = _parse_sub_pat(ts)
        ts.expect(",")
        p2 = _parse_sub_pat(ts)
        ts.expect(")")
        return {"tag": "tuple_match", "l": p1, "r": p2}

    if tok == "_":
        ts.eat(); return _fresh()

    # NAME  or  NAME :: ?
    name = ts.eat()
    if ts.at("::"):
        ts.eat()
        mid = _fresh() if ts.peek() == "_" else ts.eat()
        if ts.peek() == "_": ts.eat(); mid = _fresh()
        if ts.at("::"):
            ts.eat()
            tail = _fresh() if ts.peek() == "_" else ts.eat()
            if ts.peek() == "_": ts.eat(); tail = _fresh()
            return {"tag": "cons3", "x": name, "h2": mid, "t": tail}
        return {"tag": "cons", "x": name, "xs": mid}
    return name


def _build_case(pat, body):
    if pat == "nil":
        return {"pat": "nil", "body": body}

    # bare variable — catch-all using cons with unused bindings (after nil already tried)
    if isinstance(pat, str):
        x, xs = _fresh(), _fresh()
        return {"pat": {"tag": "cons", "x": x, "xs": xs}, "body": body}

    tag = pat["tag"]

    if tag == "cons":
        return {"pat": {"tag": "cons", "x": pat["x"], "xs": pat["xs"]}, "body": body}

    # [h] singleton — cons where tail must be nil; xs stored for nil-check
    if tag == "singleton":
        xs = _fresh()
        checked_body = {
            "op": "match", "e": {"op": "var", "n": xs},
            "cases": [{"pat": "nil", "body": body}]
        }
        return {"pat": {"tag": "cons", "x": pat["x"], "xs": xs}, "body": checked_body}

    # h::h2::t  — nested cons spine
    if tag == "cons3":
        xs = _fresh()
        inner = {
            "op": "let", "x": pat["h2"],
            "e":  {"op": "head", "e": {"op": "var", "n": xs}},
            "in": {
                "op": "let", "x": pat["t"],
                "e":  {"op": "tail", "e": {"op": "var", "n": xs}},
                "in": body
            }
        }
        return {"pat": {"tag": "cons", "x": pat["x"], "xs": xs}, "body": inner}

    # (a, b) tuple match — scrutinee is a pair
    if tag == "tuple_match":
        tmp = _fresh()
        wrapped = {
            "op": "let", "x": pat["l"],
            "e":  {"op": "fst", "e": {"op": "var", "n": tmp}},
            "in": {
                "op": "let", "x": pat["r"],
                "e":  {"op": "snd", "e": {"op": "var", "n": tmp}},
                "in": body
            }
        }
        return {"pat": {"tag": "cons", "x": tmp, "xs": _fresh()}, "body": wrapped}

    raise SyntaxError(f"Unknown pattern tag {tag!r}")


def _is_non_nil_pat(pat):
    """True if pattern can only match a non-nil list."""
    if isinstance(pat, dict):
        return pat.get("tag") in ("cons", "singleton", "cons3")
    return False  # str = catch-all, could match anything after nil tried


def _build_cases(raw_cases):
    """
    Build runtime cases list from raw (pat, body) pairs.
    Merges consecutive non-nil patterns (singleton, cons, cons3, wildcard)
    into a single cons case with a nested match on the tail, so that:
      | [h] = e1
      | h::h2::t = e2
      | _ = e3
    are all dispatched via one outer cons case with inner tail-dispatch.
    """
    result = []
    i = 0
    while i < len(raw_cases):
        pat, body = raw_cases[i]
        if pat == "nil":
            result.append({"pat": "nil", "body": body})
            i += 1
            continue

        # Collect all non-nil-exclusive patterns starting here
        # singleton/cons3/cons/wildcard all share one outer cons case
        # Group them into an inner match on the tail (xs)
        non_nil_group = []
        j = i
        while j < len(raw_cases):
            p2, b2 = raw_cases[j]
            if p2 == "nil":
                break
            non_nil_group.append((p2, b2))
            j += 1

        if len(non_nil_group) == 1:
            # No merging needed; use standard _build_case
            result.append(_build_case(pat, body))
            i += 1
            continue

        # Multiple non-nil cases: merge into one cons case with inner tail-match
        # Shared outer head variable; inner match on xs (tail)
        outer_h = _fresh()
        outer_xs = _fresh()
        inner_cases = []

        for p, b in non_nil_group:
            if isinstance(p, str):
                # bare name catch-all (from wildcard _ or named catch-all)
                # If this was a real named variable (not a fresh wildcard), bind it
                # to the full list (head::tail) so body can use it.
                # For pure wildcard (name never appears in body), just use b directly.
                x2, xs2 = _fresh(), _fresh()
                # We can't tell if p is used in body here, so always bind it.
                # Reconstruct the list as outer_h :: outer_xs for the binding.
                if p.startswith("_w") and p[2:].isdigit():
                    # Auto-generated fresh name — wildcard, body doesn't use it
                    rebind = b
                else:
                    # User-named catch-all: bind to reconstructed list
                    rebind = {"op": "let", "x": p,
                              "e": {"op": "cons", "head": {"op": "var", "n": outer_h},
                                                  "tail": {"op": "var", "n": outer_xs}},
                              "in": b}
                inner_cases.append({"pat": "nil", "body": b})
                inner_cases.append({"pat": {"tag": "cons", "x": x2, "xs": xs2}, "body": rebind})
                break  # catch-all, no more cases needed after this
            tag = p["tag"] if isinstance(p, dict) else None
            if tag == "singleton":
                # [h] = b  →  inner: | [] = let h=outer_h in b
                h_bind = {"op": "let", "x": p["x"], "e": {"op": "var", "n": outer_h}, "in": b}
                inner_cases.append({"pat": "nil", "body": h_bind})
            elif tag == "cons":
                # h::xs = b  →  inner: bind h=outer_h; xs is already the outer_xs var from pat
                # But pat has its own x/xs names. Re-bind them from outer_h / outer_xs
                h_bind = {
                    "op": "let", "x": p["x"], "e": {"op": "var", "n": outer_h},
                    "in": {
                        "op": "let", "x": p["xs"], "e": {"op": "var", "n": outer_xs},
                        "in": b
                    }
                }
                # matches any tail: use fresh vars in inner cons case
                x2, xs2 = _fresh(), _fresh()
                inner_cases.append({"pat": {"tag": "cons", "x": x2, "xs": xs2}, "body": h_bind})
            elif tag == "cons3":
                # h::h2::t = b  →  inner: | h2_fresh::t_fresh =
                #   let h=outer_h; let h2=h2_fresh; let t=t_fresh in b
                h2v = _fresh(); tv = _fresh()
                full_bind = {
                    "op": "let", "x": p["x"], "e": {"op": "var", "n": outer_h},
                    "in": {
                        "op": "let", "x": p["h2"], "e": {"op": "var", "n": h2v},
                        "in": {
                            "op": "let", "x": p["t"], "e": {"op": "var", "n": tv},
                            "in": b
                        }
                    }
                }
                inner_cases.append({"pat": {"tag": "cons", "x": h2v, "xs": tv}, "body": full_bind})

        inner_match = {"op": "match", "e": {"op": "var", "n": outer_xs}, "cases": inner_cases}
        result.append({"pat": {"tag": "cons", "x": outer_h, "xs": outer_xs}, "body": inner_match})
        i = j  # skip all consumed non-nil cases

    return result


def _parse_let_pat(ts):
    """Pattern for let-bindings: NAME or (a, b)."""
    if ts.at("("):
        ts.eat()
        p1 = _parse_simple_pat(ts)
        ts.expect(",")
        p2 = _parse_simple_pat(ts)
        ts.expect(")")
        return {"tag": "tuple", "l": p1, "r": p2}
    tok = ts.eat()
    return _fresh() if tok == "_" else tok


def _build_let(pat, val, body):
    if isinstance(pat, str):
        return {"op": "let", "x": pat, "e": val, "in": body}
    if isinstance(pat, dict) and pat["tag"] == "tuple":
        tmp = _fresh()
        return {
            "op": "let", "x": tmp, "e": val,
            "in": {
                "op": "let", "x": pat["l"],
                "e":  {"op": "fst", "e": {"op": "var", "n": tmp}},
                "in": {
                    "op": "let", "x": pat["r"],
                    "e":  {"op": "snd", "e": {"op": "var", "n": tmp}},
                    "in": body
                }
            }
        }
    raise SyntaxError(f"Cannot build let from {pat!r}")


def _sub_pat_to_match_cases(sub_pat, rhs_name: str, body: dict) -> list:
    """
    Convert a sub_pat into runtime match cases for the given scrutinee variable.
    Returns a LIST of cases (wild/var return two cases: nil + cons).
    """
    tag = sub_pat["tag"]
    if tag == "nil":
        return [{"pat": "nil", "body": body}]
    if tag == "wild":
        x, xs = _fresh(), _fresh()
        return [
            {"pat": "nil", "body": body},
            {"pat": {"tag": "cons", "x": x, "xs": xs}, "body": body}
        ]
    if tag == "var":
        n = sub_pat["n"]
        new_body = {"op": "let", "x": n, "e": {"op": "var", "n": rhs_name}, "in": body}
        x, xs = _fresh(), _fresh()
        return [
            {"pat": "nil", "body": new_body},
            {"pat": {"tag": "cons", "x": x, "xs": xs}, "body": new_body}
        ]
    if tag == "cons":
        return [{"pat": {"tag": "cons", "x": sub_pat["x"], "xs": sub_pat["xs"]}, "body": body}]
    if tag == "singleton":
        xs = _fresh()
        checked = {"op": "match", "e": {"op": "var", "n": xs},
                   "cases": [{"pat": "nil", "body": body}]}
        return [{"pat": {"tag": "cons", "x": sub_pat["x"], "xs": xs}, "body": checked}]
    raise SyntaxError(f"Unknown sub-pat tag {tag!r}")


def _desugar_tuple_match(scrutinee: dict, raw_cases: list) -> dict:
    """
    Desugar match (a,b) | (p1, p2) = body ...
    into:
      let _tmp = (a, b) in
        match (fst _tmp)  -- outer dispatch on first element
        | nil  => match (snd _tmp) | ...
        | h::t => match (snd _tmp) | ...
    Strategy: group cases by their first sub-pattern shape, build nested matches.
    For the mergesort use-case (and general case), we build:
      outer match on fst_var, with cases that each do inner match on snd_var.
    """
    tmp  = _fresh()
    fst_var = _fresh()
    snd_var = _fresh()

    # For each case, check if first sub-pat is nil, wild/var, or cons
    # We need to build the outer match cases covering all (fst, snd) combos.
    # Simple approach: translate each raw case into a nested if-chain.
    # Build: let tmp=(a,b); let fst_v=fst(tmp); let snd_v=snd(tmp); if-chain
    fst_expr = {"op": "fst", "e": {"op": "var", "n": tmp}}
    snd_expr = {"op": "snd", "e": {"op": "var", "n": tmp}}

    # Build a nested match tree. We group outer by first sub-pat.
    # Because the runtime only dispatches nil|cons, we build:
    #   outer match on fst_var
    #   for nil-first cases: inner match on snd_var
    #   for cons-first cases: inner match on snd_var
    # We collect outer-nil and outer-cons cases separately.
    outer_nil_cases  = []   # cases where p1 is nil or wild
    outer_cons_cases = []   # cases where p1 is cons/var

    for p, b in raw_cases:
        p1, p2 = p["l"], p["r"]
        if p1["tag"] in ("nil",):
            outer_nil_cases.append((p1, p2, b))
        elif p1["tag"] == "wild":
            # wildcard fst matches BOTH nil and cons — add to both groups
            outer_nil_cases.append((p1, p2, b))
            outer_cons_cases.append((p1, p2, b))
        else:
            outer_cons_cases.append((p1, p2, b))

    def build_inner_match(cases_2, fst_sub_pat, fst_outer_var, fst_outer_xs):
        """Build match(snd_var) for cases sharing the same outer (fst) pattern."""
        inner_cases = []
        for _p1, p2, body in cases_2:
            rebind = body
            if fst_sub_pat["tag"] == "var":
                rebind = {"op": "let", "x": fst_sub_pat["n"],
                          "e": {"op": "var", "n": fst_outer_var},
                          "in": rebind}
            inner_cases.extend(_sub_pat_to_match_cases(p2, snd_var, rebind))
        return {"op": "match", "e": {"op": "var", "n": snd_var}, "cases": inner_cases}

    # Build outer match on fst_var
    outer_cases = []
    if outer_nil_cases:
        inner = build_inner_match(outer_nil_cases, outer_nil_cases[0][0], fst_var, _fresh())
        outer_cases.append({"pat": "nil", "body": inner})
    if outer_cons_cases:
        # Use the first CONS-type p1 to get the named vars; fall back to fresh
        cons_p1 = next((p1 for p1, _, _ in outer_cons_cases if p1["tag"] == "cons"), None)
        if cons_p1:
            outer_x, outer_xs = cons_p1["x"], cons_p1["xs"]
        else:
            outer_x, outer_xs = _fresh(), _fresh()
        # For building inner match, pass a dummy "cons" fst_sub_pat using actual outer vars
        fst_for_inner = {"tag": "cons", "x": outer_x, "xs": outer_xs}
        inner = build_inner_match(outer_cons_cases, fst_for_inner, outer_x, outer_xs)
        outer_cases.append({"pat": {"tag": "cons", "x": outer_x, "xs": outer_xs},
                            "body": inner})

    outer_match = {"op": "match", "e": {"op": "var", "n": fst_var}, "cases": outer_cases}

    return {
        "op": "let", "x": tmp, "e": scrutinee,
        "in": {
            "op": "let", "x": fst_var, "e": fst_expr,
            "in": {
                "op": "let", "x": snd_var, "e": snd_expr,
                "in": outer_match
            }
        }
    }


# ── Expression parser ──────────────────────────────────────────────────────────

def _parse_expr(ts):
    tok = ts.peek()

    if tok == "let":
        ts.eat()
        pat  = _parse_let_pat(ts)
        ts.expect("=")
        val  = _parse_expr(ts)
        ts.expect("in")
        body = _parse_expr(ts)
        return _build_let(pat, val, body)

    if tok == "if":
        ts.eat()
        cond = _parse_cmp(ts)
        ts.expect("then")
        then = _parse_expr(ts)
        ts.expect("else")
        els  = _parse_expr(ts)
        return {"op": "if", "cond": cond, "then": then, "else_": els}

    if tok == "match":
        ts.eat()
        scrutinee = _parse_cmp(ts)
        raw_cases = []
        while ts.at("|"):
            ts.eat()
            pat  = _parse_match_pat(ts)
            ts.expect("=")
            body = _parse_expr(ts)
            raw_cases.append((pat, body))
        if not raw_cases:
            raise SyntaxError("match with no cases")
        # Detect tuple-scrutinee match: scrutinee is a pair and all pats are tuple_match
        if (isinstance(scrutinee, dict) and scrutinee["op"] == "pair" and
                all(isinstance(p, dict) and p.get("tag") == "tuple_match"
                    for p, _ in raw_cases)):
            return _desugar_tuple_match(scrutinee, raw_cases)
        return {"op": "match", "e": scrutinee,
                "cases": _build_cases(raw_cases)}

    return _parse_cons(ts)


_STOP = {"|", "=", ",", ")", "]", "then", "else", "in", "def", "++", "::",
         "+", "-", "*", "/", "%", "==", "<=", ">=", "<", ">", "<-"}

def _is_atom_start(tok):
    if tok is None:                         return False
    if tok in _KEYWORDS:                    return False
    if tok in _STOP:                        return False
    if re.fullmatch(r"\d+\.\d+", tok):     return True   # float
    if re.fullmatch(r"-?\d+", tok):        return True   # integer
    if len(tok) == 3 and tok[0] == tok[2] == "'":  return True  # char 'x'
    if tok in ("[]", "(", "["):             return True
    if tok[0].isalpha() or tok[0] == "_":  return True
    return False


def _parse_cons(ts):
    left = _parse_append(ts)
    if ts.at("::"):
        ts.eat()
        right = _parse_cons(ts)
        return {"op": "cons", "head": left, "tail": right}
    return left


def _parse_append(ts):
    left = _parse_cmp(ts)
    if ts.at("++"):
        ts.eat()
        right = _parse_append(ts)
        return {"op": "append", "l": left, "r": right}
    return left


_CMP = {"==": "eq", "<=": "le", ">=": "ge", "<": "lt", ">": "gt"}

def _parse_cmp(ts):
    left = _parse_add(ts)
    if ts.peek() in _CMP:
        op = ts.eat()
        right = _parse_add(ts)
        return {"op": _CMP[op], "l": left, "r": right}
    return left


def _parse_add(ts):
    left = _parse_mul(ts)
    while ts.peek() in ("+", "-"):
        op = ts.eat()
        right = _parse_mul(ts)
        left = {"op": "add" if op == "+" else "sub", "l": left, "r": right}
    return left


def _parse_mul(ts):
    left = _parse_unary(ts)
    while ts.peek() in ("*", "/", "%"):
        op_tok = ts.eat()
        right = _parse_unary(ts)
        node_op = {"*": "mul", "/": "div", "%": "mod"}[op_tok]
        left = {"op": node_op, "l": left, "r": right}
    return left


def _parse_unary(ts):
    if ts.at("-"):
        ts.eat()
        e = _parse_apply(ts)
        return {"op": "neg", "e": e}
    return _parse_apply(ts)


_BUILTIN_UNARY = {"head", "tail", "fst", "snd", "not",
                   "sin", "cos", "sqrt", "floor", "ceil", "round", "abs",
                   "ord", "chr", "len", "io_key"}
_BUILTIN_BINARY = {"take", "drop", "append", "filter", "get", "array", "aset"}


def _parse_apply(ts):
    func = _parse_atom(ts)
    args = []
    while _is_atom_start(ts.peek()):
        args.append(_parse_atom(ts))
    if args:
        if func["op"] == "var":
            name = func["n"]
            # Builtin unary ops: head x → {"op":"head","e":x}
            if name in _BUILTIN_UNARY and len(args) == 1:
                return {"op": name, "e": args[0]}
            # Builtin binary take/drop: take n e  /  get e n
            if name in ("take", "drop") and len(args) == 2:
                return {"op": name, "n": args[0], "e": args[1]}
            if name == "get" and len(args) == 2:
                return {"op": "get", "e": args[0], "n": args[1]}
            # array n v — fill; aset arr i v — functional update (v0.8)
            if name == "array" and len(args) == 2:
                return {"op": "array", "n": args[0], "v": args[1]}
            if name == "aset" and len(args) == 3:
                return {"op": "aset", "e": args[0], "n": args[1], "v": args[2]}
            # io_print frame token — linear I/O print (v0.9)
            if name == "io_print" and len(args) == 2:
                return {"op": "io_print", "l": args[0], "r": args[1]}
            # io_sound sid token — linear I/O sound event (v0.12)
            if name == "io_sound" and len(args) == 2:
                return {"op": "io_sound", "l": args[0], "r": args[1]}
            return {"op": "call", "fn": name, "a": args}
        raise SyntaxError(f"Application of non-name {func!r}")
    return func


def _parse_atom(ts):
    tok = ts.peek()

    # Float literal (must precede integer)
    if tok is not None and re.fullmatch(r"\d+\.\d+", tok):
        ts.eat(); return {"op": "float", "v": float(tok)}

    if tok is not None and re.fullmatch(r"-?\d+", tok):
        ts.eat(); return {"op": "int", "v": int(tok)}

    # Char literal 'x'
    if tok is not None and len(tok) == 3 and tok[0] == tok[2] == "'":
        ts.eat(); return {"op": "char", "v": tok[1]}

    if tok == "True":  ts.eat(); return {"op": "bool", "v": True}
    if tok == "False": ts.eat(); return {"op": "bool", "v": False}

    if tok == "[]":
        ts.eat(); return {"op": "nil"}

    if tok == "[":
        ts.eat()
        # comprehension: [name <- list | elem op pivot]
        # peek(1) checks if token after first name is <-
        if ts.peek(1) == "<-":
            elem = ts.eat()         # element variable name (e.g. x)
            ts.expect("<-")
            lst = _parse_cmp(ts)
            ts.expect("|")
            # parse:  elem_var op pivot  — elem_var is the same as or different from elem
            # We just need op and pivot; skip the lhs variable
            _lhs = ts.eat()         # consume the element variable in the predicate (e.g. x)
            pred_tok = ts.eat()     # the comparison operator
            pred_map = {"<=": "<=", ">=": ">=", "<": "<", ">": ">", "==": "==", "=": "=="}
            pred = pred_map.get(pred_tok, pred_tok)
            pivot = _parse_add(ts)
            ts.expect("]")
            return {"op": "filter", "pred": pred, "pivot": pivot, "list": lst}
        # list literal
        if ts.at("]"):
            ts.eat(); return {"op": "nil"}
        elements = [_parse_expr(ts)]
        while ts.at(","):
            ts.eat(); elements.append(_parse_expr(ts))
        ts.expect("]")
        result = {"op": "nil"}
        for e in reversed(elements):
            result = {"op": "cons", "head": e, "tail": result}
        return result

    if tok == "(":
        ts.eat()
        if ts.at(")"):
            ts.eat(); return {"op": "nil"}
        e = _parse_expr(ts)
        if ts.at(","):
            ts.eat()
            e2 = _parse_expr(ts)
            ts.expect(")")
            return {"op": "pair", "l": e, "r": e2}
        ts.expect(")")
        return e

    if tok and (tok[0].isalpha() or tok[0] == "_") and tok not in _STOP and tok not in _KEYWORDS:
        ts.eat(); return {"op": "var", "n": tok}

    raise SyntaxError(f"Unexpected atom token: {tok!r}  (context: {ts.tokens[ts.pos:ts.pos+6]})")


# ── Def and program ────────────────────────────────────────────────────────────

def _parse_def(ts):
    ts.expect("def")
    name = ts.eat()
    params = []
    while not ts.at("=") and not ts.eof():
        params.append(ts.eat())
    ts.expect("=")
    body = _parse_expr(ts)
    return {"name": name, "params": params, "body": body}


def parse_program(src: str) -> dict:
    """Parse NELA v0.4 source into a program dict."""
    _wc[0] = 0
    ts = _TS(_tokenize(src))
    defs = []
    while not ts.eof():
        if ts.at("def"):
            defs.append(_parse_def(ts))
        else:
            ts.eat()
    if not defs:
        raise ValueError("No 'def' forms found")
    return {"nela_version": "0.4", "program": defs[0]["name"], "defs": defs}


def parse_file(path: str) -> dict:
    with open(path) as f:
        return parse_program(f.read())
