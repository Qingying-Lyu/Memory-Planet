"""Local verified backups and conservative cleanup. Credentials are excluded."""
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
import time
import uuid
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from .store import ROOT

BACKUPS=ROOT/'backups'
TREES=('课程','课程库','data/materials','data/wps','data/runs')

def digest(data):return hashlib.sha256(data).hexdigest()

def backups():
    BACKUPS.mkdir(exist_ok=True)
    return [{'name':p.name,'bytes':p.stat().st_size,'created_at':p.stat().st_mtime}
            for p in sorted(BACKUPS.glob('planet-*.zip'),key=lambda p:(p.stat().st_mtime,p.name),reverse=True)]

def backup(automatic=False):
    from .learning import GENERATION_LOCK
    from .challenges import LOCK
    if not GENERATION_LOCK.acquire(blocking=False):raise ValueError('课程出题中，稍后再备份')
    if not LOCK.acquire(blocking=False):
        GENERATION_LOCK.release()
        raise ValueError('每日出题中，稍后再备份')
    BACKUPS.mkdir(exist_ok=True)
    name=('planet-auto-' if automatic else 'planet-')+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.zip'
    target=BACKUPS/name
    temporary=target.with_suffix('.tmp')
    snapshot=BACKUPS/(name+'.sqlite3')
    try:
        with closing(sqlite3.connect(ROOT/'data/learning.sqlite3')) as source,closing(sqlite3.connect(snapshot)) as dest:
            source.backup(dest)
        inventory={}
        files=[(snapshot,'data/learning.sqlite3')]
        for tree in TREES:
            folder=ROOT/tree
            if folder.exists():files.extend((p,p.relative_to(ROOT).as_posix()) for p in folder.rglob('*') if p.is_file() and not p.is_symlink())
        settings=ROOT/'data/local-settings.json'
        if settings.exists():files.append((settings,'data/local-settings.json'))
        if automatic:
            inventory={relative:digest(path.read_bytes()) for path,relative in files}
            previous=backups()
            if previous:
                try:old_inventory=verify(BACKUPS/previous[0]['name'])
                except (OSError,ValueError,KeyError,zipfile.BadZipFile):old_inventory=None
                if old_inventory==inventory:
                    return {'name':previous[0]['name'],'bytes':previous[0]['bytes'],'skipped':True}
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
            for path,relative in files:
                raw=path.read_bytes();inventory[relative]=digest(raw);archive.writestr(relative,raw)
            archive.writestr('inventory.json',json.dumps({'version':1,'sha256':inventory},ensure_ascii=False))
        verify(temporary)
        temporary.replace(target)
        # Legacy/manual archives are never automatically removed.
        for old in ([item for item in backups() if item['name'].startswith('planet-auto-')][3:] if automatic else []):
            candidate=(BACKUPS/old['name']).resolve()
            if candidate.parent==BACKUPS.resolve():candidate.unlink()
        return {'name':name,'bytes':target.stat().st_size}
    finally:
        if snapshot.exists():snapshot.unlink()
        if temporary.exists():temporary.unlink()
        LOCK.release();GENERATION_LOCK.release()

def verify(path):
    with zipfile.ZipFile(path) as z:
        inventory=json.loads(z.read('inventory.json'))['sha256']
        if set(z.namelist())!=set(inventory)|{'inventory.json'}:raise ValueError('备份清单不匹配')
        for name,expected in inventory.items():
            p=Path(name)
            allowed=name in ('data/learning.sqlite3','data/local-settings.json') or any(name.startswith(tree+'/') for tree in TREES)
            if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name or not allowed:
                raise ValueError('备份路径无效')
            if digest(z.read(name))!=expected:raise ValueError('备份校验失败')
    return inventory

def cleanup_preview(reviewed_audio=False):
    cutoff=time.time()-30*86400
    candidates=[]
    for folder in (ROOT/'data/qa',ROOT/'data/research-cache'):
        if folder.exists():candidates.extend(p for p in folder.rglob('*') if p.is_file() and p.stat().st_mtime<cutoff)
    # Only failed runs without a final question package; preserve the manifest.
    for manifest in (ROOT/'data/runs').glob('*/manifest.json'):
        try:row=json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError,ValueError):continue
        if row.get('state') not in ('failed','error') or manifest.stat().st_mtime>=cutoff or (manifest.parent/'package.json').exists():continue
        candidates.extend(p for p in manifest.parent.iterdir() if p.is_file() and p!=manifest)
    audio=ROOT/'data/wps/training-video/audio.wav'
    transcript=ROOT/'data/wps/training-video/transcript'
    if reviewed_audio and audio.exists() and transcript.exists() and any(transcript.rglob('*.md')):candidates.append(audio)
    result=[]
    for p in candidates:
        resolved=p.resolve()
        if resolved.is_relative_to(ROOT.resolve()) and not p.is_symlink():
            result.append({'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size})
    return {'files':result,'bytes':sum(p['bytes'] for p in result)}

def cleanup(reviewed_audio=False):
    plan=cleanup_preview(reviewed_audio)
    # A verified backup is required before removing any cache.
    if not backups():raise ValueError('请先创建备份')
    verify(BACKUPS/backups()[0]['name'])
    for item in plan['files']:
        path=(ROOT/item['path']).resolve()
        if not path.is_relative_to(ROOT.resolve()):raise ValueError('清理路径无效')
        path.unlink(missing_ok=True)
    return {'removed':len(plan['files']),'bytes':plan['bytes']}

def overview():
    return {'backups':backups(),'cleanup':cleanup_preview(),'policy':'每7天检查自动备份，内容未变化则跳过；新自动备份校验通过后仅保留最近3份自动备份，手动和历史备份不自动删除。30天前的检查截图与失败任务中间文件可清理。原始视频、课程正文、合格题和答题记录保留。'}

def worker(stop):
    next_check=0
    while not stop.is_set():
        try:
            if time.time()>=next_check:
                items=backups()
                if not items or time.time()-items[0]['created_at']>7*86400:
                    backup(automatic=True)
                    next_check=time.time()+7*86400
                else:next_check=items[0]['created_at']+7*86400
        except (Exception,):pass
        stop.wait(60)

def restore(name):
    """Offline only. Existing data is moved to a recovery folder, never deleted."""
    import socket
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',18766))==0:raise ValueError('请先关闭记忆星球后台，再恢复')
    archive=(BACKUPS/name).resolve()
    if archive.parent!=BACKUPS.resolve() or not archive.name.startswith('planet-') or archive.suffix!='.zip':raise ValueError('备份名称无效')
    inventory=verify(archive)
    stage=ROOT/'data'/('restore-stage-'+uuid.uuid4().hex)
    recovery=ROOT/'recovery'/uuid.uuid4().hex
    stage.mkdir(parents=True);recovery.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        for name in inventory:
            target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
    with closing(sqlite3.connect(stage/'data/learning.sqlite3')) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('备份数据库无效')
    paths=TREES+('data/learning.sqlite3','data/learning.sqlite3-wal','data/learning.sqlite3-shm','data/local-settings.json')
    moved=[];installed=[]
    try:
        for relative in paths:
            current=(ROOT/relative).resolve()
            if not current.is_relative_to(ROOT.resolve()):raise ValueError('恢复路径无效')
            if current.exists():
                old=recovery/relative;old.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(current),str(old));moved.append(relative)
            source=stage/relative
            if source.exists():current.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(source),str(current));installed.append(relative)
    except Exception:
        for relative in reversed(installed):
            target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(ROOT/relative),str(target))
        for relative in reversed(moved):shutil.move(str(recovery/relative),str(ROOT/relative))
        raise
    return {'recovery':str(recovery),'status':'restored'}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['restore']);parser.add_argument('name')
    args=parser.parse_args();print(json.dumps(restore(args.name),ensure_ascii=False))
