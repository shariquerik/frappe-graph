"""Detect whether the current directory is a Frappe app or bench root.

App mode: a `<slug>/hooks.py` exists at the package root inside the repo.
Bench mode is added in slice 6 (#7). For now, anything else raises a clear error.
"""

from __future__ import annotations

from pathlib import Path


class DetectionError(Exception):
    """Raised when the directory layout is not a recognised Frappe layout."""


def detect_app(path: Path) -> tuple[str, str]:
    """Detect a single Frappe app at `path`.

    Returns ("app", <slug>) where <slug> is the inner package directory
    containing hooks.py.

    Raises DetectionError if the directory is not a Frappe app.
    """
    path = Path(path).resolve()
    if not path.is_dir():
        raise DetectionError(f"Not a directory: {path}")

    candidates = [p for p in path.iterdir() if p.is_dir() and (p / "hooks.py").is_file()]
    if len(candidates) == 1:
        return ("app", candidates[0].name)

    if len(candidates) > 1:
        names = ", ".join(sorted(p.name for p in candidates))
        raise DetectionError(
            f"Ambiguous Frappe app layout at {path}: multiple inner packages with hooks.py ({names}). "
            f"Expected exactly one <slug>/hooks.py."
        )

    raise DetectionError(
        f"Not a Frappe app: {path}\n"
        f"Expected layout: <repo>/<slug>/hooks.py (a single inner package with hooks.py).\n"
        f"Bench mode (apps/ + sites/) is not yet supported."
    )
