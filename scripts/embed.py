"""Embed all corpus chunks into a ChromaDB collection.

Loads chunks via chunk.chunk_corpus(), embeds each chunk with
sentence-transformers all-MiniLM-L6-v2 (local, no API), and writes them to a
persistent ChromaDB collection at chroma_db/ using cosine distance so the
score is interpretable as "1 - cosine similarity" (0=identical, 1=orthogonal,
2=opposite).

Idempotent: re-running drops the existing collection and rebuilds. The
collection name is fixed so retrieve.py can find it without coordination.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Make `scripts/` importable when this file is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from chunk import Chunk, chunk_corpus


ROOT = Path(__file__).resolve().parent.parent
CHROMA_DIR = ROOT / "chroma_db"
COLLECTION_NAME = "utd_unofficial_guide"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBED_BATCH_SIZE = 64
CHROMA_ADD_BATCH_SIZE = 256  # Chroma has a per-call payload size limit


def _flatten_metadata(meta: dict) -> dict:
    """ChromaDB metadata fields must be primitives (str/int/float/bool).

    Drop None values and stringify anything else. Numeric strings stay
    strings so the metadata reads back consistently.
    """
    out: dict = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _make_id(chunk: Chunk, idx: int) -> str:
    """Stable per-chunk id based on source file + position or chunk index."""
    meta = chunk.metadata
    source_file = meta.get("source_file", "unknown")
    # For RMP, prof + date is the natural key. For others, use the position.
    if meta.get("source") == "rmp":
        prof = meta.get("prof_legacy_id", "?")
        date = meta.get("date", "?")
        klass = meta.get("course", "?").replace(" ", "")
        return f"rmp_{prof}_{klass}_{idx}_{date[:10]}"
    pos = meta.get("position", idx)
    return f"{source_file}_{pos}_{idx}"


def build_index(verbose: bool = True) -> int:
    """Embed all chunks and write them to ChromaDB. Returns chunk count."""
    chunks = chunk_corpus()
    if not chunks:
        raise RuntimeError("No chunks to embed. Check ingest.py / chunk.py output.")
    if verbose:
        print(f"Got {len(chunks)} chunks from chunk_corpus()")

    model = SentenceTransformer(EMBEDDING_MODEL)
    if verbose:
        print(f"Loaded embedding model: {EMBEDDING_MODEL}")

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    # Drop and recreate so re-runs are clean (no stale chunks left behind).
    existing = [c.name for c in client.list_collections()]
    if COLLECTION_NAME in existing:
        client.delete_collection(COLLECTION_NAME)
        if verbose:
            print(f"Dropped existing collection {COLLECTION_NAME!r}")
    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},  # cosine distance in [0, 2]
    )

    # Embed in batches for speed.
    t0 = time.time()
    texts = [c.text for c in chunks]
    embeddings = model.encode(
        texts,
        batch_size=EMBED_BATCH_SIZE,
        show_progress_bar=verbose,
        convert_to_numpy=True,
        normalize_embeddings=True,  # cosine distance works on normalized vectors
    )
    embed_secs = time.time() - t0
    if verbose:
        print(f"Embedded {len(chunks)} chunks in {embed_secs:.1f}s "
              f"({len(chunks)/embed_secs:.0f} chunks/sec)")

    # Add to Chroma in batches.
    ids = [_make_id(c, i) for i, c in enumerate(chunks)]
    # Dedupe ids defensively (RMP duplicates can sneak in if a prof has multiple
    # reviews with identical date + course).
    seen: dict[str, int] = {}
    dedup_ids: list[str] = []
    for cid in ids:
        if cid in seen:
            seen[cid] += 1
            dedup_ids.append(f"{cid}#{seen[cid]}")
        else:
            seen[cid] = 0
            dedup_ids.append(cid)
    metadatas = [_flatten_metadata(c.metadata) for c in chunks]

    t1 = time.time()
    for start in range(0, len(chunks), CHROMA_ADD_BATCH_SIZE):
        end = start + CHROMA_ADD_BATCH_SIZE
        collection.add(
            ids=dedup_ids[start:end],
            documents=texts[start:end],
            embeddings=embeddings[start:end].tolist(),
            metadatas=metadatas[start:end],
        )
    add_secs = time.time() - t1
    if verbose:
        print(f"Wrote {len(chunks)} records to ChromaDB in {add_secs:.1f}s")
        print(f"Persisted to: {CHROMA_DIR}")
    return len(chunks)


def main() -> int:
    n = build_index(verbose=True)
    print(f"\nDone. Collection {COLLECTION_NAME!r} contains {n} chunks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
