"""Read a downloaded PubMed / PMC paper into its text, one paragraph at a time, tagged with its section.

The PubMed record gives the title and abstract; a structured abstract labels each part (background, methods,
results, conclusions). The PMC article, when there is one, gives the body: each top-level section's paragraphs,
with the section named from its title by the [sections] settings ("3. Results and Discussion" -> results). Back
matter is never read: references, acknowledgments and footnotes sit outside the body, and body sections whose
titles match skip are left out. Citation markers ("[12]") and figure / table captions are dropped. Every setting
is in pubmed.toml; an experiment overrides any of them under [literature.reader.pubmed].

A reader (one per paper source, named after it) has VERSION, records(pool) (paper ID -> its manifest row) and
read(entry, pool, settings) (an entry of 04's papers_<source>.json -> its paragraphs).
"""
import gzip
import re
from pathlib import Path

import pyarrow.parquet as pq
from lxml import etree

VERSION = "2"  # bump when this changes what it reads, so cached papers are read again
XML = etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)
EMPTY_BRACKETS = re.compile(r"\s*[\[(][\s,;–-]*[\])]|(?<=[.,;:])[–-]+(?=\s)")  # what's left of "[12]", "¹–³"


def section_of(title: str | None, settings: dict) -> str | None:
    """The section name for a body section's title, or None for back matter."""
    t = re.sub(r"^[\d.\s]+", "", (title or "").lower())
    if any(s in t for s in settings["skip"]):
        return None
    return next((name for name, cues in settings["sections"].items() if any(c in t for c in cues)), settings["other"])


def drop(el) -> None:
    """Remove an element and what's inside it, keeping the text that follows it."""
    parent, prev = el.getparent(), el.getprevious()
    if prev is not None:
        prev.tail = (prev.tail or "") + (el.tail or "")
    else:
        parent.text = (parent.text or "") + (el.tail or "")
    parent.remove(el)


def clean(el) -> str:
    for ref in el.findall(".//xref[@ref-type='bibr']"):
        drop(ref)
    return EMPTY_BRACKETS.sub("", " ".join("".join(el.itertext()).split()))


def paragraphs(pubmed: Path, pmc: Path | None, settings: dict) -> list[dict]:
    """[{section, label, text}] in reading order: title, abstract parts, then the body (if there is full text)."""
    with gzip.open(pubmed) as f:
        art = etree.fromstring(f.read(), XML)
    out = []
    if (title := art.find("MedlineCitation/Article/ArticleTitle")) is not None:
        out.append({"section": "title", "label": None, "text": clean(title)})
    for part in art.iterfind("MedlineCitation/Article/Abstract/AbstractText"):
        label = (part.get("NlmCategory") or part.get("Label") or "").lower() or None
        out.append({"section": "abstract", "label": label, "text": clean(part)})
    if pmc:
        with gzip.open(pmc) as f:
            body = etree.fromstring(f.read(), XML).find("body")
        if body is not None:
            # Figures and tables sit beside paragraphs or inside them (a STAR Methods key resources table is
            # inside a <p>); either way their text isn't running text.
            for el in list(body.iter(*settings["skip_inside"])):
                drop(el)
            out += [{"section": settings["other"], "label": None, "text": clean(p)} for p in body.iterfind("p")]
            for sec in body.iterfind("sec"):
                name = section_of(sec.findtext("title"), settings)
                if name is None:
                    continue
                out += [{"section": name, "label": None, "text": clean(p)} for p in sec.iter("p")]
    return [p for p in out if p["text"]]


def records(pool: Path) -> dict[str, dict]:
    return {f"pubmed:{r['pmid']}": r for r in pq.read_table(pool / "manifest_pubmed.parquet").to_pylist()}


def read(entry: dict, pool: Path, settings: dict) -> list[dict]:
    data = pool / "data"
    return paragraphs(data / "pubmed" / f"{entry['pmid']}.xml.gz",
                      data / "pmc" / f"{entry['pmcid']}.xml.gz" if entry["pmcid"] else None, settings)
