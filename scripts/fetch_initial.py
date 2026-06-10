"""
Milestone 1 fetch script for "The Unofficial Guide".

Pulls raw documents into ../documents/ for 5 UTD CS upper-division courses:
  CS 3345, CS 4337, CS 4348, CS 4349, CS 4347

Sources attempted per course:
  - r/utdallas search results (+ comments per thread)
  - utdgrades.com grade pages
  - UTD official catalog (catalog.utdallas.edu)

Plus two cross-course sources:
  - Rate My Professors (UTD, schoolID U2Nob29sLTEyNzM=)  -- GraphQL probe, placeholder fallback
  - trends.utdnebula.com data via api.utdnebula.com     -- API probe, placeholder fallback

Stdlib only. Polite (1-2s sleeps). Time-boxed (RMP + nebula combined < ~10 min).

Re-runnable: every run rewrites documents/<source>_cs<num>.txt and the manifest.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import html
import datetime
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# --- config ---------------------------------------------------------------

COURSES = ["3345", "4337", "4348", "4349", "4347", "3354", "4383", "6360", "6363", "6364", "6375"]

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "documents"
DOCS.mkdir(parents=True, exist_ok=True)

REDDIT_UA = "Mozilla/5.0 (compatible; utd-rag-research/0.1 by ai201-student)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REDDIT_SLEEP = 2.0   # be polite to reddit
HOST_SLEEP = 1.0     # generic polite delay between hits to the same host

# Hard time-box for RMP + nebula combined (seconds)
HARD_OPTIONAL_BUDGET = 1800  # 30 minutes

# --- helpers --------------------------------------------------------------

def ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def http_get(url: str, *, ua: str, timeout: int = 30) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return e.code, body, dict(e.headers or {})
    except Exception as e:
        return 0, str(e).encode("utf-8", errors="ignore"), {}


def http_post_json(url: str, payload: dict, *, ua: str, extra_headers: dict | None = None,
                   timeout: int = 30) -> tuple[int, bytes, dict]:
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": ua,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return e.code, body, dict(e.headers or {})
    except Exception as e:
        return 0, str(e).encode("utf-8", errors="ignore"), {}


def write_doc(filename: str, header: dict, body: str) -> Path:
    """Write a .txt with a provenance header followed by --- then body."""
    path = DOCS / filename
    lines = []
    for k, v in header.items():
        lines.append(f"# {k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def strip_html(s: str) -> str:
    """Very light HTML -> text. We want to keep paragraph breaks."""
    if not s:
        return ""
    # drop <script>/<style> blocks
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    # block-level tags -> newline
    s = re.sub(r"</?(p|br|div|li|tr|h[1-6])[^>]*>", "\n", s, flags=re.I)
    # everything else
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    # collapse whitespace
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# --- 1. reddit ------------------------------------------------------------

def reddit_search(course_num: str) -> tuple[list[dict], str]:
    q = urllib.parse.quote_plus(f"CS {course_num}")
    url = (
        f"https://www.reddit.com/r/utdallas/search.json?"
        f"q={q}&restrict_sr=1&sort=relevance&t=all&limit=25"
    )
    status, body, _ = http_get(url, ua=REDDIT_UA)
    if status != 200:
        return [], url
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return [], url
    children = (data.get("data") or {}).get("children") or []
    threads = []
    for c in children:
        d = c.get("data") or {}
        if not d.get("permalink"):
            continue
        threads.append({
            "title": d.get("title") or "",
            "selftext": d.get("selftext") or "",
            "score": d.get("score") or 0,
            "num_comments": d.get("num_comments") or 0,
            "author": d.get("author") or "",
            "created_utc": d.get("created_utc") or 0,
            "permalink": d.get("permalink"),
            "url": f"https://www.reddit.com{d['permalink']}",
        })
    return threads, url


def reddit_comments(permalink: str, depth: int = 5, limit: int = 100) -> list[dict]:
    url = f"https://www.reddit.com{permalink}.json?limit={limit}&depth={depth}"
    status, body, _ = http_get(url, ua=REDDIT_UA)
    if status != 200:
        return []
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return []
    # data is a list: [post-listing, comment-listing]
    if not isinstance(data, list) or len(data) < 2:
        return []
    comment_listing = data[1].get("data", {}).get("children", [])
    flat = []

    def walk(node, level=0):
        kind = node.get("kind")
        if kind != "t1":
            return
        d = node.get("data") or {}
        body_text = d.get("body") or ""
        if not body_text:
            return
        flat.append({
            "level": level,
            "author": d.get("author") or "[unknown]",
            "score": d.get("score") or 0,
            "body": body_text,
        })
        replies = d.get("replies")
        if isinstance(replies, dict):
            for child in (replies.get("data") or {}).get("children") or []:
                walk(child, level + 1)

    for c in comment_listing:
        walk(c, 0)
    return flat


def fetch_reddit_for_course(course_num: str, manifest: list) -> None:
    print(f"[reddit] CS {course_num}: searching...", flush=True)
    threads, search_url = reddit_search(course_num)
    time.sleep(REDDIT_SLEEP)

    if not threads:
        placeholder_body = (
            "Reddit's public JSON endpoints currently return HTTP 403 for unauthenticated\n"
            "requests (including the search endpoint and subreddit listing endpoints).\n"
            "Reddit started blocking anonymous JSON access in mid-2023 and the restriction\n"
            "remains in effect. Without OAuth credentials the script cannot fetch threads.\n\n"
            f"Manual collection steps for CS {course_num}:\n"
            f"  1. In a browser, open https://www.reddit.com/r/utdallas/search/?q=CS+{course_num}&restrict_sr=1\n"
            "  2. Open the top 8-12 threads (sort by relevance, then by top of all time).\n"
            "  3. For each thread, copy:\n"
            "       - The post title and body text.\n"
            "       - The top 10-20 comments (skip 'deleted' and joke replies).\n"
            "  4. Paste below the --- divider, one thread per block. Format suggestion:\n"
            "       === THREAD N: <title> ===\n"
            "       url: <permalink>\n"
            "       <selftext>\n"
            "       COMMENTS:\n"
            "       - [author] <comment>\n\n"
            "Alternative: register a script-type app at https://www.reddit.com/prefs/apps,\n"
            "get a client_id + client_secret, then update this script to use the OAuth\n"
            "flow (POST https://www.reddit.com/api/v1/access_token then use the bearer\n"
            "token on oauth.reddit.com URLs). That would unlock automated re-fetches.\n"
        )
        path = write_doc(
            f"reddit_cs{course_num}.txt",
            {
                "Source": f"r/utdallas search 'CS {course_num}'",
                "URL": search_url,
                "Fetched": ts(),
                "Note": "PLACEHOLDER - reddit returned 403 to anonymous JSON. Needs manual paste-in.",
                "Status": "placeholder",
            },
            placeholder_body,
        )
        manifest.append({
            "filename": path.name,
            "source": "reddit",
            "url": search_url,
            "courses": [f"CS {course_num}"],
            "fetched": ts(),
            "status": "placeholder",
            "reason": "anonymous JSON blocked (403)",
        })
        return

    blocks = []
    total_comments = 0
    fetched_threads = 0
    # Cap comment fetches to first 12 threads to stay polite
    for i, th in enumerate(threads):
        head = f"=== THREAD {i+1}: {th['title']} ==="
        meta = (
            f"author: {th['author']} | score: {th['score']} | "
            f"comments: {th['num_comments']} | url: {th['url']}"
        )
        body = th["selftext"].strip() or "(no selftext)"
        comments_section = ""
        if i < 12 and th["num_comments"] > 0:
            comments = reddit_comments(th["permalink"])
            fetched_threads += 1
            total_comments += len(comments)
            if comments:
                lines = []
                for c in comments:
                    indent = "  " * c["level"]
                    lines.append(
                        f"{indent}- [{c['author']} | +{c['score']}] {c['body'].strip()}"
                    )
                comments_section = "\n".join(lines)
            time.sleep(REDDIT_SLEEP)
        block = "\n".join([head, meta, "", body])
        if comments_section:
            block += "\n\nCOMMENTS:\n" + comments_section
        blocks.append(block)

    body_text = "\n\n".join(blocks)
    path = write_doc(
        f"reddit_cs{course_num}.txt",
        {
            "Source": f"r/utdallas search 'CS {course_num}'",
            "URL": search_url,
            "Fetched": ts(),
            "Note": (
                f"{len(threads)} threads from search; comments fetched for "
                f"first {fetched_threads} threads ({total_comments} comments total)"
            ),
        },
        body_text,
    )
    print(f"[reddit] CS {course_num}: wrote {path.name} "
          f"({len(threads)} threads, {total_comments} comments)", flush=True)
    manifest.append({
        "filename": path.name,
        "source": "reddit",
        "url": search_url,
        "courses": [f"CS {course_num}"],
        "fetched": ts(),
        "status": "ok",
        "threads": len(threads),
        "comments": total_comments,
    })


# --- 2. utdgrades ---------------------------------------------------------

def fetch_utdgrades(course_num: str, manifest: list) -> None:
    """
    utdgrades.com is a SPA. The plain HTML returns a JS shell.
    Their data appears to come from a backend (we'll save the HTML if it's
    substantive, otherwise a placeholder).
    """
    url = f"https://utdgrades.com/results/CS%20{course_num}"
    print(f"[utdgrades] CS {course_num}: fetching {url}", flush=True)
    status, body, _ = http_get(url, ua=BROWSER_UA)
    time.sleep(HOST_SLEEP)

    if status != 200 or not body:
        path = write_doc(
            f"utdgrades_cs{course_num}.txt",
            {
                "Source": f"utdgrades.com grades for CS {course_num}",
                "URL": url,
                "Fetched": ts(),
                "Note": f"HTTP {status}. Page could not be retrieved automatically. "
                        f"Manual collection: open URL in browser and paste tabular grade "
                        f"data below the --- divider.",
                "Status": "placeholder",
            },
            "(placeholder -- paste utdgrades A/B/C/D/F table here, by section and term)",
        )
        manifest.append({
            "filename": path.name,
            "source": "utdgrades",
            "url": url,
            "courses": [f"CS {course_num}"],
            "fetched": ts(),
            "status": "placeholder",
        })
        return

    text = strip_html(body.decode("utf-8", errors="replace"))
    # SPA shells tend to be short. If we got <600 chars of useful text, treat as placeholder.
    if len(text) < 600:
        path = write_doc(
            f"utdgrades_cs{course_num}.txt",
            {
                "Source": f"utdgrades.com grades for CS {course_num}",
                "URL": url,
                "Fetched": ts(),
                "Note": "Page is a JS-rendered SPA, plain GET returned the empty shell. "
                        "Manual collection needed: open URL in browser, screenshot or copy "
                        "the grade-distribution table.",
                "Status": "placeholder",
            },
            f"(SPA shell, no data available)\n\nRaw text extracted:\n{text}",
        )
        manifest.append({
            "filename": path.name,
            "source": "utdgrades",
            "url": url,
            "courses": [f"CS {course_num}"],
            "fetched": ts(),
            "status": "placeholder",
        })
        return

    path = write_doc(
        f"utdgrades_cs{course_num}.txt",
        {
            "Source": f"utdgrades.com grades for CS {course_num}",
            "URL": url,
            "Fetched": ts(),
            "Note": "Plain HTML extract. May still need manual cleanup if SPA-rendered.",
        },
        text,
    )
    manifest.append({
        "filename": path.name,
        "source": "utdgrades",
        "url": url,
        "courses": [f"CS {course_num}"],
        "fetched": ts(),
        "status": "ok",
    })


# --- 3. UTD catalog -------------------------------------------------------

def fetch_catalog(course_num: str, manifest: list) -> None:
    url = f"https://catalog.utdallas.edu/now/undergraduate/courses/cs{course_num}"
    print(f"[catalog] CS {course_num}: fetching {url}", flush=True)
    status, body, _ = http_get(url, ua=BROWSER_UA)
    time.sleep(HOST_SLEEP)

    if status != 200 or not body:
        path = write_doc(
            f"catalog_cs{course_num}.txt",
            {
                "Source": f"UTD official catalog (Galaxy) entry for CS {course_num}",
                "URL": url,
                "Fetched": ts(),
                "Note": f"HTTP {status}. Could not retrieve catalog page.",
                "Status": "placeholder",
            },
            "(placeholder -- paste official course description from catalog page here)",
        )
        manifest.append({
            "filename": path.name,
            "source": "catalog",
            "url": url,
            "courses": [f"CS {course_num}"],
            "fetched": ts(),
            "status": "placeholder",
        })
        return

    html_text = body.decode("utf-8", errors="replace")
    # Try to grab just the main content section: catalog pages typically have a
    # <div id="content"> or similar. We'll just strip the whole page and trust
    # the boilerplate is short relative to the body.
    text = strip_html(html_text)
    path = write_doc(
        f"catalog_cs{course_num}.txt",
        {
            "Source": f"UTD official catalog (Galaxy) entry for CS {course_num}",
            "URL": url,
            "Fetched": ts(),
            "Note": "Stripped HTML to plain text. Header/footer nav still present, "
                    "M3 cleaning will trim it.",
        },
        text,
    )
    manifest.append({
        "filename": path.name,
        "source": "catalog",
        "url": url,
        "courses": [f"CS {course_num}"],
        "fetched": ts(),
        "status": "ok",
    })


# --- 4. Rate My Professors ------------------------------------------------

# Target courses for filtering RMP reviews. CS courses the eval set targets.
# Stored as the canonical "CS NNNN" form; normalization compares against the
# whitespace-stripped variants.
TARGET_COURSES = ["CS 3345", "CS 4337", "CS 4347", "CS 4348", "CS 4349", "CS 3354", "CS 4383", "CS 6360", "CS 6363", "CS 6364", "CS 6375"]
# Set of normalized course codes (no space) used for fast lookup.
_TARGET_COURSE_KEYS = {c.replace(" ", "") for c in TARGET_COURSES}
# CS-courses that are cross-listed with SE prefix per the UTD catalog
# (e.g. SE 3345 == CS 3345). When we see SE3345 we treat it as CS3345.
_SE_CROSSLISTED = {"3345"}
# Drop ratings older than this date (ISO YYYY-MM-DD). RMP date strings come as
# "YYYY-MM-DD HH:MM:SS +0000 UTC"; we compare the first 10 chars.
MIN_RATING_DATE = "2023-01-01"
# Cap on how many CS-department profs we'll fetch detail for (after dept filter).
RMP_MAX_CS_PROFS = 150
# Per-search-page size for the teacher pagination.
RMP_PAGE_SIZE = 20
# Cap on total search pages to scan (each page yields ~20 profs across all depts;
# at UTD roughly 1 in 4-5 is CS, so 35 pages -> ~700 profs scanned -> ~140 CS).
RMP_MAX_SEARCH_PAGES = 40
# Cap on profs surfaced in the output file (sorted by surviving-rating count desc).
RMP_MAX_OUTPUT_PROFS = 50


def normalize_class_code(raw: str | None) -> str:
    """Normalize a rating's class field to a canonical CS-prefixed code or empty.

    Rules:
      - Uppercase, strip whitespace and dots.
      - Drop trailing section suffix like ".001", "-001", "_001" (2-3 digits)
        or honors / IOT / etc tags after the course number ("CS 3345.HON",
        "CS3345HON", "CS4301IOT" -> CS3345 / CS3345 / CS4301 respectively).
      - If the result starts with "SE" + a 4-digit course number that is
        cross-listed with CS at UTD (currently SE 3345), rewrite as the CS form.

    Returns "" if the input doesn't parse as a 4-digit-numbered course.

    Examples:
      CS3345     -> CS3345
      cs 3345    -> CS3345
      CS3345.001 -> CS3345
      CS 3345.HON -> CS3345
      SE3345     -> CS3345 (cross-listed)
      CS4301IOT  -> CS4301 (not a target, will be filtered later)
    """
    if not raw:
        return ""
    # uppercase, drop whitespace, dots, and hyphens/underscores
    s = re.sub(r"[\s.\-_]+", "", str(raw)).upper()
    # match leading prefix (letters) then 4 digits, allow anything trailing
    m = re.match(r"^([A-Z]{2,4})(\d{4})", s)
    if not m:
        return ""
    prefix, number = m.group(1), m.group(2)
    # cross-listed SE -> CS
    if prefix == "SE" and number in _SE_CROSSLISTED:
        prefix = "CS"
    return f"{prefix}{number}"


def _rmp_post(payload: dict, *, headers: dict) -> tuple[int, dict | None]:
    """POST to RMP GraphQL and parse JSON, returning (status, parsed-or-None)."""
    status, body, _ = http_post_json(
        "https://www.ratemyprofessors.com/graphql",
        payload,
        ua=BROWSER_UA,
        extra_headers=headers,
    )
    if status != 200 or not body:
        return status, None
    try:
        return status, json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return status, None


def try_rmp(manifest: list, budget_deadline: float) -> None:
    """
    Pull UTD CS-department profs from RMP, then filter their recent reviews to
    the 5 target courses. Writes a course-tag-aligned file. Falls back to a
    placeholder if RMP blocks us.
    """
    school_id = "U2Nob29sLTEyNzM="  # base64("School-1273")
    headers = {
        "Authorization": "Basic dGVzdDp0ZXN0",
        "Origin": "https://www.ratemyprofessors.com",
        "Referer": "https://www.ratemyprofessors.com/",
    }

    search_query = (
        "query NewSearchTeachersQuery($q: TeacherSearchQuery!, $count: Int!, $cursor: String) {"
        "  newSearch {"
        "    teachers(query: $q, first: $count, after: $cursor) {"
        "      pageInfo { hasNextPage endCursor }"
        "      edges { node {"
        "        firstName lastName avgRating avgDifficulty numRatings department legacyId id"
        "      } }"
        "    }"
        "  }"
        "}"
    )
    ratings_query = (
        "query TeacherRatings($id: ID!) {"
        "  node(id: $id) {"
        "    ... on Teacher {"
        "      firstName lastName avgRating avgDifficulty numRatings department"
        "      ratings(first: 50) { edges { node {"
        "        class comment difficultyRating clarityRating helpfulRating"
        "        wouldTakeAgain date"
        "      } } }"
        "    }"
        "  }"
        "}"
    )

    # --- step 1: paginate and collect CS-department profs ----------------
    print("[rmp] paginating UTD profs, filtering to Computer Science...", flush=True)
    cs_profs: list[dict] = []
    cursor: str | None = None
    pages_scanned = 0
    total_scanned = 0
    for page in range(RMP_MAX_SEARCH_PAGES):
        if time.time() > budget_deadline:
            print("[rmp] budget exhausted during pagination", flush=True)
            break
        status, data = _rmp_post(
            {
                "query": search_query,
                "variables": {
                    "q": {"text": "", "schoolID": school_id, "fallback": True},
                    "count": RMP_PAGE_SIZE,
                    "cursor": cursor,
                },
            },
            headers=headers,
        )
        if status != 200 or data is None:
            if page == 0:
                write_rmp_placeholder(manifest, reason=f"search HTTP {status}")
                return
            print(f"[rmp] search page {page+1} HTTP {status}, stopping pagination", flush=True)
            break
        teachers = (((data.get("data") or {}).get("newSearch") or {}).get("teachers") or {})
        edges = teachers.get("edges") or []
        page_info = teachers.get("pageInfo") or {}
        pages_scanned += 1
        total_scanned += len(edges)
        for e in edges:
            n = e.get("node") or {}
            if (n.get("department") or "").strip().lower() == "computer science":
                cs_profs.append(n)
        if len(cs_profs) >= RMP_MAX_CS_PROFS:
            print(f"[rmp] reached cap of {RMP_MAX_CS_PROFS} CS profs", flush=True)
            break
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        time.sleep(1.0)

    print(f"[rmp] scanned {total_scanned} profs over {pages_scanned} pages, "
          f"found {len(cs_profs)} CS profs", flush=True)
    if not cs_profs:
        write_rmp_placeholder(manifest, reason="no CS-department profs found")
        return

    # --- step 2: fetch ratings per prof, filter to target courses --------
    # Sort by numRatings descending so highest-volume profs come first.
    # This way, if the budget runs out, we've already captured the heaviest hitters.
    cs_profs.sort(key=lambda p: -(p.get("numRatings") or 0))
    profs_with_hits: list[dict] = []
    per_course_counts: dict[str, int] = {c: 0 for c in _TARGET_COURSE_KEYS}
    total_kept = 0
    for i, prof in enumerate(cs_profs):
        if time.time() > budget_deadline:
            print(f"[rmp] budget exhausted after {i} prof detail fetches", flush=True)
            break
        node_id = prof.get("id")
        if not node_id:
            continue
        status, data = _rmp_post(
            {"query": ratings_query, "variables": {"id": node_id}},
            headers=headers,
        )
        time.sleep(1.2)
        if status != 200 or data is None:
            continue
        node = (data.get("data") or {}).get("node") or {}
        all_ratings = (node.get("ratings") or {}).get("edges") or []
        kept = []
        for edge in all_ratings:
            r = edge.get("node") or {}
            cls_norm = normalize_class_code(r.get("class"))
            if cls_norm not in _TARGET_COURSE_KEYS:
                continue
            date_str = (r.get("date") or "")[:10]
            if date_str and date_str < MIN_RATING_DATE:
                continue
            kept.append({"_cls": cls_norm, **r})
        if kept:
            # sort newest-first inside this prof's block
            kept.sort(key=lambda r: (r.get("date") or ""), reverse=True)
            profs_with_hits.append({"prof": prof, "node": node, "kept": kept})
            total_kept += len(kept)
            for k in kept:
                per_course_counts[k["_cls"]] = per_course_counts.get(k["_cls"], 0) + 1
            print(f"[rmp]   {prof.get('firstName')} {prof.get('lastName')}: "
                  f"{len(kept)} target-course reviews kept", flush=True)

    # --- step 3: write output --------------------------------------------
    if not profs_with_hits:
        write_rmp_placeholder(
            manifest,
            reason=(f"scanned {len(cs_profs)} CS profs, none had recent ratings "
                    f"tagged to target courses"),
        )
        return

    # Sort by count of target-matching reviews desc, then cap to top N profs.
    profs_with_hits.sort(key=lambda p: len(p["kept"]), reverse=True)
    if len(profs_with_hits) > RMP_MAX_OUTPUT_PROFS:
        dropped = profs_with_hits[RMP_MAX_OUTPUT_PROFS:]
        profs_with_hits = profs_with_hits[:RMP_MAX_OUTPUT_PROFS]
        # recompute totals from the surviving profs only
        per_course_counts = {c: 0 for c in _TARGET_COURSE_KEYS}
        total_kept = 0
        for item in profs_with_hits:
            for k in item["kept"]:
                per_course_counts[k["_cls"]] = per_course_counts.get(k["_cls"], 0) + 1
                total_kept += 1
        print(f"[rmp] capped at top {RMP_MAX_OUTPUT_PROFS} profs "
              f"(dropped {len(dropped)} smaller-volume profs)", flush=True)

    blocks = []
    for item in profs_with_hits:
        prof = item["prof"]
        node = item["node"]
        legacy = prof.get("legacyId")
        head = (
            f"=== {node.get('firstName') or prof.get('firstName')} "
            f"{node.get('lastName') or prof.get('lastName')} (legacyId {legacy}) ==="
        )
        meta = (
            f"avgRating: {node.get('avgRating')} | "
            f"avgDifficulty: {node.get('avgDifficulty')} | "
            f"numRatings: {node.get('numRatings')} (all-time) | "
            f"dept: {node.get('department')}"
        )
        review_lines = []
        for r in item["kept"]:
            comment_clean = (r.get("comment") or "").strip()
            # collapse 3+ blank lines inside a comment but preserve the
            # author's original text otherwise
            comment_clean = re.sub(r"\n{3,}", "\n\n", comment_clean)
            review_lines.append(
                f"- class={r['_cls']} | diff={r.get('difficultyRating')} | "
                f"clarity={r.get('clarityRating')} | help={r.get('helpfulRating')} | "
                f"wta={r.get('wouldTakeAgain')} | date={r.get('date')}\n"
                f"  {comment_clean}"
            )
        blocks.append("\n".join([head, meta] + review_lines))

    # Build the canonical "CS 3345: n1 | CS 4337: n2 | ..." line in the
    # order TARGET_COURSES lists them.
    coverage_line = " | ".join(
        f"{c}: {per_course_counts.get(c.replace(' ', ''), 0)}" for c in TARGET_COURSES
    )
    target_courses_covered = [
        c for c in TARGET_COURSES
        if per_course_counts.get(c.replace(" ", ""), 0) > 0
    ]
    missing_courses = [c for c in TARGET_COURSES if c not in target_courses_covered]
    note_extra = ""
    if missing_courses:
        note_extra = (
            " Coverage gap: no recent RMP reviews surfaced for "
            + ", ".join(missing_courses) + "."
        )

    path = write_doc(
        "rmp_utd_cs.txt",
        {
            "Source": "Rate My Professors (UTD, schoolID 1273) -- profs teaching target courses",
            "URL": "https://www.ratemyprofessors.com/school/1273",
            "Fetched": ts(),
            "Note": (
                f"UTD CS-dept profs filtered to ratings tagged with "
                f"CS 3345 / 4337 / 4347 / 4348 / 4349 (incl. cross-listed SE prefix) "
                f"and date >= {MIN_RATING_DATE}." + note_extra
            ),
            "Target courses": ", ".join(TARGET_COURSES),
            "Profs returned": len(profs_with_hits),
            "Total ratings kept": total_kept,
            "Coverage": coverage_line,
        },
        "\n\n".join(blocks),
    )
    print(f"[rmp] wrote {path.name}: {len(profs_with_hits)} profs, "
          f"{total_kept} target-course reviews kept", flush=True)
    manifest.append({
        "filename": path.name,
        "source": "rmp",
        "url": "https://www.ratemyprofessors.com/school/1273",
        "courses": ["CS 3345", "CS 4337", "CS 4347", "CS 4348", "CS 4349"],
        "fetched": ts(),
        "status": "ok",
        "profs": len(profs_with_hits),
        "total_ratings_kept": total_kept,
        "per_course": {c.replace(" ", ""): per_course_counts.get(c.replace(" ", ""), 0)
                        for c in TARGET_COURSES},
        "target_courses_covered": target_courses_covered,
        "cs_profs_scanned": len(cs_profs),
    })


def write_rmp_placeholder(manifest: list, *, reason: str) -> None:
    body = (
        "RMP is JS-rendered and aggressively bot-blocked. Automated fetch failed.\n"
        f"Reason: {reason}\n\n"
        "Manual collection steps:\n"
        "  1. In a browser, open https://www.ratemyprofessors.com/search/professors/1273\n"
        "  2. Sort by number of ratings (descending).\n"
        "  3. For the top 5-10 CS professors, click into each prof page and copy:\n"
        "       - Name, overall quality, difficulty, would-take-again %.\n"
        "       - The 15-20 most recent written reviews (the comment text + the\n"
        "         tagged course code like 'CS 3345').\n"
        "  4. Paste below the --- divider in this file, one prof per block.\n"
        "     Keep a 'PROF: Last, First' line before each prof's reviews so the\n"
        "     chunker can tag chunks correctly.\n"
    )
    path = write_doc(
        "rmp_placeholder.txt",
        {
            "Source": "Rate My Professors (UTD, schoolID 1273) -- top CS professors",
            "URL": "https://www.ratemyprofessors.com/school/1273",
            "Fetched": ts(),
            "Note": "PLACEHOLDER - needs manual paste-in. RMP blocked auto fetch.",
            "Status": "placeholder",
        },
        body,
    )
    manifest.append({
        "filename": path.name,
        "source": "rmp",
        "url": "https://www.ratemyprofessors.com/school/1273",
        "courses": ["CS 3345", "CS 4337", "CS 4348", "CS 4349", "CS 4347"],
        "fetched": ts(),
        "status": "placeholder",
        "reason": reason,
    })


# --- 5. trends.utdnebula via api.utdnebula.com ----------------------------

def try_nebula(manifest: list, budget_deadline: float) -> None:
    """Try a couple of likely endpoints. If nothing works, write a placeholder."""
    candidates = [
        "https://api.utdnebula.com/grades?courses=CS+3345",
        "https://api.utdnebula.com/grades/semester?course=CS%203345",
        "https://api.utdnebula.com/course?subject_prefix=CS&course_number=3345",
        "https://api.utdnebula.com/sections?subject_prefix=CS&course_number=3345",
    ]
    last_status = None
    last_body = None
    last_url = None
    for url in candidates:
        if time.time() > budget_deadline:
            break
        print(f"[nebula] probing {url}", flush=True)
        status, body, _ = http_get(url, ua=BROWSER_UA, timeout=15)
        time.sleep(HOST_SLEEP)
        last_status = status
        last_body = body
        last_url = url
        if status == 200 and body:
            try:
                parsed = json.loads(body.decode("utf-8", errors="replace"))
                text_len = len(json.dumps(parsed))
                if text_len > 200:
                    # We got something. Save it.
                    path = write_doc(
                        "trends_nebula.txt",
                        {
                            "Source": "trends.utdnebula.com via api.utdnebula.com",
                            "URL": url,
                            "Fetched": ts(),
                            "Note": "Probe success. Raw JSON response saved as text. "
                                    "Re-run script with the working endpoint to pull "
                                    "the other 4 courses.",
                        },
                        json.dumps(parsed, indent=2)[:200000],
                    )
                    manifest.append({
                        "filename": path.name,
                        "source": "nebula",
                        "url": url,
                        "courses": ["CS 3345"],
                        "fetched": ts(),
                        "status": "ok",
                    })
                    return
            except Exception:
                pass
    # No endpoint worked.
    body_text = (
        "trends.utdnebula.com is a Next.js frontend backed by api.utdnebula.com.\n"
        "Probed endpoints (none returned usable course-grade data with a plain GET):\n"
        + "\n".join(f"  - {u}" for u in candidates)
        + f"\nLast status: HTTP {last_status} for {last_url}\n\n"
        "Manual collection options:\n"
        "  1. Open https://trends.utdnebula.com/dashboard/ in a browser.\n"
        "  2. Search each course (CS 3345, CS 4337, CS 4348, CS 4349, CS 4347).\n"
        "  3. For each, copy the GPA-by-prof table and per-semester GPA trend.\n"
        "  4. Paste below the --- divider, one course per block, marked\n"
        "     'COURSE: CS <num>' so the chunker can tag it.\n"
        "  Alternative: inspect Network tab while loading a course page to find\n"
        "  the actual API call that returns the grade JSON, then update this script.\n"
    )
    path = write_doc(
        "trends_placeholder.txt",
        {
            "Source": "trends.utdnebula.com (backed by api.utdnebula.com)",
            "URL": "https://trends.utdnebula.com/",
            "Fetched": ts(),
            "Note": "PLACEHOLDER - needs manual paste-in. API endpoint discovery failed.",
            "Status": "placeholder",
        },
        body_text,
    )
    manifest.append({
        "filename": path.name,
        "source": "nebula",
        "url": "https://trends.utdnebula.com/",
        "courses": ["CS 3345", "CS 4337", "CS 4348", "CS 4349", "CS 4347"],
        "fetched": ts(),
        "status": "placeholder",
    })


# --- main -----------------------------------------------------------------

def main() -> int:
    print(f"Fetching into {DOCS}", flush=True)
    manifest: list[dict] = []

    # 1. reddit per course
    for c in COURSES:
        fetch_reddit_for_course(c, manifest)

    # 2. utdgrades per course
    for c in COURSES:
        fetch_utdgrades(c, manifest)

    # 3. catalog per course
    for c in COURSES:
        fetch_catalog(c, manifest)

    # 4 + 5. Optional sources under a shared hard time budget
    optional_start = time.time()
    deadline = optional_start + HARD_OPTIONAL_BUDGET
    try_rmp(manifest, deadline)
    try_nebula(manifest, deadline)

    # write manifest
    manifest_path = DOCS / "sources.json"
    manifest_path.write_text(
        json.dumps({"generated": ts(), "files": manifest}, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote manifest: {manifest_path}", flush=True)
    print(f"Total files in manifest: {len(manifest)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
