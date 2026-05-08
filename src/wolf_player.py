#!/usr/bin/env python3
"""wolf_player.py v0.8 — Wolfenstein harness for NELA-S v0.8.

NELA-S now has array/aset/len builtins.
Python provides ZERO precomputed data.  This file is strictly I/O:

  MAP    — initial level data (copied to mutable map_data in game loop)
  CHARS  — print table: shade integer → block char
  _getch — raw keyboard  (cannot be expressed in NELA-S)
  main() — game loop: key → NELA update/use_door → NELA render → print

v0.8 new: pressing E calls NELA use_door, which returns an updated map.
The updated map is kept in map_data and passed to subsequent render/update calls.
"""

import os
import select
import sys
import termios
import tty

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from nela_parser import parse_file
from nela_runtime import run_program

sys.setrecursionlimit(200_000)

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

# ── Initial state [px, py, angle]  (float positions, integer angle) ───────────
_INIT_STATE = [1.5, 1.5, 90]


# ── Print one frame (pure I/O) ────────────────────────────────────────────────
def render(state: list) -> None:
    frame = run_program(
        _prog, "render_frame",
        MAP, W, state[0], state[1], state[2],
        HALF_FOV, NCOLS, SCREEN_H,
    )
    print("\033[2J\033[H", end="", flush=True)
    for row in frame:
        print("".join(CHARS[v] for v in row))
    print(f"  pos=({state[0]:.2f},{state[1]:.2f})  angle={state[2]}\u00b0"
          "  W/S=move  A/D=turn  E=open door  Q=quit")


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
    state    = list(_INIT_STATE)
    map_data = list(MAP)          # mutable copy — use_door can open walls
    render(state, map_data)
    while True:
        ch = _getch()
        if ch in ("q", "Q", "\x03"):
            print("\033[2J\033[H", end="")
            print("Bye!")
            break
        if ch in ("e", "E"):
            map_data = run_program(_prog, "use_door", state, map_data, W)
            render(state, map_data)
            continue
        key = _KEY_MAP.get(ch)
        if key is not None:
            state = run_program(_prog, "update", state, key, map_data, W)
            render(state, map_data)


if __name__ == "__main__":
    main()
