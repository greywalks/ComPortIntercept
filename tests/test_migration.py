import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from server.archive import Archive

class MigrationTests(unittest.TestCase):
    def test_legacy_archive_remains_readable_and_flagged_unverified(self):
        with tempfile.TemporaryDirectory() as root:
            d = Path(root); (d/'jobs').mkdir()
            raw = b'^XA^FDold^FS^XZ'; (d/'jobs'/'0000000001.bin').write_bytes(raw)
            c = sqlite3.connect(d/'archive.db')
            c.executescript('''CREATE TABLE jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL,
                target_id INTEGER NOT NULL,printer_name TEXT NOT NULL,windows_port TEXT NOT NULL,byte_count INTEGER NOT NULL,
                sha256 TEXT NOT NULL,raw_path TEXT NOT NULL,text TEXT,origin TEXT NOT NULL DEFAULT 'Captured',
                parent_job_id INTEGER,reprint_count INTEGER NOT NULL DEFAULT 0,last_reprinted_at TEXT);''')
            c.execute('INSERT INTO jobs(captured_at,target_id,printer_name,windows_port,byte_count,sha256,raw_path,text) VALUES(?,?,?,?,?,?,?,?)',
                      ('2026-01-01',1,'Legacy Printer','USB001',len(raw),hashlib.sha256(raw).hexdigest(),'jobs/0000000001.bin',raw.decode()))
            c.commit(); c.close()
            archive = Archive(root, {1:{'name':'New','windows_port':'USB001'}})
            job = archive.get_job(1)
            self.assertEqual(archive.read_raw(job), raw)
            self.assertEqual(job['integrity'], 'legacy-unverified')
            self.assertEqual(job['boundary_reason'], 'legacy')
            self.assertEqual(len(archive.list_jobs()), 1)

if __name__ == '__main__': unittest.main()
