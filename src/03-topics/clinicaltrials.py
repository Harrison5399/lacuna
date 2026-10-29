"""ClinicalTrials.gov's part of a topic: a condition search for a disease, an intervention search for a drug.

The spec holds the API v2 search parameters as they are sent, so step 04 downloads exactly what was counted.
MeSH writes names inverted ("Glioma, Subependymal"); trial registrations use the natural order.
Only MeSH main headings get a trial search: the site matches words loosely, so a supplementary record's
long name ("Cerebral astrocytoma, adult") finds every astrocytoma trial.
"""
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

PAPERS = False  # trial records don't count against the paper budget
API = "https://clinicaltrials.gov/api/v2/studies"
HEADERS = {"User-Agent": "lacuna (Indiana University research)"}


def context(experiment: dict, root: Path) -> None:
    return None


def readable(name: str) -> str:
    head, sep, tail = name.partition(", ")
    return f"{tail} {head}" if sep and ", " not in tail else name


def spec(node: dict, ctx: None, chosen: set[str]) -> dict | None:
    if not node["id"].startswith("mesh:D"):
        return None
    if "disease" in node["types"]:
        return {"params": {"query.cond": readable(node["name"])}}
    if "chemical" in node["types"]:
        return {"params": {"query.intr": readable(node["name"])}}
    return None


def count(spec: dict, settings: dict, client) -> dict:
    url = f"{API}?{urlencode({**spec['params'], 'countTotal': 'true', 'pageSize': 1})}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as response:
        total = json.load(response)["totalCount"]
    return {**spec, "count": total, "take": min(total, settings["max_studies"])}
