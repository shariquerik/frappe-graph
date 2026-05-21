"""Detect whether the current directory is a Frappe app or bench root.

App mode: a `<slug>/hooks.py` exists at the package root inside the repo.
Bench mode: `<root>/apps/` and `<root>/sites/` both exist as directories.
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


def _bench_apps(path: Path) -> list[str]:
    """Return the sorted list of app slugs under `path/apps/`.

    A directory under `apps/` counts as an app iff `apps/<name>/<name>/hooks.py`
    exists.
    """
    apps_dir = path / "apps"
    if not apps_dir.is_dir():
        return []

    slugs: list[str] = []
    for entry in apps_dir.iterdir():
        if not entry.is_dir():
            continue
        if (entry / entry.name / "hooks.py").is_file():
            slugs.append(entry.name)
    return sorted(slugs)


def detect(path: Path) -> tuple[str, str] | tuple[str, list[str]]:
    """Detect whether `path` is a Frappe app or a bench root.

    Returns:
        ("bench", [<app slugs>]) when `path/apps/` and `path/sites/` both exist.
        ("app", <slug>) when `path` is a Frappe app (per `detect_app`).

    Raises DetectionError if neither layout matches.
    """
    path = Path(path).resolve()
    if not path.is_dir():
        raise DetectionError(f"Not a directory: {path}")

    apps_dir = path / "apps"
    sites_dir = path / "sites"
    if apps_dir.is_dir() and sites_dir.is_dir():
        return ("bench", _bench_apps(path))

    try:
        return detect_app(path)
    except DetectionError:
        raise DetectionError(
            f"Not a recognised Frappe layout: {path}\n"
            f"Expected one of:\n"
            f"  - app mode: <repo>/<slug>/hooks.py (a single inner package with hooks.py)\n"
            f"  - bench mode: <root>/apps/ + <root>/sites/ (both directories must exist)"
        )
