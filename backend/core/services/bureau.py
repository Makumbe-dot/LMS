"""Credit bureau enquiries.

The scorecard reads only this book. A bureau report says what the borrower owes
everyone else, and whether they paid it. An enquiry is run from the borrower's
file (or at application), the answer is stored as a `BureauEnquiry`, and the
scorecard reads the most recent one while it is still fresh.

Three backends, chosen by `BUREAU_BACKEND`, in the manner of the message gateways:

  * `none`  - no bureau. The default: a lender without a bureau contract should
    see no button, not a pretend report.
  * `demo`  - a deterministic report derived from the national ID, so a demo or a
    test can show the whole flow without a contract. Every report it returns is
    labelled "demo" and never leaves this machine.
  * `http`  - a generic JSON API, configured entirely by settings: URL, method,
    which field carries the identity number, fixed headers for the API key, and
    dotted paths to the figures in the response. FinCheck, TransUnion, XDS and the
    regional bureaus all expose some shape of this, so a new bureau is an .env
    change rather than a dependency.

A backend returns a `Report`. It raises `BureauError` for a failure that should
reach the officer (the bureau is down, the credentials are wrong); the view turns
that into a failed enquiry on the register, so the attempt is still on record.
"""
import hashlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import BureauEnquiry, BureauEnquiryStatus

log = logging.getLogger(__name__)
ZERO = Decimal("0")


class BureauError(Exception):
    """The bureau could not be asked, or did not answer usefully."""


@dataclass
class Report:
    """What a bureau says about one person, reduced to the figures the book uses."""
    provider: str
    score: int | None = None
    score_max: int = 1000
    open_accounts: int = 0
    accounts_in_arrears: int = 0
    defaults: int = 0
    worst_days_in_arrears: int = 0
    total_exposure: Decimal = ZERO
    reference: str | None = None
    summary: str = ""
    detail: dict = field(default_factory=dict)


class Backend:
    name = "backend"

    def enquire(self, borrower) -> Report:  # pragma: no cover - interface
        raise NotImplementedError


class NoneBackend(Backend):
    """No bureau contract: an enquiry is refused, and the page shows no button."""
    name = "none"

    def enquire(self, borrower) -> Report:
        raise BureauError(
            "No credit bureau is configured. Set BUREAU_BACKEND in backend/.env once the "
            "institution has a bureau contract.")


class DemoBackend(Backend):
    """A made-up report that is the same every time for the same national ID.

    Deterministic so a demo can be rehearsed and a test can assert on it; derived
    from a hash so the figures look like a spread of real customers rather than
    one canned answer.
    """
    name = "demo"

    def enquire(self, borrower) -> Report:
        digest = hashlib.sha256((borrower.national_id or "").encode()).digest()
        score = 350 + digest[0] * 600 // 255          # 350..950
        open_accounts = digest[1] % 5
        arrears = digest[2] % 3 if open_accounts else 0
        arrears = min(arrears, open_accounts)
        defaults = 1 if (digest[3] % 7 == 0 and score < 600) else 0
        worst = (digest[4] % 120) if arrears else 0
        exposure = Decimal(digest[5] * 50 + digest[6]) if open_accounts else ZERO
        return Report(
            provider=self.name, score=score, score_max=1000,
            open_accounts=open_accounts, accounts_in_arrears=arrears, defaults=defaults,
            worst_days_in_arrears=worst, total_exposure=exposure,
            reference=f"DEMO-{digest[7:11].hex().upper()}",
            summary="Demo report: figures are made up from the national ID and leave this machine never.",
            detail={"demo": True},
        )


class HttpBackend(Backend):
    """A generic JSON bureau API, configured rather than coded.

    Settings (all under BUREAU_HTTP):
        url            the enquiry endpoint
        method         POST by default
        id_field       which request field carries the national ID
        extra          fixed request fields (subscriber code, product, ...)
        headers        fixed headers (Authorization, ...)
        paths          dotted paths to each figure in the response:
                       score, score_max, open_accounts, accounts_in_arrears,
                       defaults, worst_days_in_arrears, total_exposure, reference
        timeout        seconds, 20 by default
    """
    name = "http"

    def config(self) -> dict:
        config = dict(getattr(settings, "BUREAU_HTTP", {}) or {})
        if not config.get("url"):
            raise BureauError(
                "BUREAU_BACKEND is 'http' but BUREAU_HTTP_URL is not set in backend/.env.")
        return config

    def enquire(self, borrower) -> Report:
        config = self.config()
        payload = {
            config.get("id_field", "national_id"): borrower.national_id,
            "first_name": borrower.first_name,
            "last_name": borrower.last_name,
            **(config.get("extra") or {}),
        }
        request = urllib.request.Request(
            config["url"], data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     **(config.get("headers") or {})},
            method=config.get("method", "POST"))
        try:
            with urllib.request.urlopen(request, timeout=config.get("timeout", 20)) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise BureauError(f"The bureau answered HTTP {exc.code}: "
                              f"{exc.read().decode('utf-8', 'replace')[:300]}")
        except Exception as exc:
            raise BureauError(f"The bureau could not be reached: {type(exc).__name__}: {exc}")
        try:
            body = json.loads(raw)
        except ValueError:
            raise BureauError("The bureau did not answer with JSON")

        paths = config.get("paths") or {}

        def pick(key, default=None):
            value = _dig(body, paths.get(key))
            return default if value is None else value

        def as_int(key, default=0):
            try:
                return int(Decimal(str(pick(key, default))))
            except (ValueError, ArithmeticError):
                return default

        score = pick("score")
        return Report(
            provider=self.name,
            score=as_int("score") if score is not None else None,
            score_max=as_int("score_max", 1000) or 1000,
            open_accounts=as_int("open_accounts"),
            accounts_in_arrears=as_int("accounts_in_arrears"),
            defaults=as_int("defaults"),
            worst_days_in_arrears=as_int("worst_days_in_arrears"),
            total_exposure=Decimal(str(pick("total_exposure", 0) or 0)).quantize(Decimal("0.01")),
            reference=(str(pick("reference")) if pick("reference") is not None else None),
            summary="",
            # Only the figures the paths named, never the whole response: a bureau
            # report carries other lenders' account numbers, which is not ours to keep.
            detail={k: pick(k) for k in paths},
        )


def _dig(value, path: str | None):
    if not path:
        return None
    for key in path.split("."):
        if isinstance(value, list):
            try:
                value = value[int(key)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


BACKENDS = {"none": NoneBackend, "demo": DemoBackend, "http": HttpBackend}


def backend() -> Backend:
    name = getattr(settings, "BUREAU_BACKEND", "none")
    try:
        return BACKENDS[name]()
    except KeyError:
        raise BureauError(f"Unknown bureau backend {name!r}. Choose one of: "
                          f"{', '.join(sorted(BACKENDS))}.")


def describe() -> dict:
    """Whether a check is possible, and how long one stays fresh. Shown on the
    borrower page, so the button only appears when it would do something."""
    name = getattr(settings, "BUREAU_BACKEND", "none")
    return {
        "backend": name,
        "configured": name in BACKENDS and name != "none",
        "demo": name == "demo",
        "valid_days": valid_days(),
    }


def valid_days() -> int:
    return int(getattr(settings, "BUREAU_VALID_DAYS", 90))


def _summary(report: Report) -> str:
    bits = []
    if report.score is not None:
        bits.append(f"score {report.score}/{report.score_max}")
    bits.append(f"{report.open_accounts} open account(s) elsewhere")
    if report.accounts_in_arrears:
        bits.append(f"{report.accounts_in_arrears} in arrears, worst "
                    f"{report.worst_days_in_arrears} days")
    if report.defaults:
        bits.append(f"{report.defaults} default(s) on record")
    if report.total_exposure:
        bits.append(f"owes {report.total_exposure} elsewhere")
    text = "; ".join(bits)
    return f"{text}. {report.summary}".strip() if report.summary else text


@transaction.atomic
def enquire(borrower, user, loan=None) -> BureauEnquiry:
    """Ask the bureau about a borrower and record the answer, or the failure."""
    from ..audit import audit

    row = BureauEnquiry(borrower=borrower, loan=loan, enquired_by=user,
                        provider=getattr(settings, "BUREAU_BACKEND", "none"))
    try:
        report = backend().enquire(borrower)
    except BureauError as exc:
        row.status = BureauEnquiryStatus.FAILED
        row.error = str(exc)[:500]
        row.save()
        audit(user, "bureau_enquiry_failed", "borrower", borrower.id, row.error)
        return row

    row.status = BureauEnquiryStatus.OK
    row.provider = report.provider
    row.reference = report.reference
    row.score = report.score
    row.score_max = report.score_max
    row.open_accounts = report.open_accounts
    row.accounts_in_arrears = report.accounts_in_arrears
    row.defaults = report.defaults
    row.worst_days_in_arrears = report.worst_days_in_arrears
    row.total_exposure = report.total_exposure
    row.summary = _summary(report)
    row.detail = json.dumps(report.detail, default=str)
    row.save()
    audit(user, "bureau_enquiry", "borrower", borrower.id, row.summary)
    return row


def latest(borrower, as_of: date | None = None) -> BureauEnquiry | None:
    """The most recent successful enquiry still inside the validity window."""
    as_of = as_of or date.today()
    since = timezone.make_aware(
        timezone.datetime.combine(as_of - timedelta(days=valid_days()), timezone.datetime.min.time()))
    return (BureauEnquiry.objects
            .filter(borrower=borrower, status=BureauEnquiryStatus.OK, enquired_at__gte=since)
            .order_by("-enquired_at", "-id")
            .first())
