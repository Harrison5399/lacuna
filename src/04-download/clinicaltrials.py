"""Download ClinicalTrials.gov studies for every round's topics.

Into the shared pool (every study once, whichever experiment or topic found it):
    <pool>/data/clinicaltrials/<nct_id>.json   the raw API v2 study record, as first downloaded
    <pool>/manifest_clinicaltrials.parquet     one row per study: paper ("clinicaltrials:<nct_id>"), nct_id, title,
                                               study_type, status, why_stopped, phases, has_results, conditions,
                                               condition_mesh, interventions, intervention_mesh,
                                               first_posted (the trial's public date), start_date
Into the experiment's own folder:
    selections/clinicaltrials/<topic>.json     the topic's search, limit and the NCT IDs it found
    papers_clinicaltrials.json                 every study the experiment uses, for step 05: paper, source, nct_id,
                                               topics

A topic is searched again only when its search or limit changes. Studies don't count against the paper budget
(max_studies limits them), but step 05 reads them like papers.
"""
import datetime
import json
import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pyarrow as pa
import pyarrow.parquet as pq

API = "https://clinicaltrials.gov/api/v2/studies"
HEADERS = {"User-Agent": "lacuna (Indiana University research)"}
COLUMNS = ["paper", "nct_id", "title", "study_type", "status", "why_stopped", "phases", "has_results", "conditions",
           "condition_mesh", "interventions", "intervention_mesh", "first_posted", "start_date"]


def page(params: dict) -> dict:
    request = urllib.request.Request(f"{API}?{urlencode(params)}", headers=HEADERS)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def save(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def row(study: dict) -> dict:
    p, derived = study["protocolSection"], study.get("derivedSection", {})
    status, nct = p.get("statusModule", {}), p["identificationModule"]["nctId"]
    return {"paper": f"clinicaltrials:{nct}", "nct_id": nct, "title": p["identificationModule"].get("briefTitle"),
            "study_type": p.get("designModule", {}).get("studyType"),
            "status": status.get("overallStatus"), "why_stopped": status.get("whyStopped"),
            "phases": p.get("designModule", {}).get("phases", []), "has_results": bool(study.get("hasResults")),
            "conditions": p.get("conditionsModule", {}).get("conditions", []),
            "condition_mesh": ["mesh:" + m["id"] for m in derived.get("conditionBrowseModule", {}).get("meshes", [])],
            "interventions": [i["name"] for i in p.get("armsInterventionsModule", {}).get("interventions", [])],
            "intervention_mesh": ["mesh:" + m["id"] for m in derived.get("interventionBrowseModule", {}).get("meshes", [])],
            "first_posted": status.get("studyFirstPostDateStruct", {}).get("date"),
            "start_date": status.get("startDateStruct", {}).get("date")}


def update_manifest(pool: Path) -> None:
    """Add a row for every study in the pool that isn't in the manifest yet (all of them again if the columns
    changed)."""
    path = pool / "manifest_clinicaltrials.parquet"
    rows = pq.read_table(path).to_pylist() if path.exists() else []
    if rows and set(rows[0]) != set(COLUMNS):
        rows = []
    known = {r["nct_id"] for r in rows}
    for study in sorted((pool / "data" / "clinicaltrials").glob("*.json")):
        if study.stem not in known:
            rows.append(row(json.loads(study.read_text(encoding="utf-8"))))
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(pa.Table.from_pylist(rows), tmp)
    os.replace(tmp, path)
    print(f"{path.name}: {len(rows):,} studies, {len(rows) - len(known):,} new", flush=True)


def download(rounds: list[dict], pool: Path, picked: Path, settings: dict, env_file: Path | None, plan: bool,
             prune: bool) -> None:
    studies = pool / "data" / "clinicaltrials"
    selections = picked / "selections" / "clinicaltrials"
    studies.mkdir(parents=True, exist_ok=True)
    selections.mkdir(parents=True, exist_ok=True)
    topics = [t for r in rounds for t in r["topics"] if "clinicaltrials" in t]
    for stale in (p for p in selections.glob("*.json") if p.stem not in {t["name"] for t in topics}):
        if prune and not plan:
            stale.unlink()
            print(f"pruned the selection for {stale.stem}")
        else:
            print(f"! {stale.stem} is selected but in no round; rerun with --prune to delete its selection")

    held = {p.stem for p in studies.glob("*.json")}
    selected = {}
    for topic in topics:
        path = selections / f"{topic['name']}.json"
        want = {"params": topic["clinicaltrials"]["params"], "max_studies": settings["max_studies"]}
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if {k: saved.get(k) for k in want} == want:
            selected[topic["name"]] = saved["nct_ids"]
            print(f"have  {topic['name']}")
            continue
        if plan:
            print(f"would get {topic['name']}: {topic['clinicaltrials']['take']:,} studies")
            continue
        found, new, token = [], 0, None
        while len(found) < settings["max_studies"]:
            params = {**want["params"], "pageSize": min(1000, settings["max_studies"] - len(found))}
            result = page({**params, "pageToken": token} if token else params)
            for study in result.get("studies", []):
                nct = study["protocolSection"]["identificationModule"]["nctId"]
                found.append(nct)
                if nct not in held:
                    save(studies / f"{nct}.json", study)
                    held.add(nct)
                    new += 1
            token = result.get("nextPageToken")
            if not token:
                break
            time.sleep(0.5)
        selected[topic["name"]] = found
        save(path, {**want, "searched": datetime.date.today().isoformat(), "nct_ids": found})
        print(f"saved {topic['name']} ({len(found):,} studies, {new:,} new to the pool)", flush=True)
    if plan:
        return
    update_manifest(pool)
    ids = list(dict.fromkeys(i for found in selected.values() for i in found))
    entries = [{"paper": f"clinicaltrials:{i}", "source": "clinicaltrials", "nct_id": i,
                "topics": [name for name, found in selected.items() if i in found]} for i in ids]
    save(picked / "papers_clinicaltrials.json", entries)
    print(f"{len(entries):,} studies in {picked / 'papers_clinicaltrials.json'}", flush=True)
