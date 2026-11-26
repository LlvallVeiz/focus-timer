"""Configuration handling for focus-timer.

Defaults follow the classic Pomodoro Technique (Francesco Cirillo):
25-minute work sessions, 5-minute short breaks, a 15-minute long break
after every 4 completed work sessions.

A JSON config file may override any of these (default location
``~/.config/focus-timer/config.json``; ``--config`` points elsewhere).
CLI flags override the config file, which overrides the defaults.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_WORK_MINUTES = 25
DEFAULT_SHORT_BREAK_MINUTES = 5
DEFAULT_LONG_BREAK_MINUTES = 15
DEFAULT_CYCLES = 4  # work sessions before a long break

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "focus-timer" / "config.json"

# Config-file keys and their matching attribute names.
_CONFIG_KEYS = {
    "work_minutes": "work_minutes",
    "short_break_minutes": "short_break_minutes",
    "long_break_minutes": "long_break_minutes",
    "cycles_before_long_break": "cycles_before_long_break",
    "label": "label",
}


@dataclass
class Config:
    """Effective focus-timer settings."""

    work_minutes: int = DEFAULT_WORK_MINUTES
    short_break_minutes: int = DEFAULT_SHORT_BREAK_MINUTES
    long_break_minutes: int = DEFAULT_LONG_BREAK_MINUTES
    cycles_before_long_break: int = DEFAULT_CYCLES
    label: str = "focus"

    def to_dict(self) -> dict:
        return {
            "work_minutes": self.work_minutes,
            "short_break_minutes": self.short_break_minutes,
            "long_break_minutes": self.long_break_minutes,
            "cycles_before_long_break": self.cycles_before_long_break,
            "label": self.label,
        }


def default_config_path() -> Path:
    """Return the default config file path (``~/.config/focus-timer/config.json``)."""
    return Path(os.path.expanduser("~/.config/focus-timer/config.json"))


def load_config(path: Path | str | None = None) -> Config:
    """Load config from ``path`` (default location if ``None``).

    A missing file yields defaults. Unknown keys are ignored; malformed
    JSON or wrong-typed values raise :class:`ValueError` with a clear
    message so a broken config file fails loudly instead of silently
    falling back.
    """
    cfg = Config()
    cfg_path = Path(path) if path is not None else default_config_path()
    if not cfg_path.exists():
        return cfg
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in config file {cfg_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"config file {cfg_path} must contain a JSON object")
    for key, attr in _CONFIG_KEYS.items():
        if key in raw:
            value = raw[key]
            expected = int if attr != "label" else str
            if not isinstance(value, expected) or isinstance(value, bool):
                raise ValueError(
                    f"config key {key!r} in {cfg_path} must be a {expected.__name__}"
                )
            setattr(cfg, attr, value)
    _validate(cfg)
    return cfg


def merge_config(cfg: Config, **overrides) -> Config:
    """Return a copy of ``cfg`` with any non-``None`` overrides applied.

    Used to layer CLI flags on top of the file config: pass e.g.
    ``merge_config(cfg, work_minutes=args.work)`` where ``args.work`` is
    ``None`` when the flag was not given.
    """
    merged = Config(**cfg.to_dict())
    for key, value in overrides.items():
        if value is not None and hasattr(merged, key):
            setattr(merged, key, value)
    _validate(merged)
    return merged


def _validate(cfg: Config) -> None:
    if cfg.work_minutes <= 0:
        raise ValueError("work_minutes must be positive")
    if cfg.short_break_minutes <= 0:
        raise ValueError("short_break_minutes must be positive")
    if cfg.long_break_minutes <= 0:
        raise ValueError("long_break_minutes must be positive")
    if cfg.cycles_before_long_break < 1:
        raise ValueError("cycles_before_long_break must be >= 1")
