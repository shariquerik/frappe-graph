"""@frappe.whitelist() enrichment pass.

Walks every `.py` file under the app's inner package (the directory next to
`hooks.py`) and finds module-level functions decorated with
`@frappe.whitelist(...)`. For each match:

  - If the graph already has a Function/Method node for the function (matched
    by `source_file` + `name`, or by `qualified_name` equal to the dotted RPC
    path), the pass returns a node dict with the same id so the orchestrator's
    `_merge` mutates the existing node in place — tagging `kind` as
    `WhitelistedMethod` and adding `rpc_url` / `whitelist_args`.
  - Otherwise a brand-new node with id `function:<dotted.path>` is added.

No edges are emitted in this slice — slice #5 (RPC pass) consumes the
`rpc_url` property and emits the frontend ↔ backend edges.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


_FUNCTION_KINDS = {"Function", "Method"}


def _find_app_slug(app_path: Path) -> str | None:
    """The inner package is the directory containing `hooks.py`.

    Layout: `<app_path>/<slug>/hooks.py` — the slug is the Python package name
    used to build dotted RPC paths.
    """
    for hooks in app_path.glob("*/hooks.py"):
        return hooks.parent.name
    return None


def _decorator_matches_whitelist(decorator: ast.expr) -> ast.Call | None:
    """Return the ast.Call if `decorator` is `frappe.whitelist(...)`, else None.

    We require the call form (`@frappe.whitelist()` or `@frappe.whitelist(...)`).
    Bare `@frappe.whitelist` (no parens) isn't a thing in Frappe — but if we
    ever wanted to support it, the `Attribute` arm would catch it.
    """
    if isinstance(decorator, ast.Call):
        func = decorator.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "frappe"
            and func.attr == "whitelist"
        ):
            return decorator
    return None


def _literal_kwargs(call: ast.Call) -> dict[str, Any]:
    """Extract decorator kwargs whose values are literal constants."""
    out: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            continue  # **kwargs splat — skip
        try:
            out[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, SyntaxError):
            continue
    return out


def _module_path_parts(py_path: Path, app_path: Path, slug: str) -> list[str]:
    """Return the dotted path components from the slug down to the file stem.

    e.g. `<app>/<slug>/api/foo.py` → ["<slug>", "api", "foo"]
         `<app>/<slug>/api.py`     → ["<slug>", "api"]
    """
    rel = py_path.relative_to(app_path)
    parts = list(rel.with_suffix("").parts)
    # Sanity: the first component should be the slug. If not (e.g. file is
    # outside the inner package), bail.
    if not parts or parts[0] != slug:
        return []
    return parts


def _iter_py_files(app_path: Path, slug: str):
    """Yield every `.py` file under `<app_path>/<slug>/`."""
    pkg_root = app_path / slug
    if not pkg_root.is_dir():
        return
    for py_path in pkg_root.rglob("*.py"):
        if py_path.is_file():
            yield py_path


def _build_function_lookup(graph: dict) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """Build two lookups over Function/Method nodes:

      - (source_file, name) → node_id
      - qualified_name → node_id
    """
    by_src_name: dict[tuple[str, str], str] = {}
    by_qname: dict[str, str] = {}
    for n in graph.get("nodes", []):
        if n.get("kind") not in _FUNCTION_KINDS:
            continue
        nid = n.get("id")
        if not nid:
            continue
        src = n.get("source_file") or n.get("path")
        name = n.get("name") or n.get("label")
        if src and name:
            by_src_name[(str(Path(src)), name)] = nid
        qname = n.get("qualified_name")
        if qname:
            by_qname[qname] = nid
    return by_src_name, by_qname


def whitelist_pass(app_path: Path, graph: dict) -> tuple[list[dict], list[dict]]:
    added_nodes: list[dict] = []

    app_path = Path(app_path).resolve()
    slug = _find_app_slug(app_path)
    if slug is None:
        return [], []

    by_src_name, by_qname = _build_function_lookup(graph)

    for py_path in _iter_py_files(app_path, slug):
        module_parts = _module_path_parts(py_path, app_path, slug)
        if not module_parts:
            continue
        try:
            source = py_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        rel_source = str(py_path.relative_to(app_path))

        # Only module-level functions count as real RPC endpoints.
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            match_call: ast.Call | None = None
            for dec in node.decorator_list:
                match_call = _decorator_matches_whitelist(dec)
                if match_call is not None:
                    break
            if match_call is None:
                continue

            dotted = ".".join(module_parts + [node.name])
            kwargs = _literal_kwargs(match_call)

            # Find an existing node to mutate, preferring qualified_name match.
            existing_id = by_qname.get(dotted) or by_src_name.get((rel_source, node.name))
            node_id = existing_id or f"function:{dotted}"

            entry: dict[str, Any] = {
                "id": node_id,
                "kind": "WhitelistedMethod",
                "rpc_url": dotted,
            }
            if kwargs:
                entry["whitelist_args"] = kwargs

            if existing_id is None:
                # New node — fill in label/source_file/name so it isn't an
                # opaque stub.
                entry["label"] = node.name
                entry["name"] = node.name
                entry["source_file"] = rel_source
                entry["qualified_name"] = dotted

            added_nodes.append(entry)

    return added_nodes, []
