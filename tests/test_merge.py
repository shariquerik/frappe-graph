from __future__ import annotations

import json
from pathlib import Path

import pytest

from frappe_graph.merge import (
    BENCH_GRAPH_NAME,
    OUTPUT_DIR_NAME,
    find_app_graphs,
    merge,
)


def _make_bench_with_graphs(root: Path, app_graphs: dict[str, dict]) -> Path:
    """Construct a minimal bench with pre-built per-app graphs.

    `app_graphs` is {slug: graph_dict}. Each gets written to
    apps/<slug>/frappe-graph-out/graph.json.
    """
    (root / "apps").mkdir()
    (root / "sites").mkdir()
    for slug, graph in app_graphs.items():
        out = root / "apps" / slug / "frappe-graph-out"
        out.mkdir(parents=True)
        (out / "graph.json").write_text(json.dumps(graph))
    return root


def _fake_concat_merge(inputs: list[Path], output_path: Path) -> None:
    """Stand-in for graphify's merge-graphs: just concatenates nodes/edges."""
    nodes: list[dict] = []
    edges: list[dict] = []
    for inp in inputs:
        g = json.loads(Path(inp).read_text())
        nodes.extend(g.get("nodes", []))
        edges.extend(g.get("edges", []))
    output_path.write_text(json.dumps({"nodes": nodes, "edges": edges}))


def test_find_app_graphs_returns_sorted(tmp_path: Path) -> None:
    _make_bench_with_graphs(
        tmp_path,
        {
            "foo": {"nodes": [{"id": "n1"}], "edges": []},
            "bar": {"nodes": [{"id": "n2"}], "edges": []},
        },
    )
    paths = find_app_graphs(tmp_path)
    assert len(paths) == 2
    # Sorted by app dir name.
    assert paths[0].parent.parent.name == "bar"
    assert paths[1].parent.parent.name == "foo"


def test_merge_writes_bench_graph_via_injected_runner(tmp_path: Path) -> None:
    _make_bench_with_graphs(
        tmp_path,
        {
            "foo": {
                "nodes": [{"id": "DocType:Foo", "kind": "DocType"}],
                "edges": [],
            },
            "bar": {
                "nodes": [{"id": "DocType:Bar", "kind": "DocType"}],
                "edges": [],
            },
        },
    )
    out = merge(tmp_path, run_merge=_fake_concat_merge)

    assert out == tmp_path / OUTPUT_DIR_NAME / BENCH_GRAPH_NAME
    assert out.exists()
    graph = json.loads(out.read_text())
    ids = {n["id"] for n in graph["nodes"]}
    assert "DocType:Foo" in ids
    assert "DocType:Bar" in ids


def test_merge_creates_gitignore(tmp_path: Path) -> None:
    _make_bench_with_graphs(
        tmp_path,
        {"foo": {"nodes": [], "edges": []}},
    )
    merge(tmp_path, run_merge=_fake_concat_merge)

    gitignore = tmp_path / OUTPUT_DIR_NAME / ".gitignore"
    assert gitignore.exists()
    assert "bench-graph.json" in gitignore.read_text()


def test_merge_errors_when_no_per_app_graphs(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "sites").mkdir()
    (tmp_path / "apps" / "foo").mkdir()  # No frappe-graph-out inside.

    with pytest.raises(RuntimeError) as exc:
        merge(tmp_path, run_merge=_fake_concat_merge)
    assert "No per-app graphs" in str(exc.value)


def test_merge_respects_custom_output_dir(tmp_path: Path) -> None:
    _make_bench_with_graphs(
        tmp_path,
        {"foo": {"nodes": [{"id": "n1"}], "edges": []}},
    )
    custom = tmp_path / "custom-out"
    out = merge(tmp_path, output_dir=custom, run_merge=_fake_concat_merge)
    assert out == custom / BENCH_GRAPH_NAME
    assert out.exists()


def test_merge_passes_inputs_in_sorted_order(tmp_path: Path) -> None:
    _make_bench_with_graphs(
        tmp_path,
        {
            "zebra": {"nodes": [], "edges": []},
            "alpha": {"nodes": [], "edges": []},
        },
    )
    captured: dict = {}

    def capture(inputs: list[Path], output_path: Path) -> None:
        captured["inputs"] = list(inputs)
        output_path.write_text(json.dumps({"nodes": [], "edges": []}))

    merge(tmp_path, run_merge=capture)
    names = [p.parent.parent.name for p in captured["inputs"]]
    assert names == ["alpha", "zebra"]
