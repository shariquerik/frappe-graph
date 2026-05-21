from __future__ import annotations

from pathlib import Path

import pytest

from frappe_graph.detect import DetectionError, detect, detect_app


def _make_bench(root: Path, slugs: list[str]) -> Path:
    """Construct a minimal bench layout at `root` with `slugs` apps."""
    (root / "apps").mkdir()
    (root / "sites").mkdir()
    for slug in slugs:
        pkg = root / "apps" / slug / slug
        pkg.mkdir(parents=True)
        (pkg / "hooks.py").write_text("")
    return root


def test_detect_bench_returns_sorted_slugs(tmp_path: Path) -> None:
    _make_bench(tmp_path, ["foo", "bar"])
    mode, slugs = detect(tmp_path)
    assert mode == "bench"
    assert slugs == ["bar", "foo"]


def test_detect_bench_ignores_apps_without_hooks(tmp_path: Path) -> None:
    _make_bench(tmp_path, ["foo"])
    # An apps/baz/ directory without baz/hooks.py shouldn't count.
    (tmp_path / "apps" / "baz").mkdir()
    (tmp_path / "apps" / "baz" / "README.md").write_text("nope")

    mode, slugs = detect(tmp_path)
    assert mode == "bench"
    assert slugs == ["foo"]


def test_detect_bench_empty_apps_dir_is_still_bench(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "sites").mkdir()
    mode, slugs = detect(tmp_path)
    assert mode == "bench"
    assert slugs == []


def test_detect_app_via_detect(sample_app: Path) -> None:
    mode, slug = detect(sample_app)
    assert mode == "app"
    assert slug == "sample_app"


def test_detect_neither_layout_errors_with_both_hints(tmp_path: Path) -> None:
    (tmp_path / "random.txt").write_text("not frappe")
    with pytest.raises(DetectionError) as exc:
        detect(tmp_path)
    msg = str(exc.value)
    # Must mention both layouts.
    assert "hooks.py" in msg
    assert "apps/" in msg
    assert "sites/" in msg


def test_detect_apps_without_sites_falls_through(tmp_path: Path) -> None:
    """apps/ alone (no sites/) is not bench — should fall through to app
    detection, which also fails, producing the both-layouts error."""
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "foo" / "foo").mkdir(parents=True)
    (tmp_path / "apps" / "foo" / "foo" / "hooks.py").write_text("")

    with pytest.raises(DetectionError) as exc:
        detect(tmp_path)
    msg = str(exc.value)
    assert "apps/" in msg
    assert "sites/" in msg


def test_detect_app_still_works_standalone(sample_app: Path) -> None:
    # Ensure we didn't break the original API.
    mode, slug = detect_app(sample_app)
    assert mode == "app"
    assert slug == "sample_app"
