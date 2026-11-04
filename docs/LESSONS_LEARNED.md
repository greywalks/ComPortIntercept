# Lessons learned during the first deployment

This file records the dead ends so future deployments do not repeat them.

## 1. These printers were not COM devices

The initial concept was a COM-port interceptor. Device discovery showed the Windows queues were on `USB002` and `USB007`, backed by `USBPRINT` and physical USB devices. The final solution is therefore a USB printer filter, despite the repository's historical name.

## 2. Filtering the USBPRINT child was the wrong interception layer

Initial targets:

- `USBPRINT\Zebra_TechnologiesZT9BEC`
- `USBPRINT\Zebra_TechnologiesZT7D5C`

The filter attached and the service ran, but real print traffic produced no capture records. The physical parent USB devices were then identified:

- GK420d: `USB\VID_0A5F&PID_0080`
- ZP 500: `USB\VID_0A5F&PID_0062`

Attaching there exposed real outbound writes.

## 3. Owning the write queue caused printer blocking

The early filter created a KMDF default queue with `EvtIoWrite`, copied the request buffer, formatted the request using its current type, and sent it downward. On this `usbprint.sys` stack, that implementation captured data but stopped normal printing and also prevented exact reprints from reaching the printer.

The final design deliberately avoids a PnP-device write queue.

## 4. A preprocess callback still has WDM stack semantics

The first transparent observer registered an `IRP_MJ_WRITE` preprocess callback and returned the IRP with `WdfDeviceWdmDispatchPreprocessedIrp`, but omitted the required stack-location advance. The filter loaded and again blocked printing.

The working STEP 6 change was:

```c
IoSkipCurrentIrpStackLocation(Irp);
return WdfDeviceWdmDispatchPreprocessedIrp(Device, Irp);
```

After that change, a normal manual print succeeded with the filter loaded, and a normal print also succeeded while the archive server was running.

## 5. Driver install success is not the same as driver load success

`pnputil /add-driver ... /install` can successfully stage and associate a test-signed package while the kernel later refuses to load it. We observed:

```text
StartService FAILED 577
Windows cannot verify the digital signature for this file.
```

The SYS and CAT both showed `Authenticode Status: Valid`; the failure was kernel test-signing enforcement, not a damaged package.

Always verify both:

```powershell
sc.exe query ZebraUsbTap
```

and, if stopped:

```powershell
sc.exe start ZebraUsbTap
```

## 6. Old kernel images can remain resident after package removal

Deleting the OEM INF does not guarantee the already-loaded filter image is gone. Before installing a replacement build, check `sc.exe query ZebraUsbTap`. If the removed build remains `RUNNING` and cannot be stopped, reboot before installing the replacement.

## 7. BitLocker makes boot-option experiments consequential

Temporary signature-enforcement changes can invoke Windows recovery paths. Do not change Secure Boot, BCD, or recovery options casually on a BitLocker-protected workstation. Confirm recovery material is available or suspend BitLocker according to organizational policy before entering Advanced Startup.

## 8. Keep acceptance tests separate

A successful redeployment requires four independent checks:

1. Filter attaches and reaches `STATE 4 RUNNING`.
2. With the archive server **off**, a normal label still prints.
3. With the server **on**, a normal label still prints **and** creates a history record / updates `last_capture_at`.
4. `Reprint Exact Job` physically prints the exact archived payload.

Do not infer one test from another.

## Verification status from the original troubleshooting session

Confirmed:

- web UI / SQLite / manual API ingest worked;
- physical USB interception captured traffic in the earlier blocking implementation;
- the final stack-advance fix allowed normal printing with the filter loaded;
- normal printing also continued while the archive server was running.

Not explicitly confirmed before repackaging:

- a new history entry produced by the final STEP 6 filter;
- final exact-reprint behavior under STEP 6.

Those remain mandatory deployment acceptance tests.