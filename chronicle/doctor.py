"""Read-only dependency report; never installs or starts WeChat."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def report():
    tools={name:shutil.which(name) is not None for name in ('ffmpeg','zstd')}
    lldb=False
    if sys.platform=='darwin':
        try:lldb=subprocess.run(['/usr/bin/xcrun','lldb','-P'],capture_output=True,timeout=15).returncode==0
        except (OSError,subprocess.TimeoutExpired):pass
    return {'python':platform.python_version(),'system':platform.system(),'machine':platform.machine(),
            'demo_ready':sys.version_info>=(3,11),'mac_connection_supported':sys.platform=='darwin' and platform.machine()=='arm64',
            'apple_lldb':lldb,'sqlcipher':any(Path(p).is_file() for p in (os.environ.get('SQLCIPHER_LIBRARY') or '', '/opt/homebrew/opt/sqlcipher/lib/libsqlcipher.dylib','/usr/local/opt/sqlcipher/lib/libsqlcipher.dylib') if p),**tools,'pilk':importlib.util.find_spec('pilk') is not None}


def main():
    value=report();print(json.dumps(value,ensure_ascii=False,indent=2))
    if not value['apple_lldb']:print('First Mac connection: xcode-select --install')
    if not all(value[k] for k in ('sqlcipher','ffmpeg','zstd')):print('Mac reading / media: brew install sqlcipher ffmpeg zstd')
    if not value['pilk']:print('Voice playback: python -m pip install "chronicle-workbench[mac]"')


if __name__=='__main__':main()
