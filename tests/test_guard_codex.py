import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

import guard
from codex_client import extract_assistant_reply


class GuardCodexTests(unittest.TestCase):
    def test_build_codex_prompt_mentions_output_file(self) -> None:
        prompt = guard.build_codex_prompt(Path(r"C:\temp\safe.txt"), "mask")
        self.assertIn(r"C:\temp\safe.txt", prompt)
        self.assertIn("sanitized", prompt)

    @patch("guard.run_codex_turn")
    def test_run_codex_exec_passes_output_file(self, mock_run) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "safe.txt"
            codex_output_path = Path(tmpdir) / "codex-result.txt"

            guard.run_codex_exec(output_path, "mask", str(codex_output_path), "rightcode")

            prompt = mock_run.call_args.args[0]
            self.assertIn(str(output_path), prompt)
            self.assertEqual(mock_run.call_args.args[1], codex_output_path)
            self.assertEqual(mock_run.call_args.args[2], "rightcode")

    def test_codex_output_requires_codex_flag(self) -> None:
        with self.assertRaises(SystemExit):
            with patch(
                "sys.argv",
                [
                    "guard.py",
                    "examples/plain-input.txt",
                    "--profile",
                    "coding",
                    "--codex-output",
                    "result.txt",
                ],
            ):
                guard.parse_args()

    def test_codex_requires_out_flag(self) -> None:
        with self.assertRaises(SystemExit):
            with patch(
                "sys.argv",
                [
                    "guard.py",
                    "examples/plain-input.txt",
                    "--profile",
                    "coding",
                    "--codex",
                ],
            ):
                guard.parse_args()

    def test_codex_profile_requires_codex_flag(self) -> None:
        with self.assertRaises(SystemExit):
            with patch(
                "sys.argv",
                [
                    "guard.py",
                    "examples/plain-input.txt",
                    "--profile",
                    "coding",
                    "--codex-profile",
                    "rightcode",
                ],
            ):
                guard.parse_args()

    def test_write_guard_artifacts_saves_original_and_token_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "safe.txt"
            token_map = {"[USER_EMAIL_123abc]": "test@example.com"}

            original_path, token_map_path = guard.write_guard_artifacts(
                output_path,
                "email=test@example.com\n",
                token_map,
            )

            self.assertEqual(original_path.read_text(encoding="utf-8"), "email=test@example.com\n")
            self.assertIsNotNone(token_map_path)
            self.assertIn("[USER_EMAIL_123abc]", token_map_path.read_text(encoding="utf-8"))

    def test_restore_codex_output_replaces_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_output_path = Path(tmpdir) / "codex-result.txt"
            codex_output_path.write_text(
                "The email placeholder is [USER_EMAIL_123abc].\n",
                encoding="utf-8",
            )

            restored_path = guard.restore_codex_output(
                codex_output_path,
                {"[USER_EMAIL_123abc]": "test@example.com"},
            )

            self.assertEqual(restored_path.name, "codex-result-restored.txt")
            self.assertIn("test@example.com", restored_path.read_text(encoding="utf-8"))

    def test_extract_assistant_reply_reads_last_codex_block(self) -> None:
        stdout = (
            "OpenAI Codex v0.132.0\n"
            "user\n"
            "hi\n"
            "codex\n"
            "first reply\n"
            "tokens used\n"
            "123\n"
            "codex\n"
            "second reply line 1\n"
            "second reply line 2\n"
            "tokens used\n"
            "456\n"
        )
        self.assertEqual(extract_assistant_reply(stdout), "second reply line 1\nsecond reply line 2")

    @patch("guard.run_codex_turn")
    def test_run_codex_exec_writes_fallback_output_when_file_missing(self, mock_run) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "safe.txt"
            output_path.write_text("safe", encoding="utf-8")
            codex_output_path = Path(tmpdir) / "codex-result.txt"
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="codex\nfallback reply\ntokens used\n123\n",
                stderr="",
            )

            guard.run_codex_exec(output_path, "mask", str(codex_output_path), None)

            self.assertTrue(codex_output_path.exists())
            self.assertEqual(codex_output_path.read_text(encoding="utf-8"), "fallback reply")


if __name__ == "__main__":
    unittest.main()
