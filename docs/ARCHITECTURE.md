# Architecture

## Capture and durability

The device-specific KMDF upper filter observes `IRP_MJ_WRITE` for the two physical USB printer IDs and forwards the original request through the existing WDM preprocess path. Capture is fail-open so a storage or allocation failure does not deliberately fail a print. The filter sees writes before completion, not confirmed device output.

The driver assigns each captured chunk a driver-session ID and sequence, and queues it in nonpaged memory. Protocol v3 has a single exclusive, Administrators/SYSTEM-only control-device consumer:

1. `STATS` returns the session, queued bytes and cumulative dropped chunk/byte counters.
2. `PEEK` reads the first chunk without removing it.
3. The server commits the chunk, its digest and identity to SQLite with synchronous commits.
4. `ACK` removes that exact session/sequence from the driver queue.

Duplicate deliveries are checked and deduplicated. Pending chunks are rebuilt after a user-mode restart. When all PnP devices are removed the control object is deleted; queued nodes are released and recorded as drops if the same driver session becomes visible again. A reboot or driver unload can reset in-memory counters. The server persists observed session changes as **possible** loss events; it cannot reconstruct unobserved traffic or prove absence of loss.

The queue and in-flight reservations are bounded at 16 MiB. Capacity is reserved before allocation and copying. When full, or if the write buffer cannot be mapped or allocated, the driver counts lost data but forwards the print. Counters are global, so the service conservatively flags every segment from a session with reported loss. Existing records are updated and future records are flagged; the API requires a separate explicit risk acknowledgment and records it before reprinting. This is deliberately over-inclusive and cannot prove that unreported loss did not occur. Monitor status and investigate every interruption.

A chunk carries both a monotonic interrupt timestamp for grouping and a UTC FILETIME captured by the driver. New records keep the first and last UTC observations plus a separate archive timestamp. Existing records without an observation timestamp are explicitly labeled archive-time. On recovery, pending chunks remain pending until the same driver session is polled, allowing adjacent queued chunks to join; a different driver session closes old pending segments. If the driver is offline, status reports pending chunks instead of prematurely closing them.

## Segment boundaries and reprints

The server preserves the order in which it receives chunks for each target. It groups data on a configurable idle interval or size limit. Driver timestamps are used to recognize gaps when draining a backlog. Boundaries are estimates: they are not spooler job boundaries, writes can fail later, and concurrent writes are not a confirmed device delivery order. The UI labels records as unverified stream segments. The raw `.bin` is checked against its stored SHA-256 and length before reprinting.

Reprint requests are tracked before submission. A subsequently captured segment with matching target and hash within a short window is marked `Possible Reprint` and linked to its parent; identical independent jobs and split/merged streams make authoritative provenance impossible here. Spooler success means submission, not physical output.

## Local service and privacy

On startup, the service applies protected Administrators/SYSTEM ACLs to the data tree, including existing files; it rejects links, reparse points and hard-linked files. This changes pre-existing ACLs and can be slow for large archives. The application and Python installation must separately be protected. The server binds only to loopback. All API operations require a randomly generated per-install bearer token stored in `data/private/`; on Windows, ACLs are restricted to SYSTEM and Administrators. Host and Origin checks prevent common DNS-rebinding and cross-origin request paths. Operators must protect the entire installation directory and executable as well as the token. Disable test ingestion except during explicit diagnostic work.

External previews are off by default. When explicitly enabled and requested by the operator, the selected bytes are sent to Labelary over HTTPS. The response count and per-label navigation make multi-label segments visible, while the reprint warning states that the entire segment will be sent. The browser receives a restrictive content security policy, and preview is a POST action rather than an automatic GET side effect.

Existing 1.0 databases are migrated in place; old records are labeled `legacy-unverified` and their raw files remain readable. Back up the database and jobs before upgrading.
