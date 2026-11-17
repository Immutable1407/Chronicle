"""Stable WeChat principal binding; physical database paths are only locators."""
import hashlib
import re
from pathlib import Path


from ..account_identity import wechat_account_fingerprint as fingerprint

def folder_principal(folder):
    if folder.startswith('wxid_'):
        match=re.match(r'wxid_[A-Za-z0-9]+',folder)
        return match.group() if match else None
    # Older accounts can use a non-wxid internal username; require the client folder suffix.
    match=re.fullmatch(r'([A-Za-z][A-Za-z0-9_-]{2,})_[A-Za-z0-9]{4}',folder)
    return match.group(1) if match else None


def bind_source(source, manifest):
    legacy=hashlib.sha256(str(source.root).encode()).hexdigest()
    binding=manifest.get('account_identity')
    inferred=folder_principal(source.root.parent.name)
    username=binding.get('user_id') if isinstance(binding,dict) and binding.get('scheme')=='wechat-internal-user-v1' else inferred
    if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{2,127}',username):
        return legacy,[],None,False
    if binding and inferred and inferred!=username:raise ValueError('account_identity_conflict')
    from .reader import text_literal
    if 'contact/contact.db' not in source.entries:return legacy,[],None,False
    with source.open('contact/contact.db') as db:
        rows=db.query('SELECT username FROM contact WHERE username='+text_literal(username)+' LIMIT 1')
    if not rows:return legacy,[],None,False
    account=fingerprint(username)
    if binding and binding.get('fingerprint')!=account:raise ValueError('account_identity_conflict')
    aliases=set(manifest.get('legacy_account_fingerprints',[]));aliases.add(legacy)
    if not all(isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) for value in aliases):raise ValueError('invalid_account_alias')
    value={'scheme':'wechat-internal-user-v1','user_id':username,'fingerprint':account}
    changed=manifest.get('account_identity')!=value or sorted(aliases)!=manifest.get('legacy_account_fingerprints')
    manifest.update(account_identity=value,legacy_account_fingerprints=sorted(aliases))
    return account,sorted(aliases),username,changed


def rebind(manifest, root):
    """Rebind locations only after identity and every existing key are verified."""
    import copy
    from .reader import MacSource
    value=copy.deepcopy(manifest)
    binding=value.get('account_identity')
    previous=fingerprint(binding['user_id']) if binding else None
    if binding and binding.get('fingerprint')!=previous:raise ValueError('account_identity_conflict')
    old_roots={entry['root'] for entry in value['databases']}
    if len(old_roots)!=1:raise ValueError('expected_one_verified_account')
    old_principal=folder_principal(Path(next(iter(old_roots))).parent.name)
    aliases=set(value.get('legacy_account_fingerprints',[]))
    for old_root in old_roots:aliases.add(hashlib.sha256(str(Path(old_root).resolve()).encode()).hexdigest())
    value['legacy_account_fingerprints']=sorted(aliases)
    root=Path(root).resolve(strict=True)
    if root.name!='db_storage':raise ValueError('unexpected_account_directory')
    for entry in value['databases']:entry['root']=str(root)
    materials=value.get('account_material',{})
    if materials:
        if len(materials)!=1:raise ValueError('expected_one_verified_account')
        value['account_material']={str(root):next(iter(materials.values()))}
    candidate=MacSource(key_data=value)
    for name in candidate.entries:
        with candidate.open(name) as db:db.query('SELECT count(*) FROM sqlite_master')
    if not candidate.principal:raise ValueError('verified_account_identity_required')
    if previous and candidate.account!=previous or old_principal and candidate.principal!=old_principal:raise ValueError('account_identity_conflict')
    return candidate.manifest


def main():
    import argparse,json,os
    from .core import save_private
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keys',type=Path,required=True);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.keys.stat().st_mode & 0o077:raise ValueError('key_file_not_private')
    value=rebind(json.loads(args.keys.read_text()),args.root);save_private(args.output,value)
    print(json.dumps({'relocated':True,'account_fingerprint':value['account_identity']['fingerprint']}))


if __name__=='__main__':main()
