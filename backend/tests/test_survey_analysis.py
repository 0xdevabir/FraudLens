"""The survey fixture is invented: every respondent_id starts with FAKE-."""

from pathlib import Path

import pandas as pd
import pytest

from fraudlens.validation.survey_analysis import SurveyError, load, summarise, summarise_by

FIXTURE = Path(__file__).parent / "fixtures" / "survey_fake.csv"


@pytest.fixture
def survey():
    return load(FIXTURE)


def test_fixture_is_obviously_fake(survey):
    assert survey["respondent_id"].str.startswith("FAKE-").all()


def test_rows_without_consent_are_ignored(survey):
    s = summarise(survey)
    assert s["responses"] == 5 and s["consented"] == 4
    assert s["q04_loss_bdt"]["n"] == 2  # FAKE-005's Tk 99,999 is not counted
    assert s["q04_loss_bdt"]["median"] == 3000.0


def test_shares_and_victim_only_questions(survey):
    s = summarise(survey)
    assert s["q01_contacted"] == {"n": 4, "yes": 0.75, "no": 0.25}
    assert s["q02_lost_money"]["yes"] == 0.5
    assert s["q03_scam_type"]["n"] == 2
    assert s["q03_scam_type"]["impersonation"] == 0.5
    assert s["q05_reported_to"]["nobody"] == 0.5


def test_screen_reactions(survey):
    screens = summarise(survey)["screens"]
    assert screens["warn"] == {"n": 4, "mean": 4.0, "agree": 0.75, "disagree": 0.25}
    assert screens["hold"]["agree"] == 0.25
    assert screens["step_up"]["mean"] == 4.25


def test_split_by_area(survey):
    out = summarise_by(survey, "area")
    assert set(out["groups"]) == {"rural", "urban"}
    assert out["groups"]["urban"]["consented"] == 2


def test_bad_answers_are_rejected(tmp_path):
    bad = pd.read_csv(FIXTURE, dtype=str, keep_default_na=False)
    bad.loc[0, "q07_warn_would_stop"] = "7"
    path = tmp_path / "bad.csv"
    bad.to_csv(path, index=False)
    with pytest.raises(SurveyError):
        summarise(load(path))


def test_missing_required_column(tmp_path):
    path = tmp_path / "no_consent.csv"
    path.write_text("respondent_id,area\nFAKE-001,urban\n")
    with pytest.raises(SurveyError):
        load(path)
