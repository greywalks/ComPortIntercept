# Driver build/install

The supported workflow is documented in [`../docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md).

Build:

```powershell
.\driver\Build-Driver.ps1
```

Install from Administrator PowerShell:

```powershell
.\driver\Install-Driver.ps1
```

Rollback:

```powershell
.\driver\Uninstall-Driver.ps1
```

The INF targets the physical devices `USB\VID_0A5F&PID_0080` and `USB\VID_0A5F&PID_0062` as device-specific upper filters. Do not change the package back to the `USBPRINT\...` child IDs; that was tested and did not observe the required print traffic.
