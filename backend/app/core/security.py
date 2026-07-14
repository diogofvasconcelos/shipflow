"""Password hashing. Full session-auth wiring (login/logout, require_user,
require_admin) is task T1 in docs/ORCHESTRATION.md.
"""

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())
