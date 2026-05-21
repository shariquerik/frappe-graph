from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from frappe_graph.enrich import enrich
from frappe_graph.passes.whitelist import whitelist_pass


def _make_app(tmp_path: Path, slug: str = "myapp") -> Path:
    """Create a minimal app skeleton: `<tmp_path>/<slug>/hooks.py`."""
    pkg = tmp_path / slug
    pkg.mkdir()
    (pkg / "hooks.py").write_text("")
    (pkg / "__init__.py").write_text("")
    return pkg


def _function_node(node_id: str, name: str, source_file: str) -> dict:
    return {
        "id": node_id,
        "label": name,
        "name": name,
        "kind": "Function",
        "source_file": source_file,
    }


def test_plain_whitelist_tags_existing_function_node(tmp_path: Path) -> None:
    pkg = _make_app(tmp_path)
    (pkg / "api.py").write_text(
        dedent(
            """
            import frappe

            @frappe.whitelist()
            def ping():
                return "pong"

            def helper():
                return 1
            """
        )
    )

    rel_api = "myapp/api.py"
    graph = {
        "nodes": [
            _function_node("function:myapp.api.ping", "ping", rel_api),
            _function_node("function:myapp.api.helper", "helper", rel_api),
        ],
        "edges": [],
    }

    added_nodes, added_edges = whitelist_pass(tmp_path, graph)

    assert added_edges == []
    # Exactly one tagged function — `helper` is undecorated.
    assert len(added_nodes) == 1
    entry = added_nodes[0]
    assert entry["id"] == "function:myapp.api.ping"
    assert entry["kind"] == "WhitelistedMethod"
    assert entry["rpc_url"] == "myapp.api.ping"
    assert "whitelist_args" not in entry


def test_whitelist_kwargs_captured(tmp_path: Path) -> None:
    pkg = _make_app(tmp_path)
    (pkg / "api.py").write_text(
        dedent(
            """
            import frappe

            @frappe.whitelist(allow_guest=True)
            def public_ping():
                return "hi"
            """
        )
    )

    graph = {"nodes": [], "edges": []}
    added_nodes, _ = whitelist_pass(tmp_path, graph)

    assert len(added_nodes) == 1
    entry = added_nodes[0]
    assert entry["rpc_url"] == "myapp.api.public_ping"
    assert entry["whitelist_args"] == {"allow_guest": True}


def test_undecorated_function_not_tagged(tmp_path: Path) -> None:
    pkg = _make_app(tmp_path)
    (pkg / "api.py").write_text(
        dedent(
            """
            import frappe

            def plain_func():
                pass

            @frappe.whitelist()
            def decorated():
                pass
            """
        )
    )

    added_nodes, _ = whitelist_pass(tmp_path, graph={"nodes": [], "edges": []})
    rpc_urls = {n["rpc_url"] for n in added_nodes}
    assert rpc_urls == {"myapp.api.decorated"}


def test_nested_function_not_tagged(tmp_path: Path) -> None:
    pkg = _make_app(tmp_path)
    (pkg / "api.py").write_text(
        dedent(
            """
            import frappe

            @frappe.whitelist()
            def outer():
                @frappe.whitelist()
                def inner():
                    pass
                return inner
            """
        )
    )

    added_nodes, _ = whitelist_pass(tmp_path, graph={"nodes": [], "edges": []})
    assert len(added_nodes) == 1
    assert added_nodes[0]["rpc_url"] == "myapp.api.outer"


def test_subdirectory_module_path(tmp_path: Path) -> None:
    pkg = _make_app(tmp_path)
    sub = pkg / "api" / "v2"
    sub.mkdir(parents=True)
    (pkg / "api" / "__init__.py").write_text("")
    (sub / "__init__.py").write_text("")
    (sub / "things.py").write_text(
        dedent(
            """
            import frappe

            @frappe.whitelist()
            def list_things():
                return []
            """
        )
    )

    added_nodes, _ = whitelist_pass(tmp_path, graph={"nodes": [], "edges": []})
    assert len(added_nodes) == 1
    entry = added_nodes[0]
    assert entry["rpc_url"] == "myapp.api.v2.things.list_things"
    assert entry["id"] == "function:myapp.api.v2.things.list_things"
    assert entry["source_file"] == "myapp/api/v2/things.py"


def test_enrich_orchestrator_mutates_existing_node(tmp_path: Path) -> None:
    """End-to-end via `enrich()`: the existing function node is mutated in
    place with `kind=WhitelistedMethod` and `rpc_url`, and the undecorated
    sibling is untouched.
    """
    pkg = _make_app(tmp_path)
    (pkg / "api.py").write_text(
        dedent(
            """
            import frappe

            @frappe.whitelist()
            def ping():
                pass

            def helper():
                pass
            """
        )
    )

    rel_api = "myapp/api.py"
    graph = {
        "nodes": [
            _function_node("function:myapp.api.ping", "ping", rel_api),
            _function_node("function:myapp.api.helper", "helper", rel_api),
        ],
        "edges": [],
    }

    out_dir = tmp_path / "frappe-graph-out"
    out_dir.mkdir()
    graph_path = out_dir / "graph.json"
    graph_path.write_text(json.dumps(graph))

    enrich(tmp_path, graph_path, [whitelist_pass])

    on_disk = json.loads(graph_path.read_text())
    by_id = {n["id"]: n for n in on_disk["nodes"]}

    ping_node = by_id["function:myapp.api.ping"]
    assert ping_node["kind"] == "WhitelistedMethod"
    assert ping_node["rpc_url"] == "myapp.api.ping"

    helper_node = by_id["function:myapp.api.helper"]
    assert helper_node["kind"] == "Function"
    assert "rpc_url" not in helper_node


def test_qualified_name_match_preferred(tmp_path: Path) -> None:
    """If the graph node has `qualified_name` matching the dotted RPC path,
    that node is mutated even if its `source_file`/`name` don't line up with
    our lookup-by-source.
    """
    pkg = _make_app(tmp_path)
    (pkg / "api.py").write_text(
        dedent(
            """
            import frappe

            @frappe.whitelist()
            def ping():
                pass
            """
        )
    )

    graph = {
        "nodes": [
            {
                "id": "graphify-id-xyz",
                "label": "ping",
                "kind": "Function",
                "qualified_name": "myapp.api.ping",
                # Intentionally no source_file so lookup-by-source misses.
            }
        ],
        "edges": [],
    }

    added_nodes, _ = whitelist_pass(tmp_path, graph)
    assert len(added_nodes) == 1
    assert added_nodes[0]["id"] == "graphify-id-xyz"


def test_no_app_slug_returns_empty(tmp_path: Path) -> None:
    """If there's no `<slug>/hooks.py`, the pass is a no-op."""
    # tmp_path has no hooks.py anywhere underneath.
    added_nodes, added_edges = whitelist_pass(tmp_path, graph={"nodes": [], "edges": []})
    assert added_nodes == []
    assert added_edges == []
