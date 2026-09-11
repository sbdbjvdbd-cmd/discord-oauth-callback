"""
Einfache symmetrische Verschlüsselung für Tokens (Fernet/AES).
SECRET_KEY aus Environment wird als Schlüssel genutzt.
"""
import os
import base64
import hashlib
from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    secret = os.getenv("SECRET_KEY", "fallback-insecure-key-change-me")
    key = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception:
        return ""
