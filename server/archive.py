"""Durable capture chunk storage and bounded stream segmentation.

USB writes have no reliable Windows spooler-job boundary.  Segments preserve the
observed byte order but are explicitly marked as unverified stream boundaries.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Archive:
    def __init__(self, data_dir, printers):
        self.data = Path(data_dir)
        self.jobs = self.data / "jobs"
        self.db = self.data / "archive.db"
        self.printers = printers
        self.jobs.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self):
        c = sqlite3.connect(self.db, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA synchronous=FULL")
        return c

    def _init_db(self):
        c = self.connect()
        try:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(
              id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL,
              target_id INTEGER NOT NULL, printer_name TEXT NOT NULL,
              windows_port TEXT NOT NULL, byte_count INTEGER NOT NULL,
              sha256 TEXT NOT NULL, raw_path TEXT NOT NULL, text TEXT,
              origin TEXT NOT NULL DEFAULT 'Captured', parent_job_id INTEGER,
              reprint_count INTEGER NOT NULL DEFAULT 0, last_reprinted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_time ON jobs(captured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_sha ON jobs(sha256);
            CREATE TABLE IF NOT EXISTS capture_chunks(
              session TEXT NOT NULL, seq INTEGER NOT NULL, target_id INTEGER NOT NULL,
              stamp TEXT NOT NULL, data BLOB NOT NULL, sha256 TEXT NOT NULL,
              job_id INTEGER, PRIMARY KEY(session,seq)
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_pending ON capture_chunks(job_id,session,seq);
            CREATE TABLE IF NOT EXISTS capture_losses(
              id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
              seen_at TEXT NOT NULL, dropped_chunks INTEGER NOT NULL,
              dropped_bytes INTEGER NOT NULL, UNIQUE(session,dropped_chunks,dropped_bytes)
            );
            CREATE TABLE IF NOT EXISTS capture_sessions(
              session TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
              last_queued_bytes INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capture_restarts(
              id INTEGER PRIMARY KEY AUTOINCREMENT, seen_at TEXT NOT NULL,
              previous_session TEXT NOT NULL, new_session TEXT NOT NULL,
              previous_queued_bytes INTEGER NOT NULL,
              UNIQUE(previous_session,new_session)
            );
            CREATE TABLE IF NOT EXISTS reprint_attempts(
              id INTEGER PRIMARY KEY AUTOINCREMENT, parent_job_id INTEGER NOT NULL,
              target_id INTEGER NOT NULL, sha256 TEXT NOT NULL, started_at REAL NOT NULL,
              state TEXT NOT NULL, matched_job_id INTEGER, error TEXT
            );
            """)
            columns = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
            for name, spec in {
                "integrity": "TEXT NOT NULL DEFAULT 'legacy-unverified'",
                "boundary_reason": "TEXT NOT NULL DEFAULT 'legacy'",
                "driver_session": "TEXT", "first_sequence": "INTEGER", "last_sequence": "INTEGER",
            }.items():
                if name not in columns:
                    c.execute(f"ALTER TABLE jobs ADD COLUMN {name} {spec}")
            c.commit()
        finally:
            c.close()

    def put_chunk(self, session, seq, target_id, stamp, raw):
        """Durably store before ACK; duplicate delivery is safe and verified."""
        session, seq, raw = str(session), int(seq), bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        c = self.connect()
        try:
            with c:
                row = c.execute("SELECT target_id,sha256 FROM capture_chunks WHERE session=? AND seq=?", (session, seq)).fetchone()
                if row:
                    if row["target_id"] != int(target_id) or row["sha256"] != digest:
                        raise RuntimeError("Driver delivered conflicting data for the same capture sequence")
                    return False
                c.execute("INSERT INTO capture_chunks(session,seq,target_id,stamp,data,sha256) VALUES(?,?,?,?,?,?)",
                          (session, seq, int(target_id), str(stamp), raw, digest))
            return True
        finally:
            c.close()

    def pending_chunks(self):
        c = self.connect()
        try:
            return [dict(r) for r in c.execute("SELECT session,seq,target_id,stamp,data FROM capture_chunks WHERE job_id IS NULL ORDER BY rowid")]
        finally:
            c.close()

    def record_loss(self, session, dropped_chunks, dropped_bytes):
        c = self.connect()
        try:
            with c:
                c.execute("INSERT OR IGNORE INTO capture_losses(session,seen_at,dropped_chunks,dropped_bytes) VALUES(?,?,?,?)",
                          (str(session), now(), int(dropped_chunks), int(dropped_bytes)))
        finally:
            c.close()

    def observe_session(self, session, queued_bytes):
        session = str(session)
        c = self.connect()
        try:
            with c:
                previous = c.execute("SELECT * FROM capture_sessions ORDER BY rowid DESC LIMIT 1").fetchone()
                if not previous or previous["session"] != session:
                    if previous:
                        c.execute("INSERT OR IGNORE INTO capture_restarts(seen_at,previous_session,new_session,previous_queued_bytes) VALUES(?,?,?,?)",
                                  (now(), previous["session"], session, previous["last_queued_bytes"]))
                    c.execute("INSERT OR IGNORE INTO capture_sessions(session,first_seen,last_seen,last_queued_bytes) VALUES(?,?,?,?)",
                              (session, now(), now(), int(queued_bytes)))
                c.execute("UPDATE capture_sessions SET last_seen=?,last_queued_bytes=? WHERE session=?",
                          (now(), int(queued_bytes), session))
        finally:
            c.close()

    def recent_restarts(self):
        c = self.connect()
        try:
            return [dict(r) for r in c.execute("SELECT * FROM capture_restarts ORDER BY id DESC LIMIT 20")]
        finally:
            c.close()

    def recent_losses(self):
        c = self.connect()
        try:
            return [dict(r) for r in c.execute("SELECT session,seen_at,dropped_chunks,dropped_bytes FROM capture_losses ORDER BY id DESC LIMIT 20")]
        finally:
            c.close()

    def _save(self, target_id, raw, origin, reason, chunks=()):
        raw = bytes(raw)
        if not raw:
            raise ValueError("Cannot save an empty segment")
        printer = self.printers.get(int(target_id), {"name": f"Target {target_id}", "windows_port": "Unknown"})
        digest = hashlib.sha256(raw).hexdigest()
        rel = "jobs/" + uuid.uuid4().hex + ".bin"
        path = self.data / rel
        # The durable file is created before the DB transaction. A crash before
        # commit can leave an orphan file, but never a committed missing file.
        try:
            with open(path, "xb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            c = self.connect()
            try:
                with c:
                    parent = None
                    if origin == "Captured":
                        # A byte match is only a hint; identical independent jobs
                        # cannot be distinguished from a reprint at this layer.
                        candidates = c.execute("""SELECT id,parent_job_id FROM reprint_attempts
                          WHERE target_id=? AND sha256=? AND state IN ('sending','submitted')
                          AND started_at>=? AND matched_job_id IS NULL""",
                          (int(target_id), digest, time.time() - 120)).fetchall()
                        if len(candidates) == 1:
                            origin, parent = "Possible Reprint", candidates[0]["parent_job_id"]
                    try:
                        decoded = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        decoded = raw.decode("latin-1")
                    cur = c.execute("""INSERT INTO jobs(captured_at,target_id,printer_name,windows_port,byte_count,
                      sha256,raw_path,text,origin,parent_job_id,integrity,boundary_reason,driver_session,first_sequence,last_sequence)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (now(), int(target_id), printer["name"], printer["windows_port"],
                      len(raw), digest, rel, decoded, origin, parent, "unverified-stream", reason,
                      chunks[0][0] if chunks else None, chunks[0][1] if chunks else None, chunks[-1][1] if chunks else None))
                    job_id = int(cur.lastrowid)
                    for session, seq in chunks:
                        changed = c.execute("UPDATE capture_chunks SET job_id=?,data=X'' WHERE session=? AND seq=? AND job_id IS NULL",
                                            (job_id, str(session), int(seq))).rowcount
                        if changed != 1:
                            raise RuntimeError("Capture chunk was already committed or is missing")
                    if origin == "Possible Reprint":
                        c.execute("UPDATE reprint_attempts SET matched_job_id=? WHERE id=?", (job_id, candidates[0]["id"]))
                return job_id
            finally:
                c.close()
        except BaseException:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def save_segment(self, target_id, raw, reason, chunks):
        return self._save(target_id, raw, "Captured", reason, chunks)

    def save_demo(self, target_id, raw):
        return self._save(target_id, raw, "Demo/Test", "manual-ingest")

    def list_jobs(self, query=""):
        c = self.connect()
        try:
            sql = "SELECT * FROM jobs"
            params = []
            if query:
                sql += " WHERE text LIKE ? OR printer_name LIKE ? OR windows_port LIKE ? OR origin LIKE ?"
                n = f"%{query}%"; params = [n] * 4
            sql += " ORDER BY id DESC LIMIT 500"
            rows = [dict(r) for r in c.execute(sql, params)]
            for row in rows:
                row.pop("raw_path", None)
                row["text"] = (row.get("text") or "")[:256]
            return rows
        finally:
            c.close()

    def get_job(self, job_id):
        c = self.connect()
        try:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
            return dict(row) if row else None
        finally:
            c.close()

    def read_raw(self, row):
        # Resolve within the archive; never follow a DB path outside it.
        path = (self.data / row["raw_path"]).resolve()
        if not path.is_relative_to(self.jobs.resolve()):
            raise RuntimeError("Invalid archive path")
        raw = path.read_bytes()
        if len(raw) != row["byte_count"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise RuntimeError("Archived bytes failed the length or SHA-256 integrity check")
        return raw

    def start_reprint(self, row):
        c = self.connect()
        try:
            with c:
                cur = c.execute("INSERT INTO reprint_attempts(parent_job_id,target_id,sha256,started_at,state) VALUES(?,?,?,?,?)",
                                (row["id"], row["target_id"], row["sha256"], time.time(), "sending"))
                return cur.lastrowid
        finally:
            c.close()

    def finish_reprint(self, attempt_id, job_id, error=None):
        c = self.connect()
        try:
            with c:
                c.execute("UPDATE reprint_attempts SET state=?,error=? WHERE id=?",
                          ("failed" if error else "submitted", str(error) if error else None, attempt_id))
                if not error:
                    c.execute("UPDATE jobs SET reprint_count=reprint_count+1,last_reprinted_at=? WHERE id=?", (now(), job_id))
        finally:
            c.close()


class StreamAssembler:
    """Bound memory and preserve all bytes; idle/size boundaries are estimates."""
    def __init__(self, archive, flush_ms=400, max_bytes=4 * 1024 * 1024, clock=time.monotonic):
        if flush_ms < 1 or max_bytes < 1024 * 1024:
            raise ValueError("flush_ms must be positive and max_segment_bytes at least 1 MiB")
        self.archive, self.flush_ms, self.max_bytes, self.clock = archive, flush_ms, max_bytes, clock
        self.pending = {}
        self.lock = threading.RLock()

    def feed(self, session, seq, target_id, raw, stamp=None):
        key = (str(session), int(target_id))
        with self.lock:
            current = self.pending.get(key)
            # Kernel stamps are 100-ns interrupt-time ticks. Preserve idle
            # boundaries when draining a backlog faster than it was captured.
            if current and stamp is not None and current["stamp"] is not None:
                delta = int(stamp) - current["stamp"]
                if delta >= self.flush_ms * 10000:
                    self._flush(key, "capture-idle")
                    current = None
            if current and len(current["data"]) + len(raw) > self.max_bytes:
                self._flush(key, "size-limit")
                current = None
            if current is None:
                current = self.pending[key] = {"data": bytearray(), "chunks": [], "last": self.clock(), "stamp": None}
            current["data"].extend(raw)
            current["chunks"].append((str(session), int(seq)))
            current["last"] = self.clock()
            current["stamp"] = int(stamp) if stamp is not None else None
            if len(current["data"]) >= self.max_bytes:
                self._flush(key, "size-limit")

    def _flush(self, key, reason):
        value = self.pending[key]
        job_id = self.archive.save_segment(key[1], value["data"], reason, value["chunks"])
        del self.pending[key]
        return job_id

    def flush_idle(self, force=False, reason="idle"):
        ids = []
        with self.lock:
            for key, value in list(self.pending.items()):
                if force or (self.clock() - value["last"]) * 1000 >= self.flush_ms:
                    ids.append(self._flush(key, reason))
        return ids

    def recover(self):
        for row in self.archive.pending_chunks():
            self.feed(row["session"], row["seq"], row["target_id"], row["data"], row["stamp"])
        self.flush_idle(force=True, reason="recovered-after-restart")
