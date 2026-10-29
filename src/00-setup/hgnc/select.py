"""Choose which HGNC names the linker may use, from an experiment's [linking.hgnc] settings.

    locus_groups   which genes to keep (protein-coding gene, non-coding RNA, pseudogene, other)
    withdrawn      whether retired symbols link to the gene they were merged into
"""
from pathlib import Path

from common import sql_list


def select(date: str, folder: Path, settings: dict) -> str:
    return f"""
        SELECT c.id, c.name, c.synonym, c.name_kind, false AS weak,
               c.types, c.source, c.source_version, NULL::VARCHAR AS definition, NULL::BIGINT AS depth
        FROM '{(folder / "concepts.parquet").as_posix()}' c
        JOIN '{(folder / "xref.parquet").as_posix()}' x USING (id)
        WHERE list_contains({sql_list(settings["locus_groups"])}, x.locus_group)
          {"" if settings["withdrawn"] else "AND c.name_kind <> 'withdrawn_symbol'"}
    """
