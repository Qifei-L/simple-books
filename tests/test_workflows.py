import os
from datetime import date
from decimal import Decimal
from pathlib import Path
import uuid
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-books.db")
os.environ["COOKIE_SECURE"] = "false"
os.environ["ADMIN_EMAIL"] = "admin@example.com"
os.environ["ADMIN_PASSWORD"] = "TestPassword-123456"

from fastapi.testclient import TestClient
from backend.main import app
from backend.db import engine, metadata
from python_accounting.models import Base
import pytest

@pytest.fixture
def client():
    if engine.dialect.name == "postgresql":
        if engine.url.database != "books_test":
            raise RuntimeError("Tests only reset the explicitly named books_test database")
        with engine.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE")
            c.exec_driver_sql("CREATE SCHEMA public")
    else:
        if engine.url.database != "./test-books.db":
            raise RuntimeError("Tests only reset ./test-books.db")
        metadata.drop_all(engine)
        Base.metadata.drop_all(engine)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.post("/api/auth/login", data={"username": "admin@example.com", "password": "TestPassword-123456"},
            headers={"X-Requested-With": "SimpleBooks"}).status_code == 204
        yield c

def post(c, path, data, key=None):
    return c.post(path, json=data, headers={"X-Requested-With": "SimpleBooks", "Idempotency-Key": key or str(uuid.uuid4())})

def account_ids(c):
    return {a["name"]: a["id"] for a in c.get("/api/accounts").json()}

DAY = date.today().isoformat()
START = date.today().replace(month=1, day=1).isoformat()
END = date.today().replace(month=12, day=31).isoformat()

def report(c, name):
    r = c.get(f"/api/reports/{name}", params={"start": START, "end": END})
    assert r.status_code == 200, r.text
    return r.json()

def test_uncollected_invoice_reconciles_all_reports_and_export(client):
    c, a = client, account_ids(client)
    r = post(c, "/api/documents", dict(kind="invoice", date=DAY, description="Invoice 150",
        account_id=a["Demo Customer"], lines=[dict(description="Services", account_id=a["Service Revenue"], amount="150")]))
    assert r.status_code == 200, r.text
    income, balance, trial, cash = [report(c, n) for n in ("income", "balance", "trial", "cashflow")]
    assert Decimal(income["results"]["NET_PROFIT"]) == 150
    equity = [r for r in balance["rows"] if r["section"] == "Equity"]
    assert len(equity) == 1 and Decimal(equity[0]["amount"]) == 150
    assert Decimal(balance["results"]["TOTAL_ASSETS"]) == Decimal(balance["results"]["TOTAL_EQUITY"]) == 150
    assert Decimal(trial["results"]["DEBIT"]) == Decimal(trial["results"]["CREDIT"]) == 150
    assert sum(Decimal(r["debit"]) for r in trial["rows"]) == 150
    adjustments = {r["account"]: Decimal(r["amount"]) for r in cash["rows"]}
    assert adjustments["Net Profit"] == 150 and adjustments["Receivables"] == -150
    assert Decimal(cash["results"]["NET_CASH_FLOW"]) == Decimal(cash["results"]["END_CASH_BALANCE"]) == 0
    for r in (balance, trial, cash):
        assert Decimal(r["reconciliation_difference"]) == 0
    for name, label in (("balance", "Current year earnings (unclosed)"), ("cashflow", "Receivables"), ("trial", "debit,credit")):
        exported = c.get(f"/api/export/reports/{name}", params={"start": START, "end": END})
        assert exported.status_code == 200 and label in exported.text
    # A deliberate closing journal moves earnings to capital; the report must
    # not add the original profit a second time.
    closed = post(c, "/api/journals", dict(date=DAY, description="Close income",
        lines=[dict(account_id=a["Service Revenue"], debit="150"),
               dict(account_id=a["Owner Capital"], credit="150")]))
    assert closed.status_code == 200, closed.text
    after = report(c, "balance")
    assert Decimal(next(r["amount"] for r in after["rows"] if r["account"] == "Current year earnings (unclosed)")) == 0
    assert Decimal(after["results"]["TOTAL_EQUITY"]) == 150
    assert Decimal(after["reconciliation_difference"]) == 0

def test_report_normal_signs_same_type_offsets_and_period_boundary(client):
    c, a = client, account_ids(client)
    year = date.today().year
    first, boundary = f"{year}-02-01", f"{year}-03-01"
    def journal(day, debit, credit, amount):
        r = post(c, "/api/journals", dict(date=day, description="Report regression",
            lines=[dict(account_id=debit, debit=amount), dict(account_id=credit, credit=amount)]))
        assert r.status_code == 200, r.text
    journal(first, a["Bank"], a["Service Revenue"], "200")
    journal(boundary, a["Office Expenses"], a["Bank"], "30")
    journal(boundary, a["Bank"], a["Owner Capital"], "100")
    r = post(c, "/api/accounts", dict(name="Overdraft", account_type="BANK"))
    assert r.status_code == 200, r.text
    journal(boundary, a["Bank"], r.json()["id"], "270")
    trial = report(c, "trial")
    # Bank +540 and Overdraft -270 must not be netted into one type balance.
    assert Decimal(trial["results"]["DEBIT"]) == Decimal(trial["results"]["CREDIT"]) == 570
    full_income = report(c, "income")
    assert Decimal(full_income["results"]["TOTAL_REVENUE"]) == 200
    assert Decimal(full_income["results"]["TOTAL_EXPENSES"]) == 30
    def period(name):
        r = c.get(f"/api/reports/{name}", params={"start": boundary, "end": boundary})
        assert r.status_code == 200, r.text
        return r.json()
    assert Decimal(period("income")["results"]["NET_PROFIT"]) == -30
    balance = period("balance")
    assert Decimal(next(r["amount"] for r in balance["rows"] if r["account"] == "Current year earnings (unclosed)")) == 170
    assert Decimal(balance["results"]["TOTAL_EQUITY"]) == 270
    cash = period("cashflow")
    assert Decimal(cash["results"]["BEGIN_CASH_BALANCE"]) == 200
    assert Decimal(cash["results"]["NET_CASH_FLOW"]) == 70
    assert Decimal(cash["results"]["END_CASH_BALANCE"]) == Decimal(cash["results"]["CASHBOOK_BALANCE"]) == 270
    for r in (balance, trial, cash):
        assert Decimal(r["reconciliation_difference"]) == 0

def test_suspense_idempotency_and_reclassification(client):
    c = client
    a = account_ids(c)
    payload = dict(bank_id=a["Bank"], amount="1000.00", date=DAY, description="Unknown deposit")
    key = str(uuid.uuid4())
    r = post(c, "/api/suspense", payload, key)
    assert r.status_code == 200, r.text
    assert post(c, "/api/suspense", payload, key).json()["id"] == r.json()["id"]
    assert post(c, "/api/suspense", {**payload, "amount": "999"}, key).status_code == 409
    assert len(c.get("/api/transactions").json()) == 1
    assert float(report(c, "income")["results"]["NET_PROFIT"]) == 0
    result = post(c, f'/api/suspense/{r.json()["id"]}/resolve',
        dict(account_id=a["Service Revenue"], date=DAY, description="Consulting payment"))
    assert result.status_code == 200, result.text
    assert float(report(c, "income")["results"]["NET_PROFIT"]) == 1000
    assert post(c, f'/api/suspense/{r.json()["id"]}/resolve',
        dict(account_id=a["Service Revenue"], date=DAY, description="duplicate")).status_code == 409
    balances = {x["name"]: float(x["balance"]) for x in c.get("/api/accounts").json()}
    assert balances["Bank"] == 1000
    assert balances["Suspense"] == 0
    for name in ("balance", "trial", "cashflow"):
        report(c, name)

def test_invoice_partial_payment_overclearance_and_rollback(client):
    c = client
    a = account_ids(c)
    def doc(kind, amount, counterpart):
        r = post(c, "/api/documents", dict(kind=kind, date=DAY, description=kind,
            account_id=a["Demo Customer"], lines=[dict(description="Consulting", account_id=a[counterpart], amount=amount)]))
        assert r.status_code == 200, r.text
        return r.json()["id"]
    invoice = doc("invoice", "1000", "Service Revenue")
    receipt = doc("receipt", "600", "Bank")
    payload = dict(payment_id=receipt, document_id=invoice, date=DAY, amount="600")
    key = str(uuid.uuid4())
    assert post(c, "/api/assignments", payload, key).status_code == 200
    assert post(c, "/api/assignments", payload, key).status_code == 200
    r = post(c, "/api/assignments", {**payload, "amount": "1"})
    assert r.status_code == 422, r.text
    tx = {t["id"]: t for t in c.get("/api/transactions").json()}
    assert float(tx[invoice]["remaining"]) == 400
    assert float(report(c, "income")["results"]["NET_PROFIT"]) == 1000
    count = len(tx)
    r = post(c, "/api/documents", dict(kind="invoice", date=DAY, description="invalid",
        account_id=a["Demo Customer"], lines=[dict(description="bad", account_id=a["Office Expenses"], amount="25")]))
    assert r.status_code == 422, r.text
    assert len(c.get("/api/transactions").json()) == count

def test_permissions_csrf_logout_and_password(client):
    c = client
    assert c.post("/api/accounts", json={"name": "Blocked", "account_type": "BANK"}).status_code == 403
    r = post(c, "/api/admin/users", dict(email="viewer@example.com", password="ViewerPassword-123", role="Viewer"))
    assert r.status_code == 200, r.text
    assert post(c, "/api/auth/logout", {}).status_code == 204
    assert c.get("/api/accounts").status_code == 401
    assert c.post("/api/auth/login", data={"username": "viewer@example.com", "password": "ViewerPassword-123"},
        headers={"X-Requested-With": "SimpleBooks"}).status_code == 204
    assert c.get("/api/accounts").status_code == 200
    assert post(c, "/api/accounts", dict(name="Blocked", account_type="BANK")).status_code == 403
    assert c.get("/api/admin/users").status_code == 403
    assert c.patch("/api/users/me", json={"role": "Admin"}, headers={"X-Requested-With": "SimpleBooks"}).status_code == 405

def test_unbalanced_journal_is_atomic(client):
    c = client
    a = account_ids(c)
    data = dict(date=DAY, description="Unbalanced", lines=[
        dict(account_id=a["Bank"], debit="1000"), dict(account_id=a["Owner Capital"], credit="900")])
    assert post(c, "/api/journals", data).status_code == 422
    assert c.get("/api/transactions").json() == []
    data["lines"][1]["credit"] = "1000"
    assert post(c, "/api/journals", data).status_code == 200
    r = report(c, "trial")["results"]
    assert float(r["DEBIT"]) == float(r["CREDIT"]) == 1000
    t = c.get("/api/transactions").json()[0]
    key = str(uuid.uuid4())
    payload = {"date": DAY, "description": "Reverse incorrect entry"}
    r = post(c, f'/api/journals/{t["id"]}/reverse', payload, key)
    assert r.status_code == 200, r.text
    assert post(c, f'/api/journals/{t["id"]}/reverse', payload, key).status_code == 200
    assert post(c, f'/api/journals/{t["id"]}/reverse', payload).status_code == 409
    assert all(float(a["balance"]) == 0 for a in c.get("/api/accounts").json())

def test_logout_revokes_copied_cookie(client):
    c = client
    cookie = c.cookies.get("books_session")
    assert post(c, "/api/auth/logout", {}).status_code == 204
    c.cookies.set("books_session", cookie)
    assert c.get("/api/accounts").status_code == 401

def test_role_change_revokes_session(client):
    c = client
    r = post(c, "/api/admin/users", dict(email="accountant@example.com", password="AccountantPassword-123", role="Accountant"))
    assert r.status_code == 200
    id = r.json()["id"]
    with TestClient(app) as accountant:
        assert accountant.post("/api/auth/login", data={"username": "accountant@example.com", "password": "AccountantPassword-123"},
            headers={"X-Requested-With": "SimpleBooks"}).status_code == 204
        assert accountant.get("/api/accounts").status_code == 200
        r = c.patch(f"/api/admin/users/{id}", json={"role": "Viewer", "is_active": True}, headers={"X-Requested-With": "SimpleBooks"})
        assert r.status_code == 200
        assert accountant.get("/api/accounts").status_code == 401

def test_supplier_bill_and_partial_payment(client):
    c = client
    a = account_ids(c)
    def doc(kind, amount, counterpart):
        r = post(c, "/api/documents", dict(kind=kind, date=DAY, description=kind,
            account_id=a["Demo Supplier"], lines=[dict(description="Office cost", account_id=a[counterpart], amount=amount)]))
        assert r.status_code == 200, r.text
        return r.json()["id"]
    bill = doc("bill", "300", "Office Expenses")
    payment = doc("payment", "100", "Bank")
    r = post(c, "/api/assignments", dict(payment_id=payment, document_id=bill, date=DAY, amount="100"))
    assert r.status_code == 200, r.text
    tx = {t["id"]: t for t in c.get("/api/transactions").json()}
    assert float(tx[bill]["remaining"]) == 200
    assert float(report(c, "income")["results"]["NET_PROFIT"]) == -300

def test_concurrent_duplicate_posts_postgres(client):
    if engine.dialect.name != "postgresql":
        pytest.skip("Production row-lock behavior is verified against PostgreSQL in CI")
    from concurrent.futures import ThreadPoolExecutor
    a = account_ids(client)
    key = str(uuid.uuid4())
    data = dict(bank_id=a["Bank"], amount="1000", date=DAY, description="Concurrent deposit")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: post(client, "/api/suspense", data, key), range(4)))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert len({r.json()["id"] for r in results}) == 1
    assert len(client.get("/api/transactions").json()) == 1

def test_failure_after_ledger_post_rolls_back_everything(client, monkeypatch):
    from backend import accounting
    from sqlalchemy import select, func
    from backend.db import operations, audit
    from python_accounting.models import Ledger
    c = client
    a = account_ids(c)
    def fail(*args):
        raise RuntimeError("Injected failure after library ledger posting")
    monkeypatch.setattr(accounting, "finish", fail)
    r = post(c, "/api/suspense", dict(bank_id=a["Bank"], amount="1000", date=DAY, description="Atomic test"))
    assert r.status_code == 500
    assert c.get("/api/transactions").json() == []
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(operations)).scalar() == 0
        assert conn.execute(select(func.count()).select_from(audit)).scalar() == 0
        assert conn.execute(select(func.count()).select_from(Ledger.__table__)).scalar() == 0
