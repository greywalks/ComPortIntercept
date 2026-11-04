#requires -version 5.1
$taskName = 'Zebra USB Print Archive'
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Removed startup task: $taskName"
