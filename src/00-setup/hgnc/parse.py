"""Parse one HGNC snapshot into Parquet tables. Approved genes only; everything about them is kept.

    concepts.parquet   see common.CONCEPT_COLUMNS; id = ncbigene:<NCBI Gene ID>
                       name_kind: symbol, name, alias_symbol, prev_symbol, alias_name, prev_name, withdrawn_symbol
    xref.parquet       id, hgnc_id, symbol, locus_group, locus_type, gene_groups, gene_group_ids,
                       ensembl, uniprot, mgi, omim, orphanet, cosmic, iuphar
"""
import csv
from pathlib import Path

from common import CONCEPT_COLUMNS, write

OUTPUTS = ["concepts.parquet", "xref.parquet"]

# Strongest first: when one string is in several columns, it keeps the first kind.
NAME_COLUMNS = ["symbol", "name", "alias_symbol", "prev_symbol", "alias_name", "prev_name"]


def split(value: str) -> list[str]:
    return [v for v in value.split("|") if v]


def parse(date: str, folder: Path) -> None:
    concepts, xref, genes = [], [], {}
    with open(folder / "hgnc_complete_set.txt", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if row["status"] != "Approved":
                continue
            gid = f"ncbigene:{row['entrez_id']}" if row["entrez_id"] else row["hgnc_id"].lower()
            genes[row["hgnc_id"]] = (gid, row["symbol"])
            names = {}
            for column in NAME_COLUMNS:
                for text in split(row[column]):
                    names.setdefault(text, column)
            concepts += [(gid, row["symbol"], text, kind, gid, "preferred", False, ["gene"], "hgnc", date)
                         for text, kind in names.items()]
            xref.append((gid, row["hgnc_id"], row["symbol"], row["locus_group"], row["locus_type"],
                         split(row["gene_group"]), split(row["gene_group_id"]), row["ensembl_gene_id"] or None,
                         split(row["uniprot_ids"]), split(row["mgd_id"]), split(row["omim_id"]),
                         row["orphanet"] or None, row["cosmic"] or None, row["iuphar"] or None))

    # Retired symbols point at the approved gene(s) they were merged into, so old papers still link.
    withdrawn = next(folder.glob("withdrawn_*.txt"))
    version = withdrawn.stem.split("_", 1)[1]
    with open(withdrawn, encoding="utf-8", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 4 or row[1] != "Merged/Split":
                continue
            for report in row[3].split(", "):
                hgnc_id, _, status = report.split("|")
                if status == "Approved" and hgnc_id in genes:
                    gid, symbol = genes[hgnc_id]
                    concepts.append((gid, symbol, row[2], "withdrawn_symbol", gid, "preferred", False,
                                     ["gene"], "hgnc", version))

    write(folder / "concepts.parquet", CONCEPT_COLUMNS, concepts)
    write(folder / "xref.parquet", ["id", "hgnc_id", "symbol", "locus_group", "locus_type", "gene_groups",
                                    "gene_group_ids", "ensembl", "uniprot", "mgi", "omim", "orphanet", "cosmic",
                                    "iuphar"], xref)
