"""Resource matching checks using artificial files and encrypted media rows."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from core import CipherDatabase
from resources import collect, voice_bytes


class ResourceMatching(unittest.TestCase):
    def test_quoted_file_is_not_collected_as_an_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = SimpleNamespace(root=root / 'account/db_storage')
            row = {'archive_identity': 'quoted-file', 'type_code': (57 << 32) + 49,
                   'body': '<msg><appmsg><type>57</type><title>回复</title><refermsg><type>49</type><content>引用的附件</content></refermsg></appmsg></msg>'}
            self.assertEqual(collect(source, [row], root / 'export'), [])
            self.assertFalse((root / 'export/assets').exists())

    def test_file_content_wins_over_filename(self):
        with tempfile.TemporaryDirectory(prefix='chronicle-resource-') as temporary:
            root = Path(temporary)
            files = root / 'account/msg/file'
            files.mkdir(parents=True)
            correct = b'artificial-correct'
            (files / 'requested.pdf').write_bytes(b'artificial-wrong!!')
            (files / 'renamed.pdf').write_bytes(correct)
            md5 = hashlib.md5(correct).hexdigest()
            message = {'type_code': 49, 'archive_identity': 'artificial',
                'body': '<msg><appmsg><type>6</type><title>requested.pdf</title><md5>' + md5 +
                        '</md5><appattach><totallen>' + str(len(correct)) + '</totallen></appattach></appmsg></msg>'}
            output = root / 'export'
            rows = collect(SimpleNamespace(root=root / 'account/db_storage'), [message], output)
            self.assertEqual(rows[0]['status'], 'copied')
            self.assertEqual((output / 'assets' / rows[0]['local_asset']).read_bytes(), correct)
            self.assertEqual((output / 'assets' / rows[0]['local_asset']).stat().st_mode & 0o777, 0o600)

    def test_voice_identity_is_scoped_to_conversation(self):
        with tempfile.TemporaryDirectory(prefix='chronicle-voice-') as temporary:
            path = Path(temporary) / 'media.db'
            key = '34' * 32
            with CipherDatabase(path, key, fixture_write=True) as db:
                db.query('CREATE TABLE Name2Id (user_name TEXT)')
                db.query("INSERT INTO Name2Id VALUES ('first'),('second')")
                db.query('CREATE TABLE VoiceInfo (chat_name_id INTEGER,svr_id INTEGER,local_id INTEGER,voice_data BLOB)')
                db.query("INSERT INTO VoiceInfo VALUES (1,77,1,x'0102'),(2,77,1,x'0304')")
            source = SimpleNamespace(entries=['message/media_0.db'],
                                     open=lambda _name: CipherDatabase(path, key))
            first = voice_bytes(source, {'server_id': '77', 'local_id': 1, 'conversation_id': 'first'})
            second = voice_bytes(source, {'server_id': '77', 'local_id': 1, 'conversation_id': 'second'})
            self.assertEqual(first, b'\x01\x02')
            self.assertEqual(second, b'\x03\x04')


if __name__ == '__main__':
    unittest.main(verbosity=2)
