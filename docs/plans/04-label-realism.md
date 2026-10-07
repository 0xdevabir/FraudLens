# 04 — Label realism: what under-reporting costs, and what gets it back

## Goal

MODEL_CARD §12 admits that every model is trained on ground-truth labels, while a
real MFS only learns about the scams victims report (about half). Turn that
disclosed weakness into a measured result: train the same transaction model under
realistic label regimes, evaluate every one on the same ground-truth test period,
and show which scam types under-reporting hides and how much of the loss a
recovery method wins back.

## Judge criteria it moves

- **AI/ML depth (14.67/20).** Positive-unlabelled learning, network label
  propagation and a simulated review loop, compared under one protocol with
  several seeds and mean ± sd: a real experiment, not one more model.
- **Business impact (13.67/20).** The judges discounted the numbers as
  synthetic-label numbers. This answers the obvious objection ("would it still work
  with the labels a bank actually has?") with a measured cost in scams missed, by
  typology, and names the cheapest fix (analyst verdicts, mule-network labels).

## What the simulator already models

`simulator/fraud.py` lets each victim transfer report with probability
`report_rate = 0.5`, uniformly at random, after a lognormal delay (median 1.5
days, 5 days for the held-out investment scam). A report only flags the mule
wallet; which cases were reported is not written to the dataset. So the reporting
draw is re-simulated per case, from data that is in the dataset.

## Steps

1. `backend/src/fraudlens/models/label_realism.py`, `make label-realism`.
   Model: the transaction LightGBM exactly as `train.fit_booster` builds it
   (same params, train fold, early stopping on val_a), seeds varied.
2. Reporting process (per case, seeded): report probability from a logistic in
   log loss, victim urban/rural, victim account tenure (the simulator has no
   person age, so tenure stands in for it) and typology (unauthorised takeover
   reported most, shame-heavy lottery/investment scams least); intercept solved
   so the mean is 50%, matching `report_rate`. Report delay as the simulator's;
   labels are those known at training time (end of val_b). A reported case labels
   every transaction tagged with it (victim transfers, forwards, cash-outs).
   A uniform 50% regime, the simulator's own process, is kept as a check.
3. Regimes, all evaluated on ground truth:
   a. ground truth (baseline);
   b. report-only labels, biased; b′. report-only, uniform 50%;
   c1. b + PU learning (Elkan–Noto weighting with out-of-fold propensities);
   c2. b + label propagation through the mule network (wallets that received
       reported scam money and their next hop pass soft labels to their other
       incoming and outgoing transfers in a time window);
   d. b + the analyst-verdict loop: a model trained on early report-only labels
      alerts on the later training days, simulated analysts label the alerts,
      and the verdicts join through `train.add_feedback`, the code path
      `mlops/feedback.py` feeds.
4. Metrics on the test period: PR-AUC (all fraud-chain rows), victim-transfer
   and scam recall at the served model's operating false-positive rate (v4 warn
   tier, read from its `test_scores.parquet`), recall by typology. Five seeds,
   mean ± sd.
5. Write `backend/artifacts/reports/label_realism.json` (new file, nothing
   existing is overwritten). Add a "Under-reporting" section to MODEL_CARD with
   the measured numbers.
6. Additive API field on `/model/report` and a small card on the console model page.
7. Tests on the reporting draw, propagation and PU weights on a small frame.

## Definition of done

- `make label-realism` reproduces the JSON; the model card quotes it, number for number.
- `uv run pytest -q` and `make lint` pass; new tests cover the label regimes.
- No served model, default artifact or headline number changes.
