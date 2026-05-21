# frappe-graph verification (slice #9)

End-to-end verification against real Frappe codebases. Run on 2026-05-21
against:

- ERPNext (`https://github.com/frappe/erpnext`, default branch, shallow clone).
- Frappe CRM (`https://github.com/frappe/crm`, default branch, shallow clone).
- A synthetic two-app bench (`bench/apps/erpnext` + `bench/apps/custom_app`)
  with a custom DocType that has a `Link` field to `Sales Invoice`.

Tooling: `graphifyy==0.8.14`, `frappe-graph` from `develop` at commit time of
slice #9.

## How to re-run

```bash
# from a scratch dir
git clone --depth=1 https://github.com/frappe/erpnext.git
git clone --depth=1 https://github.com/frappe/crm.git    frappe-crm

# build per-app graphs
frappe-graph build ./erpnext
frappe-graph build ./frappe-crm

# confidence audit
python scripts/confidence_audit.py erpnext/frappe-graph-out/graph.json 10

# bench-mode merge (see the test fixture in this doc for the custom_app skeleton)
frappe-graph build /path/to/bench --all --merge
```

## Findings

### 1. ERPNext smoke — PASS (after fixes)

After the bugs below were fixed, every spot-check from the slice 9 acceptance
list landed:

| Edge | Source | Target | Relation | Confidence |
| --- | --- | --- | --- | --- |
| Link to Customer | `DocType:Sales Invoice` | `DocType:Customer` | `LINK[customer]` | EXTRACTED |
| Link to Company | `DocType:Sales Invoice` | `DocType:Company` | `LINK[company]` | EXTRACTED |
| Child table | `DocType:Sales Invoice` | `DocType:Sales Invoice Item` | `LINK[items]` | EXTRACTED |
| Child → Item | `DocType:Sales Invoice Item` | `DocType:Item` | `LINK[item_code]` | EXTRACTED |
| Controller | `DocType:Sales Invoice` | `sales_invoice.py` | `controller` | EXTRACTED |
| hooks.py `on_submit` | `DocType:Sales Invoice` | `erpnext.regional.italy.utils.sales_invoice_on_submit` | `on_submit` | INFERRED |
| hooks.py `on_cancel` | `DocType:Sales Invoice` | `erpnext.regional.italy.utils.sales_invoice_on_cancel` | `on_cancel` | INFERRED |
| hooks.py `on_trash` | `DocType:Sales Invoice` | `erpnext.regional.check_deletion_permission` | `on_trash` | INFERRED |
| Controller method | `sales_invoice.py` | `.on_submit()` (`sales_invoice_sales_invoice_salesinvoice_on_submit`) | graphify `method` | EXTRACTED |

`Sales Invoice → Item` is two hops in the graph (`LINK[items]` to the child
table, then `LINK[item_code]` to Item), which matches the data model.

ID-based shortest-path `DocType:Sales Invoice → DocType:Customer` is a single
hop. Note: graphify's CLI `graphify path "X" "Y"` does fuzzy label matching
and will pick label "Sales" or "Customer" instead of the DocType nodes; query
by ID via the graph.json directly, or use a Claude Code skill that knows the
`DocType:<Name>` convention.

Final ERPNext graph: 83,928 nodes / 102,684 edges.

### 2. Frappe CRM frappe-ui spot check — PASS (after fixes)

Three `.vue` files have `READS_DOCTYPE` edges to `DocType:CRM Lead`:

- `frontend/src/pages/Lead.vue`
- `frontend/src/pages/MobileLead.vue`
- `frontend/src/components/Modals/LeadModal.vue`

`frappe.client.get_list` is detected and surfaces as a
`BUILTIN:frappe.client.get_list` synthetic node, reached from
`frontend/src/components/Settings/Sla/SlaPriorityList.vue` via `RPC_CALLS`.

Other recognised builtins (`set_value`, `insert`, `delete`) all appear as
`BuiltinMethod` nodes. 17 `MissingRPC:<url>` placeholders surface URLs that
didn't resolve to a `@frappe.whitelist()` — useful for spotting stale frontend
references.

Note: Frappe CRM's DocType is `CRM Lead`, not `Lead`; the issue's example used
`Lead` literally. The pass works regardless of the DocType name — it just
mirrors whatever string was passed to `createListResource({ doctype: '...' })`.

Final CRM graph: 4,382 nodes / 5,136 edges.

### 3. Confidence audit — PASS

ERPNext breakdown (`scripts/confidence_audit.py`):

| Confidence | Edges | % |
| --- | ---: | ---: |
| EXTRACTED | 95,398 | 92% |
| INFERRED | 7,286 | 7% |

`EXTRACTED` dominates as expected. Almost all `INFERRED` edges are graphify's
own `calls` / `uses` fuzzy matches (graphify writes `INFERRED` when it can't
unambiguously resolve a call's target). frappe-graph-emitted INFERRED edges
are limited to hooks.py event handlers (dotted paths are matched against
function nodes — INFERRED falls back to "the handler is a dotted path, not yet
a graph node") and frontend RPC calls (string-matched, never AST-parsed).

A random sample of 10 INFERRED edges (PYTHONHASHSEED=0) was hand-checked — all
plausible (`flt`, `_()`, `now_datetime`, `get_bin`, `SerialBatchCreation.set`).
No obvious false positives.

CRM breakdown: 87% EXTRACTED, 12% INFERRED. The higher INFERRED share is from
the RPC pass (128 `READS_DOCTYPE` + 52 `WRITES_DOCTYPE` + 99 `RPC_CALLS`, all
INFERRED by design since they're string-matched).

### 4. Install/uninstall round-trip — PASS (after fix)

1. `frappe-graph install` writes `.claude/skills/frappe-graph/SKILL.md`.
2. `frappe-graph hook install` writes `.git/hooks/post-commit` invoking
   `frappe-graph build . --update`.
3. A `git commit` of a single empty file fires the hook and rebuilds the graph
   in **~2 s** (Frappe CRM, 692 files).
4. `frappe-graph hook uninstall` removes the hook; `frappe-graph uninstall
   --purge` removes the skill *and* `frappe-graph-out/`.

Foreign `.git/hooks/post-commit` files (no sentinel) are left intact, as
intended.

### 5. Bench-mode build --all --merge — PASS (after fix)

Synthetic bench with `apps/erpnext` (default branch) and `apps/custom_app`
containing one DocType (`Custom SI Addon`) with a `Link` field to
`Sales Invoice`:

- Each app gets `apps/<slug>/frappe-graph-out/graph.json`.
- The bench root gets `frappe-graph-out/bench-graph.json` (88,820 nodes /
  136,580 edges).
- The cross-app edge survives:
  `custom_app::DocType:Custom SI Addon
   --LINK[sales_invoice]-->
   erpnext::DocType:Sales Invoice` (EXTRACTED).

### 6. Token-use sanity check — HITL (not run in this pass)

This step is genuinely human-in-the-loop and needs to be run by the user in a
fresh Claude Code session. Protocol:

1. Open the `erpnext/` clone in Claude Code with the `/frappe-graph` skill
   installed.
2. Baseline: in a session **without** the skill, ask "Where is the Sales
   Order submit flow handled?" Capture the token count (use the Claude Code
   `/tokens` indicator or transcript inspector).
3. Repeat the same question in a session **with** the skill. The expectation
   is that the assistant uses `frappe-graph` / `graphify` queries rather than
   grepping the tree.
4. Compare. Target per the issue: >50% reduction on navigation-heavy queries.
5. If the gap doesn't hit the target, file a follow-up issue with the
   transcript and the queries used.

### 7. Incremental rebuild — PARTIAL

A single DocType field edit on the small custom_app rebuilds in **0.17 s** and
the new `LINK[customer]` edge appears. The same single-field edit on ERPNext
takes **~34 s** — graphify still walks all 4,676 source files (cache hits are
fast but the walk itself dominates). The slice 9 acceptance asked for `< 5 s`;
that target is met on small/medium apps but not on ERPNext-scale repos. The
warm-path edit→rebuild loop is dominated by graphify's behaviour, not by the
enrichment passes. Filing as a follow-up against graphify rather than
frappe-graph.

## Bugs found & fixed during verification

Every bug below was caught only by running the tool end-to-end against real
graphify output. Each was previously masked by tests that mocked graphify and
hand-crafted graphs.

1. **`build.py` called nonexistent `graphify build --output`.** graphify's
   actual CLI surface is `graphify update <path>`, and the binary writes to
   `<path>/graphify-out/` with no `--output` flag. Fixed: invoke `graphify
   update`, copy `graphify-out/graph.json` to `frappe-graph-out/`. Same fix
   for the `python -m graphifyy` fallback (the module is `graphify`, not
   `graphifyy`).

2. **Enriched edges landed in `edges`, but graphify stores edges in `links`.**
   graphify's NetworkX node-link format uses `links`; it only reads `edges` as
   a fallback when `links` is absent. With both keys present (the graphify
   output already has `links`), every frappe-graph-added edge was invisible to
   graphify queries. Fixed `enrich._merge` to write into `links` when it
   exists, preserving the existing `edges` fallback used by synthetic test
   graphs.

3. **graphify nodes have no `kind` field; passes filtered on `kind == "File"`
   / `"Function"` / `"Method"`.** Test fixtures hand-set `kind`, masking this
   for the entire codebase. Fixed by normalising at the start of the
   enrichment pipeline: tag File / Function / Method based on graphify's
   labelling convention (file label == basename of `source_file`; symbol
   label ends in `()`, leading `.` indicating method on class).

4. **`hooks_pass` dropped *all* `doc_events` if any entry was non-literal.**
   ERPNext's hooks.py uses `tuple(period_closing_doctypes): {...}` as a key,
   so `ast.literal_eval` raised on the whole dict and the entire pass swallowed
   the failure. Fixed with a per-entry fallback that keeps the literal-keyed
   entries (`"Sales Invoice"`, `"User"`, `"Communication"`, ...) and skips
   only the non-literal ones. Added a regression test.

5. **Git post-commit hook silently no-op'd in venv installs.** Git runs hooks
   with a stripped PATH. `frappe-graph` was not on it when the binary lived
   in `.venv/bin/`. The hook redirected stderr and ended with `|| true`, so
   the failure was invisible: a commit produced no rebuild. Fixed by pinning
   the absolute path of the resolving binary at install time, with a
   `python -m frappe_graph.cli` fallback. Adjusted the install-hook regression
   test to accept either form.

6. **Bench-mode merge orphaned cross-app DocType references.** graphify's
   `merge-graphs` prefixes every node id with `<app>::`. A custom-app DocType
   linking to `DocType:Sales Invoice` became an edge into
   `custom_app::DocType:Sales Invoice` — a placeholder that is unconnected
   from the real `erpnext::DocType:Sales Invoice`. Added a post-merge
   stitcher: orphans (`kind` / `label` unset, id shape `<app>::DocType:<X>`)
   whose canonical counterpart exists in another namespace get their inbound
   edges retargeted, and the orphan node is dropped. Two regression tests
   added.

## Build-time envelope

| Repo | Files | Cold build | Warm build | Notes |
| --- | ---: | ---: | ---: | --- |
| Frappe CRM | 693 | ~3 s | ~1.5 s | enrichment passes ~100 ms |
| ERPNext | 4,676 | ~3.5 min (first run, full extraction) | ~20 s | passes ~1.5 s |
| Synthetic bench (custom_app + erpnext + merge) | 4,687 | ~3.5 min | ~22 s + merge | |

Cold-build numbers come from the first run on a fresh clone with no graphify
cache. Warm-build is a re-run with the on-disk cache populated. Both fall
inside the 2–5-minute envelope from the original plan for the first-run cost.

## Out of scope for this slice / follow-ups

- **Token-use HITL measurement** (task 6 above) — needs the user in a Claude
  Code session.
- **ERPNext incremental rebuild target of <5 s** — bounded by graphify's
  global walk, not by the enrichment passes. Follow-up against graphify.
- **graphify `path` / `query` CLI label-fuzzy matching** is awkward for our
  `DocType:<Name>` IDs. A future iteration of the `/frappe-graph` skill
  should bypass graphify's CLI and query the graph.json directly when given a
  DocType-id-shaped argument.
