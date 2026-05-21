"""Group enriched-graph edges by `confidence` and `relation`.

Usage:

    python scripts/confidence_audit.py <path-to-graph.json> [N]

Optionally sample N random INFERRED edges (default 10) to spot-check for false
positives. Run on a real enriched graph (e.g. ERPNext or Frappe CRM).
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    graph_path = Path(argv[1])
    sample_n = int(argv[2]) if len(argv) > 2 else 10

    g = json.loads(graph_path.read_text())
    links = g.get("links", g.get("edges", []))
    print(f"graph: {graph_path}")
    print(f"nodes: {len(g.get('nodes', []))}, edges: {len(links)}")

    conf = Counter(l.get("confidence", "?") for l in links)
    print("\nBy confidence:")
    total = sum(conf.values()) or 1
    for c, n in conf.most_common():
        print(f"  {c:10}: {n:>6}  ({n * 100 // total:>2}%)")

    print("\nBy (confidence, relation):")
    pair = Counter((l.get("confidence", "?"), l.get("relation", "?")) for l in links)
    for (c, r), n in pair.most_common(25):
        print(f"  {c:10} | {r:30}: {n}")

    inferred = [l for l in links if l.get("confidence") == "INFERRED"]
    if inferred:
        sample = random.sample(inferred, min(sample_n, len(inferred)))
        print(f"\nRandom {len(sample)} INFERRED edges for spot check:")
        for l in sample:
            print(f"  {l.get('source')}\n    --{l.get('relation')}-->\n    {l.get('target')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
