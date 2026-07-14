import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from model_client import ModelClient
from server import GatewayClient, GuardHTTPRequestHandler, GuardHTTPServer, SessionStore


class StubUpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        self.server.requests.append((self.path, payload, dict(self.headers)))

        if payload.get("stream") is True:
            if self.path == "/responses":
                last_text = payload["input"][-1]["content"][0]["text"]
                body = (
                    'data: {"type":"response.created","response":{"id":"resp_test","object":"response","status":"in_progress"}}\n\n'
                    'data: {"type":"response.output_item.added","response_id":"resp_test","output_index":0,"item":{"id":"msg_test","type":"message","role":"assistant","status":"in_progress","content":[]}}\n\n'
                    'data: {"type":"response.output_text.delta","delta":"reply:'
                    + last_text
                    + '","response_id":"resp_test","item_id":"msg_test","output_index":0,"content_index":0}\n\n'
                    'data: {"type":"response.output_text.done","text":"reply:'
                    + last_text
                    + '","response_id":"resp_test","item_id":"msg_test","output_index":0,"content_index":0}\n\n'
                    'data: {"type":"response.completed","response":{"id":"resp_test","object":"response","status":"completed"}}\n\n'
                    "data: [DONE]\n\n"
                ).encode("utf-8")
            elif self.path == "/chat/completions":
                last_text = payload["messages"][-1]["content"]
                body = (
                    'data: {"id":"chatcmpl_test","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"reply:'
                    + last_text
                    + '"},"finish_reason":null}]}\n\n'
                    'data: {"id":"chatcmpl_test","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                    "data: [DONE]\n\n"
                ).encode("utf-8")
            else:
                self.send_response(404)
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return

        if self.path == "/responses":
            last_text = payload["input"][-1]["content"][0]["text"]
            body = {
                "id": "resp_test",
                "object": "response",
                "status": "completed",
                "model": payload.get("model", "test-model"),
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": f"reply:{last_text}",
                            }
                        ],
                    }
                ],
                "output_text": f"reply:{last_text}",
            }
        elif self.path == "/chat/completions":
            last_text = payload["messages"][-1]["content"]
            body = {
                "id": "chatcmpl_test",
                "object": "chat.completion",
                "created": 0,
                "model": payload.get("model", "test-model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": f"reply:{last_text}",
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        else:
            self.send_response(404)
            self.end_headers()
            return

        encoded = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class StubUpstreamServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class):
        super().__init__(server_address, handler_class)
        self.requests = []


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_ner_backend = os.environ.get("APG_NER_BACKEND")
        os.environ["APG_NER_BACKEND"] = "heuristic"
        self.tmpdir = tempfile.TemporaryDirectory()
        self.session_store = SessionStore(Path(self.tmpdir.name))
        self.model_client = ModelClient(provider="mock", model="mock-gpt")
        self.upstream = StubUpstreamServer(("127.0.0.1", 0), StubUpstreamHandler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        gateway_client = GatewayClient(base_url=f"http://127.0.0.1:{self.upstream.server_port}", timeout_seconds=10)
        self.server = GuardHTTPServer(
            ("127.0.0.1", 0),
            GuardHTTPRequestHandler,
            self.session_store,
            self.model_client,
            gateway_client,
            "coding",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=5)
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

    def request_text(self, method: str, path: str, payload: dict) -> tuple[int, str, str]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8")

    def test_create_preview_and_message_flow(self) -> None:
        status, created = self.request_json("POST", "/sessions", {"profile": "coding", "session_id": "demo-session"})
        self.assertEqual(status, 201)
        self.assertEqual(created["session_id"], "demo-session")

        status, preview = self.request_json("POST", "/sessions/demo-session/preview", {"message": "email=test@example.com"})
        self.assertEqual(status, 200)
        self.assertEqual(preview["suggested_action"], "mask")
        self.assertIn("[USER_EMAIL_", preview["redacted_text"])
        self.assertIn("[USER_EMAIL_", preview["suggested_sent_text"])
        self.assertFalse(preview["blocked"])

        status, result = self.request_json(
            "POST",
            "/sessions/demo-session/messages",
            {"message": "email=test@example.com"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(result["blocked"])
        self.assertIn("[USER_EMAIL_", result["sent_text"])
        self.assertIn("[mock:coding]", result["assistant_reply"])
        self.assertEqual(result["action"], "mask")

        status, session_payload = self.request_json("GET", "/sessions/demo-session")
        self.assertEqual(status, 200)
        self.assertEqual(len(session_payload["turns"]), 1)

    def test_blocked_message_does_not_call_model(self) -> None:
        self.request_json("POST", "/sessions", {"profile": "coding", "session_id": "blocked-session"})
        status, result = self.request_json(
            "POST",
            "/sessions/blocked-session/messages",
            {"message": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["action"], "block")

    def test_messages_endpoint_applies_the_policy_automatically(self) -> None:
        self.request_json("POST", "/sessions", {"profile": "coding", "session_id": "flow-session"})
        status, result = self.request_json(
            "POST",
            "/sessions/flow-session/messages",
            {"message": "hello"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["action"], "allow")

    def test_root_is_not_exposed(self) -> None:
        status, payload = self.request_json("GET", "/")
        self.assertEqual(status, 404)
        self.assertIn("Route not found", payload["error"])

    def test_debug_console_is_available(self) -> None:
        request = urllib.request.Request(f"{self.base_url}/debug", method="GET")
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("text/html", response.headers.get("Content-Type", ""))
        self.assertIn("Agent Privacy Guard", body)
        self.assertIn("真实 Codex 会话", body)

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

    def test_profiles_endpoint_returns_template_metadata(self) -> None:
        status, payload = self.request_json("GET", "/profiles")
        self.assertEqual(status, 200)
        self.assertIn("coding", payload["profiles"])
        coding_template = next(item for item in payload["templates"] if item["profile"] == "coding")
        self.assertEqual(coding_template["title"], "代码场景隐私模板")
        self.assertGreater(len(coding_template["sample_inputs"]), 0)


    def test_gateway_responses_route_masks_request_and_restores_reply(self) -> None:
        status, payload = self.request_json(
            "POST",
            "/v1/responses",
            {
                "model": "test-model",
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "email=test@example.com"}],
                    }
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["output_text"], "reply:email=test@example.com")
        upstream_path, upstream_payload, _ = self.upstream.requests[-1]
        self.assertEqual(upstream_path, "/responses")
        self.assertIn("[USER_EMAIL_", upstream_payload["input"][-1]["content"][0]["text"])

        status, sessions_payload = self.request_json("GET", "/gateway-traces")
        self.assertEqual(status, 200)
        self.assertEqual(len(sessions_payload["sessions"]), 1)
        session_id = sessions_payload["sessions"][0]["session_id"]
        status, trace_session = self.request_json("GET", f"/gateway-traces/{session_id}")
        self.assertEqual(status, 200)
        trace = trace_session["traces"][0]
        self.assertEqual(trace["original_text"], "email=test@example.com")
        self.assertIn("[USER_EMAIL_", trace["sent_text"])
        self.assertIn("[USER_EMAIL_", trace["assistant_raw_reply"])
        self.assertEqual(trace["assistant_reply"], "reply:email=test@example.com")
        self.assertEqual(trace["status"], "completed")

    def test_gateway_chat_completions_route_masks_request_and_restores_reply(self) -> None:
        status, payload = self.request_json(
            "POST",
            "/v1/chat/completions",
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "email=test@example.com"}],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["choices"][0]["message"]["content"], "reply:email=test@example.com")
        upstream_path, upstream_payload, _ = self.upstream.requests[-1]
        self.assertEqual(upstream_path, "/chat/completions")
        self.assertIn("[USER_EMAIL_", upstream_payload["messages"][-1]["content"])

    def test_gateway_blockable_text_is_masked_and_forwarded(self) -> None:
        before_count = len(self.upstream.requests)
        status, payload = self.request_json(
            "POST",
            "/v1/chat/completions",
            {
                "model": "test-model",
                "messages": [
                    {"role": "user", "content": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"},
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("reply:Authorization: Bearer ", payload["choices"][0]["message"]["content"])
        self.assertEqual(len(self.upstream.requests), before_count + 1)
        upstream_path, upstream_payload, _ = self.upstream.requests[-1]
        self.assertEqual(upstream_path, "/chat/completions")
        self.assertIn("[AUTH_TOKEN_", upstream_payload["messages"][-1]["content"])

    def test_gateway_chat_completions_stream_restores_sse_chunks(self) -> None:
        status, content_type, body = self.request_text(
            "POST",
            "/v1/chat/completions",
            {
                "model": "test-model",
                "stream": True,
                "messages": [{"role": "user", "content": "email=test@example.com"}],
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", content_type)
        self.assertIn("reply:email=test@example.com", body)
        self.assertIn("data: [DONE]", body)
        upstream_path, upstream_payload, _ = self.upstream.requests[-1]
        self.assertEqual(upstream_path, "/chat/completions")
        self.assertTrue(upstream_payload["stream"])

    def test_gateway_responses_stream_restores_sse_chunks(self) -> None:
        status, content_type, body = self.request_text(
            "POST",
            "/v1/responses",
            {
                "model": "test-model",
                "stream": True,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "email=test@example.com"}],
                    }
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", content_type)
        self.assertIn("reply:email=test@example.com", body)
        self.assertIn("data: [DONE]", body)
        upstream_path, upstream_payload, _ = self.upstream.requests[-1]
        self.assertEqual(upstream_path, "/responses")
        self.assertTrue(upstream_payload["stream"])

        status, sessions_payload = self.request_json("GET", "/gateway-traces")
        self.assertEqual(status, 200)
        session_id = sessions_payload["sessions"][0]["session_id"]
        status, trace_session = self.request_json("GET", f"/gateway-traces/{session_id}")
        self.assertEqual(status, 200)
        trace = trace_session["traces"][0]
        self.assertEqual(trace["status"], "completed")
        self.assertIn("[USER_EMAIL_", trace["assistant_raw_reply"])
        self.assertEqual(trace["assistant_reply"], "reply:email=test@example.com")
        self.assertTrue(Path(trace["artifacts"]["stream_raw"]).exists())

    def test_gateway_traces_group_requests_by_prompt_cache_key(self) -> None:
        for message in ("first", "second"):
            status, _ = self.request_json(
                "POST",
                "/v1/responses",
                {
                    "model": "test-model",
                    "prompt_cache_key": "codex-thread-123",
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": message}],
                        }
                    ],
                },
            )
            self.assertEqual(status, 200)

        status, sessions_payload = self.request_json("GET", "/gateway-traces")
        self.assertEqual(status, 200)
        self.assertEqual(len(sessions_payload["sessions"]), 1)
        session_id = sessions_payload["sessions"][0]["session_id"]
        status, trace_session = self.request_json("GET", f"/gateway-traces/{session_id}")
        self.assertEqual(status, 200)
        self.assertEqual([trace["original_text"] for trace in trace_session["traces"]], ["first", "second"])

    def test_gateway_responses_stream_blockable_text_is_masked_and_completes(self) -> None:
        before_count = len(self.upstream.requests)
        status, content_type, body = self.request_text(
            "POST",
            "/v1/responses",
            {
                "model": "test-model",
                "stream": True,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
                            }
                        ],
                    }
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", content_type)
        self.assertIn('"type": "response.created"', body)
        self.assertIn('"type": "response.output_item.added"', body)
        self.assertIn('"type": "response.output_text.delta"', body)
        self.assertIn('"type": "response.output_text.done"', body)
        self.assertIn('"type": "response.completed"', body)
        self.assertIn("data: [DONE]", body)
        self.assertEqual(len(self.upstream.requests), before_count + 1)
        upstream_path, upstream_payload, _ = self.upstream.requests[-1]
        self.assertEqual(upstream_path, "/responses")
        self.assertIn("[AUTH_TOKEN_", upstream_payload["input"][-1]["content"][0]["text"])

    def test_gateway_responses_stream_preserves_codex_event_sequence(self) -> None:
        status, content_type, body = self.request_text(
            "POST",
            "/v1/responses",
            {
                "model": "test-model",
                "stream": True,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "email=test@example.com"}],
                    }
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", content_type)
        self.assertIn('"type": "response.created"', body)
        self.assertIn('"type": "response.output_item.added"', body)
        self.assertIn('"type": "response.output_text.delta"', body)
        self.assertIn('"type": "response.output_text.done"', body)
        self.assertIn('"type": "response.completed"', body)
        self.assertLess(body.index('"type": "response.created"'), body.index('"type": "response.output_item.added"'))
        self.assertLess(body.index('"type": "response.output_item.added"'), body.index('"type": "response.output_text.delta"'))
        self.assertLess(body.index('"type": "response.output_text.delta"'), body.index('"type": "response.output_text.done"'))
        self.assertLess(body.index('"type": "response.output_text.done"'), body.index('"type": "response.completed"'))


if __name__ == "__main__":
    unittest.main()
