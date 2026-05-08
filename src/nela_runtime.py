"""
NELA Runtime v0.3 — surface language interpreter.

The NELA surface language is S-expression syntax (.nela files).
nela_parser.py converts .nela source text into the dict AST evaluated here.
Interaction nets are the formal semantic foundation (compiler backend),
not the surface representation.

Supported ops:
  var, int, bool, nil, cons, match, call, let, if,
  pair, fst, snd, head, tail, take, drop,
  add, sub, mul, eq, lt, le, gt, ge, and, or, not,
  filter, append
"""

import json, sys, time
from typing import Any
from nela_parser import parse_program, parse_file


# ── Interpreter ────────────────────────────────────────────────────────────────

def eval_expr(expr: dict, env: dict, defs: dict) -> Any:
    op = expr["op"]

    if op == "var":
        return env[expr["n"]]

    if op == "int":
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

    all_pass = qs_pass and ms_pass and vm_pass
    print(f"\n{'='*55}")
    print(f"Quicksort:  {'PASS' if qs_pass else 'FAIL'}")
    print(f"Mergesort:  {'PASS' if ms_pass else 'FAIL'}")
    print(f"Stack VM:   {'PASS' if vm_pass else 'FAIL'}")
    print(f"Overall:    {'ALL TESTS PASSED' if all_pass else 'SOME TESTS FAILED'}")
    sys.exit(0 if all_pass else 1)
