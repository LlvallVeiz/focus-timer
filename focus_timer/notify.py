"""Desktop notifications (best effort).

Tries ``notify-send`` (libnotify, common on Linux desktops) if it is on
``PATH``. If it is missing — macOS, Windows, headless servers — this is
a silent no-op; the caller always also prints a terminal bell so the
user still gets a signal.
"""

from __future__ import annotations

import shutil
import subprocess


def notify(title: str, message: str) -> bool:
    """Send a desktop notification. Returns True if one was actually sent."""
    notify_send = shutil.which("notify-send")
    if notify_send is None:
        return False
    try:
        result = subprocess.run(
            [notify_send, title, message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def alert(title: str, message: str) -> None:
    """Notify on the desktop (if possible) and always ring the terminal bell."""
    notify(title, message)
    print("\a", end="", flush=True)  # terminal bell
