#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ModelResponse:
    reply_text: str
    raw_response: dict
    provider: str
    model: str


class ModelClientError(RuntimeError):
    pass


class ModelClient:
    def __init__(
        self,
        provider: str = "mock",
        model: str = "mock-gpt",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/") if base_url else None
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "ModelClient":
        provider = os.environ.get("APG_MODEL_PROVIDER", "mock")
        base_url = os.environ.get("APG_BASE_URL")
        model = os.environ.get("APG_MODEL_NAME", "mock-gpt")
        api_key = os.environ.get("APG_API_KEY") or os.environ.get("RIGHTCODE_API_KEY") or os.environ.get("OPENAI_API_KEY")

        if provider == "rightcode":
            codex_config = load_codex_provider_config("rightcode")
            base_url = base_url or codex_config.get("base_url")
            model = os.environ.get("APG_MODEL_NAME") or codex_config.get("model") or model

        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=int(os.environ.get("APG_TIMEOUT_SECONDS", "60")),
        )

    def generate_reply(self, history: list[dict[str, str]], current_message: str, profile: str) -> ModelResponse:
        if self.provider == "mock":
            return self._generate_mock_reply(history, current_message, profile)
        if self.provider == "rightcode":
            return self._generate_responses_reply(history, current_message, profile)
        if self.provider == "openai_compatible":
            return self._generate_openai_compatible_reply(history, current_message, profile)
        raise ModelClientError(f"Unsupported model provider: {self.provider}")

    def _generate_mock_reply(self, history: list[dict[str, str]], current_message: str, profile: str) -> ModelResponse:
        turn_count = len(history) + 1
        reply_text = (
            f"[mock:{profile}] turn {turn_count} received. "
            f"Latest message: {current_message}"
        )
        return ModelResponse(
            reply_text=reply_text,
            raw_response={
                "provider": "mock",
                "model": self.model,
                "history_length": len(history),
                "reply_text": reply_text,
            },
            provider="mock",
            model=self.model,
        )

    def _generate_openai_compatible_reply(
        self,
        history: list[dict[str, str]],
        current_message: str,
        profile: str,
    ) -> ModelResponse:
        if not self.base_url:
            raise ModelClientError("APG_BASE_URL is required for openai_compatible provider.")
        if not self.api_key:
            raise ModelClientError("APG_API_KEY is required for openai_compatible provider.")

        endpoint = f"{self.base_url}/chat/completions"
        messages = [
            {
                "role": "system",
                "content": (
                    "You are participating in a session protected by Agent Privacy Guard. "
                    f"Active profile: {profile}."
                ),
            }
        ]
        for turn in history:
            messages.append({"role": "user", "content": turn["user"]})
            messages.append({"role": "assistant", "content": turn["assistant"]})
        messages.append({"role": "user", "content": current_message})

        payload = {
            "model": self.model,
            "messages": messages,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelClientError(f"Model API HTTP error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ModelClientError(f"Model API connection failed: {exc}") from exc

        payload = json.loads(raw_text)
        try:
            reply_text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelClientError(f"Unexpected model response shape: {payload}") from exc

        return ModelResponse(
            reply_text=reply_text,
            raw_response=payload,
            provider=self.provider,
            model=self.model,
        )

    def _generate_responses_reply(
        self,
        history: list[dict[str, str]],
        current_message: str,
        profile: str,
    ) -> ModelResponse:
        if not self.base_url:
            raise ModelClientError("base_url is required for rightcode provider.")
        if not self.api_key:
            raise ModelClientError("APG_API_KEY / RIGHTCODE_API_KEY / OPENAI_API_KEY is required for rightcode provider.")

        endpoint = f"{self.base_url}/responses"
        input_items: list[dict] = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You are participating in a session protected by Agent Privacy Guard. "
                            f"Active profile: {profile}."
                        ),
                    }
                ],
            }
        ]
        for turn in history:
            input_items.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": turn["user"]}],
                }
            )
            input_items.append(
                {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": turn["assistant"]}],
                }
            )
        input_items.append(
            {
                "role": "user",
                "content": [{"type": "input_text", "text": current_message}],
            }
        )

        payload = {
            "model": self.model,
            "input": input_items,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelClientError(f"Responses API HTTP error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ModelClientError(f"Responses API connection failed: {exc}") from exc

        if not raw_text.strip():
            return self._generate_openai_compatible_reply(history, current_message, profile)

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return self._generate_openai_compatible_reply(history, current_message, profile)

        reply_text = extract_response_output_text(payload)
        if not reply_text:
            return self._generate_openai_compatible_reply(history, current_message, profile)

        return ModelResponse(
            reply_text=reply_text,
            raw_response=payload,
            provider=self.provider,
            model=self.model,
        )


def load_codex_provider_config(provider_name: str, config_path: Path | None = None) -> dict:
    actual_path = config_path or (Path.home() / ".codex" / "config.toml")
    if not actual_path.exists():
        return {}

    payload = tomllib.loads(actual_path.read_text(encoding="utf-8"))
    providers = payload.get("model_providers", {})
    provider_payload = providers.get(provider_name, {})
    if not isinstance(provider_payload, dict):
        provider_payload = {}

    return {
        "base_url": provider_payload.get("base_url"),
        "model": payload.get("model"),
    }


def extract_response_output_text(payload: dict) -> str:
    output = payload.get("output", [])
    collected: list[str] = []
    for item in output:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                collected.append(text)
    return "\n".join(collected).strip()
