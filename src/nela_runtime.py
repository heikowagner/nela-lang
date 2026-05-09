"""
NELA Runtime v0.9 — surface language interpreter.

The NELA surface language is ML/Haskell-like syntax (.nela files).
nela_parser.py converts .nela source text into the dict AST evaluated here.
Interaction nets are the formal semantic foundation (compiler backend),
not the surface representation.

Supported ops:
  var, int, float, char, bool, nil, cons, match, call, let, if,
  pair, fst, snd, head, tail, take, drop, get, len, array, aset,
  add, sub, mul, div, mod, neg,
  sin, cos, sqrt, floor, ceil, round, abs, ord, chr,
  eq, lt, le, gt, ge, and, or, not,
  filter, append,
    io_key, io_print, io_sound
"""

import json, math, os, sys, time
from typing import Any
from nela_parser import parse_program, parse_file


# ── IOToken — linear I/O resource (v0.9) ───────────────────────────────────

class IOToken:
    """Linear I/O token threaded through NELA-S I/O operations.

    By convention (enforced by the future linear type checker), each
    io_key / io_print call consumes this token and returns a fresh one.
    The Python harness creates the initial token; NELA-S holds it thereafter.
    """
    def __init__(self, read_key, print_frame, play_sound=None):
        self.read_key    = read_key      # () -> str (single char)
        self.print_frame = print_frame   # (list[list[int]]) -> None
        self.play_sound  = play_sound    # (int) -> None

    def _fresh(self):
        """Return a logically fresh token (linearity by convention)."""
        return IOToken(self.read_key, self.print_frame, self.play_sound)


# ── Interpreter ────────────────────────────────────────────────────────────────

def eval_expr(expr: dict, env: dict, defs: dict) -> Any:
    op = expr["op"]

    if op == "var":
        return env[expr["n"]]

    if op == "int":
        return expr["v"]

    if op == "float":
        return expr["v"]

    if op == "char":
        return expr["v"]

    if op == "bool":
        return expr["v"]

    if op == "nil":
        return []

    if op == "cons":
        h = eval_expr(expr["head"], env, defs)
        t = eval_expr(expr["tail"], env, defs)
        return [h] + t

    if op == "match":
        scrutinee = eval_expr(expr["e"], env, defs)
        for case in expr["cases"]:
            pat = case["pat"]
            if pat == "nil" and scrutinee == []:
                return eval_expr(case["body"], env, defs)
            if isinstance(pat, dict) and pat.get("tag") == "cons" and scrutinee != []:
                new_env = {**env, pat["x"]: scrutinee[0], pat["xs"]: scrutinee[1:]}
                return eval_expr(case["body"], new_env, defs)
        raise ValueError(f"Non-exhaustive match on {scrutinee!r}")

    if op == "call":
        fn_def = defs[expr["fn"]]
        args   = [eval_expr(a, env, defs) for a in expr["a"]]
        fn_env = dict(zip(fn_def["params"], args))
        return eval_expr(fn_def["body"], fn_env, defs)

    if op == "let":
        new_env = {**env, expr["x"]: eval_expr(expr["e"], env, defs)}
        return eval_expr(expr["in"], new_env, defs)

    if op == "filter":
        pivot = eval_expr(expr["pivot"], env, defs)
        lst   = eval_expr(expr["list"], env, defs)
        pred  = expr["pred"]
        if pred == "<=": return [x for x in lst if x <= pivot]
        if pred == ">":  return [x for x in lst if x > pivot]
        if pred == "<":  return [x for x in lst if x < pivot]
        if pred == ">=": return [x for x in lst if x >= pivot]
        if pred == "==": return [x for x in lst if x == pivot]
        raise ValueError(f"Unknown predicate {pred!r}")

    if op == "append":
        l = eval_expr(expr["l"], env, defs)
        r = eval_expr(expr["r"], env, defs)
        return l + r

    if op == "add":
        return eval_expr(expr["l"], env, defs) + eval_expr(expr["r"], env, defs)

    if op == "sub":
        return eval_expr(expr["l"], env, defs) - eval_expr(expr["r"], env, defs)

    if op == "mul":
        return eval_expr(expr["l"], env, defs) * eval_expr(expr["r"], env, defs)

    if op == "div":
        return eval_expr(expr["l"], env, defs) // eval_expr(expr["r"], env, defs)

    if op == "mod":
        return eval_expr(expr["l"], env, defs) % eval_expr(expr["r"], env, defs)

    if op == "neg":
        return -eval_expr(expr["e"], env, defs)

    # ── Maths builtins (v0.6) ─────────────────────────────────────────────
    if op == "sin":   return math.sin(eval_expr(expr["e"], env, defs))
    if op == "cos":   return math.cos(eval_expr(expr["e"], env, defs))
    if op == "sqrt":  return math.sqrt(eval_expr(expr["e"], env, defs))
    if op == "floor": return math.floor(eval_expr(expr["e"], env, defs))
    if op == "ceil":  return math.ceil(eval_expr(expr["e"], env, defs))
    if op == "round": return round(eval_expr(expr["e"], env, defs))
    if op == "abs":   return abs(eval_expr(expr["e"], env, defs))

    # ── Char builtins (v0.7) ──────────────────────────────────────────────
    if op == "ord":   return ord(eval_expr(expr["e"], env, defs))
    if op == "chr":   return chr(eval_expr(expr["e"], env, defs))

    if op == "eq":
        return eval_expr(expr["l"], env, defs) == eval_expr(expr["r"], env, defs)

    if op == "lt":
        return eval_expr(expr["l"], env, defs) < eval_expr(expr["r"], env, defs)

    if op == "le":
        return eval_expr(expr["l"], env, defs) <= eval_expr(expr["r"], env, defs)

    if op == "gt":
        return eval_expr(expr["l"], env, defs) > eval_expr(expr["r"], env, defs)

    if op == "ge":
        return eval_expr(expr["l"], env, defs) >= eval_expr(expr["r"], env, defs)

    # Pair(A,B): represented as a 2-tuple
    if op == "pair":
        return (eval_expr(expr["l"], env, defs),
                eval_expr(expr["r"], env, defs))

    if op == "fst":
        return eval_expr(expr["e"], env, defs)[0]

    if op == "snd":
        return eval_expr(expr["e"], env, defs)[1]

    if op == "len":
        return len(eval_expr(expr["e"], env, defs))

    if op == "head":
        lst = eval_expr(expr["e"], env, defs)
        if not lst:
            raise ValueError("head of empty list")
        return lst[0]

    if op == "tail":
        lst = eval_expr(expr["e"], env, defs)
        if not lst:
            raise ValueError("tail of empty list")
        return lst[1:]

    if op == "take":
        n   = eval_expr(expr["n"], env, defs)
        lst = eval_expr(expr["e"], env, defs)
        return lst[:n]

    if op == "drop":
        n   = eval_expr(expr["n"], env, defs)
        lst = eval_expr(expr["e"], env, defs)
        return lst[n:]

    if op == "get":
        lst = eval_expr(expr["e"], env, defs)
        n   = int(eval_expr(expr["n"], env, defs))
        return lst[n]

    # ── Array builtins (v0.8) ──────────────────────────────────────────────
    if op == "array":
        n = int(eval_expr(expr["n"], env, defs))
        v = eval_expr(expr["v"], env, defs)
        return [v] * n

    if op == "aset":
        lst = list(eval_expr(expr["e"], env, defs))   # copy
        i   = int(eval_expr(expr["n"], env, defs))
        v   = eval_expr(expr["v"], env, defs)
        lst[i] = v
        return lst

    # ── IOToken builtins (v0.9) ─────────────────────────────────────────────────────
    # io_key token  →  (char, token')   — linear: consumes token
    if op == "io_key":
        token = eval_expr(expr["e"], env, defs)
        ch    = token.read_key()
        return (ch, token._fresh())

    # io_print frame token  →  token'   — linear: consumes token
    if op == "io_print":
        frame = eval_expr(expr["l"], env, defs)
        token = eval_expr(expr["r"], env, defs)
        token.print_frame(frame)
        return token._fresh()

    # io_sound sound token  →  token'   — linear: consumes token
    # sound is runtime payload defined by NELA-S (e.g. [freq_hz, dur_ms, volume])
    if op == "io_sound":
        sound = eval_expr(expr["l"], env, defs)
        token = eval_expr(expr["r"], env, defs)
        play_sound = getattr(token, "play_sound", None)
        if play_sound is not None:
            play_sound(sound)
        return token._fresh()

    if op == "and":
        return eval_expr(expr["l"], env, defs) and eval_expr(expr["r"], env, defs)

    if op == "or":
        return eval_expr(expr["l"], env, defs) or eval_expr(expr["r"], env, defs)

    if op == "not":
        return not eval_expr(expr["e"], env, defs)

    if op == "if":
        cond = eval_expr(expr["cond"], env, defs)
        return eval_expr(expr["then"] if cond else expr["else_"], env, defs)

    raise ValueError(f"Unknown op: {op!r}")


def load_and_run(path: str, fn_name: str, args: list) -> Any:
    with open(path) as f:
        prog = json.load(f)
    defs = {d["name"]: d for d in prog["defs"]}
    call_expr = {"op": "call", "fn": fn_name, "a": [{"op": "int", "v": v} for v in args]}
    # for list input, build a list literal
    return None  # handled below


def run_program(prog: dict, fn_name: str, *arg_values: Any) -> Any:
    defs = {d["name"]: d for d in prog["defs"]}
    fn_def = defs[fn_name]
    fn_env = dict(zip(fn_def["params"], arg_values))
    return eval_expr(fn_def["body"], fn_env, defs)


# ── Reference implementations ─────────────────────────────────────────────────

def python_quicksort(lst: list) -> list:
    if not lst:
        return []
    pivot, *rest = lst
    return python_quicksort([x for x in rest if x <= pivot]) + \
           [pivot] + \
           python_quicksort([x for x in rest if x > pivot])


# ── Tests ──────────────────────────────────────────────────────────────────────

_QS_SOURCE = """\
def qs lst =
  match lst
  | []    = []
  | h::t  = qs [x <- t | x <= h] ++ [h] ++ qs [x <- t | x > h]
"""

NELA_QS_PROGRAM = parse_program(_QS_SOURCE)


def run_test(prog: dict, fn: str, ref_fn, case: list, label: str) -> bool:
    print(f"\n{'='*55}")
    print(f"[{label}] Input: {case}")

    t0 = time.perf_counter()
    py_result = ref_fn(case[:])
    py_time   = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    nela_result = run_program(prog, fn, case[:])
    nela_time   = (time.perf_counter() - t0) * 1000

    ok = py_result == nela_result
    print(f"  Reference: {py_result}  ({py_time:.4f} ms)")
    print(f"  NELA:      {nela_result}  ({nela_time:.4f} ms)")
    print(f"  Match:     {'PASS' if ok else 'FAIL'}")
    return ok


# ── Mergesort NELA program ─────────────────────────────────────────────────────

def _load(path: str) -> dict:
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full = os.path.join(base, "examples", path)
    if path.endswith(".nela"):
        return parse_file(full)
    with open(full) as f:
        return json.load(f)


def python_mergesort(lst: list) -> list:
    if len(lst) <= 1:
        return lst
    mid = len(lst) // 2
    def merge(a, b):
        out = []
        i = j = 0
        while i < len(a) and j < len(b):
            if a[i] <= b[j]:
                out.append(a[i]); i += 1
            else:
                out.append(b[j]); j += 1
        return out + a[i:] + b[j:]
    return merge(python_mergesort(lst[:mid]), python_mergesort(lst[mid:]))


# ── Stack VM reference implementation ─────────────────────────────────────────

def python_vm_eval(program: list) -> int:
    """Reference stack-based VM: same semantics as the NELA stack_vm program."""
    stack: list = []
    for instr in program:
        opc = instr[0]
        if opc == 0:    # PUSH
            stack.append(instr[1])
        elif opc == 1:  # ADD
            a = stack.pop(); b = stack.pop(); stack.append(a + b)
        elif opc == 2:  # SUB: b - a  (b was pushed before a)
            a = stack.pop(); b = stack.pop(); stack.append(b - a)
        elif opc == 3:  # MUL
            a = stack.pop(); b = stack.pop(); stack.append(a * b)
        elif opc == 4:  # NEG
            a = stack.pop(); stack.append(-a)
        elif opc == 5:  # DUP
            stack.append(stack[-1])
        elif opc == 6:  # SWAP: pop a (top), pop b; push a then b (b ends on top)
            a = stack.pop(); b = stack.pop()
            stack.append(a); stack.append(b)
    return stack[0] if stack else 0


# ── VM test helper ─────────────────────────────────────────────────────────────

def run_vm_test(prog: dict, program: list, label: str) -> bool:
    print(f"\n{'='*55}")
    print(f"[vm: {label}]  program: {program}")

    t0 = time.perf_counter()
    py_result = python_vm_eval(program)
    py_time   = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    nela_result = run_program(prog, "vm_eval", program)
    nela_time   = (time.perf_counter() - t0) * 1000

    ok = py_result == nela_result
    print(f"  Reference: {py_result}  ({py_time:.4f} ms)")
    print(f"  NELA:      {nela_result}  ({nela_time:.4f} ms)")
    print(f"  Match:     {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _load(path):
        return parse_file(os.path.join(base, "examples", path))

    def _load_wolf(path):
        return parse_file(os.path.join(base, "examples", "wolf", path))

    sort_cases = [
        [],
        [1],
        [3, 1, 2],
        [5, 3, 8, 1, 9, 2, 7, 4, 6],
        [1, 2, 3, 4, 5],
        [5, 4, 3, 2, 1],
        [3, 3, 3, 1, 1],
        [42],
        list(range(20, 0, -1)),
    ]

    print("\n" + "#"*55)
    print("# QUICKSORT")
    print("#"*55)
    qs_pass = all(
        run_test(NELA_QS_PROGRAM, "qs", python_quicksort, c, "qs")
        for c in sort_cases
    )

    print("\n" + "#"*55)
    print("# MERGESORT")
    print("#"*55)
    ms_prog = _load("mergesort.nela")
    ms_pass = all(
        run_test(ms_prog, "mergesort", python_mergesort, c, "ms")
        for c in sort_cases
    )

    # ── Stack VM tests ─────────────────────────────────────────────────────────
    # Each case: (label, program)
    # Instructions: PUSH=[0,n]  ADD=[1]  SUB=[2]  MUL=[3]  NEG=[4]  DUP=[5]  SWAP=[6]
    vm_cases = [
        ("push 5",              [[0, 5]]),
        ("3 + 4",               [[0, 3], [0, 4], [1]]),
        ("10 - 3",              [[0, 10], [0, 3], [2]]),
        ("6 * 7",               [[0, 6], [0, 7], [3]]),
        ("neg 3",               [[0, 3], [4]]),
        ("4^2 via dup*mul",     [[0, 4], [5], [3]]),
        ("(2+3)*4",             [[0, 2], [0, 3], [1], [0, 4], [3]]),
        ("10 - (2*2)",          [[0, 10], [0, 2], [5], [3], [2]]),
        ("swap then sub",       [[0, 3], [0, 8], [6], [2]]),
        ("1+2+3",               [[0, 1], [0, 2], [0, 3], [1], [1]]),
        ("neg neg 7",           [[0, 7], [4], [4]]),
        ("(3+4)*(5-2)",         [[0, 3], [0, 4], [1], [0, 5], [0, 2], [2], [3]]),
    ]

    print("\n" + "#"*55)
    print("# STACK VM")
    print("#"*55)
    vm_prog = _load("stack_vm.nela")
    vm_pass = all(
        run_vm_test(vm_prog, prog, label)
        for label, prog in vm_cases
    )

    # ── Wolf Grid tests ────────────────────────────────────────────────────────
    # 5x5 map:  1=wall  0=passable
    #   1 1 1 1 1
    #   1 0 0 0 1
    #   1 0 1 0 1   <- centre wall at (2,2)
    #   1 0 0 0 1
    #   1 1 1 1 1
    WOLF_MAP = [
        1,1,1,1,1,
        1,0,0,0,1,
        1,0,1,0,1,
        1,0,0,0,1,
        1,1,1,1,1,
    ]
    W = 5

    def py_map_get(m, idx):           return m[idx]
    def py_is_wall(m, x, y, w):       return m[x + y * w]
    def py_cast_ray(m, x, y, dx, dy, w):
        steps = 0
        while m[x + y * w] != 1:
            x += dx; y += dy; steps += 1
        return steps
    def py_wall_height(dist):         return 19200 if dist == 0 else 19200 // dist
    def py_scan_4(m, px, py, w):
        return [py_cast_ray(m, px, py, dx, dy, w)
                for dx, dy in [(1,0),(0,1),(-1,0),(0,-1)]]
    def py_reachable(m, sx, sy, gx, gy, w):
        from collections import deque
        visited = set(); q = deque([(sx, sy)])
        while q:
            x, y = q.popleft()
            if (x, y) == (gx, gy): return 1
            if (x, y) in visited or m[x + y * w] == 1: continue
            visited.add((x, y))
            for dx, dy in [(1,0),(0,1),(-1,0),(0,-1)]:
                q.append((x+dx, y+dy))
        return 0

    wolf_cases = [
        # (fn, py_fn, args, label)
        ("map_get",    lambda _: py_map_get(WOLF_MAP, 0),           [WOLF_MAP, 0],        "map_get idx=0 (corner wall)"),
        ("map_get",    lambda _: py_map_get(WOLF_MAP, 6),           [WOLF_MAP, 6],        "map_get idx=6 (open cell)"),
        ("is_wall",    lambda _: py_is_wall(WOLF_MAP, 0, 0, W),     [WOLF_MAP,0,0,W],     "is_wall (0,0)=wall"),
        ("is_wall",    lambda _: py_is_wall(WOLF_MAP, 1, 1, W),     [WOLF_MAP,1,1,W],     "is_wall (1,1)=open"),
        ("is_wall",    lambda _: py_is_wall(WOLF_MAP, 2, 2, W),     [WOLF_MAP,2,2,W],     "is_wall (2,2)=centre wall"),
        ("cast_ray",   lambda _: py_cast_ray(WOLF_MAP, 1,1, 1, 0,W),[WOLF_MAP,1,1,1,0,W], "ray right from (1,1)"),
        ("cast_ray",   lambda _: py_cast_ray(WOLF_MAP, 1,1, 0, 1,W),[WOLF_MAP,1,1,0,1,W], "ray down from (1,1)"),
        ("cast_ray",   lambda _: py_cast_ray(WOLF_MAP, 1,1,-1, 0,W),[WOLF_MAP,1,1,-1,0,W],"ray left from (1,1)"),
        ("cast_ray",   lambda _: py_cast_ray(WOLF_MAP, 1,1, 0,-1,W),[WOLF_MAP,1,1,0,-1,W],"ray up from (1,1)"),
        ("cast_ray",   lambda _: py_cast_ray(WOLF_MAP, 3,1, 1, 0,W),[WOLF_MAP,3,1,1,0,W], "ray right from (3,1) 1 step"),
        ("wall_height",lambda _: py_wall_height(3),                 [3],                  "wall_height dist=3"),
        ("wall_height",lambda _: py_wall_height(1),                 [1],                  "wall_height dist=1"),
        ("scan_4",     lambda _: py_scan_4(WOLF_MAP, 1,1, W),       [WOLF_MAP,1,1,W],     "scan_4 from (1,1)"),
        ("scan_4",     lambda _: py_scan_4(WOLF_MAP, 3,3, W),       [WOLF_MAP,3,3,W],     "scan_4 from (3,3)"),
        ("reachable",  lambda _: py_reachable(WOLF_MAP,1,1,3,3,W),  [WOLF_MAP,1,1,3,3,W], "reachable (1,1)->(3,3)"),
        ("reachable",  lambda _: 0,                                 [WOLF_MAP,1,1,2,2,W], "reachable (1,1)->(2,2) wall=0"),
        ("reachable",  lambda _: py_reachable(WOLF_MAP,1,1,1,3,W),  [WOLF_MAP,1,1,1,3,W], "reachable (1,1)->(1,3) open"),
    ]

    def run_wolf_test(prog, fn, py_fn, args, label):
        print(f"\n{'='*55}")
        print(f"[wolf: {label}]")
        py_result   = py_fn(None)
        nela_result = run_program(prog, fn, *args)
        ok = py_result == nela_result
        print(f"  Reference: {py_result}")
        print(f"  NELA:      {nela_result}")
        print(f"  Match:     {'PASS' if ok else 'FAIL'}")
        return ok

    print("\n" + "#"*55)
    print("# WOLF GRID")
    print("#"*55)
    wolf_prog = _load_wolf("wolf_grid.nela")
    wolf_pass = all(
        run_wolf_test(wolf_prog, fn, py_fn, args, label)
        for fn, py_fn, args, label in wolf_cases
    )

    # ── Wolf Game tests (v0.6 float + trig) ───────────────────────────────────
    import math as _math
    MAP8 = [
        1,1,1,1,1,1,1,1,
        1,0,0,0,0,0,0,1,
        1,0,1,1,0,1,0,1,
        1,0,1,0,0,0,0,1,
        1,0,0,0,1,0,0,1,
        1,0,1,0,0,1,0,1,
        1,0,0,0,0,0,0,1,
        1,1,1,1,1,1,1,1,
    ]
    W8 = 8

    def _wg_approx(got, ref):
        if isinstance(ref, float):
            return abs(got - ref) < 1e-9
        return got == ref

    wg_game_prog = _load_wolf("wolf_game.nela")
    wg_cases = [
        # (fn, args, ref_fn, label)
        ("deg_to_rad",  [0],   lambda: 0.0,                          "deg_to_rad(0)=0"),
        ("deg_to_rad",  [90],  lambda: _math.pi / 2,                 "deg_to_rad(90)=pi/2"),
        ("deg_to_rad",  [180], lambda: _math.pi,                     "deg_to_rad(180)=pi"),
        ("norm_angle",  [0],   lambda: 0,                            "norm_angle(0)"),
        ("norm_angle",  [370], lambda: 10,                           "norm_angle(370)=10"),
        ("norm_angle",  [-20], lambda: 340,                          "norm_angle(-20)=340"),
        ("norm_angle",  [720], lambda: 0,                            "norm_angle(720)=0"),
        ("is_wall",     [MAP8, 0.5, 0.5, W8], lambda: 1,             "is_wall(0.5,0.5)=wall"),
        ("is_wall",     [MAP8, 1.5, 1.5, W8], lambda: 0,             "is_wall(1.5,1.5)=open"),
        ("is_wall",     [MAP8, 2.5, 2.5, W8], lambda: 1,             "is_wall(2.5,2.5)=wall"),
        ("turn",        [[1.5, 1.5, 90], 5, ],  lambda: [1.5, 1.5, 95],  "turn +5 -> 95"),
        ("turn",        [[1.5, 1.5, 5], -10],   lambda: [1.5, 1.5, 355], "turn -10 wraps -> 355"),
        ("update",      [[1.5, 1.5, 90], 2, MAP8, W8], lambda: [1.5, 1.5, 85], "key=2 turn-left"),
        ("update",      [[1.5, 1.5, 90], 3, MAP8, W8], lambda: [1.5, 1.5, 95], "key=3 turn-right"),
    ]

    def run_wg_test(prog, fn, args, ref_fn, label):
        print(f"\n{'='*55}")
        print(f"[wg: {label}]")
        ref = ref_fn()
        got = run_program(prog, fn, *args)
        if isinstance(ref, list):
            ok = all(_wg_approx(g, r) for g, r in zip(got, ref)) and len(got) == len(ref)
        else:
            ok = _wg_approx(got, ref)
        print(f"  Reference: {ref}")
        print(f"  NELA:      {got}")
        print(f"  Match:     {'PASS' if ok else 'FAIL'}")
        return ok

    print("\n" + "#"*55)
    print("# WOLF GAME (v0.6 float trig)")
    print("#"*55)
    wg_pass = all(
        run_wg_test(wg_game_prog, fn, args, ref_fn, label)
        for fn, args, ref_fn, label in wg_cases
    )

    # ── v0.7 tests: get, char literals, ord, chr ──────────────────────────────
    _v7_src = """\
def first  lst   = get lst 0
def second lst   = get lst 1
def third  lst   = get lst 2

def char_eq a b  = if a == b then 1 else 0
def to_int  c    = ord c
def to_char n    = chr n
def is_upper c   = if ord c >= 65 then if ord c <= 90 then 1 else 0 else 0
def digit_val c  = ord c - ord '0'
"""
    _v7_prog = parse_program(_v7_src)

    def _run_v7(fn, *args):
        return run_program(_v7_prog, fn, *args)

    v7_cases = [
        # (label, got_fn, expected)
        ("get [10,20,30] 0 = 10",  lambda: _run_v7("first",  [10, 20, 30]),        10),
        ("get [10,20,30] 1 = 20",  lambda: _run_v7("second", [10, 20, 30]),        20),
        ("get [10,20,30] 2 = 30",  lambda: _run_v7("third",  [10, 20, 30]),        30),
        ("char_eq 'a' 'a' = 1",    lambda: _run_v7("char_eq", "a", "a"),           1),
        ("char_eq 'a' 'b' = 0",    lambda: _run_v7("char_eq", "a", "b"),           0),
        ("ord 'A' = 65",           lambda: _run_v7("to_int",  "A"),                65),
        ("ord '0' = 48",           lambda: _run_v7("to_int",  "0"),                48),
        ("chr 65 = 'A'",           lambda: _run_v7("to_char", 65),                 "A"),
        ("chr 48 = '0'",           lambda: _run_v7("to_char", 48),                 "0"),
        ("is_upper 'A' = 1",       lambda: _run_v7("is_upper", "A"),               1),
        ("is_upper 'z' = 0",       lambda: _run_v7("is_upper", "z"),               0),
        ("digit_val '5' = 5",      lambda: _run_v7("digit_val", "5"),              5),
        ("digit_val '0' = 0",      lambda: _run_v7("digit_val", "0"),              0),
        # get on the actual game map  (O(1) via builtin)
        ("map get[0]=1 (wall)",    lambda: run_program(wg_game_prog, "map_get", MAP8, 0),  1),
        ("map get[9]=0 (open)",    lambda: run_program(wg_game_prog, "map_get", MAP8, 9),  0),
    ]

    def run_v7_test(label, got_fn, expected):
        print(f"\n{'='*55}")
        print(f"[v7: {label}]")
        got = got_fn()
        ok  = got == expected
        print(f"  Expected:  {expected!r}")
        print(f"  NELA:      {got!r}")
        print(f"  Match:     {'PASS' if ok else 'FAIL'}")
        return ok

    print("\n" + "#"*55)
    print("# V0.7 (get / char / ord / chr)")
    print("#"*55)
    v7_pass = all(run_v7_test(lbl, fn, exp) for lbl, fn, exp in v7_cases)

    # ── v0.8 tests: array / aset / len / use_door ─────────────────────────────
    _v8_src = """\
def fill3z       = array 3 0
def fill4one     = array 4 1
def len3         = len (array 3 0)
def set_first  a = aset a 0 9
def set_last   a = aset a 2 9
def set_mid    a = aset a 1 9
def get_after  a = get (aset a 1 9) 1
"""
    _v8_prog = parse_program(_v8_src)

    def _run_v8(fn, *args):
        return run_program(_v8_prog, fn, *args)

    v8_cases = [
        ("array 3 0 = [0,0,0]",         lambda: _run_v8("fill3z"),            [0, 0, 0]),
        ("array 4 1 = [1,1,1,1]",        lambda: _run_v8("fill4one"),          [1, 1, 1, 1]),
        ("len (array 3 0) = 3",          lambda: _run_v8("len3"),              3),
        ("aset [1,2,3] 0 9 = [9,2,3]",   lambda: _run_v8("set_first", [1,2,3]),[9, 2, 3]),
        ("aset [1,2,3] 2 9 = [1,2,9]",   lambda: _run_v8("set_last",  [1,2,3]),[1, 2, 9]),
        ("aset [1,2,3] 1 9 = [1,9,3]",   lambda: _run_v8("set_mid",   [1,2,3]),[1, 9, 3]),
        ("get(aset arr 1 9) 1 = 9",       lambda: _run_v8("get_after", [1,2,3]), 9),
        # use_door: player at (1.5,1.5) facing west (270) → cell index 8 is wall
        ("use_door opens west wall",
         lambda: run_program(wg_game_prog, "use_door",
                             [1.5, 1.5, 270], MAP8, W8)[8],  0),
        # use_door facing east (90) → cell index 10 is open; map unchanged
        ("use_door on open cell noop",
         lambda: run_program(wg_game_prog, "use_door",
                             [1.5, 1.5, 90], MAP8, W8)[10], 0),
    ]

    def run_v8_test(label, got_fn, expected):
        print(f"\n{'='*55}")
        print(f"[v8: {label}]")
        got = got_fn()
        ok  = got == expected
        print(f"  Expected:  {expected!r}")
        print(f"  NELA:      {got!r}")
        print(f"  Match:     {'PASS' if ok else 'FAIL'}")
        return ok

    print("\n" + "#"*55)
    print("# V0.8 (array / aset / len / use_door)")
    print("#"*55)
    v8_pass = all(run_v8_test(lbl, fn, exp) for lbl, fn, exp in v8_cases)

    # ── v0.9 tests: IOToken linear I/O ────────────────────────────────────────
    # Mock IOToken replays a fixed key sequence then returns 'q' (quit).
    class _MockToken:
        def __init__(self, keys):
            self._keys   = list(keys) + ["q"]
            self._idx    = 0
            self.printed = []           # frames passed to print_frame
        def read_key(self):
            ch = self._keys[self._idx]; self._idx += 1; return ch
        def print_frame(self, frame):
            self.printed.append(frame)
        def _fresh(self):
            return self   # single shared mock token; linearity by convention

    _v9_src = """\
def io_key_char token =
  let p = io_key token in
  fst p

def io_key_tok token =
  let p = io_key token in
  snd p

def print_and_return frame token =
  io_print frame token
"""
    _v9_prog = parse_program(_v9_src)

    def _run_v9_key_char(keys):
        tok = _MockToken(keys)
        return run_program(_v9_prog, "io_key_char", tok)

    def _run_v9_print_count(frame, keys):
        tok = _MockToken(keys)
        run_program(_v9_prog, "print_and_return", frame, tok)
        return len(tok.printed)

    def _run_v9_game_loop(keys):
        tok = _MockToken(keys)
        run_program(wg_game_prog, "game_loop",
                    list(_INIT_STATE_V9), list(MAP8), W8, tok)
        return len(tok.printed)

    _INIT_STATE_V9 = [1.5, 1.5, 90]

    v9_cases = [
        ("io_key returns first char",
         lambda: _run_v9_key_char(["w"]),              "w"),
        ("io_key returns 'q'",
         lambda: _run_v9_key_char(["q"]),              "q"),
        ("io_print side-effect fires",
         lambda: _run_v9_print_count([[0, 1], [2, 3]], []),  1),
        ("game_loop immediate quit: 1 frame",
         lambda: _run_v9_game_loop([]),                1),
        ("game_loop fwd then quit: 2 frames",
         lambda: _run_v9_game_loop(["w"]),             2),
        ("game_loop turn-right then quit: 2 frames",
         lambda: _run_v9_game_loop(["d"]),             2),
        ("game_loop door then quit: 2 frames",
         lambda: _run_v9_game_loop(["e"]),             2),
    ]

    def run_v9_test(label, got_fn, expected):
        print(f"\n{'='*55}")
        print(f"[v9: {label}]")
        got = got_fn()
        ok  = got == expected
        print(f"  Expected:  {expected!r}")
        print(f"  NELA:      {got!r}")
        print(f"  Match:     {'PASS' if ok else 'FAIL'}")
        return ok

    print("\n" + "#"*55)
    print("# V0.9 (IOToken: io_key / io_print / game_loop)")
    print("#"*55)
    v9_pass = all(run_v9_test(lbl, fn, exp) for lbl, fn, exp in v9_cases)

    all_pass = qs_pass and ms_pass and vm_pass and wolf_pass and wg_pass and v7_pass and v8_pass and v9_pass
    print(f"\n{'='*55}")
    print(f"Quicksort:  {'PASS' if qs_pass else 'FAIL'}")
    print(f"Mergesort:  {'PASS' if ms_pass else 'FAIL'}")
    print(f"Stack VM:   {'PASS' if vm_pass else 'FAIL'}")
    print(f"Wolf Grid:  {'PASS' if wolf_pass else 'FAIL'}")
    print(f"Wolf Game:  {'PASS' if wg_pass else 'FAIL'}")
    print(f"V0.7:       {'PASS' if v7_pass else 'FAIL'}")
    print(f"V0.8:       {'PASS' if v8_pass else 'FAIL'}")
    print(f"V0.9:       {'PASS' if v9_pass else 'FAIL'}")
    print(f"Overall:    {'ALL TESTS PASSED' if all_pass else 'SOME TESTS FAILED'}")
    sys.exit(0 if all_pass else 1)


