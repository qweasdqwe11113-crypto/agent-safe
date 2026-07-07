import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(r"C:\Users\jiahjq\Desktop\summer_projection\guard.py")


class GuardScriptTests(unittest.TestCase):
    def run_guard(self, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_stdin_coding_profile_blocks_secrets(self) -> None:
        result = self.run_guard(
            "--stdin",
            "--profile",
            "coding",
            input_text="Authorization: Bearer abcdefghijklmnopqrstuvwxyz\nemail=test@example.com\n",
        )
        stdout = result.stdout
        self.assertIn("Detection Results:", stdout)
        self.assertIn("Risk Level: HIGH", stdout)
        self.assertIn("Suggested Action: BLOCK", stdout)
        self.assertIn("Final Action: BLOCK", stdout)
        self.assertIn("Original Content:", stdout)
        self.assertIn("[AUTH_TOKEN_", stdout)
        self.assertIn("[USER_EMAIL_", stdout)

    def test_file_input_masks_pii_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "contact.txt"
            input_path.write_text("email=test@example.com\nphone=13800138000\n", encoding="utf-8")

            result = self.run_guard(str(input_path), "--profile", "coding")

        stdout = result.stdout
        self.assertIn("Risk Level: MEDIUM", stdout)
        self.assertIn("Suggested Action: MASK", stdout)
        self.assertIn("Final Action: MASK", stdout)
        self.assertIn("User Email: 1", stdout)
        self.assertIn("Phone Number: 1", stdout)

    def test_name_address_and_id_are_treated_as_pii(self) -> None:
        result = self.run_guard(
            "--stdin",
            "--profile",
            "coding",
            input_text="name=Zhang San\naddress=Room 502, 88 College Rd\nid_card=440305199901011234\n",
        )
        stdout = result.stdout
        self.assertIn("Risk Level: MEDIUM", stdout)
        self.assertIn("Suggested Action: MASK", stdout)
        self.assertIn("Person Name: 1", stdout)
        self.assertIn("Street Address: 1", stdout)
        self.assertIn("National Id: 1", stdout)

    def test_free_text_ner_like_detection_finds_name_and_address(self) -> None:
        result = self.run_guard(
            "--stdin",
            "--profile",
            "coding",
            input_text="我叫张三，住在深圳市南山区科技园科苑路15号。\n",
        )
        stdout = result.stdout
        self.assertIn("Risk Level: MEDIUM", stdout)
        self.assertIn("Person Name: 1", stdout)
        self.assertIn("Street Address: 1", stdout)


if __name__ == "__main__":
    unittest.main()
