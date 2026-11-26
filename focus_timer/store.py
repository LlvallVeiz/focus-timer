"""Session-log and current-session state persistence.

Sessions live in a JSON file (default
``~/.local/share/focus-timer/sessions.json``). The file holds a JSON
object ``{"sessions": [...]}``; each entry is a plain dict with the keys:

    id, label, kind, planned_minutes, started_at, ended_at,
    status ("completed" | "aborted"), actual_seconds

``started_at`` / ``ended_at`` are epoch seconds (floats).

A second file, ``.current.json`` in the same directory, holds the
session currently running under a blocking ``start`` command so that a
separate ``abort`` invocation can mark it aborted. It is written at
session start and removed when the session ends normally.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "focus-timer"
DEFAULT_LOG_PATH = DEFAULT_DATA_DIR / "sessions.json"
CURRENT_FILE_NAME = ".current.json"

KINDS = ("work", "short_break", "long_break")
STATUSES = ("completed", "aborted")


@dataclass
class Session:
    """One recorded focus/break session."""

    id: str
    label: str
    kind: str
    planned_minutes: float
    started_at: float
    ended_at: float | None = None
    status: str = "completed"
    actual_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            id=str(data["id"]),
            label=str(data.get("label", "focus")),
            kind=str(data["kind"]),
            planned_minutes=float(data["planned_minutes"]),
            started_at=float(data["started_at"]),
            ended_at=(
                float(data["ended_at"]) if data.get("ended_at") is not None else None
            ),
            status=str(data.get("status", "completed")),
            actual_seconds=(
                float(data["actual_seconds"])
                if data.get("actual_seconds") is not None
                else None
            ),
        )

    @classmethod
    def new(
        cls,
        label: str,
        kind: str,
        planned_minutes: float,
        started_at: float,
    ) -> "Session":
        return cls(
            id=uuid.uuid4().hex[:12],
            label=label,
            kind=kind,
            planned_minutes=planned_minutes,
            started_at=started_at,
        )


def default_log_path() -> Path:
    return Path(os.path.expanduser("~/.local/share/focus-timer/sessions.json"))


def default_data_dir() -> Path:
    return Path(os.path.expanduser("~/.local/share/focus-timer"))


class SessionStore:
    """JSON-backed session log plus current-session state file."""

    def __init__(self, log_path: Path | str | None = None) -> None:
        self.log_path = Path(log_path) if log_path is not None else default_log_path()
        self.data_dir = self.log_path.parent

    # -- session log ----------------------------------------------------
    def read_all(self) -> list[Session]:
        """Return all logged sessions (empty list if the log does not exist)."""
        if not self.log_path.exists():
            return []
        try:
            raw = json.loads(self.log_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        entries = raw.get("sessions", []) if isinstance(raw, dict) else []
        sessions = []
        for entry in entries:
            try:
                sessions.append(Session.from_dict(entry))
            except (KeyError, TypeError, ValueError):
                continue  # skip a single corrupt entry, keep the rest
        return sessions

    def append(self, session: Session) -> None:
        """Append ``session`` to the log (atomic-ish: write temp, rename)."""
        sessions = self.read_all()
        sessions.append(session)
        self._write(sessions)

    def update(self, session: Session) -> None:
        """Replace the entry with the same id, or append it if absent."""
        sessions = self.read_all()
        for i, existing in enumerate(sessions):
            if existing.id == session.id:
                sessions[i] = session
                break
        else:
            sessions.append(session)
        self._write(sessions)

    def _write(self, sessions: list[Session]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"sessions": [s.to_dict() for s in sessions]}
        tmp = self.log_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.log_path)

    # -- current-session state ------------------------------------------
    def current_path(self) -> Path:
        return self.data_dir / CURRENT_FILE_NAME

    def write_current(self, session: Session) -> None:
        """Record ``session`` as the currently running one."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.current_path().write_text(
            json.dumps(session.to_dict(), indent=2), encoding="utf-8"
        )

    def read_current(self) -> Session | None:
        path = self.current_path()
        if not path.exists():
            return None
        try:
            return Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def clear_current(self) -> None:
        try:
            self.current_path().unlink()
        except FileNotFoundError:
            pass
