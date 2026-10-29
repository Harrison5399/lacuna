"""Claims from one sentence by cue words: for every pair of different concepts linked in it, what it says about them.

Domain-free: the words come from the lexicon a pipeline passes (biomed/lexicon.py is the biomedical one):
    cues         [(relation, {cue lemmas})], checked in order
    typed        {relation: (subject types, object types)}
    undirected   relations without a direction
    negations    negation lemmas
    pseudo       (word, next word) pairs that look like negations but aren't ("not limited")
    hedges       hedge lemmas

For a pair of mentions:
  - the relation comes from the cue words on the dependency path between them (scispaCy's parse); a pair with no
    cue is "co_occurs", kept only if cooccurrence is on
  - the subject is the mention that comes first, except that a typed relation points from its subject type to its
    object type (and becomes associated_with when the pair doesn't fit), and an undirected relation (or co_occurs)
    stores its two concepts in ID order, so "A co_occurs B" and "B co_occurs A" are one edge
  - negated: a negation word on the path or attached to it, unless it's a pseudo-negation
  - hedged: a hedge word anywhere in the sentence
"""


def path(a, b) -> list:
    """The tokens on the dependency path from a to b (both ends included)."""
    up_a, up_b = [a, *a.ancestors], [b, *b.ancestors]
    common = next((t for t in up_a if t in up_b), None)
    if common is None:
        return []
    return up_a[:up_a.index(common) + 1] + up_b[:up_b.index(common)][::-1]


def claims(sent, mentions: list[dict], lexicon: dict, cooccurrence: bool) -> list[dict]:
    hedge = next((t.text.lower() for t in sent if t.lemma_.lower() in lexicon["hedges"]), None)
    out, pairs = [], set()
    for i, a in enumerate(mentions):
        for b in mentions[i + 1:]:
            if a["id"] == b["id"] or (a["id"], b["id"]) in pairs:
                continue
            pairs.add((a["id"], b["id"]))
            between = [t for t in path(a["span"].root, b["span"].root) if t.i not in range(a["span"].start, a["span"].end)
                       and t.i not in range(b["span"].start, b["span"].end)]
            lemmas = {t.lemma_.lower() for t in between}
            relation = next((name for name, cues in lexicon["cues"] if lemmas & cues), None)
            if relation is None and not cooccurrence:
                continue
            s, o = a, b
            if relation in lexicon["typed"]:
                subjects, objects = lexicon["typed"][relation]
                if set(b["types"]) & subjects and set(a["types"]) & objects:
                    s, o = b, a
                elif not (set(a["types"]) & subjects and set(b["types"]) & objects):
                    relation = "associated_with"
            if (relation is None or relation in lexicon["undirected"]) and b["id"] < a["id"]:
                s, o = b, a
            near = between + [c for t in between for c in t.children]
            negation = next((t.text.lower() for t in near
                             if (t.dep_ == "neg" or t.lemma_.lower() in lexicon["negations"])
                             and not (t.i + 1 < len(t.doc) and (t.lower_, t.nbor().lower_) in lexicon["pseudo"])), None)
            out.append({"s": s["id"], "s_text": s["span"].text, "r": relation or "co_occurs", "o": o["id"],
                        "o_text": o["span"].text, "negated": negation is not None, "hedged": hedge is not None,
                        "cue": next((t.text for t in between if any(t.lemma_.lower() in c for _, c in lexicon["cues"])), None),
                        "negation_cue": negation, "hedge_cue": hedge})
    return out
