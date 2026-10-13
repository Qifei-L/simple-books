from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
import csv
import io
import os
import time
from collections import defaultdict, deque
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, EmailStr
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from python_accounting.models import Account, Transaction, ReportingPeriod
from . import accounting as books
from .auth import (fastapi_users, auth_backend, User, UserRead, UserCreate, UserUpdate,
                   UserDB, Manager, current_user, writer, admin, Role)
from .db import engine, users, tokens, operations, audit

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Amount = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Text = Annotated[str, Field(min_length=1, max_length=500)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

class Entry(Input):
    account_id: int
    debit: Amount = Decimal(0)
    credit: Amount = Decimal(0)
    description: str = Field(default="", max_length=500)

class Journal(Input):
    date: date
    description: Text
    reference: str = Field(default="", max_length=200)
    lines: list[Entry] = Field(min_length=2, max_length=50)

class DocLine(Input):
    account_id: int
    description: Text
    amount: Money
    quantity: Annotated[Decimal, Field(gt=0, max_digits=8, decimal_places=2)] = Decimal(1)

class Document(Input):
    kind: Literal["invoice", "bill", "receipt", "payment"]
    date: date
    due_date: date | None = None
    description: Text
    reference: str = Field(default="", max_length=200)
    account_id: int
    lines: list[DocLine] = Field(min_length=1, max_length=50)

class Assignment(Input):
    payment_id: int
    document_id: int
    amount: Money
    date: date

class Suspense(Input):
    bank_id: int
    amount: Money
    date: date
    description: Text
    reference: str = Field(default="", max_length=200)
    direction: Literal["in", "out"] = "in"

class Resolve(Input):
    account_id: int
    date: date
    description: Text
    document_id: int | None = None
    clear_amount: Money | None = None

class BankTransaction(Suspense):
    account_id: int | None = None
    document_id: int | None = None
    clear_amount: Money | None = None

class Reversal(Input):
    date: date
    description: Text

class NewAccount(Input):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    account_type: Literal["BANK", "EQUITY", "RECEIVABLE", "PAYABLE", "OPERATING_REVENUE",
        "OPERATING_EXPENSE", "NON_CURRENT_ASSET", "CURRENT_ASSET", "CURRENT_LIABILITY",
        "NON_CURRENT_LIABILITY", "DIRECT_EXPENSE", "OVERHEAD_EXPENSE"]

class NewUser(Input):
    email: EmailStr
    password: Annotated[str, Field(min_length=12, max_length=128)]
    role: Role

class RoleChange(Input):
    role: Role
    is_active: bool

@asynccontextmanager
async def lifespan(app):
    books.initialize(os.getenv("COMPANY_NAME", "Demo Company"))
    email, password = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD")
    db = UserDB()
    if email and password and not await db.get_by_email(email):
        await Manager(db).create(UserCreate(email=email, password=password, is_superuser=True), safe=False)
        u = await db.get_by_email(email)
        await db.update(u, {"role": "Admin", "is_verified": True})
    yield

app = FastAPI(title="Simple Books", version="0.1.0", lifespan=lifespan)
attempts = defaultdict(deque)

@app.middleware("http")
async def security(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-Requested-With") != "SimpleBooks":
            return JSONResponse({"detail": "Missing request verification header."}, 403)
    if request.url.path == "/api/auth/login" and request.method == "POST":
        key = request.client.host
        now = time.monotonic()
        q = attempts[key]
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= 20:
            return JSONResponse({"detail": "Too many sign-in attempts. Try again in one minute."}, 429)
        q.append(now)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return JSONResponse({"detail": "The record already exists or the request conflicts. Refresh and try again."}, 409)

@app.exception_handler(Exception)
async def library_error(request, exc):
    if exc.__class__.__module__.startswith("python_accounting"):
        return JSONResponse({"detail": str(exc) or exc.__class__.__name__}, 422)
    import logging
    logging.getLogger(__name__).exception("Request failed")
    return JSONResponse({"detail": "The server could not process the request. Check the server logs."}, 500)

app.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/api/auth", tags=["Auth"])
# No public registration or self-service role fields.
@app.get("/api/users/me", response_model=UserRead)
def me(user: User = Depends(current_user)):
    return user

class PasswordChange(Input):
    current_password: str
    password: Annotated[str, Field(min_length=12, max_length=128)]

@app.post("/api/users/password")
async def change_password(data: PasswordChange, user: User = Depends(current_user)):
    manager = Manager(UserDB())
    verified, _ = manager.password_helper.verify_and_update(data.current_password, user.hashed_password)
    if not verified:
        raise HTTPException(400, "The current password is incorrect.")
    await manager.update(UserUpdate(password=data.password), user, safe=True)
    with engine.begin() as c:
        c.execute(delete(tokens).where(tokens.c.user_id == user.id))
    return {"success": True}

@app.get("/health")
def health():
    with engine.connect() as c:
        c.exec_driver_sql("SELECT 1")
    return {"status": "ok"}

@app.get("/api/company")
def company(user: User = Depends(current_user)):
    with books.session() as s:
        return {"id": s.entity.id, "name": s.entity.name, "currency": "USD"}

@app.get("/api/account-types")
def types(user: User = Depends(current_user)):
    return [{"id": a.name, "name": a.value} for a in Account.AccountType]

@app.get("/api/accounts")
def accounts(user: User = Depends(current_user)):
    with books.session() as s:
        return [books.account_json(s, a) for a in s.scalars(select(Account).order_by(Account.account_code)).all()]

@app.post("/api/accounts")
def add_account(data: NewAccount, user: User = Depends(writer)):
    with books.session(write=True) as s:
        if s.scalars(select(Account).where(Account.name == data.name.title())).first():
            raise HTTPException(409, "An account with this name already exists.")
        a = Account(name=data.name, account_type=Account.AccountType[data.account_type],
                    currency_id=s.entity.currency_id, entity_id=s.entity.id)
        s.add(a)
        s.flush()
        books.audit_event(s.connection(), user.id, "create_account", {"account_id": a.id})
        return books.account_json(s, a)

@app.get("/api/transactions")
def transactions(start: date | None = None, end: date | None = None,
                 user: User = Depends(current_user)):
    with books.session() as s:
        q = select(Transaction).order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        if start:
            q = q.where(Transaction.transaction_date >= datetime.combine(start, datetime.min.time()))
        if end:
            q = q.where(Transaction.transaction_date <= datetime.combine(end, datetime.max.time()))
        return [books.transaction_json(s, t) for t in s.scalars(q).all() if t.is_posted]

@app.get("/api/accounts/{id}/statement")
def statement(id: int, start: date, end: date, user: User = Depends(current_user)):
    if start > end or start.year != end.year:
        raise HTTPException(422, "Select a date range within one calendar year.")
    with books.session() as s:
        a = books.get_account(s, id)
        result = a.statement(s, datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time()))
        return {"opening": str(result["opening_balance"]), "closing": str(result["closing_balance"]),
                "transactions": [books.transaction_json(s, t) for t in result["transactions"]]}

@app.post("/api/journals")
def journal(data: Journal, key: Key, user: User = Depends(writer)):
    return books.create_journal(data.model_dump(), key, user.id)

@app.post("/api/journals/{id}/reverse")
def reverse(id: int, data: Reversal, key: Key, user: User = Depends(writer)):
    return books.reverse_journal(id, data.model_dump(), key, user.id)

@app.post("/api/documents")
def document(data: Document, key: Key, user: User = Depends(writer)):
    if data.due_date and data.due_date < data.date:
        raise HTTPException(422, "The due date cannot precede the document date.")
    return books.create_document(data.model_dump(), key, user.id)

@app.post("/api/assignments")
def assign(data: Assignment, key: Key, user: User = Depends(writer)):
    return books.assign(data.model_dump(), key, user.id)

@app.post("/api/suspense")
def suspense(data: Suspense, key: Key, user: User = Depends(writer)):
    return books.suspense(data.model_dump(), key, user.id)

@app.post("/api/bank-transactions")
def bank_transaction(data: BankTransaction, key: Key, user: User = Depends(writer)):
    return books.bank_transaction(data.model_dump(), key, user.id)

@app.post("/api/suspense/{id}/resolve")
def resolve(id: int, data: Resolve, key: Key, user: User = Depends(writer)):
    return books.resolve_suspense(id, data.model_dump(), key, user.id)

@app.get("/api/reports/{name}")
def report(name: Literal["income", "balance", "trial", "cashflow"], start: date, end: date,
           user: User = Depends(current_user)):
    return books.report(name, start, end)

def csv_response(rows, fields, filename):
    buffer = io.StringIO()
    w = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for row in rows:
        # Prevent spreadsheet formula execution in user-controlled text fields.
        w.writerow({k: ("'" + str(v) if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
                    for k, v in row.items() if k in fields})
    return Response("\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@app.get("/api/export/transactions")
def export_transactions(start: date, end: date, user: User = Depends(current_user)):
    return csv_response(transactions(start, end, user), ["id", "date", "number", "kind", "description", "account", "amount"], "transactions.csv")

@app.get("/api/export/reports/{name}")
def export_report(name: Literal["income", "balance", "trial", "cashflow"], start: date, end: date,
                  user: User = Depends(current_user)):
    return csv_response(books.report(name, start, end)["rows"],
                        ["section", "category", "account", "account_code", "amount"] +
                        (["debit", "credit"] if name == "trial" else []), f"{name}.csv")

@app.get("/api/admin/users")
def list_users(user: User = Depends(admin)):
    with engine.connect() as c:
        return [dict(row) for row in c.execute(select(users.c.id, users.c.email, users.c.role, users.c.is_active)).mappings()]

@app.post("/api/admin/users")
async def create_user(data: NewUser, user: User = Depends(admin)):
    db = UserDB()
    if await db.get_by_email(str(data.email)):
        raise HTTPException(409, "This email address already exists.")
    u = await Manager(db).create(UserCreate(email=data.email, password=data.password), safe=True)
    u = await db.update(u, {"role": data.role})
    with engine.begin() as c:
        books.audit_event(c, user.id, "create_user", {"user_id": u.id, "role": u.role})
    return UserRead.model_validate(u, from_attributes=True)

@app.patch("/api/admin/users/{id}")
def change_role(id: int, data: RoleChange, user: User = Depends(admin)):
    if id == user.id:
        raise HTTPException(422, "Ask another administrator to change your role or status.")
    with engine.begin() as c:
        if not c.execute(select(users.c.id).where(users.c.id == id)).first():
            raise HTTPException(404, "User not found.")
        c.execute(update(users).where(users.c.id == id).values(**data.model_dump()))
        c.execute(delete(tokens).where(tokens.c.user_id == id))
        books.audit_event(c, user.id, "change_role", {"user_id": id, **data.model_dump()})
    return {"id": id, **data.model_dump()}

@app.get("/api/admin/audit")
def audit_log(user: User = Depends(admin)):
    with engine.connect() as c:
        return [dict(row) for row in c.execute(select(audit).order_by(audit.c.id.desc()).limit(500)).mappings()]

# All API routes are declared before the SPA fallback.
dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(dist / "index.html")
