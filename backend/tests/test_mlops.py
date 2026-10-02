"""After deployment: masking, drift on served traffic, and retraining on verdicts."""

import json
import shutil

import numpy as np
import pandas as pd
import pytest

from fraudlens.decision import insights
from fraudlens.decision.policy import load_policy
from fraudlens.features import FEATURES
from fraudlens.mlops import drift
from fraudlens.mlops.shadow import Shadow
from fraudlens.models import registry
from fraudlens.models.data import load_frame
from fraudlens.models.train import FEEDBACK_COLUMNS, train
from fraudlens.platform.pii import redact

# ------------------------------------------------------------------- masking


def test_redact_masks_numbers_and_addresses_and_leaves_the_story():
    text = "He called from 01712-345678 and said send 5000 taka to 01898765432."
    assert redact(text) == "He called from [number] and said send 5000 taka to [number]."
    assert redact("mail me at rahim.uddin+1@example.com.bd now") == "mail me at [email] now"
    assert redact("card 4111 1111 1111 1111, pin 1234") == "card [number], pin 1234"
    assert redact("+8801712345678 এ ফোন দিন") == "[number] এ ফোন দিন"
    # Bangla digits are digits; what is left keeps its own script.
    assert redact("নম্বর ০১৭১২৩৪৫৬৭৮ থেকে ৫০০ টাকা") == "নম্বর [number] থেকে ৫০০ টাকা"


def test_redact_leaves_short_numbers_ids_and_nothing_alone():
    for text in ("paid 12,500.50 on 2026-04-06", "wallet W0012345678901", "ref A1B2C3", ""):
        assert redact(text) == text
    assert redact(None) is None


# --------------------------------------------------------------------- drift


@pytest.fixture(scope="module")
def reference(trained):
    data_dir, models_root, _ = trained
    insights.run(data_dir, models_root=models_root)  # writes the drift reference
    version = registry.current_version(models_root)
    bundle = registry.load(version, models_root)
    thresholds = load_policy("v1").resolve_thresholds(bundle.manifest)
    frame = load_frame(data_dir)
    frame = frame[~frame["ambiguous"]]
    return drift.load_reference(models_root / version), bundle, thresholds, frame


def test_the_drift_reference_is_kept_with_the_model(reference):
    kept, _, _, frame = reference
    assert kept is not None and set(kept["features"]) == set(FEATURES)
    assert kept["reference"] == {"features": "train", "score": "val_b"}
    assert kept["rows"]["features"] == int((frame["fold"] == "train").sum())
    for bins in (*kept["features"].values(), kept["score"]):
        assert sum(bins["shares"]) == pytest.approx(1.0, abs=0.05)


def test_drift_is_quiet_on_the_training_period_and_flags_a_shifted_sample(reference):
    kept, bundle, thresholds, frame = reference
    train_rows = frame[frame["fold"] == "train"]
    x = train_rows[list(FEATURES)].to_numpy(dtype=np.float64)
    risk = bundle.score(x)["risk"]
    same = drift.measure(kept, x, risk, thresholds)
    assert same["rows"] == len(x)
    assert same["feature_status"] == {"stable": len(FEATURES), "watch": 0, "shifted": 0}
    assert max(row["psi"] for row in same["features"]) < 1e-6

    # Amounts five times larger, and one input that has stopped arriving.
    shifted = x.copy()
    amount, lost = FEATURES.index("amount"), FEATURES.index("s_age_days")
    shifted[:, amount] *= 5
    shifted[:, lost] = np.nan
    report = drift.measure(kept, shifted, bundle.score(shifted)["risk"], thresholds)
    by_name = {row["feature"]: row for row in report["features"]}
    assert by_name["amount"]["status"] == "shifted"
    assert by_name["s_age_days"]["status"] == "shifted" and by_name["s_age_days"]["missing"] == 1
    assert report["features"][0]["psi"] == max(row["psi"] for row in report["features"])
    untouched = [name for name in FEATURES if name not in ("amount", "s_age_days")]
    # Only the two inputs changed: nothing else may be reported as moving.
    assert all(by_name[name]["psi"] < 1e-6 for name in untouched)
    assert report["feature_status"]["shifted"] == 2

    # A score that has collapsed to "everything is risky" is drift too.
    alarmed = drift.measure(kept, x, np.ones(len(x)), thresholds)
    assert alarmed["score"]["status"] == "shifted"
    assert alarmed["score"]["alert_rate"] == alarmed["score"]["hold_rate"] == 1.0
    assert same["score"]["alert_rate"] < 0.2


def test_drift_refuses_a_sample_it_cannot_judge(reference):
    kept, _, thresholds, frame = reference
    x = frame[list(FEATURES)].to_numpy(dtype=np.float64)
    with pytest.raises(ValueError, match="at least"):
        drift.measure(kept, x[: drift.MIN_ROWS - 1], np.zeros(drift.MIN_ROWS - 1), thresholds)
    with pytest.raises(ValueError, match="one feature vector"):
        drift.measure(kept, x[:500, :-1], np.zeros(500), thresholds)
    with pytest.raises(ValueError, match="one feature vector"):
        drift.measure(kept, x[:500], np.zeros(499), thresholds)


def test_a_reference_for_other_features_is_not_used(reference, tmp_path):
    kept = reference[0]
    assert drift.load_reference(tmp_path) is None  # insights not run yet
    stale = {**kept, "features": dict(list(kept["features"].items())[:-1])}
    (tmp_path / insights.DRIFT_REFERENCE_FILE).write_text(json.dumps(stale))
    assert drift.load_reference(tmp_path) is None


# ------------------------------------------------------- retraining on feedback


@pytest.fixture(scope="module")
def retrained(trained, reference, tmp_path_factory):
    """A challenger trained with verdicts on the first week of the model's alerts."""
    data_dir, models_root, _ = trained
    _, bundle, _, frame = reference
    root = tmp_path_factory.mktemp("retrain") / "models"
    shutil.copytree(models_root, root)  # other tests count the versions in the shared root
    test = frame[frame["fold"] == "test"]
    risk = bundle.score(test[list(FEATURES)].to_numpy(dtype=np.float64))["risk"]
    # What analysts would have looked at: the riskiest payments of the first test week.
    first_week = (test["ts"] < test["ts"].min() + pd.Timedelta(days=7)).to_numpy()
    reviewed = first_week & (risk >= np.quantile(risk[first_week], 0.9))
    feedback = test.loc[reviewed, list(FEEDBACK_COLUMNS)].reset_index(drop=True)
    assert 0 < feedback["y"].sum() < len(feedback)  # verdicts of both kinds
    report = train(data_dir, models_root=root, promote=False, feedback=feedback)
    return data_dir, root, feedback, report, len(test)


def test_retraining_registers_a_challenger_without_promoting_it(retrained):
    _, root, feedback, report, _ = retrained
    assert report["version"] == "v2" and registry.versions(root) == ["v1", "v2"]
    assert registry.current_version(root) == "v1"  # promotion is a separate, deliberate step
    manifest = json.loads((root / "v2" / "manifest.json").read_text())
    assert manifest["parent"] == "v1"
    assert manifest["feedback"]["rows"] == len(feedback)
    assert manifest["feedback"]["fraud"] == int(feedback["y"].sum())
    assert manifest["feedback"]["fraud"] + manifest["feedback"]["legitimate"] == len(feedback)
    assert json.loads((root / "v1" / "manifest.json").read_text()).get("feedback") is None
    challenger = Shadow.load("v2", root, load_policy("v1"))
    risk, tier = challenger.score(feedback[list(FEATURES)].to_numpy(dtype=np.float64)[:1])
    assert 0 <= risk <= 1 and tier in ("allow", "warn", "step_up", "hold")


def test_a_retrained_model_is_judged_only_on_what_came_after_its_labels(retrained):
    data_dir, root, feedback, report, test_rows = retrained
    used = report["feedback"]
    assert used["test_rows_before"] == test_rows > used["test_rows_after_last_label"] > 0
    frame = load_frame(data_dir)
    frame = frame[(frame["fold"] == "test") & ~frame["ambiguous"]]
    later = frame[frame["ts"] > feedback["ts"].max()]
    assert used["test_rows_after_last_label"] == len(later)
    scores = pd.read_parquet(root / "v2" / "test_scores.parquet")
    assert set(scores["txn_id"]) == set(later["txn_id"])
    assert not set(scores["txn_id"]) & set(feedback["txn_id"])
    # The served version on the same rows, so the two can be compared like for like.
    compared = used["comparison_after_last_label"]
    assert used["parent"] == "v1" and set(compared) == {"parent", "retrained"}
    for side in compared.values():
        assert 0 <= side["pr_auc"] <= 1 and 0 <= side["precision"] <= 1


def test_feedback_that_cannot_be_trusted_is_refused(retrained):
    data_dir, root, feedback, _, _ = retrained
    for bad, message in (
        (feedback.drop(columns=["y_mule"]), "missing columns"),
        (pd.concat([feedback, feedback.head(1)]), "more than one label"),
        (feedback.assign(y=2), "must be 0 or 1"),
        # Labels up to the last test day leave nothing to judge the model on.
        (feedback.assign(ts=pd.Timestamp("2100-01-01")), "no test data is left"),
    ):
        with pytest.raises(ValueError, match=message):
            train(data_dir, models_root=root, promote=False, feedback=bad)
    assert registry.versions(root) == ["v1", "v2"]  # nothing half-written


# ------------------------------------------------------------------ fairness


def test_the_fairness_report_covers_region_account_age_and_balance(trained):
    data_dir, models_root, _ = trained
    report = insights.run(data_dir, models_root=models_root)
    fair = report["fairness"]
    for side, tables in (
        ("sender", ("region", "account_age", "balance_tier")),
        ("receiver", ("region", "account_age")),
    ):
        for name in tables:
            rows = fair[side][name]
            assert len(rows) >= 2, (side, name)
            for row in rows:
                assert row["false_alert_rate"] is None or 0 <= row["false_alert_rate"] <= 1
                assert row["false_alerts"] <= row["legitimate"] <= row["transactions"]
    # Every transaction falls in exactly one group of each table.
    for name in ("region", "account_age", "balance_tier"):
        assert sum(row["transactions"] for row in fair["sender"][name]) == report["rows"]
    regions = {row["group"] for row in fair["sender"]["region"]}
    assert regions <= {*insights.DIVISIONS.values(), "unknown"} and "Dhaka" in regions
    ages = [row["group"] for row in fair["sender"]["account_age"]]
    order = [label for _, label in insights.AGE_BANDS]
    assert [a for a in ages if a != "unknown"] == [label for label in order if label in ages]
    # Some group is always at or above the average it is part of (none, with no false alerts).
    largest = fair["sender_largest_ratio"]
    assert largest >= 1 if fair["overall"]["false_alert_rate"] else largest is None
