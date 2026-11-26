"""Tests for focus-timer (unittest, stdlib only).

Run from the project root:  python3 -m unittest discover -s tests -v
"""

import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focus_timer import __version__  # noqa: E402
from focus_timer.clock import Clock, FakeClock  # noqa: E402
from focus_timer.config import Config, load_config, merge_config  # noqa: E402
from focus_timer.engine import (  # noqa: E402
    compute_stats,
    compute_streak,
    next_break_kind,
    planned_minutes,
    session_end,
)
from focus_timer.cli import main, run_session  # noqa: E402
from focus_timer.store import Session, SessionStore  # noqa: E402


def _ts(year, month, day, hour=9, minute=0):
    """Epoch seconds for a local date/time (naive, local tz)."""
    return dt.datetime(year, month, day, hour, minute).timestamp()


def _work_session(day_ts, label="study", minutes=25.0, status="completed",
                  actual=None):
    return Session.new(label=label, kind="work", planned_minutes=minutes,
                       started_at=day_ts)


class TestCycleTiming(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()  # 25/5/15, long break after 4
        self.clock = FakeClock(start=_ts(2026, 11, 26, 9, 0))

    def test_work_then_short_break(self):
        self.assertEqual(next_break_kind(1, self.cfg), "short_break")
        self.assertEqual(next_break_kind(2, self.cfg), "short_break")
        self.assertEqual(next_break_kind(3, self.cfg), "short_break")

    def test_fourth_work_then_long_break(self):
        self.assertEqual(next_break_kind(4, self.cfg), "long_break")
        self.assertEqual(next_break_kind(8, self.cfg), "long_break")
        self.assertEqual(next_break_kind(5, self.cfg), "short_break")

    def test_planned_minutes_defaults(self):
        self.assertEqual(planned_minutes("work", self.cfg), 25)
        self.assertEqual(planned_minutes("short_break", self.cfg), 5)
        self.assertEqual(planned_minutes("long_break", self.cfg), 15)

    def test_session_end_is_start_plus_planned(self):
        start = self.clock.now()
        self.assertAlmostEqual(session_end(start, 25), start + 25 * 60)
        self.assertAlmostEqual(session_end(start, 5), start + 5 * 60)

    def test_run_session_with_fake_clock_completes_instantly(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            session = run_session(store, self.cfg, self.clock, "work", "study",
                                  25, dry_run=False, show_countdown=False)
            # FakeClock advanced by exactly the planned duration.
            self.assertEqual(session.status, "completed")
            self.assertAlmostEqual(session.actual_seconds, 25 * 60)
            self.assertAlmostEqual(session.ended_at, session.started_at + 25 * 60)
            self.assertEqual(len(store.read_all()), 1)
            self.assertIsNone(store.read_current())  # cleared at end

    def test_external_abort_stops_session_without_double_log(self):
        # Simulate `abort` from another terminal: the .current.json file
        # disappears mid-session. run_session must stop, report the
        # session as aborted, and NOT append a second log entry (the
        # abort command already logged it).
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            real_read = store.read_current
            calls = {"n": 0}

            def flaky_read():
                calls["n"] += 1
                return real_read() if calls["n"] == 1 else None

            store.read_current = flaky_read  # type: ignore[method-assign]
            session = run_session(store, self.cfg, self.clock, "work", "study",
                                  25, dry_run=False, show_countdown=False)
            self.assertEqual(session.status, "aborted")
            self.assertEqual(store.read_all(), [])  # no double log

    def test_run_session_dry_run_logs_full_planned_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            before = self.clock.now()
            session = run_session(store, self.cfg, self.clock, "work", "study",
                                  25, dry_run=True, show_countdown=False)
            self.assertEqual(self.clock.now(), before)  # no time passed
            self.assertEqual(session.status, "completed")
            self.assertAlmostEqual(session.actual_seconds, 25 * 60)
            self.assertEqual(len(store.read_all()), 1)


class TestStoreRoundTrip(unittest.TestCase):
    def test_log_round_trip_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            original = Session(
                id="abc123", label="writing", kind="work",
                planned_minutes=25, started_at=_ts(2026, 11, 26, 9, 0),
                ended_at=_ts(2026, 11, 26, 9, 25),
                status="completed", actual_seconds=1500.0,
            )
            store.append(original)
            loaded = store.read_all()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].to_dict(), original.to_dict())

    def test_missing_log_reads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            self.assertEqual(store.read_all(), [])

    def test_current_session_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            self.assertIsNone(store.read_current())
            session = _work_session(_ts(2026, 11, 26, 9, 0))
            store.write_current(session)
            current = store.read_current()
            self.assertIsNotNone(current)
            self.assertEqual(current.id, session.id)
            store.clear_current()
            self.assertIsNone(store.read_current())

    def test_update_replaces_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            session = _work_session(_ts(2026, 11, 26, 9, 0))
            store.append(session)
            session.status = "aborted"
            store.update(session)
            loaded = store.read_all()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].status, "aborted")


class TestStats(unittest.TestCase):
    def setUp(self):
        self.today = dt.date(2026, 11, 26)
        base = _ts(2026, 11, 26, 9, 0)
        self.sessions = [
            # Two completed work sessions today, different labels.
            Session.new("study", "work", 25, base),
            Session.new("code", "work", 25, base + 3600),
            # One aborted work session today (10 min in).
            Session.new("study", "work", 25, base + 7200),
            # One completed short break today (not focus minutes).
            Session.new("study", "short_break", 5, base + 10800),
        ]
        self.sessions[0].status = "completed"
        self.sessions[0].ended_at = base + 1500
        self.sessions[0].actual_seconds = 1500.0
        self.sessions[1].status = "completed"
        self.sessions[1].ended_at = base + 3600 + 1500
        self.sessions[1].actual_seconds = 1500.0
        self.sessions[2].status = "aborted"
        self.sessions[2].ended_at = base + 7200 + 600
        self.sessions[2].actual_seconds = 600.0
        self.sessions[3].status = "completed"
        self.sessions[3].ended_at = base + 10800 + 300
        self.sessions[3].actual_seconds = 300.0

    def test_today_focus_minutes_only_completed_work(self):
        stats = compute_stats(self.sessions, self.today)
        # 25 + 25 completed work minutes; aborted 10 min excluded,
        # break excluded.
        self.assertAlmostEqual(stats["today_focus_minutes"], 50.0)

    def test_completed_vs_aborted_counts(self):
        stats = compute_stats(self.sessions, self.today)
        self.assertEqual(stats["today_completed"], 3)  # 2 work + 1 break
        self.assertEqual(stats["today_aborted"], 1)
        self.assertEqual(stats["completed_total"], 3)
        self.assertEqual(stats["aborted_total"], 1)

    def test_per_label_totals(self):
        stats = compute_stats(self.sessions, self.today)
        self.assertAlmostEqual(stats["per_label_minutes"]["study"], 25.0)
        self.assertAlmostEqual(stats["per_label_minutes"]["code"], 25.0)
        # Aborted "study" minutes must not leak into the label total.
        self.assertNotIn("other", stats["per_label_minutes"])

    def test_week_total(self):
        stats = compute_stats(self.sessions, self.today)
        self.assertAlmostEqual(stats["week_focus_minutes"], 50.0)

    def test_aborted_session_not_counted_in_streak(self):
        # Only an aborted session today -> streak 0.
        aborted_only = [self.sessions[2]]
        self.assertEqual(compute_streak(aborted_only, self.today), 0)


class TestStreak(unittest.TestCase):
    def _work_on(self, year, month, day):
        s = _work_session(_ts(year, month, day, 9, 0))
        s.status = "completed"
        s.ended_at = s.started_at + 1500
        s.actual_seconds = 1500.0
        return s

    def test_three_consecutive_days(self):
        sessions = [self._work_on(2026, 11, d) for d in (24, 25, 26)]
        self.assertEqual(compute_streak(sessions, dt.date(2026, 11, 26)), 3)

    def test_gap_day_breaks_streak(self):
        # Work on 23rd and 26th, nothing on 24th/25th -> streak is 1.
        sessions = [self._work_on(2026, 11, 23), self._work_on(2026, 11, 26)]
        self.assertEqual(compute_streak(sessions, dt.date(2026, 11, 26)), 1)

    def test_streak_survives_today_not_yet_done(self):
        # Work yesterday and the day before, none today yet -> streak 2.
        sessions = [self._work_on(2026, 11, 24), self._work_on(2026, 11, 25)]
        self.assertEqual(compute_streak(sessions, dt.date(2026, 11, 26)), 2)

    def test_no_sessions_zero_streak(self):
        self.assertEqual(compute_streak([], dt.date(2026, 11, 26)), 0)


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.work_minutes, 25)
        self.assertEqual(cfg.short_break_minutes, 5)
        self.assertEqual(cfg.long_break_minutes, 15)
        self.assertEqual(cfg.cycles_before_long_break, 4)

    def test_load_missing_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(Path(tmp) / "nope.json")
            self.assertEqual(cfg.work_minutes, 25)

    def test_load_file_overrides_some_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(
                {"work_minutes": 50, "label": "deep work"}), encoding="utf-8")
            cfg = load_config(path)
            self.assertEqual(cfg.work_minutes, 50)
            self.assertEqual(cfg.label, "deep work")
            self.assertEqual(cfg.short_break_minutes, 5)  # untouched default

    def test_merge_cli_overrides_file(self):
        cfg = merge_config(Config(), work_minutes=50)
        self.assertEqual(cfg.work_minutes, 50)
        self.assertEqual(cfg.short_break_minutes, 5)

    def test_merge_ignores_none(self):
        cfg = merge_config(Config(work_minutes=50), work_minutes=None)
        self.assertEqual(cfg.work_minutes, 50)

    def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            merge_config(Config(), work_minutes=0)
        with self.assertRaises(ValueError):
            merge_config(Config(), cycles_before_long_break=0)


class TestClock(unittest.TestCase):
    def test_real_clock_now_is_epoch(self):
        self.assertGreater(Clock().now(), 1_700_000_000)

    def test_fake_clock_advances_on_sleep(self):
        clock = FakeClock(start=1000.0)
        clock.sleep(30)
        self.assertEqual(clock.now(), 1030.0)
        clock.advance(5)
        self.assertEqual(clock.now(), 1035.0)


class TestCliSmoke(unittest.TestCase):
    def _run_cli(self, argv, tmp):
        env = dict(os.environ)
        env["FOCUS_TIMER_DATA_DIR"] = tmp
        old_env = dict(os.environ)
        os.environ.clear()
        os.environ.update(env)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(argv)
            return code, buf.getvalue()
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_start_dry_run_completes_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run_cli(
                ["start", "--dry-run", "--label", "smoke"], tmp)
            self.assertEqual(code, 0)
            self.assertIn("complete", out)
            store = SessionStore(Path(tmp) / "sessions.json")
            sessions = store.read_all()
            # One work session + one break session.
            self.assertEqual(len(sessions), 2)
            self.assertEqual(sessions[0].kind, "work")
            self.assertEqual(sessions[0].status, "completed")
            self.assertEqual(sessions[1].kind, "short_break")
            self.assertIsNone(store.read_current())

    def test_stats_status_log_after_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_cli(["start", "--dry-run", "--label", "smoke"], tmp)
            code, out = self._run_cli(["stats"], tmp)
            self.assertEqual(code, 0)
            self.assertIn("25.0", out)  # today's focus minutes
            self.assertIn("Streak", out)
            code, out = self._run_cli(["status"], tmp)
            self.assertEqual(code, 0)
            self.assertIn("No session running", out)
            code, out = self._run_cli(["log"], tmp)
            self.assertEqual(code, 0)
            self.assertIn("smoke", out)

    def test_abort_with_no_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run_cli(["abort"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("No session running", out)

    def test_abort_marks_current_session_aborted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            session = _work_session(_ts(2026, 11, 26, 9, 0))
            store.write_current(session)
            code, out = self._run_cli(["abort"], tmp)
            self.assertEqual(code, 0)
            self.assertIn("aborted", out.lower())
            logged = store.read_all()
            self.assertEqual(len(logged), 1)
            self.assertEqual(logged[0].status, "aborted")
            self.assertIsNone(store.read_current())

    def test_start_stops_after_external_abort_no_break_follows(self):
        # End-to-end: a real blocking `start` in a thread, aborted from
        # the main thread. The work session must be logged once as
        # aborted and NO break session may follow it.
        import threading
        import time

        with tempfile.TemporaryDirectory() as tmp:
            old_env = dict(os.environ)
            os.environ["FOCUS_TIMER_DATA_DIR"] = tmp
            try:
                result = {}

                def run_start():
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        result["code"] = main(
                            ["start", "--label", "live", "--work", "5",
                             "--break", "5"])
                    result["out"] = buf.getvalue()

                thread = threading.Thread(target=run_start, daemon=True)
                thread.start()
                time.sleep(1.5)  # let the work session get going
                code, out = self._run_cli(["abort"], tmp)
                self.assertEqual(code, 0)
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive(),
                                 "start did not stop after abort")
                self.assertEqual(result.get("code"), 0)
                self.assertIn("aborted from another terminal",
                              result.get("out", ""))
                store = SessionStore(Path(tmp) / "sessions.json")
                logged = store.read_all()
                self.assertEqual(len(logged), 1)
                self.assertEqual(logged[0].kind, "work")
                self.assertEqual(logged[0].status, "aborted")
                self.assertIsNone(store.read_current())
            finally:
                os.environ.clear()
                os.environ.update(old_env)

    def test_version_flag(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["--version"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
