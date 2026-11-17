#!/usr/bin/env python3
"""Copy strongly matched files and original voice bytes into a private test export."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .core import save_private
from .reader import MacSource, text_literal


def digest_file(path, algorithm):
    digest = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def store_bytes(directory, data, extension):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    name = hashlib.sha256(data).hexdigest() + extension
    target = directory / name
    if not target.exists():
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
    return name


def voice_bytes(source, message):
    server = int(message.get('server_id') or 0)
    local = int(message['local_id'])
    conversation = text_literal(message['conversation_id'])
    matches = []
    for database in source.entries:
        if not re.fullmatch(r'message/media_\d+\.db', database):
            continue
        with source.open(database) as db:
            if not db.query("SELECT 1 FROM sqlite_master WHERE name='VoiceInfo'"):
                continue
            identity = 'v.svr_id=%d' % server if server else 'v.local_id=%d' % local
            rows = db.query('SELECT hex(v.voice_data) AS data FROM VoiceInfo v '
                            'JOIN Name2Id n ON n.rowid=v.chat_name_id '
                            'WHERE n.user_name=' + conversation + ' AND ' + identity)
            matches.extend(bytes.fromhex(row['data']) for row in rows if row['data'])
    unique = set(matches)
    return next(iter(unique)) if len(unique) == 1 else None


def collect(source, messages, destination, file_index=None):
    destination = Path(destination)
    file_root = source.root.parent / 'msg/file'
    owned_index=None
    results = []
    for message in messages:
        kind = message['type_code'] & 0xffffffff
        if kind not in (49, 34, 3, 43):
            continue
        record = {'archive_identity': message['archive_identity'], 'type_code': message['type_code'],
                  'status': 'unsupported_resource_type'}
        try:
            xml = ET.fromstring(message.get('body', ''))
        except ET.ParseError:
            record['status'] = 'invalid_resource_metadata'
            results.append(record)
            continue
        if kind == 49 and (xml.findtext('.//appmsg/type') == '57' or xml.find('.//refermsg') is not None):
            continue  # Referenced attachments are snapshots, not resources to fetch.
        if kind == 49 and xml.findtext('.//appmsg/type') == '6':
            expected = (xml.findtext('.//appmsg/md5') or '').lower()
            try:expected_size=int(xml.findtext('.//appattach/totallen') or -1)
            except (TypeError,ValueError):
                record['status']='invalid_resource_metadata';results.append(record);continue
            title = xml.findtext('.//appmsg/title') or 'attachment'
            if file_index is None:
                from .resource_index import FileIndex
                owned_index=file_index=FileIndex(file_root)
            indexed=file_index.find(expected_size,expected) if re.fullmatch('[0-9a-f]{32}',expected) else []
            matches=[path for path,_ in indexed]
            if len({digest for _,digest in indexed})>1:
                record['status']='file_ambiguous';results.append(record);continue
            if matches:
                data = matches[0].read_bytes()
                if len(data)!=expected_size or hashlib.md5(data).hexdigest()!=expected or hashlib.sha256(data).hexdigest()!=indexed[0][1]:
                    record['status']='file_changed_during_copy';results.append(record);continue
                extension = Path(title).suffix
                if not re.fullmatch(r'\.[A-Za-z0-9]{1,12}', extension):
                    extension = '.bin'
                record.update(status='copied', name=title, bytes=len(data),
                              local_asset=store_bytes(destination / 'assets', data, extension),
                              matching='message_md5_and_size')
            else:
                record['status'] = 'file_missing_or_unmatched'
        elif kind == 34:
            data = voice_bytes(source, message)
            if data:
                record.update(status='copied', bytes=len(data),
                              local_asset=store_bytes(destination / 'assets', data, '.silk'),
                              matching='conversation_and_message_identity')
            else:
                record['status'] = 'voice_unavailable_or_ambiguous'
        elif kind in (3, 43):
            from .media import collect_visual
            record.update(collect_visual(source, message, xml, destination))
        results.append(record)
    if owned_index is not None:owned_index.close()
    save_private(destination / 'resources.json', {'resources': results})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keys', type=Path, required=True)
    parser.add_argument('--messages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records = collect(MacSource(args.keys), json.loads(args.messages.read_text())['messages'], args.output)
    summary = {}
    for record in records:
        status = record['status']
        summary[status] = summary.get(status, 0) + 1
    print(json.dumps({'resources': len(records), 'status': summary}))


if __name__ == '__main__':
    main()
