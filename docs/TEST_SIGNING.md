# Driver signing and error 577

Windows x64 requires kernel-mode drivers to satisfy kernel signing policy. A Visual Studio/WDK development build can show a valid Authenticode signature and still be rejected by the kernel if the certificate is only a development/test certificate.

## Preferred reusable deployment

For repeatable production use, distribute a Microsoft-accepted signed package rather than relying on one-boot signature-enforcement bypasses. Keep the source and build scripts in this repository, but treat production signing as a release step.

Microsoft documentation:

- https://learn.microsoft.com/windows-hardware/drivers/install/windows-driver-signing-tutorial
- https://learn.microsoft.com/windows-hardware/drivers/install/driver-signing
- https://learn.microsoft.com/windows-hardware/drivers/dashboard/driver-signing-offerings

## Development/test deployment

After building, verify both the SYS and CAT:

```powershell
Get-AuthenticodeSignature .\driver\x64\Debug\ZebraUsbTap.sys
Get-AuthenticodeSignature .\driver\x64\Debug\ZebraUsbTap\zebrausbtap.cat
```

Then install with `Install-Driver.ps1` and inspect:

```powershell
sc.exe query ZebraUsbTap
sc.exe start ZebraUsbTap
```

If `sc.exe start` fails with **577**, Windows is enforcing kernel signature policy for the test certificate.

For a designated development/test machine, Windows offers temporary boot-time mechanisms to disable signature enforcement. This is intentionally not automated by this repository.

### BitLocker warning

Before changing boot/startup security behavior on a BitLocker-protected computer, confirm the recovery key is available or suspend protection according to your organization's policy. Do not disable Secure Boot or alter BCD settings merely to make this package load unless you understand the recovery implications.

After a one-boot signature-enforcement bypass, the bypass disappears at the next normal reboot.