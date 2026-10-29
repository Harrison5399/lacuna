"""Step 02: grow the seeds into the experiment graph for the next round, from each source's curated facts and the
literature graph of the papers read so far.

Each source with a module here (mesh.py, hgnc.py) adds the neighbourhood its [expand.<source>] settings ask
for. Then the latest literature graph (05) is merged in: all its edges, and every concept they connect to the
graph, at distance = its nearest graph neighbour's + 1, with reason "literature". Only edges that at least
[expand.literature] min_papers papers state without negating, and of the relations listed there, connect;
a hub (a concept in more than max_share of the experiment's papers) is never added as a new node.
Every node records how far it is from the nearest seed (distance) and how it got in (reason); step 03 picks
the round's download topics from that. Run 01-query first.

Writes <runs>/<experiment name>/02-expand/graph_<n>/, where n is the round it is for (one more than the
topic rounds step 03 has written; rerunning before 03 rewrites the same graph):
    nodes.parquet    id, name, types, source, distance, reason, support (papers backing its literature edges)
    edges.parquet    s, r, o, layer, source, source_version, and for literature edges n_papers, n_negated,
                     n_hedged, n_primary, n_claims, first_seen, last_seen, papers

Usage:
    python src/02-expand/run.py --experiment experiments/default.toml
    python src/02-expand/run.py --experiment experiments/default.toml --root /N/project/ontology_learning/L665/data/background --runs /N/project/ontology_learning/L665/runs
"""
import argparse
import heapq
import importlib
import json
import tomllib
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

LACUNA = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grow the seeds into the experiment's graph for the next round.")
    parser.add_argument("--experiment", type=Path, default=LACUNA / "experiments" / "default.toml",
                        help="Experiment file (default: experiments/default.toml)")
    parser.add_argument("--root", type=Path, default=LACUNA / "data",
                        help="Folder 00-setup saved the sources under (default: lacuna/data)")
    parser.add_argument("--runs", type=Path, default=LACUNA / "runs",
                        help="Folder each experiment's step outputs go under (default: lacuna/runs)")
    return parser.parse_args()


def build(seeds: list[str], experiment: dict, root: Path) -> tuple[pa.Table, pa.Table]:
    """Nodes and edges grown from these seed IDs."""
    nodes, edges = [], []
    for source, version in experiment["sources"].items():
        n, e = importlib.import_module(source).expand(seeds, root / source / version, experiment["expand"][source],
                                                      version)
        nodes += n
        edges += e
    return pa.Table.from_pylist(nodes), pa.Table.from_pylist(edges)


def merge(nodes: pa.Table, edges: pa.Table, literature: Path, settings: dict) -> tuple[pa.Table, pa.Table]:
    """The curated graph plus the literature graph's edges and the concepts they connect to it."""
    lit_edges = pq.read_table(literature / "edges.parquet").to_pylist() if (literature / "edges.parquet").exists() else []
    lit_nodes = {n["id"]: n for n in pq.read_table(literature / "nodes.parquet").to_pylist()}
    graph = nodes.to_pylist()
    distance = {n["id"]: n["distance"] for n in graph}
    neighbours = defaultdict(set)
    for e in lit_edges:
        if e["r"] in settings["relations"] and e["n_papers"] - e["n_negated"] >= settings["min_papers"]:
            neighbours[e["s"]].add(e["o"])
            neighbours[e["o"]].add(e["s"])
    heap = [(d, i) for i, d in distance.items()]
    heapq.heapify(heap)
    while heap:
        d, i = heapq.heappop(heap)
        if d > distance[i]:
            continue
        for j in neighbours[i]:
            if j not in distance and lit_nodes.get(j, {}).get("share", 0) > settings["max_share"]:
                continue
            if d + 1 < distance.get(j, d + 2):
                distance[j] = d + 1
                heapq.heappush(heap, (d + 1, j))
    # How many papers back each node's literature edges (without negating them); step 03 ranks by it.
    support = defaultdict(int)
    for e in lit_edges:
        if e["r"] in settings["relations"]:
            support[e["s"]] += e["n_papers"] - e["n_negated"]
            support[e["o"]] += e["n_papers"] - e["n_negated"]
    known = {n["id"] for n in graph}
    for n in graph:
        n["distance"], n["support"] = distance[n["id"]], support[n["id"]]
    graph += [{"id": i, "name": lit_nodes.get(i, {}).get("name"), "types": lit_nodes.get(i, {}).get("types"),
               "source": "literature", "distance": d, "reason": "literature", "support": support[i]}
              for i, d in distance.items() if i not in known]
    keys = list(dict.fromkeys(k for e in edges.to_pylist()[:1] + lit_edges[:1] for k in e))
    rows = [{k: e.get(k) for k in keys} for e in edges.to_pylist() + [{**e, "source": "literature"} for e in lit_edges]]
    return pa.Table.from_pylist(graph), pa.Table.from_pylist(rows)


def main() -> None:
    args = parse_args()
    with open(args.experiment, "rb") as f:
        experiment = tomllib.load(f)
    run = args.runs / experiment["name"]
    seeds = json.loads((run / "01-query" / "seeds.json").read_text(encoding="utf-8"))["seeds"]
    nodes, edges = build([s["id"] for s in seeds], experiment, args.root)
    rounds = len(list((run / "03-topics").glob("round_*.json")))
    literature = run / "05-literature" / f"round_{rounds}"
    if (literature / "nodes.parquet").exists():
        nodes, edges = merge(nodes, edges, literature, experiment["expand"]["literature"])
        print(f"merged {literature}")
    out = run / "02-expand" / f"graph_{rounds + 1}"
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(nodes, out / "nodes.parquet")
    pq.write_table(edges, out / "edges.parquet")
    print(f"wrote {out} ({nodes.num_rows:,} nodes, {edges.num_rows:,} edges from {len(seeds)} seeds)")


if __name__ == "__main__":
    main()
