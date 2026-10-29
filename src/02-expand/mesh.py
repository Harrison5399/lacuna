"""MeSH's part of the expanded graph: the tree around each seed, plus supplementary records, see-also
links and drug classes, as an experiment's [expand.mesh] settings ask.

The tree neighbourhood of a seed is everything under its ancestor tree_up levels up, down to tree_down
levels below the seed's own level. Only levels that branch count: Glioblastoma is Astrocytoma's only
child, so one level up from it is Glioma. Distance counts tree steps: parent = 1, child = 1, sibling = 2.
The tree is walked only in tree_branches (diseases): the chemical tree groups drugs by structure
(temozolomide sits with histamine under Imidazoles), so drugs reach their neighbours through their
drug classes instead: the class (distance 1) and its other drugs (distance 2) if it has at most
max_class_size of them.
"""
from collections import defaultdict
from pathlib import Path

import duckdb


def table(folder: Path, name: str, columns: str) -> list[tuple]:
    return duckdb.sql(f"SELECT {columns} FROM '{(folder / name).as_posix()}'").fetchall()


def common(a: list[str], b: list[str]) -> int:
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


def ancestor(number: str, up: int, children: dict) -> str:
    parts = number.split(".")
    while up > 0 and len(parts) > 1:
        parts = parts[:-1]
        if children[".".join(parts)] > 1:
            up -= 1
    return ".".join(parts)


def expand(seeds: list[str], folder: Path, settings: dict, version: str) -> tuple[list[dict], list[dict]]:
    seeds = [s for s in seeds if s.startswith("mesh:")]
    numbers, owner, children = defaultdict(list), {}, defaultdict(int)
    for mid, number in table(folder, "tree.parquet", "id, tree_number"):
        numbers[mid].append(number)
        owner[number] = mid
        children[number.rsplit(".", 1)[0]] += 1
    scr_map = table(folder, "scr_map.parquet", "scr_id, descriptor_id")

    nodes, edges = {}, set()

    def add(mid: str, distance: int, reason: str) -> None:
        if mid not in nodes or distance < nodes[mid][0]:
            nodes[mid] = (distance, reason)

    # A supplementary-record seed (a rare disease, a new drug) sits in the tree through its main heading.
    anchors = {s: 0 for s in seeds if s in numbers}
    for scr, descriptor in scr_map:
        if scr in seeds:
            anchors.setdefault(descriptor, 1)
            edges.add((scr, "mapped_to", descriptor))
    for s in seeds:
        add(s, 0, "seed")

    for anchor, base in anchors.items():
        for number in numbers[anchor]:
            if not number.startswith(tuple(settings["tree_branches"])):
                continue
            parts = number.split(".")
            top = ancestor(number, settings["tree_up"], children)
            limit = len(parts) + settings["tree_down"]
            for other, mid in owner.items():
                other_parts = other.split(".")
                if (other == top or other.startswith(top + ".")) and len(other_parts) <= limit:
                    c = common(parts, other_parts)
                    reason = ("seed" if other == number else "ancestor" if c == len(other_parts)
                              else "descendant" if c == len(parts) else "relative")
                    add(mid, base + len(parts) - c + len(other_parts) - c, reason)

    for mid in list(nodes):
        for number in numbers.get(mid, []):
            parent = owner.get(number.rsplit(".", 1)[0]) if "." in number else None
            if parent in nodes:
                edges.add((mid, "is_a", parent))

    if settings["supplementary"]:
        for scr, descriptor in scr_map:
            if descriptor in nodes and scr not in nodes:
                add(scr, nodes[descriptor][0] + 1, "supplementary")
                edges.add((scr, "mapped_to", descriptor))
    if settings["see_also"]:
        for mid, related in table(folder, "see_also.parquet", "id, related_id"):
            if mid in seeds:
                add(related, 1, "see_also")
                edges.add((mid, "see_also", related))
    if settings["actions"]:
        actions = table(folder, "actions.parquet", "class_id, drug_id")
        drugs = defaultdict(list)
        for cls, drug in actions:
            drugs[cls].append(drug)
        for cls, drug in actions:
            if drug in seeds:
                add(cls, 1, "drug_class")
                edges.add((drug, "has_action", cls))
                if len(drugs[cls]) <= settings["max_class_size"]:
                    for other in drugs[cls]:
                        add(other, 2, "same_drug_class")
                        edges.add((other, "has_action", cls))

    names = {mid: (name, types) for mid, name, types in duckdb.execute(
        f"SELECT DISTINCT id, name, types FROM '{(folder / 'concepts.parquet').as_posix()}' WHERE list_contains(?, id)",
        [list(nodes)]).fetchall()}
    return ([{"id": mid, "name": names[mid][0], "types": names[mid][1], "source": "mesh", "distance": d, "reason": r}
             for mid, (d, r) in nodes.items()],
            [{"s": s, "r": r, "o": o, "layer": "curated", "source": "mesh", "source_version": version}
             for s, r, o in sorted(edges)])
