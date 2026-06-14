# Case Study — Post-Silicon Validation Copilot

> An interview-ready walkthrough. This is how I'd present the project in a senior
> AI PM loop: problem, why it matters, what I built, the decisions and tradeoffs,
> and how I'd scale and govern it.

## Problem

Before every phase gate in a mixed-signal NPI, the team has to answer: *are we
covered, and what's still at risk?* The inputs — a validation plan and bench
logs — are real but scattered, and assembling a defensible coverage picture by
hand is slow and error-prone. The cost of getting it wrong is asymmetric: shipping
on an untested critical path is far worse than a slipped gate.

## Why it matters

This is the decision that gates tape-out-to-production spend. A clear, trustworthy
readout shortens gate reviews, makes the risk call explicit, and creates an audit
trail. It's also exactly the kind of judgment-heavy, domain-specific workflow that
generic AI tooling can't touch — which is what makes it a strong product wedge.

## What I built

A copilot that (1) computes coverage **deterministically in Python** — pass rate,
failures, skips, untested gaps, and critical-not-passing — then (2) uses Claude to
turn those facts into a gate-review readout: headline, go/conditional/no-go call,
top risks, and a prioritized, ownable action list. Output is a typed Pydantic
schema, so it's structured data, not prose to parse.

## Key decisions & tradeoffs

- **Deterministic core, LLM judgment.** The numbers must be auditable, so the math
  never touches the model. The LLM only does prioritization and communication —
  the things it's actually good at. This is the single most important design call;
  it's what makes the output trustworthy in a high-stakes setting.
- **Structured output over free text.** Forcing a schema (`go | conditional-go |
  no-go`, P0/P1/P2 actions) makes the result usable downstream and prevents the
  model from hedging into unactionable prose.
- **Domain in the system prompt, not the data.** The model is told it's a
  post-silicon PM and that a skipped jitter test ≠ a failed bandgap trim. That
  domain framing is the moat; the code is commodity.
- **Model-portable by config.** Defaults to `claude-opus-4-8` for the best
  judgment; one env var drops to a cheaper model for high-volume runs. The
  intelligence/cost tradeoff stays a product decision, not a code change.

## Reconciling against Jira (the real-world version)

In practice, validation status lives in **Jira**, and the team's own observation is
that "even with Jira, it's never 100%." So the copilot reads a Jira export and
reconciles it against the authoritative plan, flagging the three failure modes that
quietly corrupt a gate decision:

- **Untracked** — a required test that never made it onto the board.
- **Ambiguous** — a ticket closed as *Done* with no recorded result (closed ≠ passed).
- **Orphan** — a Jira ticket that isn't in the plan (scope drift).

The load-bearing call: a *Done*-with-no-result ticket is **not** counted as covered.
That single rule is what stops "the board is green" from being mistaken for "the
silicon is verified" — which is the exact gap the team was worried about.

## How do we know it's trustworthy? (the PMO question)

The decision this tool informs is a phase gate — so "it demos well" isn't enough.
I built an **evaluation harness** ([EVALUATION.md](EVALUATION.md)) that scores the
output against labeled scenarios with known-correct answers, on six trustworthy-AI
dimensions: **determinism, decision accuracy, confidence calibration, grounding (no
fabricated test IDs), critical recall, and hygiene recall.** It runs offline for free
CI, or against the live model, and fails closed (non-zero exit) if any check fails.

Two design choices make those scores achievable:

- **A hard trust boundary.** The LLM never computes a number — all coverage and
  data-quality facts are deterministic and auditable; the model only turns a *grounded
  fact brief* into judgment and language. That collapses the hallucination surface.
- **Calibrated confidence.** Confidence (high/medium/low) is computed from data
  completeness, and the model is instructed to adopt it, not inflate it — so it can
  never report a confident "go" on an incomplete picture (the most dangerous failure
  mode in a gate review).

**Current result: 18/18 checks pass, both offline and against the live model** — the
key evidence being that the model passes *grounding* and *confidence calibration*,
i.e. it operates inside the guardrails. The output is still **advisory**: the harness
gates the machine, the gate owner makes the call.

## How I'd scale it

- Harden the evals: an LLM-as-judge rubric for narrative quality, a larger scenario
  bank, adversarial/red-team inputs, and tracking decision accuracy vs. real gate outcomes.
- Pull live from the Jira API (not a CSV export); reconcile on a schedule.
- Ingest directly from the test framework / LIMS to auto-populate test results.
- Trend coverage across silicon revisions; flag regressions gate-over-gate.
- Promote the readout to a tool the model calls inside a larger NPI agent, so a
  gate review becomes one step in an end-to-end readiness check.
- Add evals: a labeled set of plan/log pairs with known correct gate calls, scored
  automatically, so model or prompt changes can't silently regress the risk call.

## Risks & governance

- **The call is advisory, not authoritative.** A no-go recommendation supports the
  gate owner; it doesn't replace sign-off. That boundary has to be explicit.
- **Garbage-in risk.** Coverage is only as honest as the bench logs; the tool
  should surface *unlogged* tests loudly (it does) rather than imply silence = pass.
- **Auditability.** Because the math is deterministic and the prompt is fixed, any
  readout can be reproduced and explained — essential for a regulated hardware org.
- **No confidential data.** Built and demonstrated entirely on synthetic data.

## Likely interview questions I can answer from this

- Why split deterministic vs. LLM work, and where exactly is the line?
- Your team already uses Jira — what does this add? (Answer: it reconciles the board
  against the required plan and flags untracked/ambiguous/orphan tickets, so "green
  board" can't be mistaken for "verified silicon.")
- How would you evaluate this — what's your eval set and metric? (Answer: the harness
  in EVALUATION.md — labeled scenarios scored on determinism, decision accuracy,
  confidence calibration, grounding, and recall; 18/18 today, fails closed.)
- How do you stop the AI being confidently wrong? (Answer: confidence is computed from
  data completeness and the model must adopt it; it can't claim "high" on thin data.)
- What breaks first at 100× the data, and what do you change?
- How do you keep a hallucinated risk call from reaching a gate owner?
