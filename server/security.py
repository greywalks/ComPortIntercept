"""Protect the archive's label data as well as its API credential."""
from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path


def _check_no_links(path):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & 0x400):
        raise RuntimeError(f'Archive data cannot contain links or reparse points: {path}')
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        raise RuntimeError(f'Archive data cannot contain hard-linked files: {path}')


def _windows_acl(path, directory):
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    get_dacl = advapi.GetSecurityDescriptorDacl
    get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    get_dacl.restype = wintypes.BOOL
    set_info = advapi.SetNamedSecurityInfoW
    set_info.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    set_info.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # Protect the DACL against parent inheritance; allow only SYSTEM and the
    # local Administrators group. Directories pass those rights to new children.
    flags = 'OICI' if directory else ''
    sddl = f'D:P(A;{flags};FA;;;SY)(A;{flags};FA;;;BA)'
    descriptor = ctypes.c_void_p()
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise OSError(ctypes.get_last_error(), 'Could not create archive ACL')
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present.value or not dacl.value:
            raise OSError(ctypes.get_last_error(), 'Could not read archive ACL')
        result = set_info(str(path), 1, 0x80000004, None, None, dacl, None)
        if result:
            raise OSError(result, f'Could not protect archive data: {path}')
    finally:
        kernel.LocalFree(descriptor)


def secure_archive_data(data_dir):
    """Fail closed if archive contents cannot be restricted to local admins.

    This changes permissions on existing files inside data_dir. Never follow
    links or hard links to files outside the archive.
    """
    root = Path(data_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_no_links(root)
    paths = [(root, True)]
    def fail(error):
        raise error
    for current, directories, files in os.walk(root, topdown=True, followlinks=False, onerror=fail):
        for name in directories:
            path = Path(current) / name
            _check_no_links(path)
            paths.append((path, True))
        for name in files:
            path = Path(current) / name
            _check_no_links(path)
            paths.append((path, False))
    for path, is_directory in paths:
        # Recheck each object after traversal; the installation directory also
        # must be protected against replacement by untrusted local users.
        _check_no_links(path)
        if os.name == 'nt':
            _windows_acl(path, is_directory)
        else:
            os.chmod(path, 0o700 if is_directory else 0o600)
