"""Read a downloaded arXiv paper into its text, one paragraph at a time, tagged with its section.

The API entry gives the title and abstract. arXiv's HTML rendering, when there is one, gives the body: each
top-level section's paragraphs (read_classes; appendices aren't by default), with the section named from its
title by the same [sections] rules as pubmed.py ("3 Data Analysis and Results" -> results), and sections whose
titles match skip left out. The bibliography sits outside the sections. Citations, figures, tables (display
equations are tables too), footnotes and numbering are dropped (drop_tags, drop_classes); inline math becomes its
LaTeX (math = "alttext") or is dropped (math = "drop"). Every setting is in arxiv.toml; an experiment overrides
any of them under [literature.reader.arxiv].
"""
import gzip
import re
from pathlib import Path

import lxml.html
import pyarrow.parquet as pq
from lxml import etree

from pubmed import section_of

VERSION = "1"  # bump when this changes what it reads, so cached papers are read again
NS = {"a": "http://www.w3.org/2005/Atom"}
SPACE_BEFORE = re.compile(r"\s+([.,;:)])")  # what a dropped citation leaves: "in 1924 ." -> "in 1924."


def text(el) -> str:
    return SPACE_BEFORE.sub(r"\1", " ".join(el.text_content().split()))


def body(page: bytes, settings: dict) -> list[dict]:
    doc = lxml.html.fromstring(page)
    for el in doc.iter("math"):
        latex = el.get("alttext") if settings["math"] == "alttext" else None
        el.tail = f" {latex or ''} {el.tail or ''}"
    for el in list(doc.iter("math", *settings["drop_tags"])):
        el.drop_tree()
    for name in settings["drop_classes"]:
        for el in doc.xpath(f"//*[contains(concat(' ', normalize-space(@class), ' '), ' {name} ')]"):
            el.drop_tree()
    out = []
    for sec in doc.iter("section"):
        if sec.get("class") not in settings["read_classes"]:
            continue
        title = sec.find("h2")
        name = section_of(text(title) if title is not None else None, settings)
        if name is not None:
            out += [{"section": name, "label": None, "text": text(p)}
                    for p in sec.xpath(".//p[contains(@class, 'ltx_p')]")]
    return out


def paragraphs(entry: Path, html: Path | None, settings: dict) -> list[dict]:
    """[{section, label, text}] in reading order: title, abstract, then the body (if there is full text)."""
    with gzip.open(entry) as f:
        atom = etree.fromstring(f.read())
    out = [{"section": "title", "label": None, "text": " ".join((atom.findtext("a:title", namespaces=NS) or "").split())},
           {"section": "abstract", "label": None, "text": " ".join((atom.findtext("a:summary", namespaces=NS) or "").split())}]
    if html:
        with gzip.open(html) as f:
            out += body(f.read(), settings)
    return [p for p in out if p["text"]]


def records(pool: Path) -> dict[str, dict]:
    return {r["paper"]: r for r in pq.read_table(pool / "manifest_arxiv.parquet").to_pylist()}


def read(entry: dict, pool: Path, settings: dict) -> list[dict]:
    data, key = pool / "data" / "arxiv", entry["arxiv_id"].replace("/", "_")
    html = data / "html" / f"{key}.html.gz"
    return paragraphs(data / "abs" / f"{key}.xml.gz", html if html.exists() else None, settings)
