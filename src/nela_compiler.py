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

VAR   = 0x06  # wire / free variable: arity 1, meta = unique wire id
FIX   = 0x07  # fixed-point combinator: arity 1 (body port)
IOT   = 0x08  # IOToken leaf: arity 0 (linear I/O world token)
IOKEY = 0x09  # io_key:   arity 2  ports[1]=token_in, ports[2]=result_pair_out
IOPRT = 0x0A  # io_print: arity 3  ports[1]=frame, ports[2]=token_in, ports[3]=token_out
MAT   = 0x0B  # match node: arity = 2+ncases; ports[1]=scrutinee, ports[2..]=branch LAMs
FST   = 0x0C  # fst projection: arity 2  ports[1]=pair_in, ports[2]=result_out
SND   = 0x0D  # snd projection: arity 2  ports[1]=pair_in, ports[2]=result_out
FREF  = 0x0E  # function reference: arity 0, meta=fn_id (fires by deep-copying sub-net)

_TAG_NAMES = {
    CON:"CON", DUP:"DUP", ERA:"ERA", APP:"APP", LAM:"LAM",
    VAR:"VAR", FIX:"FIX", IOT:"IOT", IOKEY:"IOKEY", IOPRT:"IOPRT",
    MAT:"MAT", FST:"FST", SND:"SND", FREF:"FREF",
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
VERSION_FNTAB = 2  # extended format with function table
VERSION_STABLE = 3
VERSION_STABLE_FNTAB = 4  # v3 + function table


def _encode_node(node: Node) -> bytes:
    arity = len(node.ports) - 1
    data  = struct.pack(">BBq", node.tag, arity, node.meta)
    for p in node.ports:
        data += struct.pack(">I", p & 0xFFFFFFFF)
    return data


def _encode_node_stable(node: Node) -> bytes:
    """Encode node with explicit nid to make record order irrelevant."""
    arity = len(node.ports) - 1
    data  = struct.pack(">IBBq", node.nid, node.tag, arity, node.meta)
    for p in node.ports:
        data += struct.pack(">I", p & 0xFFFFFFFF)
    return data


def net_to_bytes(net: Net, root_nid: int,
                 fn_table: "list[tuple[Net, int]] | None" = None,
                 stable_ids: bool = False) -> bytes:
    """Serialise a Net to .nelac bytes.
    If fn_table is provided (list of (sub_net, root_nid) pairs), use version 2
    which appends a function table section after the main net root.
    """
    nodes  = sorted(net._nodes.values(), key=lambda n: n.nid)
    if stable_ids:
        version = VERSION_STABLE_FNTAB if fn_table is not None else VERSION_STABLE
        enc = _encode_node_stable
    else:
        version = VERSION_FNTAB if fn_table is not None else VERSION
        enc = _encode_node
    header = MAGIC + struct.pack(">BI", version, len(nodes))
    body   = b"".join(enc(n) for n in nodes)
    footer = struct.pack(">I", root_nid)
    result = header + body + footer
    if fn_table is not None:
        result += struct.pack(">I", len(fn_table))
        for fn_net, fn_root in fn_table:
            fn_nodes = sorted(fn_net._nodes.values(), key=lambda n: n.nid)
            result  += struct.pack(">I", len(fn_nodes))
            result  += b"".join(enc(n) for n in fn_nodes)
            result  += struct.pack(">I", fn_root)
    return result


def bytes_to_net(data: bytes) -> tuple["Net", int]:
    """Deserialise .nelac bytes back to a Net + root node id."""
    assert data[:5] == MAGIC, "Not a .nelac file"
    offset = 5
    version, node_count = struct.unpack_from(">BI", data, offset); offset += 5
    net = Net()

    # v1/v2: implicit nids by stream order (legacy, order-dependent)
    if version in (VERSION, VERSION_FNTAB):
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

    # v3/v4: explicit nids per record (order-independent)
    elif version in (VERSION_STABLE, VERSION_STABLE_FNTAB):
        records = []
        max_nid = -1
        for _ in range(node_count):
            nid, tag, arity = struct.unpack_from(">IBB", data, offset); offset += 6
            meta, = struct.unpack_from(">q", data, offset); offset += 8
            ports = []
            for _ in range(arity + 1):
                p, = struct.unpack_from(">I", data, offset); offset += 4
                ports.append(p)
            records.append((nid, tag, arity, meta, ports))
            if nid > max_nid:
                max_nid = nid

        # Allocate exactly the referenced node IDs.
        for nid, tag, arity, meta, _ports in records:
            net._nodes[nid] = Node(nid, tag, arity, meta)
        net._counter = max_nid + 1

        # Fill ports in a second pass.
        for nid, _tag, _arity, _meta, ports in records:
            for i, p in enumerate(ports):
                net.node(nid).ports[i] = p
    else:
        raise AssertionError(f"Unsupported .nelac version: {version}")

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
    for idx in range(node_count):
        if version in (VERSION_STABLE, VERSION_STABLE_FNTAB):
            nid, tag, arity = struct.unpack_from(">IBB", data, offset); offset += 6
        else:
            nid = idx
            tag, arity = struct.unpack_from(">BB", data, offset); offset += 2
        meta,      = struct.unpack_from(">q",  data, offset); offset += 8
        ports = []
        for _ in range(arity + 1):
            p, = struct.unpack_from(">I", data, offset); offset += 4
            ports.append("_" if p == _NULL else str(p))
        name = _TAG_NAMES.get(tag, f"0x{tag:02x}")
        lines.append(f"  #{nid:<4} {name:<6} meta={meta:<14} ports=[{', '.join(ports)}]")
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
    bc     = net_to_bytes(c.net, root, stable_ids=True)
    return result, bc


# ── Unreduced compiler (v0.11) ─────────────────────────────────────────────────
#
# Translates a NELA-S program to an UNEVALUATED interaction net where:
#   - Functions become nested LAM nodes
#   - Applications become APP nodes
#   - Recursion uses FIX nodes
#   - io_key / io_print become IOKEY / IOPRT nodes (reduced by C runtime)
#   - Variables are wired directly through ports (no VAR placeholder needed
#     when the variable has exactly one use)
#   - Variables with >1 uses get a DUP node
#
# The resulting .nelac can be fed to the C runtime which:
#   1. Runs the SIC reducer
#   2. When it hits IOT ⊳ IOKEY → calls getch(), wraps result in STR + PAR + IOT
#   3. When it hits IOT ⊳ IOPRT → calls puts(frame), returns new IOT
#
# Port conventions (same as C runtime):
#   LAM(arity=2): ports[0]=principal(output), ports[1]=body, ports[2]=var_wire
#   APP(arity=2): ports[0]=principal(connects to LAM.principal), ports[1]=result, ports[2]=arg
#   FIX(arity=1): ports[0]=principal, ports[1]=body_lam
#   IOKEY(arity=2): ports[0]=principal(connects to IOT), ports[1]=result_pair, ports[2]=_
#   IOPRT(arity=3): ports[0]=principal(connects to IOT), ports[1]=frame, ports[2]=_, ports[3]=token_out

class UnreducedCompiler:
    """Compile a NELA-S program to an unreduced interaction net using FREF nodes.

    Each user-defined function is compiled into its own sub-Net (function template).
    At each call site a FREF(fn_id) leaf node is emitted instead of inlining the
    function body.  The C SIC runtime fires FREF ⊳ X by deep-copying the template
    and wiring the new root to X — giving each call site its own independent copy.

    Usage:
        uc = UnreducedCompiler(prog)
        outlet = uc.compile_entry("game_loop", initial_state, initial_map, w)
        outlet = uc.attach_iotoken(outlet)
        uc.materialize_fn_table()            # compile all referenced bodies
        bc = net_to_bytes(uc.net, outlet, uc.fn_table_pairs())
    """

    def __init__(self, prog: dict):
        self.defs     = {d["name"]: d for d in prog["defs"]}
        self.net      = Net()           # main net (entry-point APP chain)
        self._port_of: dict = {}        # (nid, pidx) → peer_pidx  (main net)
        self._fn_cache: dict[str, int] = {}   # fn_name → fn_id
        self._fn_table: list = []             # fn_id → (Net, root_nid) | None

    # ── low-level helpers ─────────────────────────────────────────────────────

    def _wire(self, nid_a: int, pa: int, nid_b: int, pb: int):
        """Bidirectional port connection using raw node IDs.
        Tracks inverse port indices in self._port_of for attach_iotoken.
        """
        self.net.node(nid_a).ports[pa] = nid_b
        self.net.node(nid_b).ports[pb] = nid_a
        self._port_of[(nid_a, pa)] = pb
        self._port_of[(nid_b, pb)] = pa

    def _pattern_binds_name(self, pat: Any, name: str) -> bool:
        if isinstance(pat, str):
            return pat == name
        if isinstance(pat, dict) and pat.get("tag") == "cons":
            return pat.get("x") == name or pat.get("xs") == name
        return False

    def _count_uses(self, expr: dict, name: str) -> int:
        op = expr["op"]

        if op in ("int", "float", "char", "bool", "nil"):
            return 0
        if op == "var":
            return 1 if expr["n"] == name else 0

        if op == "let":
            c = self._count_uses(expr["e"], name)
            if expr["x"] == name:
                return c
            return c + self._count_uses(expr["in"], name)

        if op == "let_pair":
            c = self._count_uses(expr["e"], name)
            if expr["a"] == name or expr["b"] == name:
                return c
            return c + self._count_uses(expr["in"], name)

        if op == "if":
            return (self._count_uses(expr["cond"], name)
                    + self._count_uses(expr["then"], name)
                    + self._count_uses(expr["else_"], name))

        if op == "call":
            return sum(self._count_uses(ae, name) for ae in expr["a"])

        if op in ("add", "sub", "mul", "div", "mod", "eq", "lt", "le", "gt", "ge",
                  "and", "or", "pair", "append", "get", "take", "drop"):
            return self._count_uses(expr["l" if "l" in expr else "e"], name) + \
                   self._count_uses(expr["r" if "r" in expr else "n"], name)

        if op in ("neg", "not", "fst", "snd", "head", "tail", "len",
                  "sin", "cos", "sqrt", "floor", "ceil", "round", "abs", "ord", "chr", "io_key"):
            return self._count_uses(expr["e"], name)

        if op == "cons":
            return self._count_uses(expr["head"], name) + self._count_uses(expr["tail"], name)

        if op == "filter":
            return self._count_uses(expr["pivot"], name) + self._count_uses(expr["list"], name)

        if op == "array":
            return self._count_uses(expr["n"], name) + self._count_uses(expr["v"], name)

        if op == "aset":
            return (self._count_uses(expr["e"], name)
                    + self._count_uses(expr["n"], name)
                    + self._count_uses(expr["v"], name))

        if op == "io_print":
            return self._count_uses(expr["l"], name) + self._count_uses(expr["r"], name)

        if op == "match":
            total = self._count_uses(expr["e"], name)
            for case in expr["cases"]:
                if self._pattern_binds_name(case["pat"], name):
                    continue
                total += self._count_uses(case["body"], name)
            return total

        return 0

    def _make_var_supply(self, source_var_nid: int, uses: int) -> list[int]:
        """Return exactly `uses` consumable VAR endpoints for one source value."""
        if uses <= 0:
            return []
        if uses == 1:
            return [source_var_nid]

        supply: list[int] = []
        cur = source_var_nid
        for _ in range(uses - 1):
            dup = self.net.alloc(DUP, 2)
            self._wire(dup, 0, cur, 0)
            out_now = self.net.alloc(VAR, 1)
            out_later = self.net.alloc(VAR, 1)
            self._wire(dup, 1, out_now, 1)
            self._wire(dup, 2, out_later, 1)
            supply.append(out_now)
            cur = out_later
        supply.append(cur)
        return supply

    def _consume_var(self, env: dict, name: str) -> int:
        if name not in env:
            raise KeyError(f"Unbound variable: {name!r}")
        supply = env[name]
        if not supply:
            raise RuntimeError(f"Variable {name!r} used too many times")
        return supply.pop(0)

    # ── function-reference allocation ─────────────────────────────────────────

    def _alloc_fref(self, fn_name: str) -> int:
        """Return a fresh FREF(fn_id) node in self.net.
        Registers fn_name in _fn_cache/_fn_table if not already seen.
        A fresh node is returned every call — no sharing, so each call site is
        independent.  _fn_table entries are filled by materialize_fn_table().
        """
        if fn_name not in self._fn_cache:
            fn_id = len(self._fn_table)
            self._fn_cache[fn_name] = fn_id
            self._fn_table.append(None)      # placeholder; compiled lazily
        fn_id = self._fn_cache[fn_name]
        return self.net.alloc(FREF, 0, meta=fn_id)

    # ── public entry-point builder ────────────────────────────────────────────

    def compile_entry(self, fn_name: str, *arg_values) -> int:
        """Build APP chain: FREF(fn) applied to arg_values in self.net.
        Returns the outlet VAR nid (result placeholder).
        """
        fn_nid = self._alloc_fref(fn_name)
        cur    = fn_nid
        result_port = None
        for val in arg_values:
            app = self.net.alloc(APP, 2)
            arg = self._value_to_node(val)
            self._wire(app, 0, cur, 0)
            self._wire(app, 2, arg, 0)
            result_var = self.net.alloc(VAR, 1)
            self._wire(app, 1, result_var, 1)
            cur = result_var
            result_port = (app, 1)
        outlet = self.net.alloc(VAR, 1)
        if result_port:
            self._wire(result_port[0], result_port[1], outlet, 1)
        return outlet

    def attach_iotoken(self, outlet_nid: int) -> int:
        """Append an IOToken as the final argument, returning the new outlet."""
        iot = self.net.alloc(IOT, 0)
        app = self.net.alloc(APP, 2)
        outlet = outlet_nid
        outlet_port = 0 if self.net.node(outlet).ports[0] != _NULL else 1
        prev_nid  = self.net.node(outlet).ports[outlet_port]
        if prev_nid == _NULL:
            raise ValueError("attach_iotoken: no APP to extend")
        prev_pidx = self._port_of.get((outlet, outlet_port), 1)
        # disconnect outlet from prev
        self.net.node(prev_nid).ports[prev_pidx] = _NULL
        self.net.node(outlet).ports[outlet_port] = _NULL
        if (outlet, outlet_port) in self._port_of:
            del self._port_of[(outlet, outlet_port)]
        if (prev_nid, prev_pidx) in self._port_of:
            del self._port_of[(prev_nid, prev_pidx)]
        # insert new APP
        self._wire(app, 0, prev_nid, prev_pidx)
        self._wire(app, 2, iot, 0)
        new_outlet = self.net.alloc(VAR, 1)
        self._wire(app, 1, new_outlet, 1)
        return new_outlet

    # ── lazy function-body compilation ────────────────────────────────────────

    def materialize_fn_table(self):
        """BFS: compile every referenced-but-not-yet-compiled function body.
        New functions may be discovered while compiling (via _alloc_fref calls
        inside _compile_expr), so we loop until the table is fully populated.
        """
        i = 0
        while i < len(self._fn_table):
            if self._fn_table[i] is None:
                fn_name = next(k for k, v in self._fn_cache.items() if v == i)
                self._fn_table[i] = self._compile_fn_body(fn_name)
            i += 1

    def fn_table_pairs(self) -> "list[tuple[Net, int]]":
        """Return the fully materialised fn_table as (Net, root_nid) pairs."""
        assert all(e is not None for e in self._fn_table), \
            "call materialize_fn_table() first"
        return list(self._fn_table)

    def _compile_fn_body(self, fn_name: str) -> "tuple[Net, int]":
        """Compile fn_name into a fresh standalone (Net, root_nid) pair.
        Temporarily swaps self.net / self._port_of so that _compile_expr and
        _alloc_fref operate on the sub-net.
        Recursive references to fn_name produce FREF(fn_id) leaf nodes — no
        infinite recursion and no sharing because each call site gets its own
        fresh node.
        """
        fn_def = self.defs[fn_name]
        params = fn_def["params"]

        old_net, old_port_of = self.net, self._port_of
        self.net      = Net()
        self._port_of = {}
        try:
            body = fn_def["body"]
            param_use_count = {p: self._count_uses(body, p) for p in params}

            param_wire = {}
            env = {}
            for p in params:
                uses = param_use_count[p]
                if uses == 0:
                    era = self.net.alloc(ERA, 0)
                    param_wire[p] = (era, 0)
                    env[p] = []
                else:
                    src = self.net.alloc(VAR, 1)
                    param_wire[p] = (src, 1)
                    env[p] = self._make_var_supply(src, uses)

            body_nid = self._compile_expr(fn_def["body"], env)
            cur      = body_nid
            for p in reversed(params):
                lam = self.net.alloc(LAM, 2)
                self._wire(lam, 1, cur,          0)
                self._wire(lam, 2, param_wire[p][0], param_wire[p][1])
                cur = lam
            fn_net  = self.net
            fn_root = cur
        finally:
            self.net, self._port_of = old_net, old_port_of

        return fn_net, fn_root

    # ── expression compiler ───────────────────────────────────────────────────

    def _compile_expr(self, expr: dict, env: dict) -> int:
        """Returns the nid whose principal port is the OUTPUT of this expression.
        Uses self.net (which may be main net or a sub-net during _compile_fn_body).
        """
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
            name = expr["n"]
            return self._consume_var(env, name)

        if op == "let":
            x_var = self.net.alloc(VAR, 1)
            e_nid = self._compile_expr(expr["e"], env)
            self._wire(x_var, 1, e_nid, 0)
            uses = self._count_uses(expr["in"], expr["x"])
            if uses == 0:
                er = self.net.alloc(ERA, 0)
                self._wire(er, 0, x_var, 0)
                supply = []
            else:
                supply = self._make_var_supply(x_var, uses)
            new_env = {**env, expr["x"]: supply}
            return self._compile_expr(expr["in"], new_env)

        if op == "let_pair":
            e_nid = self._compile_expr(expr["e"], env)
            a_var = self.net.alloc(VAR, 1)
            b_var = self.net.alloc(VAR, 1)
            fst_n = self.net.alloc(FST, 2)
            snd_n = self.net.alloc(SND, 2)
            dup   = self.net.alloc(DUP, 2)
            self._wire(dup,   0, e_nid, 0)
            self._wire(fst_n, 1, dup,   1)
            self._wire(snd_n, 1, dup,   2)
            self._wire(fst_n, 2, a_var, 1)
            self._wire(snd_n, 2, b_var, 1)
            uses_a = self._count_uses(expr["in"], expr["a"])
            uses_b = self._count_uses(expr["in"], expr["b"])
            if uses_a == 0:
                er = self.net.alloc(ERA, 0)
                self._wire(er, 0, a_var, 0)
                supply_a = []
            else:
                supply_a = self._make_var_supply(a_var, uses_a)
            if uses_b == 0:
                er = self.net.alloc(ERA, 0)
                self._wire(er, 0, b_var, 0)
                supply_b = []
            else:
                supply_b = self._make_var_supply(b_var, uses_b)
            new_env = {**env, expr["a"]: supply_a, expr["b"]: supply_b}
            return self._compile_expr(expr["in"], new_env)

        if op == "if":
            cond   = self._compile_expr(expr["cond"],  env)
            then   = self._compile_expr(expr["then"],  env)
            els    = self._compile_expr(expr["else_"], env)
            ift    = self.net.alloc(IFT, 3)
            result = self.net.alloc(VAR, 1)
            self._wire(ift, 0, cond,   0)
            self._wire(ift, 1, then,   0)
            self._wire(ift, 2, els,    0)
            self._wire(ift, 3, result, 1)
            return result

        if op == "call":
            fn_name   = expr["fn"]
            arg_exprs = expr["a"]
            # Emit a fresh FREF for this call site — no sharing, each fires independently
            fn_nid = self._alloc_fref(fn_name)
            cur    = fn_nid
            for ae in arg_exprs:
                app    = self.net.alloc(APP, 2)
                arg    = self._compile_expr(ae, env)
                result = self.net.alloc(VAR, 1)
                self._wire(app, 0, cur,    0)
                self._wire(app, 2, arg,    0)
                self._wire(app, 1, result, 1)
                cur = result
            return cur

        if op in ("add","sub","mul","div","mod"):
            tag_map = {"add":ADD,"sub":SUB,"mul":MUL,"div":DIV,"mod":MOD}
            t   = tag_map[op]
            lv  = self._compile_expr(expr["l"], env)
            rv  = self._compile_expr(expr["r"], env)
            n   = self.net.alloc(t, 3)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 1, lv,  0); self._wire(n, 2, rv, 0); self._wire(n, 3, res, 1)
            return res

        if op == "neg":
            v   = self._compile_expr(expr["e"], env)
            n   = self.net.alloc(NEG, 2)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 1, v, 0); self._wire(n, 2, res, 1)
            return res

        if op in ("eq","lt","le","gt","ge"):
            tag_map = {"eq":EQL,"lt":LTH,"le":LEQ,"gt":GTH,"ge":GEQ}
            t   = tag_map[op]
            lv  = self._compile_expr(expr["l"], env)
            rv  = self._compile_expr(expr["r"], env)
            n   = self.net.alloc(t, 3)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 1, lv, 0); self._wire(n, 2, rv, 0); self._wire(n, 3, res, 1)
            return res

        if op in ("and","or"):
            tag_map = {"and":AND,"or":ORR}
            t   = tag_map[op]
            lv  = self._compile_expr(expr["l"], env)
            rv  = self._compile_expr(expr["r"], env)
            n   = self.net.alloc(t, 3)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 1, lv, 0); self._wire(n, 2, rv, 0); self._wire(n, 3, res, 1)
            return res

        if op == "not":
            v   = self._compile_expr(expr["e"], env)
            n   = self.net.alloc(NOT, 2)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 1, v, 0); self._wire(n, 2, res, 1)
            return res

        if op == "cons":
            h = self._compile_expr(expr["head"], env)
            t = self._compile_expr(expr["tail"], env)
            n = self.net.alloc(CON, 2)
            self._wire(n, 1, h, 0); self._wire(n, 2, t, 0)
            return n

        if op == "append":
            lv = self._compile_expr(expr["l"], env)
            rv = self._compile_expr(expr["r"], env)
            return self._builtin2(0xF0, lv, rv)

        if op == "filter":
            pivot    = self._compile_expr(expr["pivot"], env)
            lst      = self._compile_expr(expr["list"],  env)
            pred_tag = {"<=":0xF1,">":0xF2,"<":0xF3,">=":0xF4,"==":0xF5}[expr["pred"]]
            return self._builtin2(pred_tag, pivot, lst)

        if op == "match":
            sc     = self._compile_expr(expr["e"], env)
            ncases = len(expr["cases"])
            mat    = self.net.alloc(MAT, 1 + ncases)
            res    = self.net.alloc(VAR, 1)
            self._wire(mat, 0, sc, 0)
            for i, case in enumerate(expr["cases"]):
                br = self._compile_branch(case, env)
                self._wire(mat, 1 + i, br, 0)
            self._wire(mat, 1 + ncases, res, 1)
            return res

        if op == "pair":
            lv = self._compile_expr(expr["l"], env)
            rv = self._compile_expr(expr["r"], env)
            n  = self.net.alloc(PAR, 2)
            self._wire(n, 1, lv, 0); self._wire(n, 2, rv, 0)
            return n

        if op == "fst":
            v   = self._compile_expr(expr["e"], env)
            n   = self.net.alloc(FST, 2)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 1, v, 0); self._wire(n, 2, res, 1)
            return res

        if op == "snd":
            v   = self._compile_expr(expr["e"], env)
            n   = self.net.alloc(SND, 2)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 1, v, 0); self._wire(n, 2, res, 1)
            return res

        if op == "head":
            v   = self._compile_expr(expr["e"], env)
            n   = self.net.alloc(HED, 2)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 1, v, 0); self._wire(n, 2, res, 1)
            return res

        if op == "tail":
            v   = self._compile_expr(expr["e"], env)
            n   = self.net.alloc(TAL, 2)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 1, v, 0); self._wire(n, 2, res, 1)
            return res

        if op == "get":
            lst = self._compile_expr(expr["e"], env)
            idx = self._compile_expr(expr["n"], env)
            return self._builtin2(GET, lst, idx)

        if op == "len":
            v   = self._compile_expr(expr["e"], env)
            n   = self.net.alloc(LEN, 2)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 1, v, 0); self._wire(n, 2, res, 1)
            return res

        if op == "array":
            nv = self._compile_expr(expr["n"], env)
            vv = self._compile_expr(expr["v"], env)
            return self._builtin2(ARR, nv, vv)

        if op == "aset":
            lst = self._compile_expr(expr["e"], env)
            idx = self._compile_expr(expr["n"], env)
            val = self._compile_expr(expr["v"], env)
            return self._builtin3(AST, lst, idx, val)

        if op == "take":
            nv  = self._compile_expr(expr["n"], env)
            lst = self._compile_expr(expr["e"], env)
            return self._builtin2(0xF6, nv, lst)

        if op == "drop":
            nv  = self._compile_expr(expr["n"], env)
            lst = self._compile_expr(expr["e"], env)
            return self._builtin2(0xF7, nv, lst)

        for unary_op, tag in [("sin",0xE0),("cos",0xE1),("sqrt",0xE2),
                               ("floor",0xE3),("ceil",0xE4),("round",0xE5),
                               ("abs",0xE6),("ord",0xE7),("chr",0xE8)]:
            if op == unary_op:
                v   = self._compile_expr(expr["e"], env)
                n   = self.net.alloc(tag, 2)
                res = self.net.alloc(VAR, 1)
                self._wire(n, 1, v, 0); self._wire(n, 2, res, 1)
                return res

        if op == "io_key":
            tok = self._compile_expr(expr["e"], env)
            n   = self.net.alloc(IOKEY, 2)
            res = self.net.alloc(VAR, 1)
            self._wire(n, 0, tok, 0)
            self._wire(n, 1, res, 1)
            return res

        if op == "io_print":
            frame = self._compile_expr(expr["l"], env)
            tok   = self._compile_expr(expr["r"], env)
            n     = self.net.alloc(IOPRT, 3)
            res   = self.net.alloc(VAR, 1)
            self._wire(n, 0, tok,   0)
            self._wire(n, 1, frame, 0)
            self._wire(n, 3, res,   1)
            return res

        raise NotImplementedError(f"UnreducedCompiler: unhandled op {op!r}")

    # ── branch / builtin helpers ──────────────────────────────────────────────

    def _compile_branch(self, case: dict, env: dict) -> int:
        pat  = case["pat"]
        body = case["body"]
        if pat == "nil":
            dummy = self.net.alloc(VAR, 1)
            lam   = self.net.alloc(LAM, 2)
            b     = self._compile_expr(body, env)
            self._wire(lam, 1, b,     0)
            self._wire(lam, 2, dummy, 1)
            return lam
        if isinstance(pat, dict) and pat.get("tag") == "cons":
            h_var  = self.net.alloc(VAR, 1)
            t_var  = self.net.alloc(VAR, 1)
            hx = pat["x"]
            tx = pat["xs"]
            uses_h = self._count_uses(body, hx)
            uses_t = self._count_uses(body, tx)
            if uses_h == 0:
                er = self.net.alloc(ERA, 0)
                self._wire(er, 0, h_var, 0)
                supply_h = []
            else:
                supply_h = self._make_var_supply(h_var, uses_h)
            if uses_t == 0:
                er = self.net.alloc(ERA, 0)
                self._wire(er, 0, t_var, 0)
                supply_t = []
            else:
                supply_t = self._make_var_supply(t_var, uses_t)
            new_env = {**env, hx: supply_h, tx: supply_t}
            b      = self._compile_expr(body, new_env)
            lam_t  = self.net.alloc(LAM, 2)
            self._wire(lam_t, 1, b,     0)
            self._wire(lam_t, 2, t_var, 1)
            lam_h  = self.net.alloc(LAM, 2)
            self._wire(lam_h, 1, lam_t, 0)
            self._wire(lam_h, 2, h_var, 1)
            return lam_h
        v_var   = self.net.alloc(VAR, 1)
        pname   = pat if isinstance(pat, str) else "_"
        uses_v = 0 if pname == "_" else self._count_uses(body, pname)
        if uses_v == 0:
            er = self.net.alloc(ERA, 0)
            self._wire(er, 0, v_var, 0)
            supply_v = []
        else:
            supply_v = self._make_var_supply(v_var, uses_v)
        new_env = {**env, pname: supply_v}
        b       = self._compile_expr(body, new_env)
        lam     = self.net.alloc(LAM, 2)
        self._wire(lam, 1, b,     0)
        self._wire(lam, 2, v_var, 1)
        return lam

    def _builtin2(self, tag: int, a: int, b: int) -> int:
        n   = self.net.alloc(tag, 3)
        res = self.net.alloc(VAR, 1)
        self._wire(n, 1, a,   0)
        self._wire(n, 2, b,   0)
        self._wire(n, 3, res, 1)
        return res

    def _builtin3(self, tag: int, a: int, b: int, c: int) -> int:
        n   = self.net.alloc(tag, 4)
        res = self.net.alloc(VAR, 1)
        self._wire(n, 1, a,   0)
        self._wire(n, 2, b,   0)
        self._wire(n, 3, c,   0)
        self._wire(n, 4, res, 1)
        return res

    def _value_to_node(self, value: Any) -> int:
        """Encode a Python value as leaf/CON net nodes in self.net."""
        c      = Compiler.__new__(Compiler)
        c.net  = self.net
        c.defs = self.defs
        return c._py_to_node(value)


def compile_program(prog: dict, fn_name: str, *arg_values) -> bytes:
    """
    Compile a NELA-S program call to .nelac bytecode.

    - Pure entry points (declared arity == provided args) are compiled eagerly
      to reduced normal-form nets (version 1).
    - Effectful entry points (declared arity == provided args + 1) are compiled
      as unreduced nets with lazy function table and appended IOToken (version 2).

    Returns bytes ready for the C runtime.
    """
    target = None
    for d in prog.get("defs", []):
        if d.get("name") == fn_name:
            target = d
            break

    declared_arity = len(target.get("params", [])) if target is not None else None

    # Pure entry: compile to reduced value net.
    if declared_arity is not None and declared_arity == len(arg_values):
        c = Compiler(prog)
        root = c.compile_call(fn_name, list(arg_values))
        return net_to_bytes(c.net, root)

    # Effectful entry: unreduced net + IOToken + function table.
    uc     = UnreducedCompiler(prog)
    outlet = uc.compile_entry(fn_name, *arg_values)
    if declared_arity is not None and declared_arity == len(arg_values) + 1:
        outlet = uc.attach_iotoken(outlet)
    uc.materialize_fn_table()
    return net_to_bytes(uc.net, outlet, uc.fn_table_pairs())

# ── CLI test harness ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    sys.setrecursionlimit(200_000)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _load(path):
        return parse_file(os.path.join(base, "examples", path))

    def _load_wolf(path):
        return parse_file(os.path.join(base, "examples", "wolf", path))

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
    wg_prog   = _load_wolf("wolf_game.nela")

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
