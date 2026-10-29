"""Download one HGNC quarterly snapshot (version = its date, e.g. 2026-10-06), plus its withdrawn-symbols file.

HGNC doesn't publish a withdrawn file every quarter, so we take the newest one on or before the snapshot's date.
"""
import json
import urllib.request
from pathlib import Path

from common import fetch

BUCKET = "https://storage.googleapis.com/storage/v1/b/public-download-files/o"
HGNC = "https://storage.googleapis.com/public-download-files/hgnc/archive/archive/quarterly/tsv"


def withdrawn_date(date: str) -> str:
    query = f"{BUCKET}?prefix=hgnc/archive/archive/quarterly/tsv/withdrawn_&fields=items(name)"
    names = [item["name"] for item in json.load(urllib.request.urlopen(query))["items"]]
    return max(n.rsplit("_", 1)[1][:-4] for n in names if n.endswith(".txt") and n.rsplit("_", 1)[1][:-4] <= date)


def download(date: str, folder: Path) -> None:
    fetch(f"{HGNC}/hgnc_complete_set_{date}.txt", folder / "hgnc_complete_set.txt")
    if not list(folder.glob("withdrawn_*.txt")):
        name = f"withdrawn_{withdrawn_date(date)}.txt"
        fetch(f"{HGNC}/{name}", folder / name)
