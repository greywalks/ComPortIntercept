# Changelog

## 1.0.0 - 2026-11-04

- Consolidated the original troubleshooting session into a reusable package.
- Moved capture from USBPRINT child devices to the physical Zebra USB stacks.
- Replaced queue-owning `EvtIoWrite` interception with a transparent WDM preprocess observer.
- Added the required `IoSkipCurrentIrpStackLocation` call before returning preprocessed write IRPs to KMDF.
- Added fail-open capture behavior and 1 MiB chunking.
- Added build, install, uninstall, discovery, diagnostics, and API-test scripts.
- Added detailed architecture, deployment, recovery, signing, and lessons-learned documentation.
- Changed default Labelary endpoint to HTTPS.