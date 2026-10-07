"""Scam intelligence: the taxonomy, the message classifier, the link and payment-proof checks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from fraudlens.config import Settings
from fraudlens.intel import corpus, links, proof
from fraudlens.intel.analyze import check_message
from fraudlens.intel.attribute import categorise, categorise_report
from fraudlens.intel.cues import load_cues, matched
from fraudlens.intel.taxonomy import TAXONOMY_PATH, Taxonomy, load_taxonomy
from fraudlens.intel.text import MANIFEST_FILE, intel_dir, load_text_model, normalise

TAXONOMY = load_taxonomy()
BRANDS = TAXONOMY.brands

OTP_SCAM = (
    "Sir ami upay head office theke bolchi. Apnar account block hoye jabe, "
    "ekhoni OTP code ta bolun."
)
OTP_NOTICE = "Your upay OTP is 482913. Never share your OTP or PIN with anyone, even upay staff."
LUNCH = "Dupure ki khaba? Ami 1 tar dike ber hobo, office er niche theko."


# ------------------------------------------------------------------ taxonomy


def test_the_taxonomy_covers_the_eight_categories_and_every_older_name():
    assert [c.number for c in TAXONOMY.categories] == list(range(1, 9))
    assert all(c.advice.en and c.advice.bn and c.bangladesh for c in TAXONOMY.categories)
    # Every scenario the decision engine names and every category a customer can pick is mapped.
    assert set(TAXONOMY.scenarios) == {"scam", "takeover", "unusual_access", "cash_out"}
    from fraudlens.api.schemas import ScamReport

    picks = set(ScamReport.model_fields["category"].annotation.__args__)
    assert picks == set(TAXONOMY.report_categories)
    # Each category has at least one detector, and between them every detector is used.
    assert {d for c in TAXONOMY.categories for d in c.detectors} == {
        "transaction", "text", "links", "ledger",
    }  # fmt: skip


def test_a_taxonomy_that_names_an_unknown_category_is_rejected():
    import yaml

    raw = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))
    raw["scenarios"]["scam"] = ["not_a_category"]
    with pytest.raises(ValueError, match="unknown categories"):
        Taxonomy.model_validate(raw)


# -------------------------------------------------------------------- corpus


def test_the_corpus_is_reproducible_and_keeps_its_test_wording_apart():
    first, second = corpus.generate(seed=3, per_template=4), corpus.generate(seed=3, per_template=4)
    assert first == second and first != corpus.generate(seed=4, per_template=4)
    assert {s.lang for s in first} == set(corpus.LANGUAGES)
    assert {s.split for s in first} == {"train", "val", "test", "unseen"}
    held_out = {f.id for f in corpus.load_families() if f.held_out}
    assert held_out and all((s.split == "unseen") == (s.family in held_out) for s in first)
    # Every category is taught, and harmless messages exist in every split.
    train = [s for s in first if s.split == "train"]
    assert {label for s in train for label in s.labels} == set(TAXONOMY.ids)
    assert all(any(not s.labels for s in first if s.split == split) for split in ("train", "test"))


# ---------------------------------------------------------------------- cues


def test_cues_tell_asking_for_a_code_from_warning_about_one():
    asked = {cue.id for cue in matched(normalise(OTP_SCAM))}
    warned = {cue.id for cue in matched(normalise(OTP_NOTICE))}
    assert "ask_secret" in asked
    assert "warn_secret" in warned and "ask_secret" not in warned
    assert not [cue for cue in matched(normalise(LUNCH)) if not cue.benign]
    assert all(cue.label["en"] and cue.label["bn"] for cue in load_cues())


def test_text_is_reduced_to_its_shape():
    assert normalise("কোড ১২৩৪ দিন http://x.xyz/a") == "কোড 0000 দিন urltoken"
    assert normalise("CALL 01711-223344") == "call 00000-000000"


# --------------------------------------------------------------------- links


@pytest.mark.parametrize(
    ("link", "flag", "level"),
    [
        ("https://www.upaybd.com/offers", "official", "none"),
        ("http://upay-verify.xyz/login", "lookalike_domain", "high"),
        ("https://bka5h.com.account-update.info/kyc", "lookalike_domain", "high"),
        ("http://203.0.113.9/pay", "ip_address", "high"),
        ("http://files.example.net/upay.apk", "apk_download", "high"),
        ("https://upaybd.com@elsewhere.example.net/login", "hidden_host", "high"),
        ("bit.ly/3xQz9", "shortener", "caution"),
    ],
)
def test_a_link_is_judged_from_its_text(link, flag, level):
    [found] = links.check_links(f"Click {link} now.", BRANDS)
    assert flag in found["flags"] and found["level"] == level


def test_ordinary_links_and_plain_text_are_not_flagged():
    assert links.check_links("See https://www.prothomalo.com/sports tonight", BRANDS) == [
        {"host": "www.prothomalo.com", "flags": [], "level": "none"}
    ]
    assert links.check_links("Meet at 5.30 pm. Bring Tk 1,500.00 please", BRANDS) == []
    many = " ".join(f"http://site{i}.example.com/" for i in range(40))
    assert len(links.check_links(many, BRANDS)) == links.MAX_LINKS


# --------------------------------------------------------------------- model


@pytest.fixture(scope="module")
def trained_text(tmp_path_factory):
    from fraudlens.intel.train import train

    directory = intel_dir(Settings(artifacts_dir=tmp_path_factory.mktemp("artifacts")))
    return directory, train(directory)


@pytest.fixture(scope="module")
def model(trained_text):
    from fraudlens.intel.text import TextModel

    return TextModel.load(trained_text[0])


def test_the_classifier_generalises_to_wording_and_scripts_it_never_saw(trained_text):
    _, report = trained_text
    test, unseen = report["test"], report["unseen"]
    # Loose floors: the exact figures are in the report and the docs.
    assert test["auc"] > 0.95 and test["caution"]["recall"] > 0.75
    assert test["caution"]["false_positive_rate"] < 0.03
    assert test["high"]["false_positive_rate"] <= 0.01
    assert unseen["auc"] > 0.85 and unseen["caution"]["recall"] > 0.5
    assert unseen["with_link_check"]["recall"] >= unseen["caution"]["recall"]
    assert set(test["categories"]) == set(TAXONOMY.ids) and report["limits"]


def test_a_message_check_names_the_fraud_and_explains_itself(model):
    scam = check_message(OTP_SCAM, model, TAXONOMY)
    assert scam["level"] == "high" and scam["risk"] > 0.8
    assert {c["id"] for c in scam["categories"]} & {"social_engineering", "account_takeover"}
    assert "ask_secret" in {s["id"] for s in scam["signals"]}
    assert scam["advice"]["en"] and scam["advice"]["bn"]

    for harmless in (OTP_NOTICE, LUNCH):
        found = check_message(harmless, model, TAXONOMY)
        assert found["level"] == "none" and found["advice"] is None, harmless
        assert found["categories"] == [] and found["signals"] == []


def test_a_bad_link_raises_the_level_whatever_the_words_say(model):
    text = "Your bill is ready. View it here: http://upay-verify.xyz/login"
    for served in (model, None):  # the link check works without the classifier
        found = check_message(text, served, TAXONOMY)
        assert found["level"] == "high"
        assert "phishing_malware" in {c["id"] for c in found["categories"]}
        assert found["links"][0]["flags"] == ["lookalike_domain"]
    assert check_message(LUNCH, None, TAXONOMY)["level"] == "none"


def test_a_model_trained_on_other_cues_is_not_served(trained_text, tmp_path):
    directory, _ = trained_text
    settings = Settings(artifacts_dir=directory.parent)
    assert load_text_model(settings) is not None
    assert load_text_model(Settings(artifacts_dir=tmp_path)) is None  # never trained
    stale = tmp_path / "intel"
    stale.mkdir()
    manifest = json.loads((directory / MANIFEST_FILE).read_text())
    (stale / MANIFEST_FILE).write_text(json.dumps(manifest | {"cues": manifest["cues"][:-1]}))
    assert load_text_model(Settings(artifacts_dir=tmp_path)) is None


# --------------------------------------------------------------------- proof


def _txn(**over):
    row = {
        "txn_id": 9001, "ts": datetime(2025, 3, 1, 12, tzinfo=UTC), "type": "SEND_MONEY",
        "sender_id": "W_buyer", "receiver_id": "W_seller", "amount": 2500.0,
        "status": "completed",
    }  # fmt: skip
    return SimpleNamespace(**row | over)


def test_a_claim_is_read_from_the_fields_or_the_pasted_message():
    sms = "Cash In Tk 2,500.00 from 01711000000 successful. TrxID 9001 at 12:01. Balance Tk 9,100"
    assert proof.read_claim("W_seller", None, None, sms) == proof.Claim("W_seller", 9001, 2500.0)
    assert proof.read_claim("W_seller", None, None, "আপনি ২,৫০০ টাকা পেয়েছেন").amount == 2500.0
    assert proof.read_claim("W_seller", "77", 10.0, sms) == proof.Claim("W_seller", 77, 10.0)
    # An ID that is not a number, or too large for the ledger, matches nothing.
    assert proof.read_claim("W_seller", "AB12CD34EF", None, "").txn_id is None
    assert proof.read_claim("W_seller", "9" * 25, None, "").txn_id is None


def test_a_payment_is_verified_only_against_the_ledger_and_only_for_its_receiver():
    claim = proof.Claim("W_seller", 9001, 2500.0)
    assert proof.judge(claim, _txn())["status"] == "verified"
    assert proof.judge(claim, None)["status"] == "not_found"

    short = proof.judge(claim, _txn(amount=250.0))
    assert short["status"] == "mismatch" and short["checks"]["amount_matches"] is False
    pending = proof.judge(claim, _txn(status="held"))
    assert pending["status"] == "mismatch" and pending["checks"]["completed"] is False

    # Someone else's payment looks exactly like one that does not exist.
    other = proof.judge(proof.Claim("W_curious", 9001, 2500.0), _txn())
    assert other == proof.judge(proof.Claim("W_curious", 9001, 2500.0), None)
    assert other["transaction"] is None


# --------------------------------------------------------------- attribution


def test_an_alert_is_named_from_the_evidence_already_on_it():
    cases = [
        {"typology": "lottery_fee", "similarity": 0.9},
        {"typology": "impersonation", "similarity": 0.6},
    ]
    named = {c["id"]: c["basis"] for c in categorise("scam", {"mule": 0.8}, cases, TAXONOMY)}
    assert named == {
        "social_engineering": ["scenario", "similar_cases"],
        "financial_network": ["mule_score"],
        "scam_campaign": ["similar_cases"],
    }
    assert [c["id"] for c in categorise("takeover", {"mule": 0.1}, [], TAXONOMY)] == [
        "account_takeover"
    ]
    assert categorise(None, None, None, TAXONOMY) == []


def test_a_customer_report_is_labelled_from_the_choice_and_the_description(model):
    by_choice = categorise_report("fake_payment", "see attached", None, TAXONOMY)
    assert [c["id"] for c in by_choice] == ["fake_payment"]
    assert by_choice[0]["basis"] == ["customer_choice"]
    read = categorise_report("other", OTP_SCAM, model, TAXONOMY)
    assert read and all(c["basis"] == ["description"] for c in read)
    assert categorise_report("other", LUNCH, None, TAXONOMY) == []


# ------------------------------------------------- cue-level reasons, both scripts


@pytest.mark.parametrize(
    "text, cue, phrase",
    [
        (OTP_SCAM, "ask_secret", "OTP code ta bolun"),
        ("আপনার অ্যাকাউন্ট বন্ধ হয়ে যাবে, এখনই ওটিপি কোডটি বলুন।", "ask_secret", "ওটিপি কোডটি বলুন"),
        ("Sir আপনার account block হবে, OTP টা এখনই বলুন।", "ask_secret", "OTP টা এখনই বলুন"),
        ("Prize পেতে ৫০০ টাকা processing fee পাঠান।", "pay_first", "fee পাঠান"),
    ],
)
def test_a_reason_points_at_the_words_that_showed_it(text, cue, phrase):
    from fraudlens.intel.explain import cue_phrases, mapped

    assert mapped(text)[0] == normalise(text)  # the offsets follow the same normalisation
    found = {c.id: phrases for c, phrases in cue_phrases(text)}
    assert cue in found
    spans = found[cue]
    assert any(phrase in p["text"] for p in spans), spans
    assert all(text[p["start"] : p["end"]].strip() == p["text"] for p in spans)


def test_a_flagged_message_carries_its_phrases_and_highlighted_words(model):
    found = check_message(OTP_SCAM, model, TAXONOMY)
    secret = next(s for s in found["signals"] if s["id"] == "ask_secret")
    assert secret["label"]["bn"] and secret["phrases"][0]["text"]
    words = found["highlights"]
    assert words and all(OTP_SCAM[w["start"] : w["end"]] == w["text"] for w in words)
    assert words == sorted(words, key=lambda w: -w["weight"])
    assert check_message(LUNCH, model, TAXONOMY)["highlights"] == []


# -------------------------------------------------------------- public corpus


def test_the_public_corpus_is_used_only_when_it_is_the_pinned_one(tmp_path):
    from fraudlens.intel import external

    assert external.load(tmp_path) is None  # nothing there: trained on the corpus alone
    (tmp_path / "train.parquet").write_bytes(b"not the pinned file")
    assert external.load(tmp_path) is None
    meta = external.provenance()
    assert meta["licence"] == "MIT" and len(meta["revision"]) == 40
    assert all(len(f["sha256"]) == 64 for f in meta["files"].values())


def test_every_scam_family_has_a_typology_and_each_is_held_out_once():
    families = corpus.load_families()
    scams = [f for f in families if f.labels]
    assert all(f.typology in corpus.TYPOLOGIES for f in scams)
    assert all(f.typology is None for f in families if not f.labels)
    held = {f.typology for f in scams if f.held_out}
    assert set(corpus.TYPOLOGIES) - {"other"} <= held


# ------------------------------------------------------- message, then payment


def test_a_payment_that_follows_a_flagged_message_is_linked_to_it():
    from datetime import timedelta

    from fraudlens.intel.memory import MessageMemory, targets

    wallets, phones, amounts = targets("ভুল করে ৳২,৫০০ গেছে, W0000331 নম্বরে ফেরত দিন বা 01712345678")
    assert wallets == {"W0000331"} and phones == {"01712345678"} and amounts == (2500.0,)

    memory, now = MessageMemory(), datetime(2026, 1, 1, 12, tzinfo=UTC)
    flagged = {"level": "high", "categories": [{"id": "social_engineering"}]}
    text = "Bhul kore 2,500 taka gese, W0000331 e ferot pathan"
    assert not memory.remember("W1", text, {"level": "none"}, now)  # not flagged: not kept
    assert memory.remember("W1", text, flagged, now)
    later = now + timedelta(minutes=10)
    assert memory.link("W1", "W0000331", 100.0, later)["matched"] == ["receiver"]
    assert memory.link("W1", "W0000999", 2500.0, later)["matched"] == ["amount"]
    assert memory.link("W1", "W0000999", 100.0, later) is None
    assert memory.link("W2", "W0000331", 2500.0, later) is None  # someone else's message
    assert memory.link("W1", "W0000331", 2500.0, now + timedelta(minutes=31)) is None


def test_a_linked_payment_is_warned_with_the_reason_in_both_languages():
    from dataclasses import dataclass, field

    from fraudlens.decision import load_policy
    from fraudlens.intel.memory import escalate

    @dataclass(frozen=True)
    class Decision:
        tier: str = "allow"
        action: str = "proceed"
        scenario: str | None = None
        customer_message: dict | None = None
        decided_by: str = "model"
        reasons: tuple = ()
        evidence: dict = field(default_factory=dict)

    policy = load_policy(Settings().policy_version)
    link = {"matched": ["receiver", "amount"], "minutes_ago": 4, "level": "high", "categories": []}
    warned = escalate(Decision(), link, policy)
    assert warned.tier == "warn" and warned.scenario == "scam"
    assert warned.action == policy.tiers["warn"].action and warned.customer_message["bn"]
    reason = warned.reasons[0]
    assert reason["code"] == "follows_flagged_message" and reason["direction"] == "raises"
    assert "4 minutes" in reason["detail_en"] and reason["detail_bn"]
    assert warned.evidence["message_link"] == link
    held = escalate(Decision(tier="hold", action="hold_for_review"), link, policy)
    assert held.tier == "hold" and held.reasons[0]["code"] == "follows_flagged_message"
