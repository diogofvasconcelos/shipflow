"""Fernet encryption for ML tokens at rest (see docs/ARCHITECTURE.md §6.1, §11).
Exactly two legitimate call sites: app/repositories/meli_account.py (encrypt on
write, decrypt for the client) and app/integrations/meli/client.py (decrypt for
auth headers — CLAUDE.md: "decrypted only inside the meli client"). Never call
this anywhere else; tokens must never be decrypted outside those boundaries.
"""

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().token_encryption_key.encode())


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
