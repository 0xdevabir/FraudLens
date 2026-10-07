"""Pure helpers behind report tracking and API keys: no database or Redis needed."""

import pytest

from fraudlens.platform import apikeys, sandbox, tracking
from fraudlens.platform.models import Case


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("FL-ABCD-2345", "FL-ABCD-2345"),
        ("fl-abcd-2345", "FL-ABCD-2345"),
        ("ABCD 2345", "FL-ABCD-2345"),
        ("flabcd2345", "FL-ABCD-2345"),
    ],
)
def test_a_reference_typed_loosely_is_read_the_same_way(typed, expected):
    assert tracking.normalise(typed) == expected


def test_the_status_comes_from_the_case_and_says_no_more_than_that():
    def case(status, verdict=None, assigned=None):
        return Case(status=status, verdict=verdict, assigned_to=assigned)

    assert tracking.status_of(None) == "received"
    assert tracking.status_of(case("open")) == "received"  # nobody has picked it up
    assert tracking.status_of(case("open", assigned=3)) == "investigating"
    assert tracking.status_of(case("escalated")) == "investigating"
    assert tracking.status_of(case("closed", "confirmed_fraud")) == "action_taken"
    for verdict in ("legitimate", "inconclusive"):
        assert tracking.status_of(case("closed", verdict)) == "closed"


def test_every_status_has_wording_in_both_languages():
    assert set(tracking.STATUS_TEXT) == {"received", "investigating", "action_taken", "closed"}
    for parts in tracking.STATUS_TEXT.values():
        for text in parts.values():
            assert text["en"] and text["bn"]


def test_a_key_shows_once_and_is_stored_only_as_a_hash():
    raw, prefix = apikeys.generate()
    assert raw.startswith(prefix + "_") and prefix.startswith("flk_") and len(raw) > 40
    assert apikeys.hash_key(raw) != raw and len(apikeys.hash_key(raw)) == 64
    assert apikeys.generate()[0] != raw


@pytest.mark.parametrize(
    ("cents", "tier"),
    [
        (0.01, "allow"),
        (100.02, "warn"),
        (5000.03, "step_up"),
        (12.04, "hold"),
        (12.0, "allow"),
        (7.5, "allow"),
    ],
)
def test_a_sandbox_amount_picks_the_tier(cents, tier):
    assert sandbox.tier_for(cents) == tier
