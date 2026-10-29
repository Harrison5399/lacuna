"""Parse one MeSH release into Parquet tables. Everything is kept; experiments choose what to link to.

    concepts.parquet   see common.CONCEPT_COLUMNS (one row per synonym)
    records.parquet    id, name, kind, class, date_introduced, definition
    tree.parquet       id, tree_number
    scr_map.parquet    scr_id, descriptor_id (supplementary record -> main heading)
    actions.parquet    class_id, drug_id (pharmacological actions)
    registry.parquet   id, concept_id, number, related (CAS / UNII / EC numbers)
    see_also.parquet   id, related_id
"""
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

from common import CONCEPT_COLUMNS, write

OUTPUTS = ["concepts.parquet", "records.parquet", "tree.parquet", "scr_map.parquet", "actions.parquet",
           "registry.parquet", "see_also.parquet"]

# The first letter of a tree number is its branch.
BRANCHES = {"A": "anatomy", "B": "organism", "C": "disease", "D": "chemical", "E": "procedure",
            "F": "psychology", "G": "process", "H": "discipline", "I": "social", "J": "technology",
            "K": "humanities", "L": "information", "M": "persons", "N": "health_care",
            "V": "publication_type", "Z": "geography"}
DESCRIPTOR_CLASSES = {"1": "topical", "2": "publication_type", "3": "check_tag", "4": "geographic"}
SCR_CLASSES = {"1": "chemical", "2": "protocol", "3": "disease", "4": "organism", "5": "population", "6": "anatomy"}
RELATIONS = {"NRW": "narrower", "BRD": "broader", "REL": "related"}
NAME_KINDS = {"NON": "term", "ABB": "abbreviation", "ABX": "embedded_abbreviation", "ACR": "acronym",
              "ACX": "embedded_acronym", "TRD": "trade_name", "LAB": "lab_code", "EPO": "eponym",
              "NAM": "proper_name", "HIST": "historical"}


def records(path: Path, tag: str):
    """Yield each <tag> element of a (gzipped) MeSH XML file, freeing it afterwards."""
    with (gzip.open(path) if path.suffix == ".gz" else open(path, "rb")) as f:
        for _, el in ET.iterparse(f):
            if el.tag == tag:
                yield el
                el.clear()


def branch(number: str) -> str:
    # Mental disorders (F03) sit in the psychology branch but are diseases.
    return "disease" if number.startswith("F03") else BRANCHES[number[0]]


def introduced(rec) -> str | None:
    d = rec.find("DateIntroduced")
    return None if d is None else f"{d.findtext('Year')}-{d.findtext('Month')}-{d.findtext('Day')}"


def names(rec, mid: str, name: str, types: list[str], year: str) -> list[tuple]:
    """One row per distinct term, tagged with its concept and that concept's relation to the preferred one."""
    relation = {r.findtext("Concept2UI"): RELATIONS[r.get("RelationName")]
                for r in rec.iterfind("ConceptList/Concept/ConceptRelationList/ConceptRelation")}
    rows = {}
    for concept in rec.iterfind("ConceptList/Concept"):
        cid = concept.findtext("ConceptUI")
        rel = "preferred" if concept.get("PreferredConceptYN") == "Y" else relation.get(cid)
        for term in concept.iterfind("TermList/Term"):
            text = term.findtext("String")
            rows.setdefault(text, (mid, name, text, NAME_KINDS.get(term.get("LexicalTag"), term.get("LexicalTag")),
                                   "mesh:" + cid, rel, term.get("IsPermutedTermYN") == "Y", types, "mesh", year))
    return list(rows.values())


def registry_numbers(rec, mid: str) -> list[tuple]:
    rows = []
    for concept in rec.iterfind("ConceptList/Concept"):
        cid = "mesh:" + concept.findtext("ConceptUI")
        rows += [(mid, cid, n.text, False) for n in concept.iterfind("RegistryNumberList/RegistryNumber") if n.text != "0"]
        rows += [(mid, cid, n.text.split(" (")[0], True)
                 for n in concept.iterfind("RelatedRegistryNumberList/RelatedRegistryNumber")]
    return rows


def parse(year: str, folder: Path) -> None:
    concepts, recs, tree, scr_map, actions, registry, see_also = [], [], [], [], [], [], []
    for rec in records(folder / f"desc{year}.gz", "DescriptorRecord"):
        mid = "mesh:" + rec.findtext("DescriptorUI")
        name = rec.findtext("DescriptorName/String")
        numbers = [t.text for t in rec.iterfind("TreeNumberList/TreeNumber")]
        types = sorted({branch(n) for n in numbers})
        definition = (rec.findtext("ConceptList/Concept[@PreferredConceptYN='Y']/ScopeNote") or "").strip() or None
        recs.append((mid, name, "descriptor", DESCRIPTOR_CLASSES[rec.get("DescriptorClass")], introduced(rec), definition))
        tree += [(mid, n) for n in numbers]
        see_also += [(mid, "mesh:" + d.text) for d in rec.iterfind("SeeRelatedList/SeeRelatedDescriptor/DescriptorReferredTo/DescriptorUI")]
        concepts += names(rec, mid, name, types, year)
        registry += registry_numbers(rec, mid)
    for rec in records(folder / f"supp{year}.gz", "SupplementalRecord"):
        mid = "mesh:" + rec.findtext("SupplementalRecordUI")
        name = rec.findtext("SupplementalRecordName/String")
        types = [SCR_CLASSES[rec.get("SCRClass")]]
        recs.append((mid, name, "supplementary", types[0], introduced(rec), None))
        scr_map += [(mid, "mesh:" + d.text.lstrip("*"))
                    for d in rec.iterfind("HeadingMappedToList/HeadingMappedTo/DescriptorReferredTo/DescriptorUI")]
        concepts += names(rec, mid, name, types, year)
        registry += registry_numbers(rec, mid)
    for rec in records(folder / f"pa{year}.xml", "PharmacologicalAction"):
        cls = "mesh:" + rec.findtext("DescriptorReferredTo/DescriptorUI")
        actions += [(cls, "mesh:" + u.text) for u in rec.iterfind("PharmacologicalActionSubstanceList/Substance/RecordUI")]
    write(folder / "concepts.parquet", CONCEPT_COLUMNS, concepts)
    write(folder / "records.parquet", ["id", "name", "kind", "class", "date_introduced", "definition"], recs)
    write(folder / "tree.parquet", ["id", "tree_number"], tree)
    write(folder / "scr_map.parquet", ["scr_id", "descriptor_id"], scr_map)
    write(folder / "actions.parquet", ["class_id", "drug_id"], actions)
    write(folder / "registry.parquet", ["id", "concept_id", "number", "related"], registry)
    write(folder / "see_also.parquet", ["id", "related_id"], see_also)
