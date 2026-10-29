"""Link a paper's mentions to concept IDs: the experiment's linker dictionary, with the paper as context.

Mentions are every dictionary name found in the text (longest first, up to max_words; a one-word name only as one
of single_word_pos, so "leads to" isn't the metal) plus every scispaCy entity that isn't one. Each is linked:
  1. exactly, with the shared name rules (shared/dictionary.py)
  2. otherwise, if the linker has a fuzzy-match index (00-setup), to its best of fuzzy_candidates candidates at or
     above [linking] min_score
  3. a name that needs context (an abbreviation, an alias symbol) counts only if the paper backs it: it defines
     the abbreviation, its own index terms (MeSH headings) include the concept, or it names the concept another way
  4. of several concepts, the one in the paper's index terms, then one already named elsewhere in the paper
     (one sense per paper), then the [linking] prefer order
An abbreviation the paper defines ("temozolomide (TMZ)") links wherever it appears, to its long form's concept.
Concepts too general to be useful ("Cells", "Neoplasms") are left out by the caller (general).

Settings (a pipeline's [linker] table): max_words, single_word_pos, fuzzy_candidates.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import dictionary  # noqa: E402

INDEX = ["nmslib_index.bin", "tfidf_vectorizer.joblib", "tfidf_vectors_sparse.npz", "concept_aliases.json"]


class Linker:
    def __init__(self, folder: Path, linking: dict, settings: dict, general: set[str]):
        self.linking, self.needs_context = linking, set(linking["needs_context"])
        self.settings, self.single_word_pos = settings, set(settings["single_word_pos"])
        self.names = defaultdict(list)
        table = pq.read_table(folder / "aliases.parquet",
                              columns=["synonym", "id", "name_kind", "weak", "source", "types"]).to_pylist()
        for r in table:
            if r["id"] not in general:
                self.names[r["synonym"].lower()].append((r["synonym"], r["id"], r["name_kind"], r["weak"], r["source"],
                                                         r["types"]))
        self.types = {row[1]: row[5] for rows in self.names.values() for row in rows}
        self.source = {row[1]: row[4] for rows in self.names.values() for row in rows}
        self.fuzzy = None
        if all((folder / name).exists() for name in INDEX):
            from scispacy.candidate_generation import CandidateGenerator, LinkerPaths
            from scispacy.linking_utils import Entity, KnowledgeBase
            paths = LinkerPaths.from_directory(folder)
            with open(folder / "kb.jsonl", encoding="utf-8") as f:
                kb = KnowledgeBase([Entity(**json.loads(line)) for line in f])
            self.fuzzy = CandidateGenerator(paths.get_ann_index(), paths.get_tfidf_vectorizer(),
                                            paths.get_concept_aliases(), kb)

    def exact(self, text: str) -> list[tuple]:
        return dictionary.matches(text, self.names.get(text.lower(), []), self.linking["needs_context"])

    def spans(self, doc, short_forms: dict) -> list[tuple]:
        """(span, rows): dictionary names and the paper's short forms, longest first and non-overlapping, then the
        other scispaCy entities."""
        found, taken, i = [], set(), 0
        while i < len(doc):
            for j in range(min(len(doc), i + self.settings["max_words"]), i, -1):
                span = doc[i:j]
                if j - i == 1 and span[0].pos_ not in self.single_word_pos and span.text not in short_forms:
                    continue
                rows = self.exact(span.text)
                if rows or span.text in short_forms:
                    found.append((span, rows))
                    taken |= set(range(i, j))
                    i = j - 1
                    break
            i += 1
        found += [(ent, []) for ent in doc.ents if not taken & set(range(ent.start, ent.end))]
        return found

    def link_paper(self, docs: list, mesh: set[str]) -> list[list[dict]]:
        """Per doc (paragraph), its linked mentions: {span, id, alias, name_kind, method, score}."""
        long_forms = {}
        for doc in docs:
            for short in doc._.abbreviations:
                long_forms.setdefault(short.text, short._.long_form.text)
        found = [self.spans(doc, long_forms) for doc in docs]

        unmatched = sorted({span.text for spans in found for span, rows in spans if not rows
                            and span.text not in long_forms})
        fuzzy = {}
        if self.fuzzy and unmatched:
            for text, candidates in zip(unmatched, self.fuzzy(unmatched, self.settings["fuzzy_candidates"])):
                best = max(((max(c.similarities), c.concept_id, c.aliases[0]) for c in candidates), default=None)
                if best and best[0] >= self.linking["min_score"]:
                    fuzzy[text] = best

        def candidates(span, rows):
            if span.text in long_forms:
                return [(r, "abbreviation", 1.0) for r in self.exact(long_forms[span.text])], True
            if rows:
                return [(r, "exact", 0.9 if r[3] else 1.0) for r in rows], False
            if span.text in fuzzy:
                score, cid, alias = fuzzy[span.text]
                return [((alias, cid, "fuzzy", False, self.source.get(cid), self.types.get(cid, [])), "fuzzy", score)], False
            return [], False

        resolved = [[(span, *candidates(span, rows)) for span, rows in spans] for spans in found]
        named = {r[1] for spans in resolved for _, cands, _ in spans for r, _, _ in cands
                 if r[2] not in self.needs_context}
        context = mesh | named
        seen, out = set(), []
        for spans in resolved:
            mentions = []
            for span, cands, defined in spans:
                cands = [c for c in cands if defined or c[0][2] not in self.needs_context or c[0][1] in context]
                if not cands:
                    continue
                rows = dictionary.strongest([c[0] for c in cands], self.linking["prefer"])
                row = (next((r for r in rows if r[1] in mesh), None) or next((r for r in rows if r[1] in seen), None)
                       or rows[0])
                method, score = next((m, s) for r, m, s in cands if r is row)
                seen.add(row[1])
                mentions.append({"span": span, "id": row[1], "alias": row[0], "name_kind": row[2], "method": method,
                                 "score": score, "types": self.types.get(row[1], [])})
            out.append(mentions)
        return out
