"""Link a goal sentence to concept IDs with the experiment's linker dictionary and the shared name rules
(shared/dictionary.py), so the goal gets the same IDs the papers will.

Exact names only (the goal is one sentence, so no fuzzy matching or context model is needed): every run of up
to MAX_WORDS words is looked up, and the longest non-overlapping matches win. Concepts fewer than
min_seed_depth levels deep in their source's hierarchy ("Drug", "Neoplasms") are too general to be seeds.
"""
import re
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
import dictionary  # noqa: E402

MAX_WORDS = 8
too_general = dictionary.too_general


def link(goal: str, aliases: Path, seed_types: list[str], needs_context: list[str], prefer: list[str],
         general: set[str] = frozenset()) -> list[dict]:
    words = [m.span() for m in re.finditer(r"\w[\w'-]*", goal)]
    spans = [(words[i][0], words[j][1]) for i in range(len(words)) for j in range(i, min(i + MAX_WORDS, len(words)))]
    texts = sorted({goal[a:b].lower() for a, b in spans})
    rows = [r for r in duckdb.execute(f"""
        SELECT synonym, id, name_kind, weak, source FROM '{aliases.as_posix()}'
        WHERE list_contains(?, lower(synonym)) AND list_has_any(types, ?::VARCHAR[])
    """, [texts, seed_types]).fetchall() if r[1] not in general]

    found, taken = [], set()
    for a, b in sorted(spans, key=lambda s: (s[0] - s[1], s[0])):
        if taken & set(range(a, b)):
            continue
        best = dictionary.strongest(dictionary.matches(goal[a:b], rows, needs_context), prefer)
        if best:
            taken |= set(range(a, b))
            found += [{"text": goal[a:b], "start": a, "end": b, "id": r[1], "alias": r[0], "name_kind": r[2],
                       "source": r[4]} for r in {r[1]: r for r in best}.values()]
    return sorted(found, key=lambda m: m["start"])
