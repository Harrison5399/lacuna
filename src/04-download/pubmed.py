"""Download PubMed records and PMC full text for every round's topics.

Into the shared pool (every record once, whichever experiment or topic asked for it):
    <pool>/data/pubmed/<pmid>.xml.gz        PubMed records: title, abstract, MeSH headings, publication types, dates
    <pool>/data/pmc/<pmcid>.xml.gz          PMC full text (JATS XML), for the open-access papers
    <pool>/manifest_pubmed.parquet          one row per PubMed record in the pool: pmid, pmcid, doi, title,
                                            journal, date, date_source, publication_types, mesh (heading IDs),
                                            full_text. date is when the paper became public: its electronic
                                            publication date, else its issue date, else the day PubMed listed it.
Into the experiment's own folder (what it picked from the pool):
    pubmed_topics.json                      topic -> its round and query, in round order
    selections/pubmed/<topic>.json          the topic's query, filters, limits and ranked PMIDs
    papers_pubmed.json                      every paper the experiment uses: paper ("pubmed:<pmid>"), source, pmid,
                                            pmcid (or null), topics

A topic takes its open-access papers first (max_fulltext; these get full text), then other papers
(max_abstract_only; abstracts only). Its selection is saved, and searched again only when the query or
limits change: PubMed's ranking shifts daily, so the saved selection keeps an experiment stable. Records
already in the pool are never fetched again. The pool's data/ has the same layout as the Brain corpus on Slate.
"""
import gzip
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

import ncbi

XML = etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                      "Dec"], 1)}
# Where a paper's public date comes from, in order of preference.
DATES = [("epub", "MedlineCitation/Article/ArticleDate[@DateType='Electronic']"),
         ("issue", "MedlineCitation/Article/Journal/JournalIssue/PubDate"),
         ("pubmed", "PubmedData/History/PubMedPubDate[@PubStatus='pubmed']")]


def save(path: Path, payload: bytes, gz: bool = False) -> None:
    # Written under a temporary name and renamed, so an interrupted run never leaves a truncated file.
    tmp = path.with_name(path.name + ".tmp")
    with (gzip.open(tmp, "wb") if gz else open(tmp, "wb")) as f:
        f.write(payload)
    os.replace(tmp, path)


def select(client: ncbi.NCBI, name: str, query: str, settings: dict, folder: Path) -> list[str]:
    path = folder / f"{name}.json"
    want = {"query": query, "filters": ncbi.filters(settings), "max_fulltext": settings["max_fulltext"],
            "max_abstract_only": settings["max_abstract_only"]}
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if {k: saved.get(k) for k in want} == want:
            return saved["pmids"]
    oa = client.search(ncbi.open_access(query, settings), settings["max_fulltext"]) if settings["max_fulltext"] else []
    seen = set(oa)
    rest = [p for p in client.search(ncbi.everything(query, settings), settings["max_abstract_only"] + len(oa))
            if p not in seen]
    pmids = oa + rest[:settings["max_abstract_only"]]
    save(path, json.dumps({**want, "searched": date.today().isoformat(), "pmids": pmids}, indent=1).encode())
    return pmids


def pubmed_records(root):
    for art in root.findall("PubmedArticle"):
        yield art.findtext("MedlineCitation/PMID"), art


def pmc_records(root):
    for art in root.findall("article"):
        meta = art.find("front/article-meta")
        pmcid = meta.findtext("article-id[@pub-id-type='pmcid']") or "PMC" + meta.findtext("article-id[@pub-id-type='pmc']")
        yield pmcid, art


def fetch(client: ncbi.NCBI, db: str, ids: list[str], folder: Path, batch: int, records) -> int:
    """Download every record in ids not on disk yet. Returns how many batches failed (asked again next run)."""
    folder.mkdir(parents=True, exist_ok=True)
    held = set(os.listdir(folder))  # one listing: on shared storage every exists() is a round trip
    todo = [i for i in dict.fromkeys(ids) if f"{i}.xml.gz" not in held]
    print(f"{db}: {len(ids) - len(todo):,} on disk, {len(todo):,} to fetch", flush=True)

    def one(chunk: list[str]) -> str | None:
        try:
            root = etree.fromstring(client.call("efetch.fcgi", {"db": db, "retmode": "xml",
                                                                "id": ",".join(i.removeprefix("PMC") for i in chunk)}), XML)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        for i, art in records(root):
            if i in chunk:
                save(folder / f"{i}.xml.gz", etree.tostring(art, encoding="utf-8"), gz=True)
        return None

    chunks = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    failed, started = 0, time.monotonic()
    with ThreadPoolExecutor(client.workers) as pool:
        for n, future in enumerate(as_completed([pool.submit(one, c) for c in chunks]), 1):
            if error := future.result():
                failed += 1
                print(f"  batch failed, asked again next run: {error[:150]}", file=sys.stderr, flush=True)
            if n % 20 == 0 or n == len(chunks):
                done = min(n * batch, len(todo))
                print(f"  {db} {done:,}/{len(todo):,}  ~{done / max(time.monotonic() - started, 1) * 60:,.0f}/min  "
                      f"failed batches {failed}", flush=True)
    return failed


def pmcid_of(path: Path) -> str | None:
    """The paper's own PMCID (the record also lists the PMCIDs of papers it cites, so only its own ID list is read)."""
    with gzip.open(path) as f:
        art = etree.fromstring(f.read(), XML)
    return next((el.text for el in art.findall("PubmedData/ArticleIdList/ArticleId") if el.get("IdType") == "pmc"), None)


def day(el) -> str | None:
    """A PubMed date element as YYYY-MM-DD; a missing month or day counts as the first."""
    year = el.findtext("Year") or (el.findtext("MedlineDate") or "")[:4]
    if not year.isdigit():
        return None
    month = el.findtext("Month") or "1"
    month = MONTHS.get(month[:3], int(month) if month.isdigit() else 1)
    dom = el.findtext("Day") or "1"
    return f"{int(year):04d}-{month:02d}-{int(dom) if dom.isdigit() else 1:02d}"


def row(art) -> dict:
    ids = {el.get("IdType"): el.text for el in art.findall("PubmedData/ArticleIdList/ArticleId")}
    date, source = next(((d, name) for name, where in DATES
                         if (el := art.find(where)) is not None and (d := day(el))), (None, None))
    title = art.find("MedlineCitation/Article/ArticleTitle")
    return {"pmid": art.findtext("MedlineCitation/PMID"), "pmcid": ids.get("pmc"), "doi": ids.get("doi"),
            "title": "".join(title.itertext()).strip() if title is not None else None,
            "journal": art.findtext("MedlineCitation/Article/Journal/Title"), "date": date, "date_source": source,
            "publication_types": [p.text for p in art.iterfind("MedlineCitation/Article/PublicationTypeList/PublicationType")],
            "mesh": ["mesh:" + d.get("UI") for d in art.iterfind("MedlineCitation/MeshHeadingList/MeshHeading/DescriptorName")]}


def update_manifest(pool: Path) -> None:
    """Add a row for every PubMed record in the pool that isn't in the manifest yet, and refresh full_text."""
    path = pool / "manifest_pubmed.parquet"
    rows = pq.read_table(path).to_pylist() if path.exists() else []
    known = {r["pmid"] for r in rows}
    for name in sorted(os.listdir(pool / "data" / "pubmed")):
        if name.endswith(".xml.gz") and name.split(".")[0] not in known:
            with gzip.open(pool / "data" / "pubmed" / name) as f:
                rows.append(row(etree.fromstring(f.read(), XML)))
    full = set(os.listdir(pool / "data" / "pmc")) if (pool / "data" / "pmc").exists() else set()
    for r in rows:
        r["full_text"] = bool(r["pmcid"]) and f"{r['pmcid']}.xml.gz" in full
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(pa.Table.from_pylist(rows), tmp)
    os.replace(tmp, path)
    print(f"{path.name}: {len(rows):,} papers, {len(rows) - len(known):,} new", flush=True)


def download(rounds: list[dict], pool: Path, picked: Path, settings: dict, env_file: Path | None, plan: bool,
             prune: bool) -> None:
    settings = {**ncbi.DEFAULTS, **settings}
    topics = {t["name"]: {"round": r["round"], "query": t["pubmed"]["query"]}
              for r in rounds for t in r["topics"] if "pubmed" in t}
    client = ncbi.NCBI(env_file)
    if plan:
        print(f"{'topic':44} {'round':>5} {'matches':>9} {'open acc.':>9}")
        for name, t in topics.items():
            matches = int(client.esearch(ncbi.everything(t["query"], settings))["count"])
            oa = int(client.esearch(ncbi.open_access(t["query"], settings))["count"])
            print(f"{name:44} {t['round']:>5} {matches:9,} {oa:9,}", flush=True)
        return

    data = pool / "data"
    selections = picked / "selections" / "pubmed"
    selections.mkdir(parents=True, exist_ok=True)
    stale = sorted(p for p in selections.glob("*.json") if p.stem not in topics)
    if stale and not prune:
        raise SystemExit(f"{selections} holds selections for topics in no round: {', '.join(p.stem for p in stale)}. "
                         f"Rerun with --prune to delete them.")
    for p in stale:
        p.unlink()
        print(f"pruned the selection for {p.stem}")
    save(picked / "pubmed_topics.json", json.dumps(topics, indent=1).encode())

    selected = {}
    for name, t in topics.items():
        selected[name] = select(client, name, t["query"], settings, selections)
        print(f"{name:44} round {t['round']}  {len(selected[name]):6,} papers", flush=True)
    pmids = list(dict.fromkeys(p for ids in selected.values() for p in ids))
    print(f"{len(pmids):,} distinct papers across {len(topics)} topics", flush=True)

    failed = fetch(client, "pubmed", pmids, data / "pubmed", 200, pubmed_records)
    held = set(os.listdir(data / "pubmed"))
    pmcids = {p: pmcid_of(data / "pubmed" / f"{p}.xml.gz") for p in pmids if f"{p}.xml.gz" in held}
    if settings["max_fulltext"]:
        print(f"{sum(1 for c in pmcids.values() if c):,} of them have a PMC copy", flush=True)
        failed += fetch(client, "pmc", [c for c in pmcids.values() if c], data / "pmc", 50, pmc_records)
    full = set(os.listdir(data / "pmc")) if (data / "pmc").exists() else set()
    papers = [{"paper": f"pubmed:{p}", "source": "pubmed", "pmid": p, "pmcid": c if c and f"{c}.xml.gz" in full else None,
               "topics": [name for name, ids in selected.items() if p in ids]} for p, c in pmcids.items()]
    save(picked / "papers_pubmed.json", json.dumps(papers, indent=1).encode())
    print(f"{len(papers):,} papers in {picked / 'papers_pubmed.json'} "
          f"({sum(1 for p in papers if p['pmcid']):,} with full text)", flush=True)
    update_manifest(pool)
    if failed:
        print(f"{failed} batches failed; run the same command again to fetch only those", file=sys.stderr)
