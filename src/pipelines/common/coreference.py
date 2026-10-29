"""Coreference with fastcoref: link "it", "the drug", "these tumours" to the concept they stand for.

fastcoref runs as a spaCy component on any pipeline (it reads the text itself and only borrows spaCy's tokens), and
puts each document's clusters (character spans that refer to the same thing) in doc._.coref_clusters. A member
stands for a concept when a linked mention covers the member's head word: "Temozolomide" does, but "40 patients
with recurrent glioblastoma" stands for the patients, not for glioblastoma. A cluster whose members stand for
exactly one concept passes it on to its other, unlinked members, with method "coreference"; a cluster mixing
several concepts is left alone. Clusters stay inside one document (one paragraph, as the pipeline reads them).

Settings (a pipeline's [coreference] table): model (fastcoref's architecture, "FCoref" or "LingMessCoref") and
model_path (its Hugging Face model).
"""


def add(nlp, settings: dict, device: str) -> None:
    """Put fastcoref at the end of the spaCy pipeline."""
    from fastcoref import spacy_component  # noqa: F401  (registers the "fastcoref" component)
    nlp.add_pipe("fastcoref", config={"model_architecture": settings["model"], "model_path": settings["model_path"],
                                      "device": "cuda" if device == "gpu" else "cpu"})


def extend(doc, mentions: list[dict]) -> list[dict]:
    """The mentions plus the cluster members their concept passes to."""
    added = []
    for cluster in doc._.coref_clusters:
        spans = [doc.char_span(start, end, alignment_mode="expand") for start, end in cluster]
        spans = [s for s in spans if s is not None]
        linked = [m for m in mentions if any(m["span"].start <= s.root.i < m["span"].end for s in spans)]
        if len({m["id"] for m in linked}) != 1:
            continue
        source = linked[0]
        for span in spans:
            if not any(m["span"].start < span.end and span.start < m["span"].end for m in mentions + added):
                added.append({**source, "span": span, "method": "coreference", "score": source["score"]})
    return sorted(mentions + added, key=lambda m: m["span"].start)
