#!/usr/bin/env python3
"""Inspect connection prerequisites; never read chat data or extract keys.

--probe-read-port requests and immediately releases a dedicated read port.
It does not read target memory, attach a debugger, or suspend a process.
"""

import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, timeout=15)
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return None, b"", b""


def request_read_port(pid):
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    try:
        request = lib.task_read_for_pid
    except AttributeError:
        return {"status": "api_unavailable"}
    port_type = ctypes.c_uint32
    lib.mach_task_self.argtypes = []
    lib.mach_task_self.restype = port_type
    request.argtypes = [port_type, ctypes.c_int, ctypes.POINTER(port_type)]
    request.restype = ctypes.c_int
    lib.mach_port_deallocate.argtypes = [port_type, port_type]
    lib.mach_port_deallocate.restype = ctypes.c_int
    owner = lib.mach_task_self()
    port = port_type()
    ctypes.set_errno(0)
    result = request(owner, pid, ctypes.byref(port))
    error = ctypes.get_errno()
    granted = result == 0 and port.value != 0
    observation = {
        "status": "granted" if granted else "denied",
        "return_code": result,
        "errno": error,
        "errno_name": errno.errorcode.get(error) if error else None,
        "target_memory_read": False,
    }
    if port.value:
        observation["release_return_code"] = lib.mach_port_deallocate(owner, port)
    return observation


def matching_processes(executable):
    rc, output, _ = command(["/usr/bin/pgrep", "-x", executable.name])
    if rc not in (0, 1):
        return None
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    lib.proc_pidpath.restype = ctypes.c_int
    matches = []
    for value in output.split():
        if not value.isdigit():
            continue
        pid = int(value)
        buffer = ctypes.create_string_buffer(4096)
        if lib.proc_pidpath(pid, buffer, len(buffer)) <= 0:
            continue
        if Path(os.fsdecode(buffer.value)).resolve() == executable:
            matches.append(pid)
    return matches


def inspect(app, probe):
    report = {
        "schema_version": 1,
        "host": {"os": platform.system(), "version": platform.mac_ver()[0],
                 "architecture": platform.machine()},
        "scope": {"chat_data_read": False, "key_extraction": False,
                  "process_restart": False, "signatures_changed": False,
                  "system_settings_changed": False},
    }
    if platform.system() != "Darwin":
        report["status"] = "unsupported_platform"
        return report
    rc, output, _ = command(["/usr/bin/csrutil", "status"])
    text = output.decode(errors="replace").lower()
    report["sip"] = ("enabled" if "status: enabled" in text else
                     "disabled" if "status: disabled" in text else "unknown")
    app = app.expanduser().resolve()
    try:
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        report["status"] = "app_metadata_unavailable"
        return report
    if not isinstance(info, dict) or info.get("CFBundleIdentifier") != "com.tencent.xinWeChat":
        report["status"] = "unexpected_app_identity"
        return report
    report["app"] = {"version": info.get("CFBundleShortVersionString"),
                     "build": info.get("CFBundleVersion")}
    executable_name = info.get("CFBundleExecutable")
    if not isinstance(executable_name, str) or Path(executable_name).name != executable_name:
        report["status"] = "invalid_executable_name"
        return report
    executable = (app / "Contents/MacOS" / executable_name).resolve()
    rc, _, sign_output = command(["/usr/bin/codesign", "-dv", str(app)])
    report["app"]["hardened_runtime"] = (
        "(runtime)" in sign_output.decode(errors="replace") if rc == 0 else None)
    rc, entitlements, _ = command([
        "/usr/bin/codesign", "-d", "--entitlements", "-", "--xml", str(app)])
    try:
        entitlements = plistlib.loads(entitlements) if rc == 0 else None
    except (plistlib.InvalidFileException, ValueError):
        entitlements = None
    report["app"]["get_task_allow"] = (
        bool(entitlements.get("com.apple.security.get-task-allow", False))
        if isinstance(entitlements, dict) else None)
    report["tools"] = {}
    for name, args in {
        "developer_tools": ["/usr/bin/xcode-select", "-p"],
        "lldb": ["/usr/bin/xcrun", "--find", "lldb"],
    }.items():
        rc, _, _ = command(args)
        report["tools"][name] = rc == 0
    processes = matching_processes(executable)
    report["app"]["matching_process_count"] = len(processes) if processes is not None else None
    if probe:
        control = request_read_port(os.getpid())
        report["read_port_probe"] = {
            "self_control": control,
            "targets": ([request_read_port(pid) for pid in processes] if processes else []),
            "target_status": ("running" if processes else "not_running" if processes == []
                              else "process_inventory_unavailable"),
        }
    report["status"] = "observed"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("/Applications/WeChat.app"))
    parser.add_argument("--probe-read-port", action="store_true")
    args = parser.parse_args()
    report = inspect(args.app, args.probe_read_port)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "observed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
