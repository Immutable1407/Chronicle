#!/usr/bin/env python3
"""Observe key derivation only in an explicitly launched child executable."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import time

if __package__:
    from .core import encrypted_pages, save_private, valid_page_key
else:
    from core import encrypted_pages, save_private, valid_page_key


def event(kind, **fields):
    print(json.dumps(dict(event=kind, **fields)), flush=True)


def capture(executable, roots, output, timeout):
    import lldb
    if Path(output).exists():
        raise RuntimeError('key_output_already_exists')
    pages = encrypted_pages(roots)
    if not pages:
        raise RuntimeError('no_encrypted_pages')
    by_salt = {}
    for root, name, page in pages:
        by_salt.setdefault(page[:16], []).append((root, name, page))
    mac_salts = {bytes(b ^ 0x3a for b in salt): salt for salt in by_salt}
    debugger = lldb.SBDebugger.Create()
    debugger.SetAsync(True)
    debugger.SkipLLDBInitFiles(True)
    process = None
    found = {}
    account_material = {}
    counts = {'derivation_calls': 0, 'matching_calls': 0}
    def cancelled(_signal, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, cancelled)
    try:
        target = debugger.CreateTarget(str(Path(executable).resolve(strict=True)))
        if not target.IsValid() or not target.GetTriple().startswith('arm64'):
            raise RuntimeError('requires_arm64_executable')
        breakpoint = target.BreakpointCreateByName('CCKeyDerivationPBKDF')
        launch = lldb.SBLaunchInfo([])
        inherited = ('HOME', 'PATH', 'TMPDIR', 'USER', 'LOGNAME', 'LANG', 'LC_CTYPE',
                     'CHRONICLE_FIXTURE_PID_FILE', 'CHRONICLE_FIXTURE_IDLE')
        launch.SetEnvironmentEntries([name + '=' + os.environ[name]
                                     for name in inherited if name in os.environ], False)
        for descriptor in (0, 1, 2):
            launch.AddSuppressFileAction(descriptor, descriptor == 0, descriptor != 0)
        error = lldb.SBError()
        process = target.Launch(launch, error)
        if error.Fail() or not process.IsValid():
            raise RuntimeError('child_launch_failed')
        event('child_started', candidate_databases=len(pages))
        deadline = time.monotonic() + timeout
        listener = debugger.GetListener()
        while time.monotonic() < deadline:
            notification = lldb.SBEvent()
            if not listener.WaitForEvent(1, notification):
                continue
            if not lldb.SBProcess.EventIsProcessEvent(notification):
                continue
            state = lldb.SBProcess.GetStateFromEvent(notification)
            if state in (lldb.eStateExited, lldb.eStateDetached, lldb.eStateCrashed):
                break
            if state != lldb.eStateStopped:
                continue
            for thread in process:
                if (thread.GetStopReason() != lldb.eStopReasonBreakpoint or
                    thread.GetStopReasonDataCount() < 2 or
                    thread.GetStopReasonDataAtIndex(0) != breakpoint.GetID()):
                    continue
                counts['derivation_calls'] += 1
                frame = thread.GetFrameAtIndex(0)
                arguments = [frame.FindRegister('x%d' % i).GetValueAsUnsigned() for i in range(7)]
                algorithm, password, length, salt_pointer, salt_length, prf, rounds = arguments
                if algorithm != 2 or salt_length != 16 or not 0 < length <= 256 or prf != 5:
                    continue
                if rounds not in (2, 256000):
                    continue
                def read(address, size):
                    err = lldb.SBError()
                    value = process.ReadMemory(address, size, err)
                    return bytes(value) if err.Success() and len(value) == size else b''
                observed_salt = read(salt_pointer, 16)
                salt = observed_salt if rounds == 256000 else mac_salts.get(observed_salt)
                if salt not in by_salt:
                    continue
                secret = read(password, length)
                if len(secret) != length:
                    continue
                counts['matching_calls'] += 1
                key = (hashlib.pbkdf2_hmac('sha512', secret, salt, rounds, 32)
                       if rounds == 256000 else secret)
                updated = False
                for root, name, page in by_salt[salt]:
                    identity = root + '/' + name
                    if identity not in found and valid_page_key(key, page):
                        found[identity] = {'root': root, 'database': name, 'salt': salt.hex(), 'raw_key': key.hex()}
                        updated = True
                # One confirmed account passphrase may also unlock its other shards.
                # Never apply it to another account just because that account is on disk.
                if rounds == 256000 and updated:
                    confirmed_roots = {entry['root'] for entry in found.values()}
                    for confirmed_root in confirmed_roots:
                        account_material[confirmed_root] = secret.hex()
                    for other_root, name, page in pages:
                        identity = other_root + '/' + name
                        if other_root not in confirmed_roots or identity in found:
                            continue
                        candidate = hashlib.pbkdf2_hmac('sha512', secret, page[:16], 256000, 32)
                        if valid_page_key(candidate, page):
                            found[identity] = {'root': other_root, 'database': name,
                                               'salt': page[:16].hex(), 'raw_key': candidate.hex()}
                if updated:
                    save_private(output, {'version': 1, 'databases': list(found.values()),
                                          'account_material': account_material})
                    event('keys_verified', verified=len(found), candidates=len(pages))
            # Resume our child even when a stop does not match the watched call.
            if process.Continue().Fail():
                raise RuntimeError('child_resume_failed')
            matched_roots = {entry['root'] for entry in found.values()}
            if matched_roots and len(found) == sum(root in matched_roots for root, _, _ in pages):
                break
        event('capture_completed', verified=len(found), candidates=len(pages), **counts)
        return 0 if found else 3
    finally:
        if process is not None and process.IsValid():
            if process.GetState() not in (lldb.eStateExited, lldb.eStateDetached, lldb.eStateInvalid):
                process.Kill()
            event('owned_child_closed')
        lldb.SBDebugger.Destroy(debugger)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--database-root', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error('timeout must be between 1 and 600 seconds')
    return capture(args.executable, args.database_root, args.output, args.timeout)


if __name__ == '__main__':
    try:
        result = main()
    except KeyboardInterrupt:
        event('capture_cancelled')
        result = 130
    except Exception as error:
        # Do not include debugger/native exception text which might contain memory.
        event('capture_failed', error_type=type(error).__name__)
        result = 2
    raise SystemExit(result)
