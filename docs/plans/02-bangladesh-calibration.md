# Plan 02: Bangladesh evidence, simulator calibration, real-user validation kit

## Goal

Replace "informed guesses" with sourced Bangladesh figures wherever a public
source exists, say plainly where none does, and give the team the instruments
to collect real evidence from customers and analysts.

## Judge criteria it moves

- **Problem relevance (16/20).** The problem is stated in sourced numbers
  (MFS scale, scam reports, regulation) instead of assertion.
- **Business impact (13.67/20).** Judges said results are "bounded by synthetic
  data, real-world impact not yet validated". A calibration profile tied to
  Bangladesh Bank statistics, plus a ready-to-run survey and interview guide,
  turns that from an open gap into a concrete validation path.

## Steps

1. Research (web): Bangladesh Bank monthly MFS statistics, published MFS scam
   figures, Bangladesh Bank MFS regulation and customer-protection guidance, the
   UK PSR APP-scam reimbursement rules. Every figure gets a URL and access date.
   Nothing unsourced is presented as sourced.
2. `docs/BANGLADESH_CONTEXT.md`: problem in numbers, stakeholder map,
   regulatory fit, 3–5 cited pitch openers.
3. Simulator: compare `simulator/` parameters with sourced figures. Add an
   opt-in `calibrated` profile (`SimConfig.profile`, `FRAUDLENS_SIM_PROFILE`
   env var, `--profile` flag). The default profile and its output are unchanged
   (same seed, same bytes). Add the comparison table to `DATA_ASSUMPTIONS.md`.
4. Validation kit: `docs/validation/customer_survey.md` (Bangla + English
   consent, 10–15 questions), `docs/validation/analyst_interview_guide.md`,
   `backend/src/fraudlens/validation/survey_analysis.py` (CSV in, summary out)
   with a test on an obviously fake fixture.

## Definition of done

- Every number in `BANGLADESH_CONTEXT.md` and the calibration table has a source
  URL and access date, or is marked "no public source found".
- `SimConfig()` default is unchanged; a test checks the calibrated profile
  differs only in the sourced parameters and that default is untouched.
- `survey_analysis.py` runs on the fixture and is tested.
- `uv run pytest -q` and `make lint` pass; committed on the worktree branch.
