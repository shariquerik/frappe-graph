"""Install/uninstall the ``/frappe-graph`` Claude Code skill.

The skill is a single ``SKILL.md`` file with YAML frontmatter that tells the
assistant to prefer querying the pre-built knowledge graph over grepping.
It can be installed at the project level (``<project>/.claude/skills/``) or
globally (``~/.claude/skills/``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

SKILL_DIR_NAME = "frappe-graph"
SKILL_FILE_NAME = "SKILL.md"
OUT_DIR_NAME = "frappe-graph-out"

SKILL_MD = """\
---
name: frappe-graph
description: Query the pre-built knowledge graph at frappe-graph-out/graph.json for navigation in this Frappe app. Use before grepping for DocTypes, hooks, RPC handlers, or controllers.
---

# /frappe-graph

This folder is a Frappe app (or bench) with a pre-built knowledge graph at
`frappe-graph-out/graph.json` produced by `frappe-graph build`.

## Prefer graph queries over grep

For DocType, hook, RPC, or controller navigation questions, use:

- `frappe-graph query "<id-or-question>"` - return nodes/edges matching a substring or id.
- `frappe-graph path "<source-id>" "<target-id>"` - shortest path between two nodes.
- `frappe-graph explain "<id>"` - node details + immediate neighbours.

(These delegate to graphify under the hood and work against the enriched graph.)

## Node id conventions

- `DocType:<Name>` - a Frappe DocType (e.g. `DocType:Sales Invoice`).
- `BUILTIN:<method>` - synthetic node for a Frappe built-in RPC (e.g. `BUILTIN:frappe.client.get_list`).
- `MISSING_RPC:<url>` - placeholder for an RPC URL referenced by Vue/JS but not found in this graph.
- `Scheduler:<interval>` - synthetic node for a scheduler interval (daily, hourly, cron, etc.).
- File/function/class nodes come from graphify (`file:...`, `function:...`, `class:...`).

## Edge relations

- `controller` - DocType -> its `<slug>.py` controller file.
- `LINK[<fieldname>]` - DocType Link/Table/Dynamic Link field -> target DocType.
- `RPC_CALLS` - Vue/JS node -> Python whitelisted method or builtin.
- `READS_DOCTYPE` / `WRITES_DOCTYPE` - caller -> DocType (via `frappe.get_doc`, `createDocumentResource`, etc.).
- `on_submit`, `before_save`, `validate`, `on_cancel`, ... - DocType -> hooks.py-declared handler.
- `overrides-controller`, `permission-query`, `has-permission` - hooks.py overrides.

## When to fall back to reading code

If a question is about implementation details inside a function body, the graph
won't have it - read the file directly. The graph is for cross-file navigation.
"""


def _skill_root(project_root: Path, *, global_install: bool) -> Path:
    """Return the directory containing the skill folder (parent of ``frappe-graph/``)."""
    if global_install:
        return Path.home() / ".claude" / "skills"
    return Path(project_root) / ".claude" / "skills"


def skill_path(project_root: Path, *, global_install: bool = False) -> Path:
    """Return the absolute path where the SKILL.md is/would be installed."""
    return (
        _skill_root(project_root, global_install=global_install)
        / SKILL_DIR_NAME
        / SKILL_FILE_NAME
    ).resolve()


def install_skill(project_root: Path, *, global_install: bool = False) -> Path:
    """Write the SKILL.md file. Returns the path written.

    Idempotent: overwrites any existing file at the same location.
    """
    target = skill_path(project_root, global_install=global_install)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SKILL_MD)
    return target


def uninstall_skill(
    project_root: Path,
    *,
    global_install: bool = False,
    purge: bool = False,
) -> dict:
    """Remove the skill directory.

    With ``purge=True`` (project-only), also delete ``frappe-graph-out/``
    from the project root. ``purge=True`` with ``global_install=True`` is an
    error - purge is a project-level concept.

    Returns a dict like
    ``{"removed": [paths], "missing": bool, "purged": bool}``.
    """
    if global_install and purge:
        raise ValueError(
            "--purge is only valid for project-level uninstall; "
            "global uninstall has no frappe-graph-out/ to remove."
        )

    skill_dir = (
        _skill_root(project_root, global_install=global_install) / SKILL_DIR_NAME
    ).resolve()

    removed: list[Path] = []
    missing = not skill_dir.exists()

    if not missing:
        shutil.rmtree(skill_dir)
        removed.append(skill_dir)

    purged = False
    if purge:
        out_dir = (Path(project_root) / OUT_DIR_NAME).resolve()
        if out_dir.exists():
            shutil.rmtree(out_dir)
            removed.append(out_dir)
            purged = True

    return {"removed": removed, "missing": missing, "purged": purged}
