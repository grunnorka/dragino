#!/usr/bin/env python3
"""On-screen prompts for hardware steps (switch positions, RESET presses).

Hardware steps must not be missed, so every prompt is shown three ways:
a blocking desktop dialog, a desktop notification, and a terminal banner.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

WIDTH = 74


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _banner(title: str, lines: list[str]) -> None:
    bar = "=" * WIDTH
    print(f"\n{bar}", flush=True)
    print(f"  ACTION NEEDED ON THE DEVICE: {title}".upper(), flush=True)
    print(bar, flush=True)
    for line in lines:
        print(f"  {line}", flush=True)
    print(f"{bar}\n", flush=True)


def _notify(title: str, lines: list[str]) -> None:
    if not shutil.which("notify-send"):
        return
    body = " / ".join(line for line in lines if line.strip())
    subprocess.run(
        [
            "notify-send",
            "--urgency=critical",
            "--app-name=Dragino flasher",
            f"ACTION NEEDED: {title}",
            body,
        ],
        check=False,
    )


def _dialog(
    title: str, lines: list[str], ok_label: str, cancel_label: str
) -> bool | None:
    if not shutil.which("zenity"):
        return None
    text = "<big><b>{}</b></big>\n\n{}".format(
        _escape(title), _escape("\n".join(lines))
    )
    proc = subprocess.run(
        [
            "zenity",
            "--question",
            "--title=Dragino: action needed",
            f"--text={text}",
            "--width=560",
            f"--ok-label={ok_label}",
            f"--cancel-label={cancel_label}",
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def step(
    title: str,
    lines: list[str],
    ok_label: str = "Done, continue",
    cancel_label: str = "Abort",
) -> None:
    """Show a blocking prompt; exit the program if the user aborts."""
    _banner(title, lines)
    _notify(title, lines)
    answer = _dialog(title, lines, ok_label, cancel_label)
    if answer is None:
        # No desktop dialog available: fall back to the terminal.
        try:
            reply = input("  Press ENTER when done ('a' to abort): ").strip().lower()
        except EOFError:
            reply = ""
        answer = reply != "a"
    if not answer:
        print("Aborted by user.", file=sys.stderr)
        raise SystemExit(1)
    print(f"-> confirmed: {title}\n", flush=True)


def info(title: str, lines: list[str]) -> None:
    """Show a non-blocking notification plus terminal banner."""
    _banner(title, lines)
    _notify(title, lines)
