<#
.SYNOPSIS
    Register the nightly LMS batch with Windows Task Scheduler.

.DESCRIPTION
    Creates a task that runs scripts\run_nightly_jobs.ps1 once a day. Registered
    for the current user, so no administrator rights are needed; the task runs
    only while that account can log on. For a server, register it instead under
    a service account with "run whether the user is logged on or not", which
    does need elevation.

    Re-running replaces the existing task rather than creating a duplicate.

.EXAMPLE
    .\register_scheduled_task.ps1
    .\register_scheduled_task.ps1 -At 23:30 -TaskName "LMS nightly (test)"
    .\register_scheduled_task.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'LMS nightly batch',
    [string] $At       = '22:00',
    [switch] $Remove
)

$ErrorActionPreference = 'Stop'

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed the scheduled task '$TaskName'." -ForegroundColor Green
    } else {
        Write-Host "No scheduled task named '$TaskName'." -ForegroundColor Yellow
    }
    return
}

$script = Join-Path $PSScriptRoot 'run_nightly_jobs.ps1'
if (-not (Test-Path $script)) { throw "Cannot find $script" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory (Split-Path $PSScriptRoot -Parent)

$trigger  = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description 'Accrues loan penalties, queues borrower messages, credits savings interest on the 1st, and backs up the LMS database.' `
    -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
Write-Host "Registered '$TaskName', running daily at $At." -ForegroundColor Green
Write-Host "State: $($task.State)"
Write-Host ''
Write-Host 'Run it once now, without waiting for tonight:' -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host 'Check what happened:' -ForegroundColor Cyan
Write-Host "  Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "  Get-Content '$(Join-Path (Split-Path $PSScriptRoot -Parent) 'logs')\nightly-*.log' -Tail 40"
