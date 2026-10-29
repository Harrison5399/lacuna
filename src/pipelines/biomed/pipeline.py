"""The biomedical reading pipeline: how step 05 turns papers into sentences, mentions and claims.

    scispaCy (model)                       sentences, parse, entities, and the abbreviations each paper defines
    common/linking.py ([linker])           mentions -> concept IDs with the experiment's linker, the paper as context
    common/coreference.py ([coreference])  if enabled: "it", "the drug" -> the concept they stand for
    common/relations.py ([lexicon])        a claim per pair of concepts in a sentence: relation, negated, hedged
    context.py ([context])                 study type, species, date, and which sections are the paper's findings
Every setting is in defaults.toml; an experiment overrides any of them under [literature.biomed].

A pipeline is a folder in src/pipelines/ with defaults.toml (every setting) and pipeline.py, which has VERSION and
build(experiment, root, device, settings) returning an object whose read(papers) takes [(paper, paragraphs, record)]
(paper: its ID, e.g. "pubmed:3276093"; record: its row in its source's manifest) and gives, per paper, (sentences,
mentions, claims) rows. [literature] pipeline names the folder. A record field a source lacks (an arXiv paper has
no MeSH tags or publication types) counts as empty. A record's facts (a trial's coded intervention x condition, from
its reader's facts()) become claims too, with i = -1, section "registry" and method "registry", if the linker knows
both concepts. Bump VERSION when a code change alters what it produces, so cached papers are read again.
"""
import sys
from pathlib import Path

from biomed import context
from common import coreference, linking, relations

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import dictionary  # noqa: E402

VERSION = "3"


def lexicon(settings: dict) -> dict:
    """The [lexicon] settings in the shape common/relations.py reads."""
    return {"cues": [(name, set(cues)) for name, cues in settings["cues"].items()],
            "typed": {name: (set(t["subjects"]), set(t["objects"])) for name, t in settings["typed"].items()},
            "undirected": set(settings["undirected"]), "negations": set(settings["negations"]),
            "pseudo": {tuple(p.split(" ", 1)) for p in settings["pseudo"]}, "hedges": set(settings["hedges"])}


class Pipeline:
    def __init__(self, experiment: dict, root: Path, device: str, settings: dict):
        import spacy
        from scispacy.abbreviation import AbbreviationDetector  # noqa: F401  (registers the pipe)
        if device == "gpu":
            spacy.require_gpu()
        self.settings, self.lexicon = settings, lexicon(settings["lexicon"])
        self.nlp = spacy.load(settings["model"])
        self.nlp.add_pipe("abbreviation_detector")
        if settings["coreference"]["enabled"]:
            coreference.add(self.nlp, settings["coreference"], device)
        folder = root / "linker" / experiment["name"]
        self.linker = linking.Linker(folder, experiment["linking"], settings["linker"],
                                     dictionary.too_general(folder / "aliases.parquet", settings["min_depth"]))

    def parse(self, texts: list[str]) -> list:
        """A doc per text. If the batch fails (a text the model can't take, such as one over its 512-wordpiece
        limit), each text is tried alone and the ones that still fail are None, so one paragraph can't stop a run."""
        try:
            return list(self.nlp.pipe(texts, batch_size=self.settings["batch_size"]))
        except RuntimeError:
            docs = []
            for text in texts:
                try:
                    docs.append(self.nlp(text))
                except RuntimeError as exc:
                    print(f"skipped a paragraph the model can't read ({str(exc)[:100]}): {text[:80]}",
                          file=sys.stderr, flush=True)
                    docs.append(None)
            return docs

    def read(self, papers: list[tuple]) -> list[tuple[list, list, list]]:
        texts = [(n, para) for n, (_, paragraphs, _) in enumerate(papers) for para in paragraphs]
        docs = self.parse([para["text"] for _, para in texts])
        texts, docs = [t for t, d in zip(texts, docs) if d is not None], [d for d in docs if d is not None]
        ctx = self.settings["context"]
        out = []
        for n, (paper, _, record) in enumerate(papers):
            paras = [(para, doc) for (m, para), doc in zip(texts, docs) if m == n]
            about = context.paper(record, ctx)
            linked = self.linker.link_paper([doc for _, doc in paras], set(record.get("mesh") or []))
            if self.settings["coreference"]["enabled"]:
                linked = [coreference.extend(doc, found) for (_, doc), found in zip(paras, linked)]
            sentences, mentions, claims, i = [], [], [], 0
            for (para, doc), found in zip(paras, linked):
                for sent in doc.sents:
                    here = [m for m in found if sent.start <= m["span"].start < sent.end]
                    sentences.append({"paper": paper, "i": i, "section": para["section"], "label": para["label"],
                                      "text": sent.text})
                    mentions += [{"paper": paper, "i": i, "start": m["span"].start_char - sent.start_char,
                                  "end": m["span"].end_char - sent.start_char, "text": m["span"].text, "id": m["id"],
                                  "name_kind": m["name_kind"], "method": m["method"], "score": m["score"]} for m in here]
                    claims += [{"paper": paper, "i": i, "section": para["section"], "label": para["label"],
                                "primary": context.primary(para["section"], para["label"], ctx), **c,
                                "quote": sent.text, **about, "method": "biomed-rules"}
                               for c in relations.claims(sent, here, self.lexicon, self.settings["cooccurrence"])]
                    i += 1
            claims += [{"paper": paper, "i": -1, "section": "registry", "label": f["label"], "primary": False,
                        "s": f["s"], "s_text": f["s_text"], "r": f["r"], "o": f["o"], "o_text": f["o_text"],
                        "negated": False, "hedged": False, "cue": None, "negation_cue": None, "hedge_cue": None,
                        "quote": f["quote"], **about, "method": "registry"}
                       for f in record.get("facts", []) if f["s"] in self.linker.types and f["o"] in self.linker.types]
            out.append((sentences, mentions, claims))
        return out


def build(experiment: dict, root: Path, device: str, settings: dict) -> Pipeline:
    return Pipeline(experiment, root, device, settings)
