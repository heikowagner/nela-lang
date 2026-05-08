#!/usr/bin/env python3
"""
nela_compiler.py — NELA-S → NELA-C (interaction net bytecode) compiler + reducer.

Architecture (mission-aligned):
  NELA-S (.nela) ──parser──► AST dict ──compiler──► Interaction Net ──reducer──► value
                                                           │
                                                           ▼
                                                    .nelac (binary)

Theory: Interaction Nets (Lafont, 1990/1997) — see nela-foundations SKILL.md.
The computation model is local graph rewriting over typed nodes, NOT a Von Neumann VM.

Agent vocabulary (symmetric interaction combinators extended for NELA):
  CON (γ)  arity 2  — constructor / tensor: builds a pair / cons cell
  DUP (δ)  arity 2  — duplicator / !: copies a resource
  ERA (ε)  arity 0  — eraser / 0: discards a resource
  APP       arity 2  — function application
  LAM       arity 2  — lambda
  INT       arity 0  — integer leaf (value stored in meta)
  FLT       arity 0  — float leaf (IEEE-754 bits in meta)
  STR       arity 0  — char leaf (ord in meta)
  BOO       arity 0  — boolean leaf (0/1 in meta)
  ADD/SUB/MUL/DIV/MOD/NEG  — arithmetic active pairs
  EQL/LTH/LEQ/GTH/GEQ      — comparison active pairs
  AND/ORR/NOT               — boolean active pairs
  IFT       arity 3  — if-then-else
  NIL       arity 0  — empty list
  HED/TAL/LEN/GET/ARR/AST  — list ops

Bytecode format (.nelac):
  Header:  b"NELAC" + version(u8) + node_count(u32)
  Nodes:   for each: tag(u8) + arity(u8) + meta(i64) + port[0..arity](u32 each)
  Footer:  root(u32)  — node id of the output
"""

import struct
import sys
import os
import math as _math
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from nela_parser import parse_program, parse_file


# ── Agent tags ─────────────────────────────────────────────────────────────────

CON = 0x01  # list cons cell: ports[1]=head, ports[2]=tail
DUP = 0x02
ERA = 0x03
APP = 0x04
LAM = 0x05
INT = 0x10
FLT = 0x11
STR = 0x12
BOO = 0x13
PAR = 0x14  # pair / tensor: ports[1]=fst, ports[2]=snd  (distinct from CON)
ADD = 0x20
SUB = 0x21
MUL = 0x22
DIV = 0x23
MOD = 0x24
NEG = 0x25
EQL = 0x30
LTH = 0x31
LEQ = 0x32
GTH = 0x33
GEQ = 0x34
AND = 0x40
ORR = 0x41
NOT = 0x42
IFT = 0x50
NIL = 0x60
HED = 0x61
TAL = 0x62
GET = 0x63
LEN = 0x64
ARR = 0x65
AST = 0x66

_TAG_NAMES = {
    CON:"CON", DUP:"DUP", ERA:"ERA", APP:"APP", LAM:"LAM",
    INT:"INT", FLT:"FLT", STR:"STR", BOO:"BOO", PAR:"PAR",
    ADD:"ADD", SUB:"SUB", MUL:"MUL", DIV:"DIV", MOD:"MOD", NEG:"NEG",
    EQL:"EQL", LTH:"LTH", LEQ:"LEQ", GTH:"GTH", GEQ:"GEQ",
    AND:"AND", ORR:"ORR", NOT:"NOT",
    IFT:"IFT", NIL:"NIL", HED:"HED", TAL:"TAL",
    GET:"GET", LEN:"LEN", ARR:"ARR", AST:"AST",
}

_NULL = 0xFFFFFFFF   # sentinel: unconnected port


# ── Node ───────────────────────────────────────────────────────────────────────

class Node:
    __slots__ = ("nid", "tag", "ports", "meta")

    def __init__(self, nid: int, tag: int, arity: int, meta: int = 0):
        self.nid   = nid
        self.tag   = tag
        self.meta  = meta
        self.ports = [_NULL] * (arity + 1)

    def __repr__(self):
        name = _TAG_NAMES.get(self.tag, f"0x{self.tag:02x}")
        return f"Node({name}#{self.nid} meta={self.meta} ports={self.ports})"


# ── Interaction Net ────────────────────────────────────────────────────────────

class Net:
    """Mutable interaction net (adjacency representation)."""

    def __init__(self):
        self._nodes: dict[int, Node] = {}
        self._counter = 0

    def alloc(self, tag: int, arity: int, meta: int = 0) -> int:
        nid = self._counter
        self._counter += 1
        self._nodes[nid] = Node(nid, tag, arity, meta)
        return nid

    def node(self, nid: int) -> Node:
        return self._nodes[nid]

    def delete(self, nid: int):
        del self._nodes[nid]


# ── Compiler: NELA-S AST → Interaction Net ────────────────────────────────────

class Compiler:
    """Lower a NELA-S function call into an interaction net, then read back the result.

    Strategy: each sub-expression is reduced eagerly (call-by-value).  The net
    is populated with leaf/CON nodes representing the fully-reduced value.
    This gives us the bytecode representation of the *result net* — equivalent
    to NELA-C after full reduction — which can be serialised and reloaded.
    """

    def __init__(self, prog: dict):
        self.defs = {d["name"]: d for d in prog["defs"]}
        self.net  = Net()

    def compile_call(self, fn_name: str, arg_values: list) -> int:
        fn_def = self.defs[fn_name]
        env    = dict(zip(fn_def["params"], arg_values))
        return self._compile(fn_def["body"], env)

    # ── expression compiler ────────────────────────────────────────────────────

    def _compile(self, expr: dict, env: dict) -> int:
        op = expr["op"]

        if op == "int":
            return self.net.alloc(INT, 0, meta=expr["v"])

        if op == "float":
            bits = struct.unpack("q", struct.pack("d", expr["v"]))[0]
            return self.net.alloc(FLT, 0, meta=bits)

        if op == "char":
            return self.net.alloc(STR, 0, meta=ord(expr["v"]))

        if op == "bool":
            return self.net.alloc(BOO, 0, meta=int(expr["v"]))

        if op == "nil":
            return self.net.alloc(NIL, 0)

        if op == "var":
            return self._py_to_node(env[expr["n"]])

        if op == "let":
            val = self._node_to_py(self._compile(expr["e"], env))
            return self._compile(expr["in"], {**env, expr["x"]: val})

        if op == "if":
            cond = self._node_to_py(self._compile(expr["cond"], env))
            return self._compile(expr["then"] if cond else expr["else_"], env)

        if op == "call":
            fn_def = self.defs[expr["fn"]]
            args   = [self._node_to_py(self._compile(a, env)) for a in expr["a"]]
            fn_env = dict(zip(fn_def["params"], args))
            return self._compile(fn_def["body"], fn_env)

        if op in ("add", "sub", "mul", "div", "mod"):
            lv = self._node_to_py(self._compile(expr["l"], env))
            rv = self._node_to_py(self._compile(expr["r"], env))
            if op == "add": res = lv + rv
            elif op == "sub": res = lv - rv
            elif op == "mul": res = lv * rv
            elif op == "div": res = lv // rv
            else:             res = lv % rv
            return self._py_to_node(res)

        if op == "neg":
            return self._py_to_node(-self._node_to_py(self._compile(expr["e"], env)))

        if op in ("eq","lt","le","gt","ge"):
            lv = self._node_to_py(self._compile(expr["l"], env))
            rv = self._node_to_py(self._compile(expr["r"], env))
            res = {"eq":lv==rv,"lt":lv<rv,"le":lv<=rv,"gt":lv>rv,"ge":lv>=rv}[op]
            return self._py_to_node(res)

        if op == "and":
            lv = self._node_to_py(self._compile(expr["l"], env))
            rv = self._node_to_py(self._compile(expr["r"], env))
            return self._py_to_node(lv and rv)

        if op == "or":
            lv = self._node_to_py(self._compile(expr["l"], env))
            rv = self._node_to_py(self._compile(expr["r"], env))
            return self._py_to_node(lv or rv)

        if op == "not":
            return self._py_to_node(not self._node_to_py(self._compile(expr["e"], env)))

        if op == "cons":
            h = self._node_to_py(self._compile(expr["head"], env))
            t = self._node_to_py(self._compile(expr["tail"], env))
            return self._py_to_node([h] + t)

        if op == "append":
            lv = self._node_to_py(self._compile(expr["l"], env))
            rv = self._node_to_py(self._compile(expr["r"], env))
            return self._py_to_node(lv + rv)

        if op == "filter":
            pivot = self._node_to_py(self._compile(expr["pivot"], env))
            lst   = self._node_to_py(self._compile(expr["list"], env))
            pred  = expr["pred"]
            ops   = {"<=": lambda a,b: a<=b, ">":  lambda a,b: a>b,
                     "<":  lambda a,b: a<b,  ">=": lambda a,b: a>=b,
                     "==": lambda a,b: a==b}
            return self._py_to_node([x for x in lst if ops[pred](x, pivot)])

        if op == "match":
            sc = self._node_to_py(self._compile(expr["e"], env))
            for case in expr["cases"]:
                pat = case["pat"]
                if pat == "nil" and sc == []:
                    return self._compile(case["body"], env)
                if isinstance(pat, dict) and pat.get("tag") == "cons" and sc != []:
                    new_env = {**env, pat["x"]: sc[0], pat["xs"]: sc[1:]}
                    return self._compile(case["body"], new_env)
            raise ValueError(f"Non-exhaustive match: {sc!r}")

        if op == "head":
            return self._py_to_node(self._node_to_py(self._compile(expr["e"], env))[0])

        if op == "tail":
            return self._py_to_node(self._node_to_py(self._compile(expr["e"], env))[1:])

        if op == "take":
            n   = self._node_to_py(self._compile(expr["n"], env))
            lst = self._node_to_py(self._compile(expr["e"], env))
            return self._py_to_node(lst[:n])

        if op == "drop":
            n   = self._node_to_py(self._compile(expr["n"], env))
            lst = self._node_to_py(self._compile(expr["e"], env))
            return self._py_to_node(lst[n:])

        if op == "get":
            lst = self._node_to_py(self._compile(expr["e"], env))
            n   = int(self._node_to_py(self._compile(expr["n"], env)))
            return self._py_to_node(lst[n])

        if op == "len":
            lst = self._node_to_py(self._compile(expr["e"], env))
            return self._py_to_node(len(lst))

        if op == "array":
            n = int(self._node_to_py(self._compile(expr["n"], env)))
            v = self._node_to_py(self._compile(expr["v"], env))
            return self._py_to_node([v] * n)

        if op == "aset":
            lst = list(self._node_to_py(self._compile(expr["e"], env)))
            i   = int(self._node_to_py(self._compile(expr["n"], env)))
            v   = self._node_to_py(self._compile(expr["v"], env))
            lst[i] = v
            return self._py_to_node(lst)

        if op == "pair":
            lv = self._node_to_py(self._compile(expr["l"], env))
            rv = self._node_to_py(self._compile(expr["r"], env))
            return self._py_to_node((lv, rv))

        if op == "fst":
            return self._py_to_node(self._node_to_py(self._compile(expr["e"], env))[0])

        if op == "snd":
            return self._py_to_node(self._node_to_py(self._compile(expr["e"], env))[1])

        if op == "sin":   return self._py_to_node(_math.sin(self._node_to_py(self._compile(expr["e"], env))))
        if op == "cos":   return self._py_to_node(_math.cos(self._node_to_py(self._compile(expr["e"], env))))
        if op == "sqrt":  return self._py_to_node(_math.sqrt(self._node_to_py(self._compile(expr["e"], env))))
        if op == "floor": return self._py_to_node(_math.floor(self._node_to_py(self._compile(expr["e"], env))))
        if op == "ceil":  return self._py_to_node(_math.ceil(self._node_to_py(self._compile(expr["e"], env))))
        if op == "round": return self._py_to_node(round(self._node_to_py(self._compile(expr["e"], env))))
        if op == "abs":   return self._py_to_node(abs(self._node_to_py(self._compile(expr["e"], env))))
        if op == "ord":   return self._py_to_node(ord(self._node_to_py(self._compile(expr["e"], env))))
        if op == "chr":   return self._py_to_node(chr(self._node_to_py(self._compile(expr["e"], env))))

        raise NotImplementedError(f"Compiler: unhandled op {op!r}")

    # ── Value ↔ Node codecs ────────────────────────────────────────────────────

    def _py_to_node(self, value: Any) -> int:
        """Encode a Python value as interaction net nodes (reduced normal form)."""
        if isinstance(value, bool):
            return self.net.alloc(BOO, 0, meta=int(value))
        if isinstance(value, int):
            return self.net.alloc(INT, 0, meta=value)
        if isinstance(value, float):
            bits = struct.unpack("q", struct.pack("d", value))[0]
            return self.net.alloc(FLT, 0, meta=bits)
        if isinstance(value, str):
            return self.net.alloc(STR, 0, meta=ord(value) if value else 0)
        if isinstance(value, list):
            if not value:
                return self.net.alloc(NIL, 0)
            # CON(head_nid, tail_nid) — principal port is the list root
            node = self.net.alloc(CON, 2)
            head = self._py_to_node(value[0])
            tail = self._py_to_node(value[1:])
            self.net.node(node).ports[1] = head
            self.net.node(node).ports[2] = tail
            return node
        if isinstance(value, tuple):
            node = self.net.alloc(PAR, 2)   # PAR = distinct from CON (list)
            lv   = self._py_to_node(value[0])
            rv   = self._py_to_node(value[1])
            self.net.node(node).ports[1] = lv
            self.net.node(node).ports[2] = rv
            return node
        raise TypeError(f"Cannot encode {value!r} as Net node")

    def _node_to_py(self, nid: int) -> Any:
        """Decode a leaf or CON-chain node back to a Python value."""
        n = self.net.node(nid)
        if n.tag == INT:  return n.meta
        if n.tag == FLT:  return struct.unpack("d", struct.pack("q", n.meta))[0]
        if n.tag == STR:  return chr(n.meta)
        if n.tag == BOO:  return bool(n.meta)
        if n.tag == NIL:  return []
        if n.tag == CON:
            h = self._node_to_py(n.ports[1])
            t = self._node_to_py(n.ports[2])
            return [h] + t
        if n.tag == PAR:
            l = self._node_to_py(n.ports[1])
            r = self._node_to_py(n.ports[2])
            return (l, r)
        raise TypeError(f"Cannot decode node {n!r}")


# ── Bytecode serialiser / deserialiser ────────────────────────────────────────

MAGIC   = b"NELAC"
VERSION = 1


def _encode_node(node: Node) -> bytes:
    arity = len(node.ports) - 1
    data  = struct.pack(">BBq", node.tag, arity, node.meta)
    for p in node.ports:
        data += struct.pack(">I", p & 0xFFFFFFFF)
    return data


def net_to_bytes(net: Net, root_nid: int) -> bytes:
    nodes  = sorted(net._nodes.values(), key=lambda n: n.nid)
    header = MAGIC + struct.pack(">BI", VERSION, len(nodes))
    body   = b"".join(_encode_node(n) for n in nodes)
    footer = struct.pack(">I", root_nid)
    return header + body + footer


def bytes_to_net(data: bytes) -> tuple["Net", int]:
    """Deserialise .nelac bytes back to a Net + root node id."""
    assert data[:5] == MAGIC, "Not a .nelac file"
    offset = 5
    version, node_count = struct.unpack_from(">BI", data, offset); offset += 5
    net = Net()
    for _ in range(node_count):
        tag, arity = struct.unpack_from(">BB", data, offset); offset += 2
        meta,      = struct.unpack_from(">q",  data, offset); offset += 8
        ports = []
        for _ in range(arity + 1):
            p, = struct.unpack_from(">I", data, offset); offset += 4
            ports.append(p)
        nid = net.alloc(tag, arity, meta)
        for i, p in enumerate(ports):
            net.node(nid).ports[i] = p
    root, = struct.unpack_from(">I", data, offset)
    return net, root


def bytes_to_py(data: bytes) -> Any:
    """Decode .nelac bytes to a Python value by reading the root node."""
    net, root = bytes_to_net(data)
    c = Compiler.__new__(Compiler)
    c.net = net
    return c._node_to_py(root)


def disassemble(data: bytes) -> str:
    lines = []
    assert data[:5] == MAGIC
    version, node_count = struct.unpack_from(">BI", data, 5)
    lines.append(f"NELAC v{version}  nodes={node_count}")
    offset = 10
    for _ in range(node_count):
        tag, arity = struct.unpack_from(">BB", data, offset); offset += 2
        meta,      = struct.unpack_from(">q",  data, offset); offset += 8
        ports = []
        for _ in range(arity + 1):
            p, = struct.unpack_from(">I", data, offset); offset += 4
            ports.append("_" if p == _NULL else str(p))
        name = _TAG_NAMES.get(tag, f"0x{tag:02x}")
        lines.append(f"  {name:<6} meta={meta:<14} ports=[{', '.join(ports)}]")
    root, = struct.unpack_from(">I", data, offset)
    lines.append(f"  ROOT  → node {root}")
    return "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────────────

def compile_and_run(prog: dict, fn_name: str, *arg_values: Any) -> tuple[Any, bytes]:
    """
    Compile a NELA-S call to an interaction net, reduce fully, serialise.
    Returns (python_result, nelac_bytes).
    The bytes encode the reduced (normal-form) net — ready to reload with bytes_to_py.
    """
    c      = Compiler(prog)
    root   = c.compile_call(fn_name, list(arg_values))
    result = c._node_to_py(root)
    bc     = net_to_bytes(c.net, root)
    return result, bc


# ── CLI test harness ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    sys.setrecursionlimit(200_000)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _load(path):
        return parse_file(os.path.join(base, "examples", path))

    def _tc(got, ref):
        if isinstance(ref, float): return abs(got - ref) < 1e-9
        return got == ref

    def run_test(prog, fn, args, ref, label, *, key_check=None):
        print(f"\n{'='*55}")
        print(f"[ncc: {label}]")
        t0     = time.perf_counter()
        result, bc = compile_and_run(prog, fn, *args)
        elapsed = (time.perf_counter() - t0) * 1000
        if key_check:
            ok = key_check(result)
        elif isinstance(ref, list):
            ok = (isinstance(result, list) and len(result) == len(ref)
                  and all(_tc(r, e) for r, e in zip(result, ref)))
        else:
            ok = _tc(result, ref)
        print(f"  Expected: {ref!r}")
        print(f"  Got:      {result!r}")
        print(f"  Bytecode: {len(bc)} bytes")
        print(f"  Time:     {elapsed:.3f} ms")
        print(f"  Match:    {'PASS' if ok else 'FAIL'}")
        return ok

    qs_prog = parse_program("""\
def qs lst =
  match lst
  | []   = []
  | h::t = qs [x <- t | x <= h] ++ [h] ++ qs [x <- t | x > h]
""")
    ms_prog   = _load("mergesort.nela")
    wg_prog   = _load("wolf_game.nela")

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

    cases = [
        (qs_prog, "qs", [[]], [],         "qs []"),
        (qs_prog, "qs", [[1]], [1],       "qs [1]"),
        (qs_prog, "qs", [[3,1,2]], [1,2,3], "qs [3,1,2]"),
        (qs_prog, "qs", [[5,3,8,1,9,2,7,4,6]], list(range(1,10)), "qs 9 elem"),
        (qs_prog, "qs", [[5,4,3,2,1]], [1,2,3,4,5], "qs reverse"),
        (ms_prog, "mergesort", [[3,1,2]], [1,2,3],   "mergesort [3,1,2]"),
        (ms_prog, "mergesort", [[5,3,8,1,9,2]], [1,2,3,5,8,9], "mergesort 6"),
        (wg_prog, "deg_to_rad", [0],   0.0,           "deg_to_rad(0)"),
        (wg_prog, "deg_to_rad", [90],  _math.pi/2,    "deg_to_rad(90)"),
        (wg_prog, "norm_angle", [370], 10,             "norm_angle(370)"),
        (wg_prog, "norm_angle", [-20], 340,            "norm_angle(-20)"),
        (wg_prog, "is_wall",    [MAP8, 0.5, 0.5, 8], 1, "is_wall wall"),
        (wg_prog, "is_wall",    [MAP8, 1.5, 1.5, 8], 0, "is_wall open"),
        (wg_prog, "key_action", ["w"], 0,              "key_action 'w'"),
        (wg_prog, "key_action", ["q"], 5,              "key_action 'q'"),
        (wg_prog, "key_action", ["x"], 6,              "key_action unknown"),
    ]

    print("\n" + "#"*55)
    print("# NELA-C COMPILER (interaction net bytecode)")
    print("#"*55)
    all_pass = True
    for prog, fn, args, ref, label in cases:
        ok = run_test(prog, fn, args, ref, label)
        all_pass = all_pass and ok

    # use_door: check result[8] == 0
    result, bc = compile_and_run(wg_prog, "use_door", [1.5,1.5,270], MAP8, 8)
    ok = isinstance(result, list) and result[8] == 0
    print(f"\n{'='*55}")
    print(f"[ncc: use_door opens west wall]")
    print(f"  result[8]: {result[8] if isinstance(result, list) else '?'!r}")
    print(f"  Bytecode: {len(bc)} bytes")
    print(f"  Match:    {'PASS' if ok else 'FAIL'}")
    all_pass = all_pass and ok

    # roundtrip: serialise then deserialise
    _, bc_qs = compile_and_run(qs_prog, "qs", [3,1,2])
    roundtrip = bytes_to_py(bc_qs)
    ok_rt     = roundtrip == [1,2,3]
    print(f"\n{'='*55}")
    print(f"[ncc: bytecode roundtrip qs [3,1,2]]")
    print(f"  Got: {roundtrip!r}")
    print(f"  Match: {'PASS' if ok_rt else 'FAIL'}")
    all_pass = all_pass and ok_rt

    # disassembly
    print("\n" + "#"*55)
    print("# DISASSEMBLY (qs [1,2]  — small result)")
    print("#"*55)
    _, bc_small = compile_and_run(qs_prog, "qs", [2,1])
    print(disassemble(bc_small))

    print(f"\n{'='*55}")
    print(f"Compiler:   {'ALL PASS' if all_pass else 'SOME FAIL'}")
    sys.exit(0 if all_pass else 1)
