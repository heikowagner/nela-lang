#!/usr/bin/env python3
"""wolf_player.py — Wolfenstein harness for NELA-S.

All game logic (raycasting, movement, collision, frame assembly, shading)
runs in pure NELA-S (wolf_game.nela).

This file is I/O + unavoidable host-only data only:
  • SIN_TAB / COS_TAB  — precomputed because NELA-S has no float literals
  • MAP                — input data
  • CHARS[]            — integer shade → terminal character (a print table, not logic)
  • _getch()           — raw keyboard (cannot be expressed in NELA-S)
  • main()             — game loop: read key → call NELA update → call NELA render → print
"""

import math
import os
import select
import sys
import termios
import tty

# ── Python path ───────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from nela_parser import parse_file
from nela_runtime import run_program

# Deep NELA recursion (render_frame > make_frame > frame_row > frame_cell
#                       + render_cols > ray_march > norm_angle stacked)
sys.setrecursionlimit(100_000)

# ── Trig tables (Python only because NELA-S has no float literals) ────────────
_SCALE = 64
SIN_TAB = [round(math.sin(math.radians(a)) * _SCALE) for a in range(360)]
COS_TAB = [round(math.cos(math.radians(a)) * _SCALE) for a in range(360)]

# ── Map data ──────────────────────────────────────────────────────────────────
MAP = [
    1, 1, 1, 1, 1, 1, 1, 1,
    1, 0, 0, 0, 0, 0, 0, 1,
    1, 0, 1, 1, 0, 1, 0, 1,
    1, 0, 1, 0, 0, 0, 0, 1,
    1, 0, 0, 0, 1, 0, 0, 1,
    1, 0, 1, 0, 0, 1, 0, 1,
    1, 0, 0, 0, 0, 0, 0, 1,
    1, 1, 1, 1, 1, 1, 1, 1,
]
W = 8

# ── Render config ─────────────────────────────────────────────────────────────
NCOLS    = 40
HALF_FOV = 20
SCREEN_H = 21

# ── Shade integer → terminal character (print table, not logic) ───────────────
# NELA-S returns: 0=ceiling  1=floor  2=wall-far  3=wall-mid  4=wall-close
CHARS = ["  ", "\u00b7\u00b7", "\u2592\u2592", "\u2593\u2593", "\u2588\u2588"]

# ── Load NELA program ─────────────────────────────────────────────────────────
_GAME_NELA = os.path.join(os.path.dirname(_HERE), "examples", "wolf_game.nela")
_prog = parse_file(_GAME_NELA)

# ── Initial state [px, py, angle] ─────────────────────────────────────────────
_INIT_STATE = [1 * _SCALE + _SCALE // 2, 1 * _SCALE + _SCALE // 2, 90]


# ── Print one frame (pure I/O — no logic) ────────────────────────────────────
def render(state: list) -> None:
    frame = run_program(
        _prog, "render_frame",
        MAP, W, SIN_TAB, COS_TAB,
        state[0], state[1], state[2],
        HALF_FOV, NCOLS, SCREEN_H,
    )
    print("\033[2J\033[H", end="", flush=True)
    for row in frame:
        print("".join(CHARS[v] for v in row))
    cx, cy = state[0] // _SCALE, state[1] // _SCALE
    print(f"  cell=({cx},{cy})  angle={state[2]}\u00b0"
          "  W/S=move  A/D=turn  Q=quit")


# ── Raw single keypress (must be Python) ─────────────────────────────────────
def _getch() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            if select.select([sys.stdin], [], [], 0.05)[0]:
                ch2 = sys.stdin.read(1)
                if ch2 == "[" and select.select([sys.stdin], [], [], 0.05)[0]:
                    ch3 = sys.stdin.read(1)
                    return {"A": "w", "B": "s", "C": "d", "D": "a"}.get(ch3, "")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


_KEY_MAP = {"w": 0, "W": 0, "s": 1, "S": 1, "a": 2, "A": 2, "d": 3, "D": 3}


# ── Game loop (I/O only) ──────────────────────────────────────────────────────
def main() -> None:
    state = list(_INIT_STATE)
    render(state)
    while True:
        ch = _getch()
        if ch in ("q", "Q", "\x03"):
            print("\033[2J\033[H", end="")
            print("Bye!")
            break
        key = _KEY_MAP.get(ch)
        if key is not None:
            state = run_program(
                _prog, "update",
                state, key, MAP, W, SIN_TAB, COS_TAB,
            )
            render(state)


if __name__ == "__main__":
    main()

# ── Python path ───────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from nela_parser import parse_file
from nela_runtime import run_program

# Deep NELA recursion (render_cols × ray_march × norm_angle stacked)
sys.setrecursionlimit(50_000)

# ── Trig tables  sin_tab[a] = round(sin(a°) × 64) ────────────────────────────
_SCALE = 64
SIN_TAB = [round(math.sin(math.radians(a)) * _SCALE) for a in range(360)]
COS_TAB = [round(math.cos(math.radians(a)) * _SCALE) for a in range(360)]

# ── Map (8×8, 1=wall, 0=passable) ─────────────────────────────────────────────
#   1 1 1 1 1 1 1 1
#   1 . . . . . . 1
#   1 . # # . # . 1
#   1 . # . . . . 1
#   1 . . . # . . 1
#   1 . # . . # . 1
#   1 . . . . . . 1
#   1 1 1 1 1 1 1 1
MAP = [
    1, 1, 1, 1, 1, 1, 1, 1,
    1, 0, 0, 0, 0, 0, 0, 1,
    1, 0, 1, 1, 0, 1, 0, 1,
    1, 0, 1, 0, 0, 0, 0, 1,
    1, 0, 0, 0, 1, 0, 0, 1,
    1, 0, 1, 0, 0, 1, 0, 1,
    1, 0, 0, 0, 0, 0, 0, 1,
    1, 1, 1, 1, 1, 1, 1, 1,
]
W = 8  # map width in cells

# ── Renderer config ────────────────────────────────────────────────────────────
NCOLS    = 40   # ray columns  → terminal width = 80 chars (2 per column)
HALF_FOV = 20   # half field-of-view in degrees  → total 40°
SCREEN_H = 21   # rows for the 3-D view

# ── Load NELA program ─────────────────────────────────────────────────────────
_GAME_NELA = os.path.join(os.path.dirname(_HERE), "examples", "wolf_game.nela")
_prog = parse_file(_GAME_NELA)

# ── Initial state [px, py, angle] ─────────────────────────────────────────────
# Cell (1,1) centre, facing east (angle 90° in our convention)
_INIT_STATE = [1 * _SCALE + _SCALE // 2,   # px
               1 * _SCALE + _SCALE // 2,   # py
               90]                          # angle (90° = east)

# ── Wall shading ──────────────────────────────────────────────────────────────
def _shade(h: int) -> str:
    if h >= SCREEN_H * 3 // 4:
        return "\u2588\u2588"  # very close / full block
    if h >= SCREEN_H // 2:
        return "\u2593\u2593"  # medium
    return "\u2592\u2592"      # far


# ── Render one frame ──────────────────────────────────────────────────────────
def render(state: list) -> None:
    px, py, angle = state[0], state[1], state[2]
    heights = run_program(
        _prog, "render_cols",
        MAP, W, SIN_TAB, COS_TAB,
        px, py, angle, HALF_FOV, NCOLS, 0,
    )

    mid = SCREEN_H // 2
    lines = []
    for row in range(SCREEN_H):
        line = ""
        for h in heights:
            h = min(h, SCREEN_H)
            half = h // 2
            if abs(row - mid) <= half:
                line += _shade(h)
            elif row < mid:
                line += "  "    # ceiling
            else:
                line += "\u00b7\u00b7"   # floor  (··)
        lines.append(line)

    print("\033[2J\033[H", end="", flush=True)
    print("\n".join(lines))
    cx, cy = px // _SCALE, py // _SCALE
    print(
        f"  cell=({cx},{cy})  angle={angle}\u00b0"
        "  W/S=move  A/D=turn  Q=quit"
    )


# ── Raw single keypress ───────────────────────────────────────────────────────
def _getch() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        # Arrow key escape sequences: ESC [ A/B/C/D
        if ch == "\x1b":
            if select.select([sys.stdin], [], [], 0.05)[0]:
                ch2 = sys.stdin.read(1)
                if ch2 == "[" and select.select([sys.stdin], [], [], 0.05)[0]:
                    ch3 = sys.stdin.read(1)
                    return {"A": "w", "B": "s", "C": "d", "D": "a"}.get(ch3, "")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


_KEY_MAP = {"w": 0, "W": 0, "s": 1, "S": 1, "a": 2, "A": 2, "d": 3, "D": 3}


# ── Game loop ─────────────────────────────────────────────────────────────────
def main() -> None:
    state = list(_INIT_STATE)
    render(state)
    while True:
        ch = _getch()
        if ch in ("q", "Q", "\x03"):          # Q or Ctrl-C
            print("\033[2J\033[H", end="")
            print("Bye!")
            break
        key = _KEY_MAP.get(ch)
        if key is not None:
            state = run_program(
                _prog, "update",
                state, key, MAP, W, SIN_TAB, COS_TAB,
            )
            render(state)


if __name__ == "__main__":
    main()
