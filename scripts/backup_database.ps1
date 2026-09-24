<#
.SYNOPSIS
    Back up the LMS database and the uploaded borrower documents.

.DESCRIPTION
    Takes a compressed, checksummed full backup of the LMS database, copies the
    media directory alongside it, and deletes anything older than the retention
    window. Safe to run repeatedly; each run writes its own timestamped file.

    A backup nobody has restored is a guess, not a backup. Use -Verify to have
    SQL Server read the file back and confirm it is restorable.

.EXAMPLE
    .\backup_database.ps1
    .\backup_database.ps1 -BackupRoot D:\backups -RetentionDays 30 -Verify
#>
[CmdletBinding()]
param(
    [string] $Server       = 'localhost\SQLEXPRESS',
    [string] $Database     = 'LMS',
    [string] $BackupRoot,
    [string] $MediaBackupRoot,
    [string] $MediaPath,
    [int]    $RetentionDays = 14,
    [switch] $Verify
)

$ErrorActionPreference = 'Stop'

# $PSScriptRoot is empty inside a param() default under Windows PowerShell when
# the script is run with -File, so anything derived from it is resolved here.
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $MediaPath) { $MediaPath = Join-Path $here '..\backend\media' }

# The backup file is written by the SQL Server *service account*, not by whoever
# runs this script, so it has to land somewhere that account can write. The
# instance's own backup directory always qualifies. Override -BackupRoot only
# with a path you have granted the service account rights to.
if (-not $BackupRoot) {
    $BackupRoot = (sqlcmd -S $Server -E -h -1 -W -Q `
        "SET NOCOUNT ON; SELECT CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(4000));"
        ) -join ''
    $BackupRoot = $BackupRoot.Trim()
    if (-not $BackupRoot -or $BackupRoot -eq 'NULL') {
        throw "Could not read the instance default backup path from $Server. Pass -BackupRoot explicitly."
    }
    Write-Host "Using the instance backup directory: $BackupRoot"
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

if (-not (Test-Path $BackupRoot)) { New-Item -ItemType Directory -Force $BackupRoot | Out-Null }
$backupFile = Join-Path $BackupRoot "$Database-$stamp.bak"

# Express Edition cannot compress a backup, and asking it to is a hard error.
$edition = ((sqlcmd -S $Server -E -h -1 -W -Q `
    "SET NOCOUNT ON; SELECT CAST(SERVERPROPERTY('Edition') AS nvarchar(200));") -join '').Trim()
$compression = if ($edition -match 'Express') { '' } else { 'COMPRESSION, ' }
if (-not $compression) {
    Write-Host "$edition does not support backup compression; writing an uncompressed backup." `
        -ForegroundColor Yellow
}

Write-Host "Backing up $Database from $Server..."
$sql = @"
BACKUP DATABASE [$Database]
TO DISK = N'$backupFile'
WITH FORMAT, INIT, $($compression)CHECKSUM, STATS = 25,
     NAME = N'$Database full backup $stamp';
"@
sqlcmd -S $Server -E -b -Q $sql
if ($LASTEXITCODE -ne 0) { throw "BACKUP DATABASE failed with exit code $LASTEXITCODE" }

# The instance backup directory is ACL'd to the service account, so the size is
# read back from SQL Server rather than from the filesystem.
$sizeMb = ((sqlcmd -S $Server -E -h -1 -W -Q @"
SET NOCOUNT ON;
SELECT TOP 1 CAST(ROUND(backup_size / 1048576.0, 1) AS nvarchar(20))
FROM msdb.dbo.backupset
WHERE database_name = N'$Database' ORDER BY backup_finish_date DESC;
"@) -join '').Trim()
$size = if ($sizeMb -as [decimal]) { '{0:N1} MB' -f [decimal]$sizeMb } else { 'size unknown' }
Write-Host "Wrote $backupFile ($size)" -ForegroundColor Green

if ($Verify) {
    Write-Host 'Verifying the backup is restorable...'
    sqlcmd -S $Server -E -b -Q "RESTORE VERIFYONLY FROM DISK = N'$backupFile' WITH CHECKSUM;"
    if ($LASTEXITCODE -ne 0) { throw 'RESTORE VERIFYONLY failed: this backup is NOT restorable' }
    Write-Host 'Verified.' -ForegroundColor Green
}

# Borrower documents live on disk, not in the database, so they need copying
# too. They go somewhere the operator can read, which the instance backup
# directory usually is not.
if (-not $MediaBackupRoot) { $MediaBackupRoot = Join-Path $env:USERPROFILE 'lms-backups' }
if ((Test-Path $MediaPath) -and (Get-ChildItem $MediaPath -Recurse -File -ErrorAction SilentlyContinue)) {
    try {
        New-Item -ItemType Directory -Force $MediaBackupRoot | Out-Null
        $mediaZip = Join-Path $MediaBackupRoot "media-$stamp.zip"
        Compress-Archive -Path (Join-Path $MediaPath '*') -DestinationPath $mediaZip -Force
        Write-Host "Wrote $mediaZip" -ForegroundColor Green
    } catch {
        Write-Host "Could not archive $MediaPath : $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "No borrower documents at $MediaPath; nothing to copy." -ForegroundColor Yellow
}

if ($RetentionDays -gt 0) {
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    foreach ($folder in @($BackupRoot, $MediaBackupRoot) | Select-Object -Unique) {
        try {
            $old = Get-ChildItem $folder -File -ErrorAction Stop |
                Where-Object { $_.LastWriteTime -lt $cutoff -and $_.Extension -in '.bak', '.zip' }
            foreach ($file in $old) {
                Remove-Item $file.FullName -Force
                Write-Host "Removed expired backup $($file.Name)"
            }
        } catch {
            # Typically the instance backup directory, which is ACL'd to the
            # service account. Point -BackupRoot at a share both can reach.
            Write-Host "Cannot prune $folder (no read access): old backups will accumulate there." `
                -ForegroundColor Yellow
        }
    }
}

Write-Host ''
Write-Host 'To restore into a scratch database and prove the backup works:' -ForegroundColor Cyan
Write-Host "  RESTORE DATABASE [LMS_restoretest] FROM DISK = N'$backupFile'"
Write-Host "  WITH MOVE 'LMS' TO 'C:\temp\LMS_test.mdf',"
Write-Host "       MOVE 'LMS_log' TO 'C:\temp\LMS_test.ldf', RECOVERY;"
