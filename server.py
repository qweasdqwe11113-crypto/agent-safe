#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import uuid
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import urlparse
from dataclasses import asdict

from guard_core import PROFILE_POLICIES, RISK_LEVELS, apply_final_action, build_preview, restore_response, scan_file_bytes, scan_text
from model_client import ModelClient, ModelClientError
from session_state import SessionState, TurnRecord, append_turn, load_session, save_session_log


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

        if path == "/" or self.server.has_static_file(path):
            self._serve_static(path)
            return

        if path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return

        if path == "/profiles":
            self._write_json(HTTPStatus.OK, {"profiles": sorted(PROFILE_POLICIES)})
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

    def _serve_static(self, path: str) -> None:
        static_file = self.server.resolve_static_path(path)
        if static_file is None or not static_file.exists() or not static_file.is_file():
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Static file not found"})
            return

        content_type = guess_type(static_file.name)[0] or "application/octet-stream"
        body = static_file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/sessions":
            payload = self._read_json_body()
            if payload is None:
                return
            self._handle_create_session(payload)
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
        action_options = build_action_options(scan_result)
        preview_payload = {
            "message": message,
            "profile": session.profile,
            "original_text": scan_result.original_text,
            "redacted_text": scan_result.redacted_text,
            "token_map": scan_result.token_map,
            "suggested_action": scan_result.suggested_action,
            "risk_level": RISK_LEVELS[scan_result.suggested_action],
            "suggested_sent_text": apply_final_action(scan_result, scan_result.suggested_action),
            "action_options": action_options,
            "preview_text": build_preview(scan_result),
        }
        preview_id = self.server.session_store.save_preview(session_id, preview_payload)

        self._write_json(
            HTTPStatus.OK,
            {
                "preview_id": preview_id,
                "session_id": session_id,
                "profile": session.profile,
                "input_kind": "text",
                **preview_payload,
                "needs_confirmation": True,
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
        action_options = build_action_options(scan_result)
        preview_payload = {
            "message": f"[file] {file_name}",
            "profile": session.profile,
            "original_text": scan_result.original_text,
            "redacted_text": scan_result.redacted_text,
            "token_map": scan_result.token_map,
            "suggested_action": scan_result.suggested_action,
            "risk_level": RISK_LEVELS[scan_result.suggested_action],
            "suggested_sent_text": apply_final_action(scan_result, scan_result.suggested_action),
            "action_options": action_options,
            "preview_text": build_preview(scan_result),
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
                "needs_confirmation": True,
            },
        )

    def _handle_message(self, session_id: str, payload: dict) -> None:
        self._write_json(
            HTTPStatus.BAD_REQUEST,
            {
                "error": "Use /preview first, then /confirm with preview_id and final_action.",
            },
        )

    def _handle_confirm(self, session_id: str, payload: dict) -> None:
        session = self.server.session_store.get_session(session_id)
        if session is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Session not found"})
            return

        preview_id = payload.get("preview_id", "")
        if not isinstance(preview_id, str) or not preview_id.strip():
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "preview_id must be a non-empty string"})
            return
        preview_payload = self.server.session_store.pop_preview(preview_id)
        if preview_payload is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Preview not found or already confirmed"})
            return
        if preview_payload["session_id"] != session_id:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "preview_id does not belong to this session"})
            return

        final_action = payload.get("final_action") or preview_payload["suggested_action"]
        if final_action not in RISK_LEVELS:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid final_action: {final_action}"})
            return
        override_reason = payload.get("override_reason")

        safe_text = preview_payload["action_options"][final_action]["sent_text"]
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
                final_action=final_action,
                override_reason=override_reason,
                user_sent_text="",
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
                    "final_action": final_action,
                    "blocked": True,
                    "override": final_action != preview_payload["suggested_action"],
                    "override_reason": override_reason,
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
            final_action=final_action,
            override_reason=override_reason,
            user_sent_text=safe_text,
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
                "final_action": final_action,
                "override": final_action != preview_payload["suggested_action"],
                "override_reason": override_reason,
                "original_text": preview_payload["original_text"],
                "redacted_text": preview_payload["redacted_text"],
                "sent_text": safe_text,
                "assistant_reply": restored_reply,
                "assistant_raw_reply": model_response.reply_text,
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

    def _write_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GuardHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        session_store: SessionStore,
        model_client: ModelClient,
        web_root: Path | None = None,
    ):
        super().__init__(server_address, RequestHandlerClass)
        self.session_store = session_store
        self.model_client = model_client
        self.web_root = (web_root or Path(__file__).resolve().parent / "web").resolve()

    def has_static_file(self, request_path: str) -> bool:
        candidate = self.resolve_static_path(request_path)
        return candidate is not None and candidate.exists() and candidate.is_file()

    def resolve_static_path(self, request_path: str) -> Path | None:
        relative_path = "index.html" if request_path == "/" else request_path.lstrip("/")
        candidate = (self.web_root / relative_path).resolve()
        try:
            candidate.relative_to(self.web_root)
        except ValueError:
            return None
        return candidate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Agent Privacy Guard HTTP API server.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on.")
    parser.add_argument("--output-dir", default="outputs/api-sessions", help="Directory used to store session artifacts.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session_store = SessionStore(Path(args.output_dir))
    model_client = ModelClient.from_env()
    server = GuardHTTPServer((args.host, args.port), GuardHTTPRequestHandler, session_store, model_client)
    print(f"Agent Privacy Guard API listening on http://{args.host}:{args.port}")
    print(f"Model provider: {model_client.provider} ({model_client.model})")
    print(f"Session output dir: {Path(args.output_dir).resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
