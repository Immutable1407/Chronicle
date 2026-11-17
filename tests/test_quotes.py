from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

from chronicle import Archive
from chronicle.message_format import quoted_reply
from chronicle.workbench import Workbench, present_message
from test_archive import message, bundle


def quote_xml(content, reference_type='1', reply='请参考这个文档', sender='老师'):
    root = ET.Element('msg')
    app = ET.SubElement(root, 'appmsg')
    ET.SubElement(app, 'type').text = '57'
    ET.SubElement(app, 'title').text = reply
    reference = ET.SubElement(app, 'refermsg')
    ET.SubElement(reference, 'type').text = reference_type
    ET.SubElement(reference, 'displayname').text = sender
    ET.SubElement(reference, 'chatusr').text = 'teacher-id'
    ET.SubElement(reference, 'svrid').text = 'source-message-not-archived'
    ET.SubElement(reference, 'content').text = content
    return ET.tostring(root, encoding='unicode')


class QuotePresentation(unittest.TestCase):
    def test_text_reply_and_snapshot_are_separate(self):
        result = quoted_reply(quote_xml('被引用的文字', reply='我的回复'))
        self.assertEqual(result['text'], '我的回复')
        self.assertEqual(result['quote'], {'sender': '老师', 'kind': 'text', 'summary': '被引用的文字'})

    def test_file_snapshot_exposes_title_not_media_credentials(self):
        content = '<msg><appmsg><type>6</type><title>论文规范.pdf</title><aeskey>private-key</aeskey><cdnattachurl>https://example.com/private</cdnattachurl></appmsg></msg>'
        formatted = present_message(message(1, kind=(57 << 32) + 49, body=quote_xml(content, '49')))
        self.assertEqual(formatted['kind'], 'quote')
        self.assertEqual(formatted['text'], '请参考这个文档')
        self.assertEqual(formatted['quote']['summary'], '文件 · 论文规范.pdf')
        self.assertNotIn('private-key', str(formatted))
        self.assertNotIn('https://', str(formatted))

    def test_nested_quote_is_not_recursively_expanded(self):
        inner = quote_xml('deep-private-content', reply='被引用回复的正文')
        result = quoted_reply(quote_xml(inner, '49'))
        self.assertEqual(result['quote']['kind'], 'quote')
        self.assertEqual(result['quote']['summary'], '被引用回复的正文')
        self.assertNotIn('deep-private-content', str(result))

    def test_media_reference_only_has_a_type_label(self):
        for kind, label in [('3', '[图片]'), ('34', '[语音]'), ('43', '[视频]'), ('47', '[表情]')]:
            with self.subTest(kind=kind):
                result = quoted_reply(quote_xml('<private-media-metadata/>', kind))
                self.assertEqual(result['quote']['summary'], label)

    def test_missing_and_malformed_snapshots_do_not_hide_reply(self):
        self.assertIsNone(quoted_reply('malformed XML'))
        self.assertIsNone(quoted_reply('<msg><appmsg><type>6</type></appmsg></msg>'))
        result = quoted_reply('<msg><appmsg><type>57</type><title>保留回复</title></appmsg></msg>')
        self.assertEqual(result['text'], '保留回复')
        self.assertEqual(result['quote']['summary'], '[引用消息]')
        result = quoted_reply(quote_xml('broken media XML', '49', sender=''))
        self.assertEqual(result['quote']['sender'], '未知发言人')
        self.assertEqual(result['quote']['summary'], '[分享消息]')

    def test_text_markup_is_preserved_as_text_and_summary_is_bounded(self):
        result = quoted_reply(quote_xml('<img src=x onerror=unsafe()>'))
        self.assertEqual(result['quote']['summary'], '<img src=x onerror=unsafe()>')
        self.assertLessEqual(len(quoted_reply(quote_xml('x' * 1000))['quote']['summary']), 601)

    def test_archive_does_not_import_reference_or_create_an_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            row = message(1, kind=(57 << 32) + 49, body=quote_xml('<msg><appmsg><type>6</type><title>not-downloaded.pdf</title></appmsg></msg>', '49'))
            with Archive(Path(directory) / 'archive') as archive:
                project = archive.create_project('quote test')
                archive.import_messages(project, bundle(row))
                result = archive.timeline(project)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['attachments'], [])
                self.assertEqual(archive.verify()['objects'], 0)
                # Simulate a legacy placeholder; the presentation must suppress it without deleting stored data.
                with archive.db:
                    archive._link_asset(row['archive_identity'], 'primary', None, 'unsupported_resource_type', {})
            app = Workbench(Path(directory) / 'archive', Path(directory) / 'cache')
            view = app.timeline(project)
            self.assertEqual(view['total'], 1)
            self.assertEqual(view['resources'], 0)
            self.assertEqual(view['messages'][0]['attachments'], [])
            self.assertEqual(view['messages'][0]['kind'], 'quote')
            app.close()


if __name__ == '__main__':
    unittest.main()
