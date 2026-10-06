#!/bin/sh
# One-shot set-up run by the `db-init` service before the backend starts. The
# container equivalent of sql/01_create_database.sql and sql/02_app_login.sql:
#
#   1. creates the database if it is missing, with read-committed snapshot on;
#   2. creates (or re-passwords) the app's own SQL login, unless DB_USER is sa;
#   3. hands the shared backups volume to uid 10001, which both the SQL Server
#      and the app containers run as.
#
# Every step is idempotent, so it runs on every `docker compose up`. Tables are
# not created here: Django migrations own the schema.
set -eu

: "${MSSQL_SA_PASSWORD:?MSSQL_SA_PASSWORD is not set}"
DB_HOST="${DB_HOST:-sqlserver}"
DB_PORT="${DB_PORT:-1433}"
DB_NAME="${DB_NAME:-LMS}"
DB_USER="${DB_USER:-sa}"

# T-SQL string literals double their single quotes.
sql_quote() { printf "%s" "$1" | sed "s/'/''/g"; }

run() {
    sqlcmd -S "$DB_HOST,$DB_PORT" -U sa -P "$MSSQL_SA_PASSWORD" -C -b -h -1 -Q "SET NOCOUNT ON; $1"
}

echo "Waiting for SQL Server at $DB_HOST:$DB_PORT..."
i=0
until run "SELECT 1" >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -ge 60 ] && { echo "SQL Server did not answer within two minutes." >&2; exit 1; }
    sleep 2
done

echo "Ensuring database [$DB_NAME] exists..."
run "IF DB_ID(N'$DB_NAME') IS NULL
     BEGIN
         CREATE DATABASE [$DB_NAME];
         PRINT 'Created database $DB_NAME.';
     END
     ELSE PRINT 'Database $DB_NAME already exists.';"

# Only switched on when off: WITH ROLLBACK IMMEDIATE would otherwise drop the
# backend's connections on every restart of the stack.
run "IF (SELECT snapshot_isolation_state FROM sys.databases WHERE name = N'$DB_NAME') = 0
         ALTER DATABASE [$DB_NAME] SET ALLOW_SNAPSHOT_ISOLATION ON;
     IF (SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = N'$DB_NAME') = 0
         ALTER DATABASE [$DB_NAME] SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE;"

if [ "$DB_USER" != "sa" ]; then
    : "${DB_PASSWORD:?DB_PASSWORD is not set}"
    pw=$(sql_quote "$DB_PASSWORD")
    echo "Ensuring login [$DB_USER] exists with the password from .env..."
    # db_owner because the backend runs `manage.py migrate` on start; see
    # sql/02_app_login.sql for the least-privilege alternative.
    run "IF SUSER_ID(N'$DB_USER') IS NULL
             CREATE LOGIN [$DB_USER] WITH PASSWORD = N'$pw', DEFAULT_DATABASE = [$DB_NAME];
         ELSE
             ALTER LOGIN [$DB_USER] WITH PASSWORD = N'$pw';"
    run "USE [$DB_NAME];
         IF DATABASE_PRINCIPAL_ID(N'$DB_USER') IS NULL
             CREATE USER [$DB_USER] FOR LOGIN [$DB_USER];
         ALTER ROLE db_owner ADD MEMBER [$DB_USER];"
fi

if [ -d /backups ]; then
    chown 10001:10001 /backups
fi

echo "Database ready."
