"""Central configuration for the Program Intelligence Copilot.

Model ids, the embeddings provider switch, index location, and retrieval knobs all
live here so the system is portable and easy to retune without touching logic.

Platform note: this example targets Windows ARM64, where chromadb / torch / onnxruntime
have no wheels. Retrieval is therefore a dependency-light, pure-Python + numpy hybrid:
lexical BM25 always, plus optional Voyage AI semantic vectors over HTTP. "local" mode
needs no keys and runs anywhere.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# --- Models ----------------------------------------------------------------- #
# MODEL          — answering, synthesis, and any agent that juggles several tools AND emits
#                  structured output (analyst, risk, critic). The capable model is required
#                  here: in testing the small model leaked tool-call markup and split arrays
#                  into characters when doing multi-tool + structured submit together.
# SUBAGENT_MODEL — reserved for simple, single-shot helpers (a one-claim check, a label) where
#                  the small model is reliable and cheaper.
MODEL = os.environ.get("PIC_MODEL", "claude-opus-4-8")
ANALYST_MODEL = os.environ.get("PIC_ANALYST_MODEL", MODEL)
SUBAGENT_MODEL = os.environ.get("PIC_SUBAGENT_MODEL", "claude-haiku-4-5")

# --- Embeddings ------------------------------------------------------------- #
# "local"  -> lexical BM25 only (no key, no vectors). Honest, runs anywhere.
# "voyage" -> hybrid BM25 + Voyage semantic vectors (needs VOYAGE_API_KEY).
EMBED_PROVIDER = os.environ.get("EMBED_PROVIDER", "local").lower()
EMBED_MODEL = os.environ.get("EMBED_MODEL", "voyage-3")

# --- Index location --------------------------------------------------------- #
HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS_DIR = os.environ.get("PIC_CORPUS", os.path.join(HERE, "corpus"))
INDEX_DIR = os.environ.get("PIC_INDEX", os.path.join(HERE, "index"))

# --- Retrieval knobs -------------------------------------------------------- #
TOP_K = int(os.environ.get("PIC_TOP_K", "6"))          # chunks returned to the caller
POOL_K = int(os.environ.get("PIC_POOL_K", "30"))       # per-retriever candidate pool before fusion
CHUNK_MAX_CHARS = int(os.environ.get("PIC_CHUNK_MAX_CHARS", "900"))
RRF_K = int(os.environ.get("PIC_RRF_K", "60"))         # reciprocal-rank-fusion constant

# Anchor "today" so age/slip/trend math is reproducible across the sample corpus.
TODAY = os.environ.get("PIC_TODAY", "2026-06-14")


def get_client():
    """Anthropic client for answering/agents. Clear error if the key is missing."""
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key "
            "(or use ask.py --retrieve-only, which needs no key)."
        )
    return anthropic.Anthropic()
