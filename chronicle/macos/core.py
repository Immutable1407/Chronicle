"""Chronicle's database validation primitives (no process access)."""
import ctypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile


def valid_page_key(key, page):
    if len(key) != 32 or len(page) != 4096:
        return False
    authentication_key = hashlib.pbkdf2_hmac(
        'sha512', key, bytes(b ^ 0x3a for b in page[:16]), 2, 32)
    digest = hmac.new(authentication_key, page[16:-64], 'sha512')
    digest.update((1).to_bytes(4, 'little'))
    return hmac.compare_digest(digest.digest(), page[-64:])


def save_private(destination, value):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix='.chronicle-', dir=str(destination.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def encrypted_pages(roots):
    pages = []
    for root in roots:
        root = Path(root).resolve(strict=True)
        for database in sorted(root.rglob('*.db')):
            if database.is_symlink():
                continue
            with database.open('rb') as stream:
                page = stream.read(4096)
            if len(page) == 4096 and not page.startswith(b'SQLite format 3\x00'):
                pages.append((str(root), str(database.relative_to(root)), page))
    return pages


def refresh_keys(key_file):
    key_file = Path(key_file)
    if key_file.stat().st_mode & 0o077:
        raise RuntimeError('key_file_not_private')
    manifest = json.loads(key_file.read_text())
    added = refresh_manifest(manifest)
    if added:
        save_private(key_file, manifest)
    return added


def refresh_manifest(manifest):
    """Update verified in-memory material, also usable with a Keychain-backed profile."""
    materials = manifest.get('account_material', {})
    known = {(entry['root'], entry['database']) for entry in manifest['databases']}
    added = 0
    for root, name, page in encrypted_pages(materials):
        if (root, name) in known:
            continue
        key = hashlib.pbkdf2_hmac('sha512', bytes.fromhex(materials[root]), page[:16], 256000, 32)
        if valid_page_key(key, page):
            manifest['databases'].append({'root': root, 'database': name, 'salt': page[:16].hex(), 'raw_key': key.hex()})
            added += 1
    return added


class CipherDatabase:
    """Uses READONLY and query_only by default; writable mode is for fixtures."""
    def __init__(self, path, raw_key, fixture_write=False):
        if not re.fullmatch(r'[0-9a-fA-F]{64}(?:[0-9a-fA-F]{32})?', raw_key):
            raise ValueError('invalid raw key format')
        libraries = [os.environ.get('SQLCIPHER_LIBRARY'),
                     '/opt/homebrew/opt/sqlcipher/lib/libsqlcipher.dylib',
                     '/usr/local/opt/sqlcipher/lib/libsqlcipher.dylib']
        library = next((p for p in libraries if p and Path(p).is_file()), None)
        if not library:
            raise RuntimeError('sqlcipher_library_missing')
        self.lib = ctypes.CDLL(library)
        signatures = {
            'sqlite3_open_v2': ([ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_char_p], ctypes.c_int),
            'sqlite3_key': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
            'sqlite3_close': ([ctypes.c_void_p], ctypes.c_int),
            'sqlite3_exec': ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            'sqlite3_busy_timeout': ([ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, result
        self.handle = ctypes.c_void_p()
        rc = self.lib.sqlite3_open_v2(os.fsencode(path), ctypes.byref(self.handle),
                                      6 if fixture_write else 1, None)
        try:
            if rc:
                raise RuntimeError('sqlcipher_open_failed:%d' % rc)
            key = ("x'%s'" % raw_key).encode('ascii')
            if self.lib.sqlite3_key(self.handle, key, len(key)):
                raise RuntimeError('sqlcipher_key_setup_failed')
            self.query('PRAGMA cipher_compatibility=4')
            self.query('PRAGMA temp_store=MEMORY')
            self.lib.sqlite3_busy_timeout(self.handle, 2000)
            if not fixture_write:
                self.query('PRAGMA query_only=ON')
            self.query('SELECT count(*) FROM sqlite_master')
        except BaseException:
            self.close()
            raise

    def query(self, statement):
        rows = []
        callback_type = ctypes.CFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p))
        def collect(_context, count, values, names):
            rows.append({names[i].decode('utf-8'): (values[i].decode('utf-8', 'replace')
                         if values[i] is not None else None) for i in range(count)})
            return 0
        callback = callback_type(collect)
        result = self.lib.sqlite3_exec(self.handle, statement.encode('utf-8'), callback, None, None)
        if result:
            raise RuntimeError('sqlcipher_query_failed:%d' % result)
        return rows

    def close(self):
        if self.handle:
            self.lib.sqlite3_close(self.handle)
            self.handle = ctypes.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
