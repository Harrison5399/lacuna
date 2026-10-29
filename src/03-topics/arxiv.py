"""arXiv's part of a topic: a search for the node's name, inside the [download.arxiv] categories.

    MeSH heading or record   all:"<name>", in natural word order ("Glioma, Subependymal" -> "Subependymal Glioma")
    gene                     all:"<full gene name>" (arXiv's search ignores case, so never the bare symbol)
The search text and the categories filter are built by shared/arxiv_api.py, so step 04 downloads exactly what was
counted.
"""
from pathlib import Path

import arxiv_api
import duckdb

PAPERS = True  # its records are papers, so they count against the paper budget
CLIENT = arxiv_api.Client()  # arXiv's own rate limit; the NCBI client run.py passes isn't used


def readable(name: str) -> str:
    head, sep, tail = name.partition(", ")
    return f"{tail} {head}" if sep and ", " not in tail else name


def context(experiment: dict, root: Path) -> dict:
    gene_names = {}
    if "hgnc" in experiment["sources"]:
        hgnc = root / "hgnc" / experiment["sources"]["hgnc"]
        gene_names = dict(duckdb.sql(f"SELECT id, synonym FROM '{(hgnc / 'concepts.parquet').as_posix()}' "
                                     f"WHERE name_kind = 'name'").fetchall())
    return {"gene_names": gene_names}


def spec(node: dict, ctx: dict, chosen: set[str]) -> dict | None:
    name = ctx["gene_names"].get(node["id"]) if node["id"].startswith("ncbigene:") else readable(node["name"] or "")
    return {"terms": f'all:"{name.replace(chr(34), "")}"'} if name else None


def count(spec: dict, settings: dict, client) -> dict:
    found = CLIENT.count(arxiv_api.query(spec["terms"], settings))
    return {**spec, "count": found, "take": min(found, settings["max_papers"])}
