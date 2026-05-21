"""`frappe-graph` CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from frappe_graph import __version__
from frappe_graph.build import build as run_build
from frappe_graph.detect import DetectionError


@click.group()
@click.version_option(__version__, prog_name="frappe-graph")
def main() -> None:
    """Frappe-aware enrichment layer over graphify."""


@main.command()
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option("--update", is_flag=True, help="Incremental rebuild (forwards graphify's --update).")
def build(path: str | None, update: bool) -> None:
    """Build the enriched graph for the Frappe app at PATH (default: cwd)."""
    target = Path(path) if path else Path.cwd()
    try:
        graph_path = run_build(target, update=update)
    except DetectionError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    except RuntimeError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    click.echo(f"Enriched graph written to {graph_path}")


if __name__ == "__main__":
    main()
