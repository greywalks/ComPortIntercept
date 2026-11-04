# Changelog

## 1.1.0 - 2026-11-04 (proposed; physical acceptance pending)

- Replace destructive driver reads with a versioned peek/persist/ack protocol, bounded queue loss counters and session tracking.
- Preserve stream bytes in bounded, explicitly unverified segments; recover pending chunks after user-mode restarts.
- Add authenticated loopback API, Host/Origin checks and a restricted per-install token.
- Disable external preview and test ingestion by default; make preview an explicit action.
- Check raw-file hashes and spooler errors; label inferred reprints as possible matches.
- Add PnP control-device cleanup and require Windows 10 2004+ for `ExAllocatePool2`.
- Remove the startup task's default execution limit and add automated regression tests and a Windows driver build job.
- Document that USB writes do not establish exact Windows job boundaries or physical print completion.

## 1.0.0 - 2026-11-04

- Consolidated the original troubleshooting work into a reusable package.
- Added the targeted KMDF USB upper filter, local archive, web UI and deployment scripts.
