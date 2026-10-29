"""Helpers shared by the source download and parse scripts."""
import shutil
import urllib.request
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# Every source's concepts.parquet has these columns, one row per (id, synonym):
#   name_kind         what kind of name the synonym is (term, abbreviation, trade_name, symbol, prev_symbol, ...)
#   concept_id        the finer concept inside the record the synonym belongs to
#   concept_relation  how that concept relates to the record's main one (preferred, narrower, broader, related)
#   permuted          a variant MeSH generated from another term (plural, reordered): "Glioblastomas", "Grade IV Astrocytoma"
CONCEPT_COLUMNS = ["id", "name", "synonym", "name_kind", "concept_id", "concept_relation", "permuted",
                   "types", "source", "source_version"]


def fetch(url: str, path: Path) -> None:
    """Save url to path, unless path is already there."""
    if path.exists():
        print(f"have  {path}")
        return
    print(f"get   {url}", flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    with urllib.request.urlopen(url) as response, open(part, "wb") as out:
        shutil.copyfileobj(response, out)
    part.rename(path)
    print(f"saved {path} ({path.stat().st_size / 1e6:.0f} MB)")


def sql_list(values: list[str]) -> str:
    """A DuckDB list literal, ['a', 'b']."""
    return "[" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + "]::VARCHAR[]"


def write(path: Path, columns: list[str], rows: list[tuple]) -> None:
    pq.write_table(pa.table(dict(zip(columns, map(list, zip(*rows))))), path)
    print(f"wrote {path} ({len(rows):,} rows)")
