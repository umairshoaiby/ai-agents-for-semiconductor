# Case Study — Weekly Program Roll-Up Copilot

## The problem

Every program manager writes a weekly update, and everyone underestimates how much of the work
is *reconstruction*, not writing. The schedule is the easy part. The hard part is that the real
signal — what moved, what's stuck, what got decided, who now owes what, which topics are getting
hot — is scattered across the week's meetings, design reviews, and a Jira/action list that has
quietly drifted since you last looked. By Friday you're reassembling the "flavor" of the week
from memory and half a dozen tabs, and *then* you start writing.

I wanted a tool that does the reconstruction: ingest the week's meetings + schedule + action log,
understand what happened, and hand me a draft that already says what's working, what isn't, where
the issues are, and what's hot — leaving me to add the judgment a director actually pays me for.

## Why this was a real design problem, not a prompt

The naive version — "dump the notes into an LLM and ask for a status update" — fails the only
test that matters for a leadership artifact: **can you defend it?** If the AI decides a workstream
is "green," invents an owner, or quietly drops an action item, the update launders a false picture
into something authoritative. So the central design question was *where to draw the trust line.*

## The decision: AI reads and writes; Python judges

I split the system so that nothing decision-bearing is left to the model:

- **The intelligence layer** (`intelligence.py`) runs one constrained extraction pass per meeting.
  It pulls out action items, decisions, and "what's working / not working" signals — and every
  single item must carry the **exact source quote** it came from. That quote is the grounding
  contract: it lets the deterministic layer and the eval harness verify nothing was fabricated.

- **The deterministic core** (`status_adapter.py`) owns every number and status. RAG colors come
  from explicit rules (blocker + slip + staleness), action items are aged and reconciled against
  the carried-over log, "hot topics" are ranked by how many *separate* meetings raised them, and
  confidence is computed from data completeness. None of this is an LLM guess.

- **The writer** (`rollup.py`) asks Claude to do what it's genuinely good at — write the headline,
  the executive summary, the per-workstream narrative, and the asks — *around* those trusted facts.
  The system prompt tells it to adopt the computed RAG and confidence verbatim and never invent an
  owner, date, action, or workstream. To be safe, the computed fields are enforced in code, not
  trusted to prompt obedience.

This is the same trust boundary I used in the Post-Silicon Validation Copilot, adapted from
structured test data to *unstructured conversation*. The lesson carried over: let the LLM handle
language and ambiguity, let Python handle anything a reviewer might have to defend in a gate review.

## Things I deliberately got right

- **Calibrated confidence.** The most dangerous failure in a status update is sounding sure on
  thin data — a confident "green" built on a workstream nobody updated this week. Confidence is
  computed from completeness (stale updates, uncovered workstreams, unowned actions) and the model
  is forced to adopt it. A red call on complete data and a green call on missing data are *not* the
  same confidence.

- **Hygiene as first-class risk.** An action with no owner, or one tagged to a workstream that
  doesn't exist, isn't paperwork — it's how balls get dropped. The tool surfaces these and lets
  them lower confidence.

- **Robust workstream matching.** The LLM tags a signal "Silicon" when the workstream is
  "Silicon Bring-Up." Early on this produced a false "no meeting coverage" flag. I resolve loose
  names to the canonical workstream by shared tokens before grouping — a small fix that mattered,
  caught by actually running it.

- **Human-in-the-loop by design.** The output isn't meant to be sent as-is. It ends in a
  "Your take" panel that seeds prompts for the judgment only the PM has (customer signals, exec
  context, whether a date is credible), and exports clean Markdown to paste anywhere.

## Tradeoffs and limits

- **Transcription is out of scope.** Meetings come in as text. Real recordings would be transcribed
  upstream (Teams/Otter) and dropped in — keeping the tool focused and shippable.
- **Action reconciliation is similarity-based**, not a hard ID join. It can occasionally keep a
  near-duplicate of an action phrased two different ways. I preferred recall (don't drop a real
  action) over aggressive dedup (which risks hiding one).
- **Single-week, in-memory.** No week-over-week history yet; that's the obvious next iteration
  (trend arrows on RAG, action-aging trends).

## How it's proven

The eval harness scores three labeled scenarios (clean / mixed / red-and-slipping) on determinism,
decision accuracy, confidence calibration, grounding, action recall, and hot-topic recall — the
offline deterministic checks pass 12/12. See [EVALUATION.md](EVALUATION.md).

## What it demonstrates

Multi-agent extraction over unstructured text, structured output with strict grounding, a
deterministic/LLM trust boundary, calibrated confidence, and an evaluation harness — applied to a
real hardware-NPI program-management workflow, not a toy demo.
