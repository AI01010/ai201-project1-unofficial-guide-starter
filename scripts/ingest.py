"""Load and clean documents from documents/ for the Unofficial Guide RAG pipeline.

Per-source cleaners handle the very different document types in our corpus:
- catalog_csNNNN.txt: UTD official course catalog. Description sits on a single
  line surrounded by ~50 lines of nav cruft (catalog years, deans, etc.).
  Slice out the description line, drop everything else.
- rmp_utd_cs.txt: structured prof + review blocks. Preserve the structure
  (prof === heading + per-review tagged lines) so chunk.py can chunk by review.
- reddit_csNNNN.txt: manually pasted from reddit's web UI, has lots of UI cruft
  (Upvote/Downvote/Reply/Award/Share, "5y ago" timestamps, "u/X avatar" markers,
  bullet bullets). Strip the cruft, keep titles + selftext + comments. Also
  strip the original placeholder body text if the user left it in the file.
- All sources: lines starting with "#" are provenance headers, skip them.
  The "---" divider line is also skipped.

Empty placeholders (files where the user hasn't pasted anything yet) are detected
and excluded from the output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional


# --- paths ----------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "documents"
SOURCES_MANIFEST = DOCS_DIR / "sources.json"


# --- data shape -----------------------------------------------------------

@dataclass
class Document:
    """One cleaned source document, ready for chunking."""
    filename: str
    source_type: str            # "catalog" | "rmp" | "reddit"
    course_codes: list[str]     # e.g. ["CS 3345"] for per-course files, all 11 for RMP
    raw_text: str
    cleaned_text: str
    url: Optional[str] = None


# --- placeholder detection ------------------------------------------------

def _read_text_with_fallback(path: Path) -> str:
    """Read a file as UTF-8; fall back to cp1252 on decode error.

    Reddit content pasted from the Windows clipboard often contains cp1252
    smart quotes (e.g. \\x92 for U+2019) that error out under strict UTF-8.
    Trying cp1252 as a fallback preserves the punctuation instead of producing
    \\ufffd replacement chars that look like garbage in chunks.
    """
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


_PLACEHOLDER_MARKERS = (
    "Reddit's public JSON endpoints currently return HTTP 403",
    "Manual collection steps for",
    "PLACEHOLDER. utdgrades is a SPA",
    "PLACEHOLDER. probed `api.utdnebula.com`",
    "Alternative: register a script-type app",
)


def is_empty_placeholder(text: str) -> bool:
    """True if the file is just the placeholder template with no user content added.

    Empties have <2 KB, only the # header + placeholder body, no real threads or
    grade tables pasted in.
    """
    if len(text) > 4000:
        return False
    # Strip the # header and divider; if what remains is just placeholder markers
    # and short, it's empty.
    body_lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.startswith("#") and ln.strip() != "---"
    ]
    body = "\n".join(body_lines)
    if not body.strip():
        return True
    return any(m in body for m in _PLACEHOLDER_MARKERS) and len(body) < 3500


# --- shared helpers -------------------------------------------------------

def _strip_header_block(text: str) -> str:
    """Drop the leading # header comments and the --- divider."""
    out = []
    past_divider = False
    for line in text.splitlines():
        if not past_divider:
            if line.strip() == "---":
                past_divider = True
                continue
            if line.startswith("#"):
                continue
            # If there's a non-# line before the divider (rare), keep it.
        out.append(line)
    return "\n".join(out)


# --- catalog cleaner ------------------------------------------------------

_CATALOG_DESCRIPTION_RE = re.compile(
    r"^(CS\s+\d{4}\s+[^()]+\(\d+\s+semester credit hours\).*?\([0-9-]+\)\s*[A-Z]?)\s*$"
)


def clean_catalog(raw: str) -> str:
    """Pull just the official course description from a catalog page.

    The UTD catalog pages have the actual description on a single line that
    matches: 'CS NNNN <Title> (N semester credit hours) <description...>
    Prerequisites: ... (N-N) S/Y'. We grep that one line and ignore the
    surrounding catalog nav.
    """
    for line in raw.splitlines():
        line = line.strip()
        if _CATALOG_DESCRIPTION_RE.match(line):
            return line
    # Fallback: if the structured match misses, return everything that mentions
    # "semester credit hours" so we don't silently drop the document.
    candidates = [
        ln.strip() for ln in raw.splitlines()
        if "semester credit hours" in ln and ln.strip().startswith("CS ")
    ]
    if candidates:
        return max(candidates, key=len)
    return ""


# --- RMP cleaner ----------------------------------------------------------

def clean_rmp(raw: str) -> str:
    """Strip the # header comments. Preserve prof === blocks and per-review lines.

    The chunker will split by review boundary, so we keep the structure intact.
    """
    return _strip_header_block(raw).strip()


# --- reddit cleaner -------------------------------------------------------

# UI-cruft patterns. Any line matching one of these gets dropped.
_REDDIT_CRUFT_PATTERNS = [
    re.compile(r"^\s*$"),                      # blank (collapsed later)
    re.compile(r"^\s*•\s*$"),                  # bullet markers
    re.compile(r"^\s*\d+[ywdmh]\s+ago\s*$"),   # "5y ago", "3mo ago" etc
    re.compile(r"^\s*(Upvote|Downvote|Reply|Award|Share|Save)\s*$", re.IGNORECASE),
    re.compile(r"^\s*u/\S+\s+avatar\s*$"),     # "u/Foo avatar"
    re.compile(r"^\s*OP\s*$"),                 # "OP" by-itself markers
    re.compile(r"^\s*\d+\s*$"),                # standalone vote counts
    re.compile(r"^\s*Alumnus\s*$"),            # "Alumnus" flair line alone
    re.compile(r"^\s*r/utdallas\s*$"),         # subreddit header
]

# Placeholder template body markers (so the user can leave them in the file
# and we still strip them automatically).
_PLACEHOLDER_LINE_MARKERS = (
    "Reddit's public JSON endpoints",
    "requests (including the search",
    "Reddit started blocking anonymous",
    "remains in effect. Without OAuth",
    "Manual collection steps for",
    "1. In a browser, open",
    "2. Open the top 8-12 threads",
    "3. For each thread, copy:",
    "- The post title and body text.",
    "- The top 10-20 comments",
    "4. Paste below the --- divider",
    "=== THREAD N: <title> ===",
    "url: <permalink>",
    "<selftext>",
    "COMMENTS:",
    "- [author]",
    "- <comment>",
    "Alternative: register a script-type app",
    "get a client_id + client_secret",
    "flow (POST https://www.reddit.com/api",
    "token on oauth.reddit.com URLs)",
)


def _is_placeholder_template_line(line: str) -> bool:
    s = line.strip()
    return any(m in s for m in _PLACEHOLDER_LINE_MARKERS)


def clean_reddit(raw: str) -> str:
    """Strip the # header, placeholder template body, and reddit-UI cruft.

    Keeps post titles, body text (selftext), and comment text. Collapses
    consecutive blank lines into single blanks so chunks read cleanly.
    """
    text = _strip_header_block(raw)
    out_lines: list[str] = []
    prev_blank = False
    for line in text.splitlines():
        if _is_placeholder_template_line(line):
            continue
        if any(p.match(line) for p in _REDDIT_CRUFT_PATTERNS[1:]):
            continue
        # Collapse blanks
        if not line.strip():
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        out_lines.append(line.rstrip())
    cleaned = "\n".join(out_lines).strip()
    # One more pass to collapse 3+ blanks down to 1
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


# --- dispatch + loading ---------------------------------------------------

_FILENAME_PATTERNS = {
    re.compile(r"^catalog_cs(\d{4})\.txt$"): "catalog",
    re.compile(r"^rmp_utd_cs\.txt$"): "rmp",
    re.compile(r"^reddit_cs(\d{4})\.txt$"): "reddit",
    re.compile(r"^utdgrades_cs(\d{4})\.txt$"): "utdgrades",
    re.compile(r"^trends_placeholder\.txt$"): "trends",
}


def _classify(filename: str) -> tuple[str | None, list[str]]:
    """Return (source_type, course_codes) for a filename or (None, []) if unknown."""
    for pat, source_type in _FILENAME_PATTERNS.items():
        m = pat.match(filename)
        if m:
            if m.groups():
                return source_type, [f"CS {m.group(1)}"]
            return source_type, []
    return None, []


def _all_target_courses() -> list[str]:
    """RMP and trends cover all target courses. Read them from sources.json
    if available, fall back to a hardcoded list."""
    if SOURCES_MANIFEST.exists():
        try:
            data = json.loads(SOURCES_MANIFEST.read_text())
            for entry in data.get("files", []):
                if entry.get("filename") == "rmp_utd_cs.txt":
                    return list(entry.get("courses") or [])
        except (json.JSONDecodeError, OSError):
            pass
    return [
        "CS 3345", "CS 4337", "CS 4347", "CS 4348", "CS 4349",
        "CS 3354", "CS 4383", "CS 6360", "CS 6363", "CS 6364", "CS 6375",
    ]


def _url_for(filename: str) -> Optional[str]:
    """Look up the source URL for this filename from sources.json."""
    if not SOURCES_MANIFEST.exists():
        return None
    try:
        data = json.loads(SOURCES_MANIFEST.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    for entry in data.get("files", []):
        if entry.get("filename") == filename:
            return entry.get("url")
    return None


def load_documents(docs_dir: Path = DOCS_DIR) -> list[Document]:
    """Load and clean every document in documents/. Skip empty placeholders."""
    docs: list[Document] = []
    for path in sorted(docs_dir.glob("*.txt")):
        source_type, course_codes = _classify(path.name)
        if source_type is None:
            continue
        raw = _read_text_with_fallback(path)
        if is_empty_placeholder(raw):
            continue
        if source_type == "catalog":
            cleaned = clean_catalog(raw)
        elif source_type == "rmp":
            cleaned = clean_rmp(raw)
            course_codes = _all_target_courses()
        elif source_type == "reddit":
            cleaned = clean_reddit(raw)
        elif source_type in ("utdgrades", "trends"):
            # These are still placeholders; skip until the user pastes data.
            continue
        else:
            continue
        if not cleaned.strip():
            continue
        docs.append(Document(
            filename=path.name,
            source_type=source_type,
            course_codes=course_codes,
            raw_text=raw,
            cleaned_text=cleaned,
            url=_url_for(path.name),
        ))
    return docs


# --- CLI for spot-checking ------------------------------------------------

def _print_summary(docs: Iterable[Document]) -> None:
    by_type: dict[str, int] = {}
    total_chars = 0
    for d in docs:
        by_type[d.source_type] = by_type.get(d.source_type, 0) + 1
        total_chars += len(d.cleaned_text)
    print(f"Loaded {sum(by_type.values())} documents, {total_chars:,} cleaned chars total")
    for st, n in sorted(by_type.items()):
        print(f"  {st:10} {n:>3} files")


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--show", type=str, default=None,
                   help="Print the cleaned text of this filename")
    p.add_argument("--show-raw", action="store_true",
                   help="With --show, also print the raw text length")
    args = p.parse_args(argv)
    docs = load_documents()
    _print_summary(docs)
    if args.show:
        for d in docs:
            if d.filename == args.show:
                print("\n" + "=" * 60)
                print(f"{d.filename}  ({d.source_type}, courses: {d.course_codes})")
                if args.show_raw:
                    print(f"raw: {len(d.raw_text):,} chars  ->  cleaned: {len(d.cleaned_text):,} chars")
                print("=" * 60)
                print(d.cleaned_text[:4000])
                if len(d.cleaned_text) > 4000:
                    print(f"\n... [truncated, {len(d.cleaned_text) - 4000:,} more chars]")
                return 0
        print(f"No document loaded named {args.show!r}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
