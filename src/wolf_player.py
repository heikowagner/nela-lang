#!/usr/bin/env python3
"""wolf_player.py v0.10 — GPU-ready Wolfenstein harness for NELA-S.

Python provides ZERO precomputed data and ZERO game logic.  This file is
strictly I/O + the unavoidable host boundary:

  MAP    — initial level data (passed to NELA game_loop as first map)
  DISPLAY — GPU framebuffer (Pygame) or terminal (fallback)
  _getch — raw single keypress  (cannot be expressed in NELA-S)
  main() — create IOToken, call NELA game_loop, done

v0.10: Pygame GPU rendering backend + terminal fallback
  - Attempts to use Pygame for GPU framebuffer
  - Falls back to ANSI terminal if Pygame unavailable
  - Same NELA-S logic works with either renderer
  - Architecture: NELA-S computation → Python rendering → host GPU/terminal
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

# ── Try to import Pygame (GPU rendering) ───────────────────────────────────
try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

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

# ── Shade integer → color (for GPU rendering) ───────────────────────────────
# NELA-S returns: 0=ceiling  1=floor  2=wall-far  3=wall-mid  4=wall-close
SHADE_COLORS = [
    (32, 32, 32),      # 0=ceiling  (dark gray)
    (64, 64, 64),      # 1=floor    (gray)
    (96, 96, 96),      # 2=wall-far (light gray)
    (160, 160, 160),   # 3=wall-mid (lighter gray)
    (220, 220, 220),   # 4=wall-close (white)
]

# ── Shade integer → terminal character (fallback) ───────────────────────────
CHARS = ["  ", "··", "▒▒", "▓▓", "██"]

# ── Load NELA program ─────────────────────────────────────────────────────────
_GAME_NELA = os.path.join(os.path.dirname(_HERE), "examples", "wolf_game.nela")
_prog = parse_file(_GAME_NELA)

# ── Initial state [px, py, angle]  (float positions, integer angle) ───────────
_INIT_STATE = [1.5, 1.5, 90]

# ── GPU framebuffer (Pygame) ───────────────────────────────────────────────────
_pygame_screen = None
_pygame_clock = None
_pixel_size = 16  # Each game pixel = 16x16 physical pixels


# ── GPU Renderer (Pygame) ──────────────────────────────────────────────────────

def _init_gpu_framebuffer() -> bool:
    """Initialize Pygame GPU framebuffer. Returns True if successful."""
    global _pygame_screen, _pygame_clock
    if not PYGAME_AVAILABLE:
        return False
    try:
        pygame.init()
        # 40 cols × 21 rows × 16px per cell = 640×336 window
        screen_width = 40 * _pixel_size
        screen_height = 21 * _pixel_size
        _pygame_screen = pygame.display.set_mode((screen_width, screen_height))
        pygame.display.set_caption("Wolf Game (GPU Rendered)")
        _pygame_clock = pygame.time.Clock()
        return True
    except Exception as e:
        print(f"⚠️  Pygame init failed: {e}", file=sys.stderr)
        return False


def _render_to_gpu(frame: list) -> None:
    """Render frame to GPU framebuffer using Pygame."""
    global _pygame_screen
    if not _pygame_screen or not PYGAME_AVAILABLE:
        return
    
    surface = pygame.Surface((_pygame_screen.get_width(), _pygame_screen.get_height()))
    
    # Each cell in frame is a shade integer; render as colored rectangle
    for row_idx, row in enumerate(frame):
        for col_idx, shade in enumerate(row):
            shade = min(max(shade, 0), len(SHADE_COLORS) - 1)
            color = SHADE_COLORS[shade]
            rect = pygame.Rect(
                col_idx * _pixel_size,
                row_idx * _pixel_size,
                _pixel_size,
                _pixel_size
            )
            pygame.draw.rect(surface, color, rect)
    
    _pygame_screen.blit(surface, (0, 0))
    pygame.display.flip()
    _pygame_clock.tick(30)  # 30 FPS


def _getch_gpu() -> str:
    """Read keyboard input from Pygame event queue."""
    if not PYGAME_AVAILABLE or not _pygame_screen:
        return _getch_terminal()
    
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return "q"
        elif event.type == pygame.KEYDOWN:
            key_map = {
                pygame.K_w: "w", pygame.K_UP: "w",
                pygame.K_s: "s", pygame.K_DOWN: "s",
                pygame.K_a: "a", pygame.K_LEFT: "a",
                pygame.K_d: "d", pygame.K_RIGHT: "d",
                pygame.K_e: "e",
                pygame.K_q: "q",
            }
            return key_map.get(event.key, "")
    
    return ""


def _getch_terminal() -> str:
    """Raw single keypress (terminal); arrow keys mapped to wasd."""
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


# ── I/O callbacks (injected into IOToken) ────────────────────────────────────

def _getch() -> str:
    """Unified keyboard input: GPU or terminal."""
    if _pygame_screen:
        return _getch_gpu()
    else:
        return _getch_terminal()


def _print_frame(frame: list) -> None:
    """Unified frame rendering: GPU or terminal."""
    if _pygame_screen:
        _render_to_gpu(frame)
    else:
        _print_frame_terminal(frame)


def _print_frame_terminal(frame: list) -> None:
    """Render frame to ANSI terminal (fallback)."""
    print("\033[2J\033[H", end="", flush=True)
    for row in frame:
        print("".join(CHARS[v] for v in row))
    print("  W/S=move  A/D=turn  E=open door  Q=quit")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Try GPU first, fall back to terminal
    use_gpu = _init_gpu_framebuffer()
    if use_gpu:
        print("🎮 GPU Framebuffer READY (Pygame)")
    else:
        print("⚠️  Terminal mode (Pygame unavailable)")
    
    token = IOToken(_getch, _print_frame)
    run_program(_prog, "game_loop", list(_INIT_STATE), list(MAP), W, token)
    
    if _pygame_screen:
        pygame.quit()
    
    print("\033[2J\033[H", end="")
    print("Bye!")


if __name__ == "__main__":
    main()
