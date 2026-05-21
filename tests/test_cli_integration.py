"""CLI smoke tests for install / uninstall / hook commands.

These verify the click wrappers actually dispatch — the underlying behavior
is already tested in tests/test_install_skill.py and tests/test_install_hook.py.
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from frappe_graph.cli import main


def _make_fake_app(tmp_path: Path, slug: str = "myapp") -> Path:
    app = tmp_path / slug
    (app / slug).mkdir(parents=True)
    (app / slug / "hooks.py").write_text("")
    return app


def _make_fake_git_app(tmp_path: Path, slug: str = "myapp") -> Path:
    app = _make_fake_app(tmp_path, slug)
    (app / ".git" / "hooks").mkdir(parents=True)
    return app


def test_install_writes_skill(tmp_path: Path) -> None:
    app = _make_fake_app(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["install", str(app)])
    assert result.exit_code == 0, result.output
    assert (app / ".claude" / "skills" / "frappe-graph" / "SKILL.md").exists()


def test_uninstall_removes_skill(tmp_path: Path) -> None:
    app = _make_fake_app(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["install", str(app)])
    result = runner.invoke(main, ["uninstall", str(app)])
    assert result.exit_code == 0, result.output
    assert not (app / ".claude" / "skills" / "frappe-graph").exists()


def test_uninstall_purge_removes_out_dir(tmp_path: Path) -> None:
    app = _make_fake_app(tmp_path)
    (app / "frappe-graph-out").mkdir()
    (app / "frappe-graph-out" / "graph.json").write_text("{}")
    runner = CliRunner()
    runner.invoke(main, ["install", str(app)])
    result = runner.invoke(main, ["uninstall", "--purge", str(app)])
    assert result.exit_code == 0, result.output
    assert not (app / "frappe-graph-out").exists()


def test_uninstall_nothing_to_remove(tmp_path: Path) -> None:
    app = _make_fake_app(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["uninstall", str(app)])
    assert result.exit_code == 0
    assert "nothing to remove" in result.output


def test_hook_install_writes_hook(tmp_path: Path) -> None:
    app = _make_fake_git_app(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["hook", "install", str(app)])
    assert result.exit_code == 0, result.output
    hook = app / ".git" / "hooks" / "post-commit"
    assert hook.exists()
    assert "FRAPPE_GRAPH_HOOK_V1" in hook.read_text()


def test_hook_uninstall_removes_only_ours(tmp_path: Path) -> None:
    app = _make_fake_git_app(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["hook", "install", str(app)])
    result = runner.invoke(main, ["hook", "uninstall", str(app)])
    assert result.exit_code == 0, result.output
    assert not (app / ".git" / "hooks" / "post-commit").exists()


def test_hook_uninstall_leaves_foreign_hook(tmp_path: Path) -> None:
    app = _make_fake_git_app(tmp_path)
    hook = app / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho 'someone else wrote this'\n")
    runner = CliRunner()
    result = runner.invoke(main, ["hook", "uninstall", str(app)])
    assert result.exit_code == 0
    assert hook.exists()
    assert "left intact" in result.output


def test_hook_install_bench_all(tmp_path: Path) -> None:
    bench = tmp_path / "bench"
    (bench / "sites").mkdir(parents=True)
    for slug in ("foo", "bar"):
        app = bench / "apps" / slug
        (app / slug).mkdir(parents=True)
        (app / slug / "hooks.py").write_text("")
        (app / ".git" / "hooks").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(main, ["hook", "install", "--all", str(bench)])
    assert result.exit_code == 0, result.output
    for slug in ("foo", "bar"):
        assert (bench / "apps" / slug / ".git" / "hooks" / "post-commit").exists()
