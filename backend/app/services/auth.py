from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models.user import User
from app.repositories.user import find_user_for_login


class AuthError(Exception):
    pass


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def authenticate(self, email: str, password: str) -> User:
        user = await find_user_for_login(self.session, email)
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise AuthError("E-mail ou senha inválidos")
        return user
