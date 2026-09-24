/* ============================================================================
   Loan Management System - optional SQL Server login for the Django app
   Run in SSMS against master, as a sysadmin.

   You only need this if you do NOT want to use Windows integrated
   authentication. The default backend/.env uses a trusted connection, which
   needs no login at all.

   To use SQL authentication instead:
     1. Server must be in Mixed Mode:
        SSMS > right-click server > Properties > Security > "SQL Server and
        Windows Authentication mode", then restart the SQL Server service.
     2. Change the password below.
     3. Run this script.
     4. In backend/.env set:
          DB_TRUSTED_CONNECTION=0
          DB_USER=lms_app
          DB_PASSWORD=<the password you set>
   ============================================================================ */
USE master;
GO

DECLARE @password sysname = N'Ch4nge-Me-In-Production!';

IF SUSER_ID(N'lms_app') IS NULL
BEGIN
    DECLARE @sql nvarchar(max) = N'CREATE LOGIN lms_app WITH PASSWORD = '
        + QUOTENAME(@password, '''')
        + N', DEFAULT_DATABASE = LMS, CHECK_POLICY = ON;';
    EXEC sp_executesql @sql;
    PRINT 'Login lms_app created.';
END
ELSE
    PRINT 'Login lms_app already exists.';
GO

USE LMS;
GO

IF DATABASE_PRINCIPAL_ID(N'lms_app') IS NULL
BEGIN
    CREATE USER lms_app FOR LOGIN lms_app;
    PRINT 'User lms_app created in LMS.';
END
GO

/* db_owner is required for "manage.py migrate" (it creates and alters tables).
   Once the schema is stable you can drop it back to the two data roles below. */
ALTER ROLE db_owner ADD MEMBER lms_app;
GO

/* ---------------------------------------------------------------------------
   Least-privilege alternative, for a production server where migrations are
   run by a separate deployment account. Uncomment to apply.
   --------------------------------------------------------------------------- */
-- ALTER ROLE db_owner        DROP MEMBER lms_app;
-- ALTER ROLE db_datareader   ADD  MEMBER lms_app;
-- ALTER ROLE db_datawriter   ADD  MEMBER lms_app;
-- GO

/* ---------------------------------------------------------------------------
   A read-only login for analysts who should see the reporting views in SSMS
   but must never post a transaction.
   --------------------------------------------------------------------------- */
USE master;
GO
IF SUSER_ID(N'lms_readonly') IS NULL
BEGIN
    CREATE LOGIN lms_readonly WITH PASSWORD = N'Ch4nge-Me-Too!', DEFAULT_DATABASE = LMS;
    PRINT 'Login lms_readonly created.';
END
GO
USE LMS;
GO
IF DATABASE_PRINCIPAL_ID(N'lms_readonly') IS NULL
    CREATE USER lms_readonly FOR LOGIN lms_readonly;
GO
ALTER ROLE db_datareader ADD MEMBER lms_readonly;
GO

SELECT dp.name AS principal, dp.type_desc, r.name AS role_name
FROM sys.database_principals dp
LEFT JOIN sys.database_role_members rm ON rm.member_principal_id = dp.principal_id
LEFT JOIN sys.database_principals  r  ON r.principal_id = rm.role_principal_id
WHERE dp.name IN (N'lms_app', N'lms_readonly');
GO
