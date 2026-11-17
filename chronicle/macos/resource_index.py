"""Private, reusable file inventory. Hashes are cached against file metadata."""
import hashlib
import os
from pathlib import Path
import sqlite3
import time


class FileIndex:
    def __init__(self,root,cache=None,interval=5):
        self.root=Path(root).resolve();self.interval=interval;self.last_scan=None
        if cache:
            cache=Path(cache);cache.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            if not cache.exists():os.close(os.open(cache,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600))
        self.db=sqlite3.connect(cache or ':memory:',check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,ctime INTEGER,device INTEGER,inode INTEGER,md5 TEXT,sha256 TEXT)')
        self.db.execute('CREATE INDEX IF NOT EXISTS file_size ON files(size)')
        self.scan_count=0;self.hash_count=0

    def close(self):self.db.close()

    def refresh(self,force=False):
        now=time.monotonic()
        if not force and self.last_scan is not None and now-self.last_scan<self.interval:return
        self.scan_count+=1;seen=set()
        if self.root.is_dir():
            for directory,folders,files in os.walk(self.root,followlinks=False):
                folders[:]=[name for name in folders if not (Path(directory)/name).is_symlink()]
                for name in files:
                    path=Path(directory)/name
                    if path.is_symlink():continue
                    try:
                        stat=path.stat()
                        if not path.is_file() or self.root not in path.resolve().parents:continue
                        relative=path.relative_to(self.root).as_posix();seen.add(relative)
                        self._update(relative,stat)
                    except (OSError,ValueError):continue
        with self.db:
            self.db.execute('CREATE TEMP TABLE IF NOT EXISTS seen(path TEXT PRIMARY KEY)')
            self.db.execute('DELETE FROM seen');self.db.executemany('INSERT INTO seen VALUES (?)',((p,) for p in seen))
            self.db.execute('DELETE FROM files WHERE path NOT IN (SELECT path FROM seen)')
        self.last_scan=now

    def _update(self,relative,stat):
        self.db.execute('INSERT INTO files VALUES (?,?,?,?,?,?,NULL,NULL) ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,ctime=excluded.ctime,device=excluded.device,inode=excluded.inode,md5=NULL,sha256=NULL WHERE files.size!=excluded.size OR files.mtime!=excluded.mtime OR files.ctime!=excluded.ctime OR files.device!=excluded.device OR files.inode!=excluded.inode',
            (relative,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_dev,stat.st_ino))

    def find(self,size,md5):
        self.refresh()
        result=[]
        for row in self.db.execute('SELECT * FROM files WHERE size=? ORDER BY path',(size,)).fetchall():
            path=self.root/row['path']
            try:
                if path.is_symlink() or self.root not in path.resolve(strict=True).parents:continue
                before=path.stat()
                signature=(before.st_size,before.st_mtime_ns,before.st_ctime_ns,before.st_dev,before.st_ino)
                cached=tuple(row[k] for k in ('size','mtime','ctime','device','inode'))
                if signature!=cached:self._update(row['path'],before)
                if before.st_size!=size:continue
                digest=row['md5'] if signature==cached else None
                sha=row['sha256'] if signature==cached else None
                if not digest or not sha:
                    first=hashlib.md5();second=hashlib.sha256()
                    with path.open('rb') as stream:
                        for block in iter(lambda:stream.read(1024*1024),b''):first.update(block);second.update(block)
                    after=path.stat()
                    if signature!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_dev,after.st_ino):continue
                    digest=first.hexdigest();sha=second.hexdigest();self.hash_count+=1
                    self.db.execute('UPDATE files SET md5=?,sha256=? WHERE path=?',(digest,sha,row['path']))
                if digest==md5:result.append((path,sha))
            except (OSError,ValueError):continue
        self.db.commit()
        return result
