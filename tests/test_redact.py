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
                "user": {
                    "name": "张三",
                    "email": "test@example.com",
                    "phone": "13800138000",
                    "address": "广东省深圳市南山区科技园科苑路 15 号",
                    "id_card": "440305199901011234",
                },
                "authToken": "super-secret-token-value",
                "notes": ["server ip is 192.168.1.10"],
            }
        )
        result = self.run_script(payload)
        data = json.loads(result.stdout)
        self.assertTrue(data["user"]["name"].startswith("[PERSON_NAME_"))
        self.assertTrue(data["user"]["email"].startswith("[USER_EMAIL_"))
        self.assertTrue(data["user"]["phone"].startswith("[PHONE_NUMBER_"))
        self.assertTrue(data["user"]["address"].startswith("[STREET_ADDRESS_"))
        self.assertTrue(data["user"]["id_card"].startswith("[NATIONAL_ID_"))
        self.assertTrue(data["authToken"].startswith("[SENSITIVE_SECRET_"))
        self.assertIn("[IPV4_ADDRESS_", data["notes"][0])

    def test_plain_text_name_address_and_id_redaction(self) -> None:
        result = self.run_script(
            "name=Zhang San\naddress=Room 502, 88 College Rd\nid_card=440305199901011234\n"
        )
        stdout = result.stdout
        self.assertIn("[PERSON_NAME_", stdout)
        self.assertIn("[STREET_ADDRESS_", stdout)
        self.assertIn("[NATIONAL_ID_", stdout)

    def test_database_url_and_cloud_credentials_redaction(self) -> None:
        payload = (
            "DATABASE_URL=postgres://demo:secretpass@db.internal:5432/appdb\n"
            "aws_access_key_id=AKIAIOSFODNN7EXAMPLE\n"
            "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
            "azure_connection_string=DefaultEndpointsProtocol=https;AccountName=demo;AccountKey=abc123xyz456;EndpointSuffix=core.windows.net\n"
        )
        result = self.run_script(payload)
        stdout = result.stdout
        self.assertIn("[DATABASE_URL_", stdout)
        self.assertIn("[AWS_ACCESS_KEY_", stdout)
        self.assertIn("[AWS_SECRET_KEY_", stdout)
        self.assertIn("[AZURE_CONN_STRING_", stdout)

    def test_cookie_internal_endpoint_and_stack_path_redaction(self) -> None:
        payload = (
            "Cookie: sessionid=abc123; csrftoken=xyz987\n"
            "POST https://internal-api.company.internal/v1/orders\n"
            "File \"C:\\Users\\demo\\.ssh\\id_rsa\", line 42, in main\n"
        )
        result = self.run_script(payload)
        stdout = result.stdout
        self.assertIn("[COOKIE_HEADER_", stdout)
        self.assertIn("[INTERNAL_ENDPOINT_", stdout)
        self.assertIn("[STACK_TRACE_PATH_", stdout)

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
