import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import guard


class GuardCodexTests(unittest.TestCase):
    def test_build_codex_prompt_mentions_output_file(self) -> None:
        prompt = guard.build_codex_prompt(Path(r"C:\temp\safe.txt"), "mask")
        self.assertIn(r"C:\temp\safe.txt", prompt)
        self.assertIn("sanitized", prompt)

    @patch("guard.subprocess.run")
    def test_run_codex_exec_passes_output_file(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        output_path = Path(r"C:\temp\safe.txt")

        guard.run_codex_exec(output_path, "mask", r"C:\temp\codex-result.txt", "rightcode")

        command = mock_run.call_args.args[0]
        self.assertEqual(command[1], "exec")
        self.assertTrue(command[0].lower().endswith(("codex.cmd", "codex.exe", "codex")))
        self.assertIn("--profile", command)
        self.assertIn("rightcode", command)
        self.assertIn(str(output_path), " ".join(command))
        self.assertIn("-o", command)
        self.assertIn(r"C:\temp\codex-result.txt", command)
        self.assertIn("env", mock_run.call_args.kwargs)

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


if __name__ == "__main__":
    unittest.main()
