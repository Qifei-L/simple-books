"""Banking read models. All writes remain in the atomic accounting service."""
from datetime import datetime, time
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import aliased
from python_accounting.models import Account, Transaction, Ledger, Assignment
from . import accounting as books
from .db import operations


def entry_status(entry):
    remaining = abs(Decimal(entry["suspense_remaining"]))
    if remaining == 0:
        return "classified"
    return "partially_classified" if remaining < Decimal(entry["amount"]) else "unclassified"


def account_list(as_of=None):
    with books.session() as s:
        result = []
        for a in s.scalars(select(Account).where(Account.account_type == Account.AccountType.BANK)
                           .order_by(Account.account_code)).all():
            balance = a.closing_balance(s, datetime.combine(as_of, time.max) if as_of else None)
            result.append(dict(id=a.id, name=a.name, account_code=a.account_code,
                               balance=str(balance), currency="USD"))
        return result


def check_bank(s, bank_id):
    a = books.get_account(s, bank_id)
    if a.account_type != Account.AccountType.BANK:
        raise HTTPException(422, "Select a bank or petty cash account.")
    return a


def posted_query(start=None, end=None):
    if start and end and start > end:
        raise HTTPException(422, "The start date cannot follow the end date.")
    q = select(Transaction).order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
    if start:
        q = q.where(Transaction.transaction_date >= datetime.combine(start, time.min))
    if end:
        q = q.where(Transaction.transaction_date <= datetime.combine(end, time.max))
    return q


def transaction_list(bank_id=None, start=None, end=None, status=None, search=None):
    with books.session() as s:
        if bank_id is not None:
            check_bank(s, bank_id)
        # Restrict the accounting query to entries which actually touch BANK;
        # reclassification journals cannot appear as a second bank movement.
        bank_account = aliased(Account, flat=True)
        ids = select(Ledger.transaction_id).join(bank_account, bank_account.id == Ledger.post_account_id).where(
            bank_account.account_type == Account.AccountType.BANK, Ledger.entity_id == s.entity.id)
        if bank_id is not None:
            ids = ids.where(Ledger.post_account_id == bank_id)
        rows = []
        for t in s.scalars(posted_query(start, end).where(Transaction.id.in_(ids))).all():
            entry = books.transaction_json(s, t)
            state = entry_status(entry)
            if status and (state not in ("unclassified", "partially_classified") if status == "pending" else state != status):
                continue
            if search and search.casefold() not in " ".join(str(entry[k] or "") for k in ("number", "description", "reference")).casefold():
                continue
            for b in entry["bank_entries"]:
                if bank_id is not None and b["account_id"] != bank_id:
                    continue
                amount = Decimal(b["amount"])
                rows.append({**entry, "entry_key": f'{t.id}:{b["account_id"]}', "bank_id": b["account_id"],
                    "bank": b["account"], "money_in": str(max(amount, Decimal(0))),
                    "money_out": str(max(-amount, Decimal(0))), "status": state})
        return rows


def clearing_links(s, transaction_id):
    result = []
    for link in s.scalars(select(Assignment).where(Assignment.transaction_id == transaction_id)
                          .order_by(Assignment.id)).all():
        doc = s.get(Transaction, link.assigned_id)
        result.append(dict(id=link.id, date=link.assignment_date.date().isoformat(), amount=str(link.amount),
            document_id=link.assigned_id, document_number=doc.transaction_no if doc else None,
            document_type=doc.transaction_type.name if doc else link.assigned_type))
    return result


def ledger_rows(s, transaction_id):
    accounts = {}
    for ledger in s.scalars(select(Ledger).where(Ledger.transaction_id == transaction_id)
                            .order_by(Ledger.id)).all():
        a = books.get_account(s, ledger.post_account_id)
        row = accounts.setdefault(a.id, dict(account_id=a.id, account=a.name, account_code=a.account_code,
                                           debit=Decimal(0), credit=Decimal(0)))
        row["debit" if ledger.entry_type.name == "DEBIT" else "credit"] += ledger.amount
    return [{**row, "debit": str(row["debit"]), "credit": str(row["credit"])} for row in accounts.values()]


def transaction_detail(transaction_id):
    with books.session() as s:
        t = s.get(Transaction, transaction_id)
        if not t or t.entity_id != s.entity.id or not t.is_posted:
            raise HTTPException(404, "Bank transaction not found.")
        entry = books.transaction_json(s, t)
        if not entry["bank_entries"]:
            raise HTTPException(404, "This entry is not a bank transaction.")
        classification_rows = []
        child_ops = s.connection().execute(select(operations).where(operations.c.related_id == t.id,
            operations.c.kind.in_(["reclassification", "suspense_adjustment"])).order_by(operations.c.id)).mappings()
        for op in child_ops:
            child = s.get(Transaction, op["transaction_id"])
            if child:
                classification_rows.append(dict(id=child.id, number=child.transaction_no, kind=op["kind"],
                    date=child.transaction_date.date().isoformat(), description=child.narration,
                    amount=str(child.amount), ledger=ledger_rows(s, child.id), clearings=clearing_links(s, child.id)))
        return {**entry, "status": entry_status(entry), "ledger": ledger_rows(s, t.id),
                "classifications": classification_rows, "clearings": clearing_links(s, t.id)}


def classification_detail(transaction_id):
    return transaction_detail(transaction_id)["classifications"]


def open_documents(account_id):
    with books.session() as s:
        account = books.get_account(s, account_id)
        kind = {"RECEIVABLE": "CLIENT_INVOICE", "PAYABLE": "SUPPLIER_BILL"}.get(account.account_type.name)
        if not kind:
            raise HTTPException(422, "Select a customer A/R or supplier A/P account.")
        result = []
        for t in s.scalars(posted_query().where(Transaction.account_id == account_id,
                         Transaction.transaction_type == Transaction.TransactionType[kind])).all():
            if t.is_posted and t.amount - t.cleared(s) > 0:
                result.append(books.transaction_json(s, t))
        return result


def clearing_sources(side=None, suspense_only=False):
    with books.session() as s:
        result = []
        for t in s.scalars(posted_query()).all():
            if not t.is_posted:
                continue
            entry = books.transaction_json(s, t)
            available = Decimal(entry["available"] or 0)
            if not suspense_only and available > 0 and (side is None or entry["settlement_side"] == side):
                result.append(dict(source_id=t.id, number=entry["number"], date=entry["date"],
                    from_suspense=False, side=entry["settlement_side"], available=str(available),
                    account_id=entry["account_id"], account=entry["account"], bank_entries=entry["bank_entries"]))
            remaining = Decimal(entry["suspense_remaining"])
            if remaining != 0 and (side is None or entry["suspense_side"] == side):
                result.append(dict(source_id=t.id, number=entry["number"], date=entry["date"],
                    from_suspense=True, side=entry["suspense_side"], available=str(abs(remaining)),
                    account_id=None, account="Suspense", bank_entries=entry["bank_entries"]))
        return result
