import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

from server.archive import Archive, StreamAssembler

PRINTERS = {1: {"name": "Printer 1", "windows_port": "USB001"}, 2: {"name": "Printer 2", "windows_port": "USB002"}}


class Clock:
    def __init__(self): self.value = 0
    def __call__(self): return self.value


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = Archive(self.tmp.name, PRINTERS)
        self.clock = Clock()
        self.assembler = StreamAssembler(self.archive, 400, 1024 * 1024, self.clock)
    def tearDown(self): self.tmp.cleanup()
    def add(self, seq, raw, target=1, session="s"):
        self.assertTrue(self.archive.put_chunk(session, seq, target, 0, raw))
        self.assembler.feed(session, seq, target, raw)
    def first(self): return self.archive.get_job(self.archive.list_jobs()[0]["id"])

    def test_combined_segments_keep_every_byte_including_multiple_labels(self):
        parts = [b'PRE^XA^FDONE', b'^FS^XZ^XA^FDTWO^FS^XZ', b'TRAIL']
        for i, p in enumerate(parts): self.add(i, p)
        self.clock.value = .401
        self.assembler.flush_idle()
        rows = self.archive.list_jobs()
        self.assertEqual(len(rows), 1)
        row = self.first()
        self.assertEqual(self.archive.read_raw(row), b''.join(parts))
        self.assertEqual(row['integrity'], 'unverified-stream')
        self.assertEqual(row['boundary_reason'], 'idle')
        self.assertEqual(len(self.archive.pending_chunks()), 0)
        c = self.archive.connect()
        try:
            self.assertEqual(c.execute('SELECT count(*) FROM capture_chunks WHERE length(data) > 0').fetchone()[0], 0)
        finally:
            c.close()

    def test_non_zpl_flushes_after_idle_and_targets_are_isolated(self):
        self.add(1, b'RAW-ONE', target=1)
        self.add(2, b'RAW-TWO', target=2)
        self.clock.value = .399
        self.assertEqual(self.assembler.flush_idle(), [])
        self.clock.value = .401
        self.assertEqual(len(self.assembler.flush_idle()), 2)
        self.assertEqual({self.archive.read_raw(self.archive.get_job(j['id'])) for j in self.archive.list_jobs()}, {b'RAW-ONE', b'RAW-TWO'})

    def test_backlog_preserves_capture_time_gaps(self):
        self.archive.put_chunk('s', 1, 1, 0, b'FIRST')
        self.archive.put_chunk('s', 2, 1, 8_000_000, b'SECOND')
        self.assembler.recover()
        self.assembler.flush_idle(force=True)
        rows = self.archive.list_jobs()
        self.assertEqual(len(rows), 2)
        payloads = {self.archive.read_raw(self.archive.get_job(r['id'])) for r in rows}
        self.assertEqual(payloads, {b'FIRST', b'SECOND'})
        self.assertIn('capture-idle', {r['boundary_reason'] for r in rows})

    def test_recovery_joins_later_driver_chunk_within_capture_gap(self):
        self.archive.put_chunk('s', 1, 1, 10_000_000, b'^XA^FDHELLO ')
        recovered = StreamAssembler(self.archive, 400, 1024 * 1024, self.clock)
        recovered.recover()
        self.assertEqual(self.archive.list_jobs(), [])
        self.archive.put_chunk('s', 2, 1, 10_010_000, b'WORLD^FS^XZ')
        recovered.feed('s', 2, 1, b'WORLD^FS^XZ', 10_010_000)
        recovered.flush_idle(force=True)
        self.assertEqual(self.archive.read_raw(self.first()), b'^XA^FDHELLO WORLD^FS^XZ')

    def test_new_driver_session_finalizes_only_old_pending_segments(self):
        self.archive.put_chunk('old', 1, 1, 10, b'old')
        recovered = StreamAssembler(self.archive, 400, 1024 * 1024, self.clock)
        recovered.recover(); recovered.finish_other_sessions('new')
        self.assertEqual(self.archive.read_raw(self.first()), b'old')
        self.assertEqual(self.first()['boundary_reason'], 'recovered-after-driver-change')

    def test_observed_timestamp_is_distinct_from_archive_time(self):
        self.archive.put_chunk('s', 1, 1, 10, b'FIRST', '2024-01-01T00:00:00.000+00:00')
        self.archive.put_chunk('s', 2, 1, 10_000_010, b'SECOND', '2024-01-01T00:00:01.000+00:00')
        recovered = StreamAssembler(self.archive, 400, 1024 * 1024, self.clock)
        recovered.recover(); recovered.flush_idle(force=True)
        rows = list(reversed(self.archive.list_jobs()))
        self.assertEqual([r['captured_at'] for r in rows], ['2024-01-01T00:00:00.000+00:00','2024-01-01T00:00:01.000+00:00'])
        self.assertTrue(all(r['time_quality'] == 'driver-utc' for r in rows))
        self.assertTrue(all(r['archived_at'] != r['captured_at'] for r in rows))

    def test_session_loss_flags_old_and_new_segments_and_requires_ack(self):
        self.add(1, b'EARLY'); self.assembler.flush_idle(force=True)
        old = self.first()
        self.archive.record_loss('s', 1, 12)
        self.assertEqual(self.archive.get_job(old['id'])['integrity'], 'capture-loss-possible')
        self.add(2, b'LATE'); self.assembler.flush_idle(force=True)
        new = self.first()
        self.assertEqual(new['integrity'], 'capture-loss-possible')
        with self.assertRaises(PermissionError): self.archive.start_reprint(new)
        attempt = self.archive.start_reprint(new, acknowledge_loss=True)
        c = self.archive.connect()
        try: self.assertEqual(c.execute('SELECT risk_acknowledged FROM reprint_attempts WHERE id=?',(attempt,)).fetchone()[0], 1)
        finally: c.close()

    def test_migration_flags_prior_restart_with_queued_data(self):
        self.add(1, b'PREVIOUS'); self.assembler.flush_idle(force=True)
        job = self.first()
        self.archive.observe_session('s', 10)
        self.archive.observe_session('new', 0)
        c = self.archive.connect()
        try:
            with c:
                c.execute('DELETE FROM capture_session_flags')
                c.execute("UPDATE jobs SET integrity='unverified-stream' WHERE id=?", (job['id'],))
        finally: c.close()
        migrated = Archive(self.tmp.name, PRINTERS)
        self.assertEqual(migrated.get_job(job['id'])['integrity'], 'capture-loss-possible')
        with self.assertRaises(PermissionError): migrated.start_reprint(job)

    def test_search_finds_text_after_nul_without_changing_raw_bytes(self):
        raw = b'\0^XA^FDCUSTOMER-123^FS^XZ'
        job = self.archive.save_demo(1, raw)
        self.assertEqual(self.archive.list_jobs('CUSTOMER-123')[0]['id'], job)
        self.assertEqual(self.archive.read_raw(self.archive.get_job(job)), raw)

    def test_history_query_projects_snippets_before_loading_records(self):
        text = 'X' * (256 * 1024)
        c = self.archive.connect()
        try:
            c.executemany('INSERT INTO jobs(captured_at,target_id,printer_name,windows_port,byte_count,sha256,raw_path,text) VALUES(?,?,?,?,?,?,?,?)',
                          [('2026-01-01',1,'Test','USB001',len(text),'sha','jobs/test.bin',text)] * 100)
            c.commit()
        finally: c.close()
        tracemalloc.start()
        try:
            rows = self.archive.list_jobs(); _, peak = tracemalloc.get_traced_memory()
        finally: tracemalloc.stop()
        self.assertEqual(len(rows), 100)
        self.assertLess(peak, 8 * 1024 * 1024)
        self.assertEqual(len(rows[0]['text']), 256)

    def test_unterminated_zpl_is_bounded(self):
        self.add(1, b'^XA' + b'a' * (1024 * 1024 - 3))
        self.assertEqual(len(self.archive.list_jobs()), 1)
        self.assertFalse(self.assembler.pending)
        self.assertEqual(self.first()['boundary_reason'], 'size-limit')

    def test_recovery_replays_uncommitted_chunks_once(self):
        self.archive.put_chunk(42, 1, 1, 0, b'^XAhello')
        self.assertFalse(self.archive.put_chunk(42, 1, 1, 0, b'^XAhello'))
        self.archive.put_chunk(42, 2, 1, 0, b'^XZ')
        recovered = StreamAssembler(self.archive, 400, 1024 * 1024, self.clock)
        recovered.recover()
        self.assertEqual(self.archive.list_jobs(), [])
        recovered.flush_idle(force=True)
        self.assertEqual(self.archive.read_raw(self.first()), b'^XAhello^XZ')
        self.assertEqual(self.first()['boundary_reason'], 'idle')
        StreamAssembler(self.archive, 400, 1024 * 1024, self.clock).recover()
        self.assertEqual(len(self.archive.list_jobs()), 1)
        self.assertFalse(self.archive.put_chunk(42, 1, 1, 0, b'^XAhello'))
        with self.assertRaisesRegex(RuntimeError, 'conflicting'):
            self.archive.put_chunk(42, 1, 1, 0, b'changed')

    def test_storage_failure_retains_durable_pending_chunks(self):
        self.add(1, b'^XAhello^XZ')
        with patch.object(self.archive, 'save_segment', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.assembler.flush_idle(force=True)
        self.assertEqual(len(self.archive.pending_chunks()), 1)
        recovered = StreamAssembler(self.archive, 400, 1024 * 1024, self.clock)
        recovered.recover(); recovered.flush_idle(force=True)
        self.assertEqual(self.archive.read_raw(self.first()), b'^XAhello^XZ')

    def test_test_ingestion_never_touches_capture_assembler(self):
        demo = self.archive.save_demo(1, b'^XA^FDDEMO')
        self.add(10, b'^XA^FDREAL^FS^XZ')
        self.assembler.flush_idle(force=True)
        self.assertEqual(self.archive.read_raw(self.archive.get_job(demo)), b'^XA^FDDEMO')
        self.assertEqual(self.archive.read_raw(self.first()), b'^XA^FDREAL^FS^XZ')

    def test_reprint_match_is_explicitly_uncertain_and_hash_is_checked(self):
        job = self.archive.save_demo(1, b'^XA^XZ')
        row = self.archive.get_job(job)
        attempt = self.archive.start_reprint(row)
        self.archive.finish_reprint(attempt, job)
        self.add(1, b'^XA^XZ')
        self.assembler.flush_idle(force=True)
        captured = self.first()
        self.assertEqual(captured['origin'], 'Possible Reprint')
        self.assertEqual(captured['parent_job_id'], job)
        path = Path(self.tmp.name) / captured['raw_path']
        path.write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'integrity'):
            self.archive.read_raw(captured)

    def test_driver_restart_is_recorded_as_possible_loss(self):
        self.archive.observe_session('first', 500)
        self.archive.observe_session('first', 700)
        self.archive.observe_session('second', 0)
        self.archive.observe_session('second', 0)
        restarts = self.archive.recent_restarts()
        self.assertEqual(len(restarts), 1)
        self.assertEqual(restarts[0]['previous_queued_bytes'], 700)

    def test_loss_events_are_persistent_and_deduplicated(self):
        self.archive.record_loss('s', 2, 123)
        self.archive.record_loss('s', 2, 123)
        self.archive.record_loss('s', 3, 456)
        self.assertEqual(len(self.archive.recent_losses()), 2)
        self.assertEqual(Archive(self.tmp.name, PRINTERS).recent_losses()[0]['dropped_bytes'], 456)


if __name__ == '__main__': unittest.main()
