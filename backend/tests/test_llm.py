"""Case notes from the provider chain. Offline: every provider is a fake or a mock transport."""

import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from fraudlens.config import Settings
from fraudlens.decision import Draft, narrate, template
from fraudlens.decision.llm import (
    LABELS,
    ChatCompletionsProvider,
    GeminiProvider,
    KnowledgeError,
    NovaProvider,
    Prompt,
    ProviderChain,
    ProviderError,
    Step,
    build_chain,
    build_prompt,
    check_facts,
    check_note,
    load_knowledge,
)
from fraudlens.decision.llm.chain import tidy
from fraudlens.decision.llm.knowledge import KNOWLEDGE_PATH, parse
from fraudlens.decision.narrative import _BAND, _TYPOLOGY_BN
from fraudlens.decision.policy import TIERS
from fraudlens.decision.reasons import RULE_CODES

EVIDENCE = {
    "transaction": {
        "id": 77,
        "type": "SEND_MONEY",
        "amount": 12500.0,
        "time": "2026-04-06 03:34",
        "channel": "app",
        "district": "Dhaka",
        "sender": "W***6468",
        "receiver": "W***0292",
    },
    "decision": {
        "tier": "hold",
        "action": "hold_for_review",
        "mode": "model",
        "decided_by": "model",
        "risk_band": "very high",
        "risk_score": 96,
        "risk_scale": 100,
        "human_review": True,
        "cooling_off_minutes": None,
        "review_sla_minutes": 30,
        "fallback_signals": [],
    },
    "reasons": [
        {
            "code": "RECIPIENT_NEW_WALLET",
            "source": "model",
            "direction": "raises",
            "title_en": "Age and own activity of the receiving wallet",
            "title_bn": "প্রাপক ওয়ালেটের বয়স ও নিজস্ব লেনদেন",
            "detail_en": "the receiving wallet was opened 4 days ago",
            "detail_bn": "প্রাপক ওয়ালেট ৪ দিন আগে খোলা হয়েছে",
        }
    ],
    "similar_cases": [
        {"case_id": 608, "typology": "account_takeover", "similarity": 0.988, "loss": 23240.0}
    ],
    "recommended_actions": [{"id": "CALL_SENDER", "en": "Call the sender.", "bn": "ফোন করুন।"}],
}

GOOD_EN = (
    "What happened: W***6468 tried to send ৳12,500 to W***0292.\n\n"
    "Why it is risky: risk is very high, 96 out of 100; the receiving wallet was opened "
    "4 days ago.\n\n"
    "What happens next: the money is held until an analyst decides, within 30 minutes."
)
GOOD_BN = (
    "কী ঘটেছে: W***6468 থেকে W***0292-এ ৳১২,৫০০ পাঠানোর চেষ্টা হয়েছে।\n\n"
    "কেন ঝুঁকিপূর্ণ: ঝুঁকি খুব বেশি, ১০০-এর মধ্যে ৯৬; প্রাপক ওয়ালেট ৪ দিন আগে খোলা হয়েছে।\n\n"
    "এরপর কী: বিশ্লেষক ৩০ মিনিটের মধ্যে সিদ্ধান্ত না নেওয়া পর্যন্ত টাকা আটকে থাকবে।"
)
INVENTED = GOOD_EN.replace("96 out of 100", "96 out of 100 after 14 reports")


@pytest.fixture(scope="module")
def knowledge():
    return load_knowledge()


# ------------------------------------------------------------- knowledge base


def test_the_knowledge_base_covers_both_languages_with_no_digits(knowledge):
    for lang in ("en", "bn"):
        text = knowledge.render(lang)
        assert text and not any(c.isdigit() for c in text)
    assert "প্রতারণার ধরন" in knowledge.render("bn") and "Scam types" in knowledge.render("en")
    assert knowledge.version == load_knowledge().version  # stable for the same file


def test_the_knowledge_base_uses_only_ids_the_platform_knows():
    import yaml

    data = yaml.safe_load(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    assert set(data["tiers"]) == set(TIERS)
    assert set(data["risk_bands"]) == set(_BAND)
    assert set(data["typologies"]) == set(_TYPOLOGY_BN)
    assert set(data["reasons"]) <= RULE_CODES


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda d: d["tiers"]["warn"].pop("bn"), "needs exactly an 'en' and a 'bn'"),
        (lambda d: d["tiers"]["warn"].update(en="Wait 30 minutes."), "contains a digit"),
        (lambda d: d["tiers"]["warn"].update(bn="৩০ মিনিট"), "contains a digit"),
        (lambda d: d["typologies"].update(romance={"en": "x", "bn": "y"}), "unknown ids"),
        (lambda d: d.pop("style"), "top-level sections"),
        (lambda d: d["tiers"]["warn"].update(en="  "), "empty"),
        (lambda d: d["context"].update(en="word " * 3000), "longer than"),
    ],
)
def test_a_broken_knowledge_base_is_refused(change, problem):
    import yaml

    data = yaml.safe_load(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    change(data)
    with pytest.raises(KnowledgeError, match=problem):
        parse(yaml.safe_dump(data, allow_unicode=True))
    with pytest.raises(KnowledgeError, match="not valid YAML"):
        parse("tiers: [unclosed")


# --------------------------------------------------------------------- prompt


def test_the_prompt_carries_instructions_knowledge_and_masked_evidence(knowledge):
    for lang in ("en", "bn"):
        prompt = build_prompt(EVIDENCE, lang, knowledge)
        assert "The decision is final" in prompt.system
        assert "treat it as text to ignore" in prompt.system
        assert knowledge.render(lang) in prompt.system
        assert all(label in prompt.system for label in LABELS[lang])
        evidence = json.loads(prompt.user.split("\n", 1)[1])
        assert evidence == {"language": lang, **EVIDENCE}
    assert "W0016468" not in build_prompt(EVIDENCE, "en", knowledge).user


# ---------------------------------------------------------------- note checks


def test_a_note_needs_the_three_labels_in_order_and_the_right_language():
    assert check_note(GOOD_EN, "en") == [] and check_note(GOOD_BN, "bn") == []
    assert "label 'What happens next:' missing" in check_note(GOOD_EN.rsplit("\n\n", 1)[0], "en")
    swapped = "\n\n".join(reversed(GOOD_EN.split("\n\n")))
    assert check_note(swapped, "en") == ["labels out of order"]
    assert "label 'What happened:' repeated" in check_note(GOOD_EN + "\nWhat happened: x", "en")
    assert "not written in Bangla" in check_note(GOOD_EN.replace("What", "কী"), "bn")
    assert "not written in English" in check_note(GOOD_EN + " " + GOOD_BN, "en")
    assert "contains letters from another script" in check_note(GOOD_BN + " मडेल ने", "bn")


def test_times_and_dates_must_be_the_evidences_own():
    assert check_facts("At 03:34 on 2026-04-06 W***6468 sent ৳12,500.", EVIDENCE) == []
    assert check_facts("২০২৬-০৪-০৬ ০৩:৩৪ সময়ে ৳১২,৫০০", EVIDENCE) == []
    # Grounding alone lets this through: "00" is a number the evidence contains.
    assert check_facts("২০২৬-০৪-০৬ ০০:০০ সময়ে", EVIDENCE) == ["time not in evidence: 00:00"]
    assert check_facts("on 2026-04-07", EVIDENCE) == ["date not in evidence: 2026-04-07"]
    assert check_facts("৳৯,১৩0", EVIDENCE) == ["a number mixes Bangla and Latin digits"]


def test_gateway_status_messages_are_not_notes():
    reply = "Our neural processing nodes are temporarily recalibrating. Please try again shortly."
    assert check_note(reply, "en")


def test_tidy_removes_bold_but_keeps_masked_identifiers():
    text = "**What happened:** W***6468 sent **৳12,500** to W***0292.\r\nend  "
    assert tidy(text) == "What happened: W***6468 sent ৳12,500 to W***0292.\nend"


# ------------------------------------------------------------------ providers


def _mock(handler):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped), seen


PROMPT = Prompt(system="SYSTEM", user="USER")


def test_nova_sends_one_prompt_with_the_key_header_and_returns_the_reply():
    body = {"success": True, "model": "nova_g", "reply": " A note. ", "usage": {"total_tokens": 9}}
    transport, seen = _mock(lambda r: httpx.Response(200, json=body))
    nova = NovaProvider("nova_live_secret", "nova_g", "https://nova.test/api/chat", transport)
    assert nova.generate(PROMPT, timeout=5) == "A note."
    (request,) = seen
    assert request.headers["x-api-key"] == "nova_live_secret"
    assert json.loads(request.content) == {"prompt": "SYSTEM\n\nUSER", "model": "nova_g"}


@pytest.mark.parametrize(
    ("response", "kind", "retryable"),
    [
        # The gateway's real "temporarily recalibrating" answer: 200, success, no usage.
        (httpx.Response(200, json={"success": True, "reply": "Try again"}), "no_completion", True),
        (httpx.Response(200, json={"success": False, "reply": "x"}), "bad_response", True),
        (httpx.Response(200, json={"success": True, "reply": " ", "usage": {}}), "empty", True),
        (httpx.Response(200, text="<html>"), "bad_response", True),
        (httpx.Response(403, json={"error": {"code": "invalid_api_key"}}), "auth", False),
        (httpx.Response(400, json={"error": {"code": "invalid_model"}}), "rejected", False),
        (httpx.Response(429), "unavailable", True),
        (httpx.Response(503), "unavailable", True),
    ],
)
def test_nova_failures_say_whether_to_try_again(response, kind, retryable):
    transport, _ = _mock(lambda r: response)
    nova = NovaProvider("k", "nova_g", "https://nova.test/api/chat", transport)
    with pytest.raises(ProviderError) as caught:
        nova.generate(PROMPT, timeout=5)
    assert (caught.value.kind, caught.value.retryable) == (kind, retryable)


def test_timeouts_and_network_errors_are_worth_retrying():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    for handler, kind in ((timeout, "timeout"), (refused, "network")):
        nova = NovaProvider("k", "nova_g", "https://nova.test", httpx.MockTransport(handler))
        with pytest.raises(ProviderError) as caught:
            nova.generate(PROMPT, timeout=5)
        assert caught.value.kind == kind and caught.value.retryable


def test_a_key_never_reaches_an_error_message_or_the_log(caplog):
    transport, _ = _mock(lambda r: httpx.Response(403))
    nova = NovaProvider("nova_live_topsecret", "nova_g", "https://nova.test", transport)
    chain = ProviderChain([Step(nova, 1, True)], load_knowledge(), sleep=lambda s: None)
    with caplog.at_level(logging.DEBUG):
        draft = chain(EVIDENCE, "en")
    assert draft.text is None
    assert "topsecret" not in caplog.text


def _gemini_reply(parts, finish="STOP"):
    return {"candidates": [{"content": {"parts": parts}, "finishReason": finish}]}


def test_gemini_sends_system_and_thinking_level_and_skips_thoughts():
    reply = _gemini_reply([{"text": "plan", "thought": True}, {"text": "The note."}])
    transport, seen = _mock(lambda r: httpx.Response(200, json=reply))
    gemini = GeminiProvider("g-key", "gemini-test", "low", transport)
    assert gemini.generate(PROMPT, timeout=5) == "The note."
    (request,) = seen
    assert request.url.path == "/v1beta/models/gemini-test:generateContent"
    assert request.headers["x-goog-api-key"] == "g-key"
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "SYSTEM"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "USER"}]}]
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    no_thinking = GeminiProvider("g-key", "gemini-test", None, transport)
    no_thinking.generate(PROMPT, timeout=5)
    assert "thinkingConfig" not in json.loads(seen[-1].content)["generationConfig"]


@pytest.mark.parametrize(
    ("body", "kind", "retryable"),
    [
        (_gemini_reply([{"text": "cut"}], "MAX_TOKENS"), "incomplete", True),
        (_gemini_reply([{"text": "no"}], "SAFETY"), "refused", False),
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "refused", False),
        ({"candidates": []}, "bad_response", True),
    ],
)
def test_gemini_accepts_only_a_finished_answer(body, kind, retryable):
    transport, _ = _mock(lambda r: httpx.Response(200, json=body))
    with pytest.raises(ProviderError) as caught:
        GeminiProvider("k", "m", None, transport).generate(PROMPT, timeout=5)
    assert (caught.value.kind, caught.value.retryable) == (kind, retryable)


def _chat_reply(content, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


def test_chat_completions_send_bearer_and_messages_and_drop_think_blocks():
    transport, seen = _mock(
        lambda r: httpx.Response(200, json=_chat_reply("<think>hmm</think>\nThe note."))
    )
    groq = ChatCompletionsProvider(
        "groq", "https://groq.test/v1/chat/completions", "gsk", "m", {"X-Title": "T"}, transport
    )
    assert groq.generate(PROMPT, timeout=5) == "The note."
    (request,) = seen
    assert request.headers["authorization"] == "Bearer gsk" and request.headers["x-title"] == "T"
    assert json.loads(request.content)["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "USER"},
    ]


@pytest.mark.parametrize(
    ("body", "kind", "retryable"),
    [
        (_chat_reply("cut", "length"), "incomplete", True),
        (_chat_reply("no", "content_filter"), "refused", False),
        (_chat_reply(None), "bad_response", True),
        ({"error": {"message": "rate limited", "code": 429}}, "unavailable", True),
        ({"choices": []}, "bad_response", True),
    ],
)
def test_chat_completions_accept_only_a_finished_answer(body, kind, retryable):
    transport, _ = _mock(lambda r: httpx.Response(200, json=body))
    provider = ChatCompletionsProvider("openrouter", "https://or.test", "k", "m", None, transport)
    with pytest.raises(ProviderError) as caught:
        provider.generate(PROMPT, timeout=5)
    assert (caught.value.kind, caught.value.retryable) == (kind, retryable)


# ---------------------------------------------------------------------- chain


class Scripted:
    """A provider that plays back answers: a string is a reply, an exception is raised."""

    def __init__(self, name, *answers, clock=None, takes=0.0):
        self.name, self.answers, self.calls, self.timeouts = name, list(answers), 0, []
        self.clock, self.takes = clock, takes

    def generate(self, prompt, timeout):
        self.calls += 1
        self.timeouts.append(timeout)
        if self.clock is not None:
            self.clock.now += self.takes
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _error(name, retryable=True, kind="unavailable"):
    return ProviderError(name, kind, retryable)


def _chain(*steps, clock=None, **kw):
    clock = clock or Clock()
    return ProviderChain(steps, load_knowledge(), clock=clock, sleep=clock.sleep, **kw), clock


def test_the_primary_answers_and_no_backup_is_asked():
    nova, gemini = Scripted("nova", GOOD_EN), Scripted("gemini", GOOD_EN)
    chain, _ = _chain(Step(nova, 3, True), Step(gemini, 1, False))
    assert chain(EVIDENCE, "en") == Draft(GOOD_EN, provider="nova")
    assert (nova.calls, gemini.calls) == (1, 0)


def test_the_primary_is_retried_with_pauses_before_any_backup():
    nova = Scripted("nova", _error("nova"), _error("nova"), GOOD_BN)
    gemini = Scripted("gemini", GOOD_BN)
    chain, clock = _chain(Step(nova, 3, True), Step(gemini, 1, False))
    assert chain(EVIDENCE, "bn") == Draft(GOOD_BN, provider="nova")
    assert (nova.calls, gemini.calls) == (3, 0)
    assert clock.now == pytest.approx(1.5)  # paused 0.5 s, then 1 s


def test_backups_are_asked_in_order_once_the_primary_has_used_its_attempts():
    nova = Scripted("nova", _error("nova"))
    gemini = Scripted("gemini", _error("gemini"))
    groq = Scripted("groq", GOOD_EN)
    openrouter = Scripted("openrouter", GOOD_EN)
    chain, _ = _chain(
        Step(nova, 3, True),
        Step(gemini, 1, False),
        Step(groq, 1, False),
        Step(openrouter, 1, False),
    )
    assert chain(EVIDENCE, "en") == Draft(GOOD_EN, provider="groq")
    assert (nova.calls, gemini.calls, groq.calls, openrouter.calls) == (3, 1, 1, 0)


def test_a_bad_key_is_not_retried():
    nova = Scripted("nova", _error("nova", retryable=False, kind="auth"))
    gemini = Scripted("gemini", GOOD_EN)
    chain, _ = _chain(Step(nova, 3, True), Step(gemini, 1, False))
    assert chain(EVIDENCE, "en").provider == "gemini"
    assert nova.calls == 1


def test_an_ungrounded_or_misshapen_note_is_retried_then_passed_on():
    wrong_time = GOOD_EN.replace("tried", "at 00:00 tried")
    nova = Scripted("nova", INVENTED, "Our nodes are recalibrating.", wrong_time, GOOD_EN)
    chain, _ = _chain(Step(nova, 4, True))
    assert chain(EVIDENCE, "en") == Draft(GOOD_EN, provider="nova")
    assert nova.calls == 4

    always_invents = Scripted("nova", INVENTED)
    gemini = Scripted("gemini", GOOD_EN)
    chain, _ = _chain(Step(always_invents, 3, True), Step(gemini, 1, False))
    assert chain(EVIDENCE, "en").provider == "gemini"


def test_when_every_provider_fails_the_template_is_used_with_the_reasons():
    nova = Scripted("nova", INVENTED)
    gemini = Scripted("gemini", _error("gemini"))
    chain, _ = _chain(Step(nova, 2, True), Step(gemini, 1, False))
    draft = chain(EVIDENCE, "en")
    assert draft.text is None and draft.problems == (
        "nova: number not in evidence: 14",
        "nova: number not in evidence: 14",
    )
    note = narrate(EVIDENCE, chain, "en")
    assert note["source"] == "template" and note["text"] == template(EVIDENCE)
    assert note["rejected"] == list(draft.problems) and "provider" not in note

    silent, _ = _chain(Step(Scripted("nova", _error("nova")), 1, True))
    assert narrate(EVIDENCE, silent, "bn")["rejected"] == ["no text returned"]


def test_narrate_reports_which_provider_wrote_the_note():
    chain, _ = _chain(Step(Scripted("nova", GOOD_BN), 1, True))
    assert narrate(EVIDENCE, chain, "bn") == {
        "text": GOOD_BN,
        "source": "llm",
        "lang": "bn",
        "rejected": [],
        "provider": "nova",
    }


def test_every_request_starts_again_at_the_primary_even_after_many_failures():
    nova = Scripted("nova", _error("nova"))
    gemini = Scripted("gemini", GOOD_EN)
    chain, _ = _chain(Step(nova, 3, True), Step(gemini, 1, False))
    for _ in range(4):
        assert chain(EVIDENCE, "en").provider == "gemini"
    assert nova.calls == 12  # three attempts on every request, never skipped


def test_a_failing_backup_is_skipped_for_a_while_then_tried_again():
    clock = Clock()
    nova = Scripted("nova", _error("nova", retryable=False))
    gemini = Scripted("gemini", _error("gemini"))
    groq = Scripted("groq", GOOD_EN)
    chain, _ = _chain(
        Step(nova, 1, True), Step(gemini, 1, False), Step(groq, 1, False),
        clock=clock, trip_after=3, cooldown_seconds=60,
    )  # fmt: skip
    for _ in range(5):
        assert chain(EVIDENCE, "en").provider == "groq"
    assert gemini.calls == 3  # skipped after its third failure in a row
    clock.now += 61
    chain(EVIDENCE, "en")
    assert gemini.calls == 4


def test_the_deadline_bounds_the_whole_note():
    clock = Clock()
    nova = Scripted("nova", _error("nova"), clock=clock, takes=25.0)
    gemini = Scripted("gemini", GOOD_EN, clock=clock, takes=1.0)
    chain, _ = _chain(
        Step(nova, 3, True), Step(gemini, 1, False),
        clock=clock, deadline_seconds=40, attempt_timeout_seconds=25,
    )  # fmt: skip
    draft = chain(EVIDENCE, "en")
    # 25 s, pause, then only ~14 s left for the second attempt; nothing for a third.
    assert nova.timeouts == [25, pytest.approx(14.5)]
    assert draft.text is None and gemini.calls == 0


# ---------------------------------------------------------------- from settings

NO_KEYS = {f"{name}_api_key": None for name in ("nova", "gemini", "groq", "openrouter")}


def test_no_key_means_no_chain():
    assert build_chain(Settings(**NO_KEYS)) is None


def test_the_chain_follows_the_configured_order_and_skips_providers_without_keys():
    keys = NO_KEYS | {
        "nova_api_key": SecretStr("n"),
        "groq_api_key": SecretStr("g"),
        "openrouter_api_key": SecretStr("o"),
    }
    chain = build_chain(Settings(**keys, llm_primary_attempts=4))
    assert chain.names == ["nova", "groq", "openrouter"]
    assert [(s.attempts, s.always) for s in chain.steps] == [(4, True), (1, False), (1, False)]
    assert chain.cache_tag == f"kb-{load_knowledge().version}"

    reordered = build_chain(Settings(**keys, llm_providers=["groq", "nova"]))
    assert reordered.names == ["groq", "nova"] and reordered.steps[0].always
    assert build_chain(Settings(**keys | {"nova_api_key": SecretStr("  ")})).names[0] == "groq"
