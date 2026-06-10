"""End-to-end smoke test for the Unofficial Guide pipeline.

Walks every stage and asserts known-good behavior so you catch regressions
after editing chunkers, retrieval, or prompts. Not a unit test suite, just a
"did I break anything obvious" check.

Run with: python scripts/test_pipeline.py

Exits 0 if everything passes, 1 if any stage fails. Prints PASS/FAIL per check
and a summary at the end. Failures don't short-circuit the run, you see every
stage's status in one go.

Assumes the index is already built. If the ChromaDB collection is missing this
script will tell you to run embed.py first instead of trying to rebuild it
(rebuilding is destructive and slow, and this test should be cheap to run).
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


# --- minimal test framework -----------------------------------------------

class Outcome:
    def __init__(self, name: str):
        self.name = name
        self.passed = True
        self.message = ""
        self.elapsed = 0.0


def _check(outcome: Outcome, cond: bool, message: str) -> None:
    """Mark the outcome failed if cond is false. First failure sticks."""
    if not cond and outcome.passed:
        outcome.passed = False
        outcome.message = message


def _run(name: str, fn) -> Outcome:
    outcome = Outcome(name)
    t0 = time.time()
    try:
        fn(outcome)
    except Exception as e:
        outcome.passed = False
        outcome.message = f"raised {type(e).__name__}: {e}"
        if "--trace" in sys.argv:
            traceback.print_exc()
    outcome.elapsed = time.time() - t0
    status = "PASS" if outcome.passed else "FAIL"
    detail = f"  ({outcome.message})" if outcome.message else ""
    print(f"  [{status}] {name}  ({outcome.elapsed:.2f}s){detail}")
    return outcome


# --- stage 1: ingest ------------------------------------------------------

def stage_ingest(outcome: Outcome) -> None:
    from ingest import load_documents
    docs = load_documents()
    _check(outcome, len(docs) >= 10,
           f"expected >=10 cleaned docs, got {len(docs)}")
    by_source = {}
    for d in docs:
        by_source[d.source_type] = by_source.get(d.source_type, 0) + 1
    _check(outcome, by_source.get("catalog", 0) >= 5,
           f"expected >=5 catalog docs, got {by_source.get('catalog', 0)}")
    _check(outcome, by_source.get("rmp", 0) >= 1,
           f"expected >=1 rmp doc, got {by_source.get('rmp', 0)}")
    _check(outcome, by_source.get("reddit", 0) >= 3,
           f"expected >=3 reddit docs (with manual paste), got {by_source.get('reddit', 0)}")
    # Cleaned text should be non-trivial.
    for d in docs:
        _check(outcome, len(d.cleaned_text) >= 50,
               f"{d.filename} cleaned to <50 chars ({len(d.cleaned_text)})")
    outcome.message = (f"{len(docs)} docs: "
                       + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())))


# --- stage 2: chunk -------------------------------------------------------

def stage_chunk(outcome: Outcome) -> None:
    from chunk import chunk_corpus
    chunks = chunk_corpus()
    _check(outcome, len(chunks) >= 500,
           f"expected >=500 chunks, got {len(chunks)}")
    # Per-source minimums.
    by_source = {}
    for c in chunks:
        s = c.metadata.get("source", "?")
        by_source[s] = by_source.get(s, 0) + 1
    _check(outcome, by_source.get("rmp", 0) >= 300,
           f"expected >=300 rmp chunks, got {by_source.get('rmp', 0)}")
    # Chunk size sanity: no zero-length, no absurd outliers.
    sizes = [len(c.text) for c in chunks]
    _check(outcome, min(sizes) >= 50,
           f"min chunk size {min(sizes)} is too small")
    _check(outcome, max(sizes) <= 1500,
           f"max chunk size {max(sizes)} is too large")
    # Every chunk must have source and course in metadata.
    missing_source = sum(1 for c in chunks if not c.metadata.get("source"))
    missing_course = sum(1 for c in chunks if not c.metadata.get("course"))
    _check(outcome, missing_source == 0,
           f"{missing_source} chunks missing source metadata")
    _check(outcome, missing_course == 0,
           f"{missing_course} chunks missing course metadata")
    outcome.message = f"{len(chunks)} chunks, sizes {min(sizes)}-{max(sizes)}"


# --- stage 3: retrieve ----------------------------------------------------

def stage_retrieve(outcome: Outcome) -> None:
    # Defer the import so the missing-index error message is clean.
    try:
        from retrieve import retrieve
    except Exception as e:
        outcome.passed = False
        outcome.message = f"import failed: {e}"
        return
    try:
        hits = retrieve("What are exams like in CS 3345?", k=5)
    except Exception as e:
        outcome.passed = False
        outcome.message = (f"query failed ({e}). Run `python scripts/embed.py` "
                           f"first to build the index.")
        return
    _check(outcome, len(hits) == 5,
           f"expected 5 hits, got {len(hits)}")
    _check(outcome, hits[0].distance < 0.5,
           f"top hit distance {hits[0].distance:.3f} above the 0.5 threshold")
    # Course filter should kick in on "CS 3345" and force the right course.
    cs3345_hits = sum(1 for h in hits if h.metadata.get("course") == "CS 3345")
    _check(outcome, cs3345_hits >= 4,
           f"course filter didn't take, only {cs3345_hits}/5 hits tagged CS 3345")
    outcome.message = (f"top distance {hits[0].distance:.3f}, "
                       f"{cs3345_hits}/5 on target course")


# --- stage 4: end-to-end grounded query -----------------------------------

def stage_query_grounded(outcome: Outcome) -> None:
    try:
        from query import ask
    except Exception as e:
        outcome.passed = False
        outcome.message = f"import failed: {e}"
        return
    try:
        a = ask("What do students say about exams in CS 3345?")
    except Exception as e:
        outcome.passed = False
        outcome.message = (f"ask() failed ({e}). Check GROQ_API_KEY in .env "
                           f"or whether the Groq API is reachable.")
        return
    _check(outcome, not a.refused,
           "system refused on a query that should be answerable")
    _check(outcome, len(a.answer) > 100,
           f"answer suspiciously short ({len(a.answer)} chars)")
    _check(outcome, len(a.sources) >= 1,
           f"expected >=1 source attribution, got {len(a.sources)}")
    _check(outcome, "3345" in a.answer or "CS 3345" in a.answer,
           "answer doesn't mention CS 3345 anywhere")
    outcome.message = (f"{len(a.answer)} char answer, "
                       f"{len(a.sources)} unique source file(s)")


# --- stage 5: out-of-scope refusal ----------------------------------------

def stage_query_refusal(outcome: Outcome) -> None:
    try:
        from query import ask, REFUSAL_TEXT
    except Exception as e:
        outcome.passed = False
        outcome.message = f"import failed: {e}"
        return
    try:
        a = ask("What's the best off-campus restaurant near UTD?")
    except Exception as e:
        outcome.passed = False
        outcome.message = f"ask() failed ({e})"
        return
    _check(outcome, a.refused,
           "system answered an out-of-scope query instead of refusing")
    # Sources should be cleared on refusal.
    _check(outcome, len(a.sources) == 0,
           f"refusal should clear sources, got {len(a.sources)}")
    # The refusal text should be in the answer.
    refusal_fragment = "don't have enough information"
    _check(outcome, refusal_fragment in a.answer.lower(),
           f"refusal text not found in answer: {a.answer[:120]!r}")
    outcome.message = "refused cleanly with no sources"


# --- driver ---------------------------------------------------------------

STAGES = [
    ("ingest",          stage_ingest),
    ("chunk",           stage_chunk),
    ("retrieve",        stage_retrieve),
    ("query (grounded)", stage_query_grounded),
    ("query (refusal)",  stage_query_refusal),
]


def main() -> int:
    print("Running pipeline smoke test")
    print("-" * 50)
    results = [_run(name, fn) for name, fn in STAGES]
    print("-" * 50)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    total_secs = sum(r.elapsed for r in results)
    print(f"{passed}/{total} stages passed in {total_secs:.1f}s")
    if passed < total:
        failed = [r.name for r in results if not r.passed]
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
