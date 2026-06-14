"""Build the retrieval index from the program corpus.

Loads every source (meetings, prior weekly updates, specs, Jira export, action-log
history), splits each into provenance-tagged chunks, optionally embeds them (Voyage),
and persists three small artifacts to index/:

  chunks.jsonl    one JSON line per chunk (text + metadata)  — used by BM25
  embeddings.npy  numpy [n, dim] aligned to chunks order     — only when EMBED_PROVIDER=voyage
  meta.json       provider/model/count, so the retriever knows what it's loading

Re-run any time (idempotent): the index is fully rebuilt from corpus/.

Usage:
    python ingest.py
"""

import csv
import hashlib
import json
import os
import re
import sys

from config import CHUNK_MAX_CHARS, CORPUS_DIR, INDEX_DIR
from embeddings import get_embedder
from schemas import Chunk, Document

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def _date_from_name(name: str):
    m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else None


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# Load every source into Documents
# --------------------------------------------------------------------------- #

def load_documents(corpus_dir: str) -> list[Document]:
    docs: list[Document] = []

    def add_folder(sub, source_type):
        d = os.path.join(corpus_dir, sub)
        if not os.path.isdir(d):
            return
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith((".txt", ".md")):
                continue
            name = os.path.splitext(fn)[0]
            docs.append(Document(
                doc_id=f"{source_type}:{name}", text=_read(os.path.join(d, fn)),
                source_type=source_type, title=name.replace("_", " "),
                date=_date_from_name(name)))

    add_folder("meetings", "meeting")
    add_folder("weekly_updates", "weekly_update")
    add_folder("specs", "spec")

    # Jira export -> one document per ticket (so a ticket is independently retrievable).
    jira = os.path.join(corpus_dir, "jira_export.csv")
    if os.path.isfile(jira):
        for row in csv.DictReader(open(jira, encoding="utf-8")):
            key = row.get("issue_key", "")
            text = (f"{key} [{row.get('component', '')}] {row.get('summary', '')}. "
                    f"Status={row.get('status', '')}, result={row.get('test_result', '')}, "
                    f"priority={row.get('priority', '')}. Updated {row.get('updated', '')}.")
            docs.append(Document(doc_id=f"jira:{key}", text=text, source_type="jira",
                                 title=key, date=row.get("updated") or None,
                                 workstream=row.get("component") or None))

    # Action-log history -> one document per action (state changes over time).
    hist = os.path.join(corpus_dir, "action_log_history.csv")
    if os.path.isfile(hist):
        for row in csv.DictReader(open(hist, encoding="utf-8")):
            text = (f"Action {row.get('id', '')}: {row.get('action', '')} "
                    f"(owner {row.get('owner', '') or 'unassigned'}, state {row.get('state', '')}, "
                    f"due {row.get('due', '') or '-'}, raised {row.get('raised_date', '')}). "
                    f"Workstream {row.get('workstream', '')}.")
            docs.append(Document(doc_id=f"action:{row.get('id', '')}", text=text,
                                 source_type="action", title=row.get("id", "action"),
                                 date=row.get("raised_date") or None,
                                 workstream=row.get("workstream") or None))
    return docs


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #

def chunk_document(doc: Document) -> list[Chunk]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", doc.text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if buf and len(buf) + len(p) + 1 > CHUNK_MAX_CHARS:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    if not chunks:
        chunks = [doc.text.strip()]

    out = []
    for i, c in enumerate(chunks):
        out.append(Chunk(chunk_id=f"{doc.doc_id}#{i}:{_hash(c)}", text=c,
                         source_type=doc.source_type, title=doc.title,
                         date=doc.date, workstream=doc.workstream))
    return out


# --------------------------------------------------------------------------- #
# Build + persist
# --------------------------------------------------------------------------- #

def build() -> dict:
    docs = load_documents(CORPUS_DIR)
    chunks = [c for d in docs for c in chunk_document(d)]
    if not chunks:
        raise SystemExit(f"No documents found under {CORPUS_DIR}. Add corpus files first.")

    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(os.path.join(INDEX_DIR, "chunks.jsonl"), "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c.to_json()) + "\n")

    emb_path = os.path.join(INDEX_DIR, "embeddings.npy")
    embedder = get_embedder()
    meta = {"count": len(chunks), "docs": len(docs),
            "provider": "voyage" if embedder else "local"}

    if embedder:
        import numpy as np
        vecs = embedder([c.text for c in chunks], input_type="document")
        arr = np.asarray(vecs, dtype="float32")
        np.save(emb_path, arr)
        meta["model"] = embedder.name
        meta["dim"] = int(arr.shape[1])
    elif os.path.exists(emb_path):
        os.remove(emb_path)            # stale vectors from a previous voyage build

    json.dump(meta, open(os.path.join(INDEX_DIR, "meta.json"), "w"), indent=2)
    return meta


def main() -> None:
    meta = build()
    print(f"Indexed {meta['count']} chunks from {meta['docs']} documents "
          f"[provider: {meta['provider']}"
          + (f", model: {meta['model']}, dim: {meta['dim']}" if meta["provider"] == "voyage" else "")
          + f"] -> {INDEX_DIR}")
    if meta["provider"] == "local":
        print("  (lexical BM25 index; set EMBED_PROVIDER=voyage + VOYAGE_API_KEY for semantic hybrid)")


if __name__ == "__main__":
    main()
