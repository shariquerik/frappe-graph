# frappe-graph

Frappe-aware enrichment layer over [graphify](https://github.com/safishamsi/graphify).

Reads graphify's `graph.json`, scans a Frappe app for convention-driven files
(DocType JSON, `hooks.py`, `@frappe.whitelist()`, frappe-ui resource calls,
`frappe.get_doc` references), and writes additional nodes/edges back using
graphify's existing schema.

## Install

```bash
uv tool install .
# or
pip install -e .
```

This pulls graphify (PyPI: `graphifyy`) as a transitive dependency.

## Use

```bash
cd ~/path/to/frappe_app
frappe-graph build
```

Produces `frappe-graph-out/graph.json` containing the AST graph from graphify
plus Frappe-specific nodes and edges.

## What's in this slice

- DocType nodes (`DocType:<Name>`) with module, submittable, single, child-table flags
- `controller` edges from each DocType to its sibling `<slug>.py`
- `LINK[<field>]` edges for Link / Table / Table MultiSelect / Dynamic Link fields

Other passes (hooks.py, whitelisted methods, Vue↔Python RPC, `frappe.get_doc`
references) land in later slices — see the issue tracker.
