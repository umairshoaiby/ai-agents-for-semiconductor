"""Embeddings provider switch.

Two modes, chosen by EMBED_PROVIDER in config:

  * "local"  -> returns None. There is no torch/onnx on Windows ARM64, so the local
                path is honest lexical retrieval (BM25 only) — no key, runs anywhere.
  * "voyage" -> returns an embed() callable backed by Voyage AI (pure HTTP, no native
                deps), enabling semantic hybrid retrieval. Needs VOYAGE_API_KEY.

Keeping the provider behind one function means ingest.py and retriever.py never care
which backend is active — exactly the model-portability pattern used elsewhere in the
portfolio, applied to embeddings.
"""

import os

from config import EMBED_MODEL, EMBED_PROVIDER

# Voyage output dims (voyage-3 family = 1024). Only used for sanity checks.
_VOYAGE_DIM = 1024


def get_embedder():
    """Return an embed(texts, input_type) -> list[list[float]] callable, or None for BM25-only.

    `input_type` is "document" when indexing and "query" when searching — Voyage uses it
    to asymmetrically encode the two, which improves retrieval.
    """
    if EMBED_PROVIDER != "voyage":
        return None

    if not os.environ.get("VOYAGE_API_KEY"):
        # Asked for semantic mode but no key — fall back to lexical rather than crash.
        print("[embeddings] EMBED_PROVIDER=voyage but VOYAGE_API_KEY is not set; "
              "falling back to lexical BM25-only retrieval.")
        return None

    import voyageai

    client = voyageai.Client()

    def embed(texts, input_type="document"):
        # Voyage caps batch size; chunk the request to be safe.
        out = []
        for i in range(0, len(texts), 100):
            batch = texts[i:i + 100]
            res = client.embed(batch, model=EMBED_MODEL, input_type=input_type)
            out.extend(res.embeddings)
        return out

    embed.name = EMBED_MODEL
    embed.dim = _VOYAGE_DIM
    return embed
