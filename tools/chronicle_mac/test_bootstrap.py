"""Lifecycle failure tests; no real app or process is launched."""
import json
import plistlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bootstrap


class RestoreLifecycle(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='chronicle-lifecycle-')
        self.root = Path(self.temporary.name).resolve()
        self.original = self.root / 'Original.app'
        self.executable = self.original / 'Contents/MacOS/WeChat'
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b'artificial-original-binary')
        (self.original/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString':'4.1.15','CFBundleVersion':'270115'}))
        self.digest = bootstrap.fingerprint(self.executable)
        self.shadow = self.root / 'Temporary.app'
        self.shadow.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def test_restore_disables_copy_and_observes_original(self):
        with patch.object(bootstrap, 'run', return_value=b'') as run, \
             patch.object(bootstrap.subprocess, 'run'), \
             patch.object(bootstrap, 'wechat_process_paths', side_effect=[[], [self.executable]]):
            result = bootstrap.restore_original(self.original, self.shadow, self.digest, True)
        self.assertTrue(result['original_process_running'])
        self.assertFalse(self.shadow.exists())
        self.assertTrue(self.shadow.with_suffix('.disabled').is_dir())
        self.assertIn(['/usr/bin/open', '-n', str(self.original)], [c.args[0] for c in run.call_args_list])

    def test_live_copy_prevents_original_launch(self):
        with patch.object(bootstrap, 'run', return_value=b'') as run, \
             patch.object(bootstrap, 'wechat_process_paths', return_value=[self.shadow / 'Contents/MacOS/WeChat']):
            with self.assertRaisesRegex(RuntimeError, 'temporary_process_still_running'):
                bootstrap.restore_original(self.original, self.shadow, self.digest, True)
        self.assertTrue(self.shadow.exists())
        self.assertFalse(any(c.args[0][0] == '/usr/bin/open' for c in run.call_args_list))

    def test_changed_original_stops_restore(self):
        self.executable.write_bytes(b'changed')
        with patch.object(bootstrap, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'original_binary_changed'):
                bootstrap.restore_original(self.original, self.shadow, self.digest, True)
        run.assert_not_called()

    def test_signing_failure_still_runs_restore(self):
        state = self.root / 'state'
        databases = self.root / 'databases'
        databases.mkdir()
        def fake_run(args, **_kwargs):
            if args[:2] == ['/usr/bin/codesign', '--force']:
                raise RuntimeError('artificial_sign_failure')
            if args[0] == '/usr/bin/ditto':
                Path(args[-1]).mkdir()
            return b'/artificial/lldb/python'
        with patch.object(bootstrap, 'wechat_process_paths', return_value=[]), \
             patch.object(bootstrap, 'run', side_effect=fake_run), \
             patch.object(bootstrap, 'event'), \
             patch.object(bootstrap, 'restore_original', return_value={'original_process_running': True}) as restore:
            with self.assertRaisesRegex(RuntimeError, 'artificial_sign_failure'):
                bootstrap.initialize(self.original, state, [databases], 10)
        restore.assert_called_once()
        self.assertEqual(json.loads((state / 'state.json').read_text())['phase'], 'original_restored')
        self.assertEqual(bootstrap.fingerprint(self.executable), self.digest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
