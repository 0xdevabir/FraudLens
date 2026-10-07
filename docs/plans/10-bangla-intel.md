# Plan 10 — Bangla, Banglish and English scam-message intelligence, tied to the payment

## Goal

Make the scam-message classifier (`backend/src/fraudlens/intel/`) measurably
stronger in the three ways customers write (Bangla script, Banglish, English, and
the code-mixed text in between), show *which words* made it say so, and let a
flagged message raise the risk of the payment that follows it.

## Which judge criterion it moves, and why

- **AI/ML depth (14.67/20).** Today there is one model, trained and tested on
  synthetic templates written by the same person. This adds: a public,
  licensed, human-labelled Bangla/Banglish/English/code-mixed smishing corpus as
  an *external* test set (the first number not measured on our own templates); a
  model comparison on the same splits (current model, word TF-IDF, character
  n-grams alone, multilingual sentence-transformer embeddings), with macro-F1 per
  language and per typology and CPU latency; a production choice made on that
  evidence, with a fallback.
- **Innovation (7.67/10).** The message-to-payment link: a payment made shortly
  after the customer checked a flagged message, to the number or for the amount
  in that message, is raised to a warning with that reason shown. Fraud that
  starts in an SMS is caught at the moment money moves, without keeping the text.

## Steps

1. **Audit** the current classifier: corpus size and languages, method, measured
   numbers (from `artifacts/intel/report.json`).
2. **Grow the corpus responsibly.**
   - Public data: search Hugging Face and GitHub for Bangla SMS spam/scam data,
     check licences, pin the revision and checksum, document provenance.
   - Synthetic: new templates for the six typologies (fake agent or helpline,
     prize/lottery, wrong-number send-back, job/investment, fake government aid,
     OTP/PIN phishing), a code-mixed language (`mx`), more harmless look-alikes.
   - A held-out script per typology that is never trained on.
   - Every family tagged with its typology; provenance written in the corpus file
     and in `docs/FRAUD_TAXONOMY.md`.
3. **Compare models** (`python -m fraudlens.intel.compare`, `make intel-compare`)
   on the same splits: new wording of trained scripts, held-out scripts, and the
   external test set. Binary macro-F1 per language and per typology, category
   macro-F1, AUC, CPU latency per message. Transformer embeddings only as an
   optional extra (`uv sync --extra transformer`), never a core dependency.
4. **Pick the production model** on the evidence; keep the link check (no model)
   as the fallback when no classifier is loaded, as today.
5. **Cue-level explanations**: each signal carries the exact phrase(s) that
   matched, as spans of the customer's own text, in either script; plus the words
   that pushed the score up most (from the character n-gram weights). Additive
   fields only, so the API stays backward compatible.
6. **Message-to-payment link**: when a check is flagged, remember for 30 minutes
   only the numbers, wallet IDs and amounts in it (never the text). A payment by
   that wallet to one of those numbers, or for one of those amounts, is raised to
   at least `warn` with the reason in English and Bangla. The recipient check
   says the same before the amount is typed.
7. **Docs**: `docs/FRAUD_TAXONOMY.md`, `docs/MODEL_CARD.md` with the measured
   numbers only.
8. **Tests and lint**: `uv run pytest -q`, `make lint`.

## Definition of done

- `make intel` trains the chosen model and writes `report.json`; `make
  intel-compare` writes `compare.json` with every number quoted in the docs.
- External test set results reported per variety, with source, licence and
  revision recorded.
- Each of the six typologies has trained and held-out scripts and a reported
  number.
- `message-check` responses keep every existing field; signals gain `phrases`,
  responses gain `highlights`.
- A payment after a flagged message to its number or amount is raised to `warn`
  with a reason; covered by tests.
- No invented numbers; tests and lint pass; committed on the worktree branch.
