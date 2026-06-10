"""Chunk cleaned documents into retrieval-ready units with metadata.

Two chunking modes are used in this pipeline, dispatched on source type:

1. RMP reviews chunk by review boundary, not by character count.
   Each "- class=CSNNNN | diff=N | ..." line plus its following comment text
   becomes one chunk. The prof's avgRating/avgDifficulty/numRatings and the
   prof name ride along as metadata. Why per-review: RMP reviews are
   self-contained 1-4 sentence units already in the 200-500 char range. A
   blind 400-char chunker would either split a single review across a
   boundary (losing context) or merge multiple unrelated reviews (diluting
   the embedding). Per-review chunks preserve "who said what about which
   class" naturally.

2. Catalog + reddit chunk with a sliding ~400-char window with ~50 overlap.
   Where possible the window breaks at paragraph (double newline) and
   sentence boundaries instead of mid-word.

Each chunk carries a metadata dict consumed by the embedding step (Milestone 4)
and the answer-attribution step (Milestone 5).
"""

from __future__ import annotations

import argparse
import random
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

from ingest import Document, load_documents


# --- config ---------------------------------------------------------------

DEFAULT_CHUNK_SIZE = 400        # characters, ~80-100 tokens for short opinion text
DEFAULT_OVERLAP = 50            # characters
MIN_CHUNK_CHARS = 80            # drop anything smaller, it's a fragment


# --- data shape -----------------------------------------------------------

@dataclass
class Chunk:
    text: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"text": self.text, "metadata": self.metadata}


# --- RMP per-review chunker -----------------------------------------------

# Prof block header: === First Last (legacyId 12345) ===
_RMP_PROF_HEAD = re.compile(r"^===\s+(.+?)\s+\(legacyId\s+(\d+)\)\s+===\s*$")
# Prof summary line right after the head: avgRating: X | avgDifficulty: Y | numRatings: Z (all-time) | dept: ...
_RMP_PROF_SUMMARY = re.compile(
    r"^avgRating:\s*(?P<avg_rating>\S+)\s*\|\s*"
    r"avgDifficulty:\s*(?P<avg_difficulty>\S+)\s*\|\s*"
    r"numRatings:\s*(?P<num_ratings>\d+).*?\|\s*"
    r"dept:\s*(?P<dept>.+?)\s*$"
)
# Per-review line: - class=CSNNNN | diff=N | clarity=N | help=N | wta=N|None | date=...
_RMP_REVIEW_HEAD = re.compile(
    r"^-\s*class=(?P<klass>\S+)\s*\|\s*"
    r"diff=(?P<diff>\S+)\s*\|\s*"
    r"clarity=(?P<clarity>\S+)\s*\|\s*"
    r"help=(?P<help>\S+)\s*\|\s*"
    r"wta=(?P<wta>\S+)\s*\|\s*"
    r"date=(?P<date>.+?)\s*$"
)
# target_course_reviews summary line (optional, skip during chunking).
_RMP_PROF_TARGET_COUNT = re.compile(r"^target_course_reviews:\s*\d+\s*$")


def _normalize_class_tag(klass: str) -> str:
    """RMP's class field is messy (CS3345, CS 3345, CS3345.001, SE3345).
    Return the canonical 'CS NNNN' form. SE-prefix CS-cross-listings normalize to CS."""
    code = klass.upper().replace(" ", "").replace(".", "")
    # Strip trailing section/honors/topic suffixes. UTD section numbers always
    # start with 0 (001, 002, 010, ...), which lets us distinguish them from
    # the 4-digit course number itself.
    code = re.sub(r"(?:0\d{2}|HON|IOT|BUS|HC)$", "", code)
    # SE-prefix cross-listings count as CS
    if code.startswith("SE") and code[2:].isdigit():
        code = "CS" + code[2:]
    if code.startswith("CS") and len(code) >= 6 and code[2:6].isdigit():
        return f"CS {code[2:6]}"
    return klass.strip()


def chunk_rmp(doc: Document) -> list[Chunk]:
    """Walk the cleaned RMP text and emit one chunk per review.

    State machine:
      Outside any prof:
        - Hit a "=== Name ===" -> remember prof
        - Hit the summary line -> remember prof's overall ratings
      Inside a prof:
        - Hit "- class=... | diff=..." -> open a review
        - Hit subsequent indented lines -> append to current review
        - Hit a new "=== Name ===" or "- class=..." -> close the current review
      End of file -> close any open review.
    """
    chunks: list[Chunk] = []
    current_prof: dict = {}
    current_review_meta: dict | None = None
    current_review_body: list[str] = []

    def close_review() -> None:
        nonlocal current_review_meta, current_review_body
        if current_review_meta is None:
            return
        body = " ".join(s.strip() for s in current_review_body if s.strip())
        if not body or len(body) < MIN_CHUNK_CHARS:
            current_review_meta, current_review_body = None, []
            return
        meta = {**current_prof, **current_review_meta}
        text = (
            f"Review of Prof {current_prof.get('prof_name','?')} "
            f"for {current_review_meta['course']}: {body}"
        )
        chunks.append(Chunk(text=text, metadata=meta))
        current_review_meta, current_review_body = None, []

    for line in doc.cleaned_text.splitlines():
        # New prof block?
        m = _RMP_PROF_HEAD.match(line)
        if m:
            close_review()
            current_prof = {
                "source": "rmp",
                "source_file": doc.filename,
                "url": doc.url,
                "prof_name": m.group(1),
                "prof_legacy_id": m.group(2),
            }
            continue
        # Prof summary right after the head
        m = _RMP_PROF_SUMMARY.match(line)
        if m and current_prof:
            current_prof.update({
                "prof_avg_rating": m.group("avg_rating"),
                "prof_avg_difficulty": m.group("avg_difficulty"),
                "prof_num_ratings": int(m.group("num_ratings")),
                "prof_dept": m.group("dept"),
            })
            continue
        if _RMP_PROF_TARGET_COUNT.match(line):
            continue
        # New review?
        m = _RMP_REVIEW_HEAD.match(line)
        if m:
            close_review()
            current_review_meta = {
                "course": _normalize_class_tag(m.group("klass")),
                "raw_class_tag": m.group("klass"),
                "difficulty": m.group("diff"),
                "clarity": m.group("clarity"),
                "helpful": m.group("help"),
                "would_take_again": m.group("wta"),
                "date": m.group("date"),
            }
            current_review_body = []
            continue
        # Body line for the current review (indented continuation)
        if current_review_meta is not None and line.strip():
            current_review_body.append(line)

    close_review()
    return chunks


# --- generic sliding-window chunker ---------------------------------------

# Prefer breaks at, in order: paragraph (\n\n), sentence (.!?), space.
_BREAK_PRIORITY = ("\n\n", ". ", "! ", "? ", "; ", ", ", " ")


def _find_break(text: str, target_end: int) -> int:
    """Find a good break point near target_end. Returns the offset to cut at."""
    if target_end >= len(text):
        return len(text)
    search_window = text[max(0, target_end - 80):target_end + 80]
    base = max(0, target_end - 80)
    best = target_end
    for sep in _BREAK_PRIORITY:
        # Look backwards from target_end first (avoid making chunks much larger)
        idx = search_window.rfind(sep, 0, 80)  # within the first 80 chars (i.e. before target)
        if idx != -1:
            best = base + idx + len(sep)
            return best
    return target_end


def chunk_sliding(text: str, size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """Sliding window over `text`. Returns a list of chunk strings.

    Each chunk is up to `size` chars; consecutive chunks share `overlap` chars
    of trailing context so a fact straddling a boundary still lands in one.
    Breaks at paragraph/sentence/word boundaries when reasonable.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text] if len(text) >= MIN_CHUNK_CHARS else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        target_end = start + size
        cut = _find_break(text, target_end)
        chunk = text[start:cut].strip()
        if len(chunk) >= MIN_CHUNK_CHARS:
            chunks.append(chunk)
        if cut >= len(text):
            break
        # Step start forward with overlap, then align the new start to the
        # nearest break point so the next chunk doesn't begin mid-word.
        next_start = max(cut - overlap, start + 1)
        next_start = _align_start_to_break(text, next_start)
        if next_start <= start:  # safety: prevent infinite loop
            next_start = start + 1
        start = next_start
    return chunks


def _align_start_to_break(text: str, pos: int) -> int:
    """Nudge the start forward to the next word/sentence boundary.

    Looks ahead up to 30 chars for a break (paragraph > sentence > space) so
    the next chunk starts on a clean word, not in the middle of one. Doesn't
    nudge if no break is reasonably close.
    """
    if pos <= 0 or pos >= len(text):
        return pos
    look = text[pos:pos + 30]
    for sep in _BREAK_PRIORITY:
        idx = look.find(sep)
        if idx != -1:
            return pos + idx + len(sep)
    return pos


# --- catalog + reddit chunkers --------------------------------------------

def chunk_catalog(doc: Document) -> list[Chunk]:
    """Catalog descriptions are usually short (300-500 chars) -> typically one chunk."""
    course = doc.course_codes[0] if doc.course_codes else "?"
    pieces = chunk_sliding(doc.cleaned_text)
    out: list[Chunk] = []
    for i, p in enumerate(pieces):
        out.append(Chunk(
            text=f"UTD official catalog description for {course}: {p}",
            metadata={
                "source": "catalog",
                "source_file": doc.filename,
                "course": course,
                "url": doc.url,
                "position": i,
            },
        ))
    return out


def chunk_reddit(doc: Document) -> list[Chunk]:
    """Reddit chunks: sliding window, with course as metadata."""
    course = doc.course_codes[0] if doc.course_codes else "?"
    pieces = chunk_sliding(doc.cleaned_text)
    out: list[Chunk] = []
    for i, p in enumerate(pieces):
        out.append(Chunk(
            text=f"r/utdallas discussion of {course}: {p}",
            metadata={
                "source": "reddit",
                "source_file": doc.filename,
                "course": course,
                "url": doc.url,
                "position": i,
            },
        ))
    return out


# --- top-level dispatch ---------------------------------------------------

def chunk_document(doc: Document) -> list[Chunk]:
    if doc.source_type == "rmp":
        return chunk_rmp(doc)
    if doc.source_type == "catalog":
        return chunk_catalog(doc)
    if doc.source_type == "reddit":
        return chunk_reddit(doc)
    return []


def chunk_corpus(docs: Iterable[Document] | None = None) -> list[Chunk]:
    if docs is None:
        docs = load_documents()
    all_chunks: list[Chunk] = []
    for d in docs:
        all_chunks.extend(chunk_document(d))
    return all_chunks


# --- CLI for spot-checking ------------------------------------------------

def _summarize(chunks: list[Chunk]) -> None:
    by_source: dict[str, int] = {}
    by_course: dict[str, int] = {}
    lengths: list[int] = []
    for c in chunks:
        s = c.metadata.get("source", "?")
        by_source[s] = by_source.get(s, 0) + 1
        course = c.metadata.get("course", "?")
        by_course[course] = by_course.get(course, 0) + 1
        lengths.append(len(c.text))
    print(f"Total chunks: {len(chunks)}")
    if lengths:
        print(f"Chunk length stats: min={min(lengths)} max={max(lengths)} "
              f"mean={sum(lengths)//len(lengths)}")
    print("By source:")
    for s, n in sorted(by_source.items()):
        print(f"  {s:10} {n:>4} chunks")
    print("By course:")
    for c, n in sorted(by_course.items()):
        print(f"  {c:10} {n:>4} chunks")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=int, default=5, help="Print N random chunks for spot-check")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--filter-source", type=str, default=None,
                   help="Only sample from this source type (catalog/reddit/rmp)")
    args = p.parse_args(argv)

    chunks = chunk_corpus()
    _summarize(chunks)
    if args.sample > 0:
        random.seed(args.seed)
        pool = chunks
        if args.filter_source:
            pool = [c for c in chunks if c.metadata.get("source") == args.filter_source]
        sample = random.sample(pool, min(args.sample, len(pool)))
        print(f"\n--- {len(sample)} random chunks ---")
        for i, c in enumerate(sample, 1):
            print(f"\n[{i}] source={c.metadata.get('source')} "
                  f"course={c.metadata.get('course')} len={len(c.text)}")
            if c.metadata.get("source") == "rmp":
                print(f"    prof={c.metadata.get('prof_name')} "
                      f"diff={c.metadata.get('difficulty')} wta={c.metadata.get('would_take_again')}")
            print("    " + c.text.replace("\n", "\n    ")[:600])
            if len(c.text) > 600:
                print("    ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
