"""Is this installation ready for real borrowers? A checklist, not a guess.

Each check looks at the system as it is - settings, users, data, channels, the
nightly batch - and says pass, warn or fail, with what to do about it. Nothing
here changes anything; the page and `manage.py go_live_check` only report.
"""
from pathlib import Path

from django.conf import settings
from django.utils import timezone

PASS, WARN, FAIL = "pass", "warn", "fail"

# The users and passwords `manage.py seed` creates. Anyone who has read the
# README knows them, so one still working is an open door.
DEMO_LOGINS = {"admin": "admin123", "officer": "officer123", "officer2": "officer123",
               "teller": "teller123", "viewer": "viewer123"}
SEED_ADDRESS = "12 Samora Machel Avenue, Harare"


def _check(key, area, label, status, detail, fix=""):
    return {"key": key, "area": area, "label": label, "status": status, "detail": detail,
            "fix": fix}


def checks(request=None) -> list[dict]:
    from ..models import Borrower, OrganisationSetting, User
    from . import gateways, jobs

    out = []

    # ---- security
    out.append(_check(
        "debug", "Security", "Development mode is off",
        FAIL if settings.DEBUG else PASS,
        "DEBUG is on: error pages show code and settings to anyone." if settings.DEBUG
        else "DEBUG is off.",
        "Set DEBUG=0 in backend/.env on the live server."))

    key = settings.SECRET_KEY or ""
    weak_key = "insecure" in key or len(key) < 40
    out.append(_check(
        "secret_key", "Security", "A private secret key",
        FAIL if weak_key else PASS,
        "SECRET_KEY is the built-in development one or too short." if weak_key
        else "SECRET_KEY is set and long enough.",
        "Set SECRET_KEY in backend/.env to a long random value, different on each server."))

    open_doors = []
    for username, password in DEMO_LOGINS.items():
        user = User.objects.filter(username=username, is_active=True).first()
        if user and user.check_password(password):
            open_doors.append(username)
    out.append(_check(
        "demo_logins", "Security", "No demo logins with demo passwords",
        FAIL if open_doors else PASS,
        (f"{', '.join(open_doors)} still sign in with the demo password printed in the README."
         if open_doors else "No demo user still has its demo password."),
        "In System > Users, change each one's password or deactivate it. Create real users "
        "for your staff first."))

    show = getattr(settings, "SHOW_DEMO_LOGINS", False)
    out.append(_check(
        "demo_hint", "Security", "The sign-in page does not print demo passwords",
        FAIL if show else PASS,
        "SHOW_DEMO_LOGINS is on: the sign-in page lists the demo usernames and passwords."
        if show else "The sign-in page shows no demo passwords.",
        "Set SHOW_DEMO_LOGINS=0 (or remove it) in backend/.env."))

    secure = request.is_secure() if request is not None else None
    out.append(_check(
        "https", "Security", "Reached over HTTPS",
        PASS if secure else WARN,
        "This page was loaded over HTTPS." if secure
        else ("Checked from the command line: open System > Go-live checklist in the browser to "
              "check HTTPS." if request is None else
              "This page was loaded over plain HTTP. Fine on this computer; not for staff over a "
              "network or borrowers on the portal."),
        "Serve the system behind HTTPS with a real address (see the Docker set-up in the README)."))

    hosts = [h for h in settings.ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1", "[::1]")]
    out.append(_check(
        "hosting", "Security", "Reachable at a real address",
        PASS if hosts else WARN,
        f"Answers to {', '.join(hosts)}." if hosts
        else "Only this computer can reach it (ALLOWED_HOSTS is localhost).",
        "On the server, set ALLOWED_HOSTS to its address, e.g. lms.zinmadcapital.co.zw."))

    # ---- your details and data
    org = OrganisationSetting.load()
    demo_details = [label for label, bad in (
        ("address", (org.address or "").strip() == SEED_ADDRESS),
        ("email", (org.email or "").endswith("@example.com") or not org.email),
        ("phone", (org.phone or "").replace(" ", "") in ("", "0242700100")),
    ) if bad]
    out.append(_check(
        "org_details", "Your details", "Real contact details on statements",
        FAIL if demo_details else PASS,
        (f"The {', '.join(demo_details)} on statements and agreements are demo or blank."
         if demo_details else "Address, phone and email are set."),
        "System > Settings > Institution."))
    out.append(_check(
        "registration", "Your details", "Registration and licence line",
        PASS if org.registration else WARN,
        f"Printed: {org.registration}" if org.registration
        else "No registration or licence number is printed on statements.",
        "System > Settings > Statements and agreements."))

    seeded = Borrower.objects.filter(email__endswith="@example.com").count()
    test_rows = Borrower.objects.filter(notes__icontains="Safe to delete").count()
    out.append(_check(
        "demo_data", "Your details", "No demo borrowers or loans",
        FAIL if seeded or test_rows else PASS,
        (f"{seeded} demo borrower(s) from the seed data"
         + (f" and {test_rows} test borrower(s)" if test_rows else "") + " are in this database."
         if seeded or test_rows else "No seed or test borrowers found."),
        "Go live on a new, empty database: set DB_NAME to it, run migrate, create your admin, "
        "then bring the real loan book in with System > Loan book migration."))

    # ---- messages and the nightly batch
    gateway = gateways.describe()
    live = [name for name, on in (("SMS", gateway["sms_delivers"]),
                                  ("WhatsApp", gateway["whatsapp_delivers"]),
                                  ("email", gateway["email_delivers"])) if on]
    out.append(_check(
        "channels", "Messages", "Borrowers can be reached",
        PASS if "SMS" in live else (WARN if live else FAIL),
        (f"Delivering by {', '.join(live)}." if live
         else "No channel delivers: reminders and receipts are only logged."),
        "Communications > Channels: connect an SMS provider at least."))

    from ..models import WatchlistEntry

    lists = sorted(set(WatchlistEntry.objects.values_list("source", flat=True)))
    out.append(_check(
        "screening", "Compliance", "Borrowers are screened against sanctions lists",
        PASS if lists else FAIL,
        f"Screening against: {', '.join(lists)}." if lists
        else "No sanctions list is loaded, so nobody is screened.",
        "Customers > Screening: load the UN consolidated list (XML) and any local list."))

    from .payouts import mobile_money_live

    out.append(_check(
        "payouts", "Operations", "Mobile-money payouts reach wallets",
        PASS if mobile_money_live() else WARN,
        "Wallet payouts go to the configured provider." if mobile_money_live()
        else "Wallet payouts are only logged; bank payouts work through the bank file.",
        "Set PAYOUT_HTTP_URL and the provider's fields in backend/.env."))

    attention = jobs.attention()
    out.append(_check(
        "jobs", "Operations", "The nightly batch is running",
        PASS if not attention else WARN,
        "Every scheduled job ran on time." if not attention
        else f"{attention} scheduled job(s) failed or have not run on time.",
        "System > Scheduled jobs; the Windows task 'LMS nightly batch' runs them at 22:00."))

    out.append(_check(
        "backups", "Operations", "Recent database backup",
        *_backup_status()))

    alerts = list(getattr(settings, "JOB_ALERT_EMAILS", []) or [])
    out.append(_check(
        "alerts", "Operations", "Someone is told when a job fails",
        PASS if alerts else WARN,
        f"Failures are emailed to {', '.join(alerts)}." if alerts
        else "A failed nightly job only shows on the Scheduled jobs page.",
        "Set JOB_ALERT_EMAILS in backend/.env (needs email configured)."))
    return out


def _backup_status():
    """From the nightly batch's own logs: when it last reported a good backup."""
    logs = Path(settings.BASE_DIR).parent / "logs"
    newest = None
    for log in sorted(logs.glob("nightly-*.log"), reverse=True)[:14]:
        try:
            if "OK     database backup" in log.read_text(encoding="utf-8", errors="replace"):
                newest = log
                break
        except OSError:
            continue
    if newest is None:
        return (FAIL, "No nightly log in the last two weeks records a good backup.",
                "Run scripts/backup_database.ps1, and check the 'LMS nightly batch' task.")
    when = timezone.datetime.strptime(newest.stem.split("-")[1], "%Y%m%d").date()
    age = (timezone.localdate() - when).days
    status = PASS if age <= 1 else (WARN if age <= 3 else FAIL)
    return (status, f"The last good backup was on {when:%d %b %Y}"
            + (" (today)." if age == 0 else f" ({age} day(s) ago)."),
            "Copy backups off this machine too: a backup on the same disk is lost with it.")


def summary(items: list[dict]) -> dict:
    counts = {PASS: 0, WARN: 0, FAIL: 0}
    for item in items:
        counts[item["status"]] += 1
    return {"ready": counts[FAIL] == 0, **counts, "checked_at": timezone.now(),
            "items": items}
