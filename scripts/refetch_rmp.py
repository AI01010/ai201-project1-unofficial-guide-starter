"""
Targeted refetch driver: re-runs ONLY the RMP step from fetch_initial.py and
patches the rmp_utd_cs.txt entry inside documents/sources.json. Leaves all
other sources (reddit, utdgrades, catalog, nebula) and their manifest entries
untouched.

Why this exists: the M1 first-pass picked profs by all-time numRatings and
returned historical heavyweights who don't teach the target courses anymore.
This driver replays the new course-tag-aligned RMP logic in fetch_initial.py
without re-fetching reddit/utdgrades/catalog (which take longer and already
have their content).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Make fetch_initial importable as a sibling module.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fetch_initial import (  # noqa: E402
    try_rmp,
    ts,
    DOCS,
    HARD_OPTIONAL_BUDGET,
)


def main() -> int:
    print(f"Refetching RMP only -> {DOCS}", flush=True)

    # Collect the new manifest fragment for the rmp file.
    rmp_manifest: list[dict] = []
    deadline = time.time() + HARD_OPTIONAL_BUDGET
    try_rmp(rmp_manifest, deadline)

    if not rmp_manifest:
        print("[refetch_rmp] try_rmp produced no manifest entry, aborting", flush=True)
        return 1

    # Find the rmp_utd_cs.txt entry (try_rmp could have written a placeholder
    # under a different filename if blocked; handle both).
    new_entry = next(
        (m for m in rmp_manifest if m.get("filename") == "rmp_utd_cs.txt"),
        rmp_manifest[0],
    )

    # Splice into the existing sources.json: replace any prior rmp entry,
    # bump the top-level generated timestamp, and leave the rest alone.
    manifest_path = DOCS / "sources.json"
    if not manifest_path.exists():
        print(f"[refetch_rmp] {manifest_path} missing, writing fresh manifest", flush=True)
        manifest_path.write_text(
            json.dumps({"generated": ts(), "files": [new_entry]}, indent=2),
            encoding="utf-8",
        )
        return 0

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = data.get("files") or []
    # drop any prior rmp entry (file may have been rmp_utd_cs.txt or rmp_placeholder.txt)
    files = [f for f in files if f.get("source") != "rmp"]
    files.append(new_entry)
    data["files"] = files
    data["generated"] = ts()
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[refetch_rmp] updated {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
