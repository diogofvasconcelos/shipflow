"""Fernet encryption for ML tokens at rest (see docs/ARCHITECTURE.md §6.1, §11).
Used exclusively by app/repositories/meli_account.py (task T2) — never call this
outside that boundary; tokens must never be decrypted anywhere else.
"""

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().token_encryption_key.encode())


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
