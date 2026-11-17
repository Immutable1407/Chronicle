"""Bounded archive reads and grouped project statistics; no live source access."""
from collections import defaultdict
from datetime import datetime,time as day_time,timedelta
import json
from zoneinfo import ZoneInfo
from .archive import identity
from .message_format import is_quoted_reply

COLUMNS='m.id,m.source_id,m.sender,m.timestamp,m.sequence,m.type_code,m.body,m.precision,s.name AS conversation_name,s.conversation,s.account,coalesce(n.name,m.sender) AS sender_name'
JOIN=' FROM messages m JOIN project_messages p ON p.message_id=m.id JOIN sources s ON s.id=m.source_id LEFT JOIN people n ON n.account=s.account AND n.person=m.sender '


def select_rows(archive,project,date=None,timezone='Asia/Shanghai',*,limit=None,after=None,query='',source='',files=False):
    archive._require_project(project)
    zone=ZoneInfo(timezone);conditions=['p.project_id=?'];params=[project]
    if date:
        start=datetime.combine(datetime.strptime(date,'%Y-%m-%d').date(),day_time(),zone)
        end=start+timedelta(days=1)
        conditions+=['m.timestamp>=? AND m.timestamp<?'];params +=[int(start.timestamp()),int(end.timestamp())]
    if source:conditions+=['m.source_id=?'];params +=[source]
    archive.db.create_function('chronicle_quote',2,lambda kind,body:int(is_quoted_reply({'type_code':kind,'body':body})),deterministic=True)
    if files:conditions+=["(m.type_code & 4294967295)!=1 AND NOT chronicle_quote(m.type_code,m.body) AND EXISTS(SELECT 1 FROM attachments a WHERE a.message_id=m.id)"]
    query=query.strip()
    if len(query)>500:raise ValueError('query_too_long')
    if query:
        terms=["instr(lower(m.body),lower(?))>0","instr(lower(coalesce(n.name,m.sender,'')),lower(?))>0","instr(lower(s.name),lower(?))>0",
               "EXISTS(SELECT 1 FROM attachments a WHERE a.message_id=m.id AND instr(lower(a.metadata),lower(?))>0)",
               "EXISTS(SELECT 1 FROM transcript_edits e WHERE e.message_id=m.id AND instr(lower(e.text),lower(?))>0)",
               "EXISTS(SELECT 1 FROM message_jobs j JOIN transcription_jobs t ON t.id=j.job_id WHERE j.message_id=m.id AND instr(lower(coalesce(t.text,'')),lower(?))>0)"]
        conditions+=['('+' OR '.join(terms)+')'];params +=[query]*len(terms)
    where=' WHERE '+' AND '.join(conditions)
    matched=archive.db.execute('SELECT count(*)'+JOIN+where,params).fetchone()[0] if limit else None
    scope=identity(project,date or '',timezone,query,source,files)
    if after:
        if not isinstance(after,dict) or after.get('scope')!=scope:raise ValueError('invalid_timeline_cursor')
        position=after.get('position')
        if not isinstance(position,list) or len(position)!=4 or not isinstance(position[0],int) or not isinstance(position[2],int) or not all(isinstance(position[i],str) for i in (1,3)):
            raise ValueError('invalid_timeline_cursor')
        where+=' AND (m.timestamp,m.source_id,m.sequence,m.id)>(?,?,?,?)';params+=position
    sql='SELECT '+COLUMNS+JOIN+where+' ORDER BY m.timestamp,m.source_id,m.sequence,m.id'
    if limit:
        if not isinstance(limit,int) or not 1<=limit<=200:raise ValueError('invalid_page_size')
        sql+=' LIMIT ?';params.append(limit+1)
    rows=[dict(row) for row in archive.db.execute(sql,params)]
    has_more=bool(limit and len(rows)>limit)
    rows=rows[:limit] if limit else rows
    cursor={'scope':scope,'position':[rows[-1][k] for k in ('timestamp','source_id','sequence','id')]} if has_more else None
    return hydrate(archive,rows,zone),cursor,matched


def hydrate(archive,rows,zone):
    for start in range(0,len(rows),400):
        chunk=rows[start:start+400];by_id={row['id']:row for row in chunk};ids=list(by_id)
        marks=','.join('?' for _ in ids)
        for row in chunk:
            row.update(datetime=datetime.fromtimestamp(row['timestamp'],zone).isoformat(),attachments=[],transcript={'state':'not_requested'},source_observations=0,source_conflict=False)
        for item in archive.db.execute(f'SELECT message_id,count(*) AS n,count(DISTINCT semantic_digest) AS variants FROM observations WHERE message_id IN ({marks}) GROUP BY message_id',ids):
            by_id[item['message_id']].update(source_observations=item['n'],source_conflict=item['variants']>1)
        for item in archive.db.execute(f'SELECT a.*,o.path AS archive_path FROM attachments a LEFT JOIN objects o ON o.digest=a.object_id WHERE a.message_id IN ({marks}) ORDER BY a.message_id,a.role',ids):
            asset=dict(item);asset['metadata']=json.loads(asset['metadata']);by_id[item['message_id']]['attachments'].append(asset)
        corrected=set()
        for item in archive.db.execute(f'SELECT e.message_id,e.text FROM transcript_edits e JOIN (SELECT message_id,max(id) AS id FROM transcript_edits WHERE message_id IN ({marks}) GROUP BY message_id) latest ON e.id=latest.id',ids):
            corrected.add(item['message_id']);by_id[item['message_id']]['transcript']={'state':'corrected','text':item['text'],'origin':'manual'}
        for item in archive.db.execute(f"SELECT * FROM (SELECT j.*,m.message_id,row_number() OVER(PARTITION BY m.message_id ORDER BY (j.status='succeeded') DESC,j.updated_at DESC,j.rowid DESC) AS ordinal FROM transcription_jobs j JOIN message_jobs m ON m.job_id=j.id WHERE m.message_id IN ({marks})) WHERE ordinal=1",ids):
            if item['message_id'] not in corrected:by_id[item['message_id']]['transcript']={'state':item['status'],'text':item['text'],'error_code':item['error_code']}
    return rows


def summary(archive,project,timezone='Asia/Shanghai'):
    archive._require_project(project)
    zone=ZoneInfo(timezone)
    archive.db.create_function('chronicle_day',1,lambda value:datetime.fromtimestamp(value,zone).date().isoformat(),deterministic=True)
    archive.db.create_function('chronicle_quote',2,lambda kind,body:int(is_quoted_reply({'type_code':kind,'body':body})),deterministic=True)
    rows=archive.db.execute('SELECT chronicle_day(m.timestamp) AS day,count(*) AS n FROM messages m JOIN project_messages p ON p.message_id=m.id WHERE p.project_id=? GROUP BY day',(project,))
    days={row['day']:row['n'] for row in rows}
    stats=archive.db.execute("SELECT count(DISTINCT CASE WHEN s.account!='manual' THEN s.id END) AS sources,sum(CASE WHEN EXISTS(SELECT 1 FROM attachments a WHERE a.message_id=m.id AND a.role='primary') AND NOT chronicle_quote(m.type_code,m.body) THEN 1 ELSE 0 END) AS resources"+JOIN+' WHERE p.project_id=?',(project,)).fetchone()
    sources=[dict(row) for row in archive.db.execute("SELECT DISTINCT s.id,s.name FROM sources s JOIN messages m ON m.source_id=s.id JOIN project_messages p ON p.message_id=m.id WHERE p.project_id=? ORDER BY s.name,s.id",(project,))]
    return {'total':sum(days.values()),'days':days,'sources':stats['sources'],'resources':stats['resources'] or 0,'source_options':sources}
