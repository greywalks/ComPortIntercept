import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from server.security import secure_archive_data

class ArchivePermissionTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX mode test')
    def test_existing_archive_files_are_restricted(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'data'; folder = root / 'jobs'; folder.mkdir(parents=True)
            record = folder / 'job.bin'; record.write_bytes(b'private')
            os.chmod(root, 0o777); os.chmod(folder, 0o777); os.chmod(record, 0o666)
            secure_archive_data(root)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertEqual(record.stat().st_mode & 0o777, 0o600)
            self.assertEqual(record.read_bytes(), b'private')

    @unittest.skipIf(os.name == 'nt', 'link setup varies on Windows')
    def test_rejects_links_without_changing_external_files(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'data'; root.mkdir()
            outside = Path(parent) / 'outside'; outside.write_bytes(b'outside'); os.chmod(outside, 0o644)
            (root / 'link').symlink_to(outside)
            with self.assertRaisesRegex(RuntimeError, 'links|reparse'): secure_archive_data(root)
            self.assertEqual(outside.stat().st_mode & 0o777, 0o644)

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL test')
    def test_windows_removes_untrusted_explicit_acl_from_existing_data(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'data'; folder = root / 'jobs'; folder.mkdir(parents=True)
            record = folder / 'job.bin'; record.write_bytes(b'private')
            subprocess.run(['icacls.exe', str(record), '/grant:r', '*S-1-1-0:F'], check=True, capture_output=True)
            secure_archive_data(root)
            script = Path(parent) / 'acl-check.ps1'
            script.write_text("([System.IO.File]::GetAccessControl($args[0])).Access | ForEach-Object { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value + ':' + $_.IsInherited }", encoding='utf-8')
            output = subprocess.check_output(['powershell.exe', '-NoProfile', '-File', str(script), str(record)], text=True)
            self.assertEqual(set(output.splitlines()), {'S-1-5-18:False', 'S-1-5-32-544:False'})
            self.assertEqual(record.read_bytes(), b'private')

if __name__ == '__main__': unittest.main()
