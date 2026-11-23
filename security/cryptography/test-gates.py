#!/usr/bin/env python3
"""Exercise the real verifier and pip constraint with disposable local inputs."""
import importlib.util
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


class RuntimeContractTests(unittest.TestCase):
    """The image contract must tolerate Docker omitting empty fields - nothing else."""

    @staticmethod
    def smoke():
        spec = importlib.util.spec_from_file_location("smoke_image", ROOT / "smoke-image.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def contract(self):
        return json.loads((ROOT / "runtime-contract.json").read_text())

    def test_omitted_empty_user_matches_contract(self):
        # Docker 29 omits Config.User entirely when it is empty (verified live).
        contract = self.contract()
        config = {key: value for key, value in contract.items() if key != "User"}
        self.assertNotIn("User", config)
        self.assertIsNone(self.smoke().contract_mismatch(config, contract))

    def test_explicit_empty_user_matches_contract(self):
        contract = self.contract()
        self.assertIsNone(self.smoke().contract_mismatch(dict(contract, User=""), contract))

    def test_changed_user_is_rejected(self):
        contract = self.contract()
        self.assertEqual(self.smoke().contract_mismatch(dict(contract, User="appuser"), contract), "User")

    def test_null_is_not_treated_as_empty(self):
        contract = self.contract()
        self.assertEqual(self.smoke().contract_mismatch(dict(contract, User=None), contract), "User")

    def test_missing_non_empty_field_is_rejected(self):
        contract = self.contract()
        config = {key: value for key, value in contract.items() if key != "WorkingDir"}
        self.assertEqual(self.smoke().contract_mismatch(config, contract), "WorkingDir")

    def test_changed_command_or_ports_are_rejected(self):
        contract = self.contract()
        self.assertEqual(self.smoke().contract_mismatch(dict(contract, Cmd=["bash"]), contract), "Cmd")
        self.assertEqual(self.smoke().contract_mismatch(dict(contract, ExposedPorts={}), contract),
                         "ExposedPorts")


if __name__ == "__main__":
    unittest.main()
