from __future__ import annotations

import json
from pathlib import Path

from frappe_graph.enrich import enrich
from frappe_graph.passes.doctype import doctype_pass


def test_enrich_merges_passes_into_graph(sample_app_with_baseline: Path) -> None:
    graph_path = sample_app_with_baseline / "frappe-graph-out" / "graph.json"
    graph = enrich(sample_app_with_baseline, graph_path, [doctype_pass])

    # Original baseline nodes are preserved.
    ids = {n["id"] for n in graph["nodes"]}
    assert "file:sample_app/sample_module/doctype/customer/customer.py" in ids

    # New DocType nodes are added.
    assert "DocType:Customer" in ids
    assert "DocType:Sales Order" in ids


def test_enrich_writes_graph_back_to_disk(sample_app_with_baseline: Path) -> None:
    graph_path = sample_app_with_baseline / "frappe-graph-out" / "graph.json"
    enrich(sample_app_with_baseline, graph_path, [doctype_pass])
    with graph_path.open() as f:
        on_disk = json.load(f)
    assert any(n["id"] == "DocType:Sales Order" for n in on_disk["nodes"])


def test_enrich_dedupes_edges_across_pass_runs(sample_app_with_baseline: Path) -> None:
    graph_path = sample_app_with_baseline / "frappe-graph-out" / "graph.json"
    # Run the same pass twice — edge count should be stable.
    enrich(sample_app_with_baseline, graph_path, [doctype_pass])
    first = json.loads(graph_path.read_text())
    enrich(sample_app_with_baseline, graph_path, [doctype_pass])
    second = json.loads(graph_path.read_text())
    assert len(first["edges"]) == len(second["edges"])
    assert len(first["nodes"]) == len(second["nodes"])
