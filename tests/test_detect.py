from __future__ import annotations

from pathlib import Path

import pytest

from frappe_graph.detect import DetectionError, detect_app


def test_detect_app_succeeds_on_fixture(sample_app: Path) -> None:
    mode, slug = detect_app(sample_app)
    assert mode == "app"
    assert slug == "sample_app"


def test_detect_app_fails_outside_app(tmp_path: Path) -> None:
    (tmp_path / "random.txt").write_text("not a frappe app")
    with pytest.raises(DetectionError) as exc:
        detect_app(tmp_path)
    assert "Not a Frappe app" in str(exc.value)
    assert "hooks.py" in str(exc.value)


def test_detect_app_fails_when_ambiguous(tmp_path: Path) -> None:
    for slug in ("app_one", "app_two"):
        pkg = tmp_path / slug
        pkg.mkdir()
        (pkg / "hooks.py").write_text("")
    with pytest.raises(DetectionError) as exc:
        detect_app(tmp_path)
    assert "Ambiguous" in str(exc.value)
