import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(r"C:\Users\jiahjq\Desktop\summer_projection\codex-privacy-filter\scripts\redact.py")


class RedactScriptTests(unittest.TestCase):
    def run_script(self, input_text: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_plain_text_redaction(self) -> None:
        result = self.run_script(
            "email=test@example.com\nphone=13800138000\napi_key=sk-abc123def456ghi789jkl012mno345\n"
        )
        stdout = result.stdout
        self.assertIn("[USER_EMAIL_", stdout)
        self.assertIn("[PHONE_NUMBER_", stdout)
        self.assertIn("[SENSITIVE_SECRET_", stdout)

    def test_json_recursive_redaction(self) -> None:
        payload = json.dumps(
            {
                "user": {"email": "test@example.com", "phone": "13800138000"},
                "authToken": "super-secret-token-value",
                "notes": ["server ip is 192.168.1.10"],
            }
        )
        result = self.run_script(payload)
        data = json.loads(result.stdout)
        self.assertTrue(data["user"]["email"].startswith("[USER_EMAIL_"))
        self.assertTrue(data["user"]["phone"].startswith("[PHONE_NUMBER_"))
        self.assertTrue(data["authToken"].startswith("[SENSITIVE_SECRET_"))
        self.assertIn("[IPV4_ADDRESS_", data["notes"][0])

    def test_map_output_and_restore_round_trip(self) -> None:
        original = "email=test@example.com\nphone=13800138000\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            map_path = Path(tmpdir) / "token-map.json"
            redacted = self.run_script(original, "--map-out", str(map_path)).stdout
            self.assertTrue(map_path.exists())

            token_map = json.loads(map_path.read_text(encoding="utf-8"))
            self.assertIn("test@example.com", token_map.values())
            self.assertIn("13800138000", token_map.values())

            restored = self.run_script(redacted, "--restore-map", str(map_path)).stdout
            self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()