# Recovery / rollback

If printing stops after installing the filter, do not keep submitting print jobs.

## Normal rollback

Open Administrator PowerShell:

```powershell
.\driver\Uninstall-Driver.ps1
```

If automatic OEM INF discovery fails, use the published name shown during install:

```powershell
.\driver\Uninstall-Driver.ps1 -PublishedName oemNN.inf
```

The script removes the extension package and restarts matching Zebra physical USB devices.

## Confirm the filter is detached

For each physical device, `DEVPKEY_Device_CompoundUpperFilters` should no longer contain `ZebraUsbTap`.

```powershell
.\tools\Verify-Deployment.ps1
```

## If the service is still RUNNING after uninstall

The package may be removed while the previous kernel image remains resident. Do not install a different build on top of it. Reboot, then confirm:

```powershell
sc.exe query ZebraUsbTap
```

The service should be absent or stopped before installing a replacement build.