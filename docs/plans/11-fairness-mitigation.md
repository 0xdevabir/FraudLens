# Plan 11 — From disclosing the account-age bias to mitigating it

## Goal

MODEL_CARD §13 discloses that honest payments from senders whose wallet is under
30 days old are interrupted 6.0× as often as average, and payments *to* wallets
under 30 days old 16.4× (1.50% of them held). It recommends "a separate, higher
threshold for new wallets" but nothing implements it. This plan builds that
mitigation, fits it with an explicit objective, measures what it costs, and ships
it as a selectable policy version next to the unchanged v2.

## Judge criterion moved

**Responsible AI & security (4/5).** The judges praised the disclosure. The next
step up is acting on it with the trade-off measured: a fairness gap that is
reduced by a versioned, auditable policy change, with recall cost, confidence
intervals and a Pareto frontier, and with the human-review guarantee intact.

## Steps

1. **Policy schema: segment thresholds.** Add an optional `segments:` list to the
   policy file. A segment is a set of `field op number` conditions (the same
   comparison table the rules use, e.g. `r_age_days < 30`), the transaction types
   it applies to, and threshold overrides for that segment. First matching segment
   wins. Guards, enforced at load:
   - segment thresholds keep `0 < warn ≤ step_up ≤ hold < 1`, so **hold stays
     reachable** in every segment (no segment can switch holds off);
   - segments change only the *model's* tier; rules (`raise_to`, `hard`) are
     applied afterwards exactly as before, so R01/R02 still hold and nothing
     lowers a hard rule;
   - the `hold` tier still requires `human_review: true` (unchanged guard).
   The outcome records which segment applied and the thresholds used (rule trace
   and `Decision.segment`), so every decision stays auditable. The 0–100 display
   score is anchored to the thresholds actually used.
2. **Softer tier for young wallets.** The segment can raise the hold threshold
   more than the warn threshold, so a payment that would have been held becomes a
   step-up or a warning the customer can dismiss, while the riskiest payments are
   still held for a person.
3. **Fit with an explicit objective** (`decision/mitigation.py`). Thresholds are
   fitted on validation (val_a + val_b), never on test. Grid of candidate
   segment thresholds (multiples of the model's own); objective: minimise the
   larger of the two young-segment false-alert ratios, subject to the overall
   victim-transfer recall at warn falling by at most X percentage points and the
   hold-tier recall by at most Y points. Every candidate's (gap, recall) is kept,
   and the Pareto frontier is reported.
4. **Evaluate on the test period, before (v2) vs after (v3):** false-alert rate
   and ratio per age segment (sender and receiver under 30 days), overall false
   alert and false hold rate, victim-transfer and case recall, scam taka caught,
   held rate. Row-level bootstrap 95% CIs for the after − before differences.
   Write `artifacts/models/<v>/fairness_mitigation.json`.
5. **Ship `policies/v3.yaml`** = v2 + fitted segments. v1 and v2 unchanged,
   default stays v2 (`FRAUDLENS_POLICY_VERSION=v3` selects it).
6. **API + console:** `/v1/model/report` gains an additive `mitigation` field;
   the fairness page shows the before/after table and the frontier with existing
   components.
7. **Intersectional view (cheap):** account age × channel × urban/rural table in
   the fairness report.
8. **Docs:** MODEL_CARD §13 and DECISION_POLICY.md with the measured numbers and
   an honest trade-off statement. Tests for schema guards, segment selection, hard
   rules unaffected, and the fitting objective.

## Definition of done

- `policies/v3.yaml` loads; a segment that removes hold (hold ≥ 1) or breaks the
  threshold order is refused at load; hard rules still hold in every segment
  (tests).
- `fairness_mitigation.json` exists with before/after, CIs and the frontier; every
  number in the docs is read from it.
- Fairness page shows before/after and the frontier; `make lint` and
  `uv run pytest -q` pass.
- Recommendation on whether v3 should become the default, stated with its cost.
