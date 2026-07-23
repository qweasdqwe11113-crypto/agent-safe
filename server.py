#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from dataclasses import asdict
from datetime import datetime
from typing import Any

from guard_core import (
    PROFILE_POLICIES,
    RISK_LEVELS,
    ScanResult,
    apply_final_action,
    build_preview,
    get_policy_templates_summary,
    label_display_name,
    restore_response,
    scan_file_bytes,
    scan_text,
    summarize_findings,
)
from model_client import ModelClient, ModelClientError
from gateway_trace import GatewayTraceStore, extract_chat_text, extract_current_user_text, extract_latest_user_text, extract_responses_text
from session_state import SessionState, TurnRecord, append_turn, load_session, save_session_log

PROJECT_ROOT = Path(__file__).resolve().parent
DEBUG_CONSOLE_PATH = PROJECT_ROOT / "web" / "debug.html"


class SessionStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, SessionState] = {}
        self.pending_previews: dict[str, dict] = {}

    def create_session(self, profile: str, session_id: str | None = None) -> SessionState:
        session = SessionState.create(profile, self.base_dir, session_id)
        self.sessions[session.session_id] = session
        save_session_log(session)
        return session

    def get_session(self, session_id: str) -> SessionState | None:
        if session_id in self.sessions:
            return self.sessions[session_id]

        session_json = self.base_dir / session_id / "session.json"
        if session_json.exists():
            session = load_session(session_json)
            self.sessions[session_id] = session
            return session
        return None

    def save_preview(self, session_id: str, payload: dict) -> str:
        preview_id = uuid.uuid4().hex
        self.pending_previews[preview_id] = {"session_id": session_id, **payload}
        return preview_id

    def pop_preview(self, preview_id: str) -> dict | None:
        return self.pending_previews.pop(preview_id, None)

    def get_preview(self, preview_id: str) -> dict | None:
        return self.pending_previews.get(preview_id)


class ReviewCoordinator:
    INTERRUPTED_STATUSES = {"pending", "approved", "sending"}

    def __init__(self, base_dir: Path, timeout_seconds: int = 900) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self.lock = threading.RLock()
        self.reviews: dict[str, dict] = {}
        self.events: dict[str, threading.Event] = {}
        self.active_request_keys: dict[str, str] = {}
        self.session_sequences: dict[str, int] = {}
        self._restore_reviews()

    def _review_path(self, review_id: str) -> Path:
        return self.base_dir / f"review-{review_id}.json"

    def _persist_review(self, review: dict) -> None:
        destination = self._review_path(review["review_id"])
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)

    def _restore_reviews(self) -> None:
        for path in self.base_dir.glob("review-*.json"):
            try:
                review = json.loads(path.read_text(encoding="utf-8"))
                review_id = review["review_id"]
            except (OSError, KeyError, json.JSONDecodeError):
                continue
            if review.get("status") in self.INTERRUPTED_STATUSES:
                review.update(
                    {
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                        "status": "interrupted",
                        "error": "Proxy restarted before this review could finish.",
                    }
                )
                self._persist_review(review)
            self.reviews[review_id] = review
            label = str(review.get("session_label", "OpenCode session"))
            self.session_sequences[label] = max(self.session_sequences.get(label, 0), int(review.get("request_sequence", 0)))

    def get_or_create_review(self, *, request_key: str, **kwargs) -> tuple[dict, bool]:
        with self.lock:
            existing_id = self.active_request_keys.get(request_key)
            if existing_id in self.reviews:
                return self.reviews[existing_id].copy(), False
            review = self.create_review(request_key=request_key, **kwargs)
            return review, True

    def create_review(
        self,
        *,
        route: str,
        model: str,
        profile: str,
        original_text: str,
        redacted_text: str,
        suggested_action: str,
        risk_level: str,
        findings: list[dict[str, object]],
        session_label: str,
        request_key: str = "",
        token_map: dict[str, str] | None = None,
    ) -> dict:
        review_id = uuid.uuid4().hex
        with self.lock:
            request_sequence = self.session_sequences.get(session_label, 0) + 1
            self.session_sequences[session_label] = request_sequence
        review = {
            "review_id": review_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "route": route,
            "model": model,
            "profile": profile,
            "session_label": session_label,
            "request_key": request_key,
            "request_sequence": request_sequence,
            "original_text": original_text,
            "redacted_text": redacted_text,
            "suggested_action": suggested_action,
            "risk_level": risk_level,
            "findings": findings,
            "token_map": token_map or {},
            "status": "pending",
            "final_action": "",
            "is_override": False,
            "override_reason": "",
            "cloud_response": "",
            "restored_response": "",
            "error": "",
        }
        with self.lock:
            self.reviews[review_id] = review
            self.events[review_id] = threading.Event()
            if request_key:
                self.active_request_keys[request_key] = review_id
            self._persist_review(review)
        return review.copy()

    def list_reviews(self) -> list[dict]:
        with self.lock:
            reviews = [review.copy() for review in self.reviews.values()]
        return sorted(reviews, key=lambda item: item["created_at"], reverse=True)

    def get_review(self, review_id: str) -> dict | None:
        with self.lock:
            review = self.reviews.get(review_id)
            return review.copy() if review else None

    def decide(self, review_id: str, final_action: str, override_reason: str) -> tuple[dict | None, str | None]:
        with self.lock:
            review = self.reviews.get(review_id)
            if review is None:
                return None, "Review not found"
            if review["status"] != "pending":
                return None, "Review has already been decided"
            is_override = final_action != review["suggested_action"]
            if is_override and not override_reason:
                return None, "override_reason is required when final_action differs from suggested_action"
            review.update(
                {
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "status": "approved",
                    "final_action": final_action,
                    "is_override": is_override,
                    "override_reason": override_reason,
                }
            )
            self._persist_review(review)
            self.events[review_id].set()
            return review.copy(), None

    def wait_for_decision(self, review_id: str) -> dict | None:
        with self.lock:
            event = self.events.get(review_id)
        if event is None or not event.wait(self.timeout_seconds):
            self.complete_review(review_id, status="expired", error="Review confirmation timed out")
            return None
        return self.get_review(review_id)

    def mark_sending(self, review_id: str) -> None:
        with self.lock:
            review = self.reviews.get(review_id)
            if review:
                review["status"] = "sending"
                review["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._persist_review(review)
                if review.get("request_key"):
                    self.active_request_keys.pop(review["request_key"], None)

    def complete_review(
        self,
        review_id: str | None,
        *,
        status: str,
        cloud_response: str = "",
        restored_response: str = "",
        error: str = "",
    ) -> None:
        if review_id is None:
            return
        with self.lock:
            review = self.reviews.get(review_id)
            if review:
                review.update(
                    {
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                        "status": status,
                        "cloud_response": cloud_response,
                        "restored_response": restored_response,
                        "error": error,
                    }
                )
                self._persist_review(review)


class GatewayClientError(RuntimeError):
    pass


class GatewayHTTPError(GatewayClientError):
    def __init__(self, status_code: int, headers: dict[str, str], payload: dict) -> None:
        super().__init__(f"Upstream HTTP error {status_code}")
        self.status_code = status_code
        self.headers = headers
        self.payload = payload


class GatewayClient:
    def __init__(self, base_url: str | None, timeout_seconds: int = 60, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key

    @classmethod
    def from_env(cls) -> "GatewayClient":
        return cls(
            base_url=os.environ.get("APG_UPSTREAM_BASE_URL"),
            timeout_seconds=int(os.environ.get("APG_UPSTREAM_TIMEOUT_SECONDS", "60")),
            api_key=os.environ.get("APG_UPSTREAM_API_KEY"),
        )

    def forward_json(self, route_path: str, payload: dict, request_headers: dict[str, str]) -> tuple[int, dict[str, str], dict]:
        if not self.base_url:
            raise GatewayClientError("APG_UPSTREAM_BASE_URL is required for gateway routes.")

        endpoint = f"{self.base_url}{route_path}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        auth_header = request_headers.get("Authorization")
        if auth_header:
            headers["Authorization"] = auth_header
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for header_name in ("OpenAI-Beta", "OpenAI-Organization", "OpenAI-Project"):
            value = request_headers.get(header_name)
            if value:
                headers[header_name] = value

        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
                payload = json.loads(raw_body) if raw_body.strip() else {}
                response_headers = {key: value for key, value in response.headers.items()}
                return response.status, response_headers, payload
        except urllib.error.HTTPError as exc:
            detail_text = exc.read().decode("utf-8", errors="replace")
            try:
                detail_payload = json.loads(detail_text)
            except json.JSONDecodeError:
                detail_payload = {"error": {"message": detail_text}}
            response_headers = {key: value for key, value in exc.headers.items()}
            return exc.code, response_headers, detail_payload
        except urllib.error.URLError as exc:
            raise GatewayClientError(f"Upstream connection failed: {exc}") from exc

    def open_stream(self, route_path: str, payload: dict, request_headers: dict[str, str]):
        if not self.base_url:
            raise GatewayClientError("APG_UPSTREAM_BASE_URL is required for gateway routes.")

        endpoint = f"{self.base_url}{route_path}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        auth_header = request_headers.get("Authorization")
        if auth_header:
            headers["Authorization"] = auth_header
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for header_name in ("OpenAI-Beta", "OpenAI-Organization", "OpenAI-Project"):
            value = request_headers.get(header_name)
            if value:
                headers[header_name] = value

        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            return urllib.request.urlopen(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            detail_text = exc.read().decode("utf-8", errors="replace")
            try:
                detail_payload = json.loads(detail_text)
            except json.JSONDecodeError:
                detail_payload = {"error": {"message": detail_text}}
            response_headers = {key: value for key, value in exc.headers.items()}
            raise GatewayHTTPError(exc.code, response_headers, detail_payload) from exc
        except urllib.error.URLError as exc:
            raise GatewayClientError(f"Upstream connection failed: {exc}") from exc


def _merge_token_maps(target: dict[str, str], incoming: dict[str, str]) -> None:
    for key, value in incoming.items():
        existing = target.get(key)
        if existing is not None and existing != value:
            raise GatewayClientError(f"Token collision detected for placeholder {key}.")
        target[key] = value


def _combine_actions(results: list[ScanResult]) -> str:
    actions = {result.suggested_action for result in results}
    if "block" in actions:
        return "block"
    if "mask" in actions:
        return "mask"
    return "allow"


def _combine_gateway_actions(results: list[ScanResult]) -> str:
    actions = {result.suggested_action for result in results}
    if actions & {"block", "mask"}:
        return "mask"
    return "allow"


def _scan_string(
    text: str,
    profile: str,
    results: list[ScanResult],
    token_map: dict[str, str],
    *,
    block_as_mask: bool = False,
) -> str:
    scan_result = scan_text(text, profile)
    results.append(scan_result)
    _merge_token_maps(token_map, scan_result.token_map)
    if block_as_mask and scan_result.suggested_action == "block":
        return scan_result.redacted_text
    return apply_final_action(scan_result, scan_result.suggested_action) or ""


def _sanitize_message_content(
    content: Any,
    profile: str,
    results: list[ScanResult],
    token_map: dict[str, str],
    *,
    block_as_mask: bool = False,
) -> Any:
    if isinstance(content, str):
        return _scan_string(content, profile, results, token_map, block_as_mask=block_as_mask)
    if isinstance(content, list):
        sanitized_items: list[Any] = []
        for item in content:
            if not isinstance(item, dict):
                sanitized_items.append(item)
                continue
            sanitized_item = copy.deepcopy(item)
            if isinstance(sanitized_item.get("text"), str):
                sanitized_item["text"] = _scan_string(
                    sanitized_item["text"],
                    profile,
                    results,
                    token_map,
                    block_as_mask=block_as_mask,
                )
            sanitized_items.append(sanitized_item)
        return sanitized_items
    return content


def sanitize_responses_payload(payload: dict, profile: str) -> tuple[dict, dict[str, str], str]:
    sanitized = copy.deepcopy(payload)
    results: list[ScanResult] = []
    token_map: dict[str, str] = {}

    if isinstance(sanitized.get("instructions"), str):
        sanitized["instructions"] = _scan_string(
            sanitized["instructions"],
            profile,
            results,
            token_map,
            block_as_mask=True,
        )

    input_value = sanitized.get("input")
    if isinstance(input_value, str):
        sanitized["input"] = _scan_string(input_value, profile, results, token_map, block_as_mask=True)
    elif isinstance(input_value, dict):
        content = input_value.get("content")
        if content is not None:
            input_value["content"] = _sanitize_message_content(
                content,
                profile,
                results,
                token_map,
                block_as_mask=True,
            )
    elif isinstance(input_value, list):
        sanitized_items: list[Any] = []
        for item in input_value:
            if not isinstance(item, dict):
                sanitized_items.append(item)
                continue
            sanitized_item = copy.deepcopy(item)
            if "content" in sanitized_item:
                sanitized_item["content"] = _sanitize_message_content(
                    sanitized_item.get("content"),
                    profile,
                    results,
                    token_map,
                    block_as_mask=True,
                )
            sanitized_items.append(sanitized_item)
        sanitized["input"] = sanitized_items

    return sanitized, token_map, _combine_gateway_actions(results) if results else "allow"


def sanitize_chat_completions_payload(payload: dict, profile: str) -> tuple[dict, dict[str, str], str]:
    sanitized = copy.deepcopy(payload)
    results: list[ScanResult] = []
    token_map: dict[str, str] = {}

    messages = sanitized.get("messages")
    if isinstance(messages, list):
        sanitized_messages: list[Any] = []
        for message in messages:
            if not isinstance(message, dict):
                sanitized_messages.append(message)
                continue
            sanitized_message = copy.deepcopy(message)
            if "content" in sanitized_message:
                sanitized_message["content"] = _sanitize_message_content(
                    sanitized_message.get("content"),
                    profile,
                    results,
                    token_map,
                    block_as_mask=True,
                )
            sanitized_messages.append(sanitized_message)
        sanitized["messages"] = sanitized_messages

    return sanitized, token_map, _combine_gateway_actions(results) if results else "allow"


def restore_responses_payload(payload: dict, token_map: dict[str, str]) -> dict:
    if not token_map:
        return payload

    restored = copy.deepcopy(payload)
    if isinstance(restored.get("output_text"), str):
        restored["output_text"] = restore_response(restored["output_text"], token_map)

    output = restored.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = restore_response(part["text"], token_map)
    return restored


def restore_chat_completions_payload(payload: dict, token_map: dict[str, str]) -> dict:
    if not token_map:
        return payload

    restored = copy.deepcopy(payload)
    choices = restored.get("choices")
    if not isinstance(choices, list):
        return restored

    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = restore_response(content, token_map)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = restore_response(part["text"], token_map)
    return restored


def restore_json_strings(value: Any, token_map: dict[str, str]) -> Any:
    if not token_map:
        return value
    if isinstance(value, str):
        return restore_response(value, token_map)
    if isinstance(value, list):
        return [restore_json_strings(item, token_map) for item in value]
    if isinstance(value, dict):
        return {key: restore_json_strings(item, token_map) for key, item in value.items()}
    return value


def create_streaming_token_restorer(token_map: dict[str, str]):
    if not token_map:
        def passthrough(field_key: str, text: str, flush: bool = False) -> str:
            return text

        return passthrough

    max_token_len = max((len(token) for token in token_map), default=0)
    buffers: dict[str, str] = {}

    def rehydrate_text(text: str) -> str:
        for token, original in token_map.items():
            text = text.replace(token, original)
        return text

    def restore(field_key: str, text: str, flush: bool = False) -> str:
        current = buffers.get(field_key, "") + text
        if flush:
            buffers[field_key] = ""
            return rehydrate_text(current)

        hold_back = 0
        if max_token_len > 0:
            for token in token_map:
                max_prefix = min(len(token) - 1, len(current))
                for prefix_len in range(1, max_prefix + 1):
                    if current.endswith(token[:prefix_len]):
                        hold_back = max(hold_back, prefix_len)

        safe_end = len(current) - hold_back
        safe_text = current[:safe_end]
        buffers[field_key] = current[safe_end:]
        return rehydrate_text(safe_text)

    return restore


def build_blocked_responses_payload(model: str | None) -> dict:
    timestamp = int(time.time())
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": timestamp,
        "status": "completed",
        "model": model or "apg-blocked",
        "output": [
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Request blocked by Agent Privacy Guard policy.",
                    }
                ],
            }
        ],
        "output_text": "Request blocked by Agent Privacy Guard policy.",
    }


def build_blocked_responses_stream_events(model: str | None) -> list[dict]:
    timestamp = int(time.time())
    response_id = f"resp_{uuid.uuid4().hex}"
    message_id = f"msg_{uuid.uuid4().hex}"
    response = {
        "id": response_id,
        "object": "response",
        "created_at": timestamp,
        "status": "in_progress",
        "model": model or "apg-blocked",
        "output": [],
    }
    completed_response = {
        **response,
        "status": "completed",
        "output": [
            {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Request blocked by Agent Privacy Guard policy.",
                    }
                ],
            }
        ],
        "output_text": "Request blocked by Agent Privacy Guard policy.",
    }
    return [
        {
            "type": "response.created",
            "response": response,
        },
        {
            "type": "response.output_item.added",
            "response_id": response_id,
            "output_index": 0,
            "item": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
                "content": [],
            },
        },
        {
            "type": "response.output_text.delta",
            "response_id": response_id,
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "delta": "Request blocked by Agent Privacy Guard policy.",
        },
        {
            "type": "response.output_text.done",
            "response_id": response_id,
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "text": "Request blocked by Agent Privacy Guard policy.",
        },
        {
            "type": "response.completed",
            "response": completed_response,
        },
    ]


def build_blocked_chat_completions_payload(model: str | None) -> dict:
    timestamp = int(time.time())
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": timestamp,
        "model": model or "apg-blocked",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Request blocked by Agent Privacy Guard policy.",
                },
                "finish_reason": "stop",
            }
        ],
    }


def build_action_options(scan_result) -> dict:
    return {
        "allow": {
            "sent_text": scan_result.original_text,
            "description": "Send the original text to the model.",
        },
        "mask": {
            "sent_text": scan_result.redacted_text,
            "description": "Send the redacted text to the model.",
        },
        "block": {
            "sent_text": None,
            "description": "Do not send this turn to the model.",
        },
    }


def build_findings(scan_result: ScanResult) -> list[dict[str, object]]:
    return [
        {
            "label": label,
            "display_name": label_display_name(label),
            "count": count,
        }
        for label, count in summarize_findings(scan_result.token_map)
    ]


def build_review_payload(scan_result: ScanResult) -> dict:
    return {
        "profile": scan_result.profile,
        "original_text": scan_result.original_text,
        "redacted_text": scan_result.redacted_text,
        "token_map": scan_result.token_map,
        "findings": build_findings(scan_result),
        "suggested_action": scan_result.suggested_action,
        "risk_level": RISK_LEVELS[scan_result.suggested_action],
        "suggested_sent_text": apply_final_action(scan_result, scan_result.suggested_action),
        "action_options": build_action_options(scan_result),
        "preview_text": build_preview(scan_result),
        "review_mode": "review-first",
    }


def build_turn_file_paths(session: SessionState, turn_id: int) -> dict[str, Path]:
    prefix = f"turn-{turn_id:03d}"
    return {
        "user_original": session.session_path / f"{prefix}-user-original.txt",
        "user_safe": session.session_path / f"{prefix}-user-safe.txt",
        "token_map": session.session_path / f"{prefix}-token-map.json",
        "model_raw": session.session_path / f"{prefix}-model-raw.json",
        "assistant_raw": session.session_path / f"{prefix}-assistant-raw.txt",
        "assistant_restored": session.session_path / f"{prefix}-assistant-restored.txt",
    }


class GuardHTTPRequestHandler(BaseHTTPRequestHandler):
    server: "GuardHTTPServer"

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return

        if path == "/debug":
            self._write_debug_console()
            return

        if path == "/gateway-traces":
            self._write_json(HTTPStatus.OK, {"sessions": self.server.trace_store.list_sessions()})
            return

        if path.startswith("/gateway-traces/"):
            session_id = path.split("/")[2] if len(path.split("/")) > 2 else ""
            session = self.server.trace_store.get_session(session_id)
            if session is None:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "Gateway trace session not found"})
                return
            self._write_json(HTTPStatus.OK, session)
            return

        if path == "/profiles":
            self._write_json(
                HTTPStatus.OK,
                {
                    "profiles": sorted(PROFILE_POLICIES),
                    "templates": get_policy_templates_summary(),
                },
            )
            return

        if path == "/reviews":
            self._write_json(HTTPStatus.OK, {"reviews": self.server.review_coordinator.list_reviews()})
            return

        if path.startswith("/reviews/"):
            review_id = path.split("/")[2] if len(path.split("/")) > 2 else ""
            review = self.server.review_coordinator.get_review(review_id)
            if review is None:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "Review not found"})
                return
            self._write_json(HTTPStatus.OK, review)
            return

        if path.startswith("/sessions/"):
            session_id = path.split("/")[2] if len(path.split("/")) > 2 else ""
            session = self.server.session_store.get_session(session_id)
            if session is None:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "Session not found"})
                return

            if path.endswith("/turns"):
                self._write_json(HTTPStatus.OK, {"turns": [asdict(turn) for turn in session.turns]})
                return

            self._write_json(HTTPStatus.OK, session.to_dict())
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"error": "Route not found"})
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/v1/responses":
            payload = self._read_json_body()
            if payload is None:
                return
            self._handle_gateway_responses(payload)
            return

        if path == "/v1/chat/completions":
            payload = self._read_json_body()
            if payload is None:
                return
            self._handle_gateway_chat_completions(payload)
            return

        if path == "/plugin/reviews":
            payload = self._read_json_body()
            if payload is not None:
                self._handle_plugin_review(payload)
            return

        if path.startswith("/plugin/reviews/") and path.endswith("/output"):
            payload = self._read_json_body()
            if payload is not None:
                self._handle_plugin_output(path.split("/")[3], payload)
            return

        if path == "/sessions":
            payload = self._read_json_body()
            if payload is None:
                return
            self._handle_create_session(payload)
            return

        if path.startswith("/reviews/") and path.endswith("/decision"):
            review_id = path.split("/")[2]
            payload = self._read_json_body()
            if payload is None:
                return
            final_action = payload.get("final_action")
            if final_action not in {"allow", "mask", "block"}:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": "final_action must be allow, mask, or block"})
                return
            override_reason = payload.get("override_reason", "")
            if not isinstance(override_reason, str):
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": "override_reason must be a string"})
                return
            review, error = self.server.review_coordinator.decide(
                review_id,
                final_action,
                override_reason.strip(),
            )
            if error:
                status = HTTPStatus.NOT_FOUND if error == "Review not found" else HTTPStatus.BAD_REQUEST
                self._write_json(status, {"error": error})
                return
            self._write_json(HTTPStatus.OK, review or {})
            return

        if path.startswith("/sessions/") and path.endswith("/preview"):
            session_id = path.split("/")[2]
            payload = self._read_json_body()
            if payload is None:
                return
            self._handle_preview(session_id, payload)
            return

        if path.startswith("/sessions/") and path.endswith("/preview-file"):
            session_id = path.split("/")[2]
            payload = self._read_multipart_form_data()
            if payload is None:
                return
            self._handle_preview_file(session_id, payload)
            return

        if path.startswith("/sessions/") and path.endswith("/messages"):
            session_id = path.split("/")[2]
            payload = self._read_json_body()
            if payload is None:
                return
            self._handle_message(session_id, payload)
            return

        if path.startswith("/sessions/") and path.endswith("/confirm"):
            session_id = path.split("/")[2]
            payload = self._read_json_body()
            if payload is None:
                return
            self._handle_confirm(session_id, payload)
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"error": "Route not found"})

    def _resolve_gateway_review(
        self,
        route: str,
        original_payload: dict,
        sanitized_payload: dict,
        token_map: dict[str, str],
        automatic_action: str,
    ) -> dict | None:
        if self.server.gateway_review_mode != "review-first":
            return {
                "payload": sanitized_payload,
                "token_map": token_map,
                "action": automatic_action,
                "review_id": None,
                "review": None,
            }

        profile = self.server.gateway_profile
        original_text = extract_current_user_text(original_payload, route)
        if not original_text:
            return {"payload": sanitized_payload, "token_map": token_map, "action": automatic_action, "review_id": None, "review": None}
        redacted_text = extract_latest_user_text(sanitized_payload, route)
        scan_result = scan_text(original_text, profile)
        headers = {key.lower(): value for key, value in self._request_headers_dict().items()}
        session_label = headers.get("x-opencode-session-id", "OpenCode session")
        request_identity = json.dumps({"session": session_label, "route": route, "payload": original_payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        review, _ = self.server.review_coordinator.get_or_create_review(
            request_key=hashlib.sha256(request_identity.encode("utf-8")).hexdigest(),
            route=route,
            model=str(original_payload.get("model", "")),
            profile=profile,
            original_text=original_text,
            redacted_text=redacted_text,
            suggested_action=scan_result.suggested_action,
            risk_level=RISK_LEVELS[scan_result.suggested_action],
            findings=build_findings(scan_result),
            session_label=session_label,
        )
        decision = self.server.review_coordinator.wait_for_decision(review["review_id"])
        if decision is None:
            self._write_json(
                HTTPStatus.REQUEST_TIMEOUT,
                {"error": {"message": "Review confirmation timed out", "type": "apg_review_timeout", "code": "APG_REVIEW_TIMEOUT", "review_id": review["review_id"]}},
            )
            return None

        final_action = decision["final_action"]
        if final_action == "allow":
            forwarded_payload = original_payload
            active_token_map: dict[str, str] = {}
        elif final_action == "mask":
            forwarded_payload = sanitized_payload
            active_token_map = token_map
        else:
            forwarded_payload = sanitized_payload
            active_token_map = {}

        if final_action != "block":
            self.server.review_coordinator.mark_sending(review["review_id"])
        return {
            "payload": forwarded_payload,
            "token_map": active_token_map,
            "action": final_action,
            "review_id": review["review_id"],
            "review": decision,
        }

    def _handle_plugin_review(self, payload: dict) -> None:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "text must be a non-empty string"})
            return
        session_label = str(payload.get("session_id") or "OpenCode session")
        scan_result = scan_text(text, self.server.gateway_profile)
        request_key = hashlib.sha256(f"plugin:{session_label}:{text}".encode("utf-8")).hexdigest()
        review, _ = self.server.review_coordinator.get_or_create_review(
            request_key=request_key, route="plugin/messages.transform", model=str(payload.get("model", "OpenCode model")),
            profile=self.server.gateway_profile, original_text=text, redacted_text=scan_result.redacted_text,
            suggested_action=scan_result.suggested_action, risk_level=RISK_LEVELS[scan_result.suggested_action],
            findings=build_findings(scan_result), session_label=session_label,
            token_map=scan_result.token_map,
        )
        self._write_json(HTTPStatus.CREATED, review)

    def _handle_plugin_output(self, review_id: str, payload: dict) -> None:
        text = payload.get("text")
        if not isinstance(text, str):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "text must be a string"})
            return
        review = self.server.review_coordinator.get_review(review_id)
        if review is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Review not found"})
            return
        restored = restore_response(text, review.get("token_map", {}))
        self.server.review_coordinator.complete_review(
            review_id, status="completed", cloud_response=text, restored_response=restored
        )
        self._write_json(HTTPStatus.OK, {"review_id": review_id, "restored_response": restored})

    def _handle_gateway_responses(self, payload: dict) -> None:
        if payload.get("stream") is True:
            self._handle_gateway_responses_stream(payload)
            return

        profile = self.server.gateway_profile
        sanitized_payload, token_map, action = sanitize_responses_payload(payload, profile)
        resolved = self._resolve_gateway_review("/responses", payload, sanitized_payload, token_map, action)
        if resolved is None:
            return
        sanitized_payload = resolved["payload"]
        token_map = resolved["token_map"]
        action = resolved["action"]
        review_id = resolved["review_id"]
        trace = self.server.trace_store.start_trace(
            "/responses", payload, sanitized_payload, token_map, action, self._request_headers_dict(), resolved["review"]
        )
        if action == "block":
            blocked_payload = build_blocked_responses_payload(payload.get("model"))
            self.server.trace_store.complete_trace(
                trace,
                raw_reply=extract_responses_text(blocked_payload),
                restored_reply=extract_responses_text(blocked_payload),
                raw_payload=blocked_payload,
                restored_payload=blocked_payload,
                upstream_status=200,
                status="blocked",
                response_id=blocked_payload.get("id"),
            )
            self.server.review_coordinator.complete_review(
                review_id,
                status="blocked",
                cloud_response=extract_responses_text(blocked_payload),
                restored_response=extract_responses_text(blocked_payload),
            )
            self._write_json(
                HTTPStatus.OK,
                blocked_payload,
                extra_headers={"X-APG-Action": "block"},
            )
            return

        try:
            status, upstream_headers, upstream_payload = self.server.gateway_client.forward_json(
                "/responses",
                sanitized_payload,
                self._request_headers_dict(),
            )
        except GatewayClientError as exc:
            self.server.trace_store.complete_trace(trace, status="error", error=str(exc))
            self.server.review_coordinator.complete_review(review_id, status="error", error=str(exc))
            self._write_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            return

        restored_payload = restore_responses_payload(upstream_payload, token_map)
        self.server.trace_store.complete_trace(
            trace,
            raw_reply=extract_responses_text(upstream_payload),
            restored_reply=extract_responses_text(restored_payload),
            raw_payload=upstream_payload,
            restored_payload=restored_payload,
            upstream_status=status,
            status="completed" if status < 400 else "error",
            response_id=upstream_payload.get("id"),
        )
        self.server.review_coordinator.complete_review(
            review_id,
            status="completed" if status < 400 else "error",
            cloud_response=extract_responses_text(upstream_payload),
            restored_response=extract_responses_text(restored_payload),
        )
        self._write_json(
            HTTPStatus(status),
            restored_payload,
            extra_headers={"X-APG-Action": action, **self._filtered_response_headers(upstream_headers)},
        )

    def _handle_gateway_chat_completions(self, payload: dict) -> None:
        if payload.get("stream") is True:
            self._handle_gateway_chat_completions_stream(payload)
            return

        profile = self.server.gateway_profile
        sanitized_payload, token_map, action = sanitize_chat_completions_payload(payload, profile)
        resolved = self._resolve_gateway_review("/chat/completions", payload, sanitized_payload, token_map, action)
        if resolved is None:
            return
        sanitized_payload = resolved["payload"]
        token_map = resolved["token_map"]
        action = resolved["action"]
        review_id = resolved["review_id"]
        trace = self.server.trace_store.start_trace(
            "/chat/completions", payload, sanitized_payload, token_map, action, self._request_headers_dict(), resolved["review"]
        )
        if action == "block":
            blocked_payload = build_blocked_chat_completions_payload(payload.get("model"))
            self.server.trace_store.complete_trace(
                trace,
                raw_reply=extract_chat_text(blocked_payload),
                restored_reply=extract_chat_text(blocked_payload),
                raw_payload=blocked_payload,
                restored_payload=blocked_payload,
                upstream_status=200,
                status="blocked",
                response_id=blocked_payload.get("id"),
            )
            self.server.review_coordinator.complete_review(
                review_id,
                status="blocked",
                cloud_response=extract_chat_text(blocked_payload),
                restored_response=extract_chat_text(blocked_payload),
            )
            self._write_json(
                HTTPStatus.OK,
                blocked_payload,
                extra_headers={"X-APG-Action": "block"},
            )
            return

        try:
            status, upstream_headers, upstream_payload = self.server.gateway_client.forward_json(
                "/chat/completions",
                sanitized_payload,
                self._request_headers_dict(),
            )
        except GatewayClientError as exc:
            self.server.trace_store.complete_trace(trace, status="error", error=str(exc))
            self.server.review_coordinator.complete_review(review_id, status="error", error=str(exc))
            self._write_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            return

        restored_payload = restore_chat_completions_payload(upstream_payload, token_map)
        self.server.trace_store.complete_trace(
            trace,
            raw_reply=extract_chat_text(upstream_payload),
            restored_reply=extract_chat_text(restored_payload),
            raw_payload=upstream_payload,
            restored_payload=restored_payload,
            upstream_status=status,
            status="completed" if status < 400 else "error",
            response_id=upstream_payload.get("id"),
        )
        self.server.review_coordinator.complete_review(
            review_id,
            status="completed" if status < 400 else "error",
            cloud_response=extract_chat_text(upstream_payload),
            restored_response=extract_chat_text(restored_payload),
        )
        self._write_json(
            HTTPStatus(status),
            restored_payload,
            extra_headers={"X-APG-Action": action, **self._filtered_response_headers(upstream_headers)},
        )

    def _handle_gateway_responses_stream(self, payload: dict) -> None:
        profile = self.server.gateway_profile
        sanitized_payload, token_map, action = sanitize_responses_payload(payload, profile)
        resolved = self._resolve_gateway_review("/responses", payload, sanitized_payload, token_map, action)
        if resolved is None:
            return
        sanitized_payload = resolved["payload"]
        token_map = resolved["token_map"]
        action = resolved["action"]
        review_id = resolved["review_id"]
        trace = self.server.trace_store.start_trace(
            "/responses", payload, sanitized_payload, token_map, action, self._request_headers_dict(), resolved["review"]
        )
        if action == "block":
            events = build_blocked_responses_stream_events(payload.get("model"))
            self._start_sse_response(HTTPStatus.OK, {"X-APG-Action": "block"})
            for event in events:
                self.server.trace_store.append_stream_event(trace, event, event)
                self._write_sse_data(json.dumps(event, ensure_ascii=False))
            self._write_sse_done()
            completed = next((event.get("response", {}) for event in events if event.get("type") == "response.completed"), {})
            reply = extract_responses_text(completed)
            self.server.trace_store.complete_trace(
                trace,
                raw_reply=reply,
                restored_reply=reply,
                raw_payload=completed,
                restored_payload=completed,
                upstream_status=200,
                status="blocked",
                response_id=completed.get("id"),
            )
            self.server.review_coordinator.complete_review(
                review_id,
                status="blocked",
                cloud_response=reply,
                restored_response=reply,
            )
            return

        try:
            with self.server.gateway_client.open_stream(
                "/responses",
                sanitized_payload,
                self._request_headers_dict(),
            ) as upstream_response:
                self._start_sse_response(
                    HTTPStatus(upstream_response.status),
                    {"X-APG-Action": action, **self._filtered_response_headers(dict(upstream_response.headers.items()))},
                )
                stream_result = self._proxy_responses_sse(upstream_response, token_map, trace)
                self.server.trace_store.complete_trace(
                    trace,
                    raw_reply=stream_result["raw_reply"],
                    restored_reply=stream_result["restored_reply"],
                    upstream_status=upstream_response.status,
                    status="completed" if stream_result["completed"] else "incomplete",
                    response_id=stream_result["response_id"],
                )
                self.server.review_coordinator.complete_review(
                    review_id,
                    status="completed" if stream_result["completed"] else "incomplete",
                    cloud_response=stream_result["raw_reply"],
                    restored_response=stream_result["restored_reply"],
                )
        except GatewayHTTPError as exc:
            self.server.trace_store.complete_trace(
                trace,
                raw_payload=exc.payload,
                restored_payload=exc.payload,
                upstream_status=exc.status_code,
                status="error",
                error=str(exc),
            )
            self.server.review_coordinator.complete_review(review_id, status="error", error=str(exc))
            self._write_json(HTTPStatus(exc.status_code), exc.payload)
        except GatewayClientError as exc:
            self.server.trace_store.complete_trace(trace, status="error", error=str(exc))
            self.server.review_coordinator.complete_review(review_id, status="error", error=str(exc))
            self._write_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})

    def _handle_gateway_chat_completions_stream(self, payload: dict) -> None:
        profile = self.server.gateway_profile
        sanitized_payload, token_map, action = sanitize_chat_completions_payload(payload, profile)
        resolved = self._resolve_gateway_review("/chat/completions", payload, sanitized_payload, token_map, action)
        if resolved is None:
            return
        sanitized_payload = resolved["payload"]
        token_map = resolved["token_map"]
        action = resolved["action"]
        review_id = resolved["review_id"]
        trace = self.server.trace_store.start_trace(
            "/chat/completions", payload, sanitized_payload, token_map, action, self._request_headers_dict(), resolved["review"]
        )
        if action == "block":
            blocked_payload = build_blocked_chat_completions_payload(payload.get("model"))
            self._start_sse_response(HTTPStatus.OK, {"X-APG-Action": "block"})
            self.server.trace_store.append_stream_event(trace, blocked_payload, blocked_payload)
            self._write_sse_data(json.dumps(blocked_payload, ensure_ascii=False))
            self._write_sse_done()
            reply = extract_chat_text(blocked_payload)
            self.server.trace_store.complete_trace(
                trace,
                raw_reply=reply,
                restored_reply=reply,
                raw_payload=blocked_payload,
                restored_payload=blocked_payload,
                upstream_status=200,
                status="blocked",
                response_id=blocked_payload.get("id"),
            )
            self.server.review_coordinator.complete_review(
                review_id,
                status="blocked",
                cloud_response=reply,
                restored_response=reply,
            )
            return

        try:
            with self.server.gateway_client.open_stream(
                "/chat/completions",
                sanitized_payload,
                self._request_headers_dict(),
            ) as upstream_response:
                self._start_sse_response(
                    HTTPStatus(upstream_response.status),
                    {"X-APG-Action": action, **self._filtered_response_headers(dict(upstream_response.headers.items()))},
                )
                stream_result = self._proxy_chat_sse(upstream_response, token_map, trace)
                self.server.trace_store.complete_trace(
                    trace,
                    raw_reply=stream_result["raw_reply"],
                    restored_reply=stream_result["restored_reply"],
                    upstream_status=upstream_response.status,
                    status="completed" if stream_result["completed"] else "incomplete",
                    response_id=stream_result["response_id"],
                )
                self.server.review_coordinator.complete_review(
                    review_id,
                    status="completed" if stream_result["completed"] else "incomplete",
                    cloud_response=stream_result["raw_reply"],
                    restored_response=stream_result["restored_reply"],
                )
        except GatewayHTTPError as exc:
            self.server.trace_store.complete_trace(
                trace,
                raw_payload=exc.payload,
                restored_payload=exc.payload,
                upstream_status=exc.status_code,
                status="error",
                error=str(exc),
            )
            self.server.review_coordinator.complete_review(review_id, status="error", error=str(exc))
            self._write_json(HTTPStatus(exc.status_code), exc.payload)
        except GatewayClientError as exc:
            self.server.trace_store.complete_trace(trace, status="error", error=str(exc))
            self.server.review_coordinator.complete_review(review_id, status="error", error=str(exc))
            self._write_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})

    def _handle_create_session(self, payload: dict) -> None:
        profile = payload.get("profile")
        if profile not in PROFILE_POLICIES:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid profile: {profile}"})
            return

        session = self.server.session_store.create_session(profile, payload.get("session_id"))
        self._write_json(HTTPStatus.CREATED, session.to_dict())

    def _handle_preview(self, session_id: str, payload: dict) -> None:
        session = self.server.session_store.get_session(session_id)
        if session is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Session not found"})
            return

        message = payload.get("message", "")
        if not isinstance(message, str) or not message.strip():
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "message must be a non-empty string"})
            return

        scan_result = scan_text(message, session.profile)
        preview_payload = {"message": message, **build_review_payload(scan_result)}
        preview_id = self.server.session_store.save_preview(session_id, preview_payload)

        self._write_json(
            HTTPStatus.OK,
            {
                "preview_id": preview_id,
                "session_id": session_id,
                "profile": session.profile,
                "input_kind": "text",
                **preview_payload,
                "blocked": preview_payload["suggested_sent_text"] is None,
            },
        )

    def _handle_preview_file(self, session_id: str, payload: dict) -> None:
        session = self.server.session_store.get_session(session_id)
        if session is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Session not found"})
            return

        file_payload = payload.get("file")
        if not isinstance(file_payload, dict):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "file upload is required"})
            return

        file_name = file_payload.get("filename") or "uploaded-file"
        file_bytes = file_payload.get("content")
        if not isinstance(file_bytes, bytes):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "uploaded file content is invalid"})
            return

        scan_result = scan_file_bytes(file_name, file_bytes, session.profile)
        preview_payload = {
            "message": f"[file] {file_name}",
            **build_review_payload(scan_result),
            "file_name": file_name,
            "file_size": len(file_bytes),
            "content_type": file_payload.get("content_type"),
            "input_kind": "file",
        }
        preview_id = self.server.session_store.save_preview(session_id, preview_payload)
        self._write_json(
            HTTPStatus.OK,
            {
                "preview_id": preview_id,
                "session_id": session_id,
                "profile": session.profile,
                "input_kind": "file",
                **preview_payload,
                "blocked": preview_payload["suggested_sent_text"] is None,
            },
        )

    def _handle_message(self, session_id: str, payload: dict) -> None:
        session = self.server.session_store.get_session(session_id)
        if session is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Session not found"})
            return

        message = payload.get("message", "")
        if not isinstance(message, str) or not message.strip():
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "message must be a non-empty string"})
            return

        scan_result = scan_text(message, session.profile)
        preview_payload = {"message": message, **build_review_payload(scan_result)}
        preview_id = self.server.session_store.save_preview(session_id, preview_payload)
        self._handle_confirm(session_id, {"preview_id": preview_id})

    def _handle_confirm(self, session_id: str, payload: dict) -> None:
        session = self.server.session_store.get_session(session_id)
        if session is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Session not found"})
            return

        preview_id = payload.get("preview_id", "")
        if not isinstance(preview_id, str) or not preview_id.strip():
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "preview_id must be a non-empty string"})
            return
        preview_payload = self.server.session_store.get_preview(preview_id)
        if preview_payload is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Preview not found or already confirmed"})
            return
        if preview_payload["session_id"] != session_id:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "preview_id does not belong to this session"})
            return

        final_action = payload.get("final_action", preview_payload["suggested_action"])
        if final_action not in {"allow", "mask", "block"}:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "final_action must be allow, mask, or block"})
            return

        override_reason = payload.get("override_reason", "")
        if not isinstance(override_reason, str):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "override_reason must be a string"})
            return
        override_reason = override_reason.strip()
        is_override = final_action != preview_payload["suggested_action"]
        if is_override and not override_reason:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "override_reason is required when final_action differs from suggested_action"},
            )
            return

        self.server.session_store.pop_preview(preview_id)
        action_option = preview_payload["action_options"][final_action]
        safe_text = action_option["sent_text"]
        turn_id = session.next_turn_id()
        files = build_turn_file_paths(session, turn_id)
        files["user_original"].write_text(preview_payload["original_text"], encoding="utf-8")

        token_map = preview_payload["token_map"]
        if token_map:
            files["token_map"].write_text(
                json.dumps(token_map, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        if safe_text is None:
            turn_record = TurnRecord(
                turn_id=turn_id,
                user_original=preview_payload["original_text"],
                user_redacted=preview_payload["redacted_text"],
                suggested_action=preview_payload["suggested_action"],
                user_sent_text="",
                final_action=final_action,
                risk_level=preview_payload["risk_level"],
                is_override=is_override,
                override_reason=override_reason,
                artifacts={
                    "user_original": str(files["user_original"]),
                    **({"token_map": str(files["token_map"])} if token_map else {}),
                },
            )
            append_turn(session, turn_record)
            self._write_json(
                HTTPStatus.OK,
                {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "action": final_action,
                    "final_action": final_action,
                    "suggested_action": preview_payload["suggested_action"],
                    "risk_level": preview_payload["risk_level"],
                    "is_override": is_override,
                    "override_reason": override_reason,
                    "blocked": True,
                    "preview_id": preview_id,
                    "preview_text": preview_payload["preview_text"],
                },
            )
            return

        files["user_safe"].write_text(safe_text, encoding="utf-8")

        try:
            model_response = self.server.model_client.generate_reply(
                session.history_for_prompt(),
                safe_text,
                session.profile,
            )
        except ModelClientError as exc:
            self._write_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            return

        files["model_raw"].write_text(
            json.dumps(model_response.raw_response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        files["assistant_raw"].write_text(model_response.reply_text, encoding="utf-8")
        restored_reply = restore_response(model_response.reply_text, token_map)
        files["assistant_restored"].write_text(restored_reply, encoding="utf-8")

        turn_record = TurnRecord(
            turn_id=turn_id,
            user_original=preview_payload["original_text"],
            user_redacted=preview_payload["redacted_text"],
            suggested_action=preview_payload["suggested_action"],
            user_sent_text=safe_text,
            final_action=final_action,
            risk_level=preview_payload["risk_level"],
            is_override=is_override,
            override_reason=override_reason,
            codex_raw_reply=model_response.reply_text,
            codex_restored_reply=restored_reply,
            token_map_path=str(files["token_map"]) if token_map else None,
            artifacts={
                "user_original": str(files["user_original"]),
                "user_safe": str(files["user_safe"]),
                "model_raw": str(files["model_raw"]),
                "assistant_raw": str(files["assistant_raw"]),
                "assistant_restored": str(files["assistant_restored"]),
                **({"token_map": str(files["token_map"])} if token_map else {}),
            },
        )
        append_turn(session, turn_record)

        self._write_json(
            HTTPStatus.OK,
            {
                "preview_id": preview_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "profile": session.profile,
                "suggested_action": preview_payload["suggested_action"],
                "action": final_action,
                "final_action": final_action,
                "risk_level": preview_payload["risk_level"],
                "is_override": is_override,
                "override_reason": override_reason,
                "original_text": preview_payload["original_text"],
                "redacted_text": preview_payload["redacted_text"],
                "sent_text": safe_text,
                "assistant_reply": restored_reply,
                "assistant_raw_reply": model_response.reply_text,
                "cloud_response": model_response.reply_text,
                "restored_response": restored_reply,
                "blocked": False,
                "artifacts": turn_record.artifacts,
            },
        )

    def _read_json_body(self) -> dict | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
            return None

        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be valid JSON"})
            return None
        if not isinstance(payload, dict):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be a JSON object"})
            return None
        return payload

    def _read_multipart_form_data(self) -> dict | None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Content-Type must be multipart/form-data"})
            return None

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
            return None

        raw_body = self.rfile.read(content_length) if content_length > 0 else b""
        message = BytesParser(policy=email_default_policy).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw_body
        )
        if not message.is_multipart():
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "multipart body is invalid"})
            return None

        payload: dict[str, object] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename()
            content = part.get_payload(decode=True) or b""
            if filename:
                payload[name] = {
                    "filename": filename,
                    "content": content,
                    "content_type": part.get_content_type(),
                }
            else:
                payload[name] = content.decode(part.get_content_charset() or "utf-8", errors="replace")
        return payload

    def _request_headers_dict(self) -> dict[str, str]:
        return {key: value for key, value in self.headers.items()}

    def _filtered_response_headers(self, headers: dict[str, str]) -> dict[str, str]:
        passthrough: dict[str, str] = {}
        for key in ("OpenAI-Processing-Ms", "OpenAI-Version", "X-Request-Id"):
            value = headers.get(key)
            if value:
                passthrough[key] = value
        return passthrough

    def _write_json(self, status: HTTPStatus, payload: dict, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_debug_console(self) -> None:
        if not DEBUG_CONSOLE_PATH.exists():
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Debug console not found"})
            return
        body = DEBUG_CONSOLE_PATH.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _start_sse_response(self, status: HTTPStatus, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()

    def _write_sse_data(self, data: str) -> None:
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _write_sse_done(self) -> None:
        self._write_sse_data("[DONE]")

    def _proxy_chat_sse(self, upstream_response, token_map: dict[str, str], trace: dict) -> dict:
        raw_parts: list[str] = []
        restored_parts: list[str] = []
        response_id: str | None = None
        completed = False
        while True:
            raw_line = upstream_response.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace")
            if line.startswith("data:"):
                payload_text = line[5:].strip()
                if payload_text == "[DONE]":
                    completed = True
                    self.server.trace_store.append_stream_event(trace, "[DONE]", "[DONE]")
                    self._write_sse_done()
                    continue
                try:
                    event_payload = json.loads(payload_text)
                except json.JSONDecodeError:
                    restored_text = restore_response(payload_text, token_map)
                    self.server.trace_store.append_stream_event(trace, payload_text, restored_text)
                    self._write_sse_data(restored_text)
                    continue
                restored_payload = restore_json_strings(event_payload, token_map)
                response_id = response_id or event_payload.get("id")
                choices = event_payload.get("choices")
                restored_choices = restored_payload.get("choices")
                if isinstance(choices, list) and isinstance(restored_choices, list):
                    for raw_choice, restored_choice in zip(choices, restored_choices):
                        raw_delta = raw_choice.get("delta") if isinstance(raw_choice, dict) else None
                        restored_delta = restored_choice.get("delta") if isinstance(restored_choice, dict) else None
                        if isinstance(raw_delta, dict) and isinstance(raw_delta.get("content"), str):
                            raw_parts.append(raw_delta["content"])
                        if isinstance(restored_delta, dict) and isinstance(restored_delta.get("content"), str):
                            restored_parts.append(restored_delta["content"])
                self.server.trace_store.append_stream_event(trace, event_payload, restored_payload)
                self._write_sse_data(json.dumps(restored_payload, ensure_ascii=False))
                continue

            self.wfile.write(line.replace("\r\n", "\n").encode("utf-8"))
            self.wfile.flush()

        return {
            "raw_reply": "".join(raw_parts),
            "restored_reply": "".join(restored_parts),
            "response_id": response_id,
            "completed": completed,
        }

    def _proxy_responses_sse(self, upstream_response, token_map: dict[str, str], trace: dict) -> dict:
        restore_stream_text = create_streaming_token_restorer(token_map)
        raw_parts: list[str] = []
        restored_parts: list[str] = []
        done_raw_text = ""
        done_restored_text = ""
        response_id: str | None = None
        completed = False

        while True:
            raw_line = upstream_response.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace")
            if not line.startswith("data:"):
                self.wfile.write(line.replace("\r\n", "\n").encode("utf-8"))
                self.wfile.flush()
                continue

            payload_text = line[5:].strip()
            if payload_text == "[DONE]":
                self.server.trace_store.append_stream_event(trace, "[DONE]", "[DONE]")
                self._write_sse_done()
                continue

            try:
                event_payload = json.loads(payload_text)
            except json.JSONDecodeError:
                restored_text = restore_response(payload_text, token_map)
                self.server.trace_store.append_stream_event(trace, payload_text, restored_text)
                self._write_sse_data(restored_text)
                continue

            restored_payload = restore_json_strings(event_payload, token_map)
            event_type = restored_payload.get("type")
            if event_type == "response.completed":
                completed = True
            response = event_payload.get("response")
            if isinstance(response, dict):
                response_id = response_id or response.get("id")
            response_id = response_id or event_payload.get("response_id")
            output_index = restored_payload.get("output_index", 0)
            content_index = restored_payload.get("content_index", 0)
            item_id = restored_payload.get("item_id", "default")
            field_key = f"{item_id}:{output_index}:{content_index}"

            if event_type == "response.output_text.delta" and isinstance(restored_payload.get("delta"), str):
                if isinstance(event_payload.get("delta"), str):
                    raw_parts.append(event_payload["delta"])
                restored_payload["delta"] = restore_stream_text(field_key, restored_payload["delta"])
                restored_parts.append(restored_payload["delta"])
            elif event_type == "response.output_text.done" and isinstance(restored_payload.get("text"), str):
                if isinstance(event_payload.get("text"), str):
                    done_raw_text = event_payload["text"]
                restored_payload["text"] = restore_stream_text(field_key, restored_payload["text"], flush=True)
                done_restored_text = restored_payload["text"]

            self.server.trace_store.append_stream_event(trace, event_payload, restored_payload)
            self._write_sse_data(json.dumps(restored_payload, ensure_ascii=False))

        return {
            "raw_reply": "".join(raw_parts) or done_raw_text,
            "restored_reply": "".join(restored_parts) or done_restored_text,
            "response_id": response_id,
            "completed": completed,
        }


class GuardHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        session_store: SessionStore,
        model_client: ModelClient,
        gateway_client: GatewayClient,
        gateway_profile: str,
        trace_store: GatewayTraceStore | None = None,
        review_coordinator: ReviewCoordinator | None = None,
        gateway_review_mode: str = "automatic",
    ):
        super().__init__(server_address, RequestHandlerClass)
        self.session_store = session_store
        self.model_client = model_client
        self.gateway_client = gateway_client
        self.gateway_profile = gateway_profile
        self.trace_store = trace_store or GatewayTraceStore(session_store.base_dir / "gateway-traces")
        self.review_coordinator = review_coordinator or ReviewCoordinator(session_store.base_dir / "reviews")
        self.gateway_review_mode = gateway_review_mode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Agent Privacy Guard HTTP API server.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on.")
    parser.add_argument("--output-dir", default="outputs/api-sessions", help="Directory used to store session artifacts.")
    parser.add_argument("--trace-dir", default="outputs/gateway-traces", help="Directory used to store gateway traces.")
    parser.add_argument("--review-dir", default="outputs/reviews", help="Directory used to store review decisions.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session_store = SessionStore(Path(args.output_dir))
    trace_store = GatewayTraceStore(Path(args.trace_dir))
    model_client = ModelClient.from_env()
    gateway_client = GatewayClient.from_env()
    gateway_profile = os.environ.get("APG_GATEWAY_PROFILE", "coding")
    gateway_review_mode = os.environ.get("APG_GATEWAY_REVIEW_MODE", "review-first")
    review_timeout_seconds = int(os.environ.get("APG_REVIEW_TIMEOUT_SECONDS", "900"))
    server = GuardHTTPServer(
        (args.host, args.port),
        GuardHTTPRequestHandler,
        session_store,
        model_client,
        gateway_client,
        gateway_profile,
        trace_store,
        ReviewCoordinator(Path(args.review_dir), review_timeout_seconds),
        gateway_review_mode,
    )
    print(f"Agent Privacy Guard API listening on http://{args.host}:{args.port}")
    print(f"Model provider: {model_client.provider} ({model_client.model})")
    print(f"Gateway upstream: {gateway_client.base_url or 'disabled'}")
    print(f"Gateway profile: {gateway_profile}")
    print(f"Gateway review mode: {gateway_review_mode}")
    print(f"Session output dir: {Path(args.output_dir).resolve()}")
    print(f"Gateway trace dir: {Path(args.trace_dir).resolve()}")
    print(f"Review log dir: {Path(args.review_dir).resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
