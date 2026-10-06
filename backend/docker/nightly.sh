#!/bin/bash
# The nightly batch, for the Docker setup: the same steps, in the same order and
# on the same days, as scripts/run_nightly_jobs.ps1, plus the backup that
# scripts/backup_database.ps1 takes. Run by the `jobs` service:
#
#   docker compose run --rm jobs
#   docker compose run --rm jobs --as-of 2026-09-30 --skip-backup
#
# Every step is idempotent, so a repeated or retried run changes nothing extra.
# Output goes to stdout for whatever schedules it (cron, a systemd timer, Task
# Scheduler) to keep. The exit code is the number of failed steps.
set -uo pipefail

AS_OF=""
SKIP_BACKUP=0
SKIP_SAVINGS=0
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

while [ $# -gt 0 ]; do
    case "$1" in
        --as-of) AS_OF="$2"; shift 2 ;;
        --skip-backup) SKIP_BACKUP=1; shift ;;
        --skip-savings-interest) SKIP_SAVINGS=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

cd "$(dirname "$0")/.."
MEDIA_DIR="${MEDIA_ROOT:-$PWD/media}"
failures=0
log() { echo "$(date +%H:%M:%S)  $*"; }

# Run one step, indent its output under the START line, count a failure.
step() {
    local name="$1"; shift
    log "START  $name"
    if "$@" 2>&1 | sed 's/^/       /'; then
        log "OK     $name"
    else
        log "FAILED $name : exit code ${PIPESTATUS[0]}"
        failures=$((failures + 1))
    fi
}

AS_OF_ARGS=()
[ -n "$AS_OF" ] && AS_OF_ARGS=(--as-of "$AS_OF")
monthly() { [ "$(date +%d)" = "01" ] || [ -n "$AS_OF" ]; }

# A full, checksummed backup into the shared backups volume, read back by SQL
# Server to prove it restores, then the borrower documents, then pruning. The
# backup runs as sa because RESTORE VERIFYONLY needs CREATE DATABASE rights.
backup() {
    local db="${DB_NAME:-LMS}" stamp file edition compression=""
    local sql=(sqlcmd -S "${DB_HOST:-sqlserver},${DB_PORT:-1433}" -U sa -P "$MSSQL_SA_PASSWORD" -C -b)
    stamp=$(date +%Y%m%d-%H%M%S)
    file="/backups/$db-$stamp.bak"

    # Express Edition cannot compress a backup, and asking it to is a hard error.
    edition=$("${sql[@]}" -h -1 -W -Q "SET NOCOUNT ON; SELECT CAST(SERVERPROPERTY('Edition') AS nvarchar(200));") || return 1
    [[ "$edition" == *Express* ]] || compression="COMPRESSION, "

    echo "Backing up $db to $file ($edition)"
    "${sql[@]}" -Q "BACKUP DATABASE [$db] TO DISK = N'$file'
        WITH FORMAT, INIT, ${compression}CHECKSUM, STATS = 25, NAME = N'$db full backup $stamp';" || return 1
    "${sql[@]}" -Q "RESTORE VERIFYONLY FROM DISK = N'$file' WITH CHECKSUM;" || {
        echo "RESTORE VERIFYONLY failed: this backup is NOT restorable"; return 1; }

    # Borrower documents live on disk, not in the database.
    if [ -n "$(ls -A "$MEDIA_DIR" 2>/dev/null)" ]; then
        tar -czf "/backups/media-$stamp.tar.gz" -C "$MEDIA_DIR" . && echo "Wrote media-$stamp.tar.gz"
    else
        echo "No borrower documents; nothing to copy."
    fi

    if [ "$RETENTION_DAYS" -gt 0 ]; then
        find /backups -maxdepth 1 -type f \( -name '*.bak' -o -name 'media-*.tar.gz' \) \
            -mtime "+$RETENTION_DAYS" -print -delete | sed 's/^/Removed expired backup /'
    fi
}

log "===== nightly batch starting ====="

# --skip-closed unconditionally: a month closed at 09:00 on the 1st must not
# log a FAILED step at 22:00 for a date nobody can post to any more.
step "penalty accrual" python manage.py run_penalties --skip-closed "${AS_OF_ARGS[@]}"
step "borrower reminders" python manage.py send_reminders --send "${AS_OF_ARGS[@]}"
# Housekeeping; nothing breaks if this never runs.
step "prune expired token revocations" python manage.py prune_tokens

# The three monthly steps run on the 1st (or whenever --as-of is given).
if [ "$SKIP_SAVINGS" -eq 0 ]; then
    if monthly; then
        step "savings interest" python manage.py run_savings_interest --skip-closed "${AS_OF_ARGS[@]}"
    else
        log "SKIP   savings interest (runs on the 1st)"
    fi
fi

if monthly; then
    step "borrowing interest" python manage.py accrue_borrowing_interest --skip-closed "${AS_OF_ARGS[@]}"
else
    log "SKIP   borrowing interest (runs on the 1st)"
fi

# The provision is booked for the month that just closed, so on the 1st it is
# dated yesterday.
if monthly; then
    step "provision run" python manage.py run_provisions --as-of "${AS_OF:-$(date -d yesterday +%F)}"
else
    log "SKIP   provision run (runs on the 1st)"
fi

if [ "$SKIP_BACKUP" -eq 0 ]; then
    step "database backup" backup
fi

log "===== nightly batch finished, $failures failure(s) ====="
exit "$failures"
