"""Step 03: choose the next round of download topics from the experiment's graph (02-expand).

Nodes of the [topics] types become topics nearest the seeds first, then in the order of [topics] reasons, then
the most papers backing them in the literature graph (support), skipping nodes that were a topic in an earlier
round. Each topic gets a spec from every download source with a module here
(pubmed.py, clinicaltrials.py, arxiv.py), counted against the real API. A PubMed topic matching more than split_cap
papers is split into its MeSH children if it is what the goal is about (split_reasons: the seeds and their
subtypes); a too-big neighbour is narrowed to the papers tagged with that heading itself instead, so
splitting "Adenocarcinoma" doesn't bring thyroid cancer into a lung cancer experiment. The round stops at
max_topics, or when the paper budget across all rounds runs out; what didn't fit is left for the next round.

Each run writes the next round, <runs>/<experiment name>/03-topics/round_<n>.json, from 02's graph_<n>:
    topics    name (the download's topic name), node, label, types, distance, reason, take (papers, summed over
              the sources whose module sets PAPERS = True),
              and one entry per source: {query or params, count, take}
    skipped   nodes that got no topic, and why
Step 04 downloads every round.

Usage:
    python src/03-topics/run.py --experiment experiments/default.toml
    python src/03-topics/run.py --experiment experiments/default.toml --redo       # rewrite the latest round
    python src/03-topics/run.py --experiment experiments/default.toml --root /N/project/ontology_learning/L665/data/background --runs /N/project/ontology_learning/L665/runs --env-file ~/.ncbi.env
"""
import argparse
import datetime
import importlib
import json
import re
import sys
import tomllib
from pathlib import Path

import pyarrow.parquet as pq

LACUNA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LACUNA / "src" / "shared"))
import ncbi  # noqa: E402  (query text, filters and NCBI access shared with step 04)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Choose the next round of download topics from the experiment's graph.")
    parser.add_argument("--experiment", type=Path, default=LACUNA / "experiments" / "default.toml",
                        help="Experiment file (default: experiments/default.toml)")
    parser.add_argument("--root", type=Path, default=LACUNA / "data",
                        help="Folder 00-setup saved the sources under (default: lacuna/data)")
    parser.add_argument("--runs", type=Path, default=LACUNA / "runs",
                        help="Folder each experiment's step outputs go under (default: lacuna/runs)")
    parser.add_argument("--graph", type=Path,
                        help="Folder with the graph's nodes.parquet (default: <runs>/<experiment>/02-expand/graph_<n>)")
    parser.add_argument("--env-file", type=Path,
                        help="File with NCBI_EMAIL= and NCBI_API_KEY= (default: the nearest .env.local, else ~/.ncbi.env)")
    parser.add_argument("--redo", action="store_true", help="Rewrite the latest round instead of adding one")
    return parser.parse_args()


def slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:50] or "topic"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def choose(experiment: dict, nodes: list[dict], previous: list[dict], root: Path,
           env_file: Path | None = None) -> tuple[list[dict], list[dict]]:
    """The next round's topics, and the nodes skipped, given the graph's nodes and the earlier rounds."""
    settings, download = experiment["topics"], experiment["download"]
    done = {t["node"] for r in previous for t in r["topics"]}
    names = {t["name"] for r in previous for t in r["topics"]}
    used = sum(t["take"] for r in previous for t in r["topics"])
    order = {reason: i for i, reason in enumerate(settings["reasons"])}
    queue = sorted((n for n in nodes
                    if n["reason"] in order and n["distance"] <= settings["max_distance"] and n["id"] not in done
                    and set(n["types"] or []) & set(settings["types"])),
                   key=lambda n: (n["distance"], order[n["reason"]], -(n.get("support") or 0), n["name"] or ""))
    # Every node in reach becomes a topic in this round or a later one, so a heading whose children are
    # among them is searched without its subtypes ([MeSH:noexp]).
    chosen = done | {n["id"] for n in queue}
    sources = {s: importlib.import_module(s) for s in download["sources"]}
    contexts = {s: m.context(experiment, root) for s, m in sources.items()}
    client = ncbi.NCBI(env_file)

    topics, skipped = [], []
    while queue and len(topics) < settings["max_topics"]:
        node = queue.pop(0)
        specs = {s: m.spec(node, contexts[s], chosen) for s, m in sources.items()}
        specs = {s: sources[s].count(spec, download[s], client) for s, spec in specs.items() if spec}
        if "pubmed" in specs and specs["pubmed"]["count"] > settings["split_cap"]:
            pubmed = sources["pubmed"]
            if node["reason"] in settings["split_reasons"]:
                split = pubmed.children(node, contexts["pubmed"], chosen, specs["pubmed"])
                if split:
                    chosen |= {c["id"] for c in split}
                    queue = split + queue
                    specs["pubmed"] = pubmed.count(pubmed.spec(node, contexts["pubmed"], chosen), download["pubmed"],
                                                   client)
            elif pubmed.narrow(specs["pubmed"])["query"] != specs["pubmed"]["query"]:
                specs["pubmed"] = pubmed.count(pubmed.narrow(specs["pubmed"]), download["pubmed"], client)
        if not any(s["count"] for s in specs.values()):
            skipped.append({"node": node["id"], "label": node["name"], "why": "no source has records for it"})
            continue
        take = sum(spec["take"] for s, spec in specs.items() if getattr(sources[s], "PAPERS", False))
        if used + take > settings["paper_budget"]:
            skipped.append({"node": node["id"], "label": node["name"], "why": "paper budget used up"})
            continue
        used += take
        topics.append({"name": slug(node["name"], names), "node": node["id"], "label": node["name"],
                       "types": node["types"], "distance": node["distance"], "reason": node["reason"],
                       "take": take, **specs})
        print(f"{topics[-1]['name']:40} " + "  ".join(f"{s} {v['count']:>8,} -> {v['take']:>5,}"
                                                     for s, v in specs.items()), flush=True)
    return topics, skipped


def main() -> None:
    args = parse_args()
    with open(args.experiment, "rb") as f:
        experiment = tomllib.load(f)
    folder = args.runs / experiment["name"] / "03-topics"
    rounds = sorted(folder.glob("round_*.json"), key=lambda p: int(p.stem.split("_")[1]))
    if args.redo and rounds:
        rounds.pop()
    previous = [json.loads(p.read_text(encoding="utf-8")) for p in rounds]
    graph = args.graph or args.runs / experiment["name"] / "02-expand" / f"graph_{len(rounds) + 1}"
    if not (graph / "nodes.parquet").exists():
        raise SystemExit(f"no graph at {graph}: run step 02 first")
    topics, skipped = choose(experiment, pq.read_table(graph / "nodes.parquet").to_pylist(), previous, args.root,
                             args.env_file)

    if not topics:
        print("no new topics: every node in reach is already a topic or was skipped")
        return
    n = len(rounds) + 1
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"round_{n}.json").write_text(json.dumps(
        {"experiment": experiment["name"], "round": n, "graph": str(graph), "created": datetime.date.today().isoformat(),
         "topics": topics, "skipped": skipped}, indent=2), encoding="utf-8")
    used = sum(t["take"] for r in previous + [{"topics": topics}] for t in r["topics"])
    print(f"wrote {folder / f'round_{n}.json'} ({len(topics)} topics, {used:,} of "
          f"{experiment['topics']['paper_budget']:,} budget papers used across rounds)")


if __name__ == "__main__":
    main()
