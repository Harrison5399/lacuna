"""PubMed's part of a topic: the query for a graph node, and how many papers it matches.

    MeSH heading         "Glioma"[MeSH], or "Glioma"[MeSH:noexp] when its children are topics too, so a paper
                         isn't claimed by both
    supplementary record "Glioma of Brain, Familial"[nm]
    gene                 "EGFR protein, human"[nm] (MeSH's record for the human protein) OR its full name in the
                         title/abstract. Never the bare symbol: PubMed ignores case, so MET[tiab] matches "met".

Counts use the same query text and filters as step 04 (shared/ncbi.py), so they match what it will take.
Anything PubMed quietly dropped from a query (a heading it doesn't know) is kept as the topic's notes.
"""
from collections import defaultdict
from pathlib import Path

import duckdb

import ncbi

PAPERS = True  # its records are papers, so they count against the paper budget


def context(experiment: dict, root: Path) -> dict:
    mesh = root / "mesh" / experiment["sources"]["mesh"]
    owner = dict(duckdb.sql(f"SELECT tree_number, id FROM '{(mesh / 'tree.parquet').as_posix()}'").fetchall())
    children = defaultdict(set)
    for number, mid in owner.items():
        if "." in number and owner.get(number.rsplit(".", 1)[0]):
            children[owner[number.rsplit(".", 1)[0]]].add(mid)
    records = dict(duckdb.sql(f"SELECT id, name FROM '{(mesh / 'records.parquet').as_posix()}'").fetchall())
    gene_names = {}
    if "hgnc" in experiment["sources"]:
        hgnc = root / "hgnc" / experiment["sources"]["hgnc"]
        gene_names = dict(duckdb.sql(f"SELECT id, synonym FROM '{(hgnc / 'concepts.parquet').as_posix()}' "
                                     f"WHERE name_kind = 'name'").fetchall())
    return {"children": children, "names": records, "scr_names": {n for i, n in records.items() if i.startswith("mesh:C")},
            "gene_names": gene_names}


def spec(node: dict, ctx: dict, chosen: set[str]) -> dict | None:
    nid, name = node["id"], node["name"].replace('"', "")
    if nid.startswith("mesh:D"):
        return {"query": f'"{name}"[{"MeSH:noexp" if ctx["children"][nid] & chosen else "MeSH"}]'}
    if nid.startswith("mesh:C"):
        return {"query": f'"{name}"[nm]'}
    if nid.startswith("ncbigene:"):
        parts = [f'"{name} protein, human"[nm]'] if f"{name} protein, human" in ctx["scr_names"] else []
        if ctx["gene_names"].get(nid):
            parts.append(f'"{ctx["gene_names"][nid]}"[tiab]')
        return {"query": " OR ".join(parts)} if parts else None
    return None


def children(node: dict, ctx: dict, chosen: set[str], spec: dict) -> list[dict]:
    """The MeSH children a topic splits into, as new graph nodes. Only a query that includes its subtypes
    ([MeSH]) gets smaller by splitting; a [MeSH:noexp] one holds just papers tagged with the heading itself."""
    if not spec["query"].endswith("[MeSH]"):
        return []
    return [{"id": c, "name": ctx["names"][c], "types": node["types"], "distance": node["distance"] + 1,
             "reason": "split"} for c in sorted(ctx["children"][node["id"]] - chosen)]


def narrow(spec: dict) -> dict:
    """A too-big heading that isn't split: only the papers tagged with the heading itself."""
    return {"query": spec["query"].removesuffix("[MeSH]") + "[MeSH:noexp]"} if spec["query"].endswith("[MeSH]") else spec


def count(spec: dict, settings: dict, client: ncbi.NCBI) -> dict:
    settings = {**ncbi.DEFAULTS, **settings}
    every = client.esearch(ncbi.everything(spec["query"], settings))
    matches, open_access = int(every["count"]), int(client.esearch(ncbi.open_access(spec["query"], settings))["count"])
    oa = min(open_access, settings["max_fulltext"])
    counted = {**spec, "count": matches, "open_access": open_access,
               "take": oa + min(matches - oa, settings["max_abstract_only"])}
    return {**counted, "notes": ncbi.notes(every)} if ncbi.notes(every) else counted
