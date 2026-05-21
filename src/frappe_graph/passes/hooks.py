"""hooks.py enrichment pass.

Parses a Frappe app's `hooks.py` (without executing it) and emits edges
connecting DocTypes (or synthetic scheduler-interval nodes) to the handler
functions they wire up.

`hooks.py` is config-as-code: module-level assignments of literal dicts /
strings / lists. We walk the AST, pick out the names we care about, and call
`ast.literal_eval` on their values. No runtime import of `frappe` is required.

Recognised keys:

- ``doc_events = {"<DocType>": {"<event>": "<dotted.path>"}}`` →
  ``DocType:<X> --<event>--> <handler>``
- ``scheduler_events = {"<interval>": ["<dotted.path>", ...]}`` →
  ``Scheduler:<interval> --runs--> <handler>``. The ``cron`` value is a
  ``{"<cron_expr>": ["<handler>", ...]}`` sub-dict; each cron expression
  becomes its own ``Scheduler:cron:<expr>`` interval node.
- ``override_doctype_class = {"<DocType>": "<dotted.path>"}`` →
  ``--overrides-controller-->``
- ``permission_query_conditions = {"<DocType>": "<dotted.path>"}`` →
  ``--permission-query-->``
- ``has_permission = {"<DocType>": "<dotted.path>"}`` →
  ``--has-permission-->``

Edges use ``confidence: "EXTRACTED"`` because the dict literal is unambiguous.
If a referenced handler dotted path doesn't resolve to a function node already
in the graph, the edge still gets emitted, but with the dotted path as the
target id verbatim and ``confidence: "INFERRED"`` on that edge only.
"""

from __future__ import annotations

import ast
from pathlib import Path


RECOGNISED_KEYS = {
    "doc_events",
    "scheduler_events",
    "override_doctype_class",
    "permission_query_conditions",
    "has_permission",
}

SCHEDULER_INTERVALS = {"all", "hourly", "daily", "weekly", "monthly", "cron"}


def _find_hooks_py(app_path: Path) -> Path | None:
    """Find `<app_path>/<slug>/hooks.py`. There's exactly one in app mode."""
    matches = list(app_path.glob("*/hooks.py"))
    if len(matches) == 1:
        return matches[0]
    return None


def _partial_dict_literal(node: ast.AST) -> dict | None:
    """Try to extract a dict literal even when some keys/values are non-literal.

    Returns a dict of every `{key: value}` entry where BOTH key and value
    `literal_eval` cleanly. Skips entries that don't (e.g. ERPNext's
    `tuple(period_closing_doctypes): {...}`). Returns None if `node` is not a
    Dict at all.
    """
    if not isinstance(node, ast.Dict):
        return None
    out: dict = {}
    for k_node, v_node in zip(node.keys, node.values):
        if k_node is None:
            # `**spread` in dict literal — skip the entry.
            continue
        try:
            k = ast.literal_eval(k_node)
            v = ast.literal_eval(v_node)
        except (ValueError, SyntaxError):
            continue
        try:
            out[k] = v
        except TypeError:
            # Unhashable key — skip.
            continue
    return out


def _parse_hooks(hooks_path: Path) -> dict:
    """Parse hooks.py and return a dict of {name: literal_value} for the
    module-level assignments we recognise.

    Real Frappe apps (ERPNext, etc.) embed non-literal expressions like
    `tuple(period_closing_doctypes): {...}` inside an otherwise-literal dict.
    A whole-dict `ast.literal_eval` would fail on the whole assignment, so we
    fall back to entry-by-entry parsing for dict-shaped values — keeping the
    parseable entries and skipping only the non-literal ones.
    """
    try:
        source = hooks_path.read_text()
    except OSError:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    out: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        # Only handle single-target `Name = <literal>` assignments.
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name not in RECOGNISED_KEYS:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            # Fall back to entry-by-entry parsing for dict literals.
            partial = _partial_dict_literal(node.value)
            if partial is None:
                continue
            value = partial
        out[name] = value
    return out


def _handler_lookup(graph: dict) -> dict[str, str]:
    """Map a dotted handler path to a node id, for nodes that look like
    functions/methods/classes that hooks.py might point at.

    Prefers Function/Method/Class nodes' `qualified_name`, falls back to `id`.
    """
    lookup: dict[str, str] = {}
    for n in graph.get("nodes", []):
        kind = n.get("kind")
        if kind not in {"Function", "Method", "Class"}:
            continue
        nid = n.get("id")
        if not nid:
            continue
        qn = n.get("qualified_name")
        if qn and qn not in lookup:
            lookup[qn] = nid
        # Allow the id itself (e.g. "function:<dotted.path>") to match too.
        if nid not in lookup:
            lookup[nid] = nid
    return lookup


def _resolve(handler: str, lookup: dict[str, str]) -> tuple[str, str]:
    """Return (target_id, confidence). If the dotted path is in the graph,
    target the node id with EXTRACTED confidence; otherwise target the
    dotted path verbatim with INFERRED confidence.
    """
    if handler in lookup:
        return lookup[handler], "EXTRACTED"
    return handler, "INFERRED"


def _doctype_id(name: str) -> str:
    return f"DocType:{name}"


def _emit_doctype_handler_edge(
    doctype: str,
    handler: str,
    relation: str,
    lookup: dict[str, str],
) -> dict | None:
    if not isinstance(handler, str) or not handler:
        return None
    target, confidence = _resolve(handler, lookup)
    return {
        "source": _doctype_id(doctype),
        "target": target,
        "relation": relation,
        "confidence": confidence,
    }


def hooks_pass(app_path: Path, graph: dict) -> tuple[list[dict], list[dict]]:
    added_nodes: list[dict] = []
    added_edges: list[dict] = []

    app_path = Path(app_path).resolve()
    hooks_path = _find_hooks_py(app_path)
    if hooks_path is None:
        return added_nodes, added_edges

    parsed = _parse_hooks(hooks_path)
    if not parsed:
        return added_nodes, added_edges

    lookup = _handler_lookup(graph)

    # --- doc_events -----------------------------------------------------------
    doc_events = parsed.get("doc_events")
    if isinstance(doc_events, dict):
        for doctype, events in doc_events.items():
            if not isinstance(doctype, str) or not isinstance(events, dict):
                continue
            for event, handler in events.items():
                if not isinstance(event, str):
                    continue
                handlers = handler if isinstance(handler, list) else [handler]
                for h in handlers:
                    edge = _emit_doctype_handler_edge(doctype, h, event, lookup)
                    if edge:
                        added_edges.append(edge)

    # --- scheduler_events -----------------------------------------------------
    scheduler_events = parsed.get("scheduler_events")
    if isinstance(scheduler_events, dict):
        seen_intervals: set[str] = set()
        for interval, payload in scheduler_events.items():
            if not isinstance(interval, str) or interval not in SCHEDULER_INTERVALS:
                continue

            if interval == "cron" and isinstance(payload, dict):
                # Sub-dict: {"<cron_expr>": ["<handler>", ...]}
                for cron_expr, handlers in payload.items():
                    if not isinstance(cron_expr, str) or not isinstance(handlers, list):
                        continue
                    interval_label = f"cron:{cron_expr}"
                    interval_id = f"Scheduler:{interval_label}"
                    if interval_label not in seen_intervals:
                        seen_intervals.add(interval_label)
                        added_nodes.append({
                            "id": interval_id,
                            "kind": "SchedulerInterval",
                            "label": interval_label,
                        })
                    for h in handlers:
                        if not isinstance(h, str) or not h:
                            continue
                        target, confidence = _resolve(h, lookup)
                        added_edges.append({
                            "source": interval_id,
                            "target": target,
                            "relation": "runs",
                            "confidence": confidence,
                        })
                continue

            if not isinstance(payload, list):
                continue
            interval_id = f"Scheduler:{interval}"
            if interval not in seen_intervals:
                seen_intervals.add(interval)
                added_nodes.append({
                    "id": interval_id,
                    "kind": "SchedulerInterval",
                    "label": interval,
                })
            for h in payload:
                if not isinstance(h, str) or not h:
                    continue
                target, confidence = _resolve(h, lookup)
                added_edges.append({
                    "source": interval_id,
                    "target": target,
                    "relation": "runs",
                    "confidence": confidence,
                })

    # --- single-string-per-DocType maps ---------------------------------------
    for key, relation in (
        ("override_doctype_class", "overrides-controller"),
        ("permission_query_conditions", "permission-query"),
        ("has_permission", "has-permission"),
    ):
        mapping = parsed.get(key)
        if not isinstance(mapping, dict):
            continue
        for doctype, handler in mapping.items():
            if not isinstance(doctype, str):
                continue
            edge = _emit_doctype_handler_edge(doctype, handler, relation, lookup)
            if edge:
                added_edges.append(edge)

    return added_nodes, added_edges
