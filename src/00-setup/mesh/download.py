"""Download one MeSH release: headings (desc), supplementary records (supp) and drug classes (pa)."""
import urllib.error
from pathlib import Path

from common import fetch

MESH = "https://nlmpubs.nlm.nih.gov/projects/mesh"


def download(year: str, folder: Path) -> None:
    for name in (f"desc{year}.gz", f"supp{year}.gz", f"pa{year}.xml"):
        # Past years live under /<year>/; the current year is only in MESH_FILES.
        try:
            fetch(f"{MESH}/{year}/xmlmesh/{name}", folder / name)
        except urllib.error.HTTPError:
            fetch(f"{MESH}/MESH_FILES/xmlmesh/{name}", folder / name)
