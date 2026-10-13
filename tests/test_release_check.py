"""Publication checks using synthetic credentials, without exposing local keys."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "release_check", Path(__file__).resolve().parents[1] / "scripts" / "check_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseCheckTests(unittest.TestCase):
    def test_configured_secret_is_rejected_without_echoing_it(self):
        secret = b"synthetic" + b"-credential-value"
        findings = release.inspect_blob("README.md", b"text " + secret, [secret])
        self.assertIn("matches a configured local secret", findings)
        self.assertNotIn(secret.decode(), " ".join(findings))

    def test_staged_env_or_private_key_is_rejected(self):
        for path in (".env", ".env.old", "abaqus_agent/.env", "private.pem", "access.key"):
            with self.subTest(path=path):
                self.assertTrue(release.inspect_blob(path, b""))

    def test_local_runtime_data_is_rejected(self):
        for path in ("workspace/task.py", "tests/native_runs/id/report.json",
                     "abaqus_agent/state/snapshot.json", "custom/.ipc/task.json"):
            with self.subTest(path=path):
                self.assertTrue(release.inspect_blob(path, b""))

    def test_placeholders_source_and_empty_directory_markers_are_allowed(self):
        for path, content in (
            (".env.example", b"OPENAI_API_KEY=your_api_key_here"),
            ("example.py", b"api_key = 'your_api_key_here'"),
            ("config.py", b"API_KEY = os.environ.get('OPENAI_API_KEY')"),
            ("workspace/.gitkeep", b""),
            ("abaqus_agent/state/.gitkeep", b""),
        ):
            with self.subTest(path=path):
                self.assertFalse(release.inspect_blob(path, content))

    def test_common_token_and_literal_assignment_patterns_are_rejected(self):
        for content in (b"sk-" + b"a" * 24, b"ghp_" + b"b" * 32,
                        b"API_KEY = '" + b"literal-credential" + b"'"):
            self.assertTrue(release.inspect_blob("example.py", content))


if __name__ == "__main__":
    unittest.main()
