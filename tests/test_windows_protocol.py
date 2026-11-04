import ctypes
import os
import struct
import tempfile
import unittest
from ctypes import wintypes
from unittest.mock import patch

from server import app
from server.archive import Archive

PRINTERS = {1: {'name': 'Printer 1', 'windows_port': 'USB001'}}

class Fn:
    def __init__(self, function): self.function = function
    def __call__(self, *args): return self.function(*args)

class FakeKernel:
    def __init__(self, capture, archive):
        self.capture, self.archive, self.acked = capture, archive, []
        self.CreateFileW = Fn(lambda *args: 42)
        self.CloseHandle = Fn(lambda *args: 1)
        self.DeviceIoControl = Fn(self.control)
    def control(self, h, code, ib, il, ob, ol, got, ov):
        if code == app.IOCTL_STATS:
            raw = app.STATS.pack(2, 0, 123, 9, 0, 0)
        elif code == app.IOCTL_PEEK:
            payload = b'RAW-WRITE'
            raw = app.HEADER.pack(2, 1, len(payload), 0, 1, 123, 77) + payload
        elif code == app.IOCTL_ACK:
            self.acked.append(struct.unpack('<QQ', ctypes.string_at(ib, il)))
            # Acknowledgment is never allowed before the chunk transaction commits.
            assert len(self.archive.pending_chunks()) == 1
            self.capture.stop_event.set()
            raw = b''
        else: raise AssertionError(code)
        if raw: ctypes.memmove(ob, raw, len(raw))
        ctypes.cast(got, ctypes.POINTER(wintypes.DWORD))[0] = len(raw)
        return 1

class FakeAssembler:
    def __init__(self): self.chunks = []
    def feed(self, session, seq, target, raw, stamp=None): self.chunks.append((session, seq, target, raw))
    def flush_idle(self, **kwargs): pass

class ProtocolTests(unittest.TestCase):
    def test_ack_follows_durable_store(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Archive(root, PRINTERS); service = app.CaptureService(archive, {})
            kernel = FakeKernel(service, archive); assembler = FakeAssembler()
            with patch.object(ctypes, 'WinDLL', create=True, return_value=kernel):
                service._poll_windows(assembler)
            self.assertEqual(kernel.acked, [(123, 1)])
            self.assertEqual(assembler.chunks, [(123, 1, 1, b'RAW-WRITE')])
            self.assertEqual(len(archive.pending_chunks()), 1)
            self.assertFalse(service.status()['connected'])

    def test_disk_error_does_not_acknowledge_chunk(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Archive(root, PRINTERS); service = app.CaptureService(archive, {})
            kernel = FakeKernel(service, archive)
            with patch.object(ctypes, 'WinDLL', create=True, return_value=kernel), patch.object(archive, 'put_chunk', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'): service._poll_windows(FakeAssembler())
            self.assertEqual(kernel.acked, [])
            self.assertFalse(service.status()['connected'])

    def test_worker_records_failure_in_health_status(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Archive(root, PRINTERS); service = app.CaptureService(archive, {})
            def stop_after_failure(seconds): service.stop_event.set()
            with patch.object(app.os, 'name', 'nt'), patch.object(service, '_poll_windows', side_effect=OSError('disk full')), patch.object(service.stop_event, 'wait', side_effect=stop_after_failure):
                service.run()
            self.assertFalse(service.status()['connected'])
            self.assertIn('disk full', service.status()['last_error'])

class FakeSpool:
    def __init__(self, end_ok):
        self.aborts = 0
        self.OpenPrinterW = Fn(lambda *args: 1)
        self.StartDocPrinterW = Fn(lambda *args: 5)
        self.StartPagePrinter = Fn(lambda *args: 1)
        self.WritePrinter = Fn(self.write)
        self.EndPagePrinter = Fn(lambda *args: 1)
        self.EndDocPrinter = Fn(lambda *args: int(end_ok))
        self.ClosePrinter = Fn(lambda *args: 1)
        self.AbortPrinter = Fn(self.abort)
    def write(self, h, data, length, written):
        ctypes.cast(written, ctypes.POINTER(wintypes.DWORD))[0] = length
        return 1
    def abort(self, h): self.aborts += 1; return 1

class SpoolTests(unittest.TestCase):
    def test_end_document_failure_is_not_success(self):
        spool = FakeSpool(False)
        with patch.object(app.os, 'name', 'nt'), patch.object(ctypes, 'WinDLL', create=True, return_value=spool), patch.object(ctypes, 'get_last_error', create=True, return_value=5):
            with self.assertRaisesRegex(OSError, 'EndDocPrinter failed'):
                app.raw_print('printer', b'^XA^XZ', 'test')
        self.assertEqual(spool.aborts, 1)

    def test_success_does_not_abort(self):
        spool = FakeSpool(True)
        with patch.object(app.os, 'name', 'nt'), patch.object(ctypes, 'WinDLL', create=True, return_value=spool):
            app.raw_print('printer', b'^XA^XZ', 'test')
        self.assertEqual(spool.aborts, 0)

if __name__ == '__main__': unittest.main()
