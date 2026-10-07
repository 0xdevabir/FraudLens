# Plan 03 — Business case in taka

## Goal

Turn the back-test numbers (86.9% of scams caught at warn, 0.53% of payments
falsely interrupted) into a monthly profit-and-loss a provider's CFO and CRO can
check line by line: money kept from scammers, what the friction and the review
team cost, and what is left. Every assumption is explicit, editable and has a
stated source or rationale; every model number is read from the existing
`insights.json`, so nothing is re-scored and no artifact changes.

## Judge criterion it moves

**Business / customer impact (13.67/20).** Today the impact page speaks in
recall and alerts a day. A judge has to do the conversion to money and headcount
themselves, and the synthetic taka figures do not scale to a real provider. This
work answers "is it worth running, at our volume, and how many people does it
need?" in BDT, and shows which assumptions the answer depends on.

## Steps

1. `backend/src/fraudlens/decision/business.py`: a pure model over the insights
   report.
   - `Assumptions` (pydantic, bounded fields) with a source or rationale per
     field: volume, average payment, real scam rate, share stopped by warn /
     step-up / hold, abandonment, support contacts per interruption and their
     cost, analyst minutes per hold, analyst cost, coverage floor, platform
     cost, reimbursement share, reputational exposure.
   - The slider moves the warn threshold; step-up and hold stay where the
     policy puts them (or follow the slider when it passes them). Tier bands are
     differences of the cumulative sweep and `at_thresholds`.
   - Scaling: rates per payment from the back-test, multiplied by monthly
     volume; true alerts and scam money rescaled from the synthetic scam rate to
     the assumed real one.
   - Output per sweep point: prevented loss, friction cost, analyst cost, net
     benefit (customers + provider, and provider only), prevented loss per ৳1
     of operating cost, analyst FTE, honest customers interrupted per 10,000.
   - `sensitivity()`: tornado table, each assumption at a low and a high value,
     sorted by swing in net benefit.
   - CLI `python -m fraudlens.decision.business` prints the table.
2. API: `GET /v1/model/business` (defaults) and `POST /v1/model/business`
   (assumption overrides) in `routes/ops.py`, next to `/model/report`.
3. Console: extend `app/(console)/impact/page.tsx` with a business-case section:
   money stats that follow the slider, editable assumptions (volume presets,
   analyst cost, abandonment, scam rate), and the tornado table. Types added to
   `lib/types.ts`.
4. Tests: `backend/tests/test_business.py` (arithmetic on a hand-made report,
   monotonicity, sensitivity ordering, real artifact, API route).
5. `docs/BUSINESS_CASE.md` with measured numbers and the tornado table, honest
   about the synthetic data; one link from README.

## Definition of done

- `uv run pytest -q` and `make lint` pass; new tests cover the model and route.
- Dragging the impact slider updates money saved, friction cost, net benefit and
  analyst headcount; editing volume, analyst cost or abandonment recomputes.
- `docs/BUSINESS_CASE.md` numbers match `python -m fraudlens.decision.business`
  on the current artifacts.
- No default artifact or headline number changed; shared-file edits are additive.
