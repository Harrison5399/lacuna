"""Step 05: read the experiment's papers into claims, and its claims into the literature graph.

Each paper (a ClinicalTrials.gov study counts as one) is read into its text by section by its source's reader
(pubmed.py, clinicaltrials.py, arxiv.py, each with its settings in a .toml of the same name), then through the
reading pipeline [literature] names, a folder in src/pipelines/ (biomed/: scispaCy, the experiment's linker,
coreference, cue-word relations with negation and hedging; settings in biomed/defaults.toml; see
biomed/pipeline.py for what a pipeline provides). A reader may also have facts(): claims its source states
outright (a trial's coded intervention x condition), which the pipeline adds to the paper's claims with i = -1.
An experiment overrides any setting under [literature.<pipeline>] and [literature.reader.<source>]. Papers are
cached in the pool, one cache per source, under <pool>/processed/<fingerprint>/, where the fingerprint stands for
everything that changes the result (the linker's [sources] and [linking], the merged pipeline settings, the source's
merged reader settings, their code versions), so each paper is read once for any experiment with the same settings. A paper is named by its source and that
source's ID ("pubmed:3276093", "clinicaltrials:NCT01752491", "arxiv:2410.18710"):
    papers/part-*.parquet      paper, n_sentences, n_mentions, n_claims (the papers done)
    sentences/part-*.parquet   paper, i, section, label, text
    mentions/part-*.parquet    paper, i, start, end, text, id, name_kind, method, score
    claims/part-*.parquet      paper, i (-1 for a fact), section, label, primary, s, s_text, r, o, o_text, negated,
                               hedged, cue, negation_cue, hedge_cue, quote, date, study_type, species, method
    settings.json              what the fingerprint stands for
Then the experiment's literature graph, from its own papers only (04's papers_<source>.json), and in evaluation
runs only claims dated before before_date: <runs>/<experiment>/05-literature/round_<n>/ (n = the topic rounds so
far)
    edges.parquet   s, r, o, layer ("literature"), n_papers, n_negated, n_hedged, n_primary (papers with such a
                    claim), n_claims, first_seen, last_seen, papers
    nodes.parquet   id, name, types, n_papers (papers mentioning it or naming it in a fact), n_mentions,
                    share (n_papers / the papers)
02-expand merges it into the next round's experiment graph.

Usage:
    python src/05-literature/run.py --experiment experiments/default.toml
    python src/05-literature/run.py --experiment experiments/default.toml --device gpu
    python src/05-literature/run.py --experiment experiments/default.toml --task $SLURM_ARRAY_TASK_ID --tasks 16   # one slice, no graph
    python src/05-literature/run.py --experiment experiments/default.toml --graph-only                           # after the slices
"""
import argparse
import hashlib
import importlib
import json
import sys
import time
import tomllib
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

LACUNA = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(LACUNA / "src" / "pipelines"), str(LACUNA / "src" / "shared")]
import settings as defaults  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read the experiment's papers into claims and its literature graph.")
    parser.add_argument("--experiment", type=Path, default=LACUNA / "experiments" / "default.toml",
                        help="Experiment file (default: experiments/default.toml)")
    parser.add_argument("--root", type=Path, default=LACUNA / "data",
                        help="Folder 00-setup saved the sources and linker under (default: lacuna/data)")
    parser.add_argument("--runs", type=Path, default=LACUNA / "runs",
                        help="Folder each experiment's step outputs go under (default: lacuna/runs)")
    parser.add_argument("--corpora", type=Path, default=LACUNA / "corpora",
                        help="Folder the pool folder is in (default: lacuna/corpora; on Quartz .../L665/data)")
    parser.add_argument("--device", choices=["cpu", "gpu"], default="cpu", help="Where the pipeline runs (default: cpu)")
    parser.add_argument("--task", type=int, default=0, help="This slice of the papers (a Slurm array task ID)")
    parser.add_argument("--tasks", type=int, default=1, help="How many slices the papers are split into")
    parser.add_argument("--graph-only", action="store_true", help="Only build the literature graph from what's read")
    parser.add_argument("--chunk", type=int, default=50, help="Papers read and written to the cache at a time (default: 50)")
    return parser.parse_args()


def write(folder: Path, stamp: str, rows: list[dict]) -> None:
    if rows:
        folder.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), folder / f"part-{stamp}.parquet")


def clear_unfinished(cache: Path, task: int) -> None:
    """Delete what an interrupted run of this task wrote for a chunk whose papers part (written last) never was,
    so those papers are read again once, not counted twice. Other tasks' parts are left alone: they may be running."""
    finished = {p.name for p in (cache / "papers").glob(f"part-*-{task}.parquet")}
    for name in ["sentences", "mentions", "claims"]:
        for part in (cache / name).glob(f"part-*-{task}.parquet"):
            if part.name not in finished:
                part.unlink()
                print(f"deleted {name}/{part.name}, left by an interrupted run", flush=True)


def record(entry: dict, pool: Path, row: dict, reader, settings: dict) -> dict:
    """The paper's manifest row, with the facts its source states outright if its reader has facts()."""
    return {**row, "facts": reader.facts(entry, pool, settings)} if hasattr(reader, "facts") else row


def read(todo: list[dict], pool: Path, cache: Path, reader, reading: dict, pipeline, size: int, task: int) -> None:
    """Read one source's papers into its cache, a chunk at a time."""
    records = reader.records(pool)
    for start in range(0, len(todo), size):
        chunk, began = todo[start:start + size], time.time()
        batch = [(p["paper"], reader.read(p, pool, reading), record(p, pool, records[p["paper"]], reader, reading))
                 for p in chunk]
        results = pipeline.read(batch)
        stamp = f"{time.time_ns()}-{task}"
        for n, name in enumerate(["sentences", "mentions", "claims"]):
            write(cache / name, stamp, [row for result in results for row in result[n]])
        write(cache / "papers", stamp, [{"paper": paper, "n_sentences": len(s), "n_mentions": len(m), "n_claims": len(c)}
                                        for (paper, _, _), (s, m, c) in zip(batch, results)])
        print(f"read {start + len(chunk):,}/{len(todo):,} papers ({sum(len(r[0]) for r in results):,} sentences, "
              f"{sum(len(r[1]) for r in results):,} mentions, {sum(len(r[2]) for r in results):,} claims in "
              f"{time.time() - began:.0f} s)", flush=True)


def parts(caches: list[Path], name: str) -> str | None:
    """A read_parquet() of one table across the sources' caches, or None if none has it yet."""
    globs = [f"'{(c / name).as_posix()}/*.parquet'" for c in caches if list((c / name).glob("*.parquet"))]
    return f"read_parquet([{', '.join(globs)}])" if globs else None


def graph(papers: list[str], caches: list[Path], linker_folder: Path, out: Path, before: str | None) -> None:
    con = duckdb.connect()
    con.execute("CREATE TABLE picked AS SELECT unnest(?::VARCHAR[]) AS paper", [papers])
    when = f"AND c.date < '{before}'" if before else ""
    out.mkdir(parents=True, exist_ok=True)
    claims, mentions = parts(caches, "claims"), parts(caches, "mentions")
    if claims:
        con.execute(f"""COPY (
            SELECT s, r, o, 'literature' AS layer, count(DISTINCT paper) AS n_papers,
                   count(DISTINCT paper) FILTER (WHERE negated) AS n_negated,
                   count(DISTINCT paper) FILTER (WHERE hedged) AS n_hedged,
                   count(DISTINCT paper) FILTER (WHERE "primary") AS n_primary, count(*) AS n_claims,
                   min(date) AS first_seen, max(date) AS last_seen, list(DISTINCT paper) AS papers
            FROM {claims} c JOIN picked USING (paper)
            WHERE true {when} GROUP BY s, r, o ORDER BY n_papers DESC
        ) TO '{(out / "edges.parquet").as_posix()}' (FORMAT PARQUET)""")
    # A node is every concept mentioned, and every concept a source's facts name without a mention (a trial's
    # coded intervention); n_mentions counts only mentions.
    found = [f"SELECT paper, id, true AS mention FROM {mentions}"] if mentions else []
    found += [f"SELECT paper, unnest([s, o]) AS id, false AS mention FROM {claims} WHERE i = -1"] if claims else []
    if found:
        con.execute(f"""COPY (
            SELECT m.id, k.canonical_name AS name, k.types, count(DISTINCT m.paper) AS n_papers,
                   count(*) FILTER (WHERE mention) AS n_mentions, count(DISTINCT m.paper) / {len(papers)} AS share
            FROM ({" UNION ALL ".join(found)}) m JOIN picked USING (paper)
            LEFT JOIN read_json('{(linker_folder / "kb.jsonl").as_posix()}') k ON k.concept_id = m.id
            GROUP BY ALL ORDER BY n_papers DESC
        ) TO '{(out / "nodes.parquet").as_posix()}' (FORMAT PARQUET)""")
    def count(name: str) -> int:
        return con.execute(f"SELECT count(*) FROM '{(out / name).as_posix()}'").fetchone()[0] if (out / name).exists() else 0

    print(f"wrote {out} ({count('nodes.parquet'):,} nodes, {count('edges.parquet'):,} edges from {len(papers):,} papers)")


def main() -> None:
    args = parse_args()
    with open(args.experiment, "rb") as f:
        experiment = tomllib.load(f)
    run = args.runs / experiment["name"]
    pool = args.corpora / experiment["download"]["pool"]
    linker_folder = args.root / "linker" / experiment["name"]
    name = experiment["literature"]["pipeline"]
    module = importlib.import_module(f"{name}.pipeline")
    piping = defaults.merged(LACUNA / "src" / "pipelines" / name / "defaults.toml",
                             experiment["literature"].get(name, {}), f"literature.{name}.")
    files = sorted((run / "04-download").glob("papers_*.json"))
    if not files:
        raise SystemExit(f"no papers_<source>.json in {run / '04-download'}: run step 04 first")
    picked = {f.stem.removeprefix("papers_"): json.loads(f.read_text(encoding="utf-8")) for f in files}
    linker = json.loads((linker_folder / "settings.json").read_text())

    # One cache per source, so experiments with different sources still share what they have in common.
    readers, reading, caches = {}, {}, {}
    for source in picked:
        readers[source] = importlib.import_module(source)
        reading[source] = defaults.merged(Path(readers[source].__file__).with_suffix(".toml"),
                                          experiment["literature"].get("reader", {}).get(source, {}),
                                          f"literature.reader.{source}.")
        settings = {"linker": linker, "pipeline": name, "pipeline_settings": piping, "pipeline_version": module.VERSION,
                    "reader": source, "reader_settings": reading[source], "reader_version": readers[source].VERSION}
        caches[source] = pool / "processed" / hashlib.sha1(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:12]
        caches[source].mkdir(parents=True, exist_ok=True)
        (caches[source] / "settings.json").write_text(json.dumps(settings, indent=2))

    if not args.graph_only:
        pipeline = None
        for source, papers in picked.items():
            cache = caches[source]
            clear_unfinished(cache, args.task)
            done = {r[0] for r in duckdb.sql(f"SELECT paper FROM {parts([cache], 'papers')}").fetchall()} \
                if parts([cache], "papers") else set()
            todo = [p for p in papers if p["paper"] not in done][args.task::args.tasks]
            print(f"{experiment['name']} {source}: {len(papers):,} papers, "
                  f"{len(papers) - len(done & {p['paper'] for p in papers}):,} to read, this task {len(todo):,}, "
                  f"pipeline {name} -> {cache}", flush=True)
            if todo:
                pipeline = pipeline or module.build(experiment, args.root, args.device, piping)
                read(todo, pool, cache, readers[source], reading[source], pipeline, args.chunk, args.task)
        if args.tasks > 1:
            return

    rounds = len(list((run / "03-topics").glob("round_*.json")))
    before = experiment.get("before_date")
    graph([p["paper"] for papers in picked.values() for p in papers], list(caches.values()), linker_folder,
          run / "05-literature" / f"round_{rounds}", before.isoformat() if before else None)


if __name__ == "__main__":
    main()
