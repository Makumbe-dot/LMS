# Loan Management System

A microfinance / salary-based loan management system built on **React**, **Django REST Framework**
and **Microsoft SQL Server**. USD, reducing-balance interest, monthly instalments.

```
React 19 + Vite  ──HTTP/JWT──►  Django 6 + DRF  ──mssql-django/pyodbc──►  SQL Server
   frontend/                       backend/                                 sql/
```

## What it does

**Borrowers and KYC** — borrower register with employment details, net salary and payday,
guarantors, KYC flag, blacklist flag, branch, search, and document storage for identity papers,
payslips and signed agreements.

**Joint-liability groups** — a group register with roles and meeting details, group standing
(exposure, arrears, worst member), and the usual rule enforced: while a member is materially
behind, the group does not take on new debt.

**Savings** — deposit products with monthly interest, a minimum balance and an optional account
fee; accounts with deposits, withdrawals, reversals, dormancy and closure; a running statement.
Members' balances are carried as a liability in the ledger, never as income.

**Loan products** — **reducing-balance or flat-rate** interest, amount and term limits, upfront
admin and credit-life fees (deducted at disbursement), daily penalty rate, grace days, and a
maximum instalment-to-salary ratio, plus a **charges catalogue** of additional fees defined once
and attached to whichever products carry them.

**Loan lifecycle** — quote with indicative schedule and affordability check, application, approval
(maker-checker: the originating officer cannot approve their own loan), rejection, disbursement
(generates the amortisation schedule, first instalment lands on the borrower's next payday),
closure on full settlement, **early settlement with an interest rebate**, **top-up / refinance**
(a new loan that settles the old one out of its own proceeds), reschedule (capitalises arrears into
a new schedule), write-off, and a **printable loan agreement** with the terms, the schedule, the
guarantors, the security and signature blocks.

**Repayments** — waterfall allocation (penalties, then charges, then interest, then principal,
oldest instalment first), cash / bank / mobile money / salary deduction, reversals, penalty
waivers, and **bulk CSV import** with a line-by-line dry run before anything is posted.
Overpayment is refused.

**Fees mid-term** — a charge raised against a running loan is either collected at the counter
(cash in, the balance untouched) or **added to the loan balance**, where it rides on the next
unpaid instalment and is recovered ahead of interest.

**Penalties** — an end-of-day job accrues late-payment penalties on overdue instalments after the
grace period. Idempotent, so it can run any number of times a day.

**Collections** — collections-due listing, arrears / PAR, **payroll deduction schedules per
employer**, **follow-up notes** on a loan (what was tried, what was promised, what is next), and a
**message outbox** of instalment reminders, arrears notices and repayment receipts.

**Reporting** — dashboard (portfolio outstanding, PAR>30, collection rate, 12-month disbursement vs
collection chart, arrears ageing buckets) filterable by date and branch, **IFRS 9 staging and
expected-credit-loss provisioning**, **performance by officer, product and branch**, collections
due, arrears / PAR listing, loan book, transactions, per-loan statement, CSV export on every
report.

**Accounting** — a **double-entry general ledger** with a chart of accounts. Every money movement
raises one balanced journal entry automatically, so loans receivable in the ledger always equals
principal outstanding on the book. Trial balance, income statement, searchable journal, and a
rebuild command for a book that predates the ledger.

**Impairment booked, not just reported** — a month-end run compares the IFRS 9 provision required
to the provision already carried and posts only the **movement** (Dr 5100 Impairment / Cr 1900
Provision, or the reverse). The provision carried is tracked per loan, released the moment a loan
is written off, and swept when a loan leaves the book. Running a period twice posts nothing.

**Credit assessment** — a transparent, points-based **scorecard** at application (repayment
history, affordability, current arrears, employment, KYC), with the reason for every factor, plus
**approval limits** so a loan above a set amount needs an administrator.

**Security register** — collateral pledged against a loan: type, description, valuation, reference,
and release or realisation.

**Security and control** — JWT login, four roles (admin, loan officer, teller, viewer) enforced per
endpoint, **account lockout after repeated bad passwords**, self-service password change, full
audit log of every posting and decision, searchable and filterable.

**Multi-branch** — staff, borrowers and loans belong to a branch, and the dashboard, provisioning,
performance and payroll screens all filter by it.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| SQL Server 2019+ | Express is fine. Note the instance name, e.g. `localhost\SQLEXPRESS`. |
| SQL Server Management Studio | For running the scripts in `sql/` and browsing the data. |
| Microsoft ODBC Driver 17 or 18 for SQL Server | `Get-OdbcDriver` lists what is installed. |
| Python 3.11+ | |
| Node.js 20+ | Only needed to build or develop the React app. |

## Quick start

### 1. Create the database (SSMS)

Open `sql/01_create_database.sql` in SSMS, connected to your instance, and execute it. It creates
the `LMS` database and turns on read-committed snapshot isolation so reporting screens never block
a teller posting a repayment.

Tables are **not** created here — Django migrations own the schema, so the ORM and the physical
tables cannot drift apart.

If you want the app to connect with a SQL login rather than Windows authentication, also run
`sql/02_app_login.sql` (read its header first: the server must be in Mixed Mode, and you must
change the passwords).

### 2. Backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements.txt

cd backend
copy .env.example .env                # then edit DB_HOST, DB_DRIVER, SECRET_KEY
python manage.py migrate              # creates every table, key and index
python manage.py seed                 # demo data; use --reset to start over
python manage.py runserver
```

The API is now on <http://localhost:8000>.

| Username | Password   | Role         |
|----------|------------|--------------|
| admin    | admin123   | admin        |
| officer  | officer123 | loan officer |
| teller   | teller123  | teller       |
| viewer   | viewer123  | viewer       |

### 3. Reporting views (SSMS, optional)

With the tables in place, run `sql/03_reporting_views.sql`. It adds views
(`vw_loan_book`, `vw_arrears`, `vw_collections_due`, `vw_transactions`, `vw_portfolio_summary`), a
`usp_arrears_ageing` stored procedure and two covering indexes, for analysts working directly in
SSMS, Excel or Power BI. The application does not depend on them — dropping them breaks nothing.

### 4. Frontend

```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to Django
```

For a single-process deployment, build it instead and let Django serve it:

```powershell
npm run build      # writes frontend/dist
```

Django serves `frontend/dist` at `/` whenever that directory exists, so after a build the whole app
is on <http://localhost:8000> with no CORS and no second server.

---

## Going to production

`backend/.env.production.example` is the starting point. Four things are not optional:

1. **`SECRET_KEY`** — a long random value. Settings refuses to start with `DEBUG=0` and the
   development placeholder, rather than letting you deploy it by accident:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(64))"
   ```
2. **`DEBUG=0`**, which switches on HSTS, secure cookies, the SSL redirect, `X-Frame-Options:
   DENY` and content-type nosniff. Behind a reverse proxy that terminates TLS, the proxy must set
   `X-Forwarded-Proto`.
3. **`ALLOWED_HOSTS`** — every hostname the app answers on, and nothing else.
4. **A real certificate on SQL Server**, so `DB_ENCRYPT=1` and `DB_TRUST_SERVER_CERT=0`.

Then check your work and collect the static files:

```powershell
cd backend
..\.venv\Scripts\python.exe manage.py check --deploy
..\.venv\Scripts\python.exe manage.py collectstatic --noinput
```

Run it behind a real server (IIS with HttpPlatformHandler, or nginx in front of gunicorn/waitress)
rather than `runserver`, put the media directory somewhere backed up, and schedule the nightly job
and the backups.

## Configuration

Everything lives in `backend/.env` (see `backend/.env.example` for development and
`backend/.env.production.example` for a server). The settings that matter most:

| Variable | Meaning |
|---|---|
| `DB_HOST` | `localhost\SQLEXPRESS` for a named instance, `localhost` for the default one. |
| `DB_DRIVER` | `ODBC Driver 17 for SQL Server` or `ODBC Driver 18 for SQL Server`. |
| `DB_TRUSTED_CONNECTION` | `1` for Windows authentication (default), `0` to use `DB_USER`/`DB_PASSWORD`. |
| `DB_TRUST_SERVER_CERT` | Keep `1` for a local instance — Driver 18 encrypts by default and local instances have self-signed certificates. |
| `SECRET_KEY` | Change it for anything other than local development. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT access-token lifetime, 480 by default. |
| `SQL_LOG_LEVEL` | Set to `DEBUG` to print every statement the ORM sends to SQL Server. |

## Tests

```powershell
cd backend
python manage.py test core
```

61 tests. They run against a real SQL Server database (`LMS_test`, created and dropped
automatically), covering:

- the amortisation engine, both methods — annuity maths, flat-rate levelling, month-end clamping,
  schedules closing to zero and totals landing exactly on the advance;
- the full loan lifecycle through the HTTP API — roles, affordability, maker-checker approval,
  disbursement, waterfall allocation, penalty accrual and its idempotency, waiver, settlement,
  reversal, reschedule, write-off, reports and audit;
- early settlement — the rebate, the refusal of a stale confirmation amount, and closure;
- bulk import — dry run, commit, bad rows, two rows that would jointly overpay one loan;
- the message outbox — generation, idempotency, receipts, sending and cancelling;
- IFRS 9 staging and the settings that drive the rates;
- performance and payroll reports;
- branches, settings, search, documents, pagination, password change and account lockout.

## Scheduled jobs

One script runs the lot, and everything in it is idempotent, so a repeated or retried run changes
nothing extra:

```powershell
.\scripts\run_nightly_jobs.ps1                    # penalties, reminders, savings interest, backup
.\scripts\run_nightly_jobs.ps1 -AsOf 2026-09-30 -SkipBackup
```

Register it with Task Scheduler. No administrator rights are needed, because it registers for the
current user:

```powershell
.\scripts\register_scheduled_task.ps1 -At 22:00
Start-ScheduledTask -TaskName 'LMS nightly batch'   # run it now rather than waiting
.\scripts\register_scheduled_task.ps1 -Remove
```

For a server, register it instead under a service account with "run whether the user is logged on
or not", which does need elevation. Output is appended to `logs/nightly-YYYYMMDD.log`.

The individual commands, if you would rather schedule them separately:

```powershell
cd backend
..\.venv\Scripts\python.exe manage.py run_penalties [--as-of 2026-09-30]
..\.venv\Scripts\python.exe manage.py send_reminders [--send] [--days-before 5]
..\.venv\Scripts\python.exe manage.py run_savings_interest [--dormant-after 6]
```

The same work is available over the API: `POST /api/reports/run-penalties`,
`POST /api/notifications/generate` and `POST /api/savings/run-interest`. Savings interest is
idempotent within a calendar month, so the nightly script only attempts it on the 1st.

## Backups

```powershell
.\scripts\backup_database.ps1 -Verify
.\scripts\backup_database.ps1 -BackupRoot \\fileserver\sql-backups -RetentionDays 30
```

Takes a checksummed full backup, has SQL Server read it back to prove it is restorable, archives
the borrower documents (which live on disk, not in the database), and prunes anything past the
retention window.

Two things worth knowing:

- **The backup file is written by the SQL Server service account, not by you.** With no
  `-BackupRoot` the script uses the instance's own backup directory, which that account can always
  write. On Express that directory is ACL'd to the service, so the script cannot prune it — for
  anything beyond a trial, point `-BackupRoot` at a share both the service account and your
  operators can reach.
- **Express Edition cannot compress a backup.** The script detects the edition and omits
  `COMPRESSION` rather than failing.

The `LMS` database is created in SIMPLE recovery, which means point-in-time restore is not
available: you can only go back to the last full backup. If the loan book matters, switch it to
FULL and add log backups:

```sql
ALTER DATABASE LMS SET RECOVERY FULL;
BACKUP LOG LMS TO DISK = N'...\LMS-log.trn';   -- then schedule this every 15 minutes
```

## Sending messages for real

`core/services/notifications.py` generates messages into an outbox and `mark_sent()` flips their
status. That function is the seam: call your SMS or email provider per message and record success
or failure there, instead of blanket-marking. Nothing else in the system needs to change, and the
Messages screen already shows the queue, the failures and a CSV export for a bulk provider.

---

## Project layout

```
backend/                        Django project
  manage.py
  lms_backend/
    settings.py                 env-driven config, SQL Server connection, DRF + JWT
    urls.py                     /api, /admin, and the React SPA fallback
  core/
    models.py                   branches, settings, users, borrowers, guarantors, documents,
                                products, loans, instalments, transactions, notes,
                                notifications, audit_log, sequences
    serializers.py              request validation and response shaping
    permissions.py              the four-role guard
    exceptions.py               BusinessRuleError + a handler that always returns {"detail": ...}
    audit.py                    audit-trail helper
    admin.py                    Django admin, with postings deliberately read-only
    services/
      amortisation.py           reducing-balance and flat-rate schedules (Decimal, cent-exact)
      loans.py                  quote, apply, approve, reject, disburse, balances, arrears,
                                early settlement, top-up, reschedule, write-off, recoveries
      repayments.py             waterfall allocation, reversals, waivers
      penalties.py              daily penalty accrual
      charges.py                the charges catalogue, frozen onto a loan when raised
      scoring.py                the credit scorecard
      groups.py                 joint-liability groups and the borrowing rule
      savings.py                deposit accounts, interest, fees, dormancy
      ledger.py                 double-entry posting rules, trial balance, income statement
      imports.py                bulk repayment CSV: parse, validate, commit
      notifications.py          reminder and arrears message generation, outbox
      reports.py                dashboard, PAR, collections due, loan book, statement,
                                IFRS 9 provisioning, performance, payroll deductions
    templates/core/             the printable loan agreement
    views/                      auth, borrowers, products, charges, loans, groups, savings,
                                ledger, reports, org
    management/commands/        seed, run_penalties, send_reminders
    tests/                      the test suite
frontend/                       React + Vite single-page app
  src/
    lib/        api.js (fetch + JWT), auth.jsx, org.jsx, theme.jsx, format.js, useApi.js
    components/ Layout, GlobalSearch, DataTable, Modal, Toast, GroupedBars, HBars,
                LoanTable, ui.jsx
    pages/      Login, Dashboard, Borrowers, Groups, Loans, Savings, Collections, Arrears,
                Payroll, BulkImport, Notifications, Transactions, Ledger, Performance,
                Provisioning, Products, Charges, Users, Settings, Account, Audit
    styles.css  design tokens, light and dark themes
sql/
  01_create_database.sql        create the LMS database (run first)
  02_app_login.sql              optional SQL login and a read-only analyst login
  03_reporting_views.sql        loan-book views, arrears ageing, indexes
  04_reporting_views_v2.sql     savings, groups, ledger and charge views, plus
                                usp_reconcile_ledger
scripts/
  run_nightly_jobs.ps1          the nightly batch, with a dated log
  register_scheduled_task.ps1   register (or remove) that batch in Task Scheduler
  backup_database.ps1           verified backup of the database and the documents
```

## Key calculations

*Instalment, reducing balance* (annuity): `P * r * (1+r)^n / ((1+r)^n - 1)` with `r` the monthly
rate. Interest each month is charged on the opening balance; the final instalment absorbs rounding
so the schedule closes to exactly 0.00.

*Instalment, flat rate*: total interest is `P * r * n` on the original principal, and both the
principal and interest legs are spread evenly. The last instalment takes the rounding on each leg,
so principal sums to exactly the advance and interest to exactly `P * r * n`. At the same quoted
rate a flat loan always costs the borrower more than a reducing-balance one.

*Early settlement*: outstanding principal, plus interest on instalments that have already fallen
due, plus penalties. Interest on instalments not yet due has not been earned, so it is rebated
rather than collected, and the settlement figure sits below the raw total outstanding.

*IFRS 9 staging*: stage 1 up to the stage-2 threshold in days past due, stage 2 up to the stage-3
threshold, stage 3 beyond it. Expected credit loss is exposure (total outstanding) times the stage
rate. Thresholds and rates live in organisation settings.

*Top-up*: the new loan is disbursed in the ordinary way, and the old one is then settled out of
the proceeds at its early-settlement figure. The borrower receives the difference. Because both
legs are ordinary postings, the ledger and the audit trail need no special case: the bank account
is debited by the settlement and credited by the advance, and nets to the cash actually paid out.

*Savings interest*: simple, on the balance standing at the run, at one twelfth of the product's
annual rate, credited once per calendar month. The monthly account fee is taken at the same time
and never takes a balance below zero.

*Ledger recognition*: **interest is recognised when it is collected**, not as it accrues, which
keeps the ledger in step with a book whose interest is recognised instalment by instalment.
Penalties are recognised when they are charged, because they are raised as a receivable on the
instalment. So a write-off expenses principal and penalties but not unearned interest, and the
early-settlement interest rebate touches no ledger account at all.

## How the ledger stays honest

Journal entries hang off `Transaction` through a `post_save` signal
(`core/signals.py` → `core/services/ledger.py`), not off each service that posts one. That is
deliberate: *every money movement is accounted for* is an invariant that should not depend on the
next person remembering to add a line. Posting is idempotent per transaction, runs inside the
caller's atomic block, and an entry whose debits and credits disagree is refused rather than
written.

The chart of accounts being incomplete never blocks a teller: a missing account logs a warning and
skips the entry, and `POST /api/ledger/rebuild` (or the Rebuild button) creates the defaults and
posts everything that was missed.

The test suite asserts the invariant directly — after a lifecycle of disbursement, repayment,
penalty accrual, waiver and write-off, the trial balance balances and account 1100 equals the sum
of `principal_outstanding` across active loans.

The same reconciliation is available in SSMS, for the morning of a board meeting:

```sql
EXEC dbo.usp_reconcile_ledger;
```

It checks loans receivable, penalties receivable, charges receivable, client funds and the credit
loss provision against their sub-ledgers, and that debits equal credits. Any mismatch raises an
error rather than returning quietly.

## Provisioning

The expected credit loss is **booked**, not merely reported:

```powershell
cd backend
..\.venv\Scripts\python.exe manage.py run_provisions [--as-of 2026-09-30] [--dry-run] [--force]
```

or the **Book the movement** button on the Provisioning page. The nightly script attempts it on
the 1st, for the month that just closed.

Three decisions worth knowing:

- **Only the movement is posted.** The provision carried sits on `Loan.provision_held`, which is
  the sub-ledger behind account 1900 — a fifth reconciliation identity, checked by
  `usp_reconcile_ledger` and by the test suite.
- **The provision is booked on the recognised carrying amount** — principal, penalties and
  charges outstanding, exactly what accounts 1100, 1300 and 1400 hold — not on gross exposure.
  Interest is recognised when collected and there is no interest receivable, so provisioning
  gross exposure would put a provision against an asset the ledger does not carry. The page shows
  both figures side by side.
- **A run reads balances as they stand when it executes.** `period_end` is the label it is filed
  under and the key that makes it idempotent, not a point-in-time restatement, so a run behind the
  latest posted one is refused rather than posting a figure that means nothing.

A run can be reversed (the mirror is dated on the original entry, so the pair nets to zero inside
one income-statement window), and is refused if a later run exists or if any loan has moved on
since. `POST /api/ledger/rebuild` re-posts provision entries as well as transaction entries, so
the identity survives a journal wipe.

*Fees*: admin and credit-life fees are a percentage of principal, deducted from the disbursed
amount. The borrower repays the full principal.

*Penalties*: `(unpaid principal + unpaid interest of the instalment) x daily rate x days past
(due date + grace)`.

*Arrears*: sum of unpaid balances on instalments whose due date has passed; days in arrears count
from the oldest unpaid due date. PAR>30 is principal outstanding on loans more than 30 days in
arrears.

*Reschedule*: outstanding principal + overdue unpaid interest + penalties are capitalised as the new
principal; future unearned interest on the old schedule is dropped.

## Notes on the SQL Server backend

Three things behave differently from PostgreSQL and are handled explicitly in the code:

- **`bulk_create` returns no primary keys.** SQL Server does not hand back identity values from a
  multi-row insert, so `services/loans.py` re-reads a freshly created schedule
  (`_create_schedule`) before anything tries to update those rows.
- **Counter rows are locked, not sequenced.** Human-readable numbers (`BRW-000123`, `LN-000045`)
  come from a `sequences` table read with `select_for_update()`, which SQL Server executes as an
  `UPDLOCK`, so two officers capturing applications at the same moment cannot collide.
- **The default collation is case-insensitive.** `SQL_Latin1_General_CP1_CI_AS` means `icontains`
  and `contains` behave the same; searches are case-insensitive without any extra work.

Note also that identity values keep climbing after `seed --reset` — product and loan ids will not
restart at 1. Nothing depends on the numbering.

## Extending

All straightforward given the structure: flat-rate products (add a `rate_method` to `LoanProduct`
and a second branch in `amortisation.py`), multi-currency (currency on product and loan, plus a rate
table), SMS reminders driven by the collections-due report, IFRS 9 staging from the arrears buckets,
a payroll-deduction file export per employer.
