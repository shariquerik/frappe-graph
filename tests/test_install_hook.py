from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from frappe_graph.install.hook import (
    SENTINEL,
    install_hook,
    install_hook_bench,
    uninstall_hook,
    uninstall_hook_bench,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_git_repo(path: Path) -> Path:
    (path / ".git" / "hooks").mkdir(parents=True)
    return path


def make_fake_app(path: Path, slug: str) -> Path:
    app = path / slug
    app.mkdir(parents=True, exist_ok=True)
    (app / slug).mkdir(exist_ok=True)
    (app / slug / "hooks.py").write_text("")
    (app / ".git" / "hooks").mkdir(parents=True, exist_ok=True)
    return app


def _is_executable(path: Path) -> bool:
    mode = path.stat().st_mode
    return bool(mode & stat.S_IXUSR)


# ---------------------------------------------------------------------------
# install_hook
# ---------------------------------------------------------------------------


def test_install_writes_hook_with_sentinel_and_executable(tmp_path: Path) -> None:
    app = make_git_repo(tmp_path / "app")
    result = install_hook(app)

    assert result["installed"] is True
    assert result["replaced_graphify"] is False
    hook = app / ".git" / "hooks" / "post-commit"
    assert hook.exists()
    content = hook.read_text()
    assert SENTINEL in content
    # Hook invokes either the resolved frappe-graph binary or `python -m
    # frappe_graph.cli` when the binary isn't on PATH (e.g. inside a venv that
    # the test process hasn't activated).
    assert "build . --update" in content
    assert "frappe-graph" in content or "frappe_graph.cli" in content
    assert _is_executable(hook)


def test_install_is_idempotent(tmp_path: Path) -> None:
    app = make_git_repo(tmp_path / "app")
    install_hook(app)
    result = install_hook(app)

    assert result["installed"] is True
    assert result["replaced_graphify"] is False
    hook = app / ".git" / "hooks" / "post-commit"
    assert SENTINEL in hook.read_text()


def test_install_conflict_with_graphify_hook(tmp_path: Path) -> None:
    app = make_git_repo(tmp_path / "app")
    hook = app / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\n# graphify post-commit hook\ngraphify build .\n")

    result = install_hook(app)

    assert result == {
        "installed": False,
        "reason": "conflict",
        "existing": "graphify",
    }
    # Untouched.
    assert "graphify build ." in hook.read_text()
    assert SENTINEL not in hook.read_text()


def test_install_force_replaces_graphify_hook(tmp_path: Path) -> None:
    app = make_git_repo(tmp_path / "app")
    hook = app / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\n# graphify post-commit hook\ngraphify build .\n")

    result = install_hook(app, force=True)

    assert result["installed"] is True
    assert result["replaced_graphify"] is True
    assert SENTINEL in hook.read_text()
    assert _is_executable(hook)


def test_install_conflict_with_unrelated_hook(tmp_path: Path) -> None:
    app = make_git_repo(tmp_path / "app")
    hook = app / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho hi\n")

    result = install_hook(app)

    assert result == {
        "installed": False,
        "reason": "conflict",
        "existing": "other",
    }
    assert hook.read_text() == "#!/bin/sh\necho hi\n"


def test_install_force_overwrites_unrelated_hook(tmp_path: Path) -> None:
    app = make_git_repo(tmp_path / "app")
    hook = app / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho hi\n")

    result = install_hook(app, force=True)

    assert result["installed"] is True
    assert result["replaced_graphify"] is False
    assert result.get("replaced_other") is True
    assert SENTINEL in hook.read_text()


def test_install_creates_hooks_dir_if_missing(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / ".git").mkdir(parents=True)  # .git exists, but no hooks/ inside

    result = install_hook(app)

    assert result["installed"] is True
    assert (app / ".git" / "hooks" / "post-commit").exists()


def test_install_raises_when_not_a_git_repo(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    with pytest.raises(RuntimeError, match="not a git repo"):
        install_hook(app)


# ---------------------------------------------------------------------------
# uninstall_hook
# ---------------------------------------------------------------------------


def test_uninstall_removes_our_hook(tmp_path: Path) -> None:
    app = make_git_repo(tmp_path / "app")
    install_hook(app)

    result = uninstall_hook(app)

    assert result["removed"] is True
    assert not (app / ".git" / "hooks" / "post-commit").exists()


def test_uninstall_when_no_hook(tmp_path: Path) -> None:
    app = make_git_repo(tmp_path / "app")
    result = uninstall_hook(app)
    assert result == {"removed": False, "reason": "missing"}


def test_uninstall_refuses_foreign_hook(tmp_path: Path) -> None:
    app = make_git_repo(tmp_path / "app")
    hook = app / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho not ours\n")

    result = uninstall_hook(app)

    assert result == {"removed": False, "reason": "foreign"}
    assert hook.exists()
    assert hook.read_text() == "#!/bin/sh\necho not ours\n"


def test_uninstall_refuses_graphify_hook(tmp_path: Path) -> None:
    app = make_git_repo(tmp_path / "app")
    hook = app / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\n# graphify hook\ngraphify build .\n")

    result = uninstall_hook(app)

    assert result == {"removed": False, "reason": "foreign"}
    assert hook.exists()


# ---------------------------------------------------------------------------
# install_hook_bench / uninstall_hook_bench
# ---------------------------------------------------------------------------


def _make_bench(tmp_path: Path, *, with_git: list[str], without_git: list[str] = ()) -> Path:
    bench = tmp_path / "bench"
    (bench / "apps").mkdir(parents=True)
    (bench / "sites").mkdir()
    for name in with_git:
        make_fake_app(bench / "apps", name)
    for name in without_git:
        app = bench / "apps" / name
        (app / name).mkdir(parents=True)
        (app / name / "hooks.py").write_text("")
        # No .git/ on purpose.
    return bench


def test_install_bench_all_apps_with_and_without_git(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, with_git=["foo", "bar"], without_git=["baz"])

    results = install_hook_bench(bench, all_apps=True)

    by_app = {r["app"]: r for r in results}
    assert set(by_app) == {"foo", "bar", "baz"}
    assert by_app["foo"]["installed"] is True
    assert by_app["bar"]["installed"] is True
    assert by_app["baz"] == {"installed": False, "reason": "no_git", "app": "baz"}

    # Hooks really exist on disk.
    assert (bench / "apps" / "foo" / ".git" / "hooks" / "post-commit").exists()
    assert (bench / "apps" / "bar" / ".git" / "hooks" / "post-commit").exists()
    assert not (bench / "apps" / "baz" / ".git" / "hooks" / "post-commit").exists()


def test_install_bench_named_apps_only(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, with_git=["foo", "bar", "qux"])

    results = install_hook_bench(bench, app_names=["foo", "bar"])

    assert [r["app"] for r in results] == ["foo", "bar"]
    assert all(r["installed"] for r in results)
    assert (bench / "apps" / "foo" / ".git" / "hooks" / "post-commit").exists()
    assert (bench / "apps" / "bar" / ".git" / "hooks" / "post-commit").exists()
    # `qux` was not asked for, so its hook should not exist.
    assert not (bench / "apps" / "qux" / ".git" / "hooks" / "post-commit").exists()


def test_install_bench_requires_exactly_one_selector(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, with_git=["foo"])
    with pytest.raises(ValueError):
        install_hook_bench(bench)
    with pytest.raises(ValueError):
        install_hook_bench(bench, app_names=["foo"], all_apps=True)


def test_uninstall_bench_round_trip(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, with_git=["foo", "bar"])
    install_hook_bench(bench, all_apps=True)

    results = uninstall_hook_bench(bench, all_apps=True)

    by_app = {r["app"]: r for r in results}
    assert by_app["foo"]["removed"] is True
    assert by_app["bar"]["removed"] is True
    assert not (bench / "apps" / "foo" / ".git" / "hooks" / "post-commit").exists()
    assert not (bench / "apps" / "bar" / ".git" / "hooks" / "post-commit").exists()


def test_uninstall_bench_reports_missing_and_no_git(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, with_git=["foo"], without_git=["baz"])
    # Don't install — `foo` will be missing, `baz` has no .git.

    results = uninstall_hook_bench(bench, all_apps=True)

    by_app = {r["app"]: r for r in results}
    assert by_app["foo"] == {"removed": False, "reason": "missing", "app": "foo"}
    assert by_app["baz"] == {"removed": False, "reason": "no_git", "app": "baz"}
