# Changelog

## 1.2.0 - 2026-11-04 (physical acceptance pending)

- Preserve pending chunks across restarts and join them with queued continuations from the same driver session.
- Mark all records from a session with known capture loss and require an audited reprint acknowledgment.
- Add driver UTC observation timestamps, retain archive timestamps, and identify legacy records accurately.
- Show offline status reliably, support navigation through multi-label previews, and warn that reprinting sends the whole segment.
- Project history snippets in SQLite and normalize NULs in searchable text without altering raw files.
- Restrict existing and future archive data to Administrators/SYSTEM, rejecting links and hard links.
- Verify the packaged SYS and driver-store identity before removal, and fail on device restart errors.
- Reserve driver queue capacity before allocating and copying, including in-flight reservations.
- Require the matching protocol-v3 driver and service; physical testing and signing are still required.

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
