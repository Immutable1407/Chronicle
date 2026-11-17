#!/usr/bin/env python3
"""Assemble a reviewable public source snapshot; never pushes or copies Git history."""
import argparse
from pathlib import Path
import re
import shutil

ROOT=Path(__file__).resolve().parents[1]
FILES=['.gitignore','AGENTS.md','LICENSE','README.md','README.zh-CN.md','CONTRIBUTING.md','SECURITY.md',
       'THIRD_PARTY_NOTICES.md','CHANGELOG.md','pyproject.toml','run.sh','启动 Chronicle.command',
       '.github/workflows/tests.yml','scripts/setup.sh','scripts/publish_snapshot.py','scripts/benchmark.py',
       'config/asr-profile.example.json','tools/mac_wechat_preflight.py','tools/chronicle_mac/README.md',
       'tools/chronicle_mac/requirements-codec.txt',
       'docs/architecture.md','docs/connection.md','docs/agent-setup.md','docs/windows-handoff.md','docs/transcription.md',
       'docs/account-identity.md','docs/encrypted-workspace.md','docs/mac-hardening.md','docs/performance.json',
       'docs/screenshots/workbench.png','docs/screenshots/first-use.png',
       'docs/screenshots/connection-login.png','docs/screenshots/account-confirm.png']


def inputs():
    paths={ROOT/file for file in FILES}
    for directory in ('chronicle','tests','tools/chronicle_mac'):
        paths.update((ROOT/directory).rglob('*.py'))
    paths.update((ROOT/'chronicle/web').glob('*.html'))
    paths.update((ROOT/'chronicle/web').glob('*.css'))
    paths.update((ROOT/'chronicle/web').glob('*.js'))
    return sorted(paths)


def audit(paths):
    failures=[]
    patterns=[r'/'+r'Users/[^/\s]+/',r'gh[pousr]_[A-Za-z0-9]{20,}',r'github_pat_[A-Za-z0-9_]{20,}',
              r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'wxid_(?!fixture)[A-Za-z0-9]{6,}',
              r'(?i)[\"\']raw_key[\"\']\s*:\s*[\"\'][a-f0-9]{64,}[\"\']']
    for path in paths:
        if path.is_symlink() or not path.is_file():failures.append(str(path.relative_to(ROOT))+': missing or symlink');continue
        if path.stat().st_size>2*1024*1024:failures.append(str(path.relative_to(ROOT))+': oversized');continue
        if path.suffix=='.png':continue  # Only explicitly reviewed synthetic screenshots are allowlisted.
        text=path.read_text()
        for pattern in patterns:
            if re.search(pattern,text):failures.append(str(path.relative_to(ROOT))+': privacy scan match');break
    if failures:raise SystemExit('\n'.join(failures))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve()
    if output==ROOT or ROOT in output.parents and output.relative_to(ROOT).parts[0]!='.publish':
        raise SystemExit('Use .publish/NAME or a separate empty directory.')
    if output.exists() and any(output.iterdir()):raise SystemExit('Output must be empty; this tool never overwrites a checkout.')
    paths=inputs();audit(paths)
    output.mkdir(parents=True,exist_ok=True)
    for path in paths:
        target=output/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    print('Public snapshot assembled: %d files. Review it before committing or pushing.'%len(paths))


if __name__=='__main__':main()
