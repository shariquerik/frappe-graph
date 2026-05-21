"""Frontend <-> backend RPC enrichment pass (frappe-ui-aware).

Scans `.vue`, `.js`, `.ts`, `.jsx`, `.tsx` files under the app for string-literal
arguments to Frappe RPC / frappe-ui APIs. For each matched call, emits one or
two edges from the calling file's File node:

  - `RPC_CALLS`        -> WhitelistedMethod node (matched by `rpc_url` set by
                          the whitelist pass), or to a synthetic
                          `BUILTIN:<method>` / `MISSING_RPC:<url>` node when
                          the URL doesn't resolve.
  - `READS_DOCTYPE` /  -> `DocType:<X>` when the call has an explicit
    `WRITES_DOCTYPE`     doctype argument (resource methods + composables are
                          always reads; `frappe.client.*` builtins are reads
                          for `get_*` and writes for `set_value`/`insert`/
                          `delete`).

All edges have `confidence: "INFERRED"` — they're string-matched, not parsed.

Recognised call shapes:

  Classic Frappe:
    frappe.call({ method: '<dotted.path>', args: { doctype: '<X>' } })

  frappe-ui resources:
    createResource({ url: '<dotted.path>' })
    createResource({ cache: { url: '<dotted.path>' } })
    createDocumentResource({ doctype: '<X>', name: '<...>' })
    createListResource({ doctype: '<X>', filters: {...} })

  frappe-ui composables:
    useDoc('<DocType>', '<name>')
    useList('<DocType>', {...})
    useCall('<dotted.path>')
    useDoctype('<DocType>')

If the call site lives in a file that isn't in the graph (graphify didn't
index it), the call is skipped — we never invent file nodes.
"""

from __future__ import annotations

import re
from pathlib import Path


SCANNED_SUFFIXES = {".vue", ".js", ".ts", ".jsx", ".tsx"}

SKIP_DIR_NAMES = {
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

# Frappe-builtin RPC paths whose presence we recognise even without a
# whitelist match. The value is the relation kind we infer for the `doctype:`
# arg (if any) — `None` means "no doctype edge inferred for this builtin".
_BUILTIN_RPCS: dict[str, str | None] = {
    "frappe.client.get_list": "READS_DOCTYPE",
    "frappe.client.get_value": "READS_DOCTYPE",
    "frappe.client.set_value": "WRITES_DOCTYPE",
    "frappe.client.insert": "WRITES_DOCTYPE",
    "frappe.client.delete": "WRITES_DOCTYPE",
}


# --- regex helpers ---------------------------------------------------------

# Capture the (balanced-ish) first object literal inside the call. We don't try
# to balance braces perfectly — `[^{}]*?` is good enough for the common case of
# `someFn({ ... })` with no nested braces in the outer level. Nested objects
# (e.g. `cache: { ... }`, `filters: { ... }`) are handled by additional inner
# regexes that scan inside the captured body.

# frappe.call({ ... })
_FRAPPE_CALL = re.compile(
    r"frappe\s*\.\s*call\s*\(\s*\{(?P<body>.*?)\}\s*\)",
    re.DOTALL,
)

# createResource({ ... }) — also catches createListResource / createDocumentResource
# but we have dedicated patterns for those so the matcher dispatches by name.
_CREATE_RESOURCE = re.compile(
    r"createResource\s*\(\s*\{(?P<body>.*?)\}\s*\)",
    re.DOTALL,
)
_CREATE_DOCUMENT_RESOURCE = re.compile(
    r"createDocumentResource\s*\(\s*\{(?P<body>.*?)\}\s*\)",
    re.DOTALL,
)
_CREATE_LIST_RESOURCE = re.compile(
    r"createListResource\s*\(\s*\{(?P<body>.*?)\}\s*\)",
    re.DOTALL,
)

# Composables — first positional arg is a string literal.
_USE_DOC = re.compile(
    r"useDoc\s*\(\s*['\"](?P<doctype>[^'\"]+)['\"]",
)
_USE_LIST = re.compile(
    r"useList\s*\(\s*['\"](?P<doctype>[^'\"]+)['\"]",
)
_USE_CALL = re.compile(
    r"useCall\s*\(\s*['\"](?P<url>[^'\"]+)['\"]",
)
_USE_DOCTYPE = re.compile(
    r"useDoctype\s*\(\s*['\"](?P<doctype>[^'\"]+)['\"]",
)

# Inside an object body: pluck the first occurrence of `<key>: '<value>'` for
# the keys we care about. The body matches we work on are bounded by `{...}`
# so this is safe enough.
_KEY_STRING = {
    "method": re.compile(r"\bmethod\s*:\s*['\"]([^'\"]+)['\"]"),
    "url": re.compile(r"\burl\s*:\s*['\"]([^'\"]+)['\"]"),
    "doctype": re.compile(r"\bdoctype\s*:\s*['\"]([^'\"]+)['\"]"),
}

# A nested `cache: { ... url: 'X' ... }` block inside a createResource body.
_CACHE_BLOCK = re.compile(
    r"\bcache\s*:\s*\{(?P<inner>.*?)\}",
    re.DOTALL,
)


def _extract_key(body: str, key: str) -> str | None:
    m = _KEY_STRING[key].search(body)
    return m.group(1) if m else None


def _extract_url_from_resource_body(body: str) -> str | None:
    """A createResource body may put the url at the top level or inside a
    `cache: { ... }` block. Try both.
    """
    direct = _extract_key(body, "url")
    if direct is not None:
        return direct
    cache_match = _CACHE_BLOCK.search(body)
    if cache_match is not None:
        inner = cache_match.group("inner")
        return _extract_key(inner, "url")
    return None


# --- graph lookup helpers --------------------------------------------------


def _file_node_lookup(graph: dict) -> dict[str, str]:
    """Map relative source file path -> File-kind node id."""
    out: dict[str, str] = {}
    for n in graph.get("nodes", []):
        if n.get("kind") != "File":
            continue
        src = n.get("source_file") or n.get("path")
        if not src:
            continue
        out[str(Path(src))] = n["id"]
    return out


def _rpc_url_lookup(graph: dict) -> dict[str, str]:
    """Map rpc_url -> node id (set by the whitelist pass)."""
    out: dict[str, str] = {}
    for n in graph.get("nodes", []):
        url = n.get("rpc_url")
        if url:
            out[url] = n["id"]
    return out


def _doctype_ids(graph: dict) -> set[str]:
    return {n["id"] for n in graph.get("nodes", []) if n.get("kind") == "DocType"}


# --- scanning --------------------------------------------------------------


def _iter_frontend_files(app_path: Path):
    for path in app_path.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        try:
            rel_parts = path.relative_to(app_path).parts
        except ValueError:
            continue
        if any(part in SKIP_DIR_NAMES or part.startswith(".") for part in rel_parts[:-1]):
            continue
        yield path


def _find_calls(source: str) -> list[tuple[str, str | None, str | None]]:
    """Return a list of (kind, url, doctype) tuples for each recognised call.

    `kind` is one of: "rpc", "doctype_read".
    `url` is set for rpc-bearing calls; `doctype` is set when the call carries
    a `doctype:` argument or a positional DocType string.
    """
    hits: list[tuple[str, str | None, str | None]] = []

    # frappe.call({ method: ..., args: { doctype: ... } })
    for m in _FRAPPE_CALL.finditer(source):
        body = m.group("body")
        method = _extract_key(body, "method")
        if method is None:
            continue
        doctype = _extract_key(body, "doctype")
        hits.append(("rpc", method, doctype))

    # createResource({ url: ... }) or createResource({ cache: { url: ... } })
    for m in _CREATE_RESOURCE.finditer(source):
        body = m.group("body")
        url = _extract_url_from_resource_body(body)
        if url is None:
            continue
        doctype = _extract_key(body, "doctype")
        hits.append(("rpc", url, doctype))

    # createDocumentResource({ doctype: ..., name: ... })
    for m in _CREATE_DOCUMENT_RESOURCE.finditer(source):
        body = m.group("body")
        doctype = _extract_key(body, "doctype")
        if doctype is None:
            continue
        hits.append(("doctype_read", None, doctype))

    # createListResource({ doctype: ..., filters: {...} })
    for m in _CREATE_LIST_RESOURCE.finditer(source):
        body = m.group("body")
        doctype = _extract_key(body, "doctype")
        if doctype is None:
            continue
        hits.append(("doctype_read", None, doctype))

    # useDoc('X', '...')
    for m in _USE_DOC.finditer(source):
        hits.append(("doctype_read", None, m.group("doctype")))

    # useList('X', {...})
    for m in _USE_LIST.finditer(source):
        hits.append(("doctype_read", None, m.group("doctype")))

    # useDoctype('X')
    for m in _USE_DOCTYPE.finditer(source):
        hits.append(("doctype_read", None, m.group("doctype")))

    # useCall('X') — RPC, no doctype
    for m in _USE_CALL.finditer(source):
        hits.append(("rpc", m.group("url"), None))

    return hits


# --- pass entry point ------------------------------------------------------


def rpc_pass(app_path: Path, graph: dict) -> tuple[list[dict], list[dict]]:
    added_nodes: list[dict] = []
    added_edges: list[dict] = []
    seen_node_ids: set[str] = set()
    seen_edge_keys: set[tuple[str, str, str]] = set()

    app_path = Path(app_path).resolve()
    file_lookup = _file_node_lookup(graph)
    rpc_lookup = _rpc_url_lookup(graph)

    def _add_node(node: dict) -> None:
        nid = node["id"]
        if nid in seen_node_ids:
            return
        seen_node_ids.add(nid)
        added_nodes.append(node)

    def _add_edge(source: str, target: str, relation: str) -> None:
        key = (source, target, relation)
        if key in seen_edge_keys:
            return
        seen_edge_keys.add(key)
        added_edges.append({
            "source": source,
            "target": target,
            "relation": relation,
            "confidence": "INFERRED",
        })

    for path in _iter_frontend_files(app_path):
        try:
            rel = str(path.relative_to(app_path))
        except ValueError:
            continue
        source_node_id = file_lookup.get(rel)
        if source_node_id is None:
            # graphify didn't index this file — don't invent a node.
            continue

        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for kind, url, doctype in _find_calls(source):
            if kind == "rpc":
                assert url is not None
                # Resolve the RPC target.
                target_id: str
                builtin_relation: str | None = None
                if url in rpc_lookup:
                    target_id = rpc_lookup[url]
                elif url in _BUILTIN_RPCS:
                    target_id = f"BUILTIN:{url}"
                    _add_node({
                        "id": target_id,
                        "kind": "BuiltinMethod",
                        "label": url,
                    })
                    builtin_relation = _BUILTIN_RPCS[url]
                else:
                    target_id = f"MISSING_RPC:{url}"
                    _add_node({
                        "id": target_id,
                        "kind": "MissingRPC",
                        "label": url,
                    })

                _add_edge(source_node_id, target_id, "RPC_CALLS")

                # If the call carried an inner doctype, attach the relevant
                # read/write edge to that DocType.
                if doctype:
                    # For known builtins we know the read/write side; for a
                    # whitelist match or missing RPC we fall back to READS
                    # (frappe.call with a custom method + doctype arg is rare
                    # and we don't have enough info to call it a write).
                    relation = builtin_relation or "READS_DOCTYPE"
                    _add_edge(source_node_id, f"DocType:{doctype}", relation)

            elif kind == "doctype_read":
                assert doctype is not None
                _add_edge(source_node_id, f"DocType:{doctype}", "READS_DOCTYPE")

    return added_nodes, added_edges
