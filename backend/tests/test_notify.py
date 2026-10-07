"""Signing and URL checks for webhooks: no database needed."""

import pytest

from fraudlens.platform import notify

SECRET = "whsec_" + "ab" * 24
BODY = b'{"id":"1","type":"decision.hold"}'


def test_a_signature_verifies_only_for_the_same_secret_and_body():
    header = notify.sign(SECRET, BODY, 1_000)
    assert header.startswith("t=1000,v1=")
    assert notify.verify(SECRET, header, BODY, now=1_000)
    assert not notify.verify(SECRET, header, BODY + b" ", now=1_000)
    assert not notify.verify("whsec_other", header, BODY, now=1_000)


def test_an_old_or_malformed_signature_is_refused():
    header = notify.sign(SECRET, BODY, 1_000)
    assert notify.verify(SECRET, header, BODY, now=1_000 + 299)
    assert not notify.verify(SECRET, header, BODY, now=1_000 + 301)  # a replayed capture
    for bad in ("", "garbage", "t=abc,v1=00", "v1=00"):
        assert not notify.verify(SECRET, bad, BODY, now=1_000)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/hook",
        "https:///nohost",
        "https://user:pw@example.com/",
        "http://example.com/hook",  # plain http
        "https://127.0.0.1/hook",
        "https://[::1]/hook",
        "https://10.1.2.3/hook",
        "https://192.168.0.10/hook",
        "https://169.254.169.254/latest",
        "https://localhost/hook",
    ],
)
def test_a_url_that_could_reach_the_internal_network_is_refused(url):
    with pytest.raises(notify.UnsafeUrl):
        notify.check_url(url)


def test_local_testing_may_use_private_addresses_but_never_credentials():
    notify.check_url("http://127.0.0.1:9000/hook", allow_private=True)
    with pytest.raises(notify.UnsafeUrl):
        notify.check_url("http://u:p@127.0.0.1/", allow_private=True)


def test_secrets_are_unique_and_prefixed():
    a, b = notify.new_secret(), notify.new_secret()
    assert a != b and a.startswith("whsec_") and len(a) > 40
