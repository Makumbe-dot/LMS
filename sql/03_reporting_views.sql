/* ============================================================================
   Loan Management System - reporting views and helper objects
   Run in SSMS against the LMS database AFTER "python manage.py migrate", because
   these objects read the tables that Django migrations create.

   These are for analysts working directly in SSMS (and for Excel / Power BI
   connections). The application itself does not depend on them, so dropping
   them never breaks the app.
   ============================================================================ */
USE LMS;
GO

/* ---------------------------------------------------------------------------
   vw_loan_book - one row per disbursed loan with live balances and arrears
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
    l.status,
    l.principal,
    l.interest_rate_pct             AS rate_pct_per_month,
    l.term_months,
    l.instalment_amount,
    l.admin_fee,
    l.insurance_fee,
    l.total_interest,
    l.disbursement_date,
    l.first_instalment_date,
    l.maturity_date,
    l.principal_outstanding,
    l.interest_outstanding,
    l.penalties_outstanding,
    l.principal_outstanding + l.interest_outstanding + l.penalties_outstanding
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
JOIN dbo.borrowers      b ON b.id = l.borrower_id
JOIN dbo.loan_products  p ON p.id = l.product_id
OUTER APPLY (
    SELECT
        arrears_amount  = ISNULL(SUM(i.principal_due + i.interest_due + i.penalty_due
                                   - i.principal_paid - i.interest_paid - i.penalty_paid), 0),
        days_in_arrears = ISNULL(MAX(DATEDIFF(day, i.due_date, CAST(GETDATE() AS date))), 0)
    FROM dbo.instalments i
    WHERE i.loan_id = l.id
      AND i.due_date < CAST(GETDATE() AS date)
      AND (i.principal_due + i.interest_due + i.penalty_due
         - i.principal_paid - i.interest_paid - i.penalty_paid) > 0
) a
WHERE l.status IN ('active', 'closed', 'written_off');
GO

/* ---------------------------------------------------------------------------
   vw_arrears - loans currently in arrears, worst first (portfolio at risk)
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_arrears
AS
SELECT TOP 100 PERCENT
    loan_id, loan_no, borrower, phone, employer, product,
    days_in_arrears, arrears_bucket, arrears_amount,
    penalties_outstanding, principal_outstanding, total_outstanding
FROM dbo.vw_loan_book
WHERE status = 'active' AND arrears_amount > 0
ORDER BY days_in_arrears DESC;
GO

/* ---------------------------------------------------------------------------
   vw_collections_due - every scheduled instalment on a running loan
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_collections_due
AS
SELECT
    i.loan_id,
    l.loan_no,
    b.first_name + ' ' + b.last_name AS borrower,
    b.employer,
    b.payday,
    i.number                        AS instalment_no,
    i.due_date,
    i.principal_due + i.interest_due + i.penalty_due       AS amount_due,
    i.principal_paid + i.interest_paid + i.penalty_paid    AS paid,
    (i.principal_due + i.interest_due + i.penalty_due)
  - (i.principal_paid + i.interest_paid + i.penalty_paid)  AS balance,
    i.status,
    i.paid_date
FROM dbo.instalments i
JOIN dbo.loans      l ON l.id = i.loan_id
JOIN dbo.borrowers  b ON b.id = l.borrower_id
WHERE l.status = 'active';
GO

/* ---------------------------------------------------------------------------
   vw_transactions - the cash book, with borrower and loan attached
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_transactions
AS
SELECT
    t.id                            AS transaction_id,
    t.txn_date,
    l.loan_no,
    b.first_name + ' ' + b.last_name AS borrower,
    t.txn_type,
    t.amount,
    t.principal_component,
    t.interest_component,
    t.penalty_component,
    t.method,
    t.reference,
    t.narration,
    t.[reversed],
    t.reversal_of_id,
    u.username                      AS posted_by,
    t.created_at
FROM dbo.transactions t
JOIN dbo.loans     l ON l.id = t.loan_id
JOIN dbo.borrowers b ON b.id = l.borrower_id
LEFT JOIN dbo.users u ON u.id = t.posted_by_id;
GO

/* ---------------------------------------------------------------------------
   vw_portfolio_summary - one row, the numbers the dashboard leads with
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW dbo.vw_portfolio_summary
AS
SELECT
    as_of                   = CAST(GETDATE() AS date),
    borrowers               = (SELECT COUNT(*) FROM dbo.borrowers),
    active_loans            = (SELECT COUNT(*) FROM dbo.loans WHERE status = 'active'),
    pending_applications    = (SELECT COUNT(*) FROM dbo.loans WHERE status = 'pending'),
    principal_outstanding   = (SELECT ISNULL(SUM(principal_outstanding), 0) FROM dbo.loans WHERE status = 'active'),
    portfolio_outstanding   = (SELECT ISNULL(SUM(principal_outstanding + interest_outstanding + penalties_outstanding), 0)
                               FROM dbo.loans WHERE status = 'active'),
    par_30_amount           = (SELECT ISNULL(SUM(principal_outstanding), 0) FROM dbo.vw_loan_book
                               WHERE status = 'active' AND days_in_arrears > 30),
    written_off_amount      = (SELECT ISNULL(SUM(amount), 0) FROM dbo.transactions WHERE txn_type = 'write_off');
GO

/* ---------------------------------------------------------------------------
   usp_arrears_ageing - principal outstanding by ageing bucket, as at a date
   EXEC dbo.usp_arrears_ageing;
   EXEC dbo.usp_arrears_ageing @as_of = '2026-06-30';
   --------------------------------------------------------------------------- */
CREATE OR ALTER PROCEDURE dbo.usp_arrears_ageing
    @as_of date = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET @as_of = ISNULL(@as_of, CAST(GETDATE() AS date));

    WITH loan_arrears AS (
        SELECT
            l.id,
            l.principal_outstanding,
            days = ISNULL((
                SELECT MAX(DATEDIFF(day, i.due_date, @as_of))
                FROM dbo.instalments i
                WHERE i.loan_id = l.id
                  AND i.due_date < @as_of
                  AND (i.principal_due + i.interest_due + i.penalty_due
                     - i.principal_paid - i.interest_paid - i.penalty_paid) > 0
            ), 0)
        FROM dbo.loans l
        WHERE l.status = 'active'
    )
    SELECT
        bucket = CASE
            WHEN days = 0        THEN 'current'
            WHEN days <=  30     THEN '1-30'
            WHEN days <=  60     THEN '31-60'
            WHEN days <=  90     THEN '61-90'
            WHEN days <= 180     THEN '91-180'
            ELSE '180+' END,
        loans                 = COUNT(*),
        principal_outstanding = SUM(principal_outstanding)
    FROM loan_arrears
    GROUP BY CASE
            WHEN days = 0        THEN 'current'
            WHEN days <=  30     THEN '1-30'
            WHEN days <=  60     THEN '31-60'
            WHEN days <=  90     THEN '61-90'
            WHEN days <= 180     THEN '91-180'
            ELSE '180+' END
    ORDER BY MIN(days);
END
GO

/* ---------------------------------------------------------------------------
   Supporting indexes. Django creates indexes for the foreign keys and for the
   fields marked db_index; these two cover the reporting access paths above.
   --------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_instalments_due_unpaid' AND object_id = OBJECT_ID(N'dbo.instalments'))
    CREATE INDEX IX_instalments_due_unpaid
        ON dbo.instalments (due_date, loan_id)
        INCLUDE (principal_due, interest_due, penalty_due, principal_paid, interest_paid, penalty_paid, status);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_transactions_type_date' AND object_id = OBJECT_ID(N'dbo.transactions'))
    CREATE INDEX IX_transactions_type_date
        ON dbo.transactions (txn_type, txn_date)
        INCLUDE (amount, principal_component, [reversed]);
GO

PRINT 'Reporting views, stored procedure and indexes are in place.';
GO

/* Smoke test */
SELECT * FROM dbo.vw_portfolio_summary;
EXEC dbo.usp_arrears_ageing;
GO
