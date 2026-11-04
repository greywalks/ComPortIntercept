# Deployment, acceptance and rollback

## Before starting

This protocol-v2 driver and service must be installed together. Do not infer production readiness from compilation or mock tests. Use a designated Windows 10 2004+ x64 test machine first; ensure the printer queues already print, use an appropriately signed package, and have a recovery plan and BitLocker recovery material available. Install the application and Python in locations writable only by administrators/SYSTEM, especially for the SYSTEM startup task.

Back up the entire `data/` directory with the service stopped. Preserve the old driver package and application version for rollback. Stopping a service or unloading the driver may discard in-memory, unacknowledged traffic: schedule the upgrade when printers are idle and make this limitation clear to operators.

## Build and sign

In an elevated PowerShell:

```powershell
.\tools\Discover-ZebraTargets.ps1
.\driver\Build-Driver.ps1
Get-AuthenticodeSignature .\driver\x64\Debug\ZebraUsbTap.sys
Get-AuthenticodeSignature .\driver\x64\Debug\ZebraUsbTap\zebrausbtap.cat
```

Sign using an approved production or development method before installing. A valid Authenticode signature alone does not establish that Windows kernel policy accepts it. Do not disable boot security casually; see [TEST_SIGNING.md](TEST_SIGNING.md).

## Upgrade or install

1. Stop the archive and its scheduled task; make sure printers are idle.
2. If replacing an existing driver, run `.\driver\Uninstall-Driver.ps1`. Verify the old filter is detached. If the old service/image remains resident, reboot before installing the new package. The new driver implements last-device control-object cleanup, but this still requires on-machine verification.
3. Install the appropriately signed package: `.\driver\Install-Driver.ps1`.
4. Run `.\tools\Verify-Deployment.ps1`. Confirm the expected filter is attached to both physical devices and the driver service is running. Resolve signature error 577 rather than repeatedly reinstalling.
5. Keep the archive server **off**. Print one normal label to each printer and confirm both print. If either fails, roll back immediately.
6. Start `.\install\Start-Archive.ps1` as administrator. Open `http://127.0.0.1:8765`. Retrieve the token with `Get-Content .\data\private\api-token` in an elevated PowerShell and unlock the UI.
7. For each printer, print a label, confirm physical output and a new segment for the expected target, then inspect bytes and the capture status. No known dropped chunks or unexpected restart/loss events should be present. Check that unplug/replug and service restart work as expected on the test system.
8. Reprint a known segment and confirm physical output. The success message only confirms spooler submission. Verify the captured reprint is labeled `Possible Reprint` when it can be matched, understanding that correlation is heuristic.
9. Test a non-ZPL payload, a split-write label, two labels close together, a restart with queued data, and simulated storage exhaustion. Confirm warnings and recovery. Where possible use Driver Verifier on a disposable test system and test multiple print sizes and sustained load.
10. Only after successful acceptance, install the optional startup task. Verify its `ExecutionTimeLimit` is unlimited (`PT0S`), its account and folder permissions are appropriate, and it restarts after a process failure.

Authenticated status example:

```powershell
$token = (Get-Content .\data\private\api-token -Raw).Trim()
Invoke-RestMethod http://127.0.0.1:8765/api/status -Headers @{Authorization="Bearer $token"} | ConvertTo-Json -Depth 6
```

Never interpret a green connection indicator as proof that every label was captured or physically printed. Review `dropped_chunks`, `loss_events`, and `driver_restarts`.

## Rollback

If printing is affected, stop submitting jobs and run `.\driver\Uninstall-Driver.ps1`. Confirm the filter is detached and both printers work normally. If necessary reboot to unload the old image. Reinstall a previously accepted driver/service pair only after reviewing its compatibility and known issues. See [RECOVERY.md](RECOVERY.md).
