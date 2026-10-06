<#
.SYNOPSIS
    Everything that has to pass before a change is committed.

.DESCRIPTION
    Django system checks, a check that no model change is missing a migration,
    the backend test suite, the frontend test suite, and a production frontend
    build. Prints a summary table and exits non-zero on the first category that
    fails, so it is usable as a pre-push hook or by hand.

    The backend tests run in parallel, one worker per clone of the test database
    (LMS_test_1, LMS_test_2, ...), twice as many workers as cores by default
    because each spends much of its time waiting on SQL Server. -Parallel 1 runs
    them in one process.

    The missing-migration check is the one most worth having: a model change
    without a migration breaks nothing locally — the test database is built from
    migrations and a missing index does not fail an assertion — and then fails on
    someone else's machine.

.EXAMPLE
    .\scripts\verify.ps1
    .\scripts\verify.ps1 -SkipBackendTests   # the slow one, for a quick loop
    .\scripts\verify.ps1 -Parallel 1         # backend tests in one process
#>
[CmdletBinding()]
param(
    [switch] $SkipBackendTests,
    [switch] $SkipFrontend,
    [string] $NodeDir,
    [int] $Parallel = 2 * [Environment]::ProcessorCount
)

$ErrorActionPreference = 'Stop'

$here    = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$root    = Resolve-Path (Join-Path $here '..')
$python  = Join-Path $root '.venv\Scripts\python.exe'
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'

if (-not (Test-Path $python)) {
    throw "No virtual environment at $python. Create it and install backend/requirements.txt."
}

# node is not on PATH in this project's usual setup; the portable copy is.
if (-not $NodeDir) {
    $portable = Join-Path $env:LOCALAPPDATA 'node-portable\node-v22.20.0-win-x64'
    if (Test-Path $portable) { $NodeDir = $portable }
}
if ($NodeDir -and (Test-Path $NodeDir)) { $env:PATH = "$NodeDir;$env:PATH" }

$results = [ordered]@{}
$failed = 0

function Invoke-Check([string] $name, [scriptblock] $body) {
    Write-Host ""
    Write-Host "==> $name" -ForegroundColor Cyan
    $started = Get-Date
    try {
        & $body
        if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
        $script:results[$name] = 'PASS'
        Write-Host "    PASS ($([int]((Get-Date) - $started).TotalSeconds)s)" -ForegroundColor Green
    } catch {
        $script:results[$name] = "FAIL - $_"
        $script:failed++
        Write-Host "    FAIL $_" -ForegroundColor Red
    }
}

Push-Location $backend
try {
    Invoke-Check 'Django system checks' { & $python manage.py check }

    Invoke-Check 'No missing migrations' {
        # --check --dry-run exits 1 when makemigrations WOULD write something.
        & $python manage.py makemigrations --check --dry-run core
    }

    if (-not $SkipBackendTests) {
        Invoke-Check 'Backend tests' { & $python manage.py test --parallel $Parallel }
    } else {
        $results['Backend tests'] = 'SKIPPED'
    }
} finally {
    Pop-Location
}

if (-not $SkipFrontend) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        $results['Frontend tests'] = 'SKIPPED - node not found'
        $results['Frontend build'] = 'SKIPPED - node not found'
        Write-Host "`nnode is not on PATH; pass -NodeDir or install Node to run the frontend checks." -ForegroundColor Yellow
    } else {
        Push-Location $frontend
        try {
            Invoke-Check 'Frontend tests' { npm test --silent }
            Invoke-Check 'Frontend build' { npm run build --silent }
        } finally {
            Pop-Location
        }
    }
} else {
    $results['Frontend tests'] = 'SKIPPED'
    $results['Frontend build'] = 'SKIPPED'
}

Write-Host ""
Write-Host "---------------------------------------------------------------" -ForegroundColor Gray
foreach ($name in $results.Keys) {
    $value = $results[$name]
    $colour = if ($value -eq 'PASS') { 'Green' } elseif ($value -like 'SKIPPED*') { 'Yellow' } else { 'Red' }
    Write-Host ("{0,-24} {1}" -f $name, $value) -ForegroundColor $colour
}
Write-Host "---------------------------------------------------------------" -ForegroundColor Gray

if ($failed) {
    Write-Host "$failed check(s) failed." -ForegroundColor Red
} else {
    Write-Host "Everything passed." -ForegroundColor Green
}
exit $failed
