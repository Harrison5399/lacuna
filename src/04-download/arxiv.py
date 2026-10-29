"""Download arXiv papers for every round's topics: their metadata, and their HTML full text where arXiv has one.

Into the shared pool (every paper once, whichever experiment or topic found it), with "/" in old-style IDs
(hep-th/9901001) written as "_":
    <pool>/data/arxiv/abs/<id>.xml.gz      the paper's Atom entry from the API: title, abstract, authors, dates,
                                           categories, version
    <pool>/data/arxiv/html/<id>.html.gz    arXiv's own HTML rendering of that version (fulltext = true); papers
                                           arXiv never rendered (most from before late 2023) get an empty
                                           <id>.missing instead, so they aren't asked for again
    <pool>/manifest_arxiv.parquet          one row per paper: paper ("arxiv:<id>"), arxiv_id, version, title,
                                           date (first version's day, the paper's public date), updated,
                                           primary_category, categories, doi, full_text
Into the experiment's own folder:
    selections/arxiv/<topic>.json          the topic's search, limit and the arXiv IDs it found
    papers_arxiv.json                      every paper the experiment uses: paper, source, arxiv_id, topics

A topic is searched again only when its search or limit changes. Every request keeps to arXiv's one per 3 seconds.
"""
import datetime
import gzip
import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

import arxiv_api
from arxiv_api import NS


def key(arxiv_id: str) -> str:
    return arxiv_id.replace("/", "_")


def save(path: Path, payload: bytes, gz: bool = False) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with (gzip.open(tmp, "wb") if gz else open(tmp, "wb")) as f:
        f.write(payload)
    os.replace(tmp, path)


def row(entry, full: set[str]) -> dict:
    base, version = arxiv_api.arxiv_id(entry)
    primary = entry.find("arxiv:primary_category", NS)
    return {"paper": f"arxiv:{base}", "arxiv_id": base, "version": version,
            "title": " ".join((entry.findtext("a:title", namespaces=NS) or "").split()),
            "date": (entry.findtext("a:published", namespaces=NS) or "")[:10] or None,
            "updated": (entry.findtext("a:updated", namespaces=NS) or "")[:10] or None,
            "primary_category": primary.get("term") if primary is not None else None,
            "categories": [c.get("term") for c in entry.findall("a:category", NS)],
            "doi": entry.findtext("arxiv:doi", namespaces=NS), "full_text": f"{key(base)}.html.gz" in full}


def update_manifest(pool: Path) -> None:
    """A row for every paper in the pool, with full_text as it is now."""
    data = pool / "data" / "arxiv"
    path = pool / "manifest_arxiv.parquet"
    full = set(os.listdir(data / "html")) if (data / "html").exists() else set()
    rows = []
    for name in sorted(os.listdir(data / "abs")):
        if name.endswith(".xml.gz"):
            with gzip.open(data / "abs" / name) as f:
                rows.append(row(etree.fromstring(f.read()), full))
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(pa.Table.from_pylist(rows), tmp)
    os.replace(tmp, path)
    print(f"{path.name}: {len(rows):,} papers, {len(full):,} HTML files and markers", flush=True)


def download(rounds: list[dict], pool: Path, picked: Path, settings: dict, env_file: Path | None, plan: bool,
             prune: bool) -> None:
    data = pool / "data" / "arxiv"
    selections = picked / "selections" / "arxiv"
    for folder in (data / "abs", data / "html", selections):
        folder.mkdir(parents=True, exist_ok=True)
    topics = [t for r in rounds for t in r["topics"] if "arxiv" in t]
    for stale in (p for p in selections.glob("*.json") if p.stem not in {t["name"] for t in topics}):
        if prune and not plan:
            stale.unlink()
            print(f"pruned the selection for {stale.stem}")
        else:
            print(f"! {stale.stem} is selected but in no round; rerun with --prune to delete its selection")

    client = arxiv_api.Client()
    held = set(os.listdir(data / "abs"))
    selected = {}
    for topic in topics:
        path = selections / f"{topic['name']}.json"
        want = {"search": arxiv_api.query(topic["arxiv"]["terms"], settings), "max_papers": settings["max_papers"]}
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if {k: saved.get(k) for k in want} == want:
            selected[topic["name"]] = saved["arxiv_ids"]
            print(f"have  {topic['name']}")
            continue
        if plan:
            print(f"would get {topic['name']}: {topic['arxiv']['take']:,} papers")
            continue
        found, new = [], 0
        for entry in client.search(want["search"], settings["max_papers"]):
            base, _ = arxiv_api.arxiv_id(entry)
            found.append(base)
            if f"{key(base)}.xml.gz" not in held:
                save(data / "abs" / f"{key(base)}.xml.gz", etree.tostring(entry, encoding="utf-8"), gz=True)
                held.add(f"{key(base)}.xml.gz")
                new += 1
        selected[topic["name"]] = found
        save(path, json.dumps({**want, "searched": datetime.date.today().isoformat(), "arxiv_ids": found},
                              indent=1).encode())
        print(f"saved {topic['name']} ({len(found):,} papers, {new:,} new to the pool)", flush=True)
    if plan:
        return

    ids = list(dict.fromkeys(i for found in selected.values() for i in found))
    if settings["fulltext"]:
        have = set(os.listdir(data / "html"))
        todo = [i for i in ids if f"{key(i)}.html.gz" not in have and f"{key(i)}.missing" not in have]
        print(f"html: {len(ids) - len(todo):,} known, {len(todo):,} to ask for", flush=True)
        for i in todo:
            with gzip.open(data / "abs" / f"{key(i)}.xml.gz") as f:
                _, version = arxiv_api.arxiv_id(etree.fromstring(f.read()))
            page = client.get(f"{arxiv_api.HTML}{i}{version}")
            if page and b"ltx_document" in page:
                save(data / "html" / f"{key(i)}.html.gz", page, gz=True)
            else:
                (data / "html" / f"{key(i)}.missing").touch()
    update_manifest(pool)
    full = set(os.listdir(data / "html"))
    papers = [{"paper": f"arxiv:{i}", "source": "arxiv", "arxiv_id": i,
               "topics": [name for name, found in selected.items() if i in found]} for i in ids]
    save(picked / "papers_arxiv.json", json.dumps(papers, indent=1).encode())
    print(f"{len(papers):,} papers in {picked / 'papers_arxiv.json'} "
          f"({sum(1 for i in ids if f'{key(i)}.html.gz' in full):,} with full text)", flush=True)
