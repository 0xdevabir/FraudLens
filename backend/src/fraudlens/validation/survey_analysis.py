"""Summarise the customer survey (docs/validation/customer_survey.md).

uv run python -m fraudlens.validation.survey_analysis responses.csv
uv run python -m fraudlens.validation.survey_analysis responses.csv --by area

One row per respondent, one column per question ID in the survey. Rows without
consent are dropped before anything is counted. Free text (q14) is never read.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

REQUIRED = ("respondent_id", "consent")
CATEGORICAL = {
    "area": ("urban", "rural"),
    "q01_contacted": ("yes", "no"),
    "q02_lost_money": ("yes", "no"),
    "q03_scam_type": (
        "impersonation",
        "lottery_fee",
        "wrong_send",
        "investment",
        "account_takeover",
        "other",
    ),
    "q05_reported_to": ("nobody", "provider", "police", "cipc", "several"),
    "q06_recovered": ("none", "partial", "full"),
    "q12_trust": ("more", "same", "less"),
    "q13_language": ("bangla", "english", "both"),
}
LIKERT = {
    "q07_warn_would_stop": "warn",
    "q08_step_up_ok": "step_up",
    "q09_hold_ok": "hold",
}
NUMERIC = ("q04_loss_bdt", "q10_max_hold_minutes", "q11_false_alarms_per_year")


class SurveyError(ValueError):
    pass


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise SurveyError(f"missing columns: {', '.join(missing)}")
    if df["respondent_id"].duplicated().any():
        raise SurveyError("respondent_id is not unique")
    for col in CATEGORICAL:
        if col in df:
            df[col] = df[col].str.strip().str.lower()
    return df


def _share(s: pd.Series, levels: tuple[str, ...]) -> dict:
    s = s[s != ""]
    unknown = sorted(set(s) - set(levels))
    if unknown:
        raise SurveyError(f"unexpected answers {unknown} in {s.name}")
    if s.empty:
        return {"n": 0}
    counts = s.value_counts()
    return {"n": int(len(s)), **{k: round(int(counts.get(k, 0)) / len(s), 3) for k in levels}}


def _numbers(s: pd.Series) -> dict:
    x = pd.to_numeric(s.replace("", None), errors="coerce").dropna()
    if (x < 0).any():
        raise SurveyError(f"negative values in {s.name}")
    if x.empty:
        return {"n": 0}
    return {
        "n": int(len(x)),
        "median": float(x.median()),
        "mean": round(float(x.mean()), 1),
        "p90": float(x.quantile(0.9)),
    }


def _likert(s: pd.Series) -> dict:
    x = pd.to_numeric(s.replace("", None), errors="coerce").dropna()
    if not x.between(1, 5).all():
        raise SurveyError(f"{s.name} must be 1-5")
    if x.empty:
        return {"n": 0}
    return {
        "n": int(len(x)),
        "mean": round(float(x.mean()), 2),
        "agree": round(float((x >= 4).mean()), 3),  # 4 or 5
        "disagree": round(float((x <= 2).mean()), 3),
    }


def summarise(df: pd.DataFrame) -> dict:
    consented = df[df["consent"].str.strip().str.lower() == "yes"]
    out: dict = {"responses": len(df), "consented": len(consented)}
    for col, levels in CATEGORICAL.items():
        if col in consented:
            out[col] = _share(consented[col], levels)
    # Scam type, loss, reporting and recovery describe victims only.
    lost = consented.get("q02_lost_money")
    victims = consented[lost == "yes"] if lost is not None else consented.iloc[0:0]
    for col in ("q03_scam_type", "q05_reported_to", "q06_recovered"):
        if col in victims:
            out[col] = _share(victims[col], CATEGORICAL[col])
    for col in NUMERIC:
        if col in consented:
            src = victims if col == "q04_loss_bdt" else consented
            out[col] = _numbers(src[col])
    out["screens"] = {
        name: _likert(consented[col]) for col, name in LIKERT.items() if col in consented
    }
    return out


def summarise_by(df: pd.DataFrame, column: str) -> dict:
    if column not in df:
        raise SurveyError(f"no column {column}")
    groups = {str(k): summarise(g) for k, g in df.groupby(column, sort=True)}
    return {"all": summarise(df), "by": column, "groups": groups}


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarise FraudLens customer survey responses")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--by", default=None, help="also split by this column, e.g. area")
    args = parser.parse_args()
    df = load(args.csv)
    result = summarise_by(df, args.by) if args.by else summarise(df)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
