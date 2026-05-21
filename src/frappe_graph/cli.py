"""`frappe-graph` CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from frappe_graph import __version__
from frappe_graph.build import build as run_build
from frappe_graph.detect import DetectionError, detect
from frappe_graph.merge import merge as run_merge_impl


@click.group()
@click.version_option(__version__, prog_name="frappe-graph")
def main() -> None:
    """Frappe-aware enrichment layer over graphify."""


def _resolve_bench_apps(
    bench_path: Path,
    all_apps: bool,
    selected: tuple[str, ...],
    available: list[str],
) -> list[str]:
    """Decide which apps to build for a bench.

    `all_apps` wins; otherwise `selected` is used (validated against `available`);
    otherwise an interactive prompt is shown.
    """
    if all_apps:
        return list(available)

    if selected:
        unknown = [name for name in selected if name not in available]
        if unknown:
            joined = ", ".join(unknown)
            avail_joined = ", ".join(available) if available else "(none)"
            raise click.ClickException(
                f"Unknown app(s): {joined}. Available: {avail_joined}"
            )
        # Preserve user-specified order, de-duplicated.
        seen: set[str] = set()
        ordered: list[str] = []
        for name in selected:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
        return ordered

    # Interactive mode. In non-tty environments this errors out clearly.
    if not sys.stdin.isatty():
        raise click.ClickException(
            "Bench mode requires --all or one or more --app NAME flags when stdin is not a TTY. "
            f"Available apps: {', '.join(available) if available else '(none)'}"
        )

    if not available:
        raise click.ClickException(f"No apps found under {bench_path}/apps/.")

    click.echo(f"Apps under {bench_path}/apps/:")
    for i, name in enumerate(available, 1):
        click.echo(f"  {i}. {name}")
    raw = click.prompt(
        "Select apps to build (comma-separated names, or 'all')",
        default="all",
    )
    raw = raw.strip()
    if raw == "all":
        return list(available)
    names = [n.strip() for n in raw.split(",") if n.strip()]
    unknown = [n for n in names if n not in available]
    if unknown:
        raise click.ClickException(f"Unknown app(s): {', '.join(unknown)}")
    return names


@main.command()
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option("--update", is_flag=True, help="Incremental rebuild (forwards graphify's --update).")
@click.option(
    "--app",
    "apps",
    multiple=True,
    help="Bench mode: build only this app (repeatable).",
)
@click.option("--all", "all_apps", is_flag=True, help="Bench mode: build every app under apps/.")
@click.option(
    "--merge",
    "do_merge",
    is_flag=True,
    help="Bench mode: after per-app builds, merge into a bench-wide graph.",
)
def build(
    path: str | None,
    update: bool,
    apps: tuple[str, ...],
    all_apps: bool,
    do_merge: bool,
) -> None:
    """Build the enriched graph for the Frappe app or bench at PATH (default: cwd)."""
    target = Path(path) if path else Path.cwd()
    try:
        mode_info = detect(target)
    except DetectionError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    mode = mode_info[0]

    if mode == "app":
        if apps or all_apps or do_merge:
            click.echo(
                "error: --app/--all/--merge are only valid in bench mode "
                "(<root>/apps/ + sites/).",
                err=True,
            )
            sys.exit(1)
        try:
            graph_path = run_build(target, update=update)
        except DetectionError as exc:
            click.echo(f"error: {exc}", err=True)
            sys.exit(1)
        except RuntimeError as exc:
            click.echo(f"error: {exc}", err=True)
            sys.exit(2)
        click.echo(f"Enriched graph written to {graph_path}")
        return

    # Bench mode.
    available: list[str] = list(mode_info[1])  # type: ignore[arg-type]
    bench_path = Path(target).resolve()

    try:
        chosen = _resolve_bench_apps(bench_path, all_apps, apps, available)
    except click.ClickException as exc:
        click.echo(f"error: {exc.message}", err=True)
        sys.exit(1)

    if not chosen:
        click.echo(
            f"error: no apps to build under {bench_path}/apps/.",
            err=True,
        )
        sys.exit(1)

    built_paths: list[Path] = []
    for slug in chosen:
        app_path = bench_path / "apps" / slug
        click.echo(f"Building {slug}...")
        try:
            graph_path = run_build(app_path, update=update)
        except DetectionError as exc:
            click.echo(f"error: {exc}", err=True)
            sys.exit(1)
        except RuntimeError as exc:
            click.echo(f"error: {exc}", err=True)
            sys.exit(2)
        built_paths.append(graph_path)
        click.echo(f"  -> {graph_path}")

    if do_merge:
        click.echo("Merging per-app graphs...")
        try:
            merged_path = run_merge_impl(bench_path)
        except RuntimeError as exc:
            click.echo(f"error: {exc}", err=True)
            sys.exit(2)
        click.echo(f"Merged bench graph written to {merged_path}")


@main.command(name="merge")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
def merge_cmd(path: str | None) -> None:
    """Merge per-app graphs under PATH/apps/ into a bench-wide graph."""
    target = Path(path) if path else Path.cwd()
    try:
        mode_info = detect(target)
    except DetectionError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    if mode_info[0] != "bench":
        click.echo(
            f"error: `merge` requires a bench root (apps/ + sites/), got {target}",
            err=True,
        )
        sys.exit(1)

    try:
        merged_path = run_merge_impl(Path(target).resolve())
    except RuntimeError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    click.echo(f"Merged bench graph written to {merged_path}")


if __name__ == "__main__":
    main()
