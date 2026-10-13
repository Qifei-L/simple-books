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
from python_accounting.reports import IncomeStatement, BalanceSheet, TrialBalance, CashflowStatement
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
            raise HTTPException(503, "账套尚未初始化")
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
        raise HTTPException(404, "科目不存在")
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
            raise HTTPException(409, "同一个提交编号不能用于不同内容")
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
        raise HTTPException(422, "借贷合计必须相等")
    if len({x["account_id"] for x in entries}) != len(entries):
        raise HTTPException(422, "每个科目只能出现一次，请先合并金额")
    main = entries[0]
    for x in entries:
        get_account(s, x["account_id"])
        if (Decimal(str(x["debit"])) > 0) == (Decimal(str(x["credit"])) > 0):
            raise HTTPException(422, "每行只能填写借方或贷方金额")
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
            raise HTTPException(422, "单据金额超过第一版支持范围")
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
            raise HTTPException(422, "第一版只支持普通日记账冲销")
        if s.connection().execute(select(operations.c.id).where(
            operations.c.related_id == id, operations.c.kind == "reversal", operations.c.id != op["id"])).first():
            raise HTTPException(409, "该日记账已冲销")
        original = s.get(Transaction, id)
        if data["date"] < original.transaction_date.date():
            raise HTTPException(422, "冲销日期不能早于原交易")
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
                raise HTTPException(404, "单据不存在")
            if (payment.transaction_type.name, doc.transaction_type.name) not in (
                    ("CLIENT_RECEIPT", "CLIENT_INVOICE"), ("SUPPLIER_PAYMENT", "SUPPLIER_BILL")):
                raise HTTPException(422, "只能用客户收款核销发票，或供应商付款核销账单")
            if data["date"] < max(payment.transaction_date.date(), doc.transaction_date.date()):
                raise HTTPException(422, "核销日期不能早于单据日期")
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
            raise HTTPException(422, "请选择银行科目")
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
            raise HTTPException(404, "待处理交易不存在")
        if original["resolved"]:
            raise HTTPException(409, "该交易已分类")
        t0 = s.get(Transaction, id)
        target = get_account(s, data["account_id"])
        pending_id = next(l.account_id for l in t0.line_items if l.credited)
        if target.id == pending_id or target.id == t0.account_id:
            raise HTTPException(422, "请选择实际分类科目")
        if data["date"] < t0.transaction_date.date():
            raise HTTPException(422, "分类日期不能早于原交易")
        t = post_journal(s, dict(date=data["date"], description=data["description"],
            reference=f"CLASSIFY-{id}", lines=[dict(account_id=pending_id, debit=t0.amount, credit=0),
                dict(account_id=target.id, debit=0, credit=t0.amount)]))
        s.connection().execute(update(operations).where(operations.c.id == original["id"]).values(resolved=True))
        return finish(s, op, t, user_id)

def report(name, start, end):
    if start > end or start.year != end.year:
        raise HTTPException(422, "第一版报表请选同一自然年内的日期区间")
    with session() as s:
        d0, d1 = datetime.combine(start, time.min), datetime.combine(end, time.max)
        cls = {"income": IncomeStatement, "balance": BalanceSheet,
               "trial": TrialBalance, "cashflow": CashflowStatement}[name]
        r = cls(s, d0, d1) if name in ("income", "cashflow") else cls(s, d1)
        rows = []
        for section, kinds in r.accounts.items():
            for kind, cats in kinds.items():
                for cat, details in cats.items():
                    for a in details["accounts"]:
                        rows.append(dict(section=section, category=cat, account=a.name,
                            account_code=a.account_code, amount=str(a.closing)))
        return dict(name=name, start=start, end=end, currency="USD", text=str(r),
            results={k: str(v) for k, v in r.result_amounts.items()}, rows=rows)
