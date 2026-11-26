"""Cycle logic and statistics.

Pomodoro cycle rule (Francesco Cirillo): after every
``cycles_before_long_break`` completed work sessions, the following
break is a long break; otherwise it is a short break.

All aggregation here is pure: it takes a list of :class:`Session`
objects and a reference "today" date, so it is easy to test.
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict

from .config import Config
from .store import Session

WORK = "work"
SHORT_BREAK = "short_break"
LONG_BREAK = "long_break"


def next_break_kind(completed_work_sessions: int, cfg: Config) -> str:
    """Return the break kind due after ``completed_work_sessions`` works.

    A long break is due exactly when the count is a multiple of
    ``cycles_before_long_break`` (e.g. after the 4th, 8th, ... session
    with the default of 4); every other break is short.
    """
    if completed_work_sessions > 0 and (
        completed_work_sessions % cfg.cycles_before_long_break == 0
    ):
        return LONG_BREAK
    return SHORT_BREAK


def planned_minutes(kind: str, cfg: Config) -> float:
    """Planned minutes for a session of ``kind`` under ``cfg``."""
    if kind == WORK:
        return float(cfg.work_minutes)
    if kind == SHORT_BREAK:
        return float(cfg.short_break_minutes)
    if kind == LONG_BREAK:
        return float(cfg.long_break_minutes)
    raise ValueError(f"unknown session kind: {kind!r}")


def session_end(started_at: float, planned_min: float) -> float:
    """Compute a session's planned end time from its start.

    End = start + planned_seconds, computed from the clock — never from
    counting sleeps, so a paused or lagging countdown still ends on time.
    """
    return started_at + planned_min * 60.0


def _day(ts: float) -> _dt.date:
    return _dt.datetime.fromtimestamp(ts).date()


def _today(date: _dt.date | None) -> _dt.date:
    return date or _dt.date.today()


def completed_work(sessions: list[Session]) -> list[Session]:
    """Completed work sessions only."""
    return [s for s in sessions if s.kind == WORK and s.status == "completed"]


def compute_streak(sessions: list[Session], today: _dt.date | None = None) -> int:
    """Consecutive days ending today (or ``today``) with >= 1 completed work session.

    A day with no completed work session breaks the streak. A gap day
    resets the count; only the unbroken run ending on ``today`` counts.
    If today itself has no completed work session yet, the streak is the
    run ending yesterday (0 if yesterday also had none).
    """
    ref = _today(today)
    days_with_work = {
        _day(s.started_at) for s in completed_work(sessions)
    }
    streak = 0
    cursor = ref
    # If today has no completed work yet, the streak may still be alive
    # from yesterday — start counting from the most recent day with work,
    # but only if that day is today or yesterday.
    if cursor not in days_with_work:
        cursor -= _dt.timedelta(days=1)
        if cursor not in days_with_work:
            return 0
    while cursor in days_with_work:
        streak += 1
        cursor -= _dt.timedelta(days=1)
    return streak


def compute_stats(
    sessions: list[Session], today: _dt.date | None = None
) -> dict:
    """Aggregate stats for ``today`` (defaults to the real today).

    Returns a dict with: ``today_focus_minutes`` (completed work only),
    ``today_completed``, ``today_aborted``, ``completed_total``,
    ``aborted_total``, ``streak`` (days), ``per_label_minutes``
    (completed work minutes keyed by label), ``week_focus_minutes``
    (completed work minutes for the Mon–Sun week containing today),
    and ``today_date`` as an ISO string.
    """
    ref = _today(today)
    week_start = ref - _dt.timedelta(days=ref.weekday())  # Monday
    week_end = week_start + _dt.timedelta(days=7)  # exclusive

    today_sessions = [s for s in sessions if _day(s.started_at) == ref]
    week_work = [
        s
        for s in completed_work(sessions)
        if week_start <= _day(s.started_at) < week_end
    ]

    per_label: dict[str, float] = defaultdict(float)
    for s in completed_work(sessions):
        if _day(s.started_at) == ref:
            per_label[s.label] += (s.actual_seconds or 0.0) / 60.0

    def _minutes(work_sessions: list[Session]) -> float:
        return sum((s.actual_seconds or 0.0) for s in work_sessions) / 60.0

    return {
        "today_date": ref.isoformat(),
        "today_focus_minutes": _minutes(
            [s for s in today_sessions if s.kind == WORK and s.status == "completed"]
        ),
        "today_completed": sum(1 for s in today_sessions if s.status == "completed"),
        "today_aborted": sum(1 for s in today_sessions if s.status == "aborted"),
        "completed_total": sum(1 for s in sessions if s.status == "completed"),
        "aborted_total": sum(1 for s in sessions if s.status == "aborted"),
        "streak": compute_streak(sessions, ref),
        "per_label_minutes": dict(sorted(per_label.items())),
        "week_focus_minutes": _minutes(week_work),
    }


def count_completed_work_today(
    sessions: list[Session], today: _dt.date | None = None
) -> int:
    """How many completed work sessions happened on ``today``."""
    ref = _today(today)
    return sum(
        1
        for s in completed_work(sessions)
        if _day(s.started_at) == ref
    )
