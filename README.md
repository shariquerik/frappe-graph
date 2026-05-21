# frappe-graph

Frappe-aware enrichment layer over [graphify](https://github.com/safishamsi/graphify).

Reads graphify's `graph.json`, scans a Frappe app (or a full bench) for
convention-driven files — DocType JSON, `hooks.py`, `@frappe.whitelist()`,
frappe-ui resource calls, `frappe.get_doc` / `get_list` / `new_doc` references —
and writes additional nodes and edges back using graphify's existing schema.

## Install

```bash
uv tool install .
# or
pip install -e .
```

This pulls graphify (PyPI: `graphifyy`) as a transitive dependency. The enricher
itself never imports `frappe`, so it can run anywhere graphify runs.

## Use

App mode (a single Frappe app, with `<slug>/hooks.py` inside):

```bash
cd ~/path/to/frappe_app
frappe-graph build
```

Bench mode (a directory containing `apps/` and `sites/`):

```bash
cd ~/path/to/frappe-bench
frappe-graph build --all          # build every app under apps/
frappe-graph build --app erpnext  # repeatable; pick specific apps
frappe-graph build --all --merge  # also produce a merged bench-wide graph
frappe-graph merge                # merge already-built per-app graphs
```

Each build produces `frappe-graph-out/graph.json` (alongside graphify's own
output, kept out of `graphify-out/` so they don't collide). Pass `--update`
to forward graphify's incremental rebuild.

## What the enricher adds

| Pass | Nodes | Edges |
| --- | --- | --- |
| DocType | `DocType:<Name>` (module, submittable, single, child-table) | `controller` → `<slug>.py`, `LINK[<field>]` for Link / Table / Table MultiSelect / Dynamic Link |
| `hooks.py` | — | `doc_events`, `scheduler_events`, `permission_query_conditions`, `has_permission`, `override_doctype_class`, `override_whitelisted_methods`, `boot_session`, `on_login`, etc. |
| `@frappe.whitelist()` | whitelisted Python functions tagged with `rpc_url` | — |
| Frontend ↔ backend RPC | — | `EXTRACTED` edges for frappe-ui `createResource` / `useDoc` calls; `INFERRED` for string-matched URLs; `MISSING_RPC:<url>` placeholders when no handler is found |
| `frappe.get_doc` refs | — | references from Python call sites to `DocType:<Name>` |

Edges carry a `confidence` of `EXTRACTED` (structured data — DocType JSON,
`hooks.py` dicts, decorator metadata) or `INFERRED` (string-matched RPC URLs).
The enricher never silently drops a recognised RPC call — unmatched URLs become
`MISSING_RPC:<url>` placeholder edges so they're visible in the graph.

## Claude Code skill

Install the `/frappe-graph` skill so an assistant prefers graph queries over
grep in this folder:

```bash
frappe-graph install            # project-level: <project>/.claude/skills/
frappe-graph install --global   # user-level: ~/.claude/skills/
frappe-graph uninstall
frappe-graph uninstall --purge  # also delete frappe-graph-out/
```

## Git hook

Keep the graph fresh on every commit:

```bash
frappe-graph hook install                  # app mode
frappe-graph hook install --all            # bench: every app
frappe-graph hook install --app erpnext    # bench: one app
frappe-graph hook install --force          # overwrite a conflicting hook
frappe-graph hook uninstall
```

The installer refuses to overwrite a hook it didn't write unless `--force`
is passed, and an existing graphify hook is detected and replaced cleanly.

## Design constraints

- **Post-processor, not a fork.** We read graphify's `graph.json`, add to it, and
  write it back. There is no plugin API; we don't reimplement graphify's merge.
- **Uniform pass interface.** Every enrichment pass is
  `(app_path, graph) -> (added_nodes, added_edges)`. Later passes see earlier
  passes' output.
- **Output dir is `frappe-graph-out/`**, never `graphify-out/`.
- **No runtime import of `frappe`.** The enricher reads files only.

See [`PRD.md`](./PRD.md) for the full product spec. The original
planning doc (cost envelope, verification milestones, design notes) is
archived for reference at
[issue #12](https://github.com/shariquerik/frappe-graph/issues/12).
