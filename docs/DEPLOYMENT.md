# Repeatable deployment procedure

This is the deployment path to use on a fresh workstation. Do not replay the historical STEP1-STEP6 repair scripts.

## Prerequisites

- Windows 10 version 1903 or later, x64.
- Administrator rights.
- The target Zebra queues installed and printing normally before adding the filter.
- Python 3.x.
- Visual Studio 2022 with **Desktop development with C++**.
- Windows SDK + WDK with VS/MSBuild integration.
- A valid driver-signing method appropriate for the environment.

Before doing anything with boot security on a BitLocker-protected machine, make sure the recovery process is understood and recovery material is available.

## A. Baseline the workstation

```powershell
Get-Printer | Where-Object { $_.Name -match 'Zebra|ZDesigner' } |
    Format-Table Name,DriverName,PortName -AutoSize

.\tools\Discover-ZebraTargets.ps1
```

Expected model hardware IDs:

```text
USB\VID_0A5F&PID_0080   GK420d
USB\VID_0A5F&PID_0062   ZP 500
```

Confirm normal printing works **before** installing the filter.

## B. Build

```powershell
.\driver\Build-Driver.ps1
```

Expected package output:

```text
driver\x64\Debug\ZebraUsbTap.sys
driver\x64\Debug\ZebraUsbTap\ZebraUsbTap.inf
driver\x64\Debug\ZebraUsbTap\zebrausbtap.cat
```

Verify signatures:

```powershell
Get-AuthenticodeSignature .\driver\x64\Debug\ZebraUsbTap.sys
Get-AuthenticodeSignature .\driver\x64\Debug\ZebraUsbTap\zebrausbtap.cat
```

A valid Authenticode result does not by itself guarantee that the Windows kernel will accept a development/test certificate. See `TEST_SIGNING.md`.

## C. Install

```powershell
.\driver\Install-Driver.ps1
```

The installer records the `oemNN.inf` published name under `.local\published-inf.txt` when it can parse it.

Verify:

```powershell
.\tools\Verify-Deployment.ps1
```

Required conditions:

- each physical Zebra USB device lists `ZebraUsbTap` in `DEVPKEY_Device_CompoundUpperFilters`;
- `sc.exe query ZebraUsbTap` reports `STATE : 4 RUNNING`.

If the service is stopped, try:

```powershell
sc.exe start ZebraUsbTap
```

Error **577** means the kernel rejected the package signature under the current boot policy. Do not rebuild or reinstall repeatedly; resolve signing policy instead.

## D. Pass-through acceptance test

Keep `server/app.py` stopped.

Print exactly one normal GK420d label. It must physically print.

Then print exactly one normal ZP 500 label. It must physically print.

If either fails, rollback immediately:

```powershell
.\driver\Uninstall-Driver.ps1
```

Do not continue to archive testing until ordinary printing works with the filter loaded.

## E. Archive-server acceptance test

Start:

```powershell
.\install\Start-Archive.ps1
```

Check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/status | ConvertTo-Json -Depth 6
```

`capture_connected` must be `true`.

The following command verifies only the user-mode archive/database path:

```powershell
.\tools\Test-ArchiveApi.ps1
```

It does **not** prove kernel capture.

Now print one real label to each printer. For each print, verify:

1. physical output succeeds;
2. `last_capture_at` changes;
3. a new history entry appears;
4. the record has the correct printer/target;
5. raw byte count and displayed ZPL look plausible.

## F. Exact-reprint acceptance test

Choose one captured record and click **Reprint Exact Job**.

Verify:

- the physical printer outputs the label;
- the original `.bin` is unchanged;
- the reprint is recorded as an archive reprint if the capture path observes it.

## G. Configure startup

Current-user elevated startup:

```powershell
.\install\Install-StartupTask.ps1
```

Machine startup under SYSTEM:

```powershell
.\install\Install-StartupTask.ps1 -Mode System
```

Verify the local UI after task startup:

```text
http://127.0.0.1:8765
```

## H. Upgrade procedure

When replacing the kernel driver with a new build:

1. stop the archive server;
2. uninstall the existing OEM package;
3. run `sc.exe query ZebraUsbTap`;
4. if the old service/image is still `RUNNING`, reboot before installing the new build;
5. install the new package;
6. repeat pass-through and capture acceptance tests.

Never assume that removing an OEM INF unloads a currently resident kernel image.