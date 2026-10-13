from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal
import os
from fastapi import Depends, HTTPException
from fastapi_users import FastAPIUsers, BaseUserManager, IntegerIDMixin, schemas
from fastapi_users.db import BaseUserDatabase
from fastapi_users.authentication import AuthenticationBackend, CookieTransport
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from sqlalchemy import select, insert, update, delete, func
from starlette.concurrency import run_in_threadpool
from .db import engine, users, tokens, audit

Role = Literal["Admin", "Accountant", "Viewer"]

@dataclass
class User:
    id: int
    email: str
    hashed_password: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    role: str

class UserRead(schemas.BaseUser[int]):
    role: Role

class UserCreate(schemas.BaseUserCreate):
    pass

class UserUpdate(schemas.BaseUserUpdate):
    pass

# Core-table adapters intentionally avoid ORM Session listeners installed by
# Python Accounting. Authentication and ledger models share a database, not sessions.
class UserDB(BaseUserDatabase[User, int]):
    async def get(self, id):
        return await self._one(users.c.id == id)

    async def get_by_email(self, email):
        return await self._one(func.lower(users.c.email) == email.lower())

    async def _one(self, where):
        def read():
            with engine.connect() as c:
                row = c.execute(select(users).where(where)).mappings().first()
                return User(**row) if row else None
        return await run_in_threadpool(read)

    async def create(self, values):
        def write():
            with engine.begin() as c:
                return c.execute(insert(users).values(**values)).inserted_primary_key[0]
        return await self.get(await run_in_threadpool(write))

    async def update(self, user, values):
        def write():
            with engine.begin() as c:
                c.execute(update(users).where(users.c.id == user.id).values(**values))
        await run_in_threadpool(write)
        return await self.get(user.id)

    async def delete(self, user):
        def write():
            with engine.begin() as c:
                c.execute(delete(tokens).where(tokens.c.user_id == user.id))
                c.execute(delete(users).where(users.c.id == user.id))
        await run_in_threadpool(write)

    async def get_by_oauth_account(self, *args):
        return None

@dataclass
class Token:
    token: str
    user_id: int
    created_at: datetime

class TokenDB:
    async def get_by_token(self, token, max_age=None):
        def read():
            with engine.connect() as c:
                q = select(tokens).where(tokens.c.token == sha256(token.encode()).hexdigest())
                if max_age:
                    q = q.where(tokens.c.created_at >= max_age)
                row = c.execute(q).mappings().first()
                return Token(token=token, user_id=row["user_id"], created_at=row["created_at"]) if row else None
        return await run_in_threadpool(read)

    async def create(self, values):
        token = Token(**values, created_at=datetime.now(timezone.utc))
        def write():
            with engine.begin() as c:
                c.execute(insert(tokens).values(token=sha256(token.token.encode()).hexdigest(),
                    user_id=token.user_id, created_at=token.created_at))
        await run_in_threadpool(write)
        return token

    async def delete(self, token):
        def write():
            with engine.begin() as c:
                c.execute(delete(tokens).where(tokens.c.token == sha256(token.token.encode()).hexdigest()))
        await run_in_threadpool(write)

class Manager(IntegerIDMixin, BaseUserManager[User, int]):
    async def validate_password(self, password, user):
        if len(password) < 12:
            from fastapi_users.exceptions import InvalidPasswordException
            raise InvalidPasswordException(reason="The password must contain at least 12 characters.")

async def get_manager():
    yield Manager(UserDB())

def get_strategy():
    return DatabaseStrategy(TokenDB(), lifetime_seconds=3600 * 12)

transport = CookieTransport(cookie_name="books_session", cookie_max_age=3600 * 12,
    cookie_secure=os.getenv("COOKIE_SECURE", "true").lower() == "true", cookie_samesite="strict")
auth_backend = AuthenticationBackend(name="cookie", transport=transport, get_strategy=get_strategy)
fastapi_users = FastAPIUsers[User, int](get_manager, [auth_backend])
current_user = fastapi_users.current_user(active=True)

def writer(user: User = Depends(current_user)):
    if user.role not in ("Admin", "Accountant"):
        raise HTTPException(403, "Read-only users cannot modify accounting records.")
    return user

def admin(user: User = Depends(current_user)):
    if user.role != "Admin":
        raise HTTPException(403, "Administrator role required.")
    return user
