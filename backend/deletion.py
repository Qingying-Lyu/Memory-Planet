"""Explicit local course deletion, with owner checks and restricted file targets."""
import json
import re
import shutil
from pathlib import Path
from .store import ROOT,connect
from . import library,material_policy as policy


def safe_path(path):
    path=Path(path).resolve()
    root=ROOT.resolve()
    allowed=[root/'课程库',root/'课程',root/'data/materials',root/'data/wps',root/'data/runs']
    if not any(path==base or path.is_relative_to(base) for base in allowed):
        raise ValueError('拒绝删除产品资料目录之外的文件')
    # Never traverse a symlink/junction to a directory outside the permitted roots.
    if path==root:raise ValueError('不能删除项目目录')
    return path


def remove_path(path):
    path=safe_path(path)
    if path.is_file():path.unlink()
    elif path.is_dir():shutil.rmtree(path)


def _plan(course_id,owner,mode,material_ids):
    if mode not in ('course','materials','bank'):raise ValueError('请选择删除整门课程、部分材料或仅清空题库')
    with connect() as db:
        policy.initialize(db);library.allowed(db,course_id,owner)
        current,mapping=policy.snapshot(db,course_id,owner)
        if mode=='materials' and (not material_ids or not set(material_ids)<=set(current)):raise ValueError('请勾选本课程的有效材料')
        mids=set(current) if mode=='course' else set(material_ids) if mode=='materials' else set()
        docs=[dict(r) for r in db.execute('SELECT * FROM library_documents WHERE course_id=?',(course_id,))]
        selected={d['id'] for d in docs if mode=='course' or policy.key(course_id,d['origin']) in mids}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='enrichment_documents'").fetchone():
            cols={r['name'] for r in db.execute('PRAGMA table_info(enrichment_documents)')}
            if 'parent_document_id' in cols:
                for r in db.execute('SELECT document_id,parent_document_id FROM enrichment_documents WHERE course_id=?',(course_id,)):
                    if mode=='course' or r['parent_document_id'] in selected:selected.add(r['document_id'])
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        bankrows=[dict(r) for r in db.execute('SELECT * FROM question_bank WHERE owner=? AND course_id=?',(owner,course_id))] if 'question_bank' in tables else []
        refs={(r['run_id'],r['question_id']) for r in bankrows if mode in ('course','bank')}
        if 'bank_materials' in tables:
            refs.update((r['run_id'],r['question_id']) for r in db.execute('SELECT * FROM bank_materials WHERE owner=?',(owner,)) if r['material_id'] in mids)
        sources=[dict(r) for r in db.execute('SELECT * FROM sources WHERE course_id=?',(course_id,)) if mode=='course' or policy.key(course_id,r['location']) in mids]
        uploads=[dict(r) for r in db.execute('SELECT * FROM learning_materials WHERE course_id=?',(course_id,)) if mode=='course' or r['document_id'] in selected or policy.key(course_id,r['location']) in mids] if 'learning_materials' in tables else []
        version_paths=[r[0] for r in db.execute('SELECT location FROM material_versions WHERE material_id IN (SELECT id FROM material_heads WHERE course_id=?)',(course_id,)) if mode=='course' or any(r[0]==v[0] for mid in mids for v in db.execute('SELECT location FROM material_versions WHERE material_id=?',(mid,)))] if 'material_versions' in tables else []
    paths=set();runs=[]
    for manifest in (ROOT/'data/runs').glob('*/manifest.json'):
        try:
            meta=json.loads(manifest.read_text(encoding='utf-8'))
            if meta.get('owner')!=owner or meta.get('course_id')!=course_id:continue
            if not re.fullmatch('[a-f0-9]{32}',manifest.parent.name):continue
            package_path=manifest.parent/'package.json'
            data=json.loads(package_path.read_text(encoding='utf-8')) if package_path.exists() else None
            if mode in ('course','bank'):
                if data:refs.update((manifest.parent.name,q['id']) for q in data['questions'])
                runs.append((manifest.parent,None));continue
            if not data:
                if any(e.get('document_id') in selected for e in meta.get('evidence_map',{}).values()):runs.append((manifest.parent,None))
                continue
            emap=data.get('evidence_map',{});concepts={c['id']:c for c in data['framework']['concepts']}
            for q in data['questions']:
                evidence=q['evidence']+concepts.get(q['concept_id'],{}).get('evidence',[])
                if any(emap.get(str(e['block']),{}).get('document_id') in selected for e in evidence):refs.add((manifest.parent.name,q['id']))
            kept=[q for q in data['questions'] if (manifest.parent.name,q['id']) not in refs]
            if len(kept)!=len(data['questions']):
                data['questions']=kept
                data['framework']['concepts']=[c for c in data['framework']['concepts'] if c['id'] in {q['concept_id'] for q in kept}]
                used={str(e['block']) for q in kept+data['framework']['concepts'] for e in q['evidence']}
                data['evidence_map']={k:v for k,v in emap.items() if str(k) in used}
                meta['evidence_map']=data['evidence_map'];meta['accepted']=len(kept)
                runs.append((manifest.parent,(data,meta) if kept else None))
        except (OSError,ValueError,KeyError):
            # Do not silently remove an unreadable arbitrary directory.
            continue
    for d in docs:
        if d['id'] in selected:paths.add(safe_path(Path(d['markdown']).parent))
    for row in uploads:
        paths.add(safe_path(Path(row['location']).parent))
    for location in version_paths:
        try:paths.add(safe_path(Path(location).parent))
        except ValueError:pass
    for row in sources:
        location=Path(row['location'])
        if location.is_file():
            try:paths.add(safe_path(location))
            except ValueError:pass  # An externally referenced original is not owned by this product.
    if mode=='course':
        local=ROOT/'data/materials'/course_id
        if local.exists():paths.add(safe_path(local))
    public={'mode':mode,'course_id':course_id,'materials':len(mids),'documents':len(selected),
        'questions':sum((r['run_id'],r['question_id']) in refs for r in bankrows),'files':len(paths)+len(runs),
        'warning':'删除后不可在产品中撤销。仅清空题库不会返还已用额度。'}
    return public,selected,mids,sources,uploads,refs,runs,paths


def preview(course_id,owner,mode,material_ids=()):
    return _plan(course_id,owner,mode,material_ids)[0]


def delete(course_id,owner,mode,material_ids=()):
    from .material_versions import LOCK as material_lock
    if not material_lock.acquire(blocking=False):raise ValueError('材料解析中，请稍后再删除')
    try:return _delete(course_id,owner,mode,material_ids)
    finally:material_lock.release()

def _delete(course_id,owner,mode,material_ids=()):
    from .learning import GENERATION_LOCK
    from .challenges import LOCK
    if not LOCK.acquire(blocking=False):raise ValueError('每日推荐处理中，请稍后再删除')
    if not GENERATION_LOCK.acquire(blocking=False):LOCK.release();raise ValueError('材料整理或出题中，请稍后再删除')
    try:
        result,docs,mids,sources,uploads,refs,runs,paths=_plan(course_id,owner,mode,material_ids)
        with connect() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            db.execute('CREATE TABLE IF NOT EXISTS deleted_questions(owner TEXT,run_id TEXT,question_id TEXT,PRIMARY KEY(owner,run_id,question_id))')
            sessions=set();concepts=set();batches=set()
            for run,qid in refs:
                db.execute('INSERT OR IGNORE INTO deleted_questions VALUES(?,?,?)',(owner,run,qid))
                if 'practice_sessions' in tables:
                    sessions.update(r[0] for r in db.execute('SELECT id FROM practice_sessions WHERE owner=? AND run_id=? AND question_id=?',(owner,run,qid)))
                if 'question_bank' in tables:
                    concepts.update(r[0] for r in db.execute('SELECT concept_key FROM question_bank WHERE owner=? AND run_id=? AND question_id=?',(owner,run,qid)))
                    db.execute('DELETE FROM question_bank WHERE owner=? AND run_id=? AND question_id=?',(owner,run,qid))
            for sid in sessions:
                if 'challenge_items' in tables:
                    batches.update(r[0] for r in db.execute('SELECT batch_id FROM challenge_items WHERE session_id=?',(sid,)))
                    db.execute('DELETE FROM challenge_items WHERE session_id=?',(sid,))
                for table,column in [('session_modes','session_id'),('daily_challenges','session_id'),('practice_sessions','id')]:
                    if table in tables:db.execute(f'DELETE FROM {table} WHERE {column}=?',(sid,))
            for bid in batches:
                items=[r[0] for r in db.execute('SELECT position FROM challenge_items WHERE batch_id=? ORDER BY position',(bid,))]
                for pos in items:db.execute('UPDATE challenge_items SET position=? WHERE batch_id=? AND position=?',(-pos-1,bid,pos))
                for pos,old in enumerate(items):db.execute('UPDATE challenge_items SET position=? WHERE batch_id=? AND position=?',(pos,bid,-old-1))
                db.execute("UPDATE challenge_batches SET state='partial',error='课程内容已删除，等待重新推荐' WHERE id=?",(bid,))
                if 'challenge_retries' in tables:db.execute('DELETE FROM challenge_retries WHERE batch_id=?',(bid,))
            for concept in concepts:
                if not db.execute('SELECT 1 FROM question_bank WHERE owner=? AND concept_key=?',(owner,concept)).fetchone():
                    if 'mastery_events' in tables:db.execute('DELETE FROM mastery_events WHERE owner=? AND concept=?',(owner,concept))
            if 'additional_rounds' in tables:
                for r in db.execute('SELECT request_id,payload FROM additional_rounds WHERE owner=?',(owner,)).fetchall():
                    if any((q['run_id'],q['question_id']) in refs for q in json.loads(r['payload'])['questions']):db.execute('DELETE FROM additional_rounds WHERE owner=? AND request_id=?',(owner,r['request_id']))
            for folder,replacement in runs:
                if replacement is None and 'challenge_runs' in tables:db.execute('DELETE FROM challenge_runs WHERE run_id=?',(folder.name,))
            for doc in docs:
                db.execute('DELETE FROM library_evidence WHERE document_id=?',(doc,))
                db.execute('DELETE FROM library_documents WHERE id=?',(doc,))
                if 'enrichment_documents' in tables:db.execute('DELETE FROM enrichment_documents WHERE document_id=?',(doc,))
            for r in sources:
                db.execute('DELETE FROM blocks WHERE source_id=?',(r['id'],));db.execute('DELETE FROM sources WHERE id=?',(r['id'],))
            for r in uploads:db.execute('DELETE FROM learning_materials WHERE id=?',(r['id'],))
            for mid in mids:
                db.execute('DELETE FROM material_learning WHERE id=?',(mid,))
                db.execute('DELETE FROM bank_materials WHERE owner=? AND material_id=?',(owner,mid))
                if 'material_versions' in tables:db.execute('DELETE FROM material_versions WHERE material_id=?',(mid,))
                if 'material_heads' in tables:db.execute('DELETE FROM material_heads WHERE id=?',(mid,))
                if 'knowledge_evidence' in tables:db.execute('DELETE FROM knowledge_evidence WHERE material_id=?',(mid,))
                if 'knowledge_pending' in tables:db.execute('DELETE FROM knowledge_pending WHERE material_id=?',(mid,))
                if 'knowledge_progress' in tables:db.execute('DELETE FROM knowledge_progress WHERE material_id=?',(mid,))
                if 'knowledge_jobs' in tables:db.execute('DELETE FROM knowledge_jobs WHERE material_id=?',(mid,))
                if 'knowledge_audits' in tables:db.execute('DELETE FROM knowledge_audits WHERE material_id=?',(mid,))
                if 'knowledge_conflicts' in tables:db.execute('DELETE FROM knowledge_conflicts WHERE material_id=?',(mid,))
            if mode=='course':
                if 'knowledge_questions' in tables:
                    db.execute('DELETE FROM knowledge_questions WHERE concept_id IN (SELECT id FROM knowledge_concepts WHERE course_id=?)',(course_id,))
                    db.execute('DELETE FROM knowledge_concepts WHERE course_id=?',(course_id,))
                    db.execute('DELETE FROM knowledge_conflicts WHERE course_id=?',(course_id,))
                    db.execute('DELETE FROM knowledge_audits WHERE course_id=?',(course_id,))
                for table in ('course_scopes','course_lifecycle','material_migration','course_jobs','enrichment_jobs','enrichment_documents','allocation_credits','daily_challenges','wps_inbox'):
                    if table in tables:db.execute(f'DELETE FROM {table} WHERE course_id=?',(course_id,))
                if 'attempts' in tables:db.execute('DELETE FROM attempts WHERE owner=? AND concept LIKE ?',(owner,course_id+':%'))
                db.execute('DELETE FROM courses WHERE id=? AND owner=?',(course_id,owner))
            elif mode=='materials' and 'enrichment_jobs' in tables:db.execute('DELETE FROM enrichment_jobs WHERE course_id=?',(course_id,))
        # Irreversible file cleanup after the authoritative DB no longer exposes deleted content.
        for folder,replacement in runs:
            remove_path(folder)
            if replacement:
                folder.mkdir(parents=True)
                data,meta=replacement
                (folder/'package.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
                (folder/'manifest.json').write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
        for path in sorted(paths,key=lambda p:len(p.parts),reverse=True):remove_path(path)
        return result
    finally:GENERATION_LOCK.release();LOCK.release()
