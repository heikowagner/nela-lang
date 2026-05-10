#!/usr/bin/env python3
"""Render NELA interaction nets as Lafont-style SVG diagrams.

This example reads a .nela source file, compiles a selected function call,
and writes an SVG visualization with explicit principal and auxiliary ports.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from nela_compiler import (  # type: ignore
    _NULL,
    _TAG_NAMES,
    UnreducedCompiler,
    bytes_to_net,
    compile_and_run,
)
from nela_parser import parse_file  # type: ignore
from nela_runtime import run_program  # type: ignore


DISPLAY_TAG_NAMES = {
    **_TAG_NAMES,
    0xE0: "SIN",
    0xE1: "COS",
    0xE2: "SQRT",
    0xE3: "FLOOR",
    0xE4: "CEIL",
    0xE5: "ROUND",
    0xE6: "ABS",
    0xE7: "ORD",
    0xE8: "CHR",
    0xF0: "APPEND",
    0xF1: "FILTER_LE",
    0xF2: "FILTER_GT",
    0xF3: "FILTER_LT",
    0xF4: "FILTER_GE",
    0xF5: "FILTER_EQ",
    0xF6: "TAKE",
    0xF7: "DROP",
}


_NETVIZ_CORE_PATH = os.path.join(ROOT, "examples", "netviz_interaction_core.nela")
_NETVIZ_CORE_PROG = parse_file(_NETVIZ_CORE_PATH)


@dataclass
class Edge:
    a_nid: int
    a_port: int
    b_nid: int
    b_port: int


@dataclass
class EdgeStats:
    total_refs: int
    reciprocal_refs: int
    inferred_refs: int


def _build_edges(net) -> Tuple[List[Edge], EdgeStats]:
    """Recover port-to-port edges from node adjacency lists.

    Compiled nets may contain one-way node references rather than strict reciprocal
    port assignments, so we accept both reciprocal and non-reciprocal links.
    """
    refs: List[List[int]] = []
    port_counts: List[Tuple[int, int]] = []
    for nid in sorted(net._nodes.keys()):
        node = net.node(nid)
        port_counts.append((nid, len(node.ports)))
        for pidx, peer_nid in enumerate(node.ports):
            if peer_nid == _NULL or peer_nid not in net._nodes:
                continue
            refs.append([nid, peer_nid, pidx])

    nela_edges = run_program(_NETVIZ_CORE_PROG, "canonical_edges", refs, port_counts)
    edges: List[Edge] = []
    for e in nela_edges:
        if not isinstance(e, list) or len(e) != 4:
            continue
        edges.append(Edge(int(e[0]), int(e[1]), int(e[2]), int(e[3])))

    stats_raw = run_program(_NETVIZ_CORE_PROG, "edge_stats", refs)
    total_refs = int(stats_raw[0]) if isinstance(stats_raw, list) and len(stats_raw) >= 1 else len(refs)
    reciprocal_refs = int(stats_raw[1]) if isinstance(stats_raw, list) and len(stats_raw) >= 2 else 0

    stats = EdgeStats(
        total_refs=total_refs,
        reciprocal_refs=reciprocal_refs,
        inferred_refs=max(0, total_refs - reciprocal_refs),
    )
    return edges, stats


def _layers_from_root(net, root_nid: int, edges: List[Edge]) -> Dict[int, int]:
    if not net._nodes:
        return {}

    if root_nid not in net._nodes:
        root_nid = sorted(net._nodes.keys())[0]

    # Undirected edge pairs for the NELA-S BFS core.
    edge_pairs = [(e.a_nid, e.b_nid) for e in edges]
    node_ids = sorted(net._nodes.keys())

    nela_layers = run_program(_NETVIZ_CORE_PROG, "compute_layers", node_ids, edge_pairs, root_nid)
    layers: Dict[int, int] = {}
    for item in nela_layers:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        nid, layer = item
        layers[int(nid)] = int(layer)

    return layers


def _node_port_pos(cx: float, cy: float, radius: float, arity: int, pidx: int) -> Tuple[float, float]:
    # Principal port on top (Lafont-style visible principal marker).
    if pidx == 0:
        return cx, cy - radius

    if arity <= 0:
        return cx, cy + radius

    # Auxiliary ports spread on lower arc.
    if arity == 1:
        angle = math.radians(90)
    else:
        start = math.radians(210)
        end = math.radians(-30)
        t = (pidx - 1) / (arity - 1)
        angle = start + t * (end - start)
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def _layout(net, root_nid: int, edges: List[Edge]) -> Tuple[Dict[int, Tuple[float, float]], int, int]:
    layers = _layers_from_root(net, root_nid, edges)
    layer_pairs = sorted((nid, layer) for nid, layer in layers.items())
    plan = run_program(_NETVIZ_CORE_PROG, "layout_plan", layer_pairs)

    by_layer: Dict[int, List[int]] = {}
    max_layer = max((layer for _, layer in layer_pairs), default=0)
    max_count = 1

    if isinstance(plan, list) and len(plan) >= 3:
        try:
            max_layer = int(plan[0])
            max_count = int(plan[1])
            buckets = plan[2]
            if isinstance(buckets, list):
                for b in buckets:
                    if not isinstance(b, tuple) or len(b) != 2:
                        continue
                    l, nids = b
                    if isinstance(nids, list):
                        by_layer[int(l)] = [int(n) for n in nids]
        except Exception:
            by_layer = {}

    if not by_layer:
        for nid, layer in layers.items():
            by_layer.setdefault(layer, []).append(nid)
        for layer in by_layer:
            by_layer[layer].sort()
        max_layer = max(by_layer.keys(), default=0)
        max_count = max((len(v) for v in by_layer.values()), default=1)

    x_step = 180
    y_step = 110
    margin = 70

    positions: Dict[int, Tuple[float, float]] = {}
    height = margin * 2 + (max_count - 1) * y_step + 120
    width = margin * 2 + max_layer * x_step + 220

    for layer, nids in by_layer.items():
        block_h = (len(nids) - 1) * y_step
        top = (height - block_h) / 2
        x = margin + 90 + layer * x_step
        for i, nid in enumerate(nids):
            y = top + i * y_step
            positions[nid] = (x, y)

    return positions, int(width), int(height)


def _collect_calls(expr: dict, out: Set[str]) -> None:
    op = expr.get("op")

    if op == "call":
        fn = expr.get("fn")
        if isinstance(fn, str):
            out.add(fn)
        for a in expr.get("a", []):
            _collect_calls(a, out)
        return

    if op in ("let",):
        _collect_calls(expr["e"], out)
        _collect_calls(expr["in"], out)
        return

    if op in ("if",):
        _collect_calls(expr["cond"], out)
        _collect_calls(expr["then"], out)
        _collect_calls(expr["else_"], out)
        return

    if op in ("add", "sub", "mul", "div", "mod", "eq", "lt", "le", "gt", "ge", "and", "or", "append", "pair"):
        _collect_calls(expr["l"], out)
        _collect_calls(expr["r"], out)
        return

    if op in ("neg", "not", "fst", "snd", "head", "tail", "len", "sin", "cos", "sqrt", "floor", "ceil", "round", "abs", "ord", "chr", "io_key"):
        _collect_calls(expr["e"], out)
        return

    if op == "cons":
        _collect_calls(expr["head"], out)
        _collect_calls(expr["tail"], out)
        return

    if op == "filter":
        _collect_calls(expr["pivot"], out)
        _collect_calls(expr["list"], out)
        return

    if op == "match":
        _collect_calls(expr["e"], out)
        for case in expr.get("cases", []):
            _collect_calls(case["body"], out)
        return

    if op == "array":
        _collect_calls(expr["n"], out)
        _collect_calls(expr["v"], out)
        return

    if op == "aset":
        _collect_calls(expr["e"], out)
        _collect_calls(expr["n"], out)
        _collect_calls(expr["v"], out)
        return

    if op in ("get", "take", "drop"):
        _collect_calls(expr["e"], out)
        _collect_calls(expr["n"], out)
        return

    if op == "io_print":
        _collect_calls(expr["l"], out)
        _collect_calls(expr["r"], out)


def _ast_reachable_functions(prog: dict, entry_fn: str) -> List[str]:
    defs = prog.get("defs", [])
    names = [d["name"] for d in defs]
    name_to_idx = {n: i for i, n in enumerate(names)}
    if entry_fn not in name_to_idx:
        return []

    edges: List[Tuple[int, int]] = []
    for d in defs:
        src = name_to_idx[d["name"]]
        calls: Set[str] = set()
        _collect_calls(d["body"], calls)
        for callee in sorted(calls):
            if callee in name_to_idx:
                edges.append((src, name_to_idx[callee]))

    nodes = list(range(len(names)))
    root = name_to_idx[entry_fn]
    reachable = run_program(_NETVIZ_CORE_PROG, "reachable_nodes", nodes, edges, root)

    out: List[str] = []
    for idx in reachable:
        i = int(idx)
        if 0 <= i < len(names):
            out.append(names[i])
    return out


def _surface_to_net_bridge_lines() -> List[str]:
    return [
        "NELA-S -> Interaction Net",
        "def / binder -> LAM",
        "f x / call -> APP + FREF(fn)",
        "if c then a else b -> IFT",
        "h :: t / [] -> CON / NIL",
        "match ... -> MAT (+ branch LAMs)",
        "x + y, x * y, ... -> ADD, MUL, ...",
        "io_key / io_print -> IOKEY / IOPRT",
        "variable usage wiring -> VAR",
        "copy/discard (linear) -> DUP / ERA",
    ]


def render_svg(
    net,
    root_nid: int,
    fn_name_by_id: Dict[int, str] | None = None,
    ast_overview: List[str] | None = None,
    bridge_lines: List[str] | None = None,
) -> Tuple[str, EdgeStats]:
    edges, stats = _build_edges(net)
    pos, width, height = _layout(net, root_nid, edges)
    fn_name_by_id = fn_name_by_id or {}
    ast_overview = ast_overview or []
    bridge_lines = bridge_lines or []

    r = 30.0
    parts: List[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">')
    parts.append("<rect x=\"0\" y=\"0\" width=\"100%\" height=\"100%\" fill=\"#ffffff\"/>")

    # Wires
    for e in edges:
        a = net.node(e.a_nid)
        b = net.node(e.b_nid)
        ax, ay = _node_port_pos(*pos[e.a_nid], r, len(a.ports) - 1, e.a_port)
        bx, by = _node_port_pos(*pos[e.b_nid], r, len(b.ports) - 1, e.b_port)

        # Slight bend gives a hand-drawn proof-net feel and prevents flat overlap.
        mx = (ax + bx) / 2
        my = (ay + by) / 2
        dx = bx - ax
        dy = by - ay
        nx = -dy
        ny = dx
        norm = math.hypot(nx, ny)
        if norm > 1e-6:
            nx /= norm
            ny /= norm
        bend = 14.0
        cx = mx + nx * bend
        cy = my + ny * bend

        parts.append(
            f'<path d="M {ax:.2f} {ay:.2f} Q {cx:.2f} {cy:.2f} {bx:.2f} {by:.2f}" '
            'stroke="#111111" stroke-width="1.6" fill="none"/>'
        )

    # Nodes and ports
    for nid in sorted(net._nodes.keys()):
        node = net.node(nid)
        cx, cy = pos[nid]
        name = DISPLAY_TAG_NAMES.get(node.tag, f"0x{node.tag:02x}")
        is_root = nid == root_nid

        stroke = "#111111"
        stroke_w = "2.8" if is_root else "2.0"
        fill = "#f5f5f5" if is_root else "#ffffff"

        parts.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_w}"/>'
        )

        # Principal port marker (filled dot)
        px, py = _node_port_pos(cx, cy, r, len(node.ports) - 1, 0)
        parts.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="4.0" fill="#111111"/>')

        # Auxiliary ports (hollow dots)
        for pidx in range(1, len(node.ports)):
            qx, qy = _node_port_pos(cx, cy, r, len(node.ports) - 1, pidx)
            parts.append(
                f'<circle cx="{qx:.2f}" cy="{qy:.2f}" r="3.3" fill="#ffffff" stroke="#111111" stroke-width="1.4"/>'
            )

        if name == "FREF":
            target_name = fn_name_by_id.get(node.meta, f"fn#{node.meta}")
            parts.append(
                f'<text x="{cx:.2f}" y="{cy - 2:.2f}" text-anchor="middle" font-family="Times New Roman, serif" '
                'font-size="11" fill="#111111">FREF</text>'
            )
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 12:.2f}" text-anchor="middle" font-family="Times New Roman, serif" '
                'font-size="9" fill="#111111">'
                f'{target_name}</text>'
            )
        else:
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 4:.2f}" text-anchor="middle" font-family="Times New Roman, serif" '
                'font-size="12" fill="#111111">'
                f'{name}</text>'
            )

    if ast_overview:
        box_x = 16
        box_y = 16
        line_h = 14
        lines = ast_overview
        box_w = 260
        box_h = 14 + len(lines) * line_h
        parts.append(
            f'<rect x="{box_x}" y="{box_y}" width="{box_w}" height="{box_h}" '
            'fill="#ffffff" stroke="#111111" stroke-width="1.2"/>'
        )
        for i, line in enumerate(lines):
            y = box_y + 14 + i * line_h
            size = "11" if i == 0 else "10"
            parts.append(
                f'<text x="{box_x + 8}" y="{y}" font-family="Times New Roman, serif" '
                f'font-size="{size}" fill="#111111">{line}</text>'
            )

    if bridge_lines:
        box_x = 292
        box_y = 16
        line_h = 14
        box_w = 320
        box_h = 14 + len(bridge_lines) * line_h
        parts.append(
            f'<rect x="{box_x}" y="{box_y}" width="{box_w}" height="{box_h}" '
            'fill="#ffffff" stroke="#111111" stroke-width="1.2"/>'
        )
        for i, line in enumerate(bridge_lines):
            y = box_y + 14 + i * line_h
            size = "11" if i == 0 else "10"
            parts.append(
                f'<text x="{box_x + 8}" y="{y}" font-family="Times New Roman, serif" '
                f'font-size="{size}" fill="#111111">{line}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts), stats


def _parse_args(raw: str) -> List[object]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in --args: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("--args must be a JSON array")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Visualize a compiled NELA interaction net as an SVG diagram."
    )
    parser.add_argument("--input", required=True, help="Path to .nela source file")
    parser.add_argument("--fn", default=None, help="Entry function name (default: first def)")
    parser.add_argument(
        "--args",
        default="[]",
        help="JSON array of positional arguments, e.g. '[[5,3,1]]'",
    )
    parser.add_argument("--out", required=True, help="Output SVG path")
    parser.add_argument(
        "--net-mode",
        choices=["unreduced", "reduced"],
        default="unreduced",
        help="unreduced = direct interaction net (recommended), reduced = evaluated value net",
    )
    parser.add_argument(
        "--legend-scope",
        choices=["reachable", "all"],
        default="reachable",
        help="reachable = only entry-call closure, all = every function in the source file",
    )
    parser.add_argument(
        "--bridge-worlds",
        choices=["on", "off"],
        default="on",
        help="on = include NELA-S to net-node translation legend",
    )
    ns = parser.parse_args()

    prog = parse_file(ns.input)
    defs = prog.get("defs", [])
    if not defs:
        raise ValueError(f"No function definitions in {ns.input}")

    fn_name = ns.fn or defs[0]["name"]
    arg_values = _parse_args(ns.args)
    ast_reachable = _ast_reachable_functions(prog, fn_name)
    all_functions = [d["name"] for d in defs]
    all_ids = list(range(len(all_functions)))
    reachable_ids = [i for i, n in enumerate(all_functions) if n in set(ast_reachable)]
    fn_name_by_id: Dict[int, str] = {}

    if ns.net_mode == "reduced":
        _result, bc = compile_and_run(prog, fn_name, *arg_values)
        net, root = bytes_to_net(bc)
    else:
        uc = UnreducedCompiler(prog)
        net, root = uc._compile_fn_body(fn_name)
        fn_name_by_id = {fn_id: name for name, fn_id in uc._fn_cache.items()}

    scope_flag = 1 if ns.legend_scope == "all" else 0
    legend_ids = run_program(_NETVIZ_CORE_PROG, "choose_legend_ids", scope_flag, all_ids, reachable_ids)
    legend_funcs = []
    if isinstance(legend_ids, list):
        for idx in legend_ids:
            i = int(idx)
            if 0 <= i < len(all_functions):
                legend_funcs.append(all_functions[i])

    legend_title = "Function Legend (all defs)" if scope_flag == 1 else "Function Legend (entry closure)"

    overview_lines = [
        legend_title,
        f"entry: {fn_name}",
        f"count: {len(legend_funcs)}",
    ] + [f"- {name}" for name in legend_funcs]
    bridge_lines = _surface_to_net_bridge_lines() if ns.bridge_worlds == "on" else []
    svg, stats = render_svg(
        net,
        root,
        fn_name_by_id=fn_name_by_id,
        ast_overview=overview_lines,
        bridge_lines=bridge_lines,
    )

    out_dir = os.path.dirname(os.path.abspath(ns.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(ns.out, "w", encoding="utf-8") as f:
        f.write(svg)

    print(f"Wrote interaction-net SVG: {ns.out}")
    print(f"Nodes: {len(net._nodes)} | Root: {root} | Entry: {fn_name} | Mode: {ns.net_mode}")
    print(
        "Port refs: "
        f"{stats.total_refs} total, {stats.reciprocal_refs} reciprocal, {stats.inferred_refs} inferred"
    )
    if stats.inferred_refs > 0:
        print("WARNING: inferred links present; this is an approximate drawing, not exact port wiring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
