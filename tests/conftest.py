from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_app(tmp_path: Path) -> Path:
    """Copy the sample_app fixture into a tmp_path and return its path."""
    src = FIXTURE_ROOT / "sample_app"
    dst = tmp_path / "sample_app"
    shutil.copytree(src, dst)
    return dst


@pytest.fixture
def sample_app_with_baseline(sample_app: Path) -> Path:
    """Sample app with the baseline graph.json copied into frappe-graph-out/.

    Simulates the state right after graphify has run but before enrichment.
    """
    out = sample_app / "frappe-graph-out"
    out.mkdir(exist_ok=True)
    baseline = sample_app / "baseline_graph.json"
    shutil.copy(baseline, out / "graph.json")
    return sample_app


def load_graph(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)
