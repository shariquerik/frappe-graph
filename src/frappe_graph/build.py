"""`frappe-graph build`: run graphify on an app, then enrich the graph."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from frappe_graph.detect import detect_app
from frappe_graph.enrich import default_passes, enrich

OUTPUT_DIR_NAME = "frappe-graph-out"
GITIGNORE_CONTENTS = "manifest.json\ncost.json\n"


def _run_graphify(app_path: Path, output_dir: Path, update: bool = False) -> None:
    """Invoke graphify against `app_path`, writing into `output_dir`.

    Tries `graphify` on PATH first; falls back to `python -m graphifyy`.
    """
    cmd_base: list[str]
    if shutil.which("graphify"):
        cmd_base = ["graphify"]
    else:
        cmd_base = [sys.executable, "-m", "graphifyy"]

    cmd = [*cmd_base, "build", str(app_path), "--output", str(output_dir)]
    if update:
        cmd.append("--update")

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "graphify (graphifyy) is not installed. Install with `pip install graphifyy` "
            "or `uv tool install graphifyy`."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"graphify build failed with exit code {exc.returncode}") from exc


def build(app_path: Path, update: bool = False, skip_graphify: bool = False) -> Path:
    """Build the enriched graph for a single Frappe app.

    Returns the path to the enriched graph.json.

    skip_graphify is used by tests that ship a baseline graph.json — production
    callers should leave it False so graphify runs first.
    """
    app_path = Path(app_path).resolve()
    detect_app(app_path)  # Raises DetectionError if not an app.

    output_dir = app_path / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)

    gitignore = output_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_CONTENTS)

    graph_path = output_dir / "graph.json"
    if not skip_graphify:
        _run_graphify(app_path, output_dir, update=update)

    if not graph_path.exists():
        raise RuntimeError(
            f"Expected graphify to produce {graph_path}, but it does not exist. "
            f"Check graphify's output."
        )

    enrich(app_path, graph_path, default_passes())
    return graph_path
