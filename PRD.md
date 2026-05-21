# PRD: `frappe-graph` — Frappe-aware enrichment layer over graphify

## Problem Statement

AI agents working in a Frappe codebase burn tokens re-deriving the same architectural facts every session — grepping for DocType definitions, hunting handlers wired through `hooks.py`, tracing `frappe.call`/`createResource` strings to their Python implementations, and chasing Link fields across DocType JSON. None of this navigation is in the AST, so generic code-graph tools see only a Python+Vue dependency map and miss Frappe's most important connections.

A pre-built, queryable knowledge graph that captures *both* the AST layer and Frappe's convention-driven layer would let an agent answer navigation questions ("what fires on Sales Invoice submit?", "where is this whitelisted method called from the frontend?") without grepping the repo from scratch.

## Solution

`frappe-graph` is a standalone CLI that wraps [graphify](https://github.com/safishamsi/graphify) and adds a post-processing enrichment pass tailored to Frappe. graphify produces a generic AST-layer `graph.json`; `frappe-graph` reads that graph, scans the Frappe app for convention-driven files (DocType JSON, `hooks.py`, `@frappe.whitelist()` decorators, frappe-ui resource calls, `frappe.get_doc` references), and writes additional Frappe-aware nodes and edges back to the same `graph.json` using graphify's existing schema. The `/graphify` Claude Code skill, MCP server, and HTML viewer then work unchanged on the enriched graph.

Users install once per machine (`uv tool install frappe-graph`), then run `frappe-graph build` inside any Frappe app to produce a `frappe-graph-out/` directory committed alongside the app. A `/frappe-graph` skill registered into `.claude/skills/` nudges the agent to prefer querying the graph over grepping. A git post-commit hook keeps the graph fresh. For cross-app questions, `frappe-graph merge` produces an on-demand bench-wide graph.

## User Stories

1. As an AI agent in a Frappe app, I want a pre-built knowledge graph queryable at session start, so that I can answer navigation questions without grepping the codebase from scratch.
2. As an AI agent, I want DocType definitions present as first-class nodes, so that I can locate a DocType's controller, schema, and module without crawling the filesystem.
3. As an AI agent, I want Link / Table / Dynamic Link relationships represented as edges between DocType nodes, so that I can answer "what other DocTypes reference Customer?" in one hop.
4. As an AI agent, I want `hooks.py` wire-ups (`doc_events`, `scheduler_events`, `permission_query_conditions`, `override_doctype_class`, `has_permission`) represented as edges between DocTypes and handler functions, so that I can trace what fires on a given event without parsing Python config dicts.
5. As an AI agent, I want `@frappe.whitelist()` functions tagged with their dotted RPC URL, so that I can match frontend RPC calls to their Python implementations.
6. As an AI agent, I want `frappe.call({ method: '...' })` calls in Vue/JS recognised and edged to the matching Python function, so that I can navigate frontend→backend in one hop.
7. As an AI agent, I want frappe-ui resource calls (`createResource`, `createDocumentResource`, `createListResource`) recognised, so that modern Frappe apps (CRM, HR, Helpdesk) are covered.
8. As an AI agent, I want frappe-ui composables (`useDoc`, `useList`, `useCall`, `useDoctype`) recognised, so that composition-API Vue code is covered alongside the older Options API patterns.
9. As an AI agent, I want frappe-ui calls that specify a `doctype` argument edged to the matching DocType node, so that I can answer "which Vue components read/write Lead?" directly from the graph.
10. As an AI agent, I want Frappe built-in RPC methods (`frappe.client.get_list`, `frappe.client.get_value`, `frappe.client.set_value`, `frappe.client.insert`, `frappe.client.delete`) represented as synthetic `BUILTIN:<method>` nodes, so that calls don't drop on the floor when there's no whitelisted target.
11. As an AI agent, I want `frappe.get_doc("Foo")` / `frappe.get_list("Foo")` / `frappe.new_doc("Foo")` references in Python edged to the DocType node, so that I can answer "what code touches Sales Invoice?".
12. As an AI agent, I want each edge tagged with confidence (`EXTRACTED` for unambiguous JSON, `INFERRED` for string-matched RPC), so that I can weight my reasoning accordingly.
13. As a Frappe developer, I want `frappe-graph build` to auto-detect whether I'm inside an app or at the bench root and adapt, so that I don't have to remember different invocations.
14. As a Frappe developer working at the bench root, I want to build one named app, multiple named apps, or all apps under `apps/` with a single command, so that I can keep the per-app graphs fresh in bulk.
15. As a Frappe developer, I want each app's graph output to live inside that app's repo (`apps/<name>/frappe-graph-out/`), so that the graph travels with the app via git.
16. As a Frappe developer, I want a `--merge` flag (or standalone `frappe-graph merge`) that unions all per-app graphs into a bench-wide graph, so that I can answer cross-app questions like "what in my custom app touches ERPNext's Sales Invoice?".
17. As a Frappe developer, I want the bench-wide merged graph to be regenerable on demand (not committed), so that I'm not pushing a huge artifact that no app actually owns.
18. As a Frappe developer, I want `frappe-graph install` to register a `/frappe-graph` Claude Code skill in `.claude/skills/` (project) or `~/.claude/skills/` (with `--global`), so that the assistant prefers the graph over grepping.
19. As a Frappe developer, I want `frappe-graph hook install` to register a git post-commit hook that runs `frappe-graph build . --update` after each commit, so that the graph stays fresh without manual intervention.
20. As a Frappe developer working in a bench, I want `frappe-graph hook install --all` to install the hook in every app's repo at once, so that I don't have to repeat the command per app.
21. As a Frappe developer, I want the post-commit hook to use graphify's mtime-based incremental rebuild, so that typical commits rebuild in under a second.
22. As a Frappe developer, I want `frappe-graph hook install` to detect a pre-existing graphify hook in the same repo and offer to replace it, so that I don't end up with two hooks running back-to-back.
23. As a Frappe developer, I want fully symmetric uninstall commands (`frappe-graph uninstall`, `frappe-graph hook uninstall`, `uv tool uninstall frappe-graph`), so that every install action is reversible.
24. As a Frappe developer, I want `frappe-graph uninstall --purge` to also delete `frappe-graph-out/`, so that I can fully remove the tool's footprint with one command.
25. As a Frappe developer, I want `frappe-graph` to pull graphify in as a transitive dependency, so that I don't have to install graphify separately.
26. As a Frappe developer, I want graph output written to `frappe-graph-out/` rather than graphify's default `graphify-out/`, so that the two tools don't collide if both are run in the same repo.
27. As a Frappe developer, I want a `.gitignore` written into `frappe-graph-out/` excluding mtime-sensitive files (`manifest.json`, `cost.json`), so that the committed graph doesn't churn on every rebuild.
28. As a Frappe developer, I want a clear error message when I run `frappe-graph build` outside an app or bench root, so that I know what layout the tool expects.
29. As a Frappe developer, I want graph queries (`frappe-graph query`, `path`, `explain`) to work via the graphify subcommands unchanged, so that I don't need to learn a new query language.
30. As a Frappe developer, I want each enrichment pass independently runnable, so that I can skip a slow or noisy pass without disabling the whole enricher.
31. As a Frappe developer, I want a tiny synthetic sample app shipped with the tests, so that I can read it to understand exactly which patterns the enricher recognises.
32. As a contributor, I want each enrichment pass to share a uniform interface (`(app_path, graph) -> (added_nodes, added_edges)`), so that adding a new pass is a small, localised change.
33. As a Frappe developer, I want zero LLM/API cost for the core enrichment (DocType, hooks, RPC matching), so that the tool runs offline and is free to use continuously.

## Implementation Decisions

### Approach: post-processor, not a fork

graphify exposes no plugin API, but its `graph.json` schema is simple and documented. The integration is a post-processing pass that reads `graph.json`, scans the Frappe app, and writes additional nodes/edges back to the same file using graphify's existing schema. This decouples from graphify's release cycle, avoids maintaining a fork, and lets every downstream consumer of the graph (skill, MCP, viewer) work unchanged.

### Major modules

**Deep modules** (encapsulate domain knowledge behind a simple, testable interface):

- **`detect`** — given a directory path, returns whether it's a Frappe app, a bench root, or neither, plus the list of apps if it's a bench. Pure function over filesystem state. Encapsulates Frappe-layout knowledge (`<slug>/hooks.py` marks an app; `apps/` + `sites/` marks a bench).
- **`enrich`** — orchestrator: reads `graph.json`, invokes each pass in order, writes the merged result back. Knows nothing about specific Frappe conventions.
- **Each enrichment pass** — uniform interface `(app_path, graph) -> (added_nodes, added_edges)`. One pass per Frappe convention:
  - DocType JSON pass (DocType nodes + controller edges + Link/Table/Dynamic Link edges)
  - `hooks.py` pass (`doc_events`, `scheduler_events`, `override_doctype_class`, `permission_query_conditions`, `has_permission`)
  - `@frappe.whitelist()` pass (tag function nodes with RPC URL)
  - RPC pass (frappe-ui-aware: classic `frappe.call`, resources, composables, builtins)
  - `frappe.get_doc` / `frappe.get_list` / `frappe.new_doc` reference pass
- Each pass is independent and can be enabled/disabled individually.

**Shallow modules** (mostly orchestration / I/O):

- **`build`** — invokes graphify + enrich for a single app.
- **`merge`** — wraps graphify's `merge-graphs` subcommand for the bench-wide union.
- **`install/skill`** — writes/removes the `/frappe-graph` Claude Code skill into `.claude/skills/` or `~/.claude/skills/`.
- **`install/hook`** — writes/removes the git post-commit hook.
- **`cli`** — argparse dispatcher.

### Edge confidence semantics

- `EXTRACTED` — derived from unambiguous structured data (DocType JSON Link field, hooks.py dict literal). High-trust.
- `INFERRED` — derived from string-matching across files (RPC URL matching, `frappe.get_doc("X")` string args). Lower-trust; surface count separately in any audit.

### Output layout

- Per-app output in `<app>/frappe-graph-out/` (not graphify's default `graphify-out/`, to avoid collision).
- `.gitignore` inside `frappe-graph-out/` excludes `manifest.json`, `cost.json`.
- Bench-wide merged graph at `<bench>/frappe-graph-out/bench-graph.json`, not committed.

### CLI surface

```
frappe-graph build [--app NAME ...] [--all] [--merge] [--update]
frappe-graph merge
frappe-graph install [--global]
frappe-graph uninstall [--purge] [--global]
frappe-graph hook install [--app NAME ...] [--all]
frappe-graph hook uninstall
frappe-graph query / path / explain   # delegated to graphify
```

`build` auto-detects app vs bench mode. In bench mode, no `--app`/`--all` flag is interactive.

### Dependencies

- `graphify` (Python package, transitive — declared in `pyproject.toml`).
- Python 3.10+ (matches graphify's floor).
- No runtime dependency on a Frappe install — the enricher reads files, doesn't import `frappe`.

### Auto-rebuild mechanism

Git post-commit hook only. No file watcher (out of scope for v1). The hook runs `frappe-graph build . --update`; graphify's mtime-manifest handles incremental work, enrichment passes re-run in full (they're cheap).

## Testing Decisions

### What makes a good test

- **Test the external behaviour of each pass**: given a sample Frappe app on disk and a starting `graph.json`, the pass should produce a specific set of nodes and edges. Assert on the output graph, not on internal parsing state.
- **No mocking of file I/O**: tests use real fixture directories under `tests/fixtures/`. The passes' only inputs are a directory path and an in-memory graph dict, so they're trivially testable end-to-end.
- **No mocking of graphify**: tests run against pre-baked `graph.json` fixtures captured from running graphify against the sample app once, checked in alongside the fixture. This isolates enrichment-pass tests from graphify's release cadence.

### Modules to test

Tests are scoped to the deep modules where the domain logic lives:

- **`detect`** — table-driven tests over fixture directories: app fixture → `("app", [name])`; bench fixture → `("bench", [names...])`; empty fixture → error. Pure, fast.
- **Each enrichment pass** — one test module per pass, asserting expected nodes/edges appear in the output graph for the sample app:
  - DocType pass: 3 fixture DocTypes (one with a Link, one with a Table) produce 3 DocType nodes, 1 Link edge, 1 Table edge, 3 controller edges.
  - hooks.py pass: a fixture `hooks.py` with `doc_events = {...}` produces the expected edges from DocTypes to handler function nodes.
  - whitelist pass: one decorated Python function gains the expected RPC URL property and `WhitelistedMethod` tag.
  - RPC pass: one Vue file using `frappe.call`, one using `createDocumentResource`, one using `useDoc`, one using a `frappe.client.*` builtin — each produces the expected edge (to whitelisted method, to DocType, or to `BUILTIN:*` synthetic node).
  - refs pass: one Python function with `frappe.get_doc("Foo")` produces a single edge to `DocType:Foo`.
- **`enrich` orchestrator** — one end-to-end test: run all passes against the full sample app, assert the final graph matches a checked-in golden file (with deterministic node/edge ordering).

### Skip from unit testing

- `cli` (argparse glue) — covered by CLI smoke tests in CI, not unit tests.
- `build`, `merge` — wrappers around external commands; covered by smoke tests against the sample app.
- `install/*` — file-writing wrappers; covered by install/uninstall round-trip smoke tests.

### Sample app fixture

`tests/fixtures/sample_app/` — synthetic Frappe app with:
- 3 DocTypes (Customer, Order, Order Item — covers Link and Table relationships).
- One `hooks.py` with `doc_events` and `scheduler_events` entries.
- One `@frappe.whitelist()` Python method.
- One Vue file using `frappe.call`, one using a frappe-ui resource, one using a composable.
- Checked-in `graph.json` baseline (output of running graphify on the fixture once).

### Smoke tests (CI, not unit)

- Build the sample app end-to-end (`frappe-graph build`) and assert the output is non-empty.
- Install + uninstall round-trip for the skill and the hook in a temporary git repo.

### Prior art

This is a new repo with no prior tests, so prior art lives in graphify's own test approach (fixture-directory + assert-on-graph pattern). Worth a read before starting Phase 1.

## Out of Scope

- **A file watcher / save-time rebuild daemon** — adds battery cost and a long-lived process. Manual `frappe-graph build . --update` covers the gap. Revisit in v2 if demand is real.
- **Auto-rebuild on `git pull`** — post-commit hooks don't fire for pulls. A `post-merge` hook is conceptually possible but out of scope for v1.
- **A `bench setup` integration** — having `bench setup` invoke `frappe-graph build` for newly-cloned apps. Defer; manual first build is fine.
- **A semantic / LLM-based query mode** — `frappe-graph query` runs graphify's deterministic NetworkX queries. Anything LLM-mediated stays in the consumer (the Claude Code skill).
- **Doc/PDF/video extraction** — graphify supports it via `--no-docs`; we expose the flag but don't add Frappe-specific doc extractors.
- **Modelling frappe-ui's `Resource` object internals** (`.fetch()`, `.submit()`, `.data` accessors) — pass 5 captures the URL/doctype each resource is bound to. That's enough for navigation; the runtime mechanics aren't modelled.
- **Database-schema-aware edges** — modelling `tabFoo` SQL queries to DocType nodes. Possible later; out of scope now.
- **A graphical query UI beyond graphify's HTML viewer** — the enriched graph works with graphify's existing viewer. No custom UI.
- **Multi-language enrichment beyond Python + Vue/JS/TS** — Frappe apps occasionally include SQL and shell. Coverage there is graphify's job, not ours.
- **Touching `claude-frappe-toolkit`** — the seven existing slash commands remain as-is. They solve a different problem (task recipes vs. navigation context).

## Further Notes

### Where the graph lives

Per-app, committed to the app repo. This matches Frappe's natural boundary (each app is its own git repo), keeps each graph small enough to rebuild quickly, and avoids committing a giant bench-wide graph that nobody actually owns. The on-demand `merge` covers cross-app queries.

### Reusing what already exists

- `claude-frappe-toolkit`'s `skills/frappe/` on-demand docs (DocType anatomy, `frappe.*` namespace, hooks.py) are the same domain knowledge the enricher encodes. Worth reading before implementing each pass so the enricher's recognition matches the skill's understanding.
- graphify's `merge-graphs` handles the bench-wide merge — not reimplemented.
- graphify's `query`, `path`, `explain` subcommands work on any `graph.json` following its schema, so they work on the enriched graph unchanged.
- graphify's `hook install` writes a post-commit hook; we wrap it so our enrichment runs in the same hook.

### Cost envelope (estimates, to verify in Phase 1)

- Disk: small app `<1 MB`; mid-sized app `2–5 MB`; ERPNext `20–50 MB`.
- Clean build: small `<10 s`; mid-sized `30–90 s`; ERPNext `2–5 min`.
- Incremental commit-time rebuild: typically `<1 s`, a few seconds for large commits.
- Peak RAM: `<200 MB` small/mid; `<1 GB` ERPNext.
- API/LLM cost: zero for the core enrichment.

### Verification milestones (see plan.md §Verification for full list)

1. Unit tests pass against the synthetic sample app.
2. Smoke test against a cloned ERPNext — spot-check DocType edges, hooks.py wire-up, controller edges.
3. Spot-check a frappe-ui app (CRM) for resource-call edges.
4. Confidence audit — EXTRACTED dominates; 10 random INFERRED edges are real, not false positives.
5. Install/uninstall round-trip leaves the repo clean.
6. Token-use sanity check: navigation queries in an enriched session use `frappe-graph query`, not grep, and total session tokens drop substantially vs. baseline.
