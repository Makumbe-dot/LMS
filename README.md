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

**Funding and capital** — shareholders' capital, and the funder facilities the book is lent from:
drawdowns, principal repayments, fees, and borrowing interest **accrued monthly** to a liability
account. Every outflow is refused if there is not the cash for it, so the bank account cannot be
driven negative. A **balance sheet** with retained earnings derived from income less expense, and a
**reconciliation report** that checks all nine ledger accounts against the sub-ledgers they claim to
equal.

**Period close** — a month can be **closed to further postings**, with the trial balance it was
signed off on frozen onto the period. Anything dated into a closed month is refused at the model
layer, so no code path can slip past it. Months close in order and only forwards, which makes "the
earliest date you can still post to" a real answer the date pickers and the bulk importer both use.
Reopening needs a reason and goes in the audit trail with the numbers it supersedes.

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
..\.venv\Scripts\python.exe manage.py run_penalties [--as-of 2026-09-30] [--skip-closed]
..\.venv\Scripts\python.exe manage.py send_reminders [--send] [--days-before 5]
..\.venv\Scripts\python.exe manage.py run_savings_interest [--dormant-after 6] [--skip-closed]
..\.venv\Scripts\python.exe manage.py accrue_borrowing_interest [--as-of 2026-09-30] [--skip-closed]
..\.venv\Scripts\python.exe manage.py run_provisions [--as-of 2026-09-30] [--dry-run] [--force]
```

The three monthly steps — savings interest, borrowing interest and the provision — run on the 1st
only; the nightly script skips them on every other night rather than calling a command that would
be a no-op.

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
      provisioning.py           booking the IFRS 9 expected credit loss movement
      funding.py                capital, funder facilities, borrowing interest, the cash guard
      arrears.py                the one set-based arrears definition every report reads
      periods.py                period close, and the guard that refuses a closed date
      reports.py                dashboard, PAR, collections due, loan book, statement,
                                IFRS 9 provisioning, performance, payroll deductions
    templates/core/             the printable loan agreement
    views/                      auth, borrowers, products, charges, loans, groups, savings,
                                ledger, funding, provisions, periods, reports, org
    management/commands/        seed, run_penalties, run_savings_interest, run_provisions,
                                accrue_borrowing_interest, send_reminders, close_period,
                                reopen_period
    tests/                      the test suite
frontend/                       React + Vite single-page app
  src/
    lib/        api.js (fetch + JWT), auth.jsx, org.jsx, periods.jsx, theme.jsx, format.js,
                useApi.js
    components/ Layout, GlobalSearch, DataTable, Modal, Toast, GroupedBars, HBars,
                LoanTable, ui.jsx
    pages/      Login, Dashboard, Borrowers, Groups, Loans, Savings, Collections, Arrears,
                Payroll, BulkImport, Notifications, Transactions, Ledger, Funding, Performance,
                Provisioning, Periods, Products, Charges, Users, Settings, Account, Audit
    styles.css  design tokens, light and dark themes
sql/
  01_create_database.sql        create the LMS database (run first)
  02_app_login.sql              optional SQL login and a read-only analyst login
  03_reporting_views.sql        loan-book views, arrears ageing (all eight columns), indexes
  04_reporting_views_v2.sql     savings, groups, ledger, charge, funding and balance-sheet
                                views, plus usp_reconcile_ledger and its ten checks
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

Four posting sources raise entries: loan transactions, savings movements, facility movements and
capital movements. They share one `_raise_entry` helper, so the rules that matter — drop zero lines,
refuse an unbalanced entry, refuse one whose accounts are missing — are written once rather than
once per source. `ledger._sources()` is the table `backfill` sweeps, so a new source cannot be
half-added and leave its account silently short after a Rebuild.

**Read the Reconciliation tab, not the balanced flag.** A trial balance that balances, and a balance
sheet where assets equal liabilities plus equity, both follow automatically from entries where every
debit has a credit. They are arithmetic, not evidence. `GET /api/ledger/reconciliation` checks the
claims that can genuinely break — nine accounts against the sub-ledgers they are supposed to equal:

| Account | Equals |
|---|---|
| 1100 Loans receivable | principal outstanding on active loans |
| 1300 Penalties receivable | penalties outstanding on active loans |
| 1400 Charges receivable | charges outstanding on active loans |
| 1900 Provision for credit losses | provision held across every loan |
| 2000 Client funds payable | savings balances |
| 2100 Funder borrowings | principal outstanding on funding facilities |
| 2110 Accrued interest on borrowings | interest accrued and unpaid on facilities |
| 3100 Share capital | capital injected less capital returned |
| 3200 Distributions | dividends paid |

A migration, a hand-edit in SSMS, or a service that moves a balance without posting all show up
there. `manage.py seed` prints the result at the end of every run, and the same reconciliation is
available in SSMS for the morning of a board meeting:

```sql
EXEC dbo.usp_reconcile_ledger;
```

## Funding and capital

Nothing modelled where the money to lend came from, so account 1000 went negative as soon as
disbursements outran collections and no balance sheet could be drawn. Capital and facilities fix
that:

```powershell
cd backend
..\.venv\Scripts\python.exe manage.py accrue_borrowing_interest [--as-of 2026-09-30] [--facility FAC-000001]
```

or the **Funding and capital** page. Two asymmetries with the loan book are deliberate:

- **Borrowing interest is accrued monthly** to account 2110, while loan interest is recognised only
  when collected. An unrecognised asset is prudent; an unrecognised liability is not, and a board
  pack that understates what is owed to a funder is the failure this exists to prevent. The accrual
  **catches up every month missed**, one at a time — accruing a single month after a scheduler was
  down for a quarter would silently lose two months of a real debt, and the 2110 check would still
  read OK because both sides would be wrong together.
- **An interest payment never splits between the liability and the expense.** Paying more than has
  accrued is refused, with the accrual command in the message. Expensing the unaccrued remainder is
  what double-counts a period: pay on the 1st before anything has accrued, and the month-end accrual
  then charges the same period again.

**Cash is guarded.** Every outflow — a principal repayment, an interest payment, a fee, a return of
capital, a dividend — is refused if it would take account 1000 below zero. A dividend also needs the
equity for it. A slice that exists to stop cash going negative must not be the thing that puts it
there.

Retained earnings (3000) is **derived**, never posted: nothing writes a year-end closing entry, so
the balance sheet computes income less expense since inception and folds in any manual posting to
3000. Closing income and expense to 3000 is deliberately out of scope — `income_statement` derives
the surplus from journal lines over a date range, so a closing entry would double-count it.

Three simplifications worth knowing, each of which removed a place for a bug to live: there is no
funder table (`funder_name` is a snapshot on the facility, as `LoanCharge.name` is on a loan); a
facility has a `closed_on` date rather than a four-state machine, which is where a "fully repaid
revolving facility can never be drawn again" bug would otherwise sit; and an arrangement fee is its
own movement rather than a component netted off a drawdown, which produces the identical ledger and
the identical cash with none of the special cases.

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

That definition is written down in **three** places and all three must agree: `Instalment.balance`
(the Python property the repayment waterfall uses), `core.services.arrears.OVERDUE_BALANCE` (the SQL
expression every report uses), and `dbo.vw_loan_book` in sql/04. All eight columns — principal,
interest, penalty and charge, due and paid. `core/tests/test_arrears.py` asserts the first two agree
loan-for-loan across a book exercised through repayments, penalties, charges, waivers, reversals and
a reschedule, at four different dates.

**Reports read arrears from the database, not by walking schedules.** The dashboard, PAR, the loan
book, the ECL report, the three performance reports, group standing and the loan list used to load
every active loan with its whole instalment schedule prefetched and iterate it in Python — about
twelve instalment rows and one model instantiation per loan, per page load. They now annotate two
correlated subqueries and read the answer off the row. `core.services.arrears` is the only place the
expression lives, and the same module's `totals()` produces the ageing buckets, the PAR figures and
the book totals in one pass, so the dashboard chart and the ageing CSV cannot disagree.

The control against sliding back is in the suite, not in a benchmark nobody runs:
`QueryCountTests` doubles the size of the book and asserts each report's query count does not
change. It has already caught one real N+1 — the loan list was fetching the officer and the branch
per row — and it fails rather than merely getting slow if a future change reintroduces one.

`instalments` carries one covering index, `ix_inst_due_loan_cover` on `(due_date, loan_id)`
INCLUDE-ing the eight money columns and `status`, so the subquery is an index seek that never touches
the table. One, not two: this is the hottest write table in the system — every repayment
bulk-updates eleven columns — and each extra wide index is maintained on every one of them.

*Reschedule*: outstanding principal + overdue unpaid interest + penalties + charges are capitalised
as the new principal; future unearned interest on the old schedule is dropped. The capitalisation is
posted as its own `capitalisation` transaction — the receivable grows by exactly what interest,
penalties and charges shed — so the ledger moves with the loan book.

## Period close

A month is closed to further postings from the **Period close** page, or on the server:

```powershell
cd backend
..\.venv\Scripts\python.exe manage.py close_period --year 2026 --month 8 --dry-run
..\.venv\Scripts\python.exe manage.py close_period --year 2026 --month 8 --note "Board pack issued"
..\.venv\Scripts\python.exe manage.py reopen_period --year 2026 --month 8 --reason "Bank confirmed a double capture"
```

`--dry-run` prints the pre-close checks and the trial balance that would be frozen, and changes
nothing. Read it before closing anything for real.

Once August is closed, any new repayment, disbursement, charge, savings movement, penalty accrual,
savings-interest run or provision run dated on or before 31 August is refused with **409** and a
message naming the earliest date that still works. The guard is a `pre_save` receiver on
`Transaction`, `SavingsTransaction` and `JournalEntry`, so it cannot be bypassed by calling
`objects.create()` directly, and the refused row never reaches SQL Server — no balance moves, no
instalment is allocated against.

Four decisions worth knowing:

- **A month with no row is open.** Nothing changes on an existing book until an administrator
  closes something. That is what lets this ship onto a book with fourteen months of back-dated
  history.
- **Months close in order and only forwards**, so the closed months are always one contiguous run
  ending at a single date. Closing August closes everything up to 31 August whether or not July was
  closed separately. This is what makes "the earliest date you can post to" a real answer rather
  than a guess — and that answer is what the date pickers and the bulk importer use. Only the most
  recently closed month can be reopened, for the same reason.
- **Closing freezes the trial balance** for the month onto the period row, so a later
  reopen-and-change is visible against what was signed off rather than silent. A reopen needs a
  reason of at least ten characters and is written to the audit trail with the numbers it
  supersedes.
- **A closed month refuses new postings; it is not immutable.** Rescheduling a loan rebuilds its
  schedule, including instalments dated inside a closed month (it posts its capitalisation today, so
  the ledger stays balanced), deleting a loan cascades to its postings, and a raw `.update()` can
  move a posting's date. `services/periods.py` says so in its docstring rather than implying
  otherwise.

`ledger.backfill()` — the **Rebuild** button — is deliberately allowed to post into closed months,
because the transactions it accounts for already exist and refusing them would leave the ledger
permanently short. It writes an audit row saying how many entries landed in a closed month, and only
when some actually did.

Two pre-close checks cannot be overridden even with force: a month that has not ended, and a month
with an earlier month still open. Two are blocking but overridable (debits equal credits; every
transaction that should post has an entry). Three are advisory and never refuse — postings dated
after the month end, penalties not accrued to the month end, and whether the ledger agrees with the
loan and savings books as at today. Which checks were overridden is recorded on the period and in
the audit trail.

The nightly script passes `--skip-closed` to the penalty and savings-interest runs, so a month
closed at 09:00 on the 1st does not fail that evening's batch.

**Close last.** Month-end order is: accrue penalties to the month end, credit savings interest, book
the provision, then close. The provision run is *not* given `--skip-closed`, deliberately: if you
close September before booking September's provision, the run fails loudly rather than silently
skipping a month of impairment.

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
