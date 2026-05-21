# frappe-graph

A Frappe-aware enrichment layer over [graphify](https://github.com/safishamsi/graphify). Reads graphify's `graph.json`, scans the Frappe app for convention-driven files (DocType JSON, `hooks.py`, `@frappe.whitelist()`, frappe-ui resource calls, `frappe.get_doc` references), and writes additional nodes/edges back using graphify's existing schema.

## Where the work is tracked

GitHub issues on [`shariquerik/frappe-graph`](https://github.com/shariquerik/frappe-graph/issues) are the source of truth. The repo is brand new — there's no implementation yet.

- **Issue #1**: the full PRD (also at [`PRD.md`](./PRD.md)).
- **Issues #2–#10**: 9 vertical-slice tickets, each tagged `ready-for-agent`. Pick one and ship it end-to-end.

To see what's available to pick up:

```bash
gh issue list --label ready-for-agent --state open
gh issue view <n>
```

## Slice dependency graph

```
#2 (skeleton + DocType pass)
├── #3  hooks.py pass
├── #4  @frappe.whitelist() pass
│   └── #5  RPC pass (frontend ↔ backend)
├── #6  frappe.get_doc / get_list / new_doc refs
├── #7  bench-mode + multi-app + merge
├── #8  /frappe-graph Claude Code skill install/uninstall
└── #9  git post-commit hook install/uninstall

#10 (HITL: ERPNext + CRM + token-use verification)
└── blocked by #2, #3, #4, #5, #6, #8, #9
```

Issue #2 is the only unblocker. Once it lands, #3, #4, #6, #7, #8, #9 can proceed in parallel; #5 waits on #4; #10 closes the loop and is HITL.

## Design constraints worth knowing before you code

- **Post-processor, not a fork.** graphify exposes no plugin API. We read its `graph.json`, add nodes/edges, write it back. Don't fork graphify; don't reimplement its merge.
- **Uniform pass interface.** Every enrichment pass is `(app_path, graph) -> (added_nodes, added_edges)`. Keep it that way — slice #2 establishes this contract and slices #3–#6 rely on it.
- **Output dir is `frappe-graph-out/`**, not graphify's default `graphify-out/`. They must not collide if both tools run in the same repo.
- **Edge confidence.** `EXTRACTED` for unambiguous structured data (DocType JSON, hooks.py dicts). `INFERRED` for string-matched RPC. Never silently drop a recognised call — emit a `MISSING_RPC:<url>` placeholder edge instead.
- **No runtime import of `frappe`.** The enricher reads files; it must not require a Frappe install.
- **Domain knowledge already documented.** See `claude-frappe-toolkit`'s `skills/frappe/` references (DocType anatomy, `frappe.*` namespace, hooks.py) — the enricher's recognition should match what those docs say.

## See also

- [`PRD.md`](./PRD.md) — full product spec, user stories, testing decisions, out-of-scope.
- [`plan.md`](./plan.md) — original planning doc with cost envelope, verification milestones, and detailed enricher behaviour.
