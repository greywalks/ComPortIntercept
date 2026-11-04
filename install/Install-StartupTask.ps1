#requires -version 5.1
param([ValidateSet('CurrentUser','System')][string]$Mode='CurrentUser')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root 'server\app.py'
$taskName = 'Zebra USB Print Archive'

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py.exe -ErrorAction SilentlyContinue }
if (-not $python) { throw 'Python was not found in PATH.' }
$pythonExe = $python.Source

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$script`"" -WorkingDirectory $root
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

if ($Mode -eq 'System') {
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
} else {
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Installed startup task '$taskName' ($Mode)." -ForegroundColor Green
Write-Host "Python: $pythonExe"
Write-Host "Script: $script"
