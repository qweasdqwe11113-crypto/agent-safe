import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

from model_client import ModelClient
from server import GuardHTTPRequestHandler, GuardHTTPServer, SessionStore


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_ner_backend = os.environ.get("APG_NER_BACKEND")
        os.environ["APG_NER_BACKEND"] = "heuristic"
        self.tmpdir = tempfile.TemporaryDirectory()
        self.session_store = SessionStore(Path(self.tmpdir.name))
        self.model_client = ModelClient(provider="mock", model="mock-gpt")
        self.server = GuardHTTPServer(("127.0.0.1", 0), GuardHTTPRequestHandler, self.session_store, self.model_client)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmpdir.cleanup()
        if self.original_ner_backend is None:
            os.environ.pop("APG_NER_BACKEND", None)
        else:
            os.environ["APG_NER_BACKEND"] = self.original_ner_backend

    def request_json(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def request_multipart(self, path: str, field_name: str, filename: str, content: bytes) -> tuple[int, dict]:
        boundary = "----CodexBoundary123456"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_create_preview_and_message_flow(self) -> None:
        status, created = self.request_json("POST", "/sessions", {"profile": "coding", "session_id": "demo-session"})
        self.assertEqual(status, 201)
        self.assertEqual(created["session_id"], "demo-session")

        status, preview = self.request_json("POST", "/sessions/demo-session/preview", {"message": "email=test@example.com"})
        self.assertEqual(status, 200)
        self.assertIn("preview_id", preview)
        self.assertEqual(preview["suggested_action"], "mask")
        self.assertIn("[USER_EMAIL_", preview["redacted_text"])
        self.assertTrue(preview["needs_confirmation"])
        self.assertEqual(preview["action_options"]["allow"]["sent_text"], "email=test@example.com")
        self.assertIn("[USER_EMAIL_", preview["action_options"]["mask"]["sent_text"])
        self.assertIsNone(preview["action_options"]["block"]["sent_text"])

        status, result = self.request_json(
            "POST",
            "/sessions/demo-session/confirm",
            {
                "preview_id": preview["preview_id"],
                "final_action": "mask",
                "override_reason": "demo",
            },
        )
        self.assertEqual(status, 200)
        self.assertFalse(result["blocked"])
        self.assertIn("[USER_EMAIL_", result["sent_text"])
        self.assertIn("[mock:coding]", result["assistant_reply"])
        self.assertFalse(result["override"])

        status, session_payload = self.request_json("GET", "/sessions/demo-session")
        self.assertEqual(status, 200)
        self.assertEqual(len(session_payload["turns"]), 1)

    def test_blocked_message_does_not_call_model(self) -> None:
        self.request_json("POST", "/sessions", {"profile": "coding", "session_id": "blocked-session"})
        _, preview = self.request_json(
            "POST",
            "/sessions/blocked-session/preview",
            {"message": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"},
        )
        status, result = self.request_json(
            "POST",
            "/sessions/blocked-session/confirm",
            {"preview_id": preview["preview_id"], "final_action": "block"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["final_action"], "block")

    def test_messages_endpoint_tells_client_to_use_preview_confirm(self) -> None:
        self.request_json("POST", "/sessions", {"profile": "coding", "session_id": "flow-session"})
        status, result = self.request_json(
            "POST",
            "/sessions/flow-session/messages",
            {"message": "hello"},
        )
        self.assertEqual(status, 400)
        self.assertIn("Use /preview first", result["error"])

    def test_root_serves_web_console(self) -> None:
        request = urllib.request.Request(f"{self.base_url}/", method="GET")
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
        self.assertIn("Agent Privacy Guard", body)
        self.assertIn("发送前治理控制台", body)

    def test_preview_file_upload(self) -> None:
        self.request_json("POST", "/sessions", {"profile": "coding", "session_id": "file-session"})
        status, preview = self.request_multipart(
            "/sessions/file-session/preview-file",
            "file",
            ".env",
            b"OPENAI_API_KEY=sk-abc123def456ghi789jkl012mno345\n",
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["input_kind"], "file")
        self.assertEqual(preview["file_name"], ".env")
        self.assertEqual(preview["suggested_action"], "block")
        self.assertIn("Sensitive File Name", preview["preview_text"])


if __name__ == "__main__":
    unittest.main()
