"""Tiny dependency-free console styling for the Argos CLI.

Why hand-roll this instead of pulling in ``rich``/``colorama``? The SDK's whole
pitch is "lightweight, no surprise dependencies." A few ANSI escape codes behind
a capability check give us colored ``[ OK ]`` / ``[WARN]`` / ``[MISS]`` markers
and headings without adding anything to install.

Color is automatically disabled when output isn't a terminal (e.g. piped to a
file or CI logs) or when the ``NO_COLOR`` convention is set, so the text stays
readable everywhere.
"""

from __future__ import annotations

import os
import sys

# Belt-and-suspenders: never let an un-encodable glyph crash the CLI. On a
# legacy Windows code page (cp1252) a stray Unicode char would otherwise raise
# UnicodeEncodeError mid-report; replacing is far better than aborting. We also
# force stdin to UTF-8 so a piped BOM decodes to a single U+FEFF the menu can
# strip, rather than the mojibake "ï»¿" cp1252 would produce.
try:  # pragma: no cover - depends on the host console
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass
try:  # pragma: no cover - depends on the host console
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass


def _unicode_ok() -> bool:
    """True only when stdout can actually render non-ASCII box/arrow glyphs."""

    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in enc


# Pretty glyphs when the terminal is UTF-8, safe ASCII otherwise.
_U = _unicode_ok()
RULE = "─" if _U else "-"   # heading underline
ARROW = "↳" if _U else ">"  # guidance prefix
DASH = "—" if _U else "-"   # detail separator
DOT = "·" if _U else "|"    # inline list separator
CHECK = "✅" if _U else "[ OK ]"  # friendly "done" mark for confirmations

# --- ANSI codes -----------------------------------------------------------
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"


def _supports_color() -> bool:
    """True when it's safe to emit ANSI color to stdout.

    Honors the NO_COLOR convention and skips color when stdout isn't a TTY. On
    Windows we first try to switch the console into ANSI ("virtual terminal")
    mode; modern Windows Terminal already supports it, classic conhost needs the
    nudge.
    """

    if os.environ.get("NO_COLOR") is not None:
        return False
    if not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        return _enable_windows_ansi()
    return True


def _enable_windows_ansi() -> bool:
    """Turn on ANSI escape processing for the current Windows console."""

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # -11 = STD_OUTPUT_HANDLE; 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING.
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except Exception:  # noqa: BLE001 - any failure just means "no color"
        return False


_COLOR = _supports_color()


def _paint(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}" if _COLOR else text


# --- public helpers -------------------------------------------------------
def bold(text: str) -> str:
    return _paint(text, _BOLD)


def dim(text: str) -> str:
    return _paint(text, _DIM)


def cyan(text: str) -> str:
    return _paint(text, _CYAN)


def green(text: str) -> str:
    return _paint(text, _GREEN)


def yellow(text: str) -> str:
    return _paint(text, _YELLOW)


def red(text: str) -> str:
    return _paint(text, _RED)


# Status markers — fixed width so columns line up in the report.
MARK_OK = _paint("[ OK ]", _GREEN)
MARK_WARN = _paint("[WARN]", _YELLOW)
MARK_MISS = _paint("[MISS]", _RED)
MARK_INFO = _paint("[ -- ]", _CYAN)


def heading(text: str) -> None:
    """Print a bold section heading with an underline rule."""

    print()
    print(bold(text))
    print(dim(RULE * max(len(text), 8)))


def line(marker: str, label: str, detail: str = "") -> None:
    """Print one status row: ``[ OK ] Docker — 27.0.3``."""

    text = f"{marker} {label}"
    if detail:
        text += f" {dim(DASH)} {detail}"
    print(text)


# --- interactive input helpers (used by the menu, C4/C5) ------------------
# All three degrade gracefully on EOF (e.g. piped/non-interactive stdin) by
# returning the default, so the menu never crashes when there's no real TTY.
def prompt(question: str, default: str = "") -> str:
    """Ask for a line of text; return the default on empty input or EOF."""

    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{cyan('?')} {question}{suffix}: ").strip()
    except EOFError:
        print()
        return default
    return answer or default


def confirm(question: str, default: bool = True) -> bool:
    """Ask a yes/no question; return ``default`` on empty input or EOF."""

    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"{cyan('?')} {question} [{hint}]: ").strip().lower()
    except EOFError:
        print()
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def pause(message: str = "Press Enter to return to the menu...") -> None:
    """Block until the user hits Enter (no-op when there's no TTY)."""

    try:
        input("\n" + dim(message))
    except EOFError:
        pass
