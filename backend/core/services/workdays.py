"""Working days: public holidays and the weekdays the offices are shut.

An instalment never falls due on a closed day. It moves to the next working day
(the "following" convention), which is always kinder to the borrower than the day
before: nobody is in arrears for a day they could not have paid on.

Only the due date moves. Interest is charged per instalment period, not per day,
so an instalment that falls due two days late is the same amount; and the dates
after it keep counting from the original first due date, so a holiday never
pushes the rest of the schedule along with it.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_weekdays(text: str | None) -> set[int]:
    """'sat, Sunday' -> {5, 6}. Raises ValueError on anything it cannot read."""
    days = set()
    for part in (text or "").split(","):
        key = part.strip().lower()[:3]
        if not key:
            continue
        if key not in WEEKDAYS:
            raise ValueError(f"'{part.strip()}' is not a day of the week")
        days.add(WEEKDAYS.index(key))
    return days


def format_weekdays(days: set[int]) -> str:
    """{6, 5} -> 'sat,sun': the canonical stored form."""
    return ",".join(WEEKDAYS[d] for d in sorted(days))


@dataclass
class WorkingCalendar:
    closed_weekdays: set[int] = field(default_factory=set)
    dates: set[date] = field(default_factory=set)
    # (month, day) of the holidays that fall on the same day every year
    annual: set[tuple[int, int]] = field(default_factory=set)

    def is_working(self, d: date) -> bool:
        return (d.weekday() not in self.closed_weekdays
                and d not in self.dates
                and (d.month, d.day) not in self.annual)

    def next_working(self, d: date) -> date:
        """d itself if the offices are open that day, else the next day they are."""
        # A year is far longer than any real run of closed days; the bound only
        # stops a calendar that closes every weekday from looping for ever.
        for _ in range(366):
            if self.is_working(d):
                return d
            d += timedelta(days=1)
        raise ValueError("The calendar has no working day in the coming year")

    def __bool__(self) -> bool:
        return bool(self.closed_weekdays or self.dates or self.annual)


def load() -> WorkingCalendar:
    """The institution's calendar as it stands."""
    from ..models import Holiday, OrganisationSetting

    cal = WorkingCalendar(closed_weekdays=parse_weekdays(
        OrganisationSetting.load().closed_weekdays))
    for day, annual in Holiday.objects.values_list("date", "recurs_annually"):
        if annual:
            cal.annual.add((day.month, day.day))
        else:
            cal.dates.add(day)
    return cal


def move_upcoming_instalments(cal: WorkingCalendar, after: date) -> int:
    """Move unpaid instalments on running loans that now fall on a closed day.

    Run when a holiday is added or the closed weekdays change, so a day declared at
    short notice reaches the loans already on the book. Only instalments due after
    `after` move: one already due has been due, and moving it would rewrite an
    arrears history. Returns how many moved.
    """
    from ..models import Instalment, InstalmentStatus, Loan, LoanStatus

    if not cal:
        return 0
    rows = list(Instalment.objects
                .filter(loan__status=LoanStatus.ACTIVE, due_date__gt=after)
                .exclude(status=InstalmentStatus.PAID)
                .only("id", "loan_id", "due_date"))
    moved = [i for i in rows if not cal.is_working(i.due_date)]
    for inst in moved:
        inst.due_date = cal.next_working(inst.due_date)
    if not moved:
        return 0
    Instalment.objects.bulk_update(moved, ["due_date"], batch_size=500)

    # The last instalment may have moved, and the maturity date is its due date.
    loans = Loan.objects.filter(pk__in={i.loan_id for i in moved})
    for loan in loans:
        last = loan.instalments.order_by("-number").values_list("due_date", flat=True).first()
        if last and last != loan.maturity_date:
            loan.maturity_date = last
            loan.save(update_fields=["maturity_date"])
    return len(moved)
