# Plan 01 — External validation and statistical rigour

## Goal

Answer the judges' common objection ("bounded by synthetic data, not validated in
the real world") with measured evidence, and put error bars on the headline numbers.

1. Run the FraudLens modelling approach (LightGBM on receiver-centric, velocity and
   cash-out-timing features; Isolation Forest as the label-free baseline) on public,
   externally generated fraud data: PaySim (mobile money, simulated from real
   African MFS logs) first, the Bank Account Fraud suite (NeurIPS 2022) if a public
   mirror exists. Temporal split, recall at a fixed false-positive rate, PR-AUC,
   and simple baselines.
2. Bootstrap 95% confidence intervals for the headline metrics on the existing
   synthetic test period (scam recall at the warn operating point, false-positive
   rate on legitimate payments, PR-AUC), written to a JSON artifact.
3. `make external`, and an "External validation" section in `docs/MODEL_CARD.md`
   with the measured numbers. If a dataset cannot be obtained, say so.

## Judge criteria moved

- **AI/ML (14.67/20):** evidence that the method is not an artefact of our own
  simulator, plus uncertainty quantification instead of point estimates.
- **Business impact (13.67/20):** the business case rests on the detection
  numbers; showing how they hold up on independent MFS data (and how wide the
  intervals are) makes the impact claim defensible.

## Steps

1. `scripts`/`backend/src/fraudlens/external/download.py`: fetch from public
   mirrors (Hugging Face, GitHub raw, Zenodo) into `backend/data/external/`
   (already gitignored via `backend/data/`), verify SHA-256 checksums.
2. `external/paysim.py`: per-step (hourly) temporal split; features computed
   strictly from the past (receiver in-degree, distinct senders, receiver
   velocity, time since receiver last received, sender velocity, amount ratios,
   type). No balance-error leakage features in the main model (reported
   separately as a known PaySim artefact). LightGBM same params; baselines:
   amount rule, PaySim's own `isFlaggedFraud` rule, logistic regression,
   Isolation Forest.
3. `external/baf.py` if BAF is reachable: month-based temporal split, recall at
   5% FPR (the benchmark's own metric).
4. `models/bootstrap.py`: day-block and case-cluster bootstrap of v4's test
   scores; writes `artifacts/models/<version>/bootstrap_ci.json`.
5. Make target, model card section, tests on small synthetic frames.

## Definition of done

- `make external` downloads (or reuses) data, verifies checksums, trains,
  writes `backend/artifacts/external/*.json` and the bootstrap JSON.
- Model card section quotes only numbers read from those JSON files.
- Tests and `make lint` pass; changes committed on the worktree branch.
