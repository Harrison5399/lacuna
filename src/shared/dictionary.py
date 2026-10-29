"""The linker's name rules, shared by 01-query (the goal) and the reading pipelines (the papers), so both give a text
the same concept IDs. A row is one dictionary name: (synonym, id, name_kind, weak, source, ...).

- A name matches a text ignoring case, unless it is a symbol or a needs_context kind (abbreviations, alias
  symbols), which must match case: "MET" is the gene, "met" is not.
- When one text matches several concepts, the strongest names win: a concept's own name beats a weak name
  (another concept in the same MeSH record) or a secondary one (an old or alias gene name: HGNC lists
  "Alzheimer disease" as an alias of APP). Then the [linking] prefer order breaks ties.
"""
from pathlib import Path

import duckdb

EXACT_CASE = {"symbol"}
SECONDARY = {"alias_symbol", "alias_name", "prev_symbol", "prev_name", "withdrawn_symbol"}


def matches(text: str, rows: list[tuple], needs_context: list[str]) -> list[tuple]:
    exact_case = EXACT_CASE | set(needs_context)
    return [r for r in rows if r[0] == text or (r[0].lower() == text.lower() and r[2] not in exact_case)]


def strongest(rows: list[tuple], prefer: list[str]) -> list[tuple]:
    strong = [r for r in rows if not r[3] and r[2] not in SECONDARY] or rows
    rank = {source: i for i, source in enumerate(prefer)}
    best = min((rank.get(r[4], len(rank)) for r in strong), default=None)
    return [r for r in strong if rank.get(r[4], len(rank)) == best]


def too_general(aliases: Path, min_depth: int) -> set[str]:
    """Concepts nearer the top of their source's hierarchy than min_depth levels ("Drug", "Neoplasms", "Cells"),
    from the depth each source's select.py gives the linker dictionary."""
    return {row[0] for row in duckdb.sql(f"SELECT DISTINCT id FROM '{aliases.as_posix()}' "
                                         f"WHERE depth < {min_depth}").fetchall()}
