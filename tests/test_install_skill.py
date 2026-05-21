"""Tests for the ``/frappe-graph`` Claude Code skill install/uninstall module."""

from __future__ import annotations

from pathlib import Path

import pytest

from frappe_graph.install.skill import (
    install_skill,
    skill_path,
    uninstall_skill,
)


# --- skill_path ------------------------------------------------------------


def test_skill_path_project_level(tmp_path: Path) -> None:
    expected = (tmp_path / ".claude" / "skills" / "frappe-graph" / "SKILL.md").resolve()
    assert skill_path(tmp_path) == expected


def test_skill_path_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    expected = (
        fake_home / ".claude" / "skills" / "frappe-graph" / "SKILL.md"
    ).resolve()
    assert skill_path(tmp_path, global_install=True) == expected


# --- install_skill ---------------------------------------------------------


def test_install_skill_writes_project_skill(tmp_path: Path) -> None:
    written = install_skill(tmp_path)

    assert written == (
        tmp_path / ".claude" / "skills" / "frappe-graph" / "SKILL.md"
    ).resolve()
    assert written.exists()

    content = written.read_text()
    # Name appears in YAML frontmatter and headline.
    assert "frappe-graph" in content
    # Node id conventions documented.
    assert "DocType:<Name>" in content
    assert "BUILTIN:<method>" in content
    assert "MISSING_RPC:<url>" in content
    # Edge relations documented.
    assert "RPC_CALLS" in content
    assert "READS_DOCTYPE" in content
    assert "controller" in content


def test_install_skill_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    written = install_skill(tmp_path, global_install=True)

    assert written == (
        fake_home / ".claude" / "skills" / "frappe-graph" / "SKILL.md"
    ).resolve()
    assert written.exists()
    # Nothing should have been written under the project root.
    assert not (tmp_path / ".claude").exists()


def test_install_skill_is_idempotent(tmp_path: Path) -> None:
    first = install_skill(tmp_path)
    first_content = first.read_text()

    second = install_skill(tmp_path)
    second_content = second.read_text()

    assert first == second
    assert first_content == second_content


# --- uninstall_skill -------------------------------------------------------


def test_uninstall_skill_removes_dir(tmp_path: Path) -> None:
    install_skill(tmp_path)
    skill_dir = (tmp_path / ".claude" / "skills" / "frappe-graph").resolve()
    assert skill_dir.exists()

    result = uninstall_skill(tmp_path)

    assert result["missing"] is False
    assert result["purged"] is False
    assert skill_dir in result["removed"]
    assert not skill_dir.exists()


def test_uninstall_skill_when_absent_returns_missing(tmp_path: Path) -> None:
    install_skill(tmp_path)
    uninstall_skill(tmp_path)

    result = uninstall_skill(tmp_path)
    assert result["missing"] is True
    assert result["removed"] == []
    assert result["purged"] is False


def test_uninstall_skill_purge_removes_output_dir(tmp_path: Path) -> None:
    install_skill(tmp_path)
    out_dir = tmp_path / "frappe-graph-out"
    out_dir.mkdir()
    (out_dir / "graph.json").write_text("{}")

    skill_dir = (tmp_path / ".claude" / "skills" / "frappe-graph").resolve()

    result = uninstall_skill(tmp_path, purge=True)

    assert result["missing"] is False
    assert result["purged"] is True
    assert skill_dir in result["removed"]
    assert out_dir.resolve() in result["removed"]
    assert not skill_dir.exists()
    assert not out_dir.exists()


def test_uninstall_skill_purge_when_output_dir_absent(tmp_path: Path) -> None:
    install_skill(tmp_path)
    # No frappe-graph-out/ in the project.

    result = uninstall_skill(tmp_path, purge=True)

    assert result["missing"] is False
    # Skill was removed; purged stays False since there was nothing to purge.
    assert result["purged"] is False


def test_uninstall_global_with_purge_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    install_skill(tmp_path, global_install=True)

    with pytest.raises(ValueError):
        uninstall_skill(tmp_path, global_install=True, purge=True)


def test_uninstall_global_removes_user_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    install_skill(tmp_path, global_install=True)
    skill_dir = (fake_home / ".claude" / "skills" / "frappe-graph").resolve()
    assert skill_dir.exists()

    result = uninstall_skill(tmp_path, global_install=True)

    assert result["missing"] is False
    assert skill_dir in result["removed"]
    assert not skill_dir.exists()
