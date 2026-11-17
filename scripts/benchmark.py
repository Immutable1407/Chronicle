#!/usr/bin/env python3
"""Synthetic warm-cache benchmarks. Never opens WeChat or a personal archive."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chronicle.archive import Archive,identity
from chronicle.workbench import Workbench
from chronicle.macos.resource_index import FileIndex


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result={'scope':'generated data, local warm cache after import; no real WeChat account','system':platform.system(),'architecture':platform.machine(),'python':platform.python_version(),'timeline':[]}
    with tempfile.TemporaryDirectory(prefix='chronicle-benchmark-') as folder:
        root=Path(folder)
        with Archive(root/'archive') as archive:
            project=archive.create_project('Synthetic performance fixture');previous=0
            for count in (1000,10000,100000):
                rows=[{'archive_identity':identity('a'*64,'fixture',str(i)),'conversation_id':'fixture','server_id':str(i),'local_id':i,'source_database':'message/message_0.db','sender_id':'fixture-person','timestamp':1794830000+i,'sort_sequence':i,'type_code':1,'body':'synthetic message '+str(i)} for i in range(previous+1,count+1)]
                archive.import_messages(project,{'account_fingerprint':'a'*64,'messages':rows});previous=count
                app=Workbench(archive.root,root/'cache')
                durations=[];queries=[]
                for repeat in range(3):
                    statements=[];archive.db.set_trace_callback(statements.append)
                    start=time.perf_counter();page=archive.timeline_page(project,limit=100);durations.append(time.perf_counter()-start)
                    archive.db.set_trace_callback(None);queries.append(sum(s.lstrip().upper().startswith('SELECT') for s in statements))
                    assert len(page['messages'])==100 and page['total']==count
                start=time.perf_counter();payload=app.timeline(project);http_payload_seconds=time.perf_counter()-start
                assert len(payload['messages'])==100
                result['timeline'].append({'project_records':count,'page_rows':100,'median_archive_seconds':round(statistics.median(durations),4),'workbench_payload_seconds':round(http_payload_seconds,4),'select_statements':max(queries),'response_bytes':len(json.dumps(payload).encode())})
                app.close()
        assets=root/'files';assets.mkdir()
        hashes=[]
        for i in range(100):
            data=hashlib.sha256(str(i).encode()).digest()*32;hashes.append(hashlib.md5(data).hexdigest());(assets/str(i)).write_bytes(data)
        index=FileIndex(assets,root/'file-index.sqlite3')
        start=time.perf_counter()
        for i in range(200):assert len(index.find(1024,hashes[i%100]))==1
        result['file_lookup']={'files':100,'lookups':200,'inventory_scans':index.scan_count,'file_hashes':index.hash_count,'seconds':round(time.perf_counter()-start,4)}
        index.close()
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
