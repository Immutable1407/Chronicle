"""Media format, corruption, identity boundary, and real decoder checks."""
from datetime import datetime
import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from media import aes_ecb, decrypt_dat, plaintext_variants, resource_links, collect_visual, probe_file, wxgf_preview


def container(plaintext, key, xor, head_length=32, tail_length=7):
    head = plaintext[:head_length]
    padding = 16 - len(head) % 16
    encrypted = aes_ecb(head + bytes([padding]) * padding, key, encrypt=True)
    middle = plaintext[head_length:len(plaintext)-tail_length]
    tail = bytes(b ^ xor for b in plaintext[-tail_length:])
    return b'\x07\x08V2\x08\x07' + struct.pack('<II', head_length, tail_length) + b'\x00' + encrypted + middle + tail


class ImageCipher(unittest.TestCase):
    def test_nist_aes_block(self):
        key = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
        plain = bytes.fromhex('00112233445566778899aabbccddeeff')
        cipher = bytes.fromhex('69c4e0d86a7b0430d8cdb78070b4c55a')
        self.assertEqual(aes_ecb(cipher, key), plain)
        self.assertEqual(aes_ecb(plain, key, encrypt=True), cipher)

    def test_dat_aligned_head_middle_and_xor_tail(self):
        key = b'chronicle-test01'
        raw = b'\x89PNG\r\n\x1a\n' + bytes(range(120))
        encrypted = container(raw, key, 91)
        self.assertEqual(decrypt_dat(encrypted, key, 91), raw)
        with self.assertRaises(ValueError):
            decrypt_dat(encrypted, b'wrong-secret-key', 91)
        self.assertEqual(list(plaintext_variants(encrypted, [(key, 91)])), [raw])

    def test_invalid_segments_and_padding_rejected(self):
        key = b'chronicle-test01'
        raw = b'\x89PNG\r\n\x1a\n' + bytes(range(120))
        malformed = bytearray(container(raw, key, 7))
        malformed[6:10] = (0xffffffff).to_bytes(4, 'little')
        with self.assertRaises(ValueError):
            decrypt_dat(bytes(malformed), key, 7)
        malformed = bytearray(container(raw, key, 7))
        malformed[15:63] = aes_ecb(raw[:32] + bytes(16), key, encrypt=True)
        with self.assertRaisesRegex(ValueError, 'padding'):
            decrypt_dat(bytes(malformed), key, 7)

    def test_legacy_xor_and_webp_signature(self):
        raw = b'GIF89a' + bytes(range(80))
        self.assertIn(raw, list(plaintext_variants(bytes(b ^ 63 for b in raw), [])))
        self.assertEqual(list(plaintext_variants(b'RIFFxxxxWAVEgarbage', [])), [])

    def test_v1_tail_is_not_guessed_from_preview_success(self):
        raw = b'wxgf' + bytes(124)
        encrypted = container(raw, b'cfcd208495d565ef', 7)
        encrypted = encrypted[:2] + b'V1' + encrypted[4:]
        self.assertEqual(list(plaintext_variants(encrypted, [])), [])


class MediaValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='chronicle-media-tests-')
        cls.root = Path(cls.temporary.name).resolve()
        result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=32x24',
            '-frames:v', '1', '-f', 'image2pipe', '-c:v', 'png', 'pipe:1'], capture_output=True, check=True)
        cls.png = result.stdout
        cls.video = cls.root / 'video.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=32x24:r=25',
            '-t', '0.24', '-an', '-c:v', 'mpeg4', str(cls.video)], capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_header_without_decodable_image_is_rejected(self):
        fake = self.root / 'fake.png'
        fake.write_bytes(b'\x89PNG\r\n\x1a\nnot-a-valid-image')
        with self.assertRaises(ValueError):
            probe_file(fake)

    def test_thumbnail_fallback_is_marked(self):
        path = self.root / 'sample_t.dat'
        key = b'chronicle-test01'
        path.write_bytes(container(self.png, key, 9))
        message = {'type_code': 3}
        with patch('media.resource_links', return_value=[path]), patch('media.key_candidates', return_value=[(key, 9)]):
            result = collect_visual(SimpleNamespace(), message, ET.fromstring('<msg/>'), self.root / 'thumb-export')
        self.assertEqual(result['status'], 'copied')
        self.assertTrue(result['thumbnail_only'])
        self.assertFalse(result['original_quality_verified'])
        self.assertFalse(result['content_md5_matches_message'])

    def test_video_full_decode_and_wrong_digest(self):
        digest = hashlib.md5(self.video.read_bytes()).hexdigest()
        with patch('media.resource_links', return_value=[self.video]):
            correct = collect_visual(SimpleNamespace(), {'type_code': 43},
                ET.fromstring('<msg><videomsg md5="' + digest + '"/></msg>'), self.root / 'video-export')
            wrong = collect_visual(SimpleNamespace(), {'type_code': 43},
                ET.fromstring('<msg><videomsg md5="' + '0'*32 + '"/></msg>'), self.root / 'bad-video-export')
        self.assertEqual(correct['status'], 'copied')
        self.assertTrue(correct['visual_stream_fully_decoded'])
        self.assertEqual(wrong['status'], 'video_decode_or_integrity_failed')

    def test_symlink_cannot_escape_verified_account(self):
        root = self.root / 'account/db_storage'
        root.mkdir(parents=True)
        message = {'conversation_id': 'test-chat', 'server_id': '1', 'type_code': 3,
                   'timestamp': 1700000000, 'local_id': 1}
        token = 'a'*32
        folder = root.parent / 'msg/attach' / hashlib.md5(b'test-chat').hexdigest() / datetime.fromtimestamp(message['timestamp']).strftime('%Y-%m') / 'Img'
        folder.mkdir(parents=True)
        (folder / (token + '.dat')).symlink_to(self.video)
        def unavailable(_name):
            raise RuntimeError('database_unavailable')
        source = SimpleNamespace(root=root, open=unavailable)
        self.assertEqual(resource_links(source, message, ET.fromstring('<msg><img md5="'+token+'"/></msg>')), [])

    def test_wxgf_preserves_container_and_decodes_partition(self):
        encoded = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=32x24',
            '-frames:v', '1', '-c:v', 'libx265', '-x265-params', 'log-level=error:pools=1:frame-threads=1',
            '-f', 'hevc', 'pipe:1'], capture_output=True, check=True).stdout
        raw = b'wxgf' + bytes([16]) + bytes(11) + len(encoded).to_bytes(4, 'big') + encoded
        preview = wxgf_preview(raw)
        self.assertTrue(preview.startswith(b'\x89PNG'))
        with self.assertRaises(ValueError):
            wxgf_preview(b'wxgf' + bytes([16]) + bytes(11) + (0xffffffff).to_bytes(4, 'big') + encoded)


if __name__ == '__main__':
    unittest.main(verbosity=2)
