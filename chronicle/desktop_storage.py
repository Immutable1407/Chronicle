"""Desktop user data and explicit, validated archive import (no source mutation)."""
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

from .archive import Archive, file_digest
from .__main__ import write_json


def application_home():
    override = os.environ.get('CHRONICLE_HOME')
    root = Path(override).expanduser() if override else Path.home() / 'Library/Application Support/Chronicle'
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root


class DesktopStore:
    def __init__(self, home):
        self.home = Path(home).resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.home, 0o700)
        self.config_path = self.home / 'settings.json'
        self.config = json.loads(self.config_path.read_text()) if self.config_path.is_file() else {
            'version': 1, 'welcome_complete': False, 'profiles': [], 'selected_profile': None}
        self.recover_import()

    def save(self):
        write_json(self.config_path, self.config)

    def recover_import(self):
        journal = self.home / 'archive-import.json'
        if not journal.exists():
            return
        item = json.loads(journal.read_text())
        stage = self.home / item['stage']
        previous = self.home / item['previous']
        if stage.parent != self.home or previous.parent != self.home:
            raise RuntimeError('invalid_import_recovery')
        target = self.home / 'archive'
        if not target.exists():
            if stage.exists():
                stage.rename(target)
            elif previous.exists():
                previous.rename(target)
            else:
                raise RuntimeError('archive_import_recovery_missing')
        if previous.exists():
            shutil.rmtree(previous)
        if stage.exists():
            shutil.rmtree(stage)
        journal.unlink()

    def import_archive(self, source):
        source = Path(source).expanduser().resolve(strict=True)
        target = self.home / 'archive'
        if source == target or not (source / 'archive.sqlite3').is_file() or (source / 'archive.sqlite3').is_symlink():
            raise ValueError('not_a_chronicle_archive')
        with Archive(target) as current:
            if current.projects():
                raise ValueError('archive_must_be_empty_for_import')
        stage = self.home / ('archive-import-' + uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        try:
            incoming = sqlite3.connect((source / 'archive.sqlite3').as_uri() + '?mode=ro', uri=True)
            try:
                version = incoming.execute('PRAGMA user_version').fetchone()[0]
                if version not in (1, 2, 3):
                    raise ValueError('unsupported_archive_version')
                destination = sqlite3.connect(stage / 'archive.sqlite3')
                try:
                    incoming.backup(destination)
                finally:
                    destination.close()
            finally:
                incoming.close()
            os.chmod(stage / 'archive.sqlite3', 0o600)
            with Archive(stage) as copied:
                objects = copied.db.execute('SELECT digest,path,bytes FROM objects').fetchall()
                for row in objects:
                    if not re.fullmatch('[0-9a-f]{64}', row['digest']) or row['path'] != 'objects/' + row['digest'][:2] + '/' + row['digest']:
                        raise ValueError('invalid_archive_object_layout')
                    original = source / row['path']
                    if original.is_symlink() or source not in original.resolve(strict=True).parents:
                        raise ValueError('archive_object_outside_source')
                    output = stage / row['path']
                    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with original.open('rb') as reader, output.open('xb') as writer:
                        shutil.copyfileobj(reader, writer)
                    os.chmod(output, 0o600)
                    if output.stat().st_size != row['bytes'] or file_digest(output) != row['digest']:
                        raise ValueError('archive_object_integrity_failed')
                check = copied.verify()
                if not check['database_ok'] or check['missing'] or check['corrupt']:
                    raise ValueError('imported_archive_invalid')
                count = copied.db.execute('SELECT count(*) FROM messages').fetchone()[0]
                projects = len(copied.projects())
            previous = self.home / ('archive-previous-' + uuid.uuid4().hex)
            write_json(self.home / 'archive-import.json', {'stage': stage.name, 'previous': previous.name})
            target.rename(previous)
            stage.rename(target)
            shutil.rmtree(previous)
            (self.home / 'archive-import.json').unlink()
            self.config['welcome_complete'] = True
            self.save()
            return {'projects': projects, 'messages': count, 'objects': len(objects)}
        except BaseException:
            if (self.home / 'archive-import.json').exists():
                self.recover_import()
            elif stage.exists():
                shutil.rmtree(stage)
            raise
