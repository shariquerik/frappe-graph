"""DocType enrichment pass.

Walks `<app>/<module>/doctype/<slug>/<slug>.json` files, emits:
  - one node per DocType (id: `DocType:<Name>`, kind: `DocType`)
  - a `controller` edge from each DocType to its sibling `<slug>.py` node
  - one `LINK[<field>]` edge per Link / Table / Table MultiSelect / Dynamic Link
    field, pointing to `DocType:<options>`.

Edges use confidence `EXTRACTED` — the DocType JSON is structured data, not
inference.
"""

from __future__ import annotations

import json
from pathlib import Path


LINK_FIELDTYPES = {"Link", "Table", "Table MultiSelect"}
DYNAMIC_LINK_FIELDTYPE = "Dynamic Link"


def _iter_doctype_jsons(app_path: Path):
    """Yield (json_path, parsed_dict) for every DocType JSON under the app.

    Layout: <app_path>/<slug>/<module>/doctype/<doctype_slug>/<doctype_slug>.json
    We don't hardcode the inner package name — we glob for `*/doctype/*/*.json`.
    """
    for json_path in app_path.glob("*/*/doctype/*/*.json"):
        # The JSON's stem should match its parent directory's name (Frappe convention).
        if json_path.stem != json_path.parent.name:
            continue
        try:
            with json_path.open() as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("doctype") != "DocType":
            continue
        yield json_path, data


def _doctype_id(name: str) -> str:
    return f"DocType:{name}"


def _controller_path(json_path: Path) -> Path:
    return json_path.with_suffix(".py")


def _node_lookup_by_source(graph: dict) -> dict[str, str]:
    """Map source-file paths to node ids. Prefer File-kind nodes over Class/etc."""
    out: dict[str, str] = {}
    for n in graph.get("nodes", []):
        src = n.get("source_file") or n.get("path")
        if not src:
            continue
        key = str(Path(src))
        # Prefer File nodes — they're the right target for a controller edge.
        if key not in out or n.get("kind") == "File":
            out[key] = n["id"]
    return out


def _dynamic_link_targets(fields: list[dict], options_field: str) -> list[str]:
    """For a Dynamic Link field, the `options` value is the name of another
    field that holds the target DocType name. We can't statically resolve the
    set of possible targets from a single JSON, so we look for a sibling Select
    field with the same `fieldname` whose `options` are newline-separated
    DocType names — that's the Frappe convention.
    """
    targets: list[str] = []
    for f in fields:
        if f.get("fieldname") == options_field and f.get("fieldtype") == "Select":
            raw = f.get("options") or ""
            for line in raw.splitlines():
                line = line.strip()
                if line:
                    targets.append(line)
            break
    return targets


def doctype_pass(app_path: Path, graph: dict) -> tuple[list[dict], list[dict]]:
    added_nodes: list[dict] = []
    added_edges: list[dict] = []

    source_to_id = _node_lookup_by_source(graph)
    app_path = Path(app_path).resolve()

    for json_path, data in _iter_doctype_jsons(app_path):
        name = data.get("name") or json_path.stem
        node_id = _doctype_id(name)
        rel_source = str(json_path.relative_to(app_path)) if json_path.is_relative_to(app_path) else str(json_path)

        added_nodes.append({
            "id": node_id,
            "label": name,
            "kind": "DocType",
            "source_file": rel_source,
            "module": data.get("module"),
            "is_submittable": bool(data.get("is_submittable", 0)),
            "is_single": bool(data.get("issingle", 0)),
            "is_child_table": bool(data.get("istable", 0)),
            "autoname": data.get("autoname"),
        })

        # Controller edge.
        ctrl_path = _controller_path(json_path)
        if ctrl_path.is_file():
            rel_ctrl = str(ctrl_path.relative_to(app_path)) if ctrl_path.is_relative_to(app_path) else str(ctrl_path)
            ctrl_id = source_to_id.get(rel_ctrl) or source_to_id.get(str(ctrl_path))
            if ctrl_id:
                added_edges.append({
                    "source": node_id,
                    "target": ctrl_id,
                    "relation": "controller",
                    "confidence": "EXTRACTED",
                })
            else:
                # Controller file exists on disk but graphify didn't index it.
                # Emit the edge against the file path so the link isn't lost.
                added_edges.append({
                    "source": node_id,
                    "target": rel_ctrl,
                    "relation": "controller",
                    "confidence": "INFERRED",
                })

        # Link / Table / Dynamic Link edges.
        fields = data.get("fields") or []
        for field in fields:
            ftype = field.get("fieldtype")
            fname = field.get("fieldname") or ""
            options = field.get("options") or ""

            if ftype in LINK_FIELDTYPES and options:
                added_edges.append({
                    "source": node_id,
                    "target": _doctype_id(options),
                    "relation": f"LINK[{fname}]",
                    "confidence": "EXTRACTED",
                    "fieldtype": ftype,
                })
            elif ftype == DYNAMIC_LINK_FIELDTYPE and options:
                for target_name in _dynamic_link_targets(fields, options):
                    added_edges.append({
                        "source": node_id,
                        "target": _doctype_id(target_name),
                        "relation": f"LINK[{fname}]",
                        "confidence": "EXTRACTED",
                        "fieldtype": ftype,
                    })

    return added_nodes, added_edges
