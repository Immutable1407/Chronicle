#!/usr/bin/env python3
"""Initialize a Chronicle-owned temporary WeChat copy, then restore the original.

This command requires WeChat to be closed beforehand. It never signs the original,
changes SIP, uses sudo, or kills processes by name.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

from .core import CipherDatabase, save_private


def event(kind, **fields):
    print(json.dumps(dict(event=kind, **fields)), flush=True)


def run(arguments, timeout=30):
    result = subprocess.run(arguments, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('command_failed:' + Path(arguments[0]).name)
    return result.stdout


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def wechat_process_paths():
    import ctypes
    result = subprocess.run(['/usr/bin/pgrep', '-x', 'WeChat'], capture_output=True)
    if result.returncode == 1:
        return []
    if result.returncode:
        raise RuntimeError('process_inventory_failed')
    lib = ctypes.CDLL('/usr/lib/libproc.dylib')
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    lib.proc_pidpath.restype = ctypes.c_int
    paths = []
    for token in result.stdout.split():
        if not token.isdigit():
            continue
        buffer = ctypes.create_string_buffer(4096)
        if lib.proc_pidpath(int(token), buffer, len(buffer)) <= 0:
            raise RuntimeError('process_identity_unavailable')
        paths.append(Path(os.fsdecode(buffer.value)).resolve())
    return paths


def restore_original(original, shadow, expected_digest, copy_created):
    executable = original / 'Contents/MacOS/WeChat'
    if fingerprint(executable) != expected_digest:
        raise RuntimeError('original_binary_changed_restore_requires_review')
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(original)])
    paths = wechat_process_paths()
    if any(shadow == candidate or shadow in candidate.parents for candidate in paths):
        raise RuntimeError('temporary_process_still_running')
    if copy_created and shadow.exists():
        registry = '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'
        # Failure to unregister is not evidence that a running process remains.
        subprocess.run([registry, '-u', str(shadow)], capture_output=True, timeout=15)
        shadow.rename(shadow.with_suffix('.disabled'))
    if executable.resolve() not in paths:
        run(['/usr/bin/open', '-n', str(original)])
    for _ in range(30):
        if executable.resolve() in wechat_process_paths():
            return {'original_process_running': True, 'original_binary_unchanged': True,
                    'original_signature_valid': True, 'login_state_verified': False}
        time.sleep(0.2)
    raise RuntimeError('original_launch_not_observed')


def initialize(original, state, roots, timeout):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('native_arm64_macos_required')
    from .compatibility import compatibility
    if not compatibility(original)['connection_supported']:raise RuntimeError('wechat_version_not_validated')
    if os.geteuid() == 0:
        raise RuntimeError('do_not_run_as_root')
    if wechat_process_paths():
        raise RuntimeError('quit_wechat_before_initialization')
    if not roots:
        base = Path.home() / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
        roots = sorted(base.glob('*/db_storage'))
    roots = [root.resolve(strict=True) for root in roots]
    if not roots:
        raise RuntimeError('no_local_database_roots')
    original = original.resolve(strict=True)
    executable = original / 'Contents/MacOS/WeChat'
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(original)])
    before = fingerprint(executable)
    lldb_path = run(['/usr/bin/xcrun', 'lldb', '-P']).decode().strip()
    state = state.resolve()
    state.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(state, 0o700)
    shadow = state / 'Chronicle-WeChat.app'
    key_output = state / 'verified-keys.json'
    record = {'version': 1, 'phase': 'preparing', 'original_digest': before,
              'original': str(original), 'shadow': str(shadow), 'roots': [str(p) for p in roots]}
    save_private(state / 'state.json', record)
    copied = False
    child = None
    result = 2
    try:
        event('preparing_temporary_copy', local_account_candidates=len(roots))
        run(['/usr/bin/ditto', str(original), str(shadow)], timeout=180)
        copied = True
        run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(shadow)], timeout=120)
        run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(shadow)])
        record['phase'] = 'waiting_for_login'
        save_private(state / 'state.json', record)
        arguments = ['/usr/bin/python3', str(Path(__file__).with_name('capture.py')),
                     '--executable', str(shadow / 'Contents/MacOS/WeChat'),
                     '--output', str(key_output), '--timeout', str(timeout)]
        for root in roots:
            arguments.extend(['--database-root', str(root)])
        event('login_required', instruction='Log in only to the account selected for this initialization.')
        child = subprocess.Popen(arguments, env=dict(os.environ, PYTHONPATH=lldb_path))
        result = child.wait(timeout=timeout + 90)
        record['capture_exit_code'] = result
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                # Do not SIGKILL the debugger before it can clean up its owned child.
                raise RuntimeError('capture_cleanup_requires_review')
        result = 130
    finally:
        record['phase'] = 'restoring_original'
        save_private(state / 'state.json', record)
        try:
            restored = restore_original(original, shadow, before, copied)
            record.update(restored)
            record['phase'] = 'original_restored'
            event('original_restored', **restored)
        except Exception as error:
            record['phase'] = 'manual_restore_required'
            record['restore_error'] = str(error)
            save_private(state / 'state.json', record)
            event('manual_restore_required', error_type=type(error).__name__)
            raise
        save_private(state / 'state.json', record)
    if key_output.exists():
        manifest = json.loads(key_output.read_text())
        verified = []
        failures = 0
        for entry in manifest['databases']:
            path = Path(entry['root']) / entry['database']
            try:
                with CipherDatabase(path, entry['raw_key']) as database:
                    tables = database.query("SELECT count(*) AS n FROM sqlite_master WHERE type='table'")
                    query_only = database.query('PRAGMA query_only')[0]['query_only']
                    verified.append({'database': entry['database'], 'table_count': int(tables[0]['n']),
                                     'query_only': query_only == '1'})
            except Exception:
                failures += 1
        record['database_checks'] = verified
        record['failed_database_checks'] = failures
        record['matched_accounts'] = len({row['root'] for row in manifest['databases']})
        record['phase'] = 'verified' if verified and failures == 0 else 'partial'
        save_private(state / 'state.json', record)
        event('database_validation', verified=len(verified), failed=failures,
              matched_accounts=record['matched_accounts'],
              query_only=all(row['query_only'] for row in verified))
        if not verified or failures:
            result = 4
    event('initialization_finished', exit_code=result, phase=record['phase'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=Path('/Applications/WeChat.app'))
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--database-root', type=Path, action='append')
    parser.add_argument('--timeout', type=int, default=240)
    args = parser.parse_args()
    if not 10 <= args.timeout <= 600:
        parser.error('timeout must be 10 to 600 seconds')
    def cancelled(_number, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, cancelled)
    return initialize(args.app, args.state, args.database_root, args.timeout)


if __name__ == '__main__':
    try:
        exit_code = main()
    except Exception as error:
        event('initialization_failed', error_type=type(error).__name__)
        exit_code = 2
    raise SystemExit(exit_code)
