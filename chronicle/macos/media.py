"""Mac image decoding and message-scoped local media resolution."""
import ctypes
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from .reader import text_literal


def aes_ecb(data, key, encrypt=False):
    if len(key) != 16 or not data or len(data) % 16:
        raise ValueError('invalid_aes_input')
    crypto = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    crypto.CCCrypt.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    crypto.CCCrypt.restype = ctypes.c_int
    output = ctypes.create_string_buffer(len(data))
    written = ctypes.c_size_t()
    rc = crypto.CCCrypt(0 if encrypt else 1, 0, 2, key, len(key), None,
                        data, len(data), output, len(data), ctypes.byref(written))
    if rc or written.value != len(data):
        raise ValueError('aes_operation_failed')
    return output.raw[:written.value]


def image_kind(data):
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return '.gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return '.webp'
    if data.startswith(b'wxgf'):
        return '.wxgf'
    return None


def decrypt_dat(data, key, xor):
    if len(data) < 15 or data[:6] not in (b'\x07\x08V1\x08\x07', b'\x07\x08V2\x08\x07'):
        raise ValueError('unsupported_dat_header')
    length, tail_length = struct.unpack_from('<II', data, 6)
    encrypted_length = (length // 16 + 1) * 16 if length else 0
    boundary = 15 + encrypted_length
    tail_start = len(data) - tail_length
    if boundary > tail_start or tail_start < 15:
        raise ValueError('invalid_dat_segments')
    head = b''
    if encrypted_length:
        padded = aes_ecb(data[15:boundary], key)
        padding = padded[-1]
        if not 1 <= padding <= 16 or padded[-padding:] != bytes([padding]) * padding:
            raise ValueError('invalid_dat_padding')
        head = padded[:-padding]
        if len(head) != length:
            raise ValueError('invalid_dat_plaintext_length')
    return head + data[boundary:tail_start] + bytes(byte ^ xor for byte in data[tail_start:])


def key_candidates(source):
    # All identity candidates come from this verified account, never other account folders.
    account = source.root.parent.name
    normalized = (re.match(r'^(wxid_[^_]+)', account).group(1) if account.startswith('wxid_')
                  else re.sub(r'_[A-Za-z0-9]{4}$', '', account))
    identities = {account, normalized}
    app_data = source.root.parent.parent.parent / 'app_data'
    codes = set()
    for relative in ('net/kvcomm', 'ilink/kvcomm'):
        for path in (app_data / relative).glob('key_*_*.statistic'):
            match = re.fullmatch(r'key_(\d+)_.+\.statistic', path.name)
            if match and 0 < int(match[1]) <= 0xffffffff:
                codes.add(int(match[1]))
    for identity in sorted(identities):
        for code in sorted(codes):
            yield hashlib.md5((str(code) + identity).encode()).hexdigest()[:16].encode(), code & 255


def plaintext_variants(data, keys):
    if image_kind(data):
        yield data
        return
    if data[:6] in (b'\x07\x08V1\x08\x07', b'\x07\x08V2\x08\x07'):
        # V1's fixed AES key does not determine its XOR tail. Do not guess a tail
        # merely because one WXGF preview partition happens to remain decodable.
        variants = keys if data[2:4] == b'V2' else []
        for key, xor in variants:
            try:
                raw = decrypt_dat(data, key, xor)
                if image_kind(raw):
                    yield raw
            except ValueError:
                continue
    elif data:
        for signature in (b'\xff\xd8\xff', b'\x89PNG', b'GIF8', b'RIFF'):
            xor = data[0] ^ signature[0]
            if bytes(b ^ xor for b in data[:len(signature)]) == signature:
                decoded = bytes(b ^ xor for b in data)
                if image_kind(decoded):
                    yield decoded


def resource_links(source, message, xml, video=False):
    hashes = set()
    for node in xml.iter():
        for value in list(node.attrib.values()) + [node.text or '']:
            if re.fullmatch(r'[a-fA-F0-9]{32}', value):
                hashes.add(value.lower())
    indexed_hashes = set()
    try:
        with source.open('message/message_resource.db') as db:
            identity = ('i.message_svr_id=%d' % int(message['server_id']) if int(message.get('server_id') or 0)
                        else 'i.message_local_id=%d' % int(message['local_id']))
            rows = db.query('SELECT hex(i.packed_info) AS info,hex(d.packed_info) AS detail '
                'FROM MessageResourceInfo i JOIN MessageResourceDetail d ON d.message_id=i.message_id '
                'JOIN ChatName2Id c ON c.rowid=i.chat_id WHERE c.user_name=' +
                text_literal(message['conversation_id']) + ' AND ' + identity)
        for row in rows:
            for field in ('info', 'detail'):
                indexed_hashes.update(match.decode().lower() for match in re.findall(
                    rb'(?<![a-fA-F0-9])[a-fA-F0-9]{32}(?![a-fA-F0-9])', bytes.fromhex(row[field] or '')))
    except RuntimeError:
        pass
    filenames = set()
    if hashes | indexed_hashes:
        try:
            with source.open('hardlink/hardlink.db') as db:
                table = 'video_hardlink_info_v4' if video else 'image_hardlink_info_v4'
                rows = db.query('SELECT file_name FROM ' + table + ' WHERE md5 IN (' +
                    ','.join(text_literal(value) for value in sorted(hashes | indexed_hashes)) + ')')
            filenames = {Path(row['file_name']).name for row in rows if row['file_name']}
        except RuntimeError:
            pass
    month = datetime.fromtimestamp(message['timestamp']).strftime('%Y-%m')
    account = source.root.parent.resolve()
    directory = account / ('msg/video/' + month if video else
        'msg/attach/' + hashlib.md5(message['conversation_id'].encode()).hexdigest() + '/' + month + '/Img')
    found = []
    if directory.is_dir():
        for path in directory.iterdir():
            if not path.is_file() or path.is_symlink() or account not in path.resolve().parents:
                continue
            stem = re.sub(r'_(?:t|h|hd|s)$', '', path.stem).lower()
            if stem in hashes | indexed_hashes or path.name in filenames:
                found.append(path)
    return sorted(found, key=lambda path: (path.name.endswith('_t.dat'), -path.stat().st_size))


def probe_file(path, video=False):
    ffprobe = shutil.which('ffprobe')
    ffmpeg = shutil.which('ffmpeg')
    if not ffprobe or not ffmpeg:
        raise RuntimeError('ffmpeg_tools_missing')
    arguments = [ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                 '-show_streams', '-show_format', '-of', 'json', str(path)]
    result = subprocess.run(arguments, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('media_probe_failed')
    metadata = json.loads(result.stdout)
    streams = [s for s in metadata.get('streams', []) if s.get('codec_type') == 'video']
    if not streams or not streams[0].get('width') or not streams[0].get('height'):
        raise ValueError('visual_stream_missing')
    selected = streams[0]
    details = {'width': selected['width'], 'height': selected['height'], 'codec': selected['codec_name']}
    if video:
        duration = float(metadata.get('format', {}).get('duration', 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('invalid_video_duration')
        details['duration_seconds'] = duration
    # Decode the visual stream; a header/extension match alone is insufficient.
    result = subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe',
        '-i', str(path), '-map', '0:v:0', '-f', 'null', '-'], capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError('media_decode_failed')
    details['visual_stream_fully_decoded'] = True
    return details


def wxgf_preview(raw):
    if len(raw) < 16 or raw[4] >= len(raw):
        raise ValueError('invalid_wxgf_header')
    partitions = set()
    for marker in (b'\x00\x00\x00\x01', b'\x00\x00\x01'):
        offset = raw[4]
        while True:
            offset = raw.find(marker, offset)
            if offset < 0:
                break
            length = int.from_bytes(raw[offset-4:offset], 'big') if offset >= 4 else 0
            if length > 0 and offset + length <= len(raw):
                partitions.add((offset, length))
            offset += len(marker)
    if not partitions:
        raise ValueError('wxgf_partitions_missing')
    for offset, length in sorted(partitions, key=lambda item: -item[1]):
        result = subprocess.run([shutil.which('ffmpeg') or 'ffmpeg', '-v', 'error', '-xerror',
            '-f', 'hevc', '-i', 'pipe:0', '-frames:v', '1', '-c:v', 'png', '-f', 'image2pipe', 'pipe:1'],
            input=raw[offset:offset+length], capture_output=True, timeout=30)
        if result.returncode == 0 and result.stdout.startswith(b'\x89PNG'):
            return result.stdout
    raise ValueError('wxgf_decode_failed')


def collect_visual(source, message, xml, destination):
    from .resources import store_bytes
    video = message['type_code'] & 0xffffffff == 43
    candidates = resource_links(source, message, xml, video)
    if not candidates:
        return {'status': 'video_missing_or_unmatched' if video else 'image_missing_or_unmatched'}
    keys = list(key_candidates(source)) if not video else []
    for path in candidates:
        stat = path.stat()
        if stat.st_size > 256 * 1024 * 1024:
            continue
        data = path.read_bytes()
        after = path.stat()
        if stat.st_mtime_ns != after.st_mtime_ns or stat.st_size != after.st_size:
            continue
        if video:
            try:
                details = probe_file(path, video=True)
                # Match available message content hashes when the metadata supplies one.
                element = xml.find('.//videomsg')
                digest = element.get('md5', '').lower() if element is not None else ''
                if re.fullmatch('[a-f0-9]{32}', digest) and hashlib.md5(data).hexdigest() != digest:
                    continue
                extension = path.suffix.lower()
                if extension not in ('.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi'):
                    extension = '.video'
                return dict(status='copied', local_asset=store_bytes(destination / 'assets', data, extension),
                            bytes=len(data), matching='message_scoped_resource_index',
                            content_md5_matches_message=bool(re.fullmatch('[a-f0-9]{32}', digest)), **details)
            except (ValueError, subprocess.TimeoutExpired):
                continue
        for raw in plaintext_variants(data, keys):
            extension = image_kind(raw)
            try:
                preview = wxgf_preview(raw) if extension == '.wxgf' else raw
                with tempfile.TemporaryDirectory(prefix='chronicle-image-') as temporary:
                    target = Path(temporary) / ('image.png' if extension == '.wxgf' else 'image' + extension)
                    target.write_bytes(preview)
                    details = probe_file(target)
                record = dict(status='copied', bytes=len(raw),
                    local_asset=store_bytes(destination / 'assets', raw, extension),
                    thumbnail_only=path.name.endswith('_t.dat'), original_quality_verified=False,
                    matching='message_scoped_resource_index', **details)
                message_hashes = {value.lower() for node in xml.iter() for value in node.attrib.values()
                                  if re.fullmatch('[a-fA-F0-9]{32}', value)}
                record['content_md5_matches_message'] = hashlib.md5(raw).hexdigest() in message_hashes
                record['integrity_basis'] = ('message_md5' if record['content_md5_matches_message']
                                             else 'resource_index_and_full_decode')
                if extension == '.wxgf':
                    record['preview_asset'] = store_bytes(destination / 'assets', preview, '.png')
                    record['preview_first_frame_only'] = True
                    record['preview_fully_decoded'] = True
                    record['visual_stream_fully_decoded'] = False
                    record['decode_scope'] = 'largest_decodable_partition_first_frame'
                else:
                    record['decode_scope'] = 'entire_image_stream'
                return record
            except (ValueError, subprocess.TimeoutExpired):
                continue
    return {'status': 'video_decode_or_integrity_failed' if video else 'image_key_or_decode_failed'}
