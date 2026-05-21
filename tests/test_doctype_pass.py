from __future__ import annotations

import json
from pathlib import Path

from frappe_graph.passes.doctype import doctype_pass


def _load_baseline(sample_app: Path) -> dict:
    with (sample_app / "baseline_graph.json").open() as f:
        return json.load(f)


def test_doctype_pass_emits_nodes_for_every_doctype(sample_app: Path) -> None:
    graph = _load_baseline(sample_app)
    nodes, _ = doctype_pass(sample_app, graph)

    ids = {n["id"] for n in nodes}
    assert "DocType:Customer" in ids
    assert "DocType:Sales Order" in ids
    assert "DocType:Sales Order Item" in ids

    by_id = {n["id"]: n for n in nodes}
    assert by_id["DocType:Sales Order"]["is_submittable"] is True
    assert by_id["DocType:Sales Order Item"]["is_child_table"] is True
    assert by_id["DocType:Customer"]["is_submittable"] is False
    assert by_id["DocType:Sales Order"]["module"] == "Sample Module"


def test_doctype_pass_emits_controller_edges(sample_app: Path) -> None:
    graph = _load_baseline(sample_app)
    _, edges = doctype_pass(sample_app, graph)

    controller_edges = [e for e in edges if e["relation"] == "controller"]
    assert len(controller_edges) == 3

    so_ctrl = next(e for e in controller_edges if e["source"] == "DocType:Sales Order")
    assert so_ctrl["target"] == "file:sample_app/sample_module/doctype/sales_order/sales_order.py"
    assert so_ctrl["confidence"] == "EXTRACTED"


def test_doctype_pass_emits_link_edges(sample_app: Path) -> None:
    graph = _load_baseline(sample_app)
    _, edges = doctype_pass(sample_app, graph)

    by_key = {(e["source"], e["target"], e["relation"]): e for e in edges}

    customer_link = ("DocType:Sales Order", "DocType:Customer", "LINK[customer]")
    assert customer_link in by_key
    assert by_key[customer_link]["confidence"] == "EXTRACTED"
    assert by_key[customer_link]["fieldtype"] == "Link"


def test_doctype_pass_emits_table_edges(sample_app: Path) -> None:
    graph = _load_baseline(sample_app)
    _, edges = doctype_pass(sample_app, graph)

    items_link = next(
        e for e in edges
        if e["source"] == "DocType:Sales Order"
        and e["target"] == "DocType:Sales Order Item"
        and e["relation"] == "LINK[items]"
    )
    assert items_link["fieldtype"] == "Table"
    assert items_link["confidence"] == "EXTRACTED"


def test_doctype_pass_emits_dynamic_link_edges(sample_app: Path) -> None:
    graph = _load_baseline(sample_app)
    _, edges = doctype_pass(sample_app, graph)

    dyn_edges = [
        e for e in edges
        if e["source"] == "DocType:Sales Order" and e["relation"] == "LINK[party]"
    ]
    targets = {e["target"] for e in dyn_edges}
    assert targets == {"DocType:Customer", "DocType:Sales Order Item"}
    for e in dyn_edges:
        assert e["fieldtype"] == "Dynamic Link"
        assert e["confidence"] == "EXTRACTED"


def test_doctype_pass_skips_non_doctype_jsons(sample_app: Path, tmp_path: Path) -> None:
    # Drop an unrelated JSON in a doctype dir — should be skipped.
    junk = sample_app / "sample_app/sample_module/doctype/customer/options.json"
    junk.write_text('{"some": "config"}')
    graph = _load_baseline(sample_app)
    nodes, _ = doctype_pass(sample_app, graph)
    ids = {n["id"] for n in nodes}
    # Only the 3 real doctypes show up.
    assert sum(1 for nid in ids if nid.startswith("DocType:")) == 3
