from datetime import datetime, date, time
from decimal import Decimal
import hashlib
import json
from contextlib import contextmanager
from fastapi import HTTPException
from sqlalchemy import select, insert, update
from python_accounting.models import Base, Entity, Currency, Account, LineItem, Transaction, Assignment, Ledger
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
    side = settlement_side(t) if not reversal and (not op or op["kind"] != "reversal") else None
    remaining_suspense = suspense_remaining(s, t)
    bank_entries = {}
    for entry in s.scalars(select(Ledger).where(Ledger.transaction_id == t.id)).all():
        account = s.get(Account, entry.post_account_id)
        if account.account_type.name == "BANK":
            bank_entries.setdefault(account.id, dict(account_id=account.id, account=account.name, amount=Decimal(0)))
            bank_entries[account.id]["amount"] += entry.amount * (1 if entry.entry_type.name == "DEBIT" else -1)
    return dict(id=t.id, number=t.transaction_no, date=t.transaction_date.date().isoformat(),
        description=t.narration, reference=t.reference, kind=op["kind"] if op else t.transaction_type.name,
        amount=str(t.amount), account_id=t.account_id, account=t.account.name,
        cleared=str(t.cleared(s)) if hasattr(t, "cleared") else None,
        remaining=str(t.amount - t.cleared(s)) if hasattr(t, "cleared") else None,
        due_date=op["due_date"] if op else None, resolved=remaining_suspense == 0,
        reversed=bool(reversal),
        settlement_side=side, available=str(t.balance(s)) if side else None,
        suspense_remaining=str(remaining_suspense), suspense_side="ar" if remaining_suspense > 0 else "ap" if remaining_suspense < 0 else None,
        bank_entries=[{**v, "amount": str(v["amount"])} for v in bank_entries.values() if v["amount"]],
        direction="out" if t.credited else "in",
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
    entries = list(data["lines"])
    if sum(Decimal(str(x["debit"])) for x in entries) != sum(Decimal(str(x["credit"])) for x in entries):
        raise HTTPException(422, "Total debits and credits must be equal.")
    if len({x["account_id"] for x in entries}) != len(entries):
        raise HTTPException(422, "Each account may appear only once. Combine its amounts first.")
    # A simple A/R or A/P journal can settle a document without cash.
    counterparties = [x for x in entries if get_account(s, x["account_id"]).account_type.name in ("RECEIVABLE", "PAYABLE")]
    if len(entries) == 2 and len(counterparties) == 1:
        entries.sort(key=lambda x: x is not counterparties[0])
    main = entries[0]
    for x in entries:
        get_account(s, x["account_id"])
        if (Decimal(str(x["debit"])) > 0) == (Decimal(str(x["credit"])) > 0):
            raise HTTPException(422, "Enter either a debit or a credit amount on each line.")
    t = JournalEntry(narration=data["description"], reference=data["reference"],
        transaction_date=datetime.combine(data["date"], time(12)), account_id=main["account_id"],
        credited=Decimal(str(main["credit"])) > 0, compound=len(entries) != 2,
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
    # Ledger posting inserts rows directly; refresh the previously empty ORM
    # relationship before assigning this entry in the same atomic transaction.
    s.expire(t, ["ledgers"])
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
        if s.scalars(select(Assignment).where((Assignment.transaction_id == id) | (Assignment.assigned_id == id))).first():
            raise HTTPException(409, "This journal has clearing links. It cannot be reversed while those links exist.")
        if s.connection().execute(select(operations.c.id).where(operations.c.related_id == id,
                operations.c.kind.in_(["reclassification", "suspense_adjustment"]))).first():
            raise HTTPException(409, "This journal has classification links. It cannot be reversed while those links exist.")
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

def settlement_side(t):
    if t.compound or t.transaction_type not in Assignment.assignables:
        return None
    if t.account.account_type.name == "RECEIVABLE" and t.credited:
        return "ar"
    if t.account.account_type.name == "PAYABLE" and not t.credited:
        return "ap"
    return None

def link_document(s, source, document_id, amount, day):
    doc = s.get(Transaction, document_id)
    if not doc or doc.entity_id != s.entity.id or source.entity_id != s.entity.id:
        raise HTTPException(404, "Document not found.")
    expected = {"ar": "CLIENT_INVOICE", "ap": "SUPPLIER_BILL"}.get(settlement_side(source))
    reversed_source = s.connection().execute(select(operations.c.id).where(
        operations.c.related_id == source.id, operations.c.kind == "reversal")).first()
    source_op = s.connection().execute(select(operations.c.kind).where(operations.c.transaction_id == source.id)).scalar()
    if source_op == "reversal" or reversed_source or not expected or doc.transaction_type.name != expected:
        raise HTTPException(422, "Select an eligible A/R credit or A/P debit and its invoice or bill.")
    if source.account_id != doc.account_id:
        raise HTTPException(422, "The clearing entry and document must belong to the same customer or supplier.")
    if day < max(source.transaction_date.date(), doc.transaction_date.date()):
        raise HTTPException(422, "The clearing date cannot precede either entry.")
    a = Assignment(assignment_date=datetime.combine(day, time(18)), transaction_id=source.id,
        assigned_id=doc.id, assigned_type=doc.__class__.__name__, amount=amount, entity_id=s.entity.id)
    s.add(a)
    s.flush()

def assign(data, key, user_id):
    if data.get("from_suspense"):
        with session() as s:
            doc = s.get(Transaction, data["document_id"])
            if not doc or doc.entity_id != s.entity.id:
                raise HTTPException(404, "Document not found.")
            account_id = doc.account_id
        return resolve_suspense(data["payment_id"], dict(date=data["date"], description="Clear from Suspense",
            allocations=[dict(account_id=account_id, amount=data["amount"], document_id=data["document_id"], clear_amount=data["amount"])]), key, user_id)
    with session(write=True) as s:
        op, existing = reserve(s, key, data, "assignment", user_id)
        if not existing:
            payment = s.get(Transaction, data["payment_id"])
            if not payment:
                raise HTTPException(404, "Document not found.")
            doc = s.get(Transaction, data["document_id"])
            if not settlement_side(payment) or not doc or doc.transaction_type.name not in ("CLIENT_INVOICE", "SUPPLIER_BILL"):
                raise HTTPException(422, "Select a valid clearing entry and document.")
            applied = min(data["amount"], payment.balance(s), doc.amount - doc.cleared(s))
            if applied <= 0:
                raise HTTPException(422, "There is no remaining amount to clear.")
            link_document(s, payment, data["document_id"], applied, data["date"])
            remainder = payment.balance(s)
            if remainder > 0:
                # An unmatched excess is kept in Suspense, not as an unexplained
                # customer/supplier credit. Apply the balancing journal to the
                # original source so its available amount cannot be used twice.
                pending = s.scalars(select(Account).where(Account.name == "Suspense")).first()
                ar = settlement_side(payment) == "ar"
                adjustment = post_journal(s, dict(date=data["date"], description="Unmatched clearing excess",
                    reference=f"EXCESS-{payment.id}", lines=[dict(account_id=payment.account_id, debit=remainder if ar else 0, credit=0 if ar else remainder),
                        dict(account_id=pending.id, debit=0 if ar else remainder, credit=remainder if ar else 0)]))
                source_op = s.connection().execute(select(operations).where(operations.c.transaction_id == payment.id)).mappings().first()
                root_id = source_op["related_id"] if source_op and source_op["kind"] == "reclassification" else payment.id
                extra_op, _ = reserve(s, hashlib.sha256((key + ":excess").encode()).hexdigest(), data, "suspense_adjustment", user_id, related=root_id)
                finish(s, extra_op, adjustment, user_id)
                s.add(Assignment(assignment_date=datetime.combine(data["date"], time(18)), transaction_id=payment.id,
                    assigned_id=adjustment.id, assigned_type=adjustment.__class__.__name__, amount=remainder, entity_id=s.entity.id))
                s.flush()
            audit_event(s.connection(), user_id, "assign", {**data, "amount": str(applied)})
        return {"id": op["id"], "success": True}

def suspense_remaining(s, t):
    op = s.connection().execute(select(operations).where(operations.c.transaction_id == t.id)).mappings().first()
    if op and op["kind"] in ("reclassification", "suspense_adjustment", "reversal"):
        return Decimal(0)
    pending = s.scalars(select(Account).where(Account.name == "Suspense")).first()
    if not pending:
        return Decimal(0)
    ids = [t.id] + list(s.connection().execute(select(operations.c.transaction_id).where(
        operations.c.related_id == t.id, operations.c.kind.in_(["reclassification", "suspense_adjustment"]))).scalars())
    return sum((entry.amount * (1 if entry.entry_type.name == "CREDIT" else -1)
        for entry in s.scalars(select(Ledger).where(Ledger.transaction_id.in_(ids), Ledger.post_account_id == pending.id)).all()), Decimal(0))

def suspense(data, key, user_id):
    with session(write=True) as s:
        op, existing = reserve(s, key, data, "suspense", user_id)
        if existing:
            return transaction_json(s, s.get(Transaction, op["transaction_id"]))
        bank = get_account(s, data["bank_id"])
        if bank.account_type.name != "BANK":
            raise HTTPException(422, "Select a bank account.")
        pending = s.scalars(select(Account).where(Account.name == "Suspense")).first()
        outgoing = data.get("direction", "in") == "out"
        t = post_journal(s, dict(date=data["date"], description=data["description"], reference=data["reference"],
            lines=[dict(account_id=bank.id, debit=0 if outgoing else data["amount"], credit=data["amount"] if outgoing else 0),
                   dict(account_id=pending.id, debit=data["amount"] if outgoing else 0, credit=0 if outgoing else data["amount"])]))
        return finish(s, op, t, user_id)

def bank_transaction(data, key, user_id):
    if data["account_id"] is None:
        if data["document_id"] is not None or data["clear_amount"] is not None:
            raise HTTPException(422, "Classify the entry to a customer or supplier before clearing a document.")
        return suspense({k: v for k, v in data.items() if k not in ("account_id", "document_id", "clear_amount")}, key, user_id)
    with session(write=True) as s:
        op, existing = reserve(s, key, data, "bank", user_id)
        if existing:
            return transaction_json(s, s.get(Transaction, op["transaction_id"]))
        bank, target = get_account(s, data["bank_id"]), get_account(s, data["account_id"])
        if bank.account_type.name != "BANK" or target.id == bank.id or target.name == "Suspense":
            raise HTTPException(422, "Select a bank or petty cash account and a different classification account. Leave classification blank for unknown entries.")
        if (data["document_id"] is None) != (data["clear_amount"] is None):
            raise HTTPException(422, "Select a document and a clearing amount together, or leave both blank.")
        outgoing = data["direction"] == "out"
        if data["document_id"] is not None:
            pending = s.scalars(select(Account).where(Account.name == "Suspense")).first()
            original = post_journal(s, dict(date=data["date"], description=data["description"], reference=data["reference"],
                lines=[dict(account_id=bank.id, debit=0 if outgoing else data["amount"], credit=data["amount"] if outgoing else 0),
                       dict(account_id=pending.id, debit=data["amount"] if outgoing else 0, credit=0 if outgoing else data["amount"])]))
            finish(s, op, original, user_id)
            classify_data = dict(date=data["date"], description=data["description"], account_id=target.id,
                document_id=data["document_id"], clear_amount=data["clear_amount"])
            child_key = hashlib.sha256((key + ":classification").encode()).hexdigest()
            child, _ = reserve(s, child_key, classify_data, "reclassification", user_id, related=original.id)
            classify_allocations(s, original, classify_data, child_key, user_id, child)
            return transaction_json(s, original)
        t = post_journal(s, dict(date=data["date"], description=data["description"], reference=data["reference"],
            lines=[dict(account_id=bank.id, debit=0 if outgoing else data["amount"], credit=data["amount"] if outgoing else 0),
                   dict(account_id=target.id, debit=data["amount"] if outgoing else 0, credit=0 if outgoing else data["amount"])]))
        return finish(s, op, t, user_id)

def classify_allocations(s, original, data, key, user_id, root_op):
    remaining = suspense_remaining(s, original)
    if remaining == 0:
        raise HTTPException(409, "This entry has no remaining Suspense amount.")
    if data["date"] < original.transaction_date.date():
        raise HTTPException(422, "The classification date cannot precede the original entry.")
    allocations = data.get("allocations")
    if not allocations:
        if data.get("account_id") is None:
            raise HTTPException(422, "Add at least one classification line.")
        allocations = [dict(account_id=data["account_id"], amount=abs(remaining),
            document_id=data.get("document_id"), clear_amount=data.get("clear_amount"))]
    elif data.get("account_id") is not None or data.get("document_id") is not None:
        raise HTTPException(422, "Use either classification lines or the single-account fields.")
    pending = s.scalars(select(Account).where(Account.name == "Suspense")).first()
    budget, first = abs(remaining), None
    outgoing = remaining < 0
    for index, line in enumerate(allocations):
        target = get_account(s, line["account_id"])
        if target.id == pending.id or target.account_type.name == "BANK":
            raise HTTPException(422, "Select a classification account other than bank or Suspense. Unallocated money stays in Suspense automatically.")
        amount = Decimal(line["amount"])
        document_id = line.get("document_id")
        if document_id is not None:
            doc = s.get(Transaction, document_id)
            if not doc or doc.entity_id != s.entity.id or doc.account_id != target.id:
                raise HTTPException(422, "The document must belong to the selected customer or supplier.")
            expected = "SUPPLIER_BILL" if outgoing else "CLIENT_INVOICE"
            if doc.transaction_type.name != expected:
                raise HTTPException(422, "Money in clears customer invoices; money out clears supplier bills.")
            amount = min(amount, Decimal(line.get("clear_amount") or amount), doc.amount - doc.cleared(s))
        elif line.get("clear_amount") is not None:
            raise HTTPException(422, "Select a document for the clearing amount.")
        if amount <= 0:
            raise HTTPException(422, "The selected document has no remaining amount to clear.")
        if amount > budget:
            # Partial receipt/payment: apply only the money actually available;
            # the invoice/bill remainder remains in A/R or A/P.
            if document_id is not None:
                amount = budget
            else:
                raise HTTPException(422, "Classification amounts cannot exceed the remaining Suspense amount.")
        if amount <= 0:
            raise HTTPException(422, "No remaining Suspense amount for this line.")
        entry = post_journal(s, dict(date=data["date"], description=data["description"],
            reference=f"CLASSIFY-{original.id}-{index + 1}", lines=[
                dict(account_id=pending.id, debit=0 if outgoing else amount, credit=amount if outgoing else 0),
                dict(account_id=target.id, debit=amount if outgoing else 0, credit=0 if outgoing else amount)]))
        if document_id is not None:
            link_document(s, entry, document_id, amount, data["date"])
            audit_event(s.connection(), user_id, "assign", dict(payment_id=entry.id, document_id=document_id, amount=str(amount)))
        if index == 0:
            op = root_op
            first = entry
        else:
            op, _ = reserve(s, hashlib.sha256(f"{key}:line:{index}".encode()).hexdigest(), line,
                "reclassification", user_id, related=original.id)
        finish(s, op, entry, user_id)
        budget -= amount
    s.connection().execute(update(operations).where(operations.c.transaction_id == original.id).values(resolved=budget == 0))
    result = transaction_json(s, first)
    result["remaining_suspense"] = str(budget)
    return result

def resolve_suspense(id, data, key, user_id):
    with session(write=True) as s:
        payload = {**data, "original_id": id}
        op, existing = reserve(s, key, payload, "reclassification", user_id, related=id)
        if existing:
            return transaction_json(s, s.get(Transaction, op["transaction_id"]))
        original = s.get(Transaction, id)
        if not original or original.entity_id != s.entity.id:
            raise HTTPException(404, "Suspense entry not found.")
        return classify_allocations(s, original, data, key, user_id, op)

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
