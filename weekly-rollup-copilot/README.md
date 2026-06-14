# Weekly Program Roll-Up Copilot

**Turn a week of scattered program signal — meeting notes, the schedule, and a drifting
action log — into an exec-ready draft update that says what's working, what isn't, where
the issues are, and the hot topics. The judgment-bearing numbers are computed in Python;
the AI only reads the messy notes and writes the prose.**

A program manager's weekly update is painful not because of the schedule alone, but because
the signal is scattered across the week's *conversations*: design reviews, syncs, staff
meetings, and an action list that quietly drifts. You end up mentally reconstructing the
"flavor" of the week — what moved, what's stuck, what got decided, who owes what, which
topics are hot — and then writing it up. This tool does the reconstruction and hands you a
draft, with an explicit space to layer your own judgment on top.

It is example #2 in [`ai-agents-for-semiconductor`](../), and it deliberately reuses the
trust pattern proven in the [Post-Silicon Validation Copilot](../post-silicon-validation-copilot):
a **deterministic core** + a **constrained LLM layer** + an **evaluation harness**, with CLI
and web UI sharing the exact same functions.

---

## What it does

Given four inputs for the week:

| Input | What it is |
|---|---|
| `workstreams.csv` | Each workstream's owner, status note, % complete, blockers, last-updated date |
| `milestones.csv` | Baseline vs. forecast dates per milestone |
| `action_log.csv` | The carried-over action items (owner, due, state, raised date) |
| `meetings/*.txt` | This week's meeting notes / transcripts (the unstructured "flavor") |

…it produces a single **weekly update**:

- **Overall RAG** (green / amber / red) + an **honest confidence** level
- An **executive summary** and a **per-workstream** narrative with *what's working* / *what's not*
- **Hot topics** — themes that recurred across multiple meetings, ranked by frequency
- **Decisions** made (with the quote they came from)
- **Open action items** — reconciled against the meetings, aged, with blocked/slipped flags
- **Schedule slips** and **asks for leadership**
- **Data-hygiene gaps** (stale workstreams, unowned or orphan actions) that *lower confidence*
- A **"Your take"** panel and one-click **Markdown export** to paste into email / Teams / Confluence

---

## The trust split (why you can defend it)

Meeting notes have to be read by an LLM — but nothing that bears a decision is left to the LLM:

```
intelligence.py   →  reads each meeting, extracts grounded signal (every item carries the
                     exact source quote): action items, decisions, what's-working/not signals
status_adapter.py →  PURE PYTHON: derives every RAG color, ages every action, reconciles the
                     action log, ranks hot topics by cross-meeting frequency, computes confidence
rollup.py         →  asks Claude to WRITE the prose around those trusted facts — headline,
                     summary, per-workstream narrative, asks, and prompts for your own take
```

So the AI does the **reading** and the **writing**; Python owns the **numbers and the status**.
The model is explicitly told to adopt the computed RAG and confidence verbatim and never to
invent an owner, date, action, or workstream. The eval harness checks all of this.

---

## Quick start

```bash
cd weekly-rollup-copilot
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
copy .env.example .env                               # then add your ANTHROPIC_API_KEY
```

**Web UI (recommended):**
```bash
python app.py        # → http://127.0.0.1:5000
```
Pick *Use sample data*, click **Generate weekly update**, and explore the three tabs. Uncheck
*Use Claude* for the deterministic, no-key draft.

**CLI:**
```bash
# Full run — mine the meetings and write the update (needs a key)
python rollup.py --meetings sample_data/meetings

# Offline — deterministic schedule + action-log draft, no API key
python rollup.py --no-llm

# Also export a shareable Markdown file
python rollup.py --meetings sample_data/meetings --report weekly_update.md
```

**Evaluation:**
```bash
python evals/eval_harness.py          # offline deterministic checks (free): 12/12
python evals/eval_harness.py --llm    # adds grounding + action/hot-topic recall
```

---

## Why it matters for a PMO / program-management role

This is the most universal program-management task — the weekly roll-up — automated in a way
that a director can trust. It demonstrates: multi-agent extraction over unstructured text,
structured output with grounding, a deterministic/LLM trust boundary, calibrated confidence,
and an evaluation harness — applied to a real hardware-NPI workflow rather than a toy demo.

The sample data is fully synthetic (no proprietary content). Real meeting recordings would be
transcribed upstream (Teams, Otter, etc.) and dropped in as `.txt`; the tool takes it from there.

See [CASE_STUDY.md](CASE_STUDY.md) for the design decisions and tradeoffs, and
[EVALUATION.md](EVALUATION.md) for how trust is proven.
