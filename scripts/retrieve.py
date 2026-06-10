"""Semantic search over the embedded corpus.

Exposes retrieve(query, k) which embeds the query with the same MiniLM model
used at index time and returns the top-k chunks from ChromaDB along with their
metadata and cosine distance score.

CLI mode runs the 3 evaluation queries from planning.md (the ones answerable
from the current corpus) and prints the top hits so you can eyeball whether
retrieval is on-topic.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from embed import CHROMA_DIR, COLLECTION_NAME, EMBEDDING_MODEL


@dataclass
class RetrievedChunk:
    text: str
    metadata: dict
    distance: float  # cosine distance: 0 = identical direction, 2 = opposite


# Cache the model + collection between calls (cheap on a single process,
# expensive otherwise: MiniLM is ~80 MB and Chroma's persistent client
# opens SQLite).
_model: Optional[SentenceTransformer] = None
_collection = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = client.get_collection(COLLECTION_NAME)
    return _collection


# Course codes mentioned in the query get pulled out and used as a hard
# metadata filter. Without this, MiniLM weights surrounding terms ("workload",
# "exams") more than the literal "CS 4337" tokens, so queries about a specific
# course often retrieve reviews from a different one with stronger word overlap.
_COURSE_CODE_RE = re.compile(r"\bCS\s*(\d{4})\b", re.IGNORECASE)

# Course-name keywords that should map to specific course codes. Covers the
# common ways students refer to each class on reddit/RMP without the code.
_COURSE_KEYWORDS = {
    "CS 3345": ["data structures"],
    "CS 3354": ["software engineering"],
    "CS 4337": ["programming language", "prog lang"],
    "CS 4347": ["database systems"],
    "CS 4348": ["operating systems"],
    "CS 4349": ["advanced algorithms"],
    "CS 6360": ["database design"],
    "CS 6363": ["graduate algorithms"],
    "CS 6364": ["artificial intelligence"],
    "CS 6375": ["machine learning"],
}


def _extract_courses(query: str) -> list[str]:
    """Find any course codes referenced in the query.

    Returns a list like ['CS 3345', 'CS 4337']. Handles 'CS 3345', 'CS3345',
    'cs 3345' uniformly. Also detects course-name keywords ('data structures',
    'machine learning') and maps them to codes. Returns [] if nothing matches.
    """
    courses = {f"CS {m.group(1)}" for m in _COURSE_CODE_RE.finditer(query)}
    qlow = query.lower()
    for code, keywords in _COURSE_KEYWORDS.items():
        if any(kw in qlow for kw in keywords):
            courses.add(code)
    return sorted(courses)


def retrieve(query: str, k: int = 5) -> list[RetrievedChunk]:
    """Top-k chunks for the query, sorted by ascending cosine distance.

    Lower distance = more similar. As a rough rule for sentence-transformers
    MiniLM: <0.3 is strong, 0.3-0.5 is decent, >0.7 is loose/unrelated.

    If the query references a specific course (by code or course-name keyword),
    results are filtered to chunks tagged with that course before falling back
    to unfiltered semantic search if fewer than k matches survive.
    """
    model = _get_model()
    collection = _get_collection()
    query_emb = model.encode(
        [query],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )[0]

    courses = _extract_courses(query)
    where = None
    if courses:
        where = {"course": {"$in": courses}} if len(courses) > 1 else {"course": courses[0]}

    res = collection.query(
        query_embeddings=[query_emb.tolist()],
        n_results=k,
        where=where,
    )
    out = _hits_from_response(res)

    # If the metadata filter starved retrieval (fewer than k hits), supplement
    # with unfiltered results so the LLM always has enough context downstream.
    if where is not None and len(out) < k:
        deficit = k - len(out)
        res_open = collection.query(
            query_embeddings=[query_emb.tolist()],
            n_results=k + deficit,  # extra to allow deduping
        )
        seen_texts = {h.text for h in out}
        for h in _hits_from_response(res_open):
            if h.text not in seen_texts:
                out.append(h)
                if len(out) >= k:
                    break
    return out[:k]


def _hits_from_response(res: dict) -> list[RetrievedChunk]:
    out: list[RetrievedChunk] = []
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        out.append(RetrievedChunk(text=doc, metadata=meta or {}, distance=float(dist)))
    return out


# --- CLI: run the 3 evaluation questions ----------------------------------

# Questions answerable from the current corpus (catalog + RMP + reddit).
# Q3 (avg GPA) requires utdgrades data which is still placeholder.
# Q5 (off-campus restaurant) is the out-of-scope refusal test for M5.
EVAL_QUERIES = [
    ("Q1", "What do students say about exams in CS 3345, and how do reviews differ by professor?"),
    ("Q2", "Which professor teaches CS 4337 most often, and what's the general consensus on the workload?"),
    ("Q4", "How do the top-reviewed CS 4337 professors compare on difficulty and teaching style?"),
]


def _print_result(label: str, query: str, hits: list[RetrievedChunk]) -> None:
    print(f"\n{'=' * 70}")
    print(f"{label}: {query}")
    print("=" * 70)
    for i, h in enumerate(hits, 1):
        course = h.metadata.get("course", "?")
        source = h.metadata.get("source", "?")
        extra = ""
        if source == "rmp":
            extra = (f"  [prof={h.metadata.get('prof_name','?')} "
                     f"diff={h.metadata.get('difficulty','?')} "
                     f"wta={h.metadata.get('would_take_again','?')}]")
        print(f"\n[{i}] distance={h.distance:.3f}  source={source}  course={course}{extra}")
        snippet = h.text.replace("\n", " ")
        if len(snippet) > 400:
            snippet = snippet[:400] + " ..."
        print(f"    {snippet}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--query", "-q", type=str, default=None,
                   help="Run a single ad-hoc query instead of the eval set")
    p.add_argument("-k", type=int, default=5, help="Number of chunks to retrieve")
    args = p.parse_args(argv)

    if args.query:
        hits = retrieve(args.query, k=args.k)
        _print_result("ad-hoc", args.query, hits)
        return 0

    for label, q in EVAL_QUERIES:
        hits = retrieve(q, k=args.k)
        _print_result(label, q, hits)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
