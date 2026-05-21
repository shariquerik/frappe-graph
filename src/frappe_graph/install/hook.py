"""Install/uninstall a git post-commit hook that rebuilds the enriched graph.

The hook is written to ``<app>/.git/hooks/post-commit`` and tagged with a
sentinel comment (``FRAPPE_GRAPH_HOOK_V1``) so we can later recognise our own
hook and refuse to clobber hooks installed by other tools.

The bench-level helpers (``install_hook_bench``, ``uninstall_hook_bench``)
iterate over apps under ``<bench>/apps/``. Each app is its own git repo, so
hook installation is always per-app; the bench helpers are just a convenience
for fanning out across many apps in one go.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

SENTINEL = "FRAPPE_GRAPH_HOOK_V1"

HOOK_SCRIPT = """\
#!/usr/bin/env sh
# frappe-graph post-commit hook (managed by `frappe-graph hook install`)
# Sentinel: FRAPPE_GRAPH_HOOK_V1
frappe-graph build . --update >/dev/null 2>&1 || true
"""


def _hook_path(app_path: Path) -> Path:
    return app_path / ".git" / "hooks" / "post-commit"


def _write_hook(hook_path: Path) -> None:
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(HOOK_SCRIPT)
    # chmod 0o755 — owner rwx, group/other rx.
    mode = (
        stat.S_IRUSR
        | stat.S_IWUSR
        | stat.S_IXUSR
        | stat.S_IRGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IXOTH
    )
    os.chmod(hook_path, mode)


def install_hook(app_path: Path, *, force: bool = False) -> dict:
    """Install the post-commit hook in ``app_path/.git/hooks/post-commit``.

    Returns a result dict. On success::

        {"installed": True, "path": Path, "replaced_graphify": bool,
         "replaced_other": bool}  # replaced_other only present when overwriting
                                   # an unrelated hook

    On conflict (when ``force=False`` and a non-frappe-graph hook exists)::

        {"installed": False, "reason": "conflict",
         "existing": "graphify" | "other"}

    Raises ``RuntimeError`` if ``app_path/.git`` is not a directory.
    """
    app_path = Path(app_path)
    git_dir = app_path / ".git"
    if not git_dir.is_dir():
        raise RuntimeError(f"not a git repo: {app_path}")

    hook_path = _hook_path(app_path)

    if hook_path.exists():
        existing = hook_path.read_text()
        if SENTINEL in existing:
            # We own it — overwrite idempotently.
            _write_hook(hook_path)
            return {
                "installed": True,
                "path": hook_path,
                "replaced_graphify": False,
            }

        if "graphify" in existing:
            if not force:
                return {
                    "installed": False,
                    "reason": "conflict",
                    "existing": "graphify",
                }
            _write_hook(hook_path)
            return {
                "installed": True,
                "path": hook_path,
                "replaced_graphify": True,
            }

        # Unknown hook script.
        if not force:
            return {
                "installed": False,
                "reason": "conflict",
                "existing": "other",
            }
        _write_hook(hook_path)
        return {
            "installed": True,
            "path": hook_path,
            "replaced_graphify": False,
            "replaced_other": True,
        }

    _write_hook(hook_path)
    return {
        "installed": True,
        "path": hook_path,
        "replaced_graphify": False,
    }


def uninstall_hook(app_path: Path) -> dict:
    """Remove the post-commit hook only if frappe-graph installed it.

    Returns::

        {"removed": True, "path": Path}
        {"removed": False, "reason": "missing"}      # no hook file
        {"removed": False, "reason": "foreign"}      # hook exists but no sentinel
    """
    app_path = Path(app_path)
    hook_path = _hook_path(app_path)

    if not hook_path.exists():
        return {"removed": False, "reason": "missing"}

    content = hook_path.read_text()
    if SENTINEL not in content:
        return {"removed": False, "reason": "foreign"}

    hook_path.unlink()
    return {"removed": True, "path": hook_path}


def _bench_app_paths(
    bench_path: Path,
    *,
    app_names: list[str] | None,
    all_apps: bool,
) -> list[tuple[str, Path]]:
    """Resolve the list of (app_name, app_path) targets for a bench-level call."""
    if all_apps == bool(app_names):
        # Either both set or both unset — caller violated the contract.
        raise ValueError("pass exactly one of app_names or all_apps=True")

    apps_dir = bench_path / "apps"
    if not apps_dir.is_dir():
        raise RuntimeError(f"not a bench: missing apps/ at {bench_path}")

    if all_apps:
        candidates: list[tuple[str, Path]] = []
        for entry in sorted(apps_dir.iterdir()):
            if not entry.is_dir():
                continue
            if (entry / entry.name / "hooks.py").is_file():
                candidates.append((entry.name, entry))
        return candidates

    assert app_names is not None  # for type checkers
    return [(name, apps_dir / name) for name in app_names]


def install_hook_bench(
    bench_path: Path,
    *,
    app_names: list[str] | None = None,
    all_apps: bool = False,
    force: bool = False,
) -> list[dict]:
    """Install the hook in multiple apps under ``bench_path/apps/``.

    Pass exactly one of ``app_names`` or ``all_apps=True``. When ``all_apps`` is
    set, apps without a ``.git/`` directory are skipped (and reported as
    ``{"installed": False, "reason": "no_git", "app": ...}``).
    """
    bench_path = Path(bench_path)
    targets = _bench_app_paths(bench_path, app_names=app_names, all_apps=all_apps)

    results: list[dict] = []
    for name, app_path in targets:
        if not (app_path / ".git").is_dir():
            results.append({"installed": False, "reason": "no_git", "app": name})
            continue
        result = install_hook(app_path, force=force)
        result["app"] = name
        results.append(result)
    return results


def uninstall_hook_bench(
    bench_path: Path,
    *,
    app_names: list[str] | None = None,
    all_apps: bool = False,
) -> list[dict]:
    """Uninstall the hook from multiple apps under ``bench_path/apps/``.

    Pass exactly one of ``app_names`` or ``all_apps=True``. Apps without a
    ``.git/`` directory are reported as
    ``{"removed": False, "reason": "no_git", "app": ...}``.
    """
    bench_path = Path(bench_path)
    targets = _bench_app_paths(bench_path, app_names=app_names, all_apps=all_apps)

    results: list[dict] = []
    for name, app_path in targets:
        if not (app_path / ".git").is_dir():
            results.append({"removed": False, "reason": "no_git", "app": name})
            continue
        result = uninstall_hook(app_path)
        result["app"] = name
        results.append(result)
    return results
