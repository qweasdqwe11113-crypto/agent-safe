#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


SESSION_KEYS = ("conversation_id", "session_id", "thread_id", "prompt_cache_key")
SESSION_HEADERS = (
    "x-opencode-session-id",
    "x-codex-thread-id",
    "x-codex-session-id",
    "x-session-id",
    "x-conversation-id",
    "openai-conversation-id",
    "x-openai-conversation-id",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        return _content_text(content.get("content"))
    if isinstance(content, list):
        parts = [_content_text(item) for item in content]
        return "\n".join(part for part in parts if part)
    return ""


def extract_latest_user_text(payload: dict, route: str) -> str:
    collection = payload.get("messages") if route == "/chat/completions" else payload.get("input")
    if isinstance(collection, str):
        return collection
    if isinstance(collection, dict):
        return _content_text(collection.get("content"))
    if not isinstance(collection, list):
        return ""

    for item in reversed(collection):
        if isinstance(item, dict) and item.get("role") == "user":
            text = _content_text(item.get("content"))
            if text:
                return text
    return ""


def extract_first_user_text(payload: dict, route: str) -> str:
    collection = payload.get("messages") if route == "/chat/completions" else payload.get("input")
    if isinstance(collection, str):
        return collection
    if isinstance(collection, dict):
        return _content_text(collection.get("content"))
    if not isinstance(collection, list):
        return ""

    for item in collection:
        if isinstance(item, dict) and item.get("role") == "user":
            text = _content_text(item.get("content"))
            if text:
                return text
    return ""


def extract_responses_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        text = _content_text(item.get("content"))
        if text:
            parts.append(text)
    return "\n".join(parts)


def extract_chat_text(payload: dict) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return ""
    parts: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            text = _content_text(message.get("content"))
            if text:
                parts.append(text)
    return "\n".join(parts)


class GatewayTraceStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.response_sessions: dict[str, str] = {}

    def _session_identity(self, payload: dict, headers: dict[str, str], route: str) -> tuple[str, str]:
        lowered_headers = {key.lower(): value for key, value in headers.items()}
        for key in SESSION_HEADERS:
            value = lowered_headers.get(key)
            if value:
                return f"header:{key}:{value}", f"{key}: {value}"

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        for key in SESSION_KEYS:
            value = payload.get(key) or metadata.get(key)
            if value:
                return f"payload:{key}:{value}", f"{key}: {value}"

        previous_response_id = payload.get("previous_response_id")
        if isinstance(previous_response_id, str) and previous_response_id in self.response_sessions:
            session_id = self.response_sessions[previous_response_id]
            return f"resolved:{session_id}", "previous_response_id chain"

        first_user_text = extract_first_user_text(payload, route)
        if first_user_text:
            fallback = f"first-user:{payload.get('model', '')}:{first_user_text}"
            return fallback, "derived from first user message"

        return f"request:{uuid.uuid4().hex}", "generated locally"

    def _load_session(self, session_id: str) -> dict | None:
        path = self.base_dir / session_id / "session.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_session(self, session: dict) -> None:
        session_dir = self.base_dir / session["session_id"]
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session.json").write_text(
            json.dumps(session, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def start_trace(
        self,
        route: str,
        original_payload: dict,
        sanitized_payload: dict,
        token_map: dict[str, str],
        action: str,
        headers: dict[str, str],
        review: dict | None = None,
    ) -> dict:
        with self.lock:
            identity, label = self._session_identity(original_payload, headers, route)
            if identity.startswith("resolved:"):
                session_id = identity.removeprefix("resolved:")
            else:
                digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
                session_id = f"codex-{digest}"

            session = self._load_session(session_id)
            now = _now()
            if session is None:
                session = {
                    "session_id": session_id,
                    "label": label,
                    "profile": "",
                    "created_at": now,
                    "updated_at": now,
                    "traces": [],
                }

            trace_id = f"trace-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
            trace_dir = self.base_dir / session_id / trace_id
            trace_dir.mkdir(parents=True, exist_ok=True)
            artifacts = {
                "request_original": str(trace_dir / "request-original.json"),
                "request_sanitized": str(trace_dir / "request-sanitized.json"),
                "token_map": str(trace_dir / "token-map.json"),
                "response_raw": str(trace_dir / "response-raw.json"),
                "response_restored": str(trace_dir / "response-restored.json"),
                "stream_raw": str(trace_dir / "stream-raw.jsonl"),
                "stream_restored": str(trace_dir / "stream-restored.jsonl"),
                "review_decision": str(trace_dir / "review-decision.json"),
            }
            Path(artifacts["request_original"]).write_text(
                json.dumps(original_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            Path(artifacts["request_sanitized"]).write_text(
                json.dumps(sanitized_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            Path(artifacts["token_map"]).write_text(
                json.dumps(token_map, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            decision = {
                "review_id": (review or {}).get("review_id", ""),
                "suggested_action": (review or {}).get("suggested_action", action),
                "final_action": action,
                "risk_level": (review or {}).get("risk_level", ""),
                "is_override": bool((review or {}).get("is_override", False)),
                "override_reason": (review or {}).get("override_reason", ""),
            }
            Path(artifacts["review_decision"]).write_text(
                json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            trace = {
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
                "route": route,
                "model": original_payload.get("model", ""),
                "stream": original_payload.get("stream") is True,
                "action": action,
                **decision,
                "status": "in_progress",
                "original_text": extract_latest_user_text(original_payload, route),
                "sent_text": extract_latest_user_text(sanitized_payload, route),
                "assistant_raw_reply": "",
                "assistant_reply": "",
                "upstream_status": None,
                "error": "",
                "token_count": len(token_map),
                "artifacts": artifacts,
            }
            session["updated_at"] = now
            session["traces"].append(trace)
            self._save_session(session)
            return {"session_id": session_id, "trace_id": trace_id}

    def append_stream_event(self, handle: dict, raw_event: Any, restored_event: Any) -> None:
        with self.lock:
            trace = self._find_trace(handle)
            if trace is None:
                return
            raw_path = Path(trace["artifacts"]["stream_raw"])
            restored_path = Path(trace["artifacts"]["stream_restored"])
            with raw_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(raw_event, ensure_ascii=False) + "\n")
            with restored_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(restored_event, ensure_ascii=False) + "\n")

    def complete_trace(
        self,
        handle: dict,
        *,
        raw_reply: str = "",
        restored_reply: str = "",
        raw_payload: dict | None = None,
        restored_payload: dict | None = None,
        upstream_status: int | None = None,
        status: str = "completed",
        error: str = "",
        response_id: str | None = None,
    ) -> None:
        with self.lock:
            session = self._load_session(handle["session_id"])
            if session is None:
                return
            trace = next((item for item in session["traces"] if item["trace_id"] == handle["trace_id"]), None)
            if trace is None:
                return
            now = _now()
            trace.update(
                {
                    "updated_at": now,
                    "status": status,
                    "assistant_raw_reply": raw_reply,
                    "assistant_reply": restored_reply,
                    "upstream_status": upstream_status,
                    "error": error,
                }
            )
            session["updated_at"] = now
            if raw_payload is not None:
                Path(trace["artifacts"]["response_raw"]).write_text(
                    json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            if restored_payload is not None:
                Path(trace["artifacts"]["response_restored"]).write_text(
                    json.dumps(restored_payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            self._save_session(session)
            if response_id:
                self.response_sessions[response_id] = handle["session_id"]

    def _find_trace(self, handle: dict) -> dict | None:
        session = self._load_session(handle["session_id"])
        if session is None:
            return None
        return next((item for item in session["traces"] if item["trace_id"] == handle["trace_id"]), None)

    def list_sessions(self) -> list[dict]:
        with self.lock:
            sessions: list[dict] = []
            for path in self.base_dir.glob("codex-*/session.json"):
                try:
                    session = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                traces = session.get("traces", [])
                latest = traces[-1] if traces else {}
                sessions.append(
                    {
                        "session_id": session["session_id"],
                        "label": session.get("label", ""),
                        "created_at": session.get("created_at", ""),
                        "updated_at": session.get("updated_at", ""),
                        "trace_count": len(traces),
                        "latest_text": latest.get("original_text", ""),
                        "latest_status": latest.get("status", ""),
                    }
                )
            return sorted(sessions, key=lambda item: item["updated_at"], reverse=True)

    def get_session(self, session_id: str) -> dict | None:
        if not re.fullmatch(r"codex-[0-9a-f]{16}", session_id):
            return None
        with self.lock:
            return self._load_session(session_id)
