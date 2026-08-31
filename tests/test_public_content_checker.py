from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_public_content",
    ROOT / "scripts" / "check_public_content.py",
)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class PublicContentCheckerTests(unittest.TestCase):
    @staticmethod
    def assignment(name: str, value: str) -> str:
        return f"{name}={value}"

    def test_rejects_standard_private_key_headers(self) -> None:
        for key_kind in ("", "ENCRYPTED ", "RSA ", "DSA ", "EC ", "OPENSSH "):
            header = "-----" + "BEGIN " + key_kind + "PRIVATE KEY" + "-----"
            with self.subTest(key_kind=key_kind or "PKCS8"):
                self.assertIn(
                    "private-key-material",
                    CHECKER.credential_rules(header),
                )

    def test_common_substrings_do_not_exempt_credentials(self) -> None:
        examples = (
            ("password", "ExamplePassword123!"),
            ("client_secret", "UnreplaceableSecret123"),
            ("api_key", "NotActuallyRedacted456"),
        )
        for name, value in examples:
            with self.subTest(name=name):
                self.assertIn(
                    "credential-material",
                    CHECKER.credential_rules(self.assignment(name, value)),
                )

    def test_only_explicit_placeholder_forms_are_exempt(self) -> None:
        placeholders = (
            "$PASSWORD",
            "${CLIENT_SECRET}",
            "<client-secret>",
            "{{ api_key }}",
            "example-password",
            "replace_me",
            "redacted-value",
            "dummy-token",
            "changeme",
            "your_client_secret",
        )
        for value in placeholders:
            with self.subTest(value=value):
                self.assertTrue(CHECKER.is_placeholder(value))
                self.assertNotIn(
                    "credential-material",
                    CHECKER.credential_rules(
                        self.assignment("client_secret", value)
                    ),
                )


if __name__ == "__main__":
    unittest.main()
