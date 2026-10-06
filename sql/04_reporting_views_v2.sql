/* ============================================================================
   Loan Management System - reporting views, part 2
   Run in SSMS against LMS AFTER "python manage.py migrate".

   Part 1 (03_reporting_views.sql) covers the loan book. This adds the savings
   book, joint-liability groups, the general ledger and the fee catalogue, and
   refreshes vw_loan_book so it carries the charges added to a balance.

   As before, these are for analysts working in SSMS, Excel or Power BI. The
   application never reads them, so dropping them breaks nothing.
   ============================================================================ */
USE LMS;
GO

/* ---------------------------------------------------------------------------
   vw_loan_book - refreshed: total outstanding now includes charges added to
   the balance, and the officer, group and credit grade are carried through.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_loan_book
AS
SELECT
    l.id                            AS loan_id,
    l.loan_no,
    b.borrower_no,
    b.first_name + ' ' + b.last_name AS borrower,
    b.national_id,
    b.phone,
    b.employer,
    p.code                          AS product_code,
    p.name                          AS product,
    p.rate_method,
    br.name                         AS branch,
    u.full_name                     AS officer,
    g.name                          AS [group],
    l.status,
    l.credit_score,
    l.credit_grade,
    l.principal,
    l.interest_rate_pct             AS rate_pct_per_month,
    l.term_months,
    l.instalment_amount,
    l.admin_fee,
    l.insurance_fee,
    l.other_charges,
    l.total_interest,
    l.disbursement_date,
    l.first_instalment_date,
    l.maturity_date,
    l.principal_outstanding,
    l.interest_outstanding,
    l.penalties_outstanding,
    l.charges_outstanding,
    l.principal_outstanding + l.interest_outstanding
        + l.penalties_outstanding + l.charges_outstanding
                                    AS total_outstanding,
    l.total_paid,
    a.arrears_amount,
    a.days_in_arrears,
    CASE
        WHEN a.days_in_arrears IS NULL OR a.days_in_arrears = 0 THEN 'current'
        WHEN a.days_in_arrears <=  30 THEN '1-30'
        WHEN a.days_in_arrears <=  60 THEN '31-60'
        WHEN a.days_in_arrears <=  90 THEN '61-90'
        WHEN a.days_in_arrears <= 180 THEN '91-180'
        ELSE '180+'
    END                             AS arrears_bucket
FROM dbo.loans l
JOIN dbo.borrowers      b  ON b.id  = l.borrower_id
JOIN dbo.loan_products  p  ON p.id  = l.product_id
LEFT JOIN dbo.branches  br ON br.id = l.branch_id
LEFT JOIN dbo.users     u  ON u.id  = l.officer_id
LEFT JOIN dbo.borrower_groups g ON g.id = l.group_id
OUTER APPLY (
    SELECT
        arrears_amount  = ISNULL(SUM(i.principal_due + i.interest_due + i.penalty_due
                                   + i.charge_due
                                   - i.principal_paid - i.interest_paid - i.penalty_paid
                                   - i.charge_paid), 0),
        days_in_arrears = ISNULL(MAX(DATEDIFF(day, i.due_date, CAST(GETDATE() AS date))), 0)
    FROM dbo.instalments i
    WHERE i.loan_id = l.id
      AND i.due_date < CAST(GETDATE() AS date)
      AND (i.principal_due + i.interest_due + i.penalty_due + i.charge_due
         - i.principal_paid - i.interest_paid - i.penalty_paid - i.charge_paid) > 0
) a
WHERE l.status IN ('active', 'closed', 'written_off');
GO

/* ---------------------------------------------------------------------------
   vw_savings_book - one row per savings account with its live balance
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_savings_book
AS
SELECT
    s.id                            AS account_id,
    s.account_no,
    b.borrower_no,
    b.first_name + ' ' + b.last_name AS member,
    b.national_id,
    b.phone,
    p.code                          AS product_code,
    p.name                          AS product,
    p.interest_rate_pct_pa,
    p.min_balance,
    p.allow_withdrawals,
    br.name                         AS branch,
    s.status,
    -- the balance is in the account's currency; blank is the organisation's
    s.currency,
    s.fx_rate                       AS booked_rate,
    s.balance,
    CASE WHEN s.balance - p.min_balance > 0 AND p.allow_withdrawals = 1
         THEN s.balance - p.min_balance ELSE 0 END AS available_balance,
    s.opened_on,
    s.closed_on,
    s.last_interest_date,
    m.last_movement,
    DATEDIFF(day, ISNULL(m.last_movement, s.opened_on), CAST(GETDATE() AS date))
                                    AS days_since_movement
FROM dbo.savings_accounts s
JOIN dbo.borrowers        b  ON b.id  = s.borrower_id
JOIN dbo.savings_products p  ON p.id  = s.product_id
LEFT JOIN dbo.branches    br ON br.id = s.branch_id
OUTER APPLY (
    SELECT last_movement = MAX(t.txn_date)
    FROM dbo.savings_transactions t
    WHERE t.account_id = s.id AND t.txn_type IN ('deposit', 'withdrawal')
) m;
GO

/* ---------------------------------------------------------------------------
   vw_savings_transactions - the savings cash book
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_savings_transactions
AS
SELECT
    t.id                            AS transaction_id,
    t.txn_date,
    s.account_no,
    b.first_name + ' ' + b.last_name AS member,
    p.name                          AS product,
    t.txn_type,
    CASE WHEN t.txn_type IN ('deposit', 'interest')   THEN t.amount ELSE 0 END AS amount_in,
    CASE WHEN t.txn_type IN ('withdrawal', 'fee')     THEN t.amount ELSE 0 END AS amount_out,
    t.balance_after,
    t.method,
    t.reference,
    t.narration,
    t.[reversed],
    u.username                      AS posted_by,
    t.created_at
FROM dbo.savings_transactions t
JOIN dbo.savings_accounts  s ON s.id = t.account_id
JOIN dbo.borrowers         b ON b.id = s.borrower_id
JOIN dbo.savings_products  p ON p.id = s.product_id
LEFT JOIN dbo.users        u ON u.id = t.posted_by_id;
GO

/* ---------------------------------------------------------------------------
   vw_group_standing - joint liability at a glance, worst first
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_group_standing
AS
SELECT
    g.id                            AS group_id,
    g.group_no,
    g.name                          AS [group],
    br.name                         AS branch,
    u.full_name                     AS officer,
    g.status,
    g.meeting_day,
    g.formed_on,
    m.member_count,
    ISNULL(x.active_loans, 0)       AS active_loans,
    ISNULL(x.total_outstanding, 0)  AS total_outstanding,
    ISNULL(x.arrears_amount, 0)     AS arrears_amount,
    ISNULL(x.worst_days_in_arrears, 0) AS worst_days_in_arrears
FROM dbo.borrower_groups g
LEFT JOIN dbo.branches br ON br.id = g.branch_id
LEFT JOIN dbo.users    u  ON u.id  = g.officer_id
OUTER APPLY (
    SELECT member_count = COUNT(*)
    FROM dbo.group_members gm
    WHERE gm.group_id = g.id AND gm.is_active = 1
) m
OUTER APPLY (
    SELECT
        active_loans          = COUNT(*),
        total_outstanding     = SUM(lb.total_outstanding),
        arrears_amount        = SUM(lb.arrears_amount),
        worst_days_in_arrears = MAX(lb.days_in_arrears)
    FROM dbo.vw_loan_book lb
    JOIN dbo.loans l2 ON l2.id = lb.loan_id
    WHERE lb.status = 'active'
      AND l2.borrower_id IN (
            SELECT gm.borrower_id FROM dbo.group_members gm
            WHERE gm.group_id = g.id AND gm.is_active = 1)
) x
WHERE g.status <> 'closed';
GO

/* ---------------------------------------------------------------------------
   vw_trial_balance - the ledger, summarised per account
   --------------------------------------------------------------------------- */
/* ---------------------------------------------------------------------------
   vw_provision_runs - the month-end provision history
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_provision_runs
AS
SELECT
    r.run_no,
    r.period_end,
    r.status,
    r.loans_assessed,
    r.loans_released,
    r.total_carrying_amount,
    r.provision_required,
    r.provision_before,
    r.movement,
    r.stage1_pct, r.stage2_pct, r.stage3_pct, r.stage2_days, r.stage3_days,
    je.entry_no,
    rev.entry_no                    AS reversal_entry_no,
    u.full_name                     AS run_by,
    r.created_at
FROM dbo.provision_runs r
LEFT JOIN dbo.journal_entries je  ON je.id  = r.journal_entry_id
LEFT JOIN dbo.journal_entries rev ON rev.id = r.reversal_entry_id
LEFT JOIN dbo.users u             ON u.id   = r.run_by_id;
GO

CREATE OR ALTER VIEW dbo.vw_trial_balance
AS
SELECT
    a.code,
    a.name                          AS account,
    a.type                          AS account_type,
    ISNULL(SUM(jl.debit), 0)        AS total_debit,
    ISNULL(SUM(jl.credit), 0)       AS total_credit,
    CASE WHEN a.type IN ('asset', 'expense')
         THEN ISNULL(SUM(jl.debit), 0) - ISNULL(SUM(jl.credit), 0)
         ELSE ISNULL(SUM(jl.credit), 0) - ISNULL(SUM(jl.debit), 0)
    END                             AS balance,
    CASE WHEN a.type IN ('asset', 'expense') THEN 'Dr' ELSE 'Cr' END AS normal_side
FROM dbo.ledger_accounts a
LEFT JOIN dbo.journal_lines jl ON jl.account_id = a.id
GROUP BY a.code, a.name, a.type;
GO

/* ---------------------------------------------------------------------------
   vw_journal - every posting, one row per line
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_journal
AS
SELECT
    je.entry_no,
    je.entry_date,
    je.source,
    je.narration,
    l.loan_no,
    s.account_no                    AS savings_account_no,
    br.name                         AS branch,
    a.code                          AS account_code,
    a.name                          AS account,
    a.type                          AS account_type,
    jl.debit,
    jl.credit,
    jl.description                  AS line_description,
    u.username                      AS posted_by,
    je.created_at
FROM dbo.journal_lines jl
JOIN dbo.journal_entries je ON je.id = jl.entry_id
JOIN dbo.ledger_accounts a  ON a.id  = jl.account_id
LEFT JOIN dbo.loans     l   ON l.id  = je.loan_id
LEFT JOIN dbo.savings_transactions st ON st.id = je.savings_transaction_id
LEFT JOIN dbo.savings_accounts s ON s.id = st.account_id
LEFT JOIN dbo.branches  br  ON br.id = je.branch_id
LEFT JOIN dbo.users     u   ON u.id  = je.posted_by_id;
GO

/* ---------------------------------------------------------------------------
   vw_loan_charges - every fee raised against a loan, and how it was recovered
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_loan_charges
AS
SELECT
    lc.id                           AS loan_charge_id,
    l.loan_no,
    b.first_name + ' ' + b.last_name AS borrower,
    lc.name                         AS charge,
    c.code                          AS charge_code,
    lc.amount,
    lc.applied_on,
    lc.collection,
    i.number                        AS instalment_no,
    p.name                          AS product
FROM dbo.loan_charges lc
JOIN dbo.loans         l ON l.id = lc.loan_id
JOIN dbo.borrowers     b ON b.id = l.borrower_id
JOIN dbo.loan_products p ON p.id = l.product_id
LEFT JOIN dbo.charges  c ON c.id = lc.charge_id
LEFT JOIN dbo.instalments i ON i.id = lc.instalment_id;
GO

/* ---------------------------------------------------------------------------
   usp_reconcile_ledger - the check worth running before any board pack.

   Every account below carries a balance that is claimed to equal something
   counted elsewhere. These are the checks with teeth: a balanced trial balance
   and a balance sheet that adds up both follow automatically from entries where
   every debit has a credit, so neither is evidence of anything. These can break,
   and a migration, a hand-edit in SSMS, or a service that moves a balance without
   posting will all show up here.

   Each ledger figure is read with a SCALAR SUBQUERY wrapped in ISNULL, not with
   a CROSS JOIN onto vw_trial_balance. A CROSS JOIN yields zero rows when the
   account does not exist - on a database that has migrated but not yet run
   Rebuild, the check would silently vanish instead of failing. A check that can
   disappear is worse than no check.
   --------------------------------------------------------------------------- */
CREATE OR ALTER PROCEDURE dbo.usp_reconcile_ledger
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @results TABLE (
        check_name   nvarchar(60),
        ledger       decimal(18, 2),
        sub_ledger   decimal(18, 2),
        difference   decimal(18, 2),
        result       nvarchar(10));

    DECLARE @gl decimal(18, 2), @book decimal(18, 2);

    /* ---- the loan book. A loan, savings account or facility in another currency
       is carried at its booked rate (fx_rate, 1 in the organisation's currency),
       each rounded to the cent on its own row, which is exactly what the ledger
       holds; see backend/core/services/fx.py. */
    SELECT @gl = ISNULL((SELECT balance FROM dbo.vw_trial_balance WHERE code = '1100'), 0),
           @book = ISNULL((SELECT SUM(ROUND(principal_outstanding * fx_rate, 2)) FROM dbo.loans
                           WHERE status = 'active'), 0);
    INSERT INTO @results VALUES ('Loans receivable', @gl, @book, @gl - @book,
        CASE WHEN @gl = @book THEN 'OK' ELSE 'MISMATCH' END);

    SELECT @gl = ISNULL((SELECT balance FROM dbo.vw_trial_balance WHERE code = '1300'), 0),
           @book = ISNULL((SELECT SUM(ROUND(penalties_outstanding * fx_rate, 2)) FROM dbo.loans
                           WHERE status = 'active'), 0);
    INSERT INTO @results VALUES ('Penalties receivable', @gl, @book, @gl - @book,
        CASE WHEN @gl = @book THEN 'OK' ELSE 'MISMATCH' END);

    SELECT @gl = ISNULL((SELECT balance FROM dbo.vw_trial_balance WHERE code = '1400'), 0),
           @book = ISNULL((SELECT SUM(ROUND(charges_outstanding * fx_rate, 2)) FROM dbo.loans
                           WHERE status = 'active'), 0);
    INSERT INTO @results VALUES ('Charges receivable', @gl, @book, @gl - @book,
        CASE WHEN @gl = @book THEN 'OK' ELSE 'MISMATCH' END);

    /* 1900 Provision for credit losses. The unary minus is required: 1900 is typed
       ASSET (it is a contra-asset), so vw_trial_balance reports it as debit minus
       credit and the provision stands on the credit side. The sub-ledger sums ALL
       loans, not just active ones, because a closed loan keeps its provision until
       a run sweeps it. */
    SELECT @gl = -ISNULL((SELECT balance FROM dbo.vw_trial_balance WHERE code = '1900'), 0),
           @book = ISNULL((SELECT SUM(provision_held) FROM dbo.loans), 0);
    INSERT INTO @results VALUES ('Provision for credit losses', @gl, @book, @gl - @book,
        CASE WHEN @gl = @book THEN 'OK' ELSE 'MISMATCH' END);

    /* ---- the savings book */
    SELECT @gl = ISNULL((SELECT balance FROM dbo.vw_trial_balance WHERE code = '2000'), 0),
           @book = ISNULL((SELECT SUM(ROUND(balance * fx_rate, 2)) FROM dbo.savings_accounts), 0);
    INSERT INTO @results VALUES ('Client funds payable', @gl, @book, @gl - @book,
        CASE WHEN @gl = @book THEN 'OK' ELSE 'MISMATCH' END);

    /* ---- funder borrowings */
    SELECT @gl = ISNULL((SELECT balance FROM dbo.vw_trial_balance WHERE code = '2100'), 0),
           @book = ISNULL((SELECT SUM(ROUND(principal_outstanding * fx_rate, 2))
                           FROM dbo.funding_facilities), 0);
    INSERT INTO @results VALUES ('Funder borrowings', @gl, @book, @gl - @book,
        CASE WHEN @gl = @book THEN 'OK' ELSE 'MISMATCH' END);

    SELECT @gl = ISNULL((SELECT balance FROM dbo.vw_trial_balance WHERE code = '2110'), 0),
           @book = ISNULL((SELECT SUM(ROUND(interest_accrued * fx_rate, 2))
                           FROM dbo.funding_facilities), 0);
    INSERT INTO @results VALUES ('Accrued borrowing interest', @gl, @book, @gl - @book,
        CASE WHEN @gl = @book THEN 'OK' ELSE 'MISMATCH' END);

    /* ---- equity. A reversed movement and its reversal net to zero in the ledger,
       so the sub-ledger total must exclude both. */
    SELECT @gl = ISNULL((SELECT balance FROM dbo.vw_trial_balance WHERE code = '3100'), 0),
           @book = ISNULL((SELECT SUM(CASE WHEN txn_type = 'injection' THEN amount
                                           ELSE -amount END)
                           FROM dbo.capital_transactions
                           WHERE reversed = 0
                             AND txn_type IN ('injection', 'return_of_capital')), 0);
    INSERT INTO @results VALUES ('Share capital', @gl, @book, @gl - @book,
        CASE WHEN @gl = @book THEN 'OK' ELSE 'MISMATCH' END);

    /* 3200 Distributions is typed EQUITY and carries a debit balance, so
       vw_trial_balance reports it negative. */
    SELECT @gl = -ISNULL((SELECT balance FROM dbo.vw_trial_balance WHERE code = '3200'), 0),
           @book = ISNULL((SELECT SUM(amount) FROM dbo.capital_transactions
                           WHERE reversed = 0 AND txn_type = 'dividend'), 0);
    INSERT INTO @results VALUES ('Distributions to shareholders', @gl, @book, @gl - @book,
        CASE WHEN @gl = @book THEN 'OK' ELSE 'MISMATCH' END);

    /* ---- double entry itself */
    INSERT INTO @results
    SELECT 'Debits equal credits', d.total, c.total, d.total - c.total,
           CASE WHEN d.total = c.total THEN 'OK' ELSE 'MISMATCH' END
    FROM (SELECT total = ISNULL(SUM(debit), 0)  FROM dbo.journal_lines) d
    CROSS JOIN (SELECT total = ISNULL(SUM(credit), 0) FROM dbo.journal_lines) c;

    SELECT * FROM @results ORDER BY CASE result WHEN 'MISMATCH' THEN 0 ELSE 1 END, check_name;

    IF EXISTS (SELECT 1 FROM @results WHERE result = 'MISMATCH')
        RAISERROR('The ledger does not agree with the sub-ledgers. Investigate before reporting.',
                  16, 1);
END
GO

/* ---------------------------------------------------------------------------
   The funding book: where the money to lend came from.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_funding_book AS
SELECT f.facility_no,
       f.funder_name,
       f.name                        AS line_name,
       state                         = CASE WHEN f.closed_on IS NOT NULL THEN 'closed'
                                            WHEN f.is_revolving = 1 THEN 'revolving'
                                            ELSE 'term' END,
       /* every amount below is in the facility's currency; blank is the
          organisation's, and booked_rate is what 2100 and 2110 carry it at */
       f.currency,
       f.fx_rate                     AS booked_rate,
       f.facility_limit,
       f.principal_outstanding,
       f.interest_accrued,
       total_drawn                   = ISNULL(t.drawn, 0),
       total_repaid                  = ISNULL(t.repaid, 0),
       total_interest_accrued        = ISNULL(t.accrued, 0),
       total_interest_paid           = ISNULL(t.interest_paid, 0),
       total_fees                    = ISNULL(t.fees, 0),
       /* The same rule as FundingFacility.available: a revolving facility measures
          against what is outstanding now, a term facility against everything drawn. */
       available                     = CASE WHEN f.is_revolving = 1
                                            THEN f.facility_limit - f.principal_outstanding
                                            ELSE f.facility_limit - ISNULL(t.drawn, 0) END,
       utilisation_pct               = CASE WHEN f.facility_limit > 0
                                            THEN CAST(f.principal_outstanding * 100.0
                                                      / f.facility_limit AS decimal(6, 2))
                                            ELSE 0 END,
       f.interest_rate_pct_pa,
       f.start_date,
       f.maturity_date,
       days_to_maturity              = DATEDIFF(day, CAST(GETDATE() AS date), f.maturity_date),
       f.last_accrual_date,
       f.closed_on,
       f.repayment_terms,
       branch                        = b.name,
       loans_funded                  = (SELECT COUNT(*) FROM dbo.loans l
                                        WHERE l.funding_facility_id = f.id)
FROM dbo.funding_facilities f
LEFT JOIN dbo.branches b ON b.id = f.branch_id
OUTER APPLY (
    /* ELSE 0 rather than an implicit NULL: SUM over NULLs raises "Null value is
       eliminated by an aggregate", which is noise in the output of every script
       that touches this view. */
    SELECT drawn         = SUM(CASE WHEN ft.txn_type = 'drawdown' THEN ft.amount ELSE 0 END),
           repaid        = SUM(CASE WHEN ft.txn_type = 'repayment' THEN ft.amount ELSE 0 END),
           accrued       = SUM(CASE WHEN ft.txn_type = 'interest_accrual' THEN ft.amount ELSE 0 END),
           interest_paid = SUM(CASE WHEN ft.txn_type = 'interest_payment' THEN ft.amount ELSE 0 END),
           fees          = SUM(CASE WHEN ft.txn_type = 'fee' THEN ft.amount ELSE 0 END)
    FROM dbo.facility_transactions ft
    WHERE ft.facility_id = f.id AND ft.reversed = 0 AND ft.txn_type <> 'reversal'
) t;
GO

/* The funder cash book, shaped like vw_savings_transactions. */
CREATE OR ALTER VIEW dbo.vw_funding_transactions AS
SELECT f.facility_no,
       f.funder_name,
       ft.txn_date,
       ft.txn_type,
       amount_in   = CASE WHEN ft.txn_type = 'drawdown' THEN ft.amount ELSE 0 END,
       amount_out  = CASE WHEN ft.txn_type IN ('repayment', 'interest_payment', 'fee')
                          THEN ft.amount ELSE 0 END,
       ft.amount,
       ft.principal_after,
       ft.accrued_after,
       ft.method,
       ft.reference,
       ft.narration,
       ft.reversed,
       entry_no    = je.entry_no,
       posted_by   = u.full_name,
       ft.created_at
FROM dbo.facility_transactions ft
JOIN dbo.funding_facilities f ON f.id = ft.facility_id
LEFT JOIN dbo.journal_entries je ON je.facility_transaction_id = ft.id
LEFT JOIN dbo.users u ON u.id = ft.posted_by_id;
GO

/* ---------------------------------------------------------------------------
   vw_balance_sheet - assets, liabilities and equity, with retained earnings
   derived as income less expense since inception. Nothing posts to 3000, so
   without the derivation the surplus would be missing from equity.

   Agrees with core.services.ledger.balance_sheet() on every account that has
   movement. It differs on accounts with none: vw_trial_balance LEFT JOINs
   journal_lines and so emits a zero row for every account in the chart, while
   the Python version drops them. The HAVING below makes the two match.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_balance_sheet AS
WITH tb AS (
    SELECT a.code, a.name, a.type,
           debit  = ISNULL(SUM(jl.debit), 0),
           credit = ISNULL(SUM(jl.credit), 0)
    FROM dbo.ledger_accounts a
    LEFT JOIN dbo.journal_lines jl ON jl.account_id = a.id
    GROUP BY a.code, a.name, a.type
    HAVING ISNULL(SUM(jl.debit), 0) <> 0 OR ISNULL(SUM(jl.credit), 0) <> 0
),
posted AS (
    SELECT code, name, type,
           balance = CASE WHEN type IN ('asset', 'expense') THEN debit - credit
                          ELSE credit - debit END
    FROM tb
)
SELECT section = 'assets',      code, name, balance, is_derived = CAST(0 AS bit)
FROM posted WHERE type = 'asset'
UNION ALL
SELECT section = 'liabilities', code, name, balance, is_derived = CAST(0 AS bit)
FROM posted WHERE type = 'liability'
UNION ALL
/* 3000 is excluded from the posted equity rows and folded into the derived figure
   below, so a manual posting to it can never be counted twice. */
SELECT section = 'equity',      code, name, balance, is_derived = CAST(0 AS bit)
FROM posted WHERE type = 'equity' AND code <> '3000'
UNION ALL
SELECT section    = 'equity',
       code       = '3000',
       name       = 'Retained earnings',
       balance    = ISNULL((SELECT SUM(balance) FROM posted WHERE type = 'income'), 0)
                  - ISNULL((SELECT SUM(balance) FROM posted WHERE type = 'expense'), 0)
                  + ISNULL((SELECT SUM(balance) FROM posted WHERE code = '3000'), 0),
       is_derived = CAST(1 AS bit);
GO

PRINT 'Savings, group, ledger and charge views are in place.';
GO

/* Smoke test */
SELECT TOP 5 * FROM dbo.vw_savings_book;
SELECT * FROM dbo.vw_group_standing;
SELECT * FROM dbo.vw_trial_balance ORDER BY code;
SELECT * FROM dbo.vw_funding_book;
SELECT section, code, name, balance, is_derived FROM dbo.vw_balance_sheet
ORDER BY CASE section WHEN 'assets' THEN 1 WHEN 'liabilities' THEN 2 ELSE 3 END, code;
EXEC dbo.usp_reconcile_ledger;
GO
