"""Start everything with one command.

    python run.py

Judges will not open four terminals. This starts the API, which owns the
pipeline, and prints what to open. The graph must already be loaded - see
the README.
"""

from __future__ import annotations

import sys
import webbrowser


def main() -> int:
    try:
        import uvicorn
    except ImportError:
        print('uvicorn is required. pip install -e ".[dev]"', file=sys.stderr)
        return 2

    url = "http://localhost:8000"
    print(f"Chalkboard is starting. Open {url}", file=sys.stderr)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a headless box has no browser
        pass

    uvicorn.run("chalkboard.app.api:app", host="127.0.0.1", port=8000, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
