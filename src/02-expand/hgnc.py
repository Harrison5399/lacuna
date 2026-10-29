"""HGNC's part of the expanded graph: each gene seed's families and their other members, as an
experiment's [expand.hgnc] settings ask. Distance: family = 1, another member = 2.
"""
from pathlib import Path

import duckdb


def expand(seeds: list[str], folder: Path, settings: dict, version: str) -> tuple[list[dict], list[dict]]:
    genes = duckdb.sql(f"SELECT id, symbol, gene_groups, gene_group_ids FROM '{(folder / 'xref.parquet').as_posix()}'"
                       ).fetchall()
    symbol = {gid: sym for gid, sym, _, _ in genes}
    members = {}
    for gid, _, _, group_ids in genes:
        for group in group_ids:
            members.setdefault(group, []).append(gid)

    nodes, edges = {}, set()
    for gid, _, group_names, group_ids in genes:
        if gid not in seeds:
            continue
        nodes[gid] = (symbol[gid], ["gene"], 0, "seed")
        if not settings["gene_groups"]:
            continue
        for name, group in zip(group_names, group_ids):
            if len(members[group]) > settings["max_group_size"]:
                continue
            nodes.setdefault(f"hgnc_group:{group}", (name, ["gene_group"], 1, "gene_group"))
            for other in members[group]:
                if other not in nodes:
                    nodes[other] = (symbol[other], ["gene"], 2, "gene_group_member")
                edges.add((other, "member_of", f"hgnc_group:{group}"))
    return ([{"id": gid, "name": n, "types": t, "source": "hgnc", "distance": d, "reason": r}
             for gid, (n, t, d, r) in nodes.items()],
            [{"s": s, "r": r, "o": o, "layer": "curated", "source": "hgnc", "source_version": version}
             for s, r, o in sorted(edges)])
