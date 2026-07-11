import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

from codex_client import build_session_prompt
from session_state import SessionState, TurnRecord, append_turn, save_session_log
import session_guard


class SessionStateTests(unittest.TestCase):
    def test_session_state_appends_turn_and_builds_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = SessionState.create("coding", Path(tmpdir), "session-demo")
            turn = TurnRecord(
                turn_id=1,
                user_original="email=test@example.com",
                user_redacted="email=[USER_EMAIL_123abc]",
                suggested_action="mask",
                user_sent_text="email=[USER_EMAIL_123abc]",
                codex_raw_reply="The placeholder is [USER_EMAIL_123abc].",
                codex_restored_reply="The placeholder is test@example.com.",
            )

            jsonl_path = append_turn(session, turn)
            session_json = save_session_log(session)

            self.assertTrue(jsonl_path.exists())
            self.assertTrue(session_json.exists())
            history = session.history_for_prompt()
            self.assertEqual(history[0]["user"], "email=[USER_EMAIL_123abc]")
            self.assertEqual(history[0]["assistant"], "The placeholder is test@example.com.")

    def test_build_session_prompt_includes_history_and_current_message(self) -> None:
        prompt = build_session_prompt(
            history=[
                {"user": "first safe message", "assistant": "first reply"},
                {"user": "second safe message", "assistant": "second reply"},
            ],
            current_message="current safe message",
            profile="coding",
        )

        self.assertIn("Active privacy profile: coding.", prompt)
        self.assertIn("User: first safe message", prompt)
        self.assertIn("Assistant: second reply", prompt)
        self.assertIn("Current user message:", prompt)
        self.assertIn("current safe message", prompt)

    @patch("session_guard.run_codex_turn")
    def test_session_turn_falls_back_to_stdout_when_raw_file_missing(self, mock_run_codex) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = SessionState.create("coding", Path(tmpdir), "session-demo")
            mock_run_codex.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="codex\nhello from codex\ntokens used\n100\n",
                stderr="",
            )

            turn = session_guard.run_session_turn(session, "hi", None)

            self.assertIsNotNone(turn)
            assert turn is not None
            self.assertEqual(turn.codex_raw_reply, "hello from codex")
            self.assertEqual(turn.codex_restored_reply, "hello from codex")
            self.assertTrue((session.session_path / "turn-001-codex-raw.txt").exists())

    @patch("session_guard.run_codex_turn")
    def test_session_turn_falls_back_when_raw_file_is_empty(self, mock_run_codex) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = SessionState.create("coding", Path(tmpdir), "session-demo")
            def fake_run(*args, **kwargs):
                raw_path = args[1]
                raw_path.write_text("", encoding="utf-8")
                return subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="codex\nreply from stdout\ntokens used\n100\n",
                    stderr="",
                )

            mock_run_codex.side_effect = fake_run
            turn = session_guard.run_session_turn(session, "hi", None)

            self.assertIsNotNone(turn)
            assert turn is not None
            self.assertEqual(turn.codex_raw_reply, "reply from stdout")


if __name__ == "__main__":
    unittest.main()
