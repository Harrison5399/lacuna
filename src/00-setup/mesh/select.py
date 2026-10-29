"""Choose which MeSH names the linker may use, from an experiment's [linking.mesh] settings.

    descriptor_classes   which kinds of main heading to keep (topical, check_tag, publication_type, geographic);
                         supplementary records are always kept
    other_concepts       names of a record's non-preferred concepts: "weak" (kept, marked weak) or "drop"

depth is the heading's shallowest level in the MeSH tree (Neoplasms = 1), so later steps can skip concepts too
general to use; supplementary records, which aren't in the tree, have none.
"""
from pathlib import Path

from common import sql_list


def select(year: str, folder: Path, settings: dict) -> str:
    drop_other = settings["other_concepts"] == "drop"
    return f"""
        SELECT c.id, c.name, c.synonym, c.name_kind, c.concept_relation <> 'preferred' AS weak,
               c.types, c.source, c.source_version, r.definition, d.depth
        FROM '{(folder / "concepts.parquet").as_posix()}' c
        JOIN '{(folder / "records.parquet").as_posix()}' r USING (id)
        LEFT JOIN (SELECT id, min(len(string_split(tree_number, '.'))) AS depth
                   FROM '{(folder / "tree.parquet").as_posix()}' GROUP BY id) d USING (id)
        WHERE (r.kind = 'supplementary' OR list_contains({sql_list(settings["descriptor_classes"])}, r.class))
          {"AND c.concept_relation = 'preferred'" if drop_other else ""}
    """
