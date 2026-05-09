"""Fernet-Wrapper für API-Key-Encryption.

Master-Key kommt aus .env (32 Byte base64). Verschlüsselt API-Keys, bevor sie
in der SQLite-DB landen. Bei MASTER_KEY-Rotation müssen alle Configs neu
verschlüsselt werden (s. README).
"""

from cryptography.fernet import Fernet
from config import Config


def _cipher() -> Fernet:
    if not Config.MASTER_KEY:
        raise RuntimeError("MASTER_KEY nicht gesetzt")
    return Fernet(Config.MASTER_KEY.encode() if isinstance(Config.MASTER_KEY, str) else Config.MASTER_KEY)


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode('utf-8')).decode('utf-8')


def decrypt(ciphertext: str) -> str:
    return _cipher().decrypt(ciphertext.encode('utf-8')).decode('utf-8')
