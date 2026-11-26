"""Clock abstraction.

All timing math in focus-timer goes through a :class:`Clock` so that
session-end arithmetic (``end = start + planned_seconds``) can be tested
with a fake clock instead of actually waiting. The real clock delegates to
:func:`time.time`; tests inject :class:`FakeClock`.
"""

from __future__ import annotations

import time


class Clock:
    """Real wall-clock time source."""

    def now(self) -> float:
        """Return the current time as seconds since the epoch."""
        return time.time()

    def sleep(self, seconds: float) -> None:
        """Sleep for ``seconds`` (a no-op override point for tests)."""
        time.sleep(seconds)


class FakeClock(Clock):
    """A controllable clock for tests.

    ``now()`` returns the value set with :meth:`advance` / :meth:`set`.
    ``sleep()`` advances the clock instantly instead of waiting.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += float(seconds)

    def advance(self, seconds: float) -> None:
        """Move the clock forward by ``seconds``."""
        self._now += float(seconds)

    def set(self, value: float) -> None:
        """Set the clock to an absolute epoch value."""
        self._now = float(value)
