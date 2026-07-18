"""Password hashing and OAuth state signing (ARCHITECTURE §6.1, §11)."""

import bcrypt
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.core.config import get_settings

OAUTH_STATE_MAX_AGE_SECONDS = 600


class InvalidOAuthState(Exception):
    """State failed signature verification or expired (CSRF / stale link)."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _oauth_state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="meli-oauth")


def sign_oauth_state(data: dict) -> str:
    return _oauth_state_serializer().dumps(data)


def verify_oauth_state(value: str, max_age: int = OAUTH_STATE_MAX_AGE_SECONDS) -> dict:
    try:
        return _oauth_state_serializer().loads(value, max_age=max_age)
    except BadSignature as exc:  # SignatureExpired subclasses BadSignature
        raise InvalidOAuthState("OAuth state invalid or expired") from exc
