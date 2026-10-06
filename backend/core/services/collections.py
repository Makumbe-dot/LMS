"""Collections: who works which overdue loan, and whether promises are kept.

A loan in arrears can be given to a collector. The collector's queue is their
loans in arrears, the ones whose follow-up date has come first, then the longest
overdue. A promise to pay is a follow-up note with a promised amount and date
(LoanNote); it is judged against the repayments actually received on the loan
from the day the promise was made to the day promised:

    pending   the promised day has not passed and it is not yet paid
    kept      at least the promised amount came in by the promised day
    broken    the promised day passed with less than that

Nothing is stored about a promise's outcome: it is read from the repayments, so a
reversed payment turns a kept promise back into a broken one by itself.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Min, Q, Sum
from django.utils import timezone

from ..audit import audit
from ..exceptions import BusinessRuleError
from ..models import (
    Loan,
    LoanNote,
    LoanStatus,
    Right,
    Transaction,
    TxnType,
    User,
)
from . import arrears as arrears_svc
from .amortisation import q

ZERO = Decimal("0")


# ---------------------------------------------------------------- promises
def _received(loan_ids, start: date, end: date) -> dict[int, list]:
    """Repayments per loan between two dates, not reversed."""
    out = defaultdict(list)
    if not loan_ids:
        return out
    for txn in (Transaction.objects
                .filter(loan_id__in=list(loan_ids), txn_type=TxnType.REPAYMENT,
                        reversed=False, txn_date__gte=start, txn_date__lte=end)
                .values("loan_id", "txn_date", "amount")):
        out[txn["loan_id"]].append(txn)
    return out


def judge(notes, as_of: date | None = None) -> dict[int, dict]:
    """The outcome of each promise among these notes, keyed by note id."""
    as_of = as_of or date.today()
    promises = [n for n in notes if n.promised_amount and n.promised_date]
    if not promises:
        return {}
    start = min(timezone.localtime(n.created_at).date() for n in promises)
    end = max(n.promised_date for n in promises)
    received = _received({n.loan_id for n in promises}, start, end)
    out = {}
    for note in promises:
        made = timezone.localtime(note.created_at).date()
        paid = q(sum((t["amount"] for t in received[note.loan_id]
                      if made <= t["txn_date"] <= note.promised_date), ZERO))
        if paid >= note.promised_amount:
            state = "kept"
        elif note.promised_date < as_of:
            state = "broken"
        else:
            state = "pending"
        out[note.id] = {"state": state, "paid": paid}
    return out


# ---------------------------------------------------------------- assignment
@transaction.atomic
def assign(loan_ids: list[int], collector: User | None, by: User) -> int:
    """Give loans to a collector, or take them back (collector None)."""
    if collector is not None:
        if not collector.is_active:
            raise BusinessRuleError(f"{collector.full_name}'s account is disabled")
        if not collector.has_right(Right.COLLECTIONS):
            raise BusinessRuleError(f"{collector.full_name} does not hold the access right "
                                    f"{Right.COLLECTIONS.label}")
    loans = list(Loan.objects.select_for_update().filter(pk__in=loan_ids))
    if len(loans) != len(set(loan_ids)):
        raise BusinessRuleError("One of those loans does not exist")
    today = date.today()
    changed = 0
    for loan in loans:
        if loan.collector_id == (collector.id if collector else None):
            continue
        loan.collector = collector
        loan.collector_since = today if collector else None
        loan.save(update_fields=["collector", "collector_since"])
        changed += 1
        audit(by, "assign_collector", "loan", loan.id,
              f"{loan.loan_no} to {collector.full_name if collector else 'nobody'}")
    return changed


# ---------------------------------------------------------------- the queue
def queue(as_of: date | None = None, *, collector_id=None, unassigned=False,
          branch_id=None, min_days: int = 1) -> list[dict]:
    """Loans in arrears, with who works them and what is next."""
    as_of = as_of or date.today()
    qs = Loan.objects.filter(status=LoanStatus.ACTIVE).filter(arrears_svc.is_overdue(as_of))
    if collector_id:
        qs = qs.filter(collector_id=collector_id)
    if unassigned:
        qs = qs.filter(collector__isnull=True)
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    qs = arrears_svc.with_arrears(qs, as_of).select_related("borrower", "collector")
    loans = [loan for loan in qs
             if arrears_svc.days_from(loan.oldest_arrears_due, as_of) >= min_days]
    # A second, grouped query: SQL Server will not group a statement that also
    # carries the arrears subqueries.
    contact = {row["loan_id"]: row for row in (
        LoanNote.objects.filter(loan_id__in=[loan.id for loan in loans])
        .values("loan_id")
        .annotate(last_note=Max("created_at"),
                  next_action=Min("next_action_date", filter=Q(resolved=False))))}

    open_promises = {}
    notes = list(LoanNote.objects.filter(loan_id__in=[loan.id for loan in loans],
                                         resolved=False, promised_amount__isnull=False,
                                         promised_date__isnull=False).order_by("-id"))
    outcomes = judge(notes, as_of)
    for note in notes:
        if note.loan_id not in open_promises:
            open_promises[note.loan_id] = {
                "amount": note.promised_amount, "date": note.promised_date,
                **outcomes.get(note.id, {})}

    rows = []
    for loan in loans:
        days = arrears_svc.days_from(loan.oldest_arrears_due, as_of)
        promise = open_promises.get(loan.id)
        last_note = contact.get(loan.id, {}).get("last_note")
        next_action = contact.get(loan.id, {}).get("next_action")
        due_now = next_action is not None and next_action <= as_of
        rows.append({
            "loan_id": loan.id, "loan_no": loan.loan_no,
            "borrower_id": loan.borrower_id, "borrower": loan.borrower.full_name,
            "phone": loan.borrower.phone,
            "arrears_amount": q(loan.arrears_amount), "days_in_arrears": days,
            "bucket": arrears_svc.bucket_for(days),
            "total_outstanding": loan.total_outstanding,
            "collector_id": loan.collector_id,
            "collector": loan.collector.full_name if loan.collector else None,
            "collector_since": loan.collector_since,
            "last_contact": last_note, "next_action": next_action,
            "action_due": due_now,
            "promise": ({**promise, "amount": str(promise["amount"]),
                         "paid": str(promise.get("paid", ZERO))} if promise else None),
        })
    # What needs doing today first, then the deepest arrears.
    rows.sort(key=lambda r: (not r["action_due"], r["next_action"] or date.max,
                             -r["days_in_arrears"]))
    return rows


# ---------------------------------------------------------------- performance
def performance(start: date, end: date) -> list[dict]:
    """Per collector over a period: what they hold, what came in on it, and how
    many promises made in the period were kept."""
    if end < start:
        raise BusinessRuleError("The period ends before it starts")
    collectors = {u.id: u for u in User.objects.filter(collected_loans__isnull=False).distinct()}
    held = defaultdict(list)
    for loan in Loan.objects.filter(collector__isnull=False,
                                    status__in=[LoanStatus.ACTIVE, LoanStatus.CLOSED,
                                                LoanStatus.WRITTEN_OFF]):
        held[loan.collector_id].append(loan)

    collected = defaultdict(lambda: ZERO)
    for row in (Transaction.objects
                .filter(loan__collector__isnull=False, reversed=False,
                        txn_type__in=[TxnType.REPAYMENT, TxnType.RECOVERY],
                        txn_date__gte=start, txn_date__lte=end)
                .values("loan__collector_id").annotate(total=Sum("amount"))):
        collected[row["loan__collector_id"]] = q(row["total"] or ZERO)

    made_from = timezone.make_aware(datetime.combine(start, time.min))
    made_to = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min))
    promise_notes = list(LoanNote.objects.filter(
        loan__collector__isnull=False, promised_amount__isnull=False,
        promised_date__isnull=False, created_at__gte=made_from, created_at__lt=made_to)
        .select_related("loan"))
    outcomes = judge(promise_notes)
    promises = defaultdict(lambda: {"made": 0, "kept": 0, "broken": 0, "pending": 0})
    for note in promise_notes:
        tally = promises[note.loan.collector_id]
        tally["made"] += 1
        tally[outcomes[note.id]["state"]] += 1

    arrears_now = {}
    active_ids = [loan.id for loans in held.values() for loan in loans
                  if loan.status == LoanStatus.ACTIVE]
    for row in arrears_svc.rows(loan_ids=active_ids):
        arrears_now[row["id"]] = row["arrears_amount"]

    out = []
    for collector_id, user in collectors.items():
        loans = held.get(collector_id, [])
        active = [loan for loan in loans if loan.status == LoanStatus.ACTIVE]
        tally = promises[collector_id]
        judged = tally["kept"] + tally["broken"]
        out.append({
            "collector_id": collector_id, "collector": user.full_name,
            "loans_held": len(active),
            "in_arrears": sum(1 for loan in active if arrears_now.get(loan.id, ZERO) > 0),
            "arrears_now": str(q(sum((arrears_now.get(loan.id, ZERO) for loan in active),
                                     ZERO))),
            "collected": str(collected[collector_id]),
            "promises_made": tally["made"], "promises_kept": tally["kept"],
            "promises_broken": tally["broken"], "promises_pending": tally["pending"],
            "kept_rate_pct": (round(100 * tally["kept"] / judged, 1) if judged else None),
        })
    out.sort(key=lambda r: r["collector"])
    return out
