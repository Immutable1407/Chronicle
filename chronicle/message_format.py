"""Small, non-recursive presentation snapshots for WeChat quoted replies."""
import xml.etree.ElementTree as ET


def parse_xml(value):
    if isinstance(value, ET.Element):
        return value
    try:
        return ET.fromstring(value)
    except (ET.ParseError, TypeError, ValueError):
        return None


def app_message(root):
    if root is None:
        return None
    return root if root.tag == 'appmsg' else root.find('./appmsg')


def quoted_reply(value, people=None):
    app = app_message(parse_xml(value))
    if app is None:
        return None
    reference = app.find('./refermsg')
    if app.findtext('./type') != '57' and reference is None:
        return None
    reply = app.findtext('./title') or ''
    quote = {'sender': '未知发言人', 'kind': 'other', 'summary': '[引用消息]'}
    if reference is not None:
        people = people or {}
        author = reference.findtext('./chatusr') or reference.findtext('./fromusr') or ''
        quote['sender'] = reference.findtext('./displayname') or people.get(author) or '未知发言人'
        content = reference.findtext('./content') or ''
        kind = reference.findtext('./type') or ''
        if kind == '1':
            quote.update(kind='text', summary=content or '[文字消息]')
        elif kind in ('3', '34', '43', '47'):
            label = {'3': ('image', '[图片]'), '34': ('voice', '[语音]'),
                     '43': ('video', '[视频]'), '47': ('sticker', '[表情]')}[kind]
            quote.update(kind=label[0], summary=label[1])
        elif kind == '49':
            nested = app_message(parse_xml(content))
            if nested is not None:
                subtype = nested.findtext('./type')
                title = nested.findtext('./title') or ''
                if subtype == '6':
                    quote.update(kind='file', summary='文件 · ' + (title or '[未提供文件名]'))
                elif subtype == '57':
                    # Only the cited reply's own text; never follow its refermsg.
                    quote.update(kind='quote', summary=title or '[引用回复]')
                else:
                    quote.update(kind='other', summary=title or '[分享消息]')
            else:
                quote['summary'] = '[分享消息]'
        elif content and not content.lstrip().startswith('<'):
            quote['summary'] = content
    quote['sender'] = quote['sender'][:200]
    summary = ' '.join(quote['summary'].split())
    quote['summary'] = summary[:600] + ('…' if len(summary) > 600 else '')
    return {'text': reply, 'quote': quote}


def is_quoted_reply(message):
    return (message.get('account') != 'manual' and int(message.get('type_code', 0)) & 0xffffffff == 49
            and quoted_reply(message.get('body', '')) is not None)
