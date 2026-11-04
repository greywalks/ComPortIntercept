# ComPortIntercept / Zebra USB Print Archive

Windows software for observing outbound writes to two Zebra USB printer devices, keeping the observed bytes, searching them, and submitting a captured segment to the original Windows queue.

> **Verification status:** This version changes the kernel driver and its user-mode protocol. It has not yet been built, signed, installed, or tested against the physical printers in the original environment. Use a test workstation and follow the acceptance/rollback procedure in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) before considering deployment.

## What a record means

The filter observes USB-stack write requests. It does **not** see reliable Windows spooler-job boundaries or prove that a lower driver or physical printer completed a write. The server groups bytes by printer, capture time and configured size into **stream segments**. A segment may contain part of a job or multiple jobs. Reprinting sends the exact bytes of that segment to the spooler; it is not proof of an exact original job or successful physical output.

Raw chunks are committed to SQLite before the driver is asked to discard them. Pending chunks are recovered after a user-mode restart. The driver keeps at most 16 MiB in memory; allocation failures, unmappable requests and overflow are counted and displayed when the service reconnects. A machine crash, driver unload, unplug, or loss while neither the service nor driver is active can still lose in-memory data. The status page reports known drops and driver restarts, but cannot prove there were no unobserved losses. If a driver session reports loss, every record from that session is conservatively flagged and reprinting it requires an explicit acknowledgment. This can flag records that were not actually damaged.

## Target printers and prerequisites

| Target | Default Windows queue | Physical USB hardware ID |
|---|---|---|
| GK420d | `ZDesigner GK420d` | `USB\VID_0A5F&PID_0080` |
| ZP 500 | `Zebra ZP 500 (ZPL)` | `USB\VID_0A5F&PID_0062` |

- Windows 10 **version 2004 / build 19041 or newer**, x64; Python 3.9 or later.
- Visual Studio 2022, the C++ workload, a matching Windows SDK and WDK, and administrator rights.
- A kernel driver package signed in a manner accepted by the destination PC. Test-signing is for a designated test system; see [docs/TEST_SIGNING.md](docs/TEST_SIGNING.md).
- A protected installation folder where only administrators and SYSTEM can modify the application, driver packages, config, data, and Python executable. This is essential if the startup task runs as SYSTEM.
- The current mapping assumes one physical device of each model and the configured queue names. Two identical devices cannot currently be distinguished.

## Deployment overview

Back up `data/` and confirm both printers work before making changes. Stop the old archive service, then install the driver and service from the **same version**: protocol v3 intentionally rejects older drivers. A driver update affects printer stacks and requires signing and staged testing.

From an elevated PowerShell in the protected installation folder:

```powershell
.\tools\Discover-ZebraTargets.ps1
.\driver\Build-Driver.ps1
# Sign the built package appropriately before proceeding.
.\driver\Install-Driver.ps1
.\tools\Verify-Deployment.ps1
```

With the archive stopped, verify normal printing on **each** printer. Then start it:

```powershell
.\install\Start-Archive.ps1
```

Open `http://127.0.0.1:8765` and unlock with the token shown by this command in an **elevated** PowerShell:

```powershell
Get-Content .\data\private\api-token
```

Treat the token as a password. It is created locally and the Windows ACL is restricted to Administrators and SYSTEM. It is not stored in this repository. The web server only binds to `127.0.0.1`; the API requires the token and validates the Host and any Origin header. If startup cannot secure the token, it fails instead of exposing the API. To rotate it, stop the service, delete the token as an administrator, and restart.

Print a test label to each printer and verify physical output, a new record, expected target, observation timestamp, sensible byte content, and zero unexpected drop counters. Test a known captured segment using **Reprint Captured Segment**, then verify physical output separately. A `Possible Reprint` record is a time-and-hash match, not certain provenance. If a print or its audit update reports an uncertain outcome, check the physical printer before retrying.

Follow [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the complete upgrade, testing and rollback sequence. The optional startup task uses an unlimited execution time and can be installed with `.\install\Install-StartupTask.ps1`; `-Mode System` requires the protected installation described above.

## Privacy and diagnostics

Labelary previews are **disabled by default**. To enable them, set `preview_provider` to `labelary` in `config.json` and restart. The UI then asks before sending the selected segment to Labelary over HTTPS and lets you navigate the labels it reports. Reprinting still sends the whole segment. Do not enable it for sensitive labels unless that external transfer is approved.

The test-ingest API is disabled by default. To run its optional diagnostic, set `enable_test_ingest` to `true`, restart, and run `.\tools\Test-ArchiveApi.ps1` elevated. Test records have an isolated path and never enter the live stream assembler. Disable it again after testing. The diagnostic does not verify the driver or a printer.

`data/` contains the database, raw label bytes and access token. At startup the application restricts the archive tree to Administrators and SYSTEM on Windows, including existing files. This changes existing ACLs and can remove access for other users or services; back up both data and permissions before upgrading. Startup rejects links, junctions, reparse points, and hard-linked files in the data tree, and fails if protection cannot be applied. Large archives can take longer to start. The installation directory and Python executable must also be protected. `data/private/` is ignored by Git.

Protocol-v3 records carry the driver's UTC observation time and a separate archive time. Older records are labeled with archive time because their original observation time is unavailable. When the driver is unavailable after a service restart, pending chunks remain durable and are displayed in status until they can be reconciled.

## Testing

```powershell
python -m unittest discover -s tests -v
node --test tests/test_ui.js
```

The test suite covers segmentation, pending-chunk recovery, loss visibility, API authorization, preview consent, legacy database migration, and mocked spooler behavior. GitHub Actions includes a Windows driver compilation job, but compilation and mocked tests cannot substitute for signing, Driver Verifier, plug/unplug, load, reboot and physical-print acceptance tests.
