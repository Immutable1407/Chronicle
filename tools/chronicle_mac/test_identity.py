"""Stable account identity survives physical relocation and preserves legacy aliases."""
import copy
import hashlib
import shutil
from pathlib import Path
import tempfile
import unittest
from chronicle.macos.core import CipherDatabase
from chronicle.macos.reader import MacSource
from chronicle.macos.identity import rebind


class StableAccountIdentity(unittest.TestCase):
    def test_relocated_legacy_manifest_works_without_original_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder).resolve();root=base/'first/wxid_fixtureA_0000/db_storage';root.mkdir(parents=True)
            rows=[];key='12'*32
            specs=[('contact/contact.db',["CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,alias TEXT)","INSERT INTO contact VALUES('wxid_fixtureA','本人','','public-alias')","INSERT INTO contact VALUES('wxid_fixtureB','别人','','other')"]),
                   ('session/session.db',['CREATE TABLE SessionTable(username TEXT,last_timestamp INTEGER)'])]
            table='Msg_'+hashlib.md5(b'fixture-chat').hexdigest()
            specs.append(('message/message_0.db',['CREATE TABLE Name2Id(user_name TEXT)',"INSERT INTO Name2Id VALUES('wxid_fixtureA')",f'CREATE TABLE {table}(local_id INTEGER,server_id INTEGER,local_type INTEGER,create_time INTEGER,sort_seq INTEGER,real_sender_id INTEGER,message_content TEXT)',f"INSERT INTO {table} VALUES(1,987654321,1,1000,1,1,'fixture message')"]))
            for name,statements in specs:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
                with CipherDatabase(path,key,fixture_write=True) as db:
                    for statement in statements:db.query(statement)
                rows.append({'root':str(root),'database':name,'raw_key':key,'salt':path.read_bytes()[:16].hex()})
            legacy={'databases':rows};before=hashlib.sha256((root/'contact/contact.db').read_bytes()).hexdigest()
            source=MacSource(key_data=copy.deepcopy(legacy));self.assertEqual(source.principal,'wxid_fixtureA')
            self.assertNotEqual(source.account,source.legacy_account)
            original_message=source.messages('fixture-chat')[0]['archive_identity']
            moved=base/'second/wxid_fixtureA_1234/db_storage';moved.parent.mkdir(parents=True)
            shutil.move(str(root),moved)
            updated=rebind(legacy,moved);new=MacSource(key_data=updated)
            self.assertEqual(new.account,source.account)
            self.assertEqual(new.messages('fixture-chat')[0]['archive_identity'],original_message)
            self.assertIn(source.legacy_account,new.identity_aliases);self.assertIn(new.legacy_account,new.identity_aliases)
            self.assertEqual(hashlib.sha256((moved/'contact/contact.db').read_bytes()).hexdigest(),before)
            self.assertEqual(new.account_info()['nickname'],'本人')
            # A principal rewrite to a different contact must not silently claim that account.
            forged=copy.deepcopy(updated);forged['account_identity']['user_id']='wxid_fixtureB'
            with self.assertRaisesRegex(ValueError,'account_identity_conflict'):MacSource(key_data=forged)


if __name__=='__main__':unittest.main()
