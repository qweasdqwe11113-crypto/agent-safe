import unittest
from pathlib import Path
from unittest.mock import patch

from model_client import ModelClient, ModelClientError, extract_response_output_text, load_codex_provider_config


class ModelClientTests(unittest.TestCase):
    def test_mock_provider_returns_reply(self) -> None:
        client = ModelClient(provider="mock", model="mock-gpt")
        response = client.generate_reply(
            history=[{"user": "hello", "assistant": "hi there"}],
            current_message="how are you?",
            profile="coding",
        )

        self.assertEqual(response.provider, "mock")
        self.assertIn("Latest message: how are you?", response.reply_text)
        self.assertEqual(response.raw_response["history_length"], 1)

    def test_openai_compatible_requires_base_url_and_api_key(self) -> None:
        client = ModelClient(provider="openai_compatible", model="demo-model", api_key=None, base_url=None)

        with self.assertRaises(ModelClientError):
            client.generate_reply([], "hello", "coding")

    def test_extract_response_output_text_reads_message_content(self) -> None:
        payload = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "hello"},
                        {"type": "output_text", "text": "world"},
                    ],
                }
            ]
        }
        self.assertEqual(extract_response_output_text(payload), "hello\nworld")

    def test_load_codex_provider_config_reads_rightcode_values(self) -> None:
        with patch("pathlib.Path.exists", return_value=True), patch(
            "pathlib.Path.read_text",
            return_value=(
                'model = "gpt-5.4"\n'
                "[model_providers]\n"
                "[model_providers.rightcode]\n"
                'base_url = "https://www.rightapi.ai/codex/v1"\n'
            ),
        ):
            config = load_codex_provider_config("rightcode", Path("dummy.toml"))

        self.assertEqual(config["base_url"], "https://www.rightapi.ai/codex/v1")
        self.assertEqual(config["model"], "gpt-5.4")

    def test_from_env_uses_rightcode_config_defaults(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "APG_MODEL_PROVIDER": "rightcode",
                "OPENAI_API_KEY": "demo-key",
            },
            clear=True,
        ), patch(
            "model_client.load_codex_provider_config",
            return_value={"base_url": "https://www.rightapi.ai/codex/v1", "model": "gpt-5.4"},
        ):
            client = ModelClient.from_env()

        self.assertEqual(client.provider, "rightcode")
        self.assertEqual(client.base_url, "https://www.rightapi.ai/codex/v1")
        self.assertEqual(client.model, "gpt-5.4")
        self.assertEqual(client.api_key, "demo-key")

    @patch.object(ModelClient, "_generate_openai_compatible_reply")
    @patch("urllib.request.urlopen")
    def test_rightcode_falls_back_to_chat_completions_on_empty_responses_body(self, mock_urlopen, mock_chat_reply) -> None:
        client = ModelClient(
            provider="rightcode",
            model="gpt-5.4",
            api_key="demo-key",
            base_url="https://www.rightapi.ai/codex/v1",
        )

        class DummyResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b""

        mock_urlopen.return_value = DummyResponse()
        mock_chat_reply.return_value = object()

        result = client.generate_reply([], "hello", "coding")

        self.assertIs(result, mock_chat_reply.return_value)
        mock_chat_reply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
