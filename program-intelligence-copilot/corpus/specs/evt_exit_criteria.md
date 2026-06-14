# EVT Exit Criteria (Phase Gate)

EVT (Engineering Validation Test) build may proceed to PVT only when the following are met.

1. **ATE production test program ready** — volume screening must be runnable on the production
   ATE with the qualified load board. This is a hard gate: no ATE program, no EVT exit.
2. **All critical validation suites passing** — power, clocking, interface, and audio. A
   failing critical suite may proceed only with a **documented waiver** that includes a root
   cause and a committed fix plan for the next spin.
3. **No open P0 blockers** without an owner and a recovery date.
4. **Supply commitment** for EVT build quantities, including long-lead components.

A "conditional go" is permitted when (2) is satisfied by a waiver and (1), (3), (4) are met.
