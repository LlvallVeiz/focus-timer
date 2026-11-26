"""Command-line interface for focus-timer.

Commands:
    start   run a work session followed by its break (blocking)
    status  show the running session (if any), the next session, today's totals
    stats   today's focus minutes, streak, per-label totals, this week total
    log     list recent sessions
    abort   mark the running session aborted

Global options (before the subcommand):
    --config PATH     config file (default ~/.config/focus-timer/config.json)
    --data-dir DIR    data dir holding sessions.json / .current.json
                      (default ~/.local/share/focus-timer; also honoured via
                      the FOCUS_TIMER_DATA_DIR environment variable)

Speeding up real waiting (manual testing only):
    --fast            run the clock 60x faster
    FOCUS_TIMER_SPEED=N   run the clock Nx faster (e.g. 60)
    start --dry-run   no waiting at all; the session completes instantly
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

from . import __version__
from .clock import Clock
from .config import Config, default_config_path, load_config, merge_config
from .engine import (
    LONG_BREAK,
    SHORT_BREAK,
    WORK,
    compute_stats,
    count_completed_work_today,
    next_break_kind,
    planned_minutes,
    session_end,
)
from .notify import alert
from .store import Session, SessionStore, default_data_dir

FAST_SPEED = 60.0  # --fast runs the clock 60x faster


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fmt_duration(seconds: float) -> str:
    """Format seconds as MM:SS (or H:MM:SS once past an hour)."""
    total = max(0, int(seconds + 0.5))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _fmt_time(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _speed_from_env_and_flag(fast: bool) -> float:
    if fast:
        return FAST_SPEED
    raw = os.environ.get("FOCUS_TIMER_SPEED", "").strip()
    if not raw:
        return 1.0
    try:
        speed = float(raw)
    except ValueError:
        print(f"warning: ignoring invalid FOCUS_TIMER_SPEED={raw!r}", file=sys.stderr)
        return 1.0
    return speed if speed > 0 else 1.0


def _resolve_data_dir(args) -> str:
    if getattr(args, "data_dir", None):
        return args.data_dir
    env_dir = os.environ.get("FOCUS_TIMER_DATA_DIR", "").strip()
    if env_dir:
        return env_dir
    return str(default_data_dir())


def _make_store(args) -> SessionStore:
    from pathlib import Path

    return SessionStore(Path(_resolve_data_dir(args)) / "sessions.json")


def _load_effective_config(args, **overrides) -> Config:
    cfg_path = getattr(args, "config", None) or default_config_path()
    try:
        cfg = load_config(cfg_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    return merge_config(cfg, **overrides)


# ---------------------------------------------------------------------------
# session runner
# ---------------------------------------------------------------------------

def run_session(
    store: SessionStore,
    cfg: Config,
    clock: Clock,
    kind: str,
    label: str,
    planned_min: float,
    *,
    dry_run: bool = False,
    speed: float = 1.0,
    show_countdown: bool = True,
) -> Session:
    """Run one session (work or break), blocking until it ends.

    The session end is computed up front as ``start + planned_seconds``
    from the clock — never from counting sleeps. ``speed`` scales how
    fast real time passes for the countdown (manual testing); the
    planned duration itself is unchanged. Returns the finished session
    (already appended to the log, unless it was aborted from outside —
    in that case the ``abort`` command already logged it).
    """
    started = clock.now()
    planned_seconds = planned_min * 60.0
    end = session_end(started, planned_min)
    session = Session.new(label=label, kind=kind, planned_minutes=planned_min,
                          started_at=started)
    store.write_current(session)

    kind_label = {"work": "Work", "short_break": "Short break",
                  "long_break": "Long break"}[kind]
    print(f"{kind_label} started: {label} — {_fmt_duration(planned_seconds)} "
          f"(ends {_fmt_time(end)})")

    if dry_run:
        session.ended_at = end
        session.status = "completed"
        session.actual_seconds = planned_seconds
        store.append(session)
        store.clear_current()
        print(f"{kind_label} complete (dry run, no waiting).")
        return session

    aborted_externally = False
    try:
        while True:
            # Virtual elapsed time: real elapsed scaled by the speed factor.
            virtual_now = started + (clock.now() - started) * speed
            remaining = end - virtual_now
            if remaining <= 0:
                break
            if show_countdown:
                print(f"\r{kind_label}: {_fmt_duration(remaining)} remaining "
                      f"— {label}  ", end="", flush=True)
            # Sleep one virtual second (1/speed real seconds), but never
            # overshoot the end.
            clock.sleep(min(1.0, remaining) / speed)
            # If another process aborted this session, stop promptly; the
            # abort command has already recorded it in the log.
            current = store.read_current()
            if current is None or current.id != session.id:
                aborted_externally = True
                break
    except KeyboardInterrupt:
        print()
        return _finish_aborted(store, session, clock, "interrupted (Ctrl-C)")

    if show_countdown:
        print()  # move off the countdown line
    if aborted_externally:
        # The abort command already recorded this session in the log;
        # mark the in-memory copy too (without re-logging) so the caller
        # knows the session did not complete — e.g. cmd_start must not
        # go on to run the break after an aborted work session.
        session.ended_at = clock.now()
        session.status = "aborted"
        session.actual_seconds = max(0.0, session.ended_at - session.started_at)
        print(f"{kind_label} aborted from another terminal.")
        return session

    session.ended_at = clock.now()
    session.status = "completed"
    session.actual_seconds = session.ended_at - session.started_at
    store.append(session)
    store.clear_current()
    alert(f"focus-timer: {kind_label} done",
          f"{label} — {_fmt_duration(planned_seconds)} finished.")
    print(f"{kind_label} complete: {label} — "
          f"{_fmt_duration(session.actual_seconds)} elapsed.")
    return session


def _finish_aborted(
    store: SessionStore, session: Session, clock: Clock, reason: str
) -> Session:
    """Record ``session`` as aborted and clear the current-session file."""
    session.ended_at = clock.now()
    session.status = "aborted"
    session.actual_seconds = max(0.0, session.ended_at - session.started_at)
    store.append(session)
    store.clear_current()
    print(f"Session aborted ({reason}): {session.label} — "
          f"{_fmt_duration(session.actual_seconds)} elapsed.")
    return session


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_start(args, clock: Clock) -> int:
    cfg = _load_effective_config(
        args,
        work_minutes=args.work,
        short_break_minutes=args.break_minutes,
        long_break_minutes=args.long_break_minutes,
        cycles_before_long_break=args.cycles,
        label=args.label,
    )
    store = _make_store(args)
    speed = _speed_from_env_and_flag(args.fast)
    label = cfg.label

    completed_before = count_completed_work_today(store.read_all())
    work = run_session(store, cfg, clock, WORK, label, cfg.work_minutes,
                       dry_run=args.dry_run, speed=speed)
    if work.status != "completed":
        return 0

    # Break due after this work session (count includes the one just done).
    break_kind = next_break_kind(completed_before + 1, cfg)
    run_session(store, cfg, clock, break_kind, label,
                planned_minutes(break_kind, cfg),
                dry_run=args.dry_run, speed=speed)

    print(f"\nCycle done: {label} — work {_fmt_duration(cfg.work_minutes * 60)} "
          f"+ {'long' if break_kind == LONG_BREAK else 'short'} break "
          f"{_fmt_duration(planned_minutes(break_kind, cfg) * 60)}.")
    return 0


def cmd_status(args, clock: Clock) -> int:
    cfg = _load_effective_config(args)
    store = _make_store(args)
    sessions = store.read_all()
    now = clock.now()

    current = store.read_current()
    if current is not None:
        end = session_end(current.started_at, current.planned_minutes)
        remaining = max(0.0, end - now)
        print(f"Running: {current.kind.replace('_', ' ')} — {current.label}")
        print(f"  started {_fmt_time(current.started_at)}, "
              f"{_fmt_duration(remaining)} remaining "
              f"(of {_fmt_duration(current.planned_minutes * 60)})")
    else:
        print("No session running.")

    completed_today = count_completed_work_today(sessions)
    break_kind = next_break_kind(completed_today + 1, cfg)
    print(f"Next: work session #{completed_today + 1} ({cfg.work_minutes} min), "
          f"then a {'long' if break_kind == LONG_BREAK else 'short'} break "
          f"({planned_minutes(break_kind, cfg):g} min).")

    stats = compute_stats(sessions)
    print(f"Today: {stats['today_focus_minutes']:.1f} focus min, "
          f"{stats['today_completed']} completed, "
          f"{stats['today_aborted']} aborted, streak {stats['streak']} day(s).")
    return 0


def cmd_stats(args, clock: Clock) -> int:
    store = _make_store(args)
    stats = compute_stats(store.read_all())
    print(f"Focus stats — {stats['today_date']}")
    print(f"  Today:            {stats['today_focus_minutes']:.1f} focus minutes")
    print(f"  Streak:           {stats['streak']} day(s) with a completed work session")
    print(f"  This week:        {stats['week_focus_minutes']:.1f} focus minutes")
    print(f"  Completed total:  {stats['completed_total']} sessions")
    print(f"  Aborted total:    {stats['aborted_total']} sessions")
    print(f"  Today:            {stats['today_completed']} completed, "
          f"{stats['today_aborted']} aborted")
    if stats["per_label_minutes"]:
        print("  Per label (today):")
        for label, minutes in stats["per_label_minutes"].items():
            print(f"    {label}: {minutes:.1f} min")
    else:
        print("  Per label (today): none yet")
    return 0


def cmd_log(args, clock: Clock) -> int:
    store = _make_store(args)
    sessions = store.read_all()
    sessions.sort(key=lambda s: s.started_at, reverse=True)
    recent = sessions[: args.limit]
    if not recent:
        print("No sessions logged yet.")
        return 0
    for s in recent:
        actual = (
            f"{s.actual_seconds / 60:.1f} min"
            if s.actual_seconds is not None
            else "—"
        )
        print(f"{_fmt_time(s.started_at)}  {s.kind:<11} {s.label:<20} "
              f"planned {s.planned_minutes:g} min  actual {actual}  {s.status}")
    return 0


def cmd_abort(args, clock: Clock) -> int:
    store = _make_store(args)
    current = store.read_current()
    if current is None:
        print("No session running — nothing to abort.")
        return 1
    _finish_aborted(store, current, clock, "aborted by user")
    return 0


# ---------------------------------------------------------------------------
# argument parsing / entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="focus-timer",
        description="Pomodoro-style focus timer (25 min work / 5 min break, "
                    "long break 15 min after every 4 work sessions).",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    parser.add_argument("--config", default=None,
                        help="config file (default ~/.config/focus-timer/config.json)")
    parser.add_argument("--data-dir", default=None,
                        help="data dir for sessions.json (default "
                             "~/.local/share/focus-timer)")

    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="run a work session + its break")
    p_start.add_argument("--label", default=None, help="session label")
    p_start.add_argument("--work", type=float, default=None,
                         help="work minutes (default 25)")
    p_start.add_argument("--break", dest="break_minutes", type=float, default=None,
                         help="short break minutes (default 5)")
    p_start.add_argument("--long-break", dest="long_break_minutes", type=float,
                         default=None, help="long break minutes (default 15)")
    p_start.add_argument("--cycles", type=int, default=None,
                         help="work sessions before a long break (default 4)")
    p_start.add_argument("--dry-run", action="store_true",
                         help="complete instantly without waiting (testing)")
    p_start.add_argument("--fast", action="store_true",
                         help="run the clock 60x faster (manual testing)")
    p_start.set_defaults(func=cmd_start)

    p_status = sub.add_parser("status", help="show running/next session + today")
    p_status.set_defaults(func=cmd_status)

    p_stats = sub.add_parser("stats", help="focus stats: today, streak, week, labels")
    p_stats.set_defaults(func=cmd_stats)

    p_log = sub.add_parser("log", help="list recent sessions")
    p_log.add_argument("--limit", type=int, default=10,
                       help="how many sessions to show (default 10)")
    p_log.set_defaults(func=cmd_log)

    p_abort = sub.add_parser("abort", help="abort the running session")
    p_abort.set_defaults(func=cmd_abort)

    return parser


def main(argv: list[str] | None = None, clock: Clock | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args, clock or Clock())


if __name__ == "__main__":
    raise SystemExit(main())
