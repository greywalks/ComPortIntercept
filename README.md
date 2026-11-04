# ComPortIntercept / Zebra USB Print Archive

A reusable Windows package for transparently archiving the exact outbound bytes sent to two Zebra USB printers, regardless of the application that initiated the print.

> Historical name: this project began as a COM-port interception idea. Device discovery showed the printers are USB printer devices (`USB002` / `USB007`), so the working implementation is a targeted KMDF USB upper filter plus a local archive service.

## Target deployment

| Target | Windows queue | Port | Physical USB hardware ID | USBPRINT child ID |
|---|---|---|---|---|
| GK420d | `ZDesigner GK420d` | `USB002` | `USB\VID_0A5F&PID_0080` | `USBPRINT\Zebra_TechnologiesZT9BEC` |
| ZP 500 | `Zebra ZP 500 (ZPL)` | `USB007` | `USB\VID_0A5F&PID_0062` | `USBPRINT\Zebra_TechnologiesZT7D5C` |

The **filter attaches to the physical USB device IDs**, not the USBPRINT child IDs. That distinction was one of the key findings from the original deployment.

## What it does

- Transparently observes outbound printer writes without taking ownership of the print request.
- Stores exact captured bytes in `data/jobs/*.bin`.
- Reassembles normal ZPL `^XA ... ^XZ` labels when Windows splits them across writes.
- Stores searchable metadata in SQLite.
- Provides a local web UI at `http://127.0.0.1:8765`.
- Can render ZPL previews through Labelary (optional; see privacy note).
- Can resend the exact archived bytes to the original Windows queue as a `RAW` print job.
- Marks archive-initiated reprints separately when the capture path sees them again.

## Working driver design

The final driver is a device-specific KMDF upper filter on the two physical Zebra USB devices.

For `IRP_MJ_WRITE` it:

1. observes the request in `EvtDeviceWdmIrpPreprocess`;
2. copies buffered/direct-I/O data when it can do so safely;
3. fails open if capture is not possible;
4. calls `IoSkipCurrentIrpStackLocation(Irp)`;
5. returns the original IRP through `WdfDeviceWdmDispatchPreprocessedIrp`.

There is intentionally **no `EvtIoWrite` PnP-device queue** in the final implementation.

Microsoft references:

- WDF preprocess dispatch: https://learn.microsoft.com/windows-hardware/drivers/ddi/wdfdevice/nf-wdfdevice-wdfdevicewdmdispatchpreprocessedirp
- KMDF filter forwarding: https://learn.microsoft.com/windows-hardware/drivers/ddi/wdffdo/nf-wdffdo-wdffdoinitsetfilter
- Device-specific filter installation: https://learn.microsoft.com/windows-hardware/drivers/install/inf-addfilter-directive

## Quick deployment

Run these from an **Administrator PowerShell** on the target PC.

### 1. Confirm the printers and device IDs

```powershell
.\tools\Discover-ZebraTargets.ps1
```

### 2. Build the driver

Install Visual Studio 2022, Desktop C++ workload, Windows SDK, and WDK, then:

```powershell
.\driver\Build-Driver.ps1
```

### 3. Sign appropriately

For durable deployment, use a Microsoft-accepted production/attestation signing path. WDK test signing is suitable only for development/testing. See [`docs/TEST_SIGNING.md`](docs/TEST_SIGNING.md).

### 4. Install the filter

```powershell
.\driver\Install-Driver.ps1
.\tools\Verify-Deployment.ps1
```

Do **not** proceed to capture testing until `sc.exe query ZebraUsbTap` reports `STATE : 4 RUNNING`.

### 5. Prove transparent pass-through first

Keep the archive server **off** and print one normal label. It must print normally. If it does not, stop and roll back with:

```powershell
.\driver\Uninstall-Driver.ps1
```

### 6. Start the archive

```powershell
.\install\Start-Archive.ps1
```

Open `http://127.0.0.1:8765` and print one normal label. The acceptance test is **both**:

- the physical label prints normally; and
- `/api/status` updates `last_capture_at` and a history record appears.

### 7. Test exact reprint

Select the new captured record and use **Reprint Exact Job**. Confirm the physical output succeeds.

### 8. Optional startup task

```powershell
.\install\Install-StartupTask.ps1
```

or, for a machine-level task:

```powershell
.\install\Install-StartupTask.ps1 -Mode System
```

## Preview privacy

The default configuration uses `https://api.labelary.com` for PNG previews. ZPL submitted for preview therefore leaves the workstation.

To keep label contents local, edit `config.json`:

```json
"preview_provider": "disabled"
```

Capture, search, raw storage, and exact reprint continue to work.

## Repository layout

```text
driver/                 Final KMDF filter source, INF, build/install/rollback
server/app.py           Local archive API, SQLite store, capture poller, RAW reprint
web/                    Local browser UI
install/                Archive startup helpers
tools/                  Discovery, diagnostics, API test utilities
docs/ARCHITECTURE.md    End-to-end data path
docs/DEPLOYMENT.md      Detailed repeatable deployment procedure
docs/LESSONS_LEARNED.md Failed approaches and final fixes
docs/TEST_SIGNING.md    Signing / error 577 guidance
docs/RECOVERY.md        Rollback procedure
config.json             Queue names, ports, target IDs, preview settings
data/                    Runtime database / exact raw jobs / preview cache
```

## Original test environment

The troubleshooting that produced this package was performed on Windows 10 Pro x64 build 19045 with Visual Studio 2022, WDK/SDK `10.0.26100.0`, and Python 3.13.15. The package is written for Windows 10 1903+ because the INF uses the declarative `AddFilter` model.

## Verification status carried forward

The final stack-advance fix was verified to allow normal printing with the filter loaded, including while the archive server was running. Earlier physical-layer builds proved that the selected physical USB interception point sees real print data. The original chat ended before the final STEP 6 build's history-entry and exact-reprint tests were explicitly confirmed, so those two checks remain part of every deployment acceptance test.
