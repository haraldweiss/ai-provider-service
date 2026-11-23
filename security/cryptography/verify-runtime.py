#!/usr/bin/env python3
"""Check the rebuilt crypto backend using disposable keys and no application data."""
import argparse
import datetime
import json
import os

import cryptography
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.backends.openssl.backend import backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-cryptography", required=True)
    parser.add_argument("--expected-openssl", default="3.5.9")
    args = parser.parse_args()
    actual = backend.openssl_version_text()
    if cryptography.__version__ != args.expected_cryptography:
        raise SystemExit("cryptography_version_mismatch")
    if actual.split()[1] != args.expected_openssl:
        raise SystemExit("openssl_version_mismatch: " + actual)

    payload = b"disposable crypto rebuild verification"
    fernet = Fernet(Fernet.generate_key())
    if fernet.decrypt(fernet.encrypt(payload)) != payload:
        raise RuntimeError("fernet_round_trip_failed")
    aes = AESGCM(AESGCM.generate_key(bit_length=256))
    nonce = os.urandom(12)
    if aes.decrypt(nonce, aes.encrypt(nonce, payload, b"audit"), b"audit") != payload:
        raise RuntimeError("aes_gcm_round_trip_failed")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pss = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH)
    key.public_key().verify(key.sign(payload, pss, hashes.SHA256()), payload, pss, hashes.SHA256())
    private_pem = key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.BestAvailableEncryption(b"disposable-audit-only"))
    restored = serialization.load_pem_private_key(private_pem, b"disposable-audit-only")
    if restored.public_key().public_numbers() != key.public_key().public_numbers():
        raise RuntimeError("encrypted_pkcs8_round_trip_failed")

    ec_key = ec.generate_private_key(ec.SECP256R1())
    ec_key.public_key().verify(ec_key.sign(payload, ec.ECDSA(hashes.SHA256())),
                               payload, ec.ECDSA(hashes.SHA256()))
    ed_key = ed25519.Ed25519PrivateKey.generate()
    ed_key.public_key().verify(ed_key.sign(payload), payload)

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "disposable.invalid")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(minutes=1))
                   .not_valid_after(now + datetime.timedelta(minutes=5))
                   .sign(key, hashes.SHA256()))
    loaded = x509.load_pem_x509_certificate(certificate.public_bytes(serialization.Encoding.PEM))
    key.public_key().verify(loaded.signature, loaded.tbs_certificate_bytes,
                            padding.PKCS1v15(), loaded.signature_hash_algorithm)
    print(json.dumps({"cryptography": cryptography.__version__, "openssl": actual,
                      "checks": ["Fernet", "AES-GCM", "RSA-PSS", "encrypted-PKCS8",
                                 "ECDSA", "Ed25519", "X509-PEM"], "result": "passed"}))


if __name__ == "__main__":
    main()
