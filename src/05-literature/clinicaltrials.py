"""Read a downloaded ClinicalTrials.gov study: its text, one paragraph at a time, and the registry's own facts.

Text, in the order of the sections setting (each a section name the claims carry):
    title         the official title, else the brief one
    summary       the brief summary
    description   the detailed description, split at its blank lines
    outcomes      each primary and secondary outcome: its measure and description
    eligibility   the inclusion and exclusion criteria; not read by default, since an exclusion list names many
                  diseases the trial isn't about ("no HIV, hepatitis B or prior malignancy")
None of these report findings, so no trial claim is primary.

Facts: each intervention x condition pair the registry coded with MeSH becomes a claim with relation
[facts] relation ("tested_for": the trial tests the intervention for the condition, which isn't evidence it
works), labelled with the trial's status ("completed", "terminated"); its quote gives the phases and why it
stopped. An umbrella trial coding more than [facts] max_conditions conditions gives none. A pipeline keeps the
facts whose concepts its linker knows.

The record gives a pipeline what a PubMed record would: the date (first posted, when the trial became public),
MeSH terms (the coded conditions and interventions, and Humans), and publication types in PubMed's words
("Clinical Trial, Phase II", "Observational Study"). Every setting is in clinicaltrials.toml; an experiment
overrides any of them under [literature.reader.clinicaltrials].
"""
import json
import re
from pathlib import Path

import pyarrow.parquet as pq

VERSION = "1"  # bump when this changes what it reads, so cached studies are read again
PHASES = {"EARLY_PHASE1": "Clinical Trial, Phase I", "PHASE1": "Clinical Trial, Phase I",
          "PHASE2": "Clinical Trial, Phase II", "PHASE3": "Clinical Trial, Phase III",
          "PHASE4": "Clinical Trial, Phase IV"}
HUMANS = "mesh:D006801"


def study(entry: dict, pool: Path) -> dict:
    return json.loads((pool / "data" / "clinicaltrials" / f"{entry['nct_id']}.json").read_text(encoding="utf-8"))


def records(pool: Path) -> dict[str, dict]:
    out = {}
    for r in pq.read_table(pool / "manifest_clinicaltrials.parquet").to_pylist():
        types = [PHASES[p] for p in r["phases"] or [] if p in PHASES] or (
            ["Clinical Trial"] if r["study_type"] == "INTERVENTIONAL" else ["Observational Study"])
        out[r["paper"]] = {**r, "date": r["first_posted"], "publication_types": types,
                           "mesh": r["condition_mesh"] + r["intervention_mesh"] + [HUMANS]}
    return out


def blocks(text: str | None) -> list[str]:
    """A registry text field as paragraphs: split at blank lines, list bullets and spacing tidied."""
    return [" ".join(re.sub(r"^\s*[*-]\s+", "", b, flags=re.M).split()) for b in re.split(r"\n\s*\n", text or "")]


def read(entry: dict, pool: Path, settings: dict) -> list[dict]:
    p = study(entry, pool)["protocolSection"]
    ident, desc = p["identificationModule"], p.get("descriptionModule", {})
    outcomes = p.get("outcomesModule", {})
    text = {"title": [ident.get("officialTitle") or ident.get("briefTitle")],
            "summary": blocks(desc.get("briefSummary")),
            "description": blocks(desc.get("detailedDescription")),
            "outcomes": [" ".join(f"{o['measure'].rstrip('.')}. {o.get('description', '')}".split())
                         for kind in ("primaryOutcomes", "secondaryOutcomes") for o in outcomes.get(kind, [])],
            "eligibility": blocks(p.get("eligibilityModule", {}).get("eligibilityCriteria"))}
    return [{"section": name, "label": None, "text": t} for name in settings["sections"] for t in text[name] if t]


def facts(entry: dict, pool: Path, settings: dict) -> list[dict]:
    if not settings["facts"]["enabled"]:
        return []
    s = study(entry, pool)
    status = s["protocolSection"].get("statusModule", {})
    derived = s.get("derivedSection", {})
    phases = ", ".join(s["protocolSection"].get("designModule", {}).get("phases", [])) or "no phase"
    label = (status.get("overallStatus") or "unknown").lower()
    stopped = f" ({status['whyStopped']})" if status.get("whyStopped") else ""
    quote = f"{entry['nct_id']}: {phases}, {label}{stopped}"
    interventions = derived.get("interventionBrowseModule", {}).get("meshes", [])
    conditions = derived.get("conditionBrowseModule", {}).get("meshes", [])
    if len(conditions) > settings["facts"]["max_conditions"]:
        return []
    return [{"s": f"mesh:{i['id']}", "s_text": i["term"], "r": settings["facts"]["relation"], "o": f"mesh:{c['id']}",
             "o_text": c["term"], "label": label, "quote": quote}
            for i in interventions for c in conditions if i["id"] != c["id"]]
