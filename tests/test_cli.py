from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from frappe_graph.build import build as run_build
from frappe_graph.cli import main


def test_cli_build_smoke(sample_app_with_baseline: Path, monkeypatch) -> None:
    """`frappe-graph build PATH` exits 0 and produces an enriched graph.json.

    We monkeypatch _run_graphify because graphify needs network/install to actually
    run; the baseline graph.json simulates its output.
    """
    monkeypatch.setattr("frappe_graph.build._run_graphify", lambda *a, **kw: None)

    runner = CliRunner()
    result = runner.invoke(main, ["build", str(sample_app_with_baseline)])
    assert result.exit_code == 0, result.output

    out = sample_app_with_baseline / "frappe-graph-out" / "graph.json"
    assert out.exists()
    graph = json.loads(out.read_text())
    ids = {n["id"] for n in graph["nodes"]}
    assert "DocType:Customer" in ids
    assert "DocType:Sales Order" in ids


def test_cli_build_writes_gitignore(sample_app_with_baseline: Path, monkeypatch) -> None:
    monkeypatch.setattr("frappe_graph.build._run_graphify", lambda *a, **kw: None)
    runner = CliRunner()
    runner.invoke(main, ["build", str(sample_app_with_baseline)])

    gitignore = sample_app_with_baseline / "frappe-graph-out" / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text()
    assert "manifest.json" in content
    assert "cost.json" in content


def test_cli_build_outside_app_errors(tmp_path: Path) -> None:
    (tmp_path / "junk.txt").write_text("nope")
    runner = CliRunner()
    result = runner.invoke(main, ["build", str(tmp_path)])
    assert result.exit_code != 0
    assert "Not a Frappe app" in result.output
    assert "hooks.py" in result.output


def test_build_function_skip_graphify(sample_app_with_baseline: Path) -> None:
    out = run_build(sample_app_with_baseline, skip_graphify=True)
    assert out.exists()
    graph = json.loads(out.read_text())
    assert any(n["id"] == "DocType:Customer" for n in graph["nodes"])
