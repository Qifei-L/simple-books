from datetime import datetime, date, time
from decimal import Decimal
import hashlib
import json
from contextlib import contextmanager
from fastapi import HTTPException
from sqlalchemy import select, insert, update
from python_accounting.models import Base, Entity, Currency, Account, LineItem, Transaction, Assignment
from python_accounting.database.session import get_session, AccountingSession
from python_accounting.transactions import (JournalEntry, ClientInvoice, SupplierBill, ClientReceipt, SupplierPayment)
from python_accounting.config import config
from .db import engine, metadata, operations, audit

class AtomicSession(AccountingSession):
    """Defer library-internal commits to the API unit-of-work boundary."""
    def commit(self):
        if self.info.get("atomic"):
            self.flush()
        else:
            super().commit()

def initialize(company="Demo Company"):
    Base.metadata.create_all(engine)
    metadata.create_all(engine)
    with get_session(engine) as s:
        entity = s.scalars(select(Entity)).first()
        if entity:
            return
        entity = Entity(name=company)
        s.add(entity)
        s.commit()
        currency = Currency(name="US Dollar", code="USD", entity_id=entity.id)
        s.add(currency)
        s.commit()
        entity.currency_id = currency.id
        s.add(entity)
        s.commit()
        for name, kind in [("Bank", "BANK"), ("Suspense", "EQUITY"),
                           ("Owner Capital", "EQUITY"), ("Service Revenue", "OPERATING_REVENUE"),
                           ("Office Expenses", "OPERATING_EXPENSE"),
                           ("Demo Customer", "RECEIVABLE"), ("Demo Supplier", "PAYABLE")]:
            s.add(Account(name=name, account_type=Account.AccountType[kind],
                          currency_id=currency.id, entity_id=entity.id))
            s.flush()
        s.commit()

@contextmanager
def session(write=False):
    with AtomicSession(bind=engine, info={"include_deleted": False, "ignore_isolation": False, "atomic": write}) as s:
        q = select(Entity).order_by(Entity.id)
        if write:
            # Serializes writes and serial-number/idempotency checks across workers.
            q = q.with_for_update()
        entity = s.scalars(q).first()
        if entity is None:
            raise HTTPException(503, "The company has not been initialized.")
        s.entity = entity
        try:
            yield s
            if write:
                s.info["atomic"] = False
                s.commit()
        except Exception:
            s.rollback()
            raise

def get_account(s, id):
    a = s.get(Account, id)
    if not a or a.entity_id != s.entity.id:
        raise HTTPException(404, "Account not found.")
    return a

def account_json(s, a):
    return dict(id=a.id, name=a.name, account_type=a.account_type.name,
                account_code=a.account_code, balance=str(a.closing_balance(s)),
                currency="USD")

def audit_event(c, user_id, action, detail):
    c.execute(insert(audit).values(actor_id=user_id, action=action,
        detail=json.dumps(detail, ensure_ascii=False, default=str), created_at=datetime.utcnow()))

def transaction_json(s, t):
    op = s.connection().execute(select(operations).where(operations.c.transaction_id == t.id)).mappings().first()
    reversal = s.connection().execute(select(operations.c.id).where(
        operations.c.related_id == t.id, operations.c.kind == "reversal")).first()
    return dict(id=t.id, number=t.transaction_no, date=t.transaction_date.date().isoformat(),
        description=t.narration, reference=t.reference, kind=op["kind"] if op else t.transaction_type.name,
        amount=str(t.amount), account_id=t.account_id, account=t.account.name,
        cleared=str(t.cleared(s)) if hasattr(t, "cleared") else None,
        remaining=str(t.amount - t.cleared(s)) if hasattr(t, "cleared") else None,
        due_date=op["due_date"] if op else None, resolved=op["resolved"] if op else False,
        reversed=bool(reversal),
        lines=[dict(account_id=l.account_id, account=l.account.name, description=l.narration,
            amount=str(l.amount), quantity=str(l.quantity), credited=l.credited) for l in t.line_items])

def reserve(s, key, payload, kind, user_id, due=None, related=None):
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    row = s.connection().execute(select(operations).where(operations.c.request_key == key)).mappings().first()
    if row:
        if row["payload_hash"] != digest:
            raise HTTPException(409, "An idempotency key cannot be reused for different content.")
        return row, True
    id = s.connection().execute(insert(operations).values(request_key=key, payload_hash=digest,
        kind=kind, actor_id=user_id, due_date=due, related_id=related,
        created_at=datetime.utcnow(), resolved=False)).inserted_primary_key[0]
    return {"id": id}, False

def finish(s, op, t, user_id):
    s.connection().execute(update(operations).where(operations.c.id == op["id"]).values(transaction_id=t.id))
    audit_event(s.connection(), user_id, "post", {"transaction_id": t.id, "number": t.transaction_no})
    return transaction_json(s, t)

def post_journal(s, data):
    entries = data["lines"]
    if sum(Decimal(str(x["debit"])) for x in entries) != sum(Decimal(str(x["credit"])) for x in entries):
        raise HTTPException(422, "Total debits and credits must be equal.")
    if len({x["account_id"] for x in entries}) != len(entries):
        raise HTTPException(422, "Each account may appear only once. Combine its amounts first.")
    main = entries[0]
    for x in entries:
        get_account(s, x["account_id"])
        if (Decimal(str(x["debit"])) > 0) == (Decimal(str(x["credit"])) > 0):
            raise HTTPException(422, "Enter either a debit or a credit amount on each line.")
    t = JournalEntry(narration=data["description"], reference=data["reference"],
        transaction_date=datetime.combine(data["date"], time(12)), account_id=main["account_id"],
        credited=Decimal(str(main["credit"])) > 0, compound=True,
        main_account_amount=Decimal(str(main["credit"])) or Decimal(str(main["debit"])),
        entity_id=s.entity.id)
    s.add(t)
    s.flush()
    for x in entries[1:]:
        l = LineItem(narration=x.get("description") or data["description"], account_id=x["account_id"],
            amount=Decimal(str(x["credit"])) or Decimal(str(x["debit"])),
            credited=Decimal(str(x["credit"])) > 0, entity_id=s.entity.id)
        s.add(l)
        s.flush()
        t.line_items.add(l)
    t.post(s)
    s.flush()
    return t

def create_journal(data, key, user_id):
    with session(write=True) as s:
        op, existing = reserve(s, key, data, "journal", user_id)
        if existing:
            return transaction_json(s, s.get(Transaction, op["transaction_id"]))
        return finish(s, op, post_journal(s, data), user_id)

def create_document(data, key, user_id):
    classes = {"invoice": ClientInvoice, "bill": SupplierBill,
               "receipt": ClientReceipt, "payment": SupplierPayment}
    with session(write=True) as s:
        if sum(x["amount"] * x["quantity"] for x in data["lines"]) > Decimal("999999999.99"):
            raise HTTPException(422, "The document amount exceeds the supported limit.")
        op, existing = reserve(s, key, data, data["kind"], user_id,
            data["due_date"].isoformat() if data.get("due_date") else None)
        if existing:
            return transaction_json(s, s.get(Transaction, op["transaction_id"]))
        get_account(s, data["account_id"])
        t = classes[data["kind"]](narration=data["description"], reference=data["reference"],
            transaction_date=datetime.combine(data["date"], time(12)),
            account_id=data["account_id"], entity_id=s.entity.id)
        s.add(t)
        s.flush()
        for x in data["lines"]:
            get_account(s, x["account_id"])
            l = LineItem(narration=x["description"], account_id=x["account_id"],
                amount=x["amount"], quantity=x["quantity"], entity_id=s.entity.id)
            s.add(l)
            s.flush()
            t.line_items.add(l)
        t.post(s)
        s.flush()
        return finish(s, op, t, user_id)

def reverse_journal(id, data, key, user_id):
    with session(write=True) as s:
        op, existing = reserve(s, key, {**data, "original_id": id}, "reversal", user_id, related=id)
        if existing:
            return transaction_json(s, s.get(Transaction, op["transaction_id"]))
        original_op = s.connection().execute(select(operations).where(
            operations.c.transaction_id == id, operations.c.kind == "journal")).first()
        if not original_op:
            raise HTTPException(422, "Only standard journal entries can be reversed in this version.")
        if s.connection().execute(select(operations.c.id).where(
            operations.c.related_id == id, operations.c.kind == "reversal", operations.c.id != op["id"])).first():
            raise HTTPException(409, "This journal entry has already been reversed.")
        original = s.get(Transaction, id)
        if data["date"] < original.transaction_date.date():
            raise HTTPException(422, "The reversal date cannot precede the original transaction.")
        lines = [dict(account_id=original.account_id,
            debit=original.main_account_amount if original.credited else 0,
            credit=0 if original.credited else original.main_account_amount)]
        for l in original.line_items:
            lines.append(dict(account_id=l.account_id, debit=l.amount * l.quantity if l.credited else 0,
                              credit=0 if l.credited else l.amount * l.quantity))
        t = post_journal(s, dict(date=data["date"], description=data["description"],
                                 reference=f"REVERSE-{id}", lines=lines))
        return finish(s, op, t, user_id)

def assign(data, key, user_id):
    with session(write=True) as s:
        op, existing = reserve(s, key, data, "assignment", user_id)
        if not existing:
            payment = s.get(Transaction, data["payment_id"])
            doc = s.get(Transaction, data["document_id"])
            if not payment or not doc:
                raise HTTPException(404, "Document not found.")
            if (payment.transaction_type.name, doc.transaction_type.name) not in (
                    ("CLIENT_RECEIPT", "CLIENT_INVOICE"), ("SUPPLIER_PAYMENT", "SUPPLIER_BILL")):
                raise HTTPException(422, "Allocate customer receipts to invoices or supplier payments to bills.")
            if data["date"] < max(payment.transaction_date.date(), doc.transaction_date.date()):
                raise HTTPException(422, "The allocation date cannot precede either document.")
            a = Assignment(assignment_date=datetime.combine(data["date"], time(18)),
                transaction_id=payment.id, assigned_id=doc.id, assigned_type=doc.__class__.__name__,
                amount=data["amount"], entity_id=s.entity.id)
            s.add(a)
            s.flush()
            audit_event(s.connection(), user_id, "assign", data)
        return {"id": op["id"], "success": True}

def suspense(data, key, user_id):
    with session(write=True) as s:
        op, existing = reserve(s, key, data, "suspense", user_id)
        if existing:
            return transaction_json(s, s.get(Transaction, op["transaction_id"]))
        bank = get_account(s, data["bank_id"])
        if bank.account_type.name != "BANK":
            raise HTTPException(422, "Select a bank account.")
        pending = s.scalars(select(Account).where(Account.name == "Suspense")).first()
        t = post_journal(s, dict(date=data["date"], description=data["description"], reference=data["reference"],
            lines=[dict(account_id=bank.id, debit=data["amount"], credit=0),
                   dict(account_id=pending.id, debit=0, credit=data["amount"])]))
        return finish(s, op, t, user_id)

def resolve_suspense(id, data, key, user_id):
    with session(write=True) as s:
        payload = {**data, "original_id": id}
        op, existing = reserve(s, key, payload, "reclassification", user_id, related=id)
        if existing:
            return transaction_json(s, s.get(Transaction, op["transaction_id"]))
        original = s.connection().execute(select(operations).where(
            operations.c.transaction_id == id, operations.c.kind == "suspense")).mappings().first()
        if not original:
            raise HTTPException(404, "Unclassified deposit not found.")
        if original["resolved"]:
            raise HTTPException(409, "This deposit has already been classified.")
        t0 = s.get(Transaction, id)
        target = get_account(s, data["account_id"])
        pending_id = next(l.account_id for l in t0.line_items if l.credited)
        if target.id == pending_id or target.id == t0.account_id:
            raise HTTPException(422, "Select the appropriate target account.")
        if data["date"] < t0.transaction_date.date():
            raise HTTPException(422, "The classification date cannot precede the original transaction.")
        t = post_journal(s, dict(date=data["date"], description=data["description"],
            reference=f"CLASSIFY-{id}", lines=[dict(account_id=pending_id, debit=t0.amount, credit=0),
                dict(account_id=target.id, debit=0, credit=t0.amount)]))
        s.connection().execute(update(operations).where(operations.c.id == original["id"]).values(resolved=True))
        return finish(s, op, t, user_id)

def report(name, start, end):
    if start > end or start.year != end.year:
        raise HTTPException(422, "Select a date range within one calendar year for this version.")
    with session() as s:
        d0, d1 = datetime.combine(start, time.min), datetime.combine(end, time.max)
        # Use engine account balances, not report printouts/type-netted totals.
        # In particular, keep opposite balances in the same account type separate.
        accounts = s.scalars(select(Account).where(Account.entity_id == s.entity.id)
                             .order_by(Account.account_code)).all()
        closing = {a.id: Decimal(a.closing_balance(s, d1)) for a in accounts}
        movement = {a.id: Decimal(a.balance_movement(s, d0, d1)) for a in accounts}
        income = config.reports["income_statement"]["sections"]
        income_types = {t for v in income.values() for t in v["account_types"]}
        earnings = -sum((closing[a.id] for a in accounts if a.account_type.name in income_types), Decimal(0))
        rows = []
        results, totals = {}, {}

        def add(section, label, value, code="", category="", debit=None, credit=None):
            row = dict(section=section, category=category, account=label,
                       account_code=code, amount=str(value))
            if debit is not None:
                row.update(debit=str(debit), credit=str(credit))
            rows.append(row)

        if name == "cashflow":
            cf = config.reports["cashflow_statement"]
            for key, section in cf["sections"].items():
                if key == "NET_CASH_FLOW":
                    continue
                total = Decimal(0)
                for subkey in section["sub_sections"]:
                    sub = cf["sub_sections"][subkey]
                    value = -sum((movement[a.id] for a in accounts
                                  if a.account_type.name in sub["account_types"]), Decimal(0))
                    if value:
                        add(section["label"], sub["label"], value)
                    total += value
                totals[section["label"]] = total
            banks = [a for a in accounts if a.account_type.name == "BANK"]
            cash = sum((closing[a.id] for a in banks), Decimal(0))
            cash_movement = sum((movement[a.id] for a in banks), Decimal(0))
            # Subtract the interval movement so start-day entries are not also
            # counted in opening cash (the engine's section helper is inclusive).
            opening = cash - cash_movement
            net = sum(totals.values(), Decimal(0))
            results = dict(BEGIN_CASH_BALANCE=opening, NET_CASH_FLOW=net,
                           END_CASH_BALANCE=opening + net, CASHBOOK_BALANCE=cash)
            difference = opening + net - cash
        else:
            spec = config.reports[{"income": "income_statement", "balance": "balance_sheet",
                                   "trial": "trial_balance"}[name]]["sections"]
            for key, section in spec.items():
                total = Decimal(0)
                for a in accounts:
                    if a.account_type.name not in section["account_types"]:
                        continue
                    raw = movement[a.id] if name == "income" else closing[a.id]
                    # Revenue, liabilities and equity have a normal credit balance.
                    value = -raw if key in ("OPERATING_REVENUES", "NON_OPERATING_REVENUES", "LIABILITIES", "EQUITY") else raw
                    if raw:
                        add(section["label"], a.name, value, a.account_code,
                            a.category.name if a.category else a.account_type.value,
                            max(raw, Decimal(0)) if name == "trial" else None,
                            max(-raw, Decimal(0)) if name == "trial" else None)
                    total += value
                totals[section["label"]] = total
            if name == "income":
                revenue = totals["Operating Revenues"] + totals["Non Operating Revenues"]
                expenses = totals["Operating Expenses"] + totals["Non Operating Expenses"]
                results = dict(TOTAL_REVENUE=revenue, TOTAL_EXPENSES=expenses, NET_PROFIT=revenue - expenses)
                difference = None
            elif name == "balance":
                add("Equity", "Current year earnings (unclosed)", earnings, category="Current year earnings")
                totals["Equity"] += earnings
                results = dict(TOTAL_ASSETS=totals["Assets"], TOTAL_LIABILITIES=totals["Liabilities"],
                               NET_ASSETS=totals["Assets"] - totals["Liabilities"], TOTAL_EQUITY=totals["Equity"])
                difference = results["NET_ASSETS"] - results["TOTAL_EQUITY"]
            else:
                results = dict(DEBIT=sum((Decimal(r["debit"]) for r in rows), Decimal(0)),
                               CREDIT=sum((Decimal(r["credit"]) for r in rows), Decimal(0)))
                difference = results["DEBIT"] - results["CREDIT"]
        effective_start = date(end.year, s.entity.year_start, 1) if name in ("balance", "trial") else start
        return dict(name=name, start=effective_start, end=end, currency="USD",
            basis="as_at" if name in ("balance", "trial") else "period",
            results={k: str(v) for k, v in results.items()}, rows=rows,
            section_totals={k: str(v) for k, v in totals.items()},
            reconciliation_difference=str(difference) if difference is not None else None)
