<#
.SYNOPSIS
    The nightly batch: the application's scheduled jobs (penalties, reminders,
    waiting payments, and on the 1st savings interest, interest owed to funders and
    the provision), then a backup of the database.

.DESCRIPTION
    Every step is idempotent, so a repeated or retried run changes nothing
    extra. Output is appended to a dated log so a failed night can be read back
    in the morning.

.EXAMPLE
    .\run_nightly_jobs.ps1
    .\run_nightly_jobs.ps1 -SkipBackup -AsOf 2026-09-30
#>
[CmdletBinding()]
param(
    [string] $AsOf,
    [switch] $SkipBackup,
    [switch] $SkipSavingsInterest,
    [string] $LogRoot
)

$ErrorActionPreference = 'Stop'

# $PSScriptRoot is empty inside a param() default under Windows PowerShell when
# the script is run with -File, so anything derived from it is resolved here.
$here    = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$root    = Resolve-Path (Join-Path $here '..')
$python  = Join-Path $root '.venv\Scripts\python.exe'
$backend = Join-Path $root 'backend'
if (-not $LogRoot) { $LogRoot = Join-Path $root 'logs' }

New-Item -ItemType Directory -Force $LogRoot | Out-Null
$log = Join-Path $LogRoot ("nightly-{0}.log" -f (Get-Date -Format 'yyyyMMdd'))

function Write-Log([string] $message, [string] $colour = 'Gray') {
    $line = "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $message
    Write-Host $line -ForegroundColor $colour
    Add-Content -Path $log -Value $line
}

function Invoke-Step([string] $name, [string[]] $arguments) {
    Write-Log "START  $name" 'Cyan'
    try {
        $output = & $python @arguments 2>&1
        $output | ForEach-Object { Write-Log "       $_" }
        if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
        Write-Log "OK     $name" 'Green'
        return $true
    } catch {
        Write-Log "FAILED $name : $_" 'Red'
        return $false
    }
}

if (-not (Test-Path $python)) {
    throw "No virtual environment at $python. Create it and install backend/requirements.txt."
}

Push-Location $backend
$failures = 0
try {
    Write-Log "===== nightly batch starting =====" 'Cyan'

    # Every job lives in the application now (core/services/jobs.py): run_jobs
    # runs what is due today, skips what already succeeded, and records each run
    # on the Scheduled jobs page. It exits 1 if any job failed.
    $jobArgs = @('manage.py', 'run_jobs')
    if ($AsOf) { $jobArgs += @('--as-of', $AsOf) }
    if ($SkipSavingsInterest) { $jobArgs += @('--skip', 'savings_interest') }
    if (-not (Invoke-Step 'scheduled jobs' $jobArgs)) { $failures++ }
} finally {
    Pop-Location
}

if (-not $SkipBackup) {
    Write-Log 'START  database backup' 'Cyan'
    try {
        & (Join-Path $here 'backup_database.ps1') -Verify |
            ForEach-Object { Write-Log "       $_" }
        Write-Log 'OK     database backup' 'Green'
    } catch {
        Write-Log "FAILED database backup : $_" 'Red'
        $failures++
    }
}

Write-Log "===== nightly batch finished, $failures failure(s) =====" `
    $(if ($failures) { 'Red' } else { 'Green' })
exit $failures

