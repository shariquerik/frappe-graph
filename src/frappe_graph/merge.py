"""`frappe-graph merge`: combine per-app graphs into a single bench-wide graph.

We invoke graphify's `merge-graphs` command rather than implementing our own
merge — graphify owns the graph schema and we should not reimplement its merge
semantics. For tests we accept an injected `run_merge` callable so they don't
need graphify on PATH.
"""

from __future__ import annotations

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
        cmd_base = [sys.executable, "-m", "graphifyy"]

    cmd = [*cmd_base, "merge-graphs", *(str(p) for p in inputs), "--output", str(output_path)]

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

    return output_path


def find_app_graphs(bench_path: Path) -> list[Path]:
    """Public helper: list of per-app graph.json paths under bench_path."""
    return _find_app_graphs(Path(bench_path).resolve())


__all__ = ["merge", "find_app_graphs", "BENCH_GRAPH_NAME", "OUTPUT_DIR_NAME"]
