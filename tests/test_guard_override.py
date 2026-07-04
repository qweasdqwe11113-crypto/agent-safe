import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(r"C:\Users\jiahjq\Desktop\summer_projection\guard.py")


class GuardOverrideTests(unittest.TestCase):
    def test_override_changes_final_action(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--stdin",
                "--profile",
                "coding",
                "--override",
                "mask",
                "--override-reason",
                "demo approval",
            ],
            input="Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n",
            capture_output=True,
            text=True,
            check=True,
        )

        stdout = result.stdout
        self.assertIn("Suggested Action: BLOCK", stdout)
        self.assertIn("Final Action: MASK", stdout)
        self.assertIn("Override: YES (demo approval)", stdout)

    def test_out_writes_redacted_content_for_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "safe.txt"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--stdin",
                    "--profile",
                    "coding",
                    "--override",
                    "mask",
                    "--override-reason",
                    "demo approval",
                    "--out",
                    str(output_path),
                ],
                input="Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n",
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertTrue(output_path.exists())
            written = output_path.read_text(encoding="utf-8")

        self.assertIn("[AUTH_TOKEN_", written)
        self.assertIn(f"Output File: {output_path}", result.stdout)

    def test_out_does_not_write_for_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "blocked.txt"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--stdin",
                    "--profile",
                    "coding",
                    "--out",
                    str(output_path),
                ],
                input="Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n",
                capture_output=True,
                text=True,
                check=True,
            )

            exists_after = output_path.exists()

        self.assertFalse(exists_after)
        self.assertIn("Output File: not written because final action is BLOCK", result.stdout)


if __name__ == "__main__":
    unittest.main()
