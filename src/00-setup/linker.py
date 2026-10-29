"""Build one experiment's linker dictionary from its sources and [linking] settings.

Each source's select.py picks its names (with id, name, synonym, name_kind, weak, types, source, source_version,
definition, depth: how general the concept is, for sources with a hierarchy, else null); the shared settings then
apply to all of them. Written to <root>/linker/<experiment name>/:
    aliases.parquet   id, synonym, name_kind, weak, needs_context, types, source, source_version, depth
    kb.jsonl          one line per concept in scispaCy's KnowledgeBase format
    settings.json     the [sources] and [linking] it was built from (and VERSION); a change rebuilds it
    nmslib_index.bin, tfidf_vectorizer.joblib, tfidf_vectors_sparse.npz, concept_aliases.json
                      the fuzzy-match index (index.py)
"""
import importlib
import json
from pathlib import Path

import duckdb

from common import sql_list

VERSION = "2"  # bump when the dictionary's columns or contents change for the same settings


def build(experiment: dict, root: Path, rebuild: bool = False) -> Path:
    out = root / "linker" / experiment["name"]
    settings = {"sources": experiment["sources"], "linking": experiment["linking"], "version": VERSION}
    if not rebuild and (out / "settings.json").exists() and json.loads((out / "settings.json").read_text()) == settings:
        print(f"have  {out}")
        return out
    print(f"build {out}", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():  # a new dictionary needs a new index too
        old.unlink()
    linking = experiment["linking"]
    parts = [importlib.import_module(f"{source}.select").select(version, root / source / version, linking[source])
             for source, version in experiment["sources"].items()]
    con = duckdb.connect()
    con.execute(f"""
        CREATE TABLE aliases AS
        SELECT id, name, synonym, name_kind, weak,
               list_contains({sql_list(linking["needs_context"])}, name_kind) AS needs_context,
               types, source, source_version, definition, depth
        FROM ({" UNION ALL ".join(parts)})
        WHERE list_has_any(types, {sql_list(linking["types"])}) AND NOT list_contains({sql_list(linking["stoplist"])}, id)
    """)
    con.execute(f"""COPY (SELECT * EXCLUDE (name, definition) FROM aliases)
                    TO '{(out / "aliases.parquet").as_posix()}' (FORMAT PARQUET)""")
    con.execute(f"""COPY (SELECT id AS concept_id, any_value(name) AS canonical_name, list(DISTINCT synonym) AS aliases,
                                 any_value(types) AS types, any_value(definition) AS definition
                          FROM aliases GROUP BY id)
                    TO '{(out / "kb.jsonl").as_posix()}' (FORMAT JSON)""")
    (out / "settings.json").write_text(json.dumps(settings, indent=2))
    n_aliases, n_concepts = con.execute("SELECT count(*), count(DISTINCT id) FROM aliases").fetchone()
    print(f"wrote {out} ({n_concepts:,} concepts, {n_aliases:,} aliases)")
    return out
