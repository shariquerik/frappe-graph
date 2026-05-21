"""Enrichment orchestrator.

Reads a graphify graph.json, runs each registered enrichment pass, and writes
the merged result back. Every pass has the uniform interface:

    pass(app_path: Path, graph: dict) -> tuple[list[dict], list[dict]]

returning (added_nodes, added_edges). The orchestrator merges them into the
graph (de-duplicating by node id and by (source, target, relation)) before
running the next pass — so later passes see earlier passes' output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

Pass = Callable[[Path, dict], tuple[list[dict], list[dict]]]


def _normalize_kinds(graph: dict) -> None:
    """Tag graphify-emitted nodes with `kind` so enrichment passes can filter.

    graphify itself does not set `kind`. Our passes (whitelist, rpc, refs)
    filter on `kind in {File, Function, Method}`. We infer the kind from
    graphify's labelling convention:

        - File:     label is the basename of source_file (e.g. ``Leads.vue``).
        - Method:   label ends in ``()`` and starts with ``.`` (method on a
                    class — graphify renders these as ``.on_submit()``).
        - Function: label ends in ``()`` (everything else with ``()``).

    Nodes that already have a `kind` (e.g. the synthetic DocType nodes we
    added on a previous run) are left untouched.
    """
    for n in graph.get("nodes", []):
        if n.get("kind"):
            continue
        label = n.get("label")
        if not isinstance(label, str):
            continue
        src = n.get("source_file")
        if isinstance(src, str) and Path(src).name == label:
            n["kind"] = "File"
            continue
        if label.endswith("()"):
            n["kind"] = "Method" if label.startswith(".") else "Function"


def _edge_key(edge: dict) -> tuple:
    return (edge.get("source"), edge.get("target"), edge.get("relation"))


def _edges_field(graph: dict) -> str:
    """Pick the edges-list key graphify is using.

    graphify (NetworkX node-link format) writes `links`; older synthetic graphs
    in tests use `edges`. Prefer `links` when present so enriched edges land in
    the same list graphify queries traverse.
    """
    if "links" in graph:
        return "links"
    return "edges"


def _merge(graph: dict, added_nodes: list[dict], added_edges: list[dict]) -> None:
    nodes = graph.setdefault("nodes", [])
    edges_key = _edges_field(graph)
    edges = graph.setdefault(edges_key, [])

    existing_node_ids = {n["id"] for n in nodes if "id" in n}
    for node in added_nodes:
        nid = node.get("id")
        if nid is None:
            continue
        if nid in existing_node_ids:
            # Merge properties onto existing node (passes may tag existing nodes).
            for n in nodes:
                if n.get("id") == nid:
                    n.update({k: v for k, v in node.items() if k != "id"})
                    break
        else:
            nodes.append(node)
            existing_node_ids.add(nid)

    existing_edge_keys = {_edge_key(e) for e in edges}
    for edge in added_edges:
        key = _edge_key(edge)
        if key not in existing_edge_keys:
            edges.append(edge)
            existing_edge_keys.add(key)


def enrich(app_path: Path, graph_path: Path, passes: list[Pass]) -> dict:
    """Run every pass against the graph at `graph_path` (in place).

    Returns the final graph dict (also written back to graph_path).
    """
    with graph_path.open() as f:
        graph = json.load(f)

    _normalize_kinds(graph)

    for p in passes:
        added_nodes, added_edges = p(app_path, graph)
        _merge(graph, added_nodes, added_edges)

    with graph_path.open("w") as f:
        json.dump(graph, f, indent=2, sort_keys=False)

    return graph


def default_passes() -> list[Pass]:
    """The pass list registered by default.

    Order matters: DocType must run first (everything references DocType:<X>
    ids); whitelist must run before any RPC pass (slice #5) since the latter
    consumes the `rpc_url` tag.
    """
    from frappe_graph.passes.doctype import doctype_pass
    from frappe_graph.passes.hooks import hooks_pass
    from frappe_graph.passes.refs import refs_pass
    from frappe_graph.passes.rpc import rpc_pass
    from frappe_graph.passes.whitelist import whitelist_pass

    return [doctype_pass, hooks_pass, whitelist_pass, rpc_pass, refs_pass]
