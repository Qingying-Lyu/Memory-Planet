"""Rebase only paths inside this installation, after an offline SQLite backup."""
import json,sqlite3,uuid
from contextlib import closing
from pathlib import Path
from datetime import datetime,timezone
from .store import ROOT

def relocate():
    state=ROOT/'data/installation.json';state.parent.mkdir(parents=True,exist_ok=True)
    old=json.loads(state.read_text(encoding='utf-8')).get('root') if state.exists() else None
    current=str(ROOT.resolve())
    if old and old!=current:
        def rewrite(value):
            if isinstance(value,str):
                for origin,target in ((old,current),(old.replace('\\','/'),current.replace('\\','/'))):
                    if value==origin or value.startswith(origin+'\\') or value.startswith(origin+'/'):return target+value[len(origin):]
                return value
            if isinstance(value,list):return [rewrite(v) for v in value]
            if isinstance(value,dict):return {k:rewrite(v) for k,v in value.items()}
            return value
        def text(value):
            result=rewrite(value)
            if result!=value:return result
            if value.lstrip().startswith(('{','[')):
                try:
                    original=json.loads(value);updated=rewrite(original)
                    if original!=updated:return json.dumps(updated,ensure_ascii=False)
                except ValueError:pass
            return value
        dbpath=ROOT/'data/learning.sqlite3'
        if dbpath.exists():
            folder=ROOT/'backups';folder.mkdir(exist_ok=True)
            backup=folder/('before-relocation-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.sqlite3')
            with closing(sqlite3.connect(dbpath)) as db,closing(sqlite3.connect(backup)) as copy:
                db.backup(copy)
                if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('迁移前备份校验失败')
                for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                    quoted='"'+table.replace('"','""')+'"'
                    columns=[r[1] for r in db.execute('PRAGMA table_info('+quoted+')')]
                    for row in db.execute('SELECT rowid,* FROM '+quoted).fetchall():
                        changes={key:text(value) for key,value in zip(columns,row[1:]) if isinstance(value,str) and text(value)!=value}
                        if changes:
                            fields=','.join('"'+c.replace('"','""')+'"=?' for c in changes)
                            db.execute('UPDATE '+quoted+' SET '+fields+' WHERE rowid=?',(*changes.values(),row[0]))
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('迁移后数据库校验失败')
                db.commit()
        # Packages and extraction manifests can also hold internal absolute paths.
        for p in (ROOT/'data').rglob('*.json'):
            if p.name in ('installation.json','backend-process.json','local-settings.json') or 'private' in p.parts:continue
            raw=p.read_text(encoding='utf-8-sig');updated=text(raw)
            if raw!=updated:
                temp=p.with_suffix('.relocate-tmp');temp.write_text(updated,encoding='utf-8');temp.replace(p)
    temp=state.with_suffix('.tmp');temp.write_text(json.dumps({'root':current},ensure_ascii=False),encoding='utf-8');temp.replace(state)
