"""Tests for the frontend <-> backend RPC enrichment pass (frappe-ui-aware).

Each test builds a self-contained synthetic mini-app under ``tmp_path``, a
matching in-memory graph dict pre-seeded with File / WhitelistedMethod /
DocType nodes as the pass requires, and asserts the expected edges and
synthetic nodes.

We never touch the shared ``sample_app`` fixture or its baseline graph.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from frappe_graph.enrich import enrich
from frappe_graph.passes.rpc import rpc_pass


# --- helpers ---------------------------------------------------------------


def _make_app(tmp_path: Path, files: dict[str, str]) -> Path:
    """Materialise a tiny Frappe app under tmp_path.

    Keys of ``files`` are relative paths from the app root; values are file
    contents. A minimal ``myapp/hooks.py`` is always created so the layout
    matches a real Frappe app.
    """
    app_path = tmp_path
    myapp = app_path / "myapp"
    myapp.mkdir(parents=True, exist_ok=True)
    (myapp / "__init__.py").write_text("")
    (myapp / "hooks.py").write_text('app_name = "myapp"\n')

    for rel, content in files.items():
        full = app_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    return app_path


def _seed_graph(
    file_nodes: list[str] | None = None,
    whitelist_methods: list[tuple[str, str, str]] | None = None,
    doctypes: list[str] | None = None,
) -> dict:
    """Build a minimal graph dict.

    - ``file_nodes``: list of relative source paths; id becomes "file:<path>".
    - ``whitelist_methods``: list of (node_id, source_file, rpc_url).
    - ``doctypes``: list of DocType names; id becomes "DocType:<name>".
    """
    nodes: list[dict] = []
    for path in file_nodes or []:
        nodes.append({
            "id": f"file:{path}",
            "label": Path(path).name,
            "kind": "File",
            "source_file": path,
        })
    for nid, src, url in whitelist_methods or []:
        nodes.append({
            "id": nid,
            "label": url.rsplit(".", 1)[-1],
            "kind": "WhitelistedMethod",
            "source_file": src,
            "rpc_url": url,
        })
    for dt in doctypes or []:
        nodes.append({
            "id": f"DocType:{dt}",
            "label": dt,
            "kind": "DocType",
        })
    return {"nodes": nodes, "edges": []}


def _edge(edges: list[dict], source: str, target: str, relation: str) -> dict | None:
    for e in edges:
        if (
            e.get("source") == source
            and e.get("target") == target
            and e.get("relation") == relation
        ):
            return e
    return None


# --- tests: classic frappe.call --------------------------------------------


def test_frappe_call_method_emits_rpc_calls_edge(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script>
        frappe.call({ method: 'myapp.api.ping' })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/foo.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/foo.vue"],
        whitelist_methods=[("function:myapp.api.ping", "myapp/api.py", "myapp.api.ping")],
    )

    nodes, edges = rpc_pass(app, graph)

    assert nodes == []
    edge = _edge(edges, "file:myapp/frontend/foo.vue", "function:myapp.api.ping", "RPC_CALLS")
    assert edge is not None
    assert edge["confidence"] == "INFERRED"


# --- tests: createResource (top-level url) ---------------------------------


def test_create_resource_url_emits_rpc_calls_edge(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script>
        const r = createResource({ url: 'myapp.api.ping' })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/bar.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/bar.vue"],
        whitelist_methods=[("function:myapp.api.ping", "myapp/api.py", "myapp.api.ping")],
    )

    _, edges = rpc_pass(app, graph)

    edge = _edge(edges, "file:myapp/frontend/bar.vue", "function:myapp.api.ping", "RPC_CALLS")
    assert edge is not None
    assert edge["confidence"] == "INFERRED"


# --- tests: createResource (nested cache.url) ------------------------------


def test_create_resource_nested_cache_url(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script>
        const r = createResource({
          cache: { url: 'myapp.api.ping' },
          auto: true,
        })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/cached.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/cached.vue"],
        whitelist_methods=[("function:myapp.api.ping", "myapp/api.py", "myapp.api.ping")],
    )

    _, edges = rpc_pass(app, graph)

    edge = _edge(edges, "file:myapp/frontend/cached.vue", "function:myapp.api.ping", "RPC_CALLS")
    assert edge is not None


# --- tests: createDocumentResource / createListResource --------------------


def test_create_document_resource_emits_reads_doctype(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script>
        const lead = createDocumentResource({
          doctype: 'Lead',
          name: 'CRM-LEAD-001',
        })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/lead.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/lead.vue"],
        doctypes=["Lead"],
    )

    _, edges = rpc_pass(app, graph)

    edge = _edge(edges, "file:myapp/frontend/lead.vue", "DocType:Lead", "READS_DOCTYPE")
    assert edge is not None
    assert edge["confidence"] == "INFERRED"


def test_create_list_resource_emits_reads_doctype(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script>
        const list = createListResource({
          doctype: 'Lead',
          filters: { status: 'Open' },
          fields: ['name'],
        })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/leads.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/leads.vue"],
        doctypes=["Lead"],
    )

    _, edges = rpc_pass(app, graph)

    edge = _edge(edges, "file:myapp/frontend/leads.vue", "DocType:Lead", "READS_DOCTYPE")
    assert edge is not None


# --- tests: composables ----------------------------------------------------


def test_use_doc_emits_reads_doctype(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script setup>
        const lead = useDoc('Lead', 'CRM-LEAD-001')
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/comp.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/comp.vue"],
        doctypes=["Lead"],
    )

    _, edges = rpc_pass(app, graph)

    edge = _edge(edges, "file:myapp/frontend/comp.vue", "DocType:Lead", "READS_DOCTYPE")
    assert edge is not None


def test_use_list_emits_reads_doctype(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script setup>
        const leads = useList('Lead', { filters: {} })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/list_comp.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/list_comp.vue"],
        doctypes=["Lead"],
    )

    _, edges = rpc_pass(app, graph)

    edge = _edge(edges, "file:myapp/frontend/list_comp.vue", "DocType:Lead", "READS_DOCTYPE")
    assert edge is not None


def test_use_call_emits_rpc_calls_edge(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script setup>
        const ping = useCall('myapp.api.ping')
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/ping.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/ping.vue"],
        whitelist_methods=[("function:myapp.api.ping", "myapp/api.py", "myapp.api.ping")],
    )

    _, edges = rpc_pass(app, graph)

    edge = _edge(edges, "file:myapp/frontend/ping.vue", "function:myapp.api.ping", "RPC_CALLS")
    assert edge is not None


def test_use_doctype_emits_reads_doctype(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script setup>
        const Lead = useDoctype('Lead')
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/dt.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/dt.vue"],
        doctypes=["Lead"],
    )

    _, edges = rpc_pass(app, graph)

    edge = _edge(edges, "file:myapp/frontend/dt.vue", "DocType:Lead", "READS_DOCTYPE")
    assert edge is not None


# --- tests: frappe.client.* builtins ---------------------------------------


def test_frappe_client_get_list_builtin_plus_reads_doctype(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script>
        frappe.call({
          method: 'frappe.client.get_list',
          args: { doctype: 'Lead', filters: {} },
        })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/builtin.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/builtin.vue"],
        doctypes=["Lead"],
    )

    nodes, edges = rpc_pass(app, graph)

    # Synthetic builtin node added.
    builtin_ids = [n for n in nodes if n["id"] == "BUILTIN:frappe.client.get_list"]
    assert len(builtin_ids) == 1
    assert builtin_ids[0]["kind"] == "BuiltinMethod"
    assert builtin_ids[0]["label"] == "frappe.client.get_list"

    # RPC_CALLS edge to the builtin.
    rpc_edge = _edge(
        edges,
        "file:myapp/frontend/builtin.vue",
        "BUILTIN:frappe.client.get_list",
        "RPC_CALLS",
    )
    assert rpc_edge is not None

    # READS_DOCTYPE edge to Lead (get_list is a read).
    reads_edge = _edge(
        edges,
        "file:myapp/frontend/builtin.vue",
        "DocType:Lead",
        "READS_DOCTYPE",
    )
    assert reads_edge is not None


def test_frappe_client_set_value_builtin_plus_writes_doctype(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script>
        frappe.call({
          method: 'frappe.client.set_value',
          args: { doctype: 'Lead', name: 'X', fieldname: 'status', value: 'Open' },
        })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/setv.vue": vue})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/setv.vue"],
        doctypes=["Lead"],
    )

    nodes, edges = rpc_pass(app, graph)

    # Builtin node.
    assert any(n["id"] == "BUILTIN:frappe.client.set_value" for n in nodes)

    # RPC_CALLS to builtin.
    assert _edge(
        edges,
        "file:myapp/frontend/setv.vue",
        "BUILTIN:frappe.client.set_value",
        "RPC_CALLS",
    ) is not None

    # WRITES_DOCTYPE to Lead.
    assert _edge(
        edges,
        "file:myapp/frontend/setv.vue",
        "DocType:Lead",
        "WRITES_DOCTYPE",
    ) is not None


# --- tests: missing RPC ----------------------------------------------------


def test_unknown_rpc_emits_missing_rpc_placeholder(tmp_path: Path) -> None:
    vue = dedent(
        """
        <script>
        frappe.call({ method: 'unknown.path.foo' })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/missing.vue": vue})
    graph = _seed_graph(file_nodes=["myapp/frontend/missing.vue"])

    nodes, edges = rpc_pass(app, graph)

    placeholder = [n for n in nodes if n["id"] == "MISSING_RPC:unknown.path.foo"]
    assert len(placeholder) == 1
    assert placeholder[0]["kind"] == "MissingRPC"
    assert placeholder[0]["label"] == "unknown.path.foo"

    edge = _edge(
        edges,
        "file:myapp/frontend/missing.vue",
        "MISSING_RPC:unknown.path.foo",
        "RPC_CALLS",
    )
    assert edge is not None
    assert edge["confidence"] == "INFERRED"


# --- tests: file not in graph ----------------------------------------------


def test_call_in_unindexed_file_is_skipped(tmp_path: Path) -> None:
    """If graphify didn't index the .vue/.js file, the pass should produce no
    edges and no synthetic nodes for that file's calls.
    """
    vue = dedent(
        """
        <script>
        frappe.call({ method: 'myapp.api.ping' })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/orphan.vue": vue})
    # Whitelist node exists, but no File node for the .vue file.
    graph = _seed_graph(
        whitelist_methods=[("function:myapp.api.ping", "myapp/api.py", "myapp.api.ping")],
    )

    nodes, edges = rpc_pass(app, graph)

    assert nodes == []
    assert edges == []


# --- tests: dedup within a single pass invocation --------------------------


def test_synthetic_nodes_deduped_within_pass(tmp_path: Path) -> None:
    """Two calls to the same unknown URL should yield one MISSING_RPC node."""
    vue = dedent(
        """
        <script>
        frappe.call({ method: 'unknown.path.foo' })
        frappe.call({ method: 'unknown.path.foo' })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/twice.vue": vue})
    graph = _seed_graph(file_nodes=["myapp/frontend/twice.vue"])

    nodes, edges = rpc_pass(app, graph)

    placeholders = [n for n in nodes if n["id"] == "MISSING_RPC:unknown.path.foo"]
    assert len(placeholders) == 1
    rpc_edges = [
        e for e in edges
        if e.get("source") == "file:myapp/frontend/twice.vue"
        and e.get("target") == "MISSING_RPC:unknown.path.foo"
        and e.get("relation") == "RPC_CALLS"
    ]
    assert len(rpc_edges) == 1


# --- tests: js/ts scanning -------------------------------------------------


def test_js_file_is_scanned(tmp_path: Path) -> None:
    js = "createResource({ url: 'myapp.api.ping' })\n"
    app = _make_app(tmp_path, {"myapp/frontend/util.js": js})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/util.js"],
        whitelist_methods=[("function:myapp.api.ping", "myapp/api.py", "myapp.api.ping")],
    )

    _, edges = rpc_pass(app, graph)

    assert _edge(edges, "file:myapp/frontend/util.js", "function:myapp.api.ping", "RPC_CALLS") is not None


def test_ts_file_is_scanned(tmp_path: Path) -> None:
    ts = "useCall('myapp.api.ping')\n"
    app = _make_app(tmp_path, {"myapp/frontend/util.ts": ts})
    graph = _seed_graph(
        file_nodes=["myapp/frontend/util.ts"],
        whitelist_methods=[("function:myapp.api.ping", "myapp/api.py", "myapp.api.ping")],
    )

    _, edges = rpc_pass(app, graph)

    assert _edge(edges, "file:myapp/frontend/util.ts", "function:myapp.api.ping", "RPC_CALLS") is not None


# --- integration: orchestrator round-trip ---------------------------------


def test_enrich_orchestrator_merges_rpc_nodes_and_edges(tmp_path: Path) -> None:
    """Running `enrich()` with `rpc_pass` registered writes the synthetic
    nodes and edges into the on-disk graph.json."""
    vue = dedent(
        """
        <script>
        frappe.call({ method: 'myapp.api.ping' })
        frappe.call({
          method: 'frappe.client.get_list',
          args: { doctype: 'Lead' },
        })
        frappe.call({ method: 'unknown.path.foo' })
        </script>
        """
    )
    app = _make_app(tmp_path, {"myapp/frontend/all.vue": vue})

    graph = _seed_graph(
        file_nodes=["myapp/frontend/all.vue"],
        whitelist_methods=[("function:myapp.api.ping", "myapp/api.py", "myapp.api.ping")],
        doctypes=["Lead"],
    )
    out = tmp_path / "frappe-graph-out"
    out.mkdir()
    graph_path = out / "graph.json"
    graph_path.write_text(json.dumps(graph))

    enrich(tmp_path, graph_path, [rpc_pass])

    on_disk = json.loads(graph_path.read_text())
    node_ids = {n["id"] for n in on_disk["nodes"]}

    assert "BUILTIN:frappe.client.get_list" in node_ids
    assert "MISSING_RPC:unknown.path.foo" in node_ids

    edges = on_disk["edges"]
    assert _edge(edges, "file:myapp/frontend/all.vue", "function:myapp.api.ping", "RPC_CALLS") is not None
    assert _edge(edges, "file:myapp/frontend/all.vue", "BUILTIN:frappe.client.get_list", "RPC_CALLS") is not None
    assert _edge(edges, "file:myapp/frontend/all.vue", "DocType:Lead", "READS_DOCTYPE") is not None
    assert _edge(edges, "file:myapp/frontend/all.vue", "MISSING_RPC:unknown.path.foo", "RPC_CALLS") is not None
