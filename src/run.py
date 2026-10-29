"""Run Lacuna's steps in order for one experiment. Each step can also be run on its own.

Each step gets only the folders it uses: --root (00-setup's sources and linker), --runs (each experiment's
step outputs) and --corpora (where 04 downloads to).

Usage:
    python src/run.py --experiment experiments/default.toml
    python src/run.py --experiment experiments/default.toml --root /N/project/ontology_learning/L665/data/background --runs /N/project/ontology_learning/L665/runs --corpora /N/project/ontology_learning/L665/data
"""
import argparse
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
STEPS = {"00-setup": ["root"], "01-query": ["root", "runs"], "02-expand": ["root", "runs"],
         "03-topics": ["root", "runs"], "04-download": ["runs", "corpora"], "05-literature": ["root", "runs", "corpora"]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Lacuna's steps in order for one experiment.")
    parser.add_argument("--experiment", type=Path, default=SRC.parent / "experiments" / "default.toml",
                        help="Experiment file (default: experiments/default.toml)")
    parser.add_argument("--root", type=Path, default=SRC.parent / "data",
                        help="Sources and linker folder (default: lacuna/data)")
    parser.add_argument("--runs", type=Path, default=SRC.parent / "runs", help="Step outputs (default: lacuna/runs)")
    parser.add_argument("--corpora", type=Path, default=SRC.parent / "corpora",
                        help="Download folder (default: lacuna/corpora)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for step, folders in STEPS.items():
        print(f"== {step}", flush=True)
        command = [sys.executable, SRC / step / "run.py", "--experiment", args.experiment]
        for folder in folders:
            command += [f"--{folder}", getattr(args, folder)]
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
