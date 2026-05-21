"""Tests for the hooks.py enrichment pass.

Each test builds a self-contained synthetic mini-app inside `tmp_path` —
hooks.py plus any referenced handler files — and an in-memory graph dict
mirroring the shape graphify emits. We do NOT touch the shared
`sample_app` fixture or its baseline_graph.json.
"""

from __future__ import annotations

from pathlib import Path

from frappe_graph.passes.hooks import hooks_pass


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _baseline_graph(extra_nodes: list[dict] | None = None) -> dict:
    """An empty-ish graph with the extra nodes spliced in."""
    return {"nodes": list(extra_nodes or []), "edges": []}


def _doctype_node(name: str) -> dict:
    return {
        "id": f"DocType:{name}",
        "kind": "DocType",
        "label": name,
    }


def _function_node(qualified_name: str, source_file: str) -> dict:
    return {
        "id": f"function:{qualified_name}",
        "kind": "Function",
        "qualified_name": qualified_name,
        "source_file": source_file,
        "label": qualified_name.rsplit(".", 1)[-1],
    }


# --- doc_events --------------------------------------------------------------


def test_doc_events_resolved_handler_extracted(tmp_path: Path) -> None:
    _write(tmp_path / "myapp" / "hooks.py", """
doc_events = {
    "Customer": {"before_save": "myapp.api.ping"},
}
""")
    _write(tmp_path / "myapp" / "api.py", "def ping():\n    pass\n")

    graph = _baseline_graph([
        _doctype_node("Customer"),
        _function_node("myapp.api.ping", "myapp/api.py"),
    ])

    nodes, edges = hooks_pass(tmp_path, graph)

    assert nodes == []
    assert len(edges) == 1
    edge = edges[0]
    assert edge["source"] == "DocType:Customer"
    assert edge["target"] == "function:myapp.api.ping"
    assert edge["relation"] == "before_save"
    assert edge["confidence"] == "EXTRACTED"


def test_doc_events_unresolved_handler_inferred(tmp_path: Path) -> None:
    _write(tmp_path / "myapp" / "hooks.py", """
doc_events = {
    "Customer": {"on_submit": "third_party.module.handler"},
}
""")

    graph = _baseline_graph([_doctype_node("Customer")])

    _, edges = hooks_pass(tmp_path, graph)

    assert len(edges) == 1
    edge = edges[0]
    assert edge["source"] == "DocType:Customer"
    # Dotted path verbatim, not a node id.
    assert edge["target"] == "third_party.module.handler"
    assert edge["relation"] == "on_submit"
    assert edge["confidence"] == "INFERRED"


def test_doc_events_multiple_events_per_doctype(tmp_path: Path) -> None:
    _write(tmp_path / "myapp" / "hooks.py", """
doc_events = {
    "Customer": {
        "before_save": "myapp.api.before",
        "after_save": "myapp.api.after",
        "validate": "myapp.api.validate_handler",
    },
}
""")
    _write(tmp_path / "myapp" / "api.py", "def before(): pass\ndef after(): pass\ndef validate_handler(): pass\n")

    graph = _baseline_graph([
        _doctype_node("Customer"),
        _function_node("myapp.api.before", "myapp/api.py"),
        _function_node("myapp.api.after", "myapp/api.py"),
        _function_node("myapp.api.validate_handler", "myapp/api.py"),
    ])

    _, edges = hooks_pass(tmp_path, graph)
    relations = {e["relation"] for e in edges}
    assert relations == {"before_save", "after_save", "validate"}
    for e in edges:
        assert e["source"] == "DocType:Customer"
        assert e["confidence"] == "EXTRACTED"


# --- scheduler_events --------------------------------------------------------


def test_scheduler_events_daily_and_cron(tmp_path: Path) -> None:
    _write(tmp_path / "myapp" / "hooks.py", """
scheduler_events = {
    "daily": [
        "myapp.tasks.cleanup",
    ],
    "cron": {
        "0 */6 * * *": [
            "myapp.tasks.every_six_hours",
        ],
    },
}
""")
    _write(tmp_path / "myapp" / "tasks.py", "def cleanup(): pass\ndef every_six_hours(): pass\n")

    graph = _baseline_graph([
        _function_node("myapp.tasks.cleanup", "myapp/tasks.py"),
        _function_node("myapp.tasks.every_six_hours", "myapp/tasks.py"),
    ])

    nodes, edges = hooks_pass(tmp_path, graph)

    node_ids = {n["id"] for n in nodes}
    assert "Scheduler:daily" in node_ids
    assert "Scheduler:cron:0 */6 * * *" in node_ids
    for n in nodes:
        assert n["kind"] == "SchedulerInterval"

    edge_keys = {(e["source"], e["target"], e["relation"]) for e in edges}
    assert ("Scheduler:daily", "function:myapp.tasks.cleanup", "runs") in edge_keys
    assert (
        "Scheduler:cron:0 */6 * * *",
        "function:myapp.tasks.every_six_hours",
        "runs",
    ) in edge_keys

    for e in edges:
        assert e["confidence"] == "EXTRACTED"


# --- override_doctype_class --------------------------------------------------


def test_override_doctype_class(tmp_path: Path) -> None:
    _write(tmp_path / "myapp" / "hooks.py", """
override_doctype_class = {
    "Customer": "myapp.overrides.CustomCustomer",
}
""")

    graph = _baseline_graph([
        _doctype_node("Customer"),
        {
            "id": "class:myapp.overrides.CustomCustomer",
            "kind": "Class",
            "qualified_name": "myapp.overrides.CustomCustomer",
            "source_file": "myapp/overrides.py",
            "label": "CustomCustomer",
        },
    ])

    _, edges = hooks_pass(tmp_path, graph)

    assert len(edges) == 1
    edge = edges[0]
    assert edge["source"] == "DocType:Customer"
    assert edge["target"] == "class:myapp.overrides.CustomCustomer"
    assert edge["relation"] == "overrides-controller"
    assert edge["confidence"] == "EXTRACTED"


# --- permission_query_conditions --------------------------------------------


def test_permission_query_conditions(tmp_path: Path) -> None:
    _write(tmp_path / "myapp" / "hooks.py", """
permission_query_conditions = {
    "Sales Order": "myapp.permissions.sales_order_query",
}
""")

    graph = _baseline_graph([
        _doctype_node("Sales Order"),
        _function_node("myapp.permissions.sales_order_query", "myapp/permissions.py"),
    ])

    _, edges = hooks_pass(tmp_path, graph)

    assert len(edges) == 1
    edge = edges[0]
    assert edge["source"] == "DocType:Sales Order"
    assert edge["target"] == "function:myapp.permissions.sales_order_query"
    assert edge["relation"] == "permission-query"
    assert edge["confidence"] == "EXTRACTED"


# --- has_permission ----------------------------------------------------------


def test_has_permission(tmp_path: Path) -> None:
    _write(tmp_path / "myapp" / "hooks.py", """
has_permission = {
    "Sales Order": "myapp.permissions.has_so_perm",
}
""")

    # Note: handler is intentionally NOT in the graph here, exercising the
    # INFERRED branch on a single-string-per-doctype map as well.
    graph = _baseline_graph([_doctype_node("Sales Order")])

    _, edges = hooks_pass(tmp_path, graph)

    assert len(edges) == 1
    edge = edges[0]
    assert edge["source"] == "DocType:Sales Order"
    assert edge["target"] == "myapp.permissions.has_so_perm"
    assert edge["relation"] == "has-permission"
    assert edge["confidence"] == "INFERRED"


# --- no hooks / nothing recognised ------------------------------------------


def test_no_hooks_py_emits_nothing(tmp_path: Path) -> None:
    # No hooks.py at all under tmp_path/<slug>/.
    (tmp_path / "myapp").mkdir()
    graph = _baseline_graph()
    nodes, edges = hooks_pass(tmp_path, graph)
    assert nodes == []
    assert edges == []


def test_doc_events_with_non_literal_keys_skips_only_those_entries(tmp_path: Path) -> None:
    """ERPNext's hooks.py has `tuple(some_list): {...}` mixed into doc_events.

    ast.literal_eval can't evaluate the whole dict, but the literal-keyed
    entries (e.g. "Sales Invoice") must still produce edges.
    """
    _write(tmp_path / "myapp" / "hooks.py", """
period_closing_doctypes = ["Sales Invoice"]

doc_events = {
    tuple(period_closing_doctypes): {
        "validate": "myapp.validators.check_period",
    },
    "Sales Invoice": {
        "on_submit": "myapp.handlers.on_submit",
    },
}
""")
    graph = _baseline_graph([_doctype_node("Sales Invoice")])

    _, edges = hooks_pass(tmp_path, graph)

    si_edges = [e for e in edges if e["source"] == "DocType:Sales Invoice"]
    assert len(si_edges) == 1
    assert si_edges[0]["relation"] == "on_submit"
    assert si_edges[0]["target"] == "myapp.handlers.on_submit"


def test_hooks_py_without_recognised_keys_emits_nothing(tmp_path: Path) -> None:
    _write(tmp_path / "myapp" / "hooks.py", """
app_name = "myapp"
app_title = "My App"
app_version = "0.0.1"
some_other_setting = {"foo": "bar"}
""")
    graph = _baseline_graph()
    nodes, edges = hooks_pass(tmp_path, graph)
    assert nodes == []
    assert edges == []
