"""Build the fuzzy-match index for one linker dictionary: TF-IDF over character 3-grams plus an nmslib HNSW index.

Same method, settings and files as scispaCy's create_tfidf_ann_index, so its CandidateGenerator loads them.
scispaCy's own function refuses to run on SciPy >= 1.11 (any Python >= 3.11) because it saves the vectors as
float16, which newer SciPy can't store sparse. Its loader converts them to float32 anyway, so we save float32.
"""
import json
import time
from pathlib import Path

import duckdb
import joblib
import nmslib
import numpy
import scipy.sparse
from sklearn.feature_extraction.text import TfidfVectorizer

FILES = ["nmslib_index.bin", "tfidf_vectorizer.joblib", "tfidf_vectors_sparse.npz", "concept_aliases.json"]


def build(folder: Path, threads: int) -> None:
    if all((folder / name).exists() for name in FILES):
        print(f"have  {folder} index")
        return
    aliases = [row[0] for row in duckdb.sql(
        f"SELECT DISTINCT synonym FROM '{(folder / 'aliases.parquet').as_posix()}' ORDER BY synonym").fetchall()]
    print(f"index {len(aliases):,} aliases with {threads} threads", flush=True)
    start = time.time()
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 3), min_df=10, dtype=numpy.float32)
    vectors = vectorizer.fit_transform(aliases)
    # nmslib crashes on all-zero vectors (aliases made only of rare 3-grams).
    keep = numpy.asarray(vectors.sum(axis=1)).ravel() != 0
    aliases, vectors = [a for a, k in zip(aliases, keep) if k], vectors[keep]
    index = nmslib.init(method="hnsw", space="cosinesimil_sparse", data_type=nmslib.DataType.SPARSE_VECTOR)
    index.addDataPointBatch(vectors)
    index.createIndex({"M": 100, "indexThreadQty": threads, "efConstruction": 2000, "post": 0}, print_progress=True)
    index.saveIndex(str(folder / "nmslib_index.bin"))
    joblib.dump(vectorizer, folder / "tfidf_vectorizer.joblib")
    scipy.sparse.save_npz(folder / "tfidf_vectors_sparse.npz", vectors)
    (folder / "concept_aliases.json").write_text(json.dumps(aliases), encoding="utf-8")
    print(f"wrote {folder} index ({len(aliases):,} aliases, {(time.time() - start) / 60:.0f} min)")
