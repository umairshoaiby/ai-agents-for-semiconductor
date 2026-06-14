"""Central configuration for the validation copilot.

Keeping the model id and client construction here makes the example
model-portable: swap the default below, or set COPILOT_MODEL in your .env,
without touching the analysis logic in copilot.py.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Default to the most capable Claude model. Override via COPILOT_MODEL in .env
# (e.g. claude-sonnet-4-6 for lower cost, claude-haiku-4-5 for speed).
MODEL = os.environ.get("COPILOT_MODEL", "claude-opus-4-8")


def get_client():
    """Return an Anthropic client. Raises a clear error if the key is missing."""
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or export ANTHROPIC_API_KEY in your shell."
        )
    return anthropic.Anthropic()
