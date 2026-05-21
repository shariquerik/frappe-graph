"""Tests for the frappe.* doc/db references enrichment pass.

Each test builds a self-contained synthetic mini-app under ``tmp_path`` and a
matching in-memory graph dict with pre-seeded function / file / DocType nodes.
We never touch the shared ``sample_app`` fixture or its baseline graph.
"""

from __future__ import annotations

from pathlib import Path

from frappe_graph.passes.refs import refs_pass


# --- helpers ---------------------------------------------------------------


def _make_app(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create files under tmp_path. Keys are relative paths from the app root.

    Always ensures hooks.py and __init__.py exist so the layout looks like a
    real Frappe app (`<app_path>/myapp/...`).
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
    function_nodes: list[tuple[str, str, str]] | None = None,
    file_nodes: list[str] | None = None,
    method_nodes: list[tuple[str, str, str]] | None = None,
    doctypes: list[str] | None = None,
) -> dict:
    """Build a minimal graph dict.

    - ``function_nodes``: list of (node_id, source_file, function_name).
    - ``method_nodes``: list of (node_id, source_file, method_name) — same shape
      but emitted with kind=Method.
    - ``file_nodes``: list of source_file paths (the id is "file:<path>").
    - ``doctypes``: list of DocType names; emitted as "DocType:<name>" nodes.
    """
    nodes: list[dict] = []
    for path in file_nodes or []:
        nodes.append({
            "id": f"file:{path}",
            "label": Path(path).name,
            "kind": "File",
            "source_file": path,
        })
    for nid, src, name in function_nodes or []:
        nodes.append({
            "id": nid,
            "label": name,
            "kind": "Function",
            "name": name,
            "source_file": src,
        })
    for nid, src, name in method_nodes or []:
        nodes.append({
            "id": nid,
            "label": name,
            "kind": "Method",
            "name": name,
            "source_file": src,
        })
    for dt in doctypes or []:
        nodes.append({
            "id": f"DocType:{dt}",
            "label": dt,
            "kind": "DocType",
        })
    return {"nodes": nodes, "edges": []}


def _edges_between(edges: list[dict], source: str, target: str) -> list[dict]:
    return [e for e in edges if e.get("source") == source and e.get("target") == target]


# --- tests -----------------------------------------------------------------


def test_get_doc_in_function_emits_reads_edge(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "\n"
        "def load_invoice(name):\n"
        '    return frappe.get_doc("Sales Invoice", name)\n'
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        function_nodes=[("func:myapp.services.load_invoice", "myapp/services.py", "load_invoice")],
        doctypes=["Sales Invoice"],
    )

    nodes, edges = refs_pass(app, graph)

    assert nodes == []
    assert len(edges) == 1
    edge = edges[0]
    assert edge["source"] == "func:myapp.services.load_invoice"
    assert edge["target"] == "DocType:Sales Invoice"
    assert edge["relation"] == "READS_DOCTYPE"
    assert edge["confidence"] == "INFERRED"


def test_get_list_and_get_all_emit_reads_edges(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "\n"
        "def fetch():\n"
        '    a = frappe.get_list("Customer")\n'
        '    b = frappe.get_all("Customer", filters={})\n'
        "    return a, b\n"
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        function_nodes=[("func:myapp.services.fetch", "myapp/services.py", "fetch")],
        doctypes=["Customer"],
    )

    _, edges = refs_pass(app, graph)

    reads = [e for e in edges if e["relation"] == "READS_DOCTYPE"]
    assert len(reads) == 2
    for e in reads:
        assert e["source"] == "func:myapp.services.fetch"
        assert e["target"] == "DocType:Customer"
        assert e["confidence"] == "INFERRED"


def test_new_doc_emits_writes_edge(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "\n"
        "def make():\n"
        '    return frappe.new_doc("Sales Order")\n'
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        function_nodes=[("func:myapp.services.make", "myapp/services.py", "make")],
        doctypes=["Sales Order"],
    )

    _, edges = refs_pass(app, graph)

    assert len(edges) == 1
    edge = edges[0]
    assert edge["source"] == "func:myapp.services.make"
    assert edge["target"] == "DocType:Sales Order"
    assert edge["relation"] == "WRITES_DOCTYPE"
    assert edge["confidence"] == "INFERRED"


def test_db_get_value_and_get_list_emit_reads_edges(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "\n"
        "def lookup():\n"
        '    v = frappe.db.get_value("Item", "ABC", "stock_uom")\n'
        '    rows = frappe.db.get_list("Item", filters={})\n'
        "    return v, rows\n"
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        function_nodes=[("func:myapp.services.lookup", "myapp/services.py", "lookup")],
        doctypes=["Item"],
    )

    _, edges = refs_pass(app, graph)

    assert len(edges) == 2
    for e in edges:
        assert e["source"] == "func:myapp.services.lookup"
        assert e["target"] == "DocType:Item"
        assert e["relation"] == "READS_DOCTYPE"
        assert e["confidence"] == "INFERRED"


def test_db_set_value_emits_writes_edge(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "\n"
        "def touch():\n"
        '    frappe.db.set_value("Item", "ABC", "stock_uom", "Nos")\n'
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        function_nodes=[("func:myapp.services.touch", "myapp/services.py", "touch")],
        doctypes=["Item"],
    )

    _, edges = refs_pass(app, graph)

    assert len(edges) == 1
    assert edges[0]["relation"] == "WRITES_DOCTYPE"
    assert edges[0]["target"] == "DocType:Item"
    assert edges[0]["confidence"] == "INFERRED"


def test_delete_doc_emits_writes_edge(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "\n"
        "def remove():\n"
        '    frappe.delete_doc("Item", "ABC")\n'
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        function_nodes=[("func:myapp.services.remove", "myapp/services.py", "remove")],
        doctypes=["Item"],
    )

    _, edges = refs_pass(app, graph)

    assert len(edges) == 1
    assert edges[0]["relation"] == "WRITES_DOCTYPE"
    assert edges[0]["source"] == "func:myapp.services.remove"
    assert edges[0]["target"] == "DocType:Item"


def test_variable_first_arg_emits_no_edge(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "\n"
        "def dynamic(doctype_var):\n"
        "    return frappe.get_doc(doctype_var)\n"
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        function_nodes=[("func:myapp.services.dynamic", "myapp/services.py", "dynamic")],
    )

    _, edges = refs_pass(app, graph)

    assert edges == []


def test_fstring_first_arg_emits_no_edge(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "\n"
        "def dynamic(x):\n"
        '    return frappe.get_doc(f"{x}")\n'
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        function_nodes=[("func:myapp.services.dynamic", "myapp/services.py", "dynamic")],
    )

    _, edges = refs_pass(app, graph)

    assert edges == []


def test_module_scope_call_uses_file_node_as_source(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "\n"
        'DEFAULT = frappe.get_doc("System Settings")\n'
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        file_nodes=["myapp/services.py"],
        doctypes=["System Settings"],
    )

    _, edges = refs_pass(app, graph)

    assert len(edges) == 1
    edge = edges[0]
    assert edge["source"] == "file:myapp/services.py"
    assert edge["target"] == "DocType:System Settings"
    assert edge["relation"] == "READS_DOCTYPE"
    assert edge["confidence"] == "INFERRED"


def test_call_inside_method_uses_method_node_as_source(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "\n"
        "class Foo:\n"
        "    def bar(self):\n"
        '        return frappe.get_doc("Customer", self.name)\n'
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        method_nodes=[("method:myapp.services.Foo.bar", "myapp/services.py", "bar")],
        doctypes=["Customer"],
    )

    _, edges = refs_pass(app, graph)

    assert len(edges) == 1
    edge = edges[0]
    assert edge["source"] == "method:myapp.services.Foo.bar"
    assert edge["target"] == "DocType:Customer"
    assert edge["relation"] == "READS_DOCTYPE"


def test_call_with_no_matching_node_is_skipped(tmp_path: Path) -> None:
    """If neither a function nor a file node exists in the graph for the call
    site, the pass should silently skip the call rather than fabricate an id.
    """
    code = (
        "import frappe\n"
        "\n"
        "def orphan():\n"
        '    return frappe.get_doc("Customer")\n'
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(doctypes=["Customer"])

    _, edges = refs_pass(app, graph)

    assert edges == []


def test_unrelated_calls_are_ignored(tmp_path: Path) -> None:
    code = (
        "import frappe\n"
        "import json\n"
        "\n"
        "def noisy():\n"
        '    json.loads("{}")\n'
        '    frappe.msgprint("hi")\n'
        '    frappe.local.cache().get("k")\n'
    )
    app = _make_app(tmp_path, {"myapp/services.py": code})
    graph = _seed_graph(
        function_nodes=[("func:myapp.services.noisy", "myapp/services.py", "noisy")],
    )

    _, edges = refs_pass(app, graph)

    assert edges == []
