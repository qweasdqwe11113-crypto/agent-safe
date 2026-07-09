import tempfile
import unittest
from pathlib import Path

from guard_core import POLICY_TEMPLATES, PROFILE_POLICIES, load_policy_templates, load_profile_policies


class GuardCorePolicyTests(unittest.TestCase):
    def test_default_policy_files_are_loaded(self) -> None:
        self.assertIn("coding", PROFILE_POLICIES)
        self.assertEqual(PROFILE_POLICIES["coding"]["block_categories"], {"secret", "file"})
        self.assertEqual(PROFILE_POLICIES["finance"]["block_categories"], {"secret", "finance", "file"})
        self.assertEqual(POLICY_TEMPLATES["coding"].title, "代码场景隐私模板")
        self.assertGreater(len(POLICY_TEMPLATES["office"].sample_inputs), 0)

    def test_load_profile_policies_reads_json_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_path = Path(tmpdir) / "demo.json"
            policy_path.write_text(
                (
                    '{'
                    '"profile":"demo",'
                    '"block_categories":["secret"],'
                    '"mask_categories":["pii","network"]'
                    '}'
                ),
                encoding="utf-8",
            )

            policies = load_profile_policies(Path(tmpdir))

        self.assertEqual(
            policies["demo"],
            {"block_categories": {"secret"}, "mask_categories": {"pii", "network"}},
        )

    def test_load_policy_templates_reads_metadata_and_runtime_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_path = Path(tmpdir) / "demo.json"
            policy_path.write_text(
                (
                    "{"
                    '"profile":"demo",'
                    '"title":"Demo Template",'
                    '"description":"Demo description",'
                    '"block_categories":["secret"],'
                    '"mask_categories":["pii"],'
                    '"sample_inputs":[{"title":"Case","content":"hello"}],'
                    '"expected_outcomes":["Mask pii"],'
                    '"applicability_notes":["demo note"],'
                    '"false_positive_notes":["demo fp"],'
                    '"sensitive_file_names":["demo.secret"],'
                    '"large_file_threshold_bytes":64'
                    "}"
                ),
                encoding="utf-8",
            )

            templates = load_policy_templates(Path(tmpdir))

        self.assertEqual(templates["demo"].title, "Demo Template")
        self.assertEqual(templates["demo"].sample_inputs[0]["title"], "Case")
        self.assertIn("demo.secret", templates["demo"].sensitive_file_names)
        self.assertEqual(templates["demo"].large_file_threshold_bytes, 64)


if __name__ == "__main__":
    unittest.main()
