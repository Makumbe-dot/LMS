/* ============================================================================
   Loan Management System - database creation
   Run this in SQL Server Management Studio against the master database.

   Creates the LMS database only. All tables, keys and indexes are created by
   Django migrations (python manage.py migrate) so that the ORM and the
   physical schema never drift apart.
   ============================================================================ */
USE master;
GO

IF DB_ID(N'LMS') IS NULL
BEGIN
    PRINT 'Creating database LMS...';
    CREATE DATABASE LMS;
END
ELSE
    PRINT 'Database LMS already exists - nothing to do.';
GO

/* Read-committed snapshot keeps the reporting screens from blocking the
   teller posting repayments, and vice versa. */
ALTER DATABASE LMS SET ALLOW_SNAPSHOT_ISOLATION ON;
GO
ALTER DATABASE LMS SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE;
GO

USE LMS;
GO

SELECT
    name                    AS database_name,
    collation_name,
    snapshot_isolation_state_desc,
    is_read_committed_snapshot_on,
    recovery_model_desc
FROM sys.databases
WHERE name = N'LMS';
GO
