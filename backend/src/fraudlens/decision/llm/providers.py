"""Clients for the language models that may write a case note.

Each provider turns a `Prompt` into text or raises `ProviderError`, never anything
else, so the chain can always move on. A reply counts only if the provider says
the model finished normally: a truncated, refused or empty answer is an error.
Keys are sent in headers and never logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

# Statuses where the same request may succeed a moment later.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429})
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass(frozen=True)
class Prompt:
    system: str  # instructions and the knowledge base
    user: str  # the evidence


class ProviderError(Exception):
    """A provider gave no usable text. `retryable`: the same call may work if repeated."""

    def __init__(self, provider: str, kind: str, retryable: bool, detail: str = "") -> None:
        super().__init__(f"{provider}: {kind}" + (f" ({detail})" if detail else ""))
        self.provider, self.kind, self.retryable = provider, kind, retryable


class HttpProvider:
    name = "http"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport  # tests pass a mock; None is the network

    def generate(self, prompt: Prompt, timeout: float) -> str:
        raise NotImplementedError

    def _post(self, url: str, headers: dict[str, str], body: dict, timeout: float) -> dict:
        try:
            with httpx.Client(transport=self.transport, timeout=timeout) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, "timeout", True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, "network", True, type(exc).__name__) from exc
        status = response.status_code
        if status in (401, 403):
            raise ProviderError(self.name, "auth", False, f"HTTP {status}")
        if status in _RETRYABLE_STATUS or status >= 500:
            raise ProviderError(self.name, "unavailable", True, f"HTTP {status}")
        if status != 200:
            raise ProviderError(self.name, "rejected", False, f"HTTP {status}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(self.name, "bad_response", True, "not JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError(self.name, "bad_response", True, "not a JSON object")
        return data

    def _text(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ProviderError(self.name, "bad_response", True, "no text")
        text = _THINK.sub("", value).strip()
        if not text:
            raise ProviderError(self.name, "empty", True)
        return text


class NovaProvider(HttpProvider):
    """The Nova gateway: one prompt field, so instructions and evidence travel together."""

    name = "nova"

    def __init__(self, api_key: str, model: str, url: str, transport=None) -> None:
        super().__init__(transport)
        self.api_key, self.model, self.url = api_key, model, url

    def generate(self, prompt: Prompt, timeout: float) -> str:
        data = self._post(
            self.url,
            {"x-api-key": self.api_key},
            {"prompt": f"{prompt.system}\n\n{prompt.user}", "model": self.model},
            timeout,
        )
        if data.get("success") is not True:
            raise ProviderError(self.name, "bad_response", True, "success is not true")
        # The gateway also answers 200 with a status message ("temporarily
        # recalibrating") that no model wrote; only a real completion reports usage.
        if not isinstance(data.get("usage"), dict):
            raise ProviderError(self.name, "no_completion", True, "reply without usage")
        return self._text(data.get("reply"))


class GeminiProvider(HttpProvider):
    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(
        self, api_key: str, model: str, thinking_level: str | None = None, transport=None
    ) -> None:
        super().__init__(transport)
        self.api_key, self.model, self.thinking_level = api_key, model, thinking_level

    def generate(self, prompt: Prompt, timeout: float) -> str:
        config: dict = {"temperature": 0.2, "maxOutputTokens": 8192}
        if self.thinking_level:  # Gemini 3 models think at length unless told otherwise
            config["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        data = self._post(
            self.URL.format(model=self.model),
            {"x-goog-api-key": self.api_key},
            {
                "systemInstruction": {"parts": [{"text": prompt.system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt.user}]}],
                "generationConfig": config,
            },
            timeout,
        )
        feedback = data.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise ProviderError(self.name, "refused", False, str(feedback["blockReason"]))
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ProviderError(self.name, "bad_response", True, "no candidates")
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        reason = first.get("finishReason")
        if reason == "MAX_TOKENS":
            raise ProviderError(self.name, "incomplete", True, "MAX_TOKENS")
        if reason != "STOP":
            raise ProviderError(self.name, "refused", False, str(reason))
        content = first.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise ProviderError(self.name, "bad_response", True, "no parts")
        return self._text(
            "".join(
                p["text"]
                for p in parts
                if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought")
            )
        )


class ChatCompletionsProvider(HttpProvider):
    """The OpenAI chat-completions format, which Groq and OpenRouter both serve."""

    def __init__(
        self,
        name: str,
        url: str,
        api_key: str,
        model: str,
        headers: dict[str, str] | None = None,
        transport=None,
    ) -> None:
        super().__init__(transport)
        self.name, self.url, self.api_key, self.model = name, url, api_key, model
        self.headers = headers or {}

    def generate(self, prompt: Prompt, timeout: float) -> str:
        data = self._post(
            self.url,
            {"Authorization": f"Bearer {self.api_key}", **self.headers},
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                "temperature": 0.2,
                "max_tokens": 4096,
            },
            timeout,
        )
        if isinstance(data.get("error"), dict):  # some gateways report errors with 200
            raise ProviderError(self.name, "unavailable", True, "error in body")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError(self.name, "bad_response", True, "no choices")
        first = choices[0]
        reason = first.get("finish_reason")
        if reason == "length":
            raise ProviderError(self.name, "incomplete", True, "length")
        if reason != "stop":
            raise ProviderError(self.name, "refused", False, str(reason))
        message = first.get("message")
        return self._text(message.get("content") if isinstance(message, dict) else None)


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
