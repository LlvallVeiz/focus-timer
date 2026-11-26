# focus-timer

A Pomodoro-style focus timer CLI — Python standard library only, no
third-party dependencies.

Work in focused sprints, take short breaks, and get a longer break after
every four work sessions. Sessions are logged as JSON so you can see
today's focus minutes, your streak, and per-label totals.

## The Pomodoro defaults

Defaults follow the classic **Pomodoro Technique** (Francesco Cirillo,
late 1980s): **25-minute** work sessions, **5-minute** short breaks, and
a **15-minute** long break after every **4** completed work sessions.
All of these are configurable.

## Install

Requires Python 3.9+. No dependencies to install:

```bash
git clone https://github.com/LlvallVeiz/focus-timer.git
cd focus-timer
python3 -m focus_timer --help
```

Or run it from anywhere by adding the project directory to `PYTHONPATH`.

## Usage

### `start` — run a work session + its break

```bash
# One 25-min work session, then a 5-min break (blocking, live countdown)
python3 -m focus_timer start

# Label the session and customise the timings
python3 -m focus_timer start --label "thesis" --work 50 --break 10 --long-break 30

# Long break after every 3 work sessions instead of 4
python3 -m focus_timer start --cycles 3
```

While a session runs, a countdown updates in place on one line
(`Work: 24:59 remaining — thesis`). When it ends you get a desktop
notification (via `notify-send` if installed) plus a terminal bell, and
a summary line. Press `Ctrl-C` to stop early — the session is logged
as aborted.

```bash
# No waiting at all — completes instantly (for testing/scripting)
python3 -m focus_timer start --dry-run

# Run the clock 60x faster (manual testing without waiting 25 minutes)
python3 -m focus_timer start --fast
# ...or any speed factor via environment variable:
FOCUS_TIMER_SPEED=10 python3 -m focus_timer start
```

### `status` — what's happening now

```bash
python3 -m focus_timer status
# No session running.
# Next: work session #1 (25 min), then a short break (5 min).
# Today: 0.0 focus min, 0 completed, 0 aborted, streak 0 day(s).
```

### `stats` — today's numbers

```bash
python3 -m focus_timer stats
# Focus stats — 2026-11-26
#   Today:            50.0 focus minutes
#   Streak:           3 day(s) with a completed work session
#   This week:        175.0 focus minutes
#   Completed total:  12 sessions
#   Aborted total:    1 sessions
#   Today:            3 completed, 1 aborted
#   Per label (today):
#     code: 25.0 min
#     study: 25.0 min
```

A **streak** is consecutive days ending today (or yesterday, if today
has no completed work session yet) with at least one completed work
session. A day with no completed work breaks it; aborted sessions
never count toward minutes or streaks.

### `log` — recent sessions

```bash
python3 -m focus_timer log
python3 -m focus_timer log --limit 20
# 2026-11-26 09:00  work        study   planned 25 min  actual 25.0 min  completed
# 2026-11-26 09:25  short_break study   planned 5 min   actual 5.0 min   completed
```

### `abort` — stop the running session from another terminal

`start` blocks, so run this in a second terminal while a session is
running:

```bash
python3 -m focus_timer abort
# Session aborted (aborted by user): thesis — 12:34 elapsed.
```

This works through a small state file (`.current.json` in the data
dir): `start` writes it when a session begins and removes it when the
session ends; `abort` reads it, records the session as aborted in the
log, and removes it — the running `start` notices and stops without
double-logging.

## Configuration

Config file: `~/.config/focus-timer/config.json` (override with
`--config PATH`). Any key may be omitted — missing keys keep their
defaults:

```json
{
  "work_minutes": 25,
  "short_break_minutes": 5,
  "long_break_minutes": 15,
  "cycles_before_long_break": 4,
  "label": "focus"
}
```

Precedence: **CLI flags > config file > defaults**.

## Data files

| What | Default location |
| --- | --- |
| Session log | `~/.local/share/focus-timer/sessions.json` |
| Running-session state | `~/.local/share/focus-timer/.current.json` |
| Config | `~/.config/focus-timer/config.json` |

Override the data directory with `--data-dir DIR` (before the
subcommand) or the `FOCUS_TIMER_DATA_DIR` environment variable.

Each log entry is a JSON object:

```json
{
  "id": "a1b2c3d4e5f6",
  "label": "thesis",
  "kind": "work",
  "planned_minutes": 25,
  "started_at": 1764147600.0,
  "ended_at": 1764149100.0,
  "status": "completed",
  "actual_seconds": 1500.0
}
```

`kind` is `work`, `short_break`, or `long_break`; `status` is
`completed` or `aborted`. Times are epoch seconds.

## How timing works

Session end is computed up front as `start + planned_seconds` from a
`Clock` abstraction (`focus_timer/clock.py`); the countdown loop only
displays the remaining time and never counts sleeps. Tests inject a
`FakeClock` so a 25-minute session completes instantly.

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

Tests cover cycle math (short break after work 1–3, long break after
the 4th), the injected-clock timing, log round-trips, stats
aggregation, streaks across consecutive days and broken by a gap day,
aborted sessions excluded from minutes/streaks, config defaults and
merging, and CLI smoke tests (`start --dry-run`, `stats`, `status`,
`log`, `abort`).

## Project layout

```
focus_timer/
  __init__.py   version
  clock.py      Clock + FakeClock (injectable timing)
  config.py     JSON config load/defaults/merge with CLI flags
  store.py      session log JSON read/write, current-session state file
  engine.py     cycle logic, planned durations, streak + stats
  notify.py     notify-send wrapper (best effort) + terminal bell
  cli.py        argparse entry point + countdown display
__main__.py     so `python -m focus_timer` works
tests/
  test_focus_timer.py
```

## License

MIT.
