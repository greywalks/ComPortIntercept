import tempfile
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
        rows = self.archive.list_jobs()
        self.assertEqual(len(rows), 2)
        payloads = {self.archive.read_raw(self.archive.get_job(r['id'])) for r in rows}
        self.assertEqual(payloads, {b'FIRST', b'SECOND'})
        self.assertIn('capture-idle', {r['boundary_reason'] for r in rows})

    def test_unterminated_zpl_is_bounded(self):
        self.add(1, b'^XA' + b'a' * (1024 * 1024 - 3))
        self.assertEqual(len(self.archive.list_jobs()), 1)
        self.assertFalse(self.assembler.pending)
        self.assertEqual(self.first()['boundary_reason'], 'size-limit')

    def test_recovery_replays_uncommitted_chunks_once(self):
        self.archive.put_chunk(42, 1, 1, 0, b'^XAhello')
        self.assertFalse(self.archive.put_chunk(42, 1, 1, 0, b'^XAhello'))
        self.archive.put_chunk(42, 2, 1, 0, b'^XZ')
        StreamAssembler(self.archive, 400, 1024 * 1024, self.clock).recover()
        self.assertEqual(self.archive.read_raw(self.first()), b'^XAhello^XZ')
        self.assertEqual(self.first()['boundary_reason'], 'recovered-after-restart')
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
        StreamAssembler(self.archive, 400, 1024 * 1024, self.clock).recover()
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
