# Architecture

## Capture and durability

The device-specific KMDF upper filter observes `IRP_MJ_WRITE` for the two physical USB printer IDs and forwards the original request through the existing WDM preprocess path. Capture is fail-open so a storage or allocation failure does not deliberately fail a print. The filter sees writes before completion, not confirmed device output.

The driver assigns each captured chunk a driver-session ID and sequence, and queues it in nonpaged memory. Protocol v2 has a single exclusive, Administrators/SYSTEM-only control-device consumer:

1. `STATS` returns the session, queued bytes and cumulative dropped chunk/byte counters.
2. `PEEK` reads the first chunk without removing it.
3. The server commits the chunk, its digest and identity to SQLite with synchronous commits.
4. `ACK` removes that exact session/sequence from the driver queue.

Duplicate deliveries are checked and deduplicated. Pending chunks are rebuilt after a user-mode restart. When all PnP devices are removed the control object is deleted; queued nodes are released and recorded as drops if the same driver session becomes visible again. A reboot or driver unload can reset in-memory counters. The server persists observed session changes as **possible** loss events; it cannot reconstruct unobserved traffic or prove absence of loss.

The queue is bounded at 16 MiB. When full, or if the write buffer cannot be mapped or allocated, the driver counts lost data but forwards the print. Counters are global, so they cannot always identify which particular segment was affected. Monitor the authenticated status and investigate every loss or capture interruption.

## Segment boundaries and reprints

The server preserves the order in which it receives chunks for each target. It groups data on a configurable idle interval or size limit. Driver timestamps are used to recognize gaps when draining a backlog. Boundaries are estimates: they are not spooler job boundaries, writes can fail later, and concurrent writes are not a confirmed device delivery order. The UI labels records as unverified stream segments. The raw `.bin` is checked against its stored SHA-256 and length before reprinting.

Reprint requests are tracked before submission. A subsequently captured segment with matching target and hash within a short window is marked `Possible Reprint` and linked to its parent; identical independent jobs and split/merged streams make authoritative provenance impossible here. Spooler success means submission, not physical output.

## Local service and privacy

The server binds only to loopback. All API operations require a randomly generated per-install bearer token stored in `data/private/`; on Windows, ACLs are restricted to SYSTEM and Administrators. Host and Origin checks prevent common DNS-rebinding and cross-origin request paths. Operators must protect the entire installation directory and executable as well as the token. Disable test ingestion except during explicit diagnostic work.

External previews are off by default. When explicitly enabled and requested by the operator, the selected bytes are sent to Labelary over HTTPS. The browser receives a restrictive content security policy, and preview is a POST action rather than an automatic GET side effect.

Existing 1.0 databases are migrated in place; old records are labeled `legacy-unverified` and their raw files remain readable. Back up the database and jobs before upgrading.
