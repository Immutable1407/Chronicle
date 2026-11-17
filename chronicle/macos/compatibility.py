"""Explicitly validated first-connection client versions."""
from pathlib import Path
import platform
import plistlib

VALIDATED_RELEASES=(('4.1.15','270115'),)


def compatibility(app=Path('/Applications/WeChat.app')):
    version=build=''
    try:
        value=plistlib.loads((Path(app)/'Contents/Info.plist').read_bytes())
        version=str(value.get('CFBundleShortVersionString',''));build=str(value.get('CFBundleVersion',''))
    except (OSError,ValueError,plistlib.InvalidFileException):pass
    supported=platform.system()=='Darwin' and platform.machine()=='arm64' and (version,build) in VALIDATED_RELEASES
    return {'wechat_version':version,'wechat_build':build,'connection_supported':supported,'validated_versions':[v+' ('+b+')' for v,b in VALIDATED_RELEASES]}
