# Plan 05 — Adaptive adversaries

## Goal

`docs/DATA_ASSUMPTIONS.md` admits the weakest point of the evaluation: "Real
scammers adapt to controls; the simulator's do not react to the model's
decisions." This plan closes that gap with a measured experiment: after FraudLens
is deployed, scam cells change their scripts in rounds, each round moving toward
the evasion tactics that got past the deployed model in the previous one. We then
compare what keeps detection up: nothing (a frozen model), retraining every round
through the existing retrain recipe, or retraining only when the drift monitor
asks for it.

## Judge criteria it moves

- **AI/ML depth (14.67/20).** A closed-loop adversarial evaluation, retraining on
  biased labels (verdicts on alerts plus victim complaints, not ground truth for
  everything), drift detection measured against a non-adaptive control, and SHAP
  on what carries detection after adaptation. This is the question a fraud team
  actually faces after launch, answered with numbers instead of a sentence.
- **Innovation (7.67/10).** Few fraud demos model the attacker at all. A
  simulator whose scam cells learn from the deployed model's decisions, and an
  honest result on whether the standard drift monitor notices, is new for this
  project and uncommon for hackathon fraud work.

## Design

- **Opt-in adversary mode** (`fraudlens.simulator.adversary`). Nothing in the
  default dataset changes: the experiment runs the default world for its 120 days
  (and checks it reproduces the stored dataset exactly, so the stored feature
  engine snapshot can continue from it), then switches the fraud planner to an
  adaptive one. New scam cells set up for a warm-up week, then run in rounds of a
  week.
- **Tactics**, one drawn per scam from the cell's current mix:
  `none` (the original script), `split_amounts` (victim transfers split below a
  cap), `seasoned_mule` (an old recruited wallet with a normal history instead of
  a young one), `delayed_cashout` (mule waits hours to days, past the 30-minute
  hold-review window, before moving money), `fan_out` (each victim transfer to a
  different mule from a larger pool), `mimic_hours` (scam timed at the victim's
  own usual hour).
- **Adaptation**: after each round the adversary sees which of its scams got
  through (no victim transfer alerted at the warn tier of that arm's deployed
  model) and reweights its mix multiplicatively toward tactics with the higher
  success rate, with an exploration floor.
- **Arms** (each its own simulation, because the adversary reacts to whichever
  model is deployed in that arm):
  - control: frozen model, adversary does not adapt (mix fixed at round 0)
  - (a) frozen served model
  - (b) retrained every round with the `models.train` recipe and the
    `mlops.retrain` label semantics: verdicts on alerted transactions plus
    complaint labels on reported scams, added to the training fold
  - (c) retrained only in rounds where `mlops.drift.measure` raises an alert on
    the round's traffic (the production trigger)
- **Drift**: `mlops.drift.measure` on every round's traffic against the model's
  stored reference, for the adaptive and the control runs, plus the same monitor
  pointed at confirmed fraud only (reference: training-period fraud).
- **SHAP**: mean |TreeSHAP| on victim transfers for the frozen model before and
  after adaptation and for the last retrained model.
- **Runtime**: the four arms share nothing but the stored day-120 engine snapshot;
  subsampling, if needed, is stated in the artifact and the model card.

## Steps

1. `Simulation.run_days` (additive; `run` calls it), `transaction_table` factored
   out of `generate.build_tables` (behaviour-preserving).
2. `simulator/adversary.py`: tactics, mix update, `AdaptivePlanner`.
3. `models/adversary.py`: the experiment, artifact
   `artifacts/reports/adversary.json`; `make adversary`.
4. Tests: default data unchanged by the adversary code, tactics change what they
   claim to change, mix moves toward successful tactics, experiment smoke on the
   small world.
5. `docs/MODEL_CARD.md` section "Adaptive adversaries" with the measured numbers;
   `DATA_ASSUMPTIONS.md` updated to point at it.
6. If clean: an additive API field and a small chart on the model page.

## Definition of done

- `make adversary` runs end to end and writes `backend/artifacts/reports/adversary.json`
  with recall per round per arm, per-tactic recall, mix per round, drift per round
  and SHAP top features; no existing artifact is overwritten.
- The model card section quotes only numbers from that artifact.
- `uv run pytest -q` and `make lint` pass; new tests cover the adversary mode.
- Committed on the worktree branch.
