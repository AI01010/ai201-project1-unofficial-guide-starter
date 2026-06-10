"""End-to-end grounded Q&A over the UTD corpus.

ask(question) does:
  1. retrieve() the top-k chunks for the question.
  2. Format them as numbered context excerpts in a prompt.
  3. Call Groq's llama-3.3-70b-versatile with a system prompt that REQUIRES
     answers come from the provided context only and tells the model to
     refuse if the context doesn't cover the question.
  4. Append a deduped "Sources:" list of the actual filenames and URLs the
     retrieved chunks came from, so attribution is programmatic rather than
     left to the model to remember.

The refusal mechanism is two layered:
  - Prompt-side: explicit instruction to say "I don't have enough information"
    if context is missing the answer.
  - Pipeline-side: if no chunks survive retrieval (or all are very weak), we
    short-circuit before the LLM call and return the refusal directly.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
from groq import Groq

from retrieve import RetrievedChunk, retrieve

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")


GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_K = 5
# Cosine distance above this means even the best hit isn't really on-topic.
# Cuts off questions like "best restaurant near UTD" before the LLM is called.
REFUSAL_DISTANCE_THRESHOLD = 0.85
REFUSAL_TEXT = (
    "I don't have enough information on that based on the documents I have access to. "
    "This system only covers UT Dallas CS class and professor reviews."
)

SYSTEM_PROMPT = """\
You are an assistant that answers questions about UT Dallas (UTD) computer science classes and professors.

Answer using ONLY the information in the provided context excerpts. Do not use your general training knowledge about courses, professors, or universities. If the context does not contain enough information to answer the question, reply exactly: "I don't have enough information on that based on the documents I have access to."

When you reference specific information from an excerpt, cite it inline like [1] or [3]. If multiple excerpts agree or disagree on a point, surface that (e.g. "reviews are split: [1] says X, [4] says Y").

Be concise. Keep your answer focused on what the question actually asked."""


# --- data shape -----------------------------------------------------------

@dataclass
class Answer:
    answer: str
    sources: list[dict]     # [{"filename": ..., "url": ..., "chunks_used": [1, 3]}]
    retrieved: list[RetrievedChunk]
    refused: bool = False   # True if the pipeline refused before calling the LLM


# --- groq client ----------------------------------------------------------

_groq_client: Optional[Groq] = None


def _get_groq() -> Groq:
    global _groq_client
    if _groq_client is None:
        key = os.environ.get("GROQ_API_KEY")
        if not key or key == "your_key_here":
            raise RuntimeError(
                "GROQ_API_KEY missing or unset in .env. "
                "Get a free key at https://console.groq.com and put it in .env."
            )
        _groq_client = Groq(api_key=key)
    return _groq_client


# --- prompt assembly ------------------------------------------------------

def _format_context(hits: list[RetrievedChunk]) -> str:
    """Build numbered context excerpts the LLM can cite."""
    lines = []
    for i, h in enumerate(hits, 1):
        meta = h.metadata
        source_file = meta.get("source_file", "?")
        course = meta.get("course", "?")
        prof = meta.get("prof_name")
        tag = f"source={source_file}, course={course}"
        if prof:
            tag += f", prof={prof}"
        lines.append(f"[{i}] ({tag})\n    {h.text.strip()}")
    return "\n\n".join(lines)


def _summarize_sources(hits: list[RetrievedChunk]) -> list[dict]:
    """Group retrieved chunks by source file. Returns one entry per unique
    source with the list of chunk indices that came from it."""
    by_source: dict[str, dict] = {}
    for i, h in enumerate(hits, 1):
        fn = h.metadata.get("source_file", "?")
        if fn not in by_source:
            by_source[fn] = {
                "filename": fn,
                "url": h.metadata.get("url"),
                "chunks_used": [],
            }
        by_source[fn]["chunks_used"].append(i)
    return list(by_source.values())


# --- main entry point -----------------------------------------------------

def ask(question: str, k: int = DEFAULT_K, model: str = GROQ_MODEL) -> Answer:
    """Retrieve, prompt the LLM, and return a grounded answer + source list."""
    hits = retrieve(question, k=k)

    # Pipeline-side refusal: no chunks, or top hit is way too distant.
    if not hits:
        return Answer(answer=REFUSAL_TEXT, sources=[], retrieved=[], refused=True)
    if hits[0].distance > REFUSAL_DISTANCE_THRESHOLD:
        return Answer(answer=REFUSAL_TEXT, sources=[], retrieved=hits, refused=True)

    context = _format_context(hits)
    user_msg = f"Question: {question}\n\nContext excerpts:\n{context}"

    client = _get_groq()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.0,
        max_tokens=600,
    )
    answer_text = (resp.choices[0].message.content or "").strip()

    # If the LLM itself refused (matched the refusal pattern), clear sources
    # so the UI doesn't suggest the answer was drawn from them.
    llm_refused = REFUSAL_TEXT in answer_text or "don't have enough information" in answer_text.lower()
    sources = [] if llm_refused else _summarize_sources(hits)
    return Answer(
        answer=answer_text,
        sources=sources,
        retrieved=hits,
        refused=llm_refused,
    )


# --- CLI for spot-checking ------------------------------------------------

EVAL_QUERIES = [
    ("Q1", "What do students say about exams in CS 3345, and how do reviews differ by professor?"),
    ("Q2", "Which professor teaches CS 4337 most often, and what's the general consensus on the workload?"),
    ("Q4", "How do the top-reviewed CS 4337 professors compare on difficulty and teaching style?"),
    ("Q5", "What's the best off-campus restaurant near UTD?"),  # out-of-scope refusal test
]


def _format_sources_for_print(sources: list[dict]) -> str:
    if not sources:
        return "(none)"
    out = []
    for s in sources:
        url = s.get("url") or ""
        cu = ",".join(str(c) for c in s["chunks_used"])
        out.append(f"  - {s['filename']}  [excerpts {cu}]  {url}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--query", "-q", type=str, default=None)
    p.add_argument("-k", type=int, default=DEFAULT_K)
    args = p.parse_args(argv)

    queries = [("ad-hoc", args.query)] if args.query else EVAL_QUERIES
    for label, q in queries:
        print(f"\n{'=' * 72}\n{label}: {q}\n{'=' * 72}")
        a = ask(q, k=args.k)
        prefix = "REFUSED (pipeline)" if a.refused else "ANSWER"
        print(f"\n{prefix}:\n{a.answer}")
        print("\nSources:")
        print(_format_sources_for_print(a.sources))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
