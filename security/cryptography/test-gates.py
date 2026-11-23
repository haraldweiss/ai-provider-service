#!/usr/bin/env python3
"""Exercise the real verifier and pip constraint with disposable local inputs."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import cryptography
from cryptography.hazmat.backends.openssl.backend import backend

ROOT = Path(__file__).resolve().parent


class CryptoBuildGates(unittest.TestCase):
    def verify(self, version, openssl):
        return subprocess.run(
            [sys.executable, str(ROOT / "verify-runtime.py"),
             "--expected-cryptography", version, "--expected-openssl", openssl],
            text=True, capture_output=True,
        )

    def test_crypto_operations_with_disposable_keys(self):
        result = self.verify(cryptography.__version__, backend.openssl_version_text().split()[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["checks"],
                         ["Fernet", "AES-GCM", "RSA-PSS", "encrypted-PKCS8",
                          "ECDSA", "Ed25519", "X509-PEM"])

    def test_rejects_wrong_package_even_with_matching_backend(self):
        result = self.verify("0.0.0", backend.openssl_version_text().split()[1])
        self.assertEqual(result.returncode, 1)
        self.assertIn("cryptography_version_mismatch", result.stderr)

    def test_rejects_wrong_bundled_backend_even_with_matching_package(self):
        result = self.verify(cryptography.__version__, "0.0.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("openssl_version_mismatch", result.stderr)

    def test_future_incompatible_requirement_fails_resolution_offline(self):
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--dry-run",
             "--no-index", "--no-deps", "-c", str(ROOT / "constraints.txt"),
             "-r", str(ROOT / "fixtures/incompatible-requirements.txt")],
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ResolutionImpossible", result.stderr)
        self.assertIn("cryptography==50.0.2", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
