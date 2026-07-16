"""Operational CLI. Run with `python -m app.cli <command>` from backend/."""

import argparse
import asyncio

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.repositories.tenant import TenantRepository
from app.repositories.user import UserRepository


async def seed_admin(tenant_slug: str, email: str, password: str) -> None:
    async with SessionLocal() as session:
        tenant_repo = TenantRepository(session)
        tenant = await tenant_repo.get_by_slug(tenant_slug)
        if tenant is None:
            tenant = await tenant_repo.create(name=tenant_slug, slug=tenant_slug)

        user_repo = UserRepository(session)
        if await user_repo.get_by_email(tenant.id, email) is not None:
            raise SystemExit(f"User {email} already exists for tenant '{tenant_slug}'")

        await user_repo.create(
            tenant.id, email=email, password_hash=hash_password(password), role="admin"
        )
        await session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed_parser = subparsers.add_parser("seed-admin", help="Create a tenant + its first admin")
    seed_parser.add_argument("--tenant-slug", required=True)
    seed_parser.add_argument("--email", required=True)
    seed_parser.add_argument("--password", required=True)

    args = parser.parse_args()
    if args.command == "seed-admin":
        asyncio.run(seed_admin(args.tenant_slug, args.email, args.password))


if __name__ == "__main__":
    main()
