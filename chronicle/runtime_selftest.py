"""Bundled-runtime checks use only generated media and a toy encrypted database."""
import ctypes
import json
from pathlib import Path
import subprocess
import tempfile


def run():
    from .workbench import mac_modules
    mac_modules()
    from .macos.core import CipherDatabase, valid_page_key
    import pilk
    import struct
    import math
    result = {'source_adapter_imports': True}
    with tempfile.TemporaryDirectory(prefix='chronicle-runtime-test-') as temporary:
        root = Path(temporary)
        key = '12' * 32
        with CipherDatabase(root / 'encrypted.db', key, fixture_write=True) as db:
            db.query('CREATE TABLE sample (value TEXT)')
            db.query("INSERT INTO sample VALUES ('artificial')")
        with CipherDatabase(root / 'encrypted.db', key) as db:
            assert db.query('SELECT value FROM sample')[0]['value'] == 'artificial'
            result['sqlcipher'] = db.query('PRAGMA cipher_version')[0]['cipher_version']
        assert valid_page_key(bytes.fromhex(key), (root / 'encrypted.db').read_bytes()[:4096])
        result['encrypted_database'] = True
        library = ctypes.CDLL(__import__('os').environ['ZSTD_LIBRARY'])
        library.ZSTD_versionString.restype = ctypes.c_char_p
        result['zstd'] = library.ZSTD_versionString().decode()
        image = root / 'test.png'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=32x24',
                        '-frames:v', '1', str(image)], check=True, capture_output=True, timeout=20)
        metadata = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(image)], timeout=20))
        assert metadata['streams'][0]['width'] == 32
        result['ffmpeg_and_ffprobe'] = True
        pcm = root / 'voice.pcm'
        pcm.write_bytes(b''.join(struct.pack('<h', int(3000 * math.sin(i * 2 * math.pi * 440 / 24000))) for i in range(4800)))
        pilk.encode(str(pcm), str(root / 'voice.silk'), pcm_rate=24000, tencent=True)
        pilk.decode(str(root / 'voice.silk'), str(root / 'decoded.pcm'), pcm_rate=24000)
        assert (root / 'decoded.pcm').stat().st_size > 0
        result['silk_codec'] = True
    return result
