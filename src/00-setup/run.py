"""Download and parse every data source an experiment names, then build its linker dictionary.
Files and tables already on disk are skipped.

Each source is a folder here with download.py (download(version, folder)), parse.py (parse(version, folder)
and OUTPUTS) and select.py (select(version, folder, settings), the names the linker may use).
Files land in <root>/<source>/<version>/, the dictionary in <root>/linker/<experiment name>/ (see linker.py).

Usage:
    python src/00-setup/run.py --experiment experiments/default.toml
    python src/00-setup/run.py --experiment experiments/default.toml --root /N/project/ontology_learning/L665/data/background
    python src/00-setup/run.py --experiment experiments/default.toml --reparse     # rebuild tables after a parser change
    python src/00-setup/run.py --experiment experiments/default.toml --no-index    # skip the slow index build
"""
import argparse
import importlib
import os
import tomllib
from pathlib import Path

import index
import linker

LACUNA = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and parse every data source an experiment names.")
    parser.add_argument("--experiment", type=Path, default=LACUNA / "experiments" / "default.toml",
                        help="Experiment file (default: experiments/default.toml)")
    parser.add_argument("--root", type=Path, default=LACUNA / "data",
                        help="Folder the sources are saved under (default: lacuna/data; on Quartz a Slate folder)")
    parser.add_argument("--reparse", action="store_true", help="Rebuild the tables and dictionary even if they exist")
    parser.add_argument("--no-index", action="store_true",
                        help="Skip the fuzzy-match index (tens of minutes of CPU; use a Slurm job on Quartz)")
    parser.add_argument("--processes", type=int, default=os.cpu_count(),
                        help="Threads for building the index (default: all CPUs)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with open(args.experiment, "rb") as f:
        experiment = tomllib.load(f)
    for source, version in experiment["sources"].items():
        folder = args.root / source / version
        importlib.import_module(f"{source}.download").download(version, folder)
        parse = importlib.import_module(f"{source}.parse")
        if not args.reparse and all((folder / name).exists() for name in parse.OUTPUTS):
            print(f"have  {source} {version} tables")
            continue
        print(f"parse {source} {version}", flush=True)
        parse.parse(version, folder)
    folder = linker.build(experiment, args.root, rebuild=args.reparse)
    if not args.no_index:
        index.build(folder, args.processes)


if __name__ == "__main__":
    main()
