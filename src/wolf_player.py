#!/usr/bin/env python3
"""wolf_player.py v0.9 — Wolfenstein harness for NELA-S v0.9.

Python provides ZERO precomputed data and ZERO game logic.  This file is
strictly I/O + the unavoidable host boundary:

  MAP    — initial level data (passed to NELA game_loop as first map)
  CHARS  — print table: shade integer → block char
  _getch — raw single keypress  (cannot be expressed in NELA-S)
  main() — create IOToken, call NELA game_loop, done

v0.9: the full game loop (render → read key → update → recurse) runs inside
NELA-S (wolf_game.nela :: game_loop).  Python only injects two callbacks into
an IOToken and hands control to NELA.
"""

import os
import select
import sys
import termios
import tty

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from nela_parser import parse_file
from nela_runtime import run_program, IOToken

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

# ── Shade integer → terminal character (print table, not logic) ───────────────
# NELA-S returns: 0=ceiling  1=floor  2=wall-far  3=wall-mid  4=wall-close
CHARS = ["  ", "··", "▒▒", "▓▓", "██"]

# ── Load NELA program ─────────────────────────────────────────────────────────
_GAME_NELA = os.path.join(os.path.dirname(_HERE), "examples", "wolf_game.nela")
_prog = parse_file(_GAME_NELA)

# ── Initial state [px, py, angle]  (float positions, integer angle) ───────────
_INIT_STATE = [1.5, 1.5, 90]


# ── I/O callbacks (injected into IOToken) ────────────────────────────────────

def _getch() -> str:
    """Raw single keypress; arrow keys mapped to wasd."""
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
        if ch == "\x03":
            return "q"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _print_frame(frame: list) -> None:
    """Print a NELA-S frame (list[list[int]]) to the terminal."""
    print("\033[2J\033[H", end="", flush=True)
    for row in frame:
        print("".join(CHARS[v] for v in row))
    print("  W/S=move  A/D=turn  E=open door  Q=quit")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    token = IOToken(_getch, _print_frame)
    run_program(_prog, "game_loop", list(_INIT_STATE), list(MAP), W, token)
    print("\033[2J\033[H", end="")
    print("Bye!")


if __name__ == "__main__":
    main()
