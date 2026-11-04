# Architecture

## Purpose

Archive the exact outbound bytes sent to two Zebra USB printers, independent of the application that originated the print. The archive provides search, raw-byte retention, ZPL preview, and exact RAW reprint to the original Windows queue.

## Data path

```text
Application / PrintNode / Browser Print / Win32 spooler
                    |
                    v
            Windows printer queue
                    |
                    v
              usbprint.sys
                    ^
                    |
         ZebraUsbTap KMDF upper filter
          (physical USB device stack)
                    |
        copies outbound IRP_MJ_WRITE bytes
        without owning/completing the write
                    |
                    +----> original IRP continues to printer
                    |
                    +----> \\.\ZebraUsbTap control device
                                  |
                                  v
                            server/app.py
                                  |
                     ZPL reassembly / SQLite
                                  |
                    data/jobs/*.bin + web UI
```

## Why the filter is on the physical USB device

The first implementation targeted the `USBPRINT\...` child devices. The driver loaded, but real PrintNode/Windows jobs did not reach that interception point. Moving the filter to the physical Zebra USB devices produced observable write traffic.

Current physical hardware IDs:

- GK420d: `USB\VID_0A5F&PID_0080`
- ZP 500: `USB\VID_0A5F&PID_0062`

The known Windows queues are still used for reprint:

- `ZDesigner GK420d` on `USB002`
- `Zebra ZP 500 (ZPL)` on `USB007`

## Transparent forwarding

The final driver does **not** create an `EvtIoWrite` queue for the PnP device. It registers an `IRP_MJ_WRITE` WDM preprocess callback, copies only data that can be safely mapped, advances the IRP stack with `IoSkipCurrentIrpStackLocation`, and returns the original IRP through `WdfDeviceWdmDispatchPreprocessedIrp`.

If a payload cannot be safely mapped or capture resources are exhausted, capture is skipped. The print IRP is still forwarded. Printing is the priority; archive loss is preferable to blocking the printer.

## User-mode archive

`server/app.py` uses only the Python standard library. It:

- polls `\\.\ZebraUsbTap` via IOCTL;
- reassembles `^XA ... ^XZ` ZPL across multiple USB writes;
- stores exact captured bytes under `data/jobs/`;
- stores metadata/search text in SQLite;
- optionally renders previews with Labelary;
- sends exact archived bytes back through the original Windows queue as a `RAW` job.

The HTTP server binds to `127.0.0.1` by default.

## Known scope limits

This is a targeted printer-write archive, not a generic USB protocol analyzer. It only observes writes that traverse the two matching physical Zebra USB stacks. If two printers with the same VID/PID are simultaneously attached, the current kernel target mapping cannot distinguish them from each other; this package assumes one GK420d and one ZP 500.