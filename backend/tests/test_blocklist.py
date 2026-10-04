"""The blocklist's value handling: no database needed."""

import pytest

from fraudlens.decision import load_policy
from fraudlens.platform import blocklist, translations
from fraudlens.platform.audit import WorkflowError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01712345678", "01712345678"),
        ("+8801712345678", "01712345678"),
        ("880 1712-345678", "01712345678"),
        ("1712345678", "01712345678"),
    ],
)
def test_a_phone_number_is_stored_one_way(raw, expected):
    assert blocklist.normalise("phone", raw) == expected


@pytest.mark.parametrize("raw", ["0171234567", "02123456789", "abc", "01212345678"])
def test_a_value_that_is_not_a_phone_number_is_refused(raw):
    with pytest.raises(WorkflowError) as error:
        blocklist.normalise("phone", raw)
    assert error.value.status == 422


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://WWW.Evil.Example.com/login?x=1", "evil.example.com"),
        ("evil.example.com", "evil.example.com"),
        ("http://evil.example.com:8080/", "evil.example.com"),
    ],
)
def test_a_link_is_stored_as_its_host(raw, expected):
    assert blocklist.normalise("url", raw) == expected


@pytest.mark.parametrize("raw", ["not a link", "http://", "localhost", "a b.com"])
def test_something_that_is_not_a_domain_is_refused(raw):
    with pytest.raises(WorkflowError):
        blocklist.normalise("url", raw)


def test_a_wallet_id_follows_the_platform_pattern():
    assert blocklist.normalise("wallet", " W1234 ") == "W1234"
    with pytest.raises(WorkflowError):
        blocklist.normalise("wallet", "W 12/34")


def test_an_entry_lists_a_wallet_until_it_expires():
    entries = {"W1": None, "W2": 1_000.0}
    assert blocklist.listed(entries, "W1", 5e9)
    assert blocklist.listed(entries, "W2", 999.0) and not blocklist.listed(entries, "W2", 1_000.0)
    assert not blocklist.listed(entries, "W3", 0.0) and not blocklist.listed(None, "W1", 0.0)


def test_every_bangla_text_has_a_stable_key():
    items = translations.texts(load_policy("v3"))
    keys = [i["key"] for i in items]
    assert len(keys) == len(set(keys)) and all(i["bn"] and i["en"] for i in items)
    assert {"rule:R08_RECIPIENT_ON_BLOCKLIST", "message:scam.warn"} <= set(keys)
    assert translations.digest("এক") != translations.digest("দুই")
