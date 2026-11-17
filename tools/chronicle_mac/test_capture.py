"""Integration checks using our own native child and encrypted toy database."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from core import CipherDatabase, valid_page_key, refresh_keys


CHILD_SOURCE = r'''
#include <CommonCrypto/CommonKeyDerivation.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(void) {
    const unsigned char salt[16] = "chronicle-test01";
    const char password[] = "chronicle-fixture-only";
    unsigned char derived[32] = {0};
    const char *pidfile = getenv("CHRONICLE_FIXTURE_PID_FILE");
    if (pidfile) { FILE *f = fopen(pidfile, "w"); if (f) { fprintf(f, "%d", getpid()); fclose(f); } }
    if (!getenv("CHRONICLE_FIXTURE_IDLE")) {
        int rc = CCKeyDerivationPBKDF(kCCPBKDF2, password, sizeof(password)-1,
            salt, sizeof(salt), kCCPRFHmacAlgSHA512, 256000, derived, sizeof(derived));
        if (rc) return 4;
    }
    sleep(60);
    return 0;
}
'''


class CaptureIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix='chronicle-artificial-')
        cls.root = Path(cls.workspace.name)
        source = cls.root / 'toy.c'
        source.write_text(CHILD_SOURCE)
        cls.executable = cls.root / 'chronicle-toy'
        subprocess.run(['/usr/bin/xcrun', 'clang', '-O0', '-g', '-arch', 'arm64',
                        str(source), '-o', str(cls.executable)], check=True, capture_output=True)
        cls.salt = b'chronicle-test01'
        cls.key = hashlib.pbkdf2_hmac('sha512', b'chronicle-fixture-only', cls.salt, 256000, 32)
        cls.database = cls.root / 'toy.db'
        with CipherDatabase(cls.database, cls.key.hex() + cls.salt.hex(), fixture_write=True) as db:
            db.query('CREATE TABLE messages (body TEXT)')
            db.query("INSERT INTO messages VALUES ('artificial message')")
        cls.lldb_path = subprocess.check_output(['/usr/bin/xcrun', 'lldb', '-P'], text=True).strip()

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def run_capture(self, name, idle=False, timeout=10):
        output = self.root / name / 'keys.json'
        pidfile = self.root / (name + '.pid')
        env = dict(os.environ, PYTHONPATH=self.lldb_path,
                   CHRONICLE_FIXTURE_PID_FILE=str(pidfile))
        if idle:
            env['CHRONICLE_FIXTURE_IDLE'] = '1'
        result = subprocess.run([
            '/usr/bin/python3', str(Path(__file__).with_name('capture.py')),
            '--executable', str(self.executable), '--database-root', str(self.root),
            '--output', str(output), '--timeout', str(timeout)], env=env,
            capture_output=True, text=True, timeout=40)
        return result, output, pidfile

    def assert_child_closed(self, pidfile):
        self.assertTrue(pidfile.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)

    def test_database_authentication_and_readonly(self):
        page = self.database.read_bytes()[:4096]
        self.assertTrue(valid_page_key(self.key, page))
        self.assertFalse(valid_page_key(bytes(32), page))
        damaged = bytearray(page)
        damaged[100] ^= 1
        self.assertFalse(valid_page_key(self.key, bytes(damaged)))
        with CipherDatabase(self.database, self.key.hex()) as db:
            self.assertEqual(db.query('SELECT body FROM messages')[0]['body'], 'artificial message')
            with self.assertRaises(RuntimeError):
                db.query("INSERT INTO messages VALUES ('must fail')")

    def test_capture_roundtrip_and_private_output(self):
        result, output, pidfile = self.run_capture('success')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads(output.read_text())
        self.assertEqual(manifest['databases'][0]['raw_key'], self.key.hex())
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(output.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(self.key.hex(), result.stdout + result.stderr)
        self.assertNotIn('chronicle-fixture-only', result.stdout + result.stderr)
        self.assert_child_closed(pidfile)
        # New media shards can appear after first login. Refresh without debugging again.
        new_salt = bytes(range(16))
        new_key = hashlib.pbkdf2_hmac('sha512', b'chronicle-fixture-only', new_salt, 256000, 32)
        with CipherDatabase(self.root / 'new-media.db', new_key.hex() + new_salt.hex(), fixture_write=True) as db:
            db.query('CREATE TABLE sample (n INTEGER)')
        self.assertEqual(refresh_keys(output), 1)
        self.assertEqual(refresh_keys(output), 0)
        # A second run must not overwrite an existing result or launch another child.
        repeated = subprocess.run([
            '/usr/bin/python3', str(Path(__file__).with_name('capture.py')),
            '--executable', str(self.executable), '--database-root', str(self.root),
            '--output', str(output)], env=dict(os.environ, PYTHONPATH=self.lldb_path),
            capture_output=True, text=True, timeout=15)
        self.assertEqual(repeated.returncode, 2)
        self.assertNotIn('child_started', repeated.stdout)

    def test_timeout_closes_only_owned_child(self):
        result, output, pidfile = self.run_capture('timeout', idle=True, timeout=2)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertFalse(output.exists())
        self.assert_child_closed(pidfile)

    def test_committed_wal_is_visible(self):
        target = self.root / 'wal.db'
        with CipherDatabase(target, self.key.hex(), fixture_write=True) as writer:
            writer.query('PRAGMA journal_mode=WAL')
            writer.query('PRAGMA wal_autocheckpoint=0')
            writer.query('CREATE TABLE sample (n INTEGER)')
            writer.query('INSERT INTO sample VALUES (7)')
            with CipherDatabase(target, self.key.hex()) as reader:
                self.assertEqual(reader.query('SELECT n FROM sample')[0]['n'], '7')


if __name__ == '__main__':
    unittest.main(verbosity=2)
