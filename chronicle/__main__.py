"""Small CLI for validating the archive independently of WeChat or a GUI."""
import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

from .archive import Archive
from .asr import Profile, HttpTranscriber, TranscriptionJobs


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.chronicle-export-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('data/archive'))
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create-project')
    create.add_argument('name')
    commands.add_parser('projects')
    importer = commands.add_parser('import')
    importer.add_argument('--project', required=True)
    importer.add_argument('--messages', type=Path, required=True)
    importer.add_argument('--resources', type=Path)
    importer.add_argument('--assets', type=Path)
    importer.add_argument('--selection', type=Path, help='JSON array of selected archive_identity values')
    importer.add_argument('--conversations', type=Path)
    timeline = commands.add_parser('timeline')
    timeline.add_argument('--project', required=True)
    timeline.add_argument('--date')
    timeline.add_argument('--timezone', default='Asia/Shanghai')
    timeline.add_argument('--output', type=Path, required=True)
    note = commands.add_parser('note')
    note.add_argument('--project', required=True)
    note.add_argument('--text', default='')
    note.add_argument('--file', type=Path)
    note.add_argument('--sender', default='我')
    note.add_argument('--at', help='ISO-8601 time including timezone')
    commands.add_parser('verify')
    commands.add_parser('accounts')
    binding=commands.add_parser('bind-account')
    binding.add_argument('--legacy',required=True)
    binding.add_argument('--connection',type=Path,required=True)
    queue = commands.add_parser('queue-transcription')
    queue.add_argument('--message', required=True)
    queue.add_argument('--profile', type=Path, required=True)
    run = commands.add_parser('run-transcription')
    run.add_argument('--job', required=True)
    run.add_argument('--profile', type=Path, required=True)
    retry = commands.add_parser('retry-transcription')
    retry.add_argument('--job', required=True)
    correct = commands.add_parser('correct-transcript')
    correct.add_argument('--message', required=True)
    correct.add_argument('--text', required=True)
    args = parser.parse_args()
    with Archive(args.root) as archive:
        if args.command == 'create-project':
            result = {'project_id': archive.create_project(args.name)}
        elif args.command == 'projects':
            result = {'projects': archive.projects()}
        elif args.command == 'import':
            resources = read_json(args.resources)['resources'] if args.resources else []
            assets = args.assets or (args.resources.parent / 'assets' if args.resources else None)
            names = {r['conversation_id']: r['display_name'] for r in read_json(args.conversations)['conversations']} if args.conversations else {}
            result = archive.import_messages(args.project, read_json(args.messages), resources, assets,
                read_json(args.selection) if args.selection else None, names)
        elif args.command == 'accounts':
            result={'accounts':[dict(row) for row in archive.db.execute('SELECT s.account,count(DISTINCT s.id) AS conversations,count(m.id) AS messages FROM sources s LEFT JOIN messages m ON m.source_id=s.id GROUP BY s.account')]}
        elif args.command == 'bind-account':
            from .macos.reader import MacSource
            source=MacSource(args.connection)
            if not source.principal:raise ValueError('verified_account_identity_required')
            result=archive.bind_account(args.legacy,source.account)
        elif args.command == 'timeline':
            rows = archive.timeline(args.project, args.date, args.timezone)
            write_json(args.output, {'project_id': args.project, 'timezone': args.timezone, 'messages': rows})
            result = {'messages': len(rows), 'dates': dict(Counter(row['datetime'][:10] for row in rows))}
        elif args.command == 'note':
            timestamp = None
            if args.at:
                parsed = datetime.fromisoformat(args.at)
                if parsed.tzinfo is None:
                    parser.error('--at must include a timezone')
                timestamp = int(parsed.timestamp())
            result = {'message_id': archive.add_manual(args.project, args.text, args.file, args.sender, timestamp)}
        elif args.command == 'verify':
            result = archive.verify()
        elif args.command == 'queue-transcription':
            result = {'job_id': TranscriptionJobs(archive).queue(args.message, Profile(**read_json(args.profile)))}
        elif args.command == 'run-transcription':
            worker = HttpTranscriber(Profile(**read_json(args.profile)))
            result = {'state': TranscriptionJobs(archive).run(args.job, worker)}
        elif args.command == 'retry-transcription':
            TranscriptionJobs(archive).retry(args.job)
            result = {'state': 'pending'}
        else:
            archive.correct_transcript(args.message, args.text)
            result = {'state': 'corrected'}
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result.get('state') == 'failed' or result.get('missing') or result.get('corrupt') or result.get('database_ok') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
