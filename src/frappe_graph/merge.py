"""`frappe-graph merge`: combine per-app graphs into a single bench-wide graph.

We invoke graphify's `merge-graphs` command rather than implementing our own
merge — graphify owns the graph schema and we should not reimplement its merge
semantics. For tests we accept an injected `run_merge` callable so they don't
need graphify on PATH.

graphify's merge prefixes every node id with `<app>::`. A custom-app DocType
that links to `DocType:Sales Invoice` becomes an edge into a placeholder
`custom_app::DocType:Sales Invoice` node — orphaned from the real
`erpnext::DocType:Sales Invoice`. After merge we stitch these together: any
orphan `<app>::DocType:<X>` node whose canonical counterpart lives in another
namespace has its inbound edges retargeted, and the placeholder is dropped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

OUTPUT_DIR_NAME = "frappe-graph-out"
BENCH_GRAPH_NAME = "bench-graph.json"
GITIGNORE_CONTENTS = "bench-graph.json\n"

RunMerge = Callable[[list[Path], Path], None]


def _default_run_merge(inputs: list[Path], output_path: Path) -> None:
    """Invoke graphify's merge-graphs against `inputs`, writing to `output_path`.

    Tries `graphify` on PATH first; falls back to `python -m graphifyy`.
    """
    cmd_base: list[str]
    if shutil.which("graphify"):
        cmd_base = ["graphify"]
    else:
        cmd_base = [sys.executable, "-m", "graphify"]

    cmd = [*cmd_base, "merge-graphs", *(str(p) for p in inputs), "--out", str(output_path)]

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "graphify (graphifyy) is not installed. Install with `pip install graphifyy` "
            "or `uv tool install graphifyy`."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"graphify merge-graphs failed with exit code {exc.returncode}"
        ) from exc


def _find_app_graphs(bench_path: Path) -> list[Path]:
    """Return the sorted list of `apps/*/frappe-graph-out/graph.json` paths."""
    apps_dir = bench_path / "apps"
    if not apps_dir.is_dir():
        return []
    found: list[Path] = []
    for app_dir in sorted(apps_dir.iterdir()):
        if not app_dir.is_dir():
            continue
        graph = app_dir / OUTPUT_DIR_NAME / "graph.json"
        if graph.is_file():
            found.append(graph)
    return found


def merge(
    bench_path: Path,
    output_dir: Path | None = None,
    run_merge: RunMerge | None = None,
) -> Path:
    """Merge every per-app `graph.json` under `bench_path/apps/` into one graph.

    Args:
        bench_path: a bench root containing `apps/` and `sites/`.
        output_dir: directory to write the merged graph into. Defaults to
            `bench_path/frappe-graph-out/`.
        run_merge: callable invoked as `run_merge(inputs, output_path)`. Defaults
            to calling graphify's `merge-graphs` subcommand. Tests inject a fake
            so they don't depend on graphify being installed.

    Returns the path to the merged `bench-graph.json`.
    """
    bench_path = Path(bench_path).resolve()

    inputs = _find_app_graphs(bench_path)
    if not inputs:
        raise RuntimeError(
            f"No per-app graphs found under {bench_path}/apps/*/{OUTPUT_DIR_NAME}/graph.json. "
            f"Run `frappe-graph build --all` first."
        )

    out_dir = Path(output_dir).resolve() if output_dir is not None else bench_path / OUTPUT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    gitignore = out_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_CONTENTS)

    output_path = out_dir / BENCH_GRAPH_NAME
    runner = run_merge if run_merge is not None else _default_run_merge
    runner(inputs, output_path)

    if not output_path.exists():
        raise RuntimeError(
            f"Expected merged graph at {output_path}, but it does not exist. "
            f"Check graphify's output."
        )

    _stitch_on_disk(output_path)
    return output_path


def find_app_graphs(bench_path: Path) -> list[Path]:
    """Public helper: list of per-app graph.json paths under bench_path."""
    return _find_app_graphs(Path(bench_path).resolve())


def _is_orphan_doctype_node(node: dict) -> bool:
    """A DocType placeholder graphify created during merge because the source
    graph referenced it without owning it. Heuristic: no kind/label set, id
    has the `<app>::DocType:<X>` shape.
    """
    if node.get("kind") or node.get("label"):
        return False
    nid = node.get("id")
    return isinstance(nid, str) and "::" in nid and "::DocType:" in nid


def _split_prefix(node_id: str) -> tuple[str | None, str]:
    """Split `app::rest` into `(app, rest)`; return `(None, node_id)` if there
    is no `::` prefix.
    """
    if "::" in node_id:
        app, rest = node_id.split("::", 1)
        return app, rest
    return None, node_id


def stitch_cross_app_doctype_refs(merged: dict) -> dict:
    """Resolve `<app>::DocType:<X>` orphans against the canonical node in
    whatever namespace really owns the DocType. Rewrites inbound edges and
    drops the orphan node. Returns the same graph dict for chaining.

    Picks the *canonical* node as the first non-orphan `<*>::DocType:<X>` in
    the graph — i.e. the one with `kind == "DocType"` set by an app's DocType
    pass. If no canonical node exists (e.g. both apps reference the DocType
    but neither owns it), the orphan is left in place.
    """
    nodes = merged.get("nodes", [])
    canonical: dict[str, str] = {}  # bare "DocType:X" -> canonical prefixed id
    orphans: dict[str, str] = {}    # prefixed orphan id -> bare "DocType:X"

    for n in nodes:
        nid = n.get("id")
        if not isinstance(nid, str):
            continue
        app, rest = _split_prefix(nid)
        if not rest.startswith("DocType:"):
            continue
        if _is_orphan_doctype_node(n):
            orphans[nid] = rest
        elif n.get("kind") == "DocType":
            # First canonical owner wins. Multiple DocType definitions across
            # apps shouldn't exist in practice — but if it does, we keep the
            # first seen.
            canonical.setdefault(rest, nid)

    if not orphans:
        return merged

    # Build the rewrite map: orphan id -> canonical id (only when canonical exists
    # AND lives in a different namespace).
    rewrite: dict[str, str] = {}
    for orphan_id, bare in orphans.items():
        target = canonical.get(bare)
        if target is None or target == orphan_id:
            continue
        rewrite[orphan_id] = target
    if not rewrite:
        return merged

    # Retarget edges. graphify uses `links`; tolerate `edges` for older shapes.
    for key in ("links", "edges"):
        if key not in merged:
            continue
        for edge in merged[key]:
            for endpoint in ("source", "target"):
                v = edge.get(endpoint)
                if isinstance(v, str) and v in rewrite:
                    edge[endpoint] = rewrite[v]

    # Drop the now-unreferenced orphan nodes.
    drop_ids = set(rewrite)
    merged["nodes"] = [n for n in nodes if n.get("id") not in drop_ids]
    return merged


def _stitch_on_disk(path: Path) -> None:
    """Read the merged graph, apply cross-app stitching, write back."""
    data = json.loads(path.read_text())
    stitch_cross_app_doctype_refs(data)
    path.write_text(json.dumps(data, indent=2))


__all__ = [
    "merge",
    "find_app_graphs",
    "stitch_cross_app_doctype_refs",
    "BENCH_GRAPH_NAME",
    "OUTPUT_DIR_NAME",
]
