from types import SimpleNamespace

from cryptography.fernet import Fernet

from app.core import crypto
from app.core.security import hash_password, verify_password


def test_password_hash_round_trip():
    hashed = hash_password("s3cret!")
    assert hashed != "s3cret!"
    assert verify_password("s3cret!", hashed)
    assert not verify_password("wrong", hashed)


def test_token_encryption_round_trip(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto, "get_settings", lambda: SimpleNamespace(token_encryption_key=key))

    ciphertext = crypto.encrypt_token("APP_USR-token-123")
    assert ciphertext != "APP_USR-token-123"
    assert crypto.decrypt_token(ciphertext) == "APP_USR-token-123"
