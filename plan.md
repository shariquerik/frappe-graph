# Plan: `frappe-graph` — a standalone Frappe-aware layer over graphify

## Context

`claude-frappe-toolkit` currently ships slash commands (`/find`, `/trace`, `/new-doctype`, etc.) that encode Frappe conventions as task recipes. The user's actual problem is different: **AI agents working in a Frappe codebase burn tokens grepping and reading files from scratch every session**. A pre-built knowledge graph the agent can query is a better fit than a set of task commands.

[graphify](https://github.com/safishamsi/graphify) builds such a graph from any folder using tree-sitter — fast, local, no API cost for code extraction. It supports the languages Frappe uses (Python, Vue, JS, SQL) and plugs into Claude Code as a `/graphify` skill so the assistant prefers querying the graph over re-reading files.

But graphify is generic — it only sees what's in the AST. A Frappe app's most important architectural connections are **not in the AST**:

- DocType JSON `"options": "Customer"` (Link field) → Customer DocType
- `hooks.py`'s `doc_events = {"Sales Invoice": {"on_submit": "..."}}` → handler function
- `@frappe.whitelist()` Python function ↔ Vue `frappe.call({ method: '...' })` (string-keyed RPC)
- `frappe.get_doc("Sales Invoice")` → Sales Invoice DocType

Without these edges, the graph is just a Python+Vue dependency map. The user marked these edges as *critical*.

**Outcome**: a standalone tool — **`frappe-graph`** — that wraps graphify and adds a Frappe-aware enrichment pass. AI agents querying the resulting graph get real framework-level navigation, not just AST-level. Token use per session drops because the agent rarely needs to grep.

## Where it lives

A **new sibling repo**, separate from `claude-frappe-toolkit`:

```
/Users/shariq/code/frappe-graph/         ← new repo
/Users/shariq/code/claude-frappe-toolkit/  ← existing toolkit, untouched
```

Reasoning: `frappe-graph` is a different kind of artifact (a packaged CLI tool, not a collection of Claude Code commands), it'll be published to PyPI on its own cadence, and people who don't use the toolkit's slash commands should still be able to adopt the graph.

## Approach: post-processor, not a fork

graphify exposes no plugin API — extractors are functions registered by file suffix inside `extract.py`. But the `graph.json` schema is simple and documented (`id, label, source_file, source_location` for nodes; `source, target, relation, confidence` for edges). So the cleanest integration is:

```
graphify .                        # graphify produces graph.json
frappe-graph enrich graphify-out  # this toolkit adds Frappe edges in place
```

Or a one-shot wrapper:

```
frappe-graph build <app-path>     # runs graphify + enrich together
```

The enricher reads `graph.json`, scans the Frappe app for convention-driven files, and writes additional nodes/edges back to the same file. All new nodes/edges use graphify's existing schema so the `/graphify` skill, MCP server, and HTML viewer all work unchanged.

This decouples from graphify's release cycle and avoids maintaining a fork.

## What the enricher adds

Each pass below adds a category of Frappe-specific node or edge. Edges use `confidence: "EXTRACTED"` when string-matched exactly, `"INFERRED"` when matched via convention.

1. **DocType nodes from JSON**. Walk `apps/<app>/<app>/<module>/doctype/<slug>/<slug>.json`. For each, emit a node with `id: "DocType:<DocType Name>"`, `kind: "DocType"`, and metadata (module, is_submittable, is_single, naming). Connect it to its sibling `<slug>.py` controller (already a node in graphify's graph) with a `controller` edge.

2. **Link / Table / Dynamic Link edges**. For each Link/Table/Table MultiSelect field in a DocType JSON, emit edge `DocType:<A> --LINK[fieldname]--> DocType:<B>`. For Dynamic Link, emit edges to all possible targets listed.

3. **hooks.py wire-up**. Parse `apps/<app>/<app>/hooks.py` as Python (it's config-as-code — dict literals at module scope). For each entry in `doc_events`, `scheduler_events`, `override_doctype_class`, `permission_query_conditions`, `has_permission`, etc., emit an edge from the relevant DocType (or scheduler interval node) to the handler function node already in graphify's graph. Edge `relation` is the event name (`on_submit`, `before_save`, etc.).

4. **Whitelisted RPC nodes**. Scan source files of Python function nodes for the `@frappe.whitelist()` decorator (graphify's tree-sitter pass may or may not capture decorators — confirm; if not, a regex pass on the source file is enough). For each, compute the RPC URL `<app>.<module>.<file>.<func>` and attach it as a property on the node, plus a `kind: "WhitelistedMethod"` tag.

5. **Frontend ↔ Backend RPC edges (frappe-ui-aware)**. Modern Frappe apps (CRM, HR, Helpdesk) are built on the [`frappe-ui`](https://github.com/frappe/frappe-ui) Vue component library, which provides the data-fetching layer between Vue and Frappe's RPC/REST. This pass recognises both the classic `frappe.call` pattern and the full frappe-ui API surface, scanning `.vue` and `.js`/`.ts` source files for string-literal arguments to:

   - **Classic**: `frappe.call({ method: '...' })`
   - **frappe-ui resources**: `createResource({ url: '...' })`, `createDocumentResource({ doctype: '...', name: '...' })`, `createListResource({ doctype: '...', filters: {...}, fields: [...] })`, `createResource({ cache: { url: '...' } })`
   - **frappe-ui composables**: `useDoc('...', '...')`, `useList('...', {...})`, `useCall('...')`, `useDoctype('...')`

   For each matched call:
   - If the URL string matches a `@frappe.whitelist()` method's dotted path (from pass 4) → emit `RPC_CALLS` edge from the Vue node to the Python function node.
   - If the URL string is a Frappe built-in (`frappe.client.get_list`, `frappe.client.get_value`, `frappe.client.set_value`, `frappe.client.insert`, `frappe.client.delete`) → emit a `RPC_CALLS` edge to a synthetic `BUILTIN:<method>` node, and if the call's args include a `doctype: '...'`, also emit a `READS_DOCTYPE`/`WRITES_DOCTYPE` edge to that DocType node.
   - If the call specifies a `doctype` (createDocumentResource, createListResource, useDoc, useList, useDoctype) → emit a `READS_DOCTYPE` edge to the matching DocType node from pass 1.

   We don't model frappe-ui's `Resource` object internals (`.fetch()`, `.submit()`, `.data`) — only the URL/doctype string each call is bound to. That's enough to draw the frontend↔backend graph.

6. **`frappe.get_doc` / `frappe.get_list` / `frappe.new_doc` references**. Scan Python source for string-literal arguments in these calls. Emit edges from the calling function node to the corresponding DocType node.

Each pass is independent — the enricher can be run with a subset of passes if a particular one is slow or noisy.

## Where the graph lives — recommendation

**Per-app, committed to the app repo, plus an on-demand bench-wide merge.**

- Run `frappe-graph build` in each Frappe app repo (`erpnext`, `frappe`, your custom app). Commit the output dir to that app's git so teammates pulling the app get the graph immediately.
- For cross-app queries (e.g. "what in my custom app touches ERPNext's Sales Invoice?"), run `frappe-graph merge` once locally — wraps graphify's `merge-graphs` to union all per-app graphs into a bench-wide `graph.json`. Not committed; regenerated when wanted.
- The git hook (see Auto-rebuild below) keeps each app's graph fresh on its own.

This matches Frappe's natural boundary (apps are independent git repos), keeps each graph small enough to rebuild quickly, and avoids committing a giant bench-wide graph that nobody actually owns.

## Repo layout — `/Users/shariq/code/frappe-graph`

```
frappe-graph/
├── pyproject.toml                  # package metadata, CLI entry points
├── README.md                       # install/uninstall, query usage, examples
├── src/frappe_graph/
│   ├── __init__.py
│   ├── cli.py                      # `frappe-graph` CLI dispatcher
│   ├── build.py                    # wrapper: invokes graphify + enrich (single app)
│   ├── merge.py                    # `frappe-graph merge`: bench-wide union via graphify merge-graphs
│   ├── enrich.py                   # orchestrates the 6 enrichment passes
│   ├── passes/
│   │   ├── doctype.py              # passes 1 & 2: DocType nodes + Link edges
│   │   ├── hooks.py                # pass 3: hooks.py wire-up
│   │   ├── whitelist.py            # pass 4: @frappe.whitelist() scan
│   │   ├── rpc.py                  # pass 5: frappe-ui-aware Vue↔Python RPC matching
│   │   └── refs.py                 # pass 6: frappe.get_doc references
│   ├── install/
│   │   ├── skill.py                # write/remove Claude Code /frappe-graph skill
│   │   └── hook.py                 # write/remove git post-commit hook
│   └── detect.py                   # app-mode vs bench-mode detection; lists apps in bench-mode
├── tests/
│   ├── fixtures/sample_app/        # tiny synthetic Frappe app for tests
│   └── test_passes.py
└── .github/workflows/ci.yml
```

The seven existing commands in `claude-frappe-toolkit` are not touched by this plan. They remain useful task recipes; this new tool solves a different problem (navigation context).

## Reusing what already exists

- The toolkit's existing `skills/frappe/` references (the on-demand docs about DocType anatomy, `frappe.*` namespace, hooks.py) are the same domain knowledge the enricher encodes. Worth reading before implementing each pass so the enricher's understanding matches the skill's.
- graphify's `merge-graphs` command handles the bench-wide merge — no need to reimplement.
- graphify's `query`, `path`, `explain` subcommands work on any `graph.json` following its schema, so they work on the enriched graph unchanged.
- graphify's `hook install` writes a post-commit hook; we wrap it so our enrichment runs in the same hook.

## Installation

Two layers: the **CLI** (the `frappe-graph` binary itself) and the **integrations** (per-Frappe-app registration as a Claude Code skill + git hook). graphify follows the same pattern, so the user model is familiar.

**Step 1 — install the CLI (once per machine)**

```bash
uv tool install frappe-graph          # recommended; puts `frappe-graph` on PATH
# or
pipx install frappe-graph
# or
pip install frappe-graph
```

The CLI declares `graphifyy` as a dependency in `pyproject.toml`, so installing `frappe-graph` pulls in graphify automatically. No manual graphify install needed.

**Step 2 — build the graph**

`frappe-graph build` auto-detects whether you're inside an app or at the bench root and adapts.

```bash
# Inside an app
cd ~/frappe-bench/apps/erpnext
frappe-graph build                       # builds this app, no prompt

# At the bench root
cd ~/frappe-bench
frappe-graph build                       # interactive: lists apps, lets you pick one or many
frappe-graph build --app erpnext         # non-interactive, build one named app
frappe-graph build --app erpnext --app hrms   # repeat the flag for several
frappe-graph build --all                 # build every app in apps/
frappe-graph build --all --merge         # build all, then run merge into a single bench-wide graph
```

**Detection logic** (in `src/frappe_graph/detect.py`):
- **App mode**: a `<slug>/hooks.py` exists at the current directory's package root → build that app, output to `<app>/frappe-graph-out/`.
- **Bench mode**: `apps/` and `sites/` directories both exist at the current directory → enumerate apps via `apps/*/`, prompt or accept `--app`/`--all`. Each app's output still lives inside that app's repo (`apps/<name>/frappe-graph-out/`), so per-app graphs travel with their respective git repos.
- **Neither**: error with a clear message and the recognised layouts shown.

**What each build does**:
1. Runs `graphify .` (scoped to the app) to produce the AST-layer graph in `frappe-graph-out/`.
2. Runs the enrichment passes against `frappe-graph-out/graph.json` in place.
3. Writes a `.gitignore` into `frappe-graph-out/` that excludes mtime-sensitive files (`manifest.json`, `cost.json`) — matches graphify's recommended `.gitignore`.

We use `frappe-graph-out/` rather than graphify's default `graphify-out/` so the two never conflict if both tools are run in the same repo.

**Bench-wide merge** (`--merge` or standalone `frappe-graph merge`):
- Collects every `apps/*/frappe-graph-out/graph.json`, runs `graphify merge-graphs` on them, writes the result to `<bench>/frappe-graph-out/bench-graph.json`. Not committed (typically `.gitignore`'d). Useful when you want a single graph to query across all apps.

**Step 3 — register the Claude Code skill (per app, once)**

```bash
frappe-graph install
```

Writes a `/frappe-graph` skill into `.claude/skills/` (project-level) or `~/.claude/skills/` (user-level — `--global` flag). The skill description tells the assistant: "this folder is a Frappe app with a knowledge graph at `frappe-graph-out/`; prefer `frappe-graph query` over grepping." Modeled directly on `graphify install`.

**Step 4 — register the git hook (per app, optional but recommended)**

```bash
# Inside an app
frappe-graph hook install

# At the bench root
frappe-graph hook install --all          # install hook in every app's repo
frappe-graph hook install --app erpnext  # one named app from the bench
```

Hooks are always per-app (each app is its own git repo, so each gets its own `.git/hooks/post-commit`). The bench-level command is just a convenience for installing across many apps in one go. See Auto-rebuild section below.

### Uninstallation

Symmetric commands at each layer, mirroring graphify's UX:

```bash
frappe-graph hook uninstall          # remove the post-commit hook from this repo
frappe-graph uninstall               # remove the /frappe-graph skill from this project
frappe-graph uninstall --purge       # also delete frappe-graph-out/
frappe-graph uninstall --global      # remove user-level skill registration

uv tool uninstall frappe-graph       # remove the CLI entirely (also removes graphify if it was pulled as a dep and isn't used by anything else)
```

Each command is reversible — nothing writes outside the repo it's run in (or `~/.claude/` for `--global` skills).

## Auto-rebuild on code update

graphify already does the hard part — incremental rebuilds via mtime-based manifest. `frappe-graph` reuses this; the enrichment passes are themselves cheap enough to rerun in full each time.

**Mechanism**: a git **post-commit hook** installed by `frappe-graph hook install`. After each commit, the hook runs:

```
frappe-graph build . --update
```

`--update` means graphify only re-extracts files whose mtime changed since the last manifest. Enrichment then runs over the (mostly unchanged) graph. Typical commit-time rebuild: **sub-second to a few seconds** for a Frappe app of any realistic size.

**What it does NOT do**:
- It does not auto-rebuild on every file save. That requires a file watcher, which adds a daemon and battery cost — out of scope for v1. If you want it, run `frappe-graph build . --update` manually before asking the AI a navigation question.
- It does not rebuild on `git pull`. The pulled commit's hook fires only if the puller themselves commits. For a freshly-cloned repo, the first `frappe-graph build` happens manually (or via a `bench setup` integration later).
- It does not auto-merge the bench-wide graph. `frappe-graph merge` is on-demand.

**Hook conflicts**: if a graphify hook is already installed in the same repo, `frappe-graph hook install` detects it and offers to replace it (since `frappe-graph build` already invokes graphify under the hood). No double-rebuild.

## How heavy is it

Three dimensions: disk, build time, runtime memory. Numbers below are estimates based on graphify's published behavior plus the enrichment passes' complexity — needs empirical verification in Phase 1 of implementation.

**Disk**:
- `frappe-graph` CLI install: ~50 MB (mostly graphify's tree-sitter language bundles). Comparable to installing a typical Python dev tool.
- `frappe-graph-out/` per app: depends on app size.
  - Small custom app (~10–50 DocTypes, ~50 Python files): **< 1 MB**.
  - Mid-sized app (HR, CRM, ~200 DocTypes, ~500 Python files): **2–5 MB**.
  - ERPNext (~600 DocTypes, ~5000 Python files, ~3000 JS): **20–50 MB**.
- The `cache/` subdir grows the output; you can choose to skip committing it (smaller repo) or commit it (faster pulls for teammates).

**Build time** (clean build, no cache):
- Small custom app: **< 10 seconds**.
- Mid-sized: **30–90 seconds**.
- ERPNext: **2–5 minutes**.

**Incremental rebuild** (post-commit, only changed files):
- Typical small commit (1–5 files): **< 1 second**.
- Large commit touching dozens of files: **a few seconds**.

**Runtime memory during build**:
- Small/mid: **< 200 MB**.
- ERPNext: **< 1 GB** peak (graphify holds the NetworkX graph in memory).

**Query time** (after the graph exists):
- `frappe-graph query "..."` returns in **< 1 second** for typical questions, since it's a NetworkX query on already-loaded JSON. No LLM call required unless the user opts into a semantic-query mode.

**API/LLM cost**:
- Code extraction (AST via tree-sitter): **zero**. Local, no model calls.
- DocType/hooks/RPC enrichment: **zero**. Pure file parsing.
- Optional doc/PDF/video extraction: uses your AI assistant's model (same as plain graphify). Skip with `--no-docs`.

So for a typical Frappe development setup, the steady-state cost is: a few MB of `frappe-graph-out/` in each app repo, sub-second rebuilds on commit, and zero ongoing API spend.

## Verification

1. **Tiny synthetic app**. `tests/fixtures/sample_app/` ships with the repo: 3 DocTypes (one with a Link, one with a Table), a `hooks.py` with `doc_events`, one whitelisted method, one Vue file calling it. Unit tests assert every expected edge appears in the enriched `graph.json`.

2. **Smoke test against ERPNext**. Run `frappe-graph build .` in a cloned ERPNext repo. Spot-check:
   - `frappe-graph query "DocType:Sales Invoice"` shows Link edges to Customer, Company, Item (via the Sales Invoice Item child table), and a controller edge to `sales_invoice.py`.
   - `frappe-graph path "DocType:Sales Invoice" "DocType:Customer"` returns a one-hop Link edge.
   - `frappe-graph query "what fires on Sales Invoice submit?"` surfaces both hooks.py-declared handlers and the controller's `on_submit` method.
   - Build time stays inside the 2–5 minute estimate.

3. **Frontend↔backend test**. Pick a Frappe-UI app (CRM or HR). Find a Vue component calling `createResource({ url: 'frappe.client.get_list' })` and run `frappe-graph path` from the Vue node to the Python function — single `RPC_CALLS` edge expected.

4. **Confidence audit**. Count edges by `confidence`. EXTRACTED should dominate (DocType JSON Link fields are unambiguous). INFERRED covers string-matched RPC. Spot-check 10 random INFERRED edges to confirm matches are real, not false positives.

5. **Install/uninstall round-trip**. Run `frappe-graph install`, confirm a `/frappe-graph` skill appears in `.claude/skills/`. Run `frappe-graph hook install`, commit a trivial change, confirm the hook fires and graph rebuilds in < 5 seconds. Run `frappe-graph uninstall --purge` and `frappe-graph hook uninstall` — confirm both leave the repo clean.

6. **Token-use sanity check**. Open a fresh Claude Code session in an enriched Frappe app. Ask a navigation question ("where is the Sales Order submit flow handled?"). Confirm the assistant uses `frappe-graph query` rather than grepping. Measure tokens used vs. a baseline session in the same repo without the graph — should drop substantially (target: >50% reduction on navigation-heavy queries).

7. **Incremental rebuild**. Edit a DocType JSON to add a new Link field. Run `frappe-graph build --update`. New edge appears, total time < 5 seconds.

8. **Bench-mode invocation**. From a bench root, run `frappe-graph build --all --merge`. Confirm every app under `apps/` ends up with its own `frappe-graph-out/`, and the bench root has `frappe-graph-out/bench-graph.json` containing nodes from all apps with cross-app edges resolved (e.g. a custom app's DocType extending ERPNext's Sales Invoice shows the link).

9. **frappe-ui frontend audit**. In an enriched CRM (frappe-ui-based) repo, find any `.vue` file using `createDocumentResource({ doctype: 'Lead' })`. Confirm there's a `READS_DOCTYPE` edge from that Vue node to `DocType:Lead`. Also confirm `frappe.client.get_list` calls land on a `BUILTIN:frappe.client.get_list` synthetic node rather than being dropped.
