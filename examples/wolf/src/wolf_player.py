#!/usr/bin/env python3
"""wolf_player.py v0.11 — framebuffer-only host for NELA-S Wolf game.

All gameplay logic (movement, doors, minimap, enemies, stats) lives in NELA-S.
Python is only the host boundary for:
  - keyboard input
  - framebuffer rendering (PyGame)
  - terminal fallback output

NELA-S io_print payload shape:
  [frame, minimap, enemies, doors, steps]
"""

import os
import select
import sys
import termios
import tty
import io
import math
import struct
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from nela_parser import parse_file
from nela_runtime import run_program, IOToken

sys.setrecursionlimit(200_000)

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

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

SHADE_COLORS = [
    (32, 32, 32),
    (64, 64, 64),
    (96, 96, 96),
    (160, 160, 160),
    (220, 220, 220),
    (220, 60, 60),
    (58, 44, 32),
    (76, 57, 39),
    (94, 70, 48),
    (112, 82, 56),
    (132, 95, 62),
    (151, 109, 72),
    (171, 123, 84),
    (192, 140, 97),
]
MINIMAP_COLORS = {
    0: (100, 100, 100),
    1: (35, 35, 35),
    2: (80, 220, 120),
    3: (230, 70, 70),
    4: (170, 30, 30),
}
CHARS = ["  ", "··", "▒▒", "▓▓", "██", "@@", "░░", "..", "::", "==", "++", "**", "##", "%%"]

_GAME_NELA = os.path.join(os.path.dirname(_HERE), "wolf_game.nela")
_prog = parse_file(_GAME_NELA)
_INIT_STATE = [1.5, 1.5, 90]

_pygame_screen = None
_pygame_clock = None
_font = None
_pixel_size = 16
_sidebar_width = 240
_audio_ready = False
_sfx = {}


def _split_payload(payload: list):
    if isinstance(payload, list) and len(payload) >= 6:
        return payload[0], payload[1], payload[2], payload[3], payload[4], payload[5]
    if isinstance(payload, list) and len(payload) >= 5:
        return payload[0], payload[1], payload[2], payload[3], payload[4], _INIT_STATE
    return payload, [], [], 0, 0, _INIT_STATE


def _make_tone(freq_hz: float, duration_ms: int, volume: float) -> "pygame.mixer.Sound":
    sample_rate = 22050
    count = max(1, int(sample_rate * duration_ms / 1000))
    amp = int(32767 * volume)
    frames = bytearray()
    for i in range(count):
        v = int(amp * math.sin(2.0 * math.pi * freq_hz * (i / sample_rate)))
        frames.extend(struct.pack("<h", v))

    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(frames))
    data.seek(0)
    return pygame.mixer.Sound(file=data)


def _init_audio() -> bool:
    global _sfx
    if not PYGAME_AVAILABLE:
        return False
    try:
        if pygame.mixer.get_init() is None:
            pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
        _sfx = {
            "step": _make_tone(150.0, 35, 0.20),
            "door": _make_tone(520.0, 95, 0.28),
            "enemy": _make_tone(240.0, 75, 0.24),
        }
        return True
    except Exception as e:
        print(f"⚠️  Audio init failed: {e}", file=sys.stderr)
        _sfx = {}
        return False


def _play_sfx(name: str) -> None:
    if not _audio_ready:
        return
    snd = _sfx.get(name)
    if snd is None:
        return
    try:
        snd.play()
    except Exception:
        pass


def _sound_event(sound_id: int) -> None:
    if sound_id == 0:
        _play_sfx("step")
    elif sound_id == 1:
        _play_sfx("door")
    elif sound_id == 2:
        _play_sfx("enemy")


def _init_gpu_framebuffer() -> bool:
    global _pygame_screen, _pygame_clock, _font
    if not PYGAME_AVAILABLE:
        return False
    try:
        pygame.init()
        sw = 40 * _pixel_size + _sidebar_width
        sh = 21 * _pixel_size
        _pygame_screen = pygame.display.set_mode((sw, sh))
        pygame.display.set_caption("Wolf Game (NELA-S logic + PyGame framebuffer)")
        _pygame_clock = pygame.time.Clock()
        _font = pygame.font.SysFont("monospace", 16)
        return True
    except Exception as e:
        print(f"⚠️  Pygame init failed: {e}", file=sys.stderr)
        return False


def _render_to_gpu(payload: list) -> None:
    if not _pygame_screen or not PYGAME_AVAILABLE:
        return

    frame, minimap, enemies, doors, steps, state = _split_payload(payload)
    surface = pygame.Surface((_pygame_screen.get_width(), _pygame_screen.get_height()))

    for row_idx, row in enumerate(frame):
        for col_idx, shade in enumerate(row):
            shade = min(max(int(shade), 0), len(SHADE_COLORS) - 1)
            rect = pygame.Rect(col_idx * _pixel_size, row_idx * _pixel_size, _pixel_size, _pixel_size)
            pygame.draw.rect(surface, SHADE_COLORS[shade], rect)

    sidebar_x = 40 * _pixel_size
    pygame.draw.rect(surface, (18, 18, 18), pygame.Rect(sidebar_x, 0, _sidebar_width, surface.get_height()))

    cell = 24
    mini_x = sidebar_x + 20
    mini_y = 20
    if isinstance(minimap, list):
        for gy, row in enumerate(minimap):
            if not isinstance(row, list):
                continue
            for gx, cell_v in enumerate(row):
                c = MINIMAP_COLORS.get(int(cell_v), (140, 140, 140))
                pygame.draw.rect(surface, c, pygame.Rect(mini_x + gx * cell, mini_y + gy * cell, cell - 1, cell - 1))

    if _font is not None:
        lines = [
            "NELA-S world state",
            f"Steps: {steps}",
            f"Doors: {doors}",
            f"Enemies: {len(enemies) if isinstance(enemies, list) else 0}",
            "Minimap codes:",
            "2=player 3=alert 4=idle",
            "WASD move, E door, Q quit",
        ]
        y = mini_y + W * cell + 16
        for line in lines:
            txt = _font.render(str(line), True, (220, 220, 220))
            surface.blit(txt, (mini_x, y))
            y += 18

    _pygame_screen.blit(surface, (0, 0))
    pygame.display.flip()
    _pygame_clock.tick(30)


def _getch_gpu() -> str:
    if not PYGAME_AVAILABLE or not _pygame_screen:
        return _getch_terminal()
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return "q"
        if event.type == pygame.KEYDOWN:
            key_map = {
                pygame.K_w: "w", pygame.K_UP: "w",
                pygame.K_s: "s", pygame.K_DOWN: "s",
                pygame.K_a: "a", pygame.K_LEFT: "a",
                pygame.K_d: "d", pygame.K_RIGHT: "d",
                pygame.K_e: "e", pygame.K_q: "q",
            }
            return key_map.get(event.key, "")
    return ""


def _getch_terminal() -> str:
    fd = sys.stdin.fileno()
    if not sys.stdin.isatty():
        ch = sys.stdin.read(1)
        if ch == "\x03" or ch == "":
            return "q"
        return ch
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


def _getch() -> str:
    return _getch_gpu() if _pygame_screen else _getch_terminal()


def _print_frame(payload: list) -> None:
    if _pygame_screen:
        _render_to_gpu(payload)
    else:
        _print_frame_terminal(payload)


def _print_frame_terminal(payload: list) -> None:
    frame, minimap, enemies, doors, steps, _state = _split_payload(payload)
    print("\033[2J\033[H", end="", flush=True)
    for row in frame:
        print("".join(CHARS[int(v)] for v in row))
    print(f"  Steps={steps} Doors={doors} Enemies={len(enemies) if isinstance(enemies, list) else 0}")
    if isinstance(minimap, list) and minimap:
        print("  Minimap (2=P,3=A,4=E):")
        for row in minimap:
            print("   " + " ".join(str(int(v)) for v in row))
    print("  W/S=move  A/D=turn  E=open door  Q=quit")


def main() -> None:
    global _audio_ready
    if _init_gpu_framebuffer():
        print("🎮 GPU Framebuffer READY (PyGame)")
    else:
        print("⚠️  Terminal mode (PyGame unavailable)")

    _audio_ready = _init_audio()
    if _audio_ready:
        print("🔊 Audio READY")

    token = IOToken(_getch, _print_frame, _sound_event)
    run_program(_prog, "game_loop", list(_INIT_STATE), list(MAP), W, token)

    if _pygame_screen:
        pygame.quit()
    print("\033[2J\033[H", end="")
    print("Bye!")


if __name__ == "__main__":
    main()
