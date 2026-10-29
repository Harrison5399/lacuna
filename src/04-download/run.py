"""Step 04: download every round of topics the experiment has chosen (03), from each source in [download].

Records go into one shared pool, <corpora>/<[download] pool>/data/, each stored once under its own ID, so
experiments that share a pool never download a paper or trial twice. What this experiment picked from the
pool (its topics, and the PMIDs / trial IDs each one took) goes to <runs>/<experiment>/04-download/:
later steps read only those, so each experiment uses just its own papers. --prune deletes the experiment's
own selections of dropped topics, never pool records another experiment may use.

Each source is a module here with download(rounds, pool, picked, settings, env_file, plan, prune):
    pubmed.py           PubMed records and PMC full text (JATS XML)
    clinicaltrials.py   ClinicalTrials.gov study records
    arxiv.py            arXiv papers and their HTML full text (experiments/test_arxiv.toml)
A paper source also writes papers_<source>.json, the papers step 05 reads, each named "<source>:<its ID>".
Downloading is resumable: rerunning fetches only what is missing.

Usage:
    python src/04-download/run.py --experiment experiments/default.toml --plan     # counts only, downloads nothing
    python src/04-download/run.py --experiment experiments/default.toml
    python src/04-download/run.py --experiment experiments/default.toml --prune    # after 03 --redo dropped topics
    python src/04-download/run.py --experiment experiments/default.toml --runs /N/project/ontology_learning/L665/runs --corpora /N/project/ontology_learning/L665/data --env-file ~/.ncbi.env
"""
import argparse
import importlib
import json
import sys
import tomllib
from pathlib import Path

LACUNA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LACUNA / "src" / "shared"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download every round of topics the experiment has chosen.")
    parser.add_argument("--experiment", type=Path, default=LACUNA / "experiments" / "default.toml",
                        help="Experiment file (default: experiments/default.toml)")
    parser.add_argument("--runs", type=Path, default=LACUNA / "runs",
                        help="Folder each experiment's step outputs go under (default: lacuna/runs)")
    parser.add_argument("--corpora", type=Path, default=LACUNA / "corpora",
                        help="Folder the pool folder is in (default: lacuna/corpora; on Quartz .../L665/data)")
    parser.add_argument("--env-file", type=Path,
                        help="File with NCBI_EMAIL= and NCBI_API_KEY= (default: the nearest .env.local, else ~/.ncbi.env)")
    parser.add_argument("--plan", action="store_true", help="Only count what would be downloaded")
    parser.add_argument("--prune", action="store_true",
                        help="Delete this experiment's selections of topics no longer in any round (e.g. after 03 --redo)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with open(args.experiment, "rb") as f:
        experiment = tomllib.load(f)
    download = experiment["download"]
    topics = args.runs / experiment["name"] / "03-topics"
    rounds = [json.loads(p.read_text(encoding="utf-8"))
              for p in sorted(topics.glob("round_*.json"), key=lambda p: int(p.stem.split("_")[1]))]
    if not rounds:
        raise SystemExit(f"no rounds in {topics}: run step 03 first")
    pool = args.corpora / download["pool"]
    picked = args.runs / experiment["name"] / "04-download"
    print(f"{experiment['name']}: {len(rounds)} rounds, {sum(len(r['topics']) for r in rounds)} topics -> pool {pool}")
    for source in download["sources"]:
        print(f"== {source}", flush=True)
        importlib.import_module(source).download(rounds, pool, picked, download[source], args.env_file, args.plan,
                                                 args.prune)


if __name__ == "__main__":
    main()
