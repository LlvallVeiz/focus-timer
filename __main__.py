"""Allow ``python -m focus_timer`` to run the CLI."""

from focus_timer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
