"""Step 01: link the experiment's goal to the concepts it names. These are the seeds every later step grows from.

The goal is matched against the experiment's linker dictionary (link.py), so the seeds have the same IDs as
everything else. Which concepts can be seeds is set by [query]. Run 00-setup first.

Writes <runs>/<experiment name>/01-query/seeds.json: the goal, and per seed the text matched, the concept ID,
the name it matched as and its source.

Usage:
    python src/01-query/run.py --experiment experiments/default.toml
    python src/01-query/run.py --experiment experiments/default.toml --goal "temozolomide resistance in glioblastoma"
    python src/01-query/run.py --experiment experiments/default.toml --root /N/project/ontology_learning/L665/data/background --runs /N/project/ontology_learning/L665/runs
"""
import argparse
import json
import tomllib
from pathlib import Path

import link

LACUNA = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Link the experiment's goal to the concepts it names.")
    parser.add_argument("--experiment", type=Path, default=LACUNA / "experiments" / "default.toml",
                        help="Experiment file (default: experiments/default.toml)")
    parser.add_argument("--root", type=Path, default=LACUNA / "data",
                        help="Folder 00-setup saved the sources under (default: lacuna/data)")
    parser.add_argument("--runs", type=Path, default=LACUNA / "runs",
                        help="Folder each experiment's step outputs go under (default: lacuna/runs)")
    parser.add_argument("--goal", help="Use this goal instead of the experiment's")
    return parser.parse_args()


def seeds(goal: str, experiment: dict, root: Path) -> list[dict]:
    query, linking = experiment["query"], experiment["linking"]
    aliases = root / "linker" / experiment["name"] / "aliases.parquet"
    return link.link(goal, aliases, query["seed_types"], linking["needs_context"], linking["prefer"],
                     link.too_general(aliases, query["min_seed_depth"]))


def main() -> None:
    args = parse_args()
    with open(args.experiment, "rb") as f:
        experiment = tomllib.load(f)
    goal = args.goal or experiment["goal"]
    found = seeds(goal, experiment, args.root)
    out = args.runs / experiment["name"] / "01-query"
    out.mkdir(parents=True, exist_ok=True)
    (out / "seeds.json").write_text(json.dumps({"goal": goal, "seeds": found}, indent=2), encoding="utf-8")
    print(f"goal  {goal}")
    for s in found:
        print(f"seed  {s['text']!r} -> {s['id']} ({s['alias']})")
    print(f"wrote {out / 'seeds.json'} ({len(found)} seeds)")


if __name__ == "__main__":
    main()
