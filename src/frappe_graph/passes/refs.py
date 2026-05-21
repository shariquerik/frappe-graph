"""frappe.* doc/db reference enrichment pass.

Scans Python source under `<app_path>/<slug>/` for string-literal arguments to
the most common Frappe data-access calls and emits READS_DOCTYPE /
WRITES_DOCTYPE edges from the enclosing function (or file, for module-scope
calls) to `DocType:<X>`.

Recognised calls (first positional arg must be a string literal):

    frappe.get_doc("DT", ...)              -> READS_DOCTYPE
    frappe.get_list("DT", ...)             -> READS_DOCTYPE
    frappe.get_all("DT", ...)              -> READS_DOCTYPE
    frappe.new_doc("DT", ...)              -> WRITES_DOCTYPE
    frappe.db.get_value("DT", ...)         -> READS_DOCTYPE
    frappe.db.get_list("DT", ...)          -> READS_DOCTYPE
    frappe.db.set_value("DT", ...)         -> WRITES_DOCTYPE
    frappe.delete_doc("DT", ...)           -> WRITES_DOCTYPE

All edges have `confidence: "INFERRED"` — string-literal match is heuristic.
Non-literal first args (variables, f-strings, attribute access, etc.) are
skipped to avoid noise.
"""

from __future__ import annotations

import ast
from pathlib import Path


# Map (parent-chain, attr) -> ("READS"|"WRITES", relation_name).
# The parent chain is the chain of attribute names from the root Name down to
# (but not including) the final attr. So `frappe.get_doc` -> (("frappe",), "get_doc")
# and `frappe.db.get_value` -> (("frappe", "db"), "get_value").
_FRAPPE_CALLS: dict[tuple[tuple[str, ...], str], str] = {
    (("frappe",), "get_doc"): "READS_DOCTYPE",
    (("frappe",), "get_list"): "READS_DOCTYPE",
    (("frappe",), "get_all"): "READS_DOCTYPE",
    (("frappe",), "new_doc"): "WRITES_DOCTYPE",
    (("frappe",), "delete_doc"): "WRITES_DOCTYPE",
    (("frappe", "db"), "get_value"): "READS_DOCTYPE",
    (("frappe", "db"), "get_list"): "READS_DOCTYPE",
    (("frappe", "db"), "set_value"): "WRITES_DOCTYPE",
}


def _unparse_call_target(func: ast.AST) -> tuple[tuple[str, ...], str] | None:
    """Return (parent_chain, attr) for an `Attribute` chain rooted at a `Name`,
    or None if the call shape doesn't match (e.g. subscripts, calls,
    parenthesised expressions). The final `attr` is the called method name and
    the parent chain is the leading dotted path.
    """
    if not isinstance(func, ast.Attribute):
        return None
    final_attr = func.attr
    chain: list[str] = []
    node: ast.AST = func.value
    while isinstance(node, ast.Attribute):
        chain.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    chain.append(node.id)
    chain.reverse()
    return tuple(chain), final_attr


def _first_arg_string(call: ast.Call) -> str | None:
    """Return the first positional arg if it's a plain string constant, else None.
    f-strings (`JoinedStr`) and non-constants return None.
    """
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _iter_py_files(app_path: Path):
    """Yield every .py file under app_path (skipping hidden dirs and common
    junk directories).
    """
    skip_dir_names = {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "frappe-graph-out",
        "graphify-out",
        ".pytest_cache",
        ".mypy_cache",
        "build",
        "dist",
    }
    for path in app_path.rglob("*.py"):
        if any(part in skip_dir_names or part.startswith(".") for part in path.relative_to(app_path).parts[:-1]):
            continue
        yield path


def _build_node_lookups(
    graph: dict,
) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """Build two lookup dicts from the graph:

    1. ``func_lookup``: (source_file, function_name) -> node_id, for
       function/method nodes. We try a few common attribute names that
       graphify (or the doctype pass) may use for the name.
    2. ``file_lookup``: source_file -> file_node_id, for module-scope calls.
       Prefers nodes with kind=="File".
    """
    func_lookup: dict[tuple[str, str], str] = {}
    file_lookup: dict[str, str] = {}

    for n in graph.get("nodes", []):
        nid = n.get("id")
        if not nid:
            continue
        src = n.get("source_file") or n.get("path")
        if not src:
            continue
        key_src = str(Path(src))
        kind = n.get("kind")

        if kind == "File":
            file_lookup[key_src] = nid
        else:
            # File-kind takes precedence; only fill if no File node yet seen.
            file_lookup.setdefault(key_src, nid) if False else None

        if kind in {"Function", "Method", "AsyncFunction"}:
            for name_key in ("name", "qualified_name", "label"):
                name = n.get(name_key)
                if not name:
                    continue
                # `qualified_name` is often `pkg.mod.func` or `pkg.mod.Cls.method`
                short = str(name).rsplit(".", 1)[-1]
                func_lookup.setdefault((key_src, short), nid)
                func_lookup.setdefault((key_src, str(name)), nid)

    return func_lookup, file_lookup


class _CallVisitor(ast.NodeVisitor):
    """Walks an AST tracking the enclosing function/method, collecting matched
    frappe.* calls.

    Each hit is `(relation, doctype_name, enclosing_function_name_or_None)`
    where the function name is the *innermost* enclosing function/method. For
    a method `Cls.foo`, the name is just `foo` — graph lookups try both the
    short name and (if available) the qualified name.
    """

    def __init__(self) -> None:
        self._func_stack: list[str] = []
        self.hits: list[tuple[str, str, str | None]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        target = _unparse_call_target(node.func)
        if target is not None:
            relation = _FRAPPE_CALLS.get(target)
            if relation is not None:
                doctype = _first_arg_string(node)
                if doctype:
                    enclosing = self._func_stack[-1] if self._func_stack else None
                    self.hits.append((relation, doctype, enclosing))
        self.generic_visit(node)


def refs_pass(app_path: Path, graph: dict) -> tuple[list[dict], list[dict]]:
    added_nodes: list[dict] = []
    added_edges: list[dict] = []

    app_path = Path(app_path).resolve()
    func_lookup, file_lookup = _build_node_lookups(graph)

    for py_path in _iter_py_files(app_path):
        try:
            source = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(py_path))
        except SyntaxError:
            continue

        try:
            rel = str(py_path.relative_to(app_path))
        except ValueError:
            rel = str(py_path)

        visitor = _CallVisitor()
        visitor.visit(tree)

        for relation, doctype, enclosing in visitor.hits:
            source_id: str | None = None
            if enclosing is not None:
                source_id = func_lookup.get((rel, enclosing))
            if source_id is None:
                source_id = file_lookup.get(rel)
            if source_id is None:
                # Neither function nor file node found — skip rather than
                # invent a node id.
                continue

            added_edges.append({
                "source": source_id,
                "target": f"DocType:{doctype}",
                "relation": relation,
                "confidence": "INFERRED",
            })

    return added_nodes, added_edges
