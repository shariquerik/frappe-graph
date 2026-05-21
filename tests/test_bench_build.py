from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from frappe_graph.cli import main


def _make_bench_app(apps_dir: Path, slug: str) -> Path:
    """Create a minimal Frappe app under `apps_dir/<slug>/<slug>/hooks.py` with
    a baseline graph.json already in `frappe-graph-out/` (so we can skip
    graphify in tests by monkeypatching _run_graphify)."""
    pkg = apps_dir / slug / slug
    pkg.mkdir(parents=True)
    (pkg / "hooks.py").write_text("")
    # Pre-place a baseline graph.json (build.py won't overwrite when graphify
    # is monkeypatched to a no-op).
    out = apps_dir / slug / "frappe-graph-out"
    out.mkdir(parents=True)
    (out / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": f"file:{slug}/hooks.py",
                        "label": "hooks.py",
                        "kind": "File",
                        "source_file": f"{slug}/hooks.py",
                    }
                ],
                "edges": [],
            }
        )
    )
    return apps_dir / slug


@pytest.fixture
def bench(tmp_path: Path, monkeypatch) -> Path:
    """A bench root with two apps (foo, bar) and pre-built baseline graphs.

    Monkeypatches _run_graphify to a no-op so we don't depend on graphify.
    """
    monkeypatch.setattr("frappe_graph.build._run_graphify", lambda *a, **kw: None)
    (tmp_path / "sites").mkdir()
    apps = tmp_path / "apps"
    apps.mkdir()
    _make_bench_app(apps, "foo")
    _make_bench_app(apps, "bar")
    return tmp_path


def test_build_bench_all_builds_every_app(bench: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["build", str(bench), "--all"])
    assert result.exit_code == 0, result.output

    for slug in ("foo", "bar"):
        out = bench / "apps" / slug / "frappe-graph-out" / "graph.json"
        assert out.exists()
        graph = json.loads(out.read_text())
        ids = {n["id"] for n in graph["nodes"]}
        assert f"file:{slug}/hooks.py" in ids


def test_build_bench_app_flag_builds_only_named(bench: Path) -> None:
    # Remove bar's pre-built graph so we can verify it wasn't rebuilt.
    bar_out = bench / "apps" / "bar" / "frappe-graph-out" / "graph.json"
    bar_out.unlink()

    runner = CliRunner()
    result = runner.invoke(main, ["build", str(bench), "--app", "foo"])
    assert result.exit_code == 0, result.output

    foo_out = bench / "apps" / "foo" / "frappe-graph-out" / "graph.json"
    assert foo_out.exists()
    # bar was not touched.
    assert not bar_out.exists()


def test_build_bench_multiple_app_flags(bench: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main, ["build", str(bench), "--app", "foo", "--app", "bar"]
    )
    assert result.exit_code == 0, result.output
    for slug in ("foo", "bar"):
        assert (bench / "apps" / slug / "frappe-graph-out" / "graph.json").exists()


def test_build_bench_with_merge_writes_bench_graph(bench: Path, monkeypatch) -> None:
    # Inject a fake graphify merge runner via merge module.
    def fake_merge(inputs, output_path):
        nodes: list[dict] = []
        edges: list[dict] = []
        for inp in inputs:
            g = json.loads(Path(inp).read_text())
            nodes.extend(g.get("nodes", []))
            edges.extend(g.get("edges", []))
        Path(output_path).write_text(json.dumps({"nodes": nodes, "edges": edges}))

    monkeypatch.setattr("frappe_graph.merge._default_run_merge", fake_merge)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["build", str(bench), "--app", "foo", "--app", "bar", "--merge"],
    )
    assert result.exit_code == 0, result.output

    bench_graph = bench / "frappe-graph-out" / "bench-graph.json"
    assert bench_graph.exists()
    g = json.loads(bench_graph.read_text())
    ids = {n["id"] for n in g["nodes"]}
    assert "file:foo/hooks.py" in ids
    assert "file:bar/hooks.py" in ids


def test_build_bench_unknown_app_errors(bench: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main, ["build", str(bench), "--app", "does-not-exist"]
    )
    assert result.exit_code != 0
    assert "does-not-exist" in result.output


def test_build_bench_no_flags_non_tty_errors(bench: Path) -> None:
    """With no --all/--app flags in a non-TTY environment (CliRunner is
    non-TTY by default), bench mode should error rather than hang on the
    interactive prompt."""
    runner = CliRunner()
    result = runner.invoke(main, ["build", str(bench)])
    assert result.exit_code != 0
    assert "--all" in result.output or "--app" in result.output


def test_build_app_mode_rejects_bench_flags(sample_app_with_baseline: Path, monkeypatch) -> None:
    monkeypatch.setattr("frappe_graph.build._run_graphify", lambda *a, **kw: None)

    runner = CliRunner()
    result = runner.invoke(
        main, ["build", str(sample_app_with_baseline), "--all"]
    )
    assert result.exit_code != 0
    assert "--app" in result.output or "--all" in result.output


def test_merge_subcommand(bench: Path, monkeypatch) -> None:
    """`frappe-graph merge <bench>` with two pre-built per-app graphs produces
    a bench-graph.json containing nodes from both."""

    def fake_merge(inputs, output_path):
        nodes: list[dict] = []
        edges: list[dict] = []
        for inp in inputs:
            g = json.loads(Path(inp).read_text())
            nodes.extend(g.get("nodes", []))
            edges.extend(g.get("edges", []))
        Path(output_path).write_text(json.dumps({"nodes": nodes, "edges": edges}))

    monkeypatch.setattr("frappe_graph.merge._default_run_merge", fake_merge)

    runner = CliRunner()
    result = runner.invoke(main, ["merge", str(bench)])
    assert result.exit_code == 0, result.output

    bench_graph = bench / "frappe-graph-out" / "bench-graph.json"
    assert bench_graph.exists()
    g = json.loads(bench_graph.read_text())
    ids = {n["id"] for n in g["nodes"]}
    assert "file:foo/hooks.py" in ids
    assert "file:bar/hooks.py" in ids


def test_merge_subcommand_rejects_non_bench(sample_app: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["merge", str(sample_app)])
    assert result.exit_code != 0
    assert "bench" in result.output.lower()
