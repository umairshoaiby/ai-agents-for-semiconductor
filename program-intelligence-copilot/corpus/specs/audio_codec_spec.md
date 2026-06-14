# Audio Codec — Key Validation Specs (excerpt)

Mixed-signal audio codec, EVT silicon (A2).

## THD+N (Total Harmonic Distortion + Noise)
- **Target: ≤ 0.008% at 1 kHz, full-scale output, both channels.**
- Measurement: 1 kHz sine, 0 dBFS, 20 Hz–20 kHz bandwidth, A-weighted off.
- Channel matching: channel A and channel B must each meet the target independently.

## PSRR
- ≥ 65 dB at 1 kHz.

## Notes for validation
- Channel-specific failures (one channel passing, the other failing) typically indicate a
  board-level coupling or layout path rather than a codec-core defect, and should be isolated
  with a coupling bench experiment before a die re-spin is considered.
- A documented root cause is required before any THD waiver can be considered at a gate.
