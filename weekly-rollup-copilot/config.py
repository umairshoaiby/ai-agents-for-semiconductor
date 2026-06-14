"""Central configuration for the Weekly Program Roll-Up Copilot.

Keeping the model id, client construction, and tunable thresholds here makes the
example model-portable and easy to retune: swap the default model below or set
ROLLUP_MODEL in your .env, and adjust the program thresholds, without touching
the analysis logic in status_adapter.py / rollup.py.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Default to the most capable Claude model. Override via ROLLUP_MODEL in .env
# (e.g. claude-sonnet-4-6 for lower cost, claude-haiku-4-5 for speed).
MODEL = os.environ.get("ROLLUP_MODEL", "claude-opus-4-8")

# "Today" for age/slip math. The week's data is dated, so we anchor to a fixed
# date for reproducibility (the eval harness depends on deterministic ages).
# Override with ROLLUP_TODAY=YYYY-MM-DD; defaults to the sample-data week.
TODAY = os.environ.get("ROLLUP_TODAY", "2026-06-14")

# --- Tunable thresholds (pure policy, no logic) ----------------------------- #
# A workstream whose status note hasn't been refreshed in this many days is
# "stale" and drags program confidence down.
STALE_DAYS = int(os.environ.get("ROLLUP_STALE_DAYS", "7"))

# A milestone forecast slipping more than this many days past baseline is a
# material slip worth escalating; within it is amber, not red.
SLIP_DAYS = int(os.environ.get("ROLLUP_SLIP_DAYS", "10"))

# A topic must be raised in at least this many separate meetings to count as a
# cross-meeting "hot topic" (vs. a one-off comment).
HOT_TOPIC_MIN_MENTIONS = int(os.environ.get("ROLLUP_HOT_TOPIC_MIN_MENTIONS", "2"))


def get_client():
    """Return an Anthropic client. Raises a clear error if the key is missing."""
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or export ANTHROPIC_API_KEY in your shell. (Or run with --no-llm for the "
            "deterministic, schedule-only draft that needs no key.)"
        )
    return anthropic.Anthropic()
