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
   usp_reconcile_ledger - the check worth running before any board pack:
   does the ledger still agree with the loan and savings books?
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

    /* 1100 Loans receivable vs principal outstanding on active loans */
    INSERT INTO @results
    SELECT 'Loans receivable',
           gl.balance,
           book.total,
           gl.balance - book.total,
           CASE WHEN gl.balance = book.total THEN 'OK' ELSE 'MISMATCH' END
    FROM (SELECT balance FROM dbo.vw_trial_balance WHERE code = '1100') gl
    CROSS JOIN (SELECT total = ISNULL(SUM(principal_outstanding), 0)
                FROM dbo.loans WHERE status = 'active') book;

    /* 1300 Penalties receivable */
    INSERT INTO @results
    SELECT 'Penalties receivable', gl.balance, book.total, gl.balance - book.total,
           CASE WHEN gl.balance = book.total THEN 'OK' ELSE 'MISMATCH' END
    FROM (SELECT balance FROM dbo.vw_trial_balance WHERE code = '1300') gl
    CROSS JOIN (SELECT total = ISNULL(SUM(penalties_outstanding), 0)
                FROM dbo.loans WHERE status = 'active') book;

    /* 1400 Charges receivable */
    INSERT INTO @results
    SELECT 'Charges receivable', gl.balance, book.total, gl.balance - book.total,
           CASE WHEN gl.balance = book.total THEN 'OK' ELSE 'MISMATCH' END
    FROM (SELECT balance FROM dbo.vw_trial_balance WHERE code = '1400') gl
    CROSS JOIN (SELECT total = ISNULL(SUM(charges_outstanding), 0)
                FROM dbo.loans WHERE status = 'active') book;

    /* 2000 Client funds payable vs the savings book */
    INSERT INTO @results
    SELECT 'Client funds payable', gl.balance, book.total, gl.balance - book.total,
           CASE WHEN gl.balance = book.total THEN 'OK' ELSE 'MISMATCH' END
    FROM (SELECT balance FROM dbo.vw_trial_balance WHERE code = '2000') gl
    CROSS JOIN (SELECT total = ISNULL(SUM(balance), 0) FROM dbo.savings_accounts) book;

    /* Double entry itself */
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

PRINT 'Savings, group, ledger and charge views are in place.';
GO

/* Smoke test */
SELECT TOP 5 * FROM dbo.vw_savings_book;
SELECT * FROM dbo.vw_group_standing;
SELECT * FROM dbo.vw_trial_balance ORDER BY code;
EXEC dbo.usp_reconcile_ledger;
GO
