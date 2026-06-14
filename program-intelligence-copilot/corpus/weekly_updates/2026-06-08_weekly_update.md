# Weekly Program Update — week of 2026-06-08

**Overall: RED** · Confidence: medium

The program moves to **red** this week. Two issues hardened: the ATE production test program is
now blocking EVT (load board slipped), and channel-B audio THD is a confirmed, repeatable
failure (VP-003 opened). EVT timing is at material risk.

## Workstreams
- **Silicon Bring-Up — RED:** A2 in lab and power-up clean, but the ATE program is blocking
  volume screening for EVT. Load board slipped again.
- **Validation — RED:** channel-B THD+N failing at 0.011–0.012% vs 0.008% target; VP-003 root
  cause in progress. Power/clocking/interface green.
- **Firmware — GREEN:** driver stack stable on A2.
- **Supply/Ops — AMBER:** substrate fine; long-lead crystal (16 weeks) a watch item.

## Hot topics
- ATE program readiness — now a blocker (was an emerging risk last week).
- Channel-B audio THD — escalated from a marginal single-board reading to a confirmed failure.

## Schedule
- EVT Build: slipping past end of June; new date pending ATE readiness.

## Asks
- Firm ATE load-board readiness date to recommit the EVT build date.
