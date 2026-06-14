"""Hybrid retriever: lexical BM25 + (optional) Voyage semantic vectors.

Why hybrid: pure embeddings miss exact tokens that matter in hardware programs —
part numbers, test IDs like "VP-003", "ATE". Pure keyword misses paraphrase. We run
both and combine with Reciprocal Rank Fusion (RRF), which needs no score calibration
between the two very different scales.

When the index has no embeddings (EMBED_PROVIDER=local, or built without a Voyage key),
this degrades cleanly to BM25-only — still real retrieval, just lexical.

Every hit comes back as a Citation carrying the source title, date, and type, so the
grounding contract holds all the way up to the agents.
"""

import json
import os
import re

from config import INDEX_DIR, POOL_K, RRF_K, TOP_K
from embeddings import get_embedder
from schemas import Chunk, Citation

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [w for w in _TOKEN.findall(text.lower()) if len(w) > 1]


def _snippet(text: str, n: int = 400) -> str:
    s = " ".join(text.split())
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " …"


class Retriever:
    def __init__(self):
        from rank_bm25 import BM25Okapi

        path = os.path.join(INDEX_DIR, "chunks.jsonl")
        if not os.path.isfile(path):
            raise SystemExit("No index found. Run `python ingest.py` first.")
        self.chunks: list[Chunk] = [
            Chunk.from_json(json.loads(line)) for line in open(path, encoding="utf-8")]
        self.bm25 = BM25Okapi([tokenize(c.text) for c in self.chunks])

        # Load semantic vectors if the index has them AND a query embedder is available.
        self.emb = None
        self.embedder = None
        emb_path = os.path.join(INDEX_DIR, "embeddings.npy")
        if os.path.exists(emb_path):
            embedder = get_embedder()
            if embedder is not None:
                import numpy as np
                arr = np.load(emb_path)
                norms = np.linalg.norm(arr, axis=1, keepdims=True)
                self.emb = arr / np.clip(norms, 1e-8, None)   # pre-normalize for cosine = dot
                self.embedder = embedder

    @property
    def mode(self) -> str:
        return "hybrid (BM25 + vectors)" if self.emb is not None else "lexical (BM25)"

    def _pool(self, workstream, since, source_type) -> list[int]:
        out = []
        for i, c in enumerate(self.chunks):
            if source_type and c.source_type != source_type:
                continue
            if workstream and (c.workstream or "").lower() != workstream.lower():
                continue
            if since and c.date and c.date < since:
                continue
            out.append(i)
        return out

    def search(self, query, k=TOP_K, workstream=None, since=None, source_type=None) -> list[Citation]:
        pool = self._pool(workstream, since, source_type)
        if not pool:
            return []

        # --- Lexical ranking over the candidate pool ---
        scores = self.bm25.get_scores(tokenize(query))
        bm25_order = sorted(pool, key=lambda i: scores[i], reverse=True)[:POOL_K]

        # --- Reciprocal Rank Fusion ---
        fused: dict[int, float] = {}
        for rank, idx in enumerate(bm25_order):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank)

        if self.emb is not None:
            import numpy as np
            qv = np.asarray(self.embedder([query], input_type="query")[0], dtype="float32")
            qv = qv / max(float(np.linalg.norm(qv)), 1e-8)
            sims = self.emb[pool] @ qv
            vec_order = [pool[j] for j in np.argsort(-sims)[:POOL_K]]
            for rank, idx in enumerate(vec_order):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank)

        top = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
        cites = []
        for idx, score in top:
            c = self.chunks[idx]
            cites.append(Citation(chunk_id=c.chunk_id, title=c.title, source_type=c.source_type,
                                  date=c.date, text=c.text, snippet=_snippet(c.text),
                                  score=round(score, 5), workstream=c.workstream))
        return cites


_RETRIEVER = None


def get_retriever() -> Retriever:
    """Process-wide singleton so the index loads once."""
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = Retriever()
    return _RETRIEVER
