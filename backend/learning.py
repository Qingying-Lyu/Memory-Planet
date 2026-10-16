"""Course lifecycle and append-only local material collection."""
import base64
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone

from .store import ROOT, connect, extract_docx
from . import library

MATERIALS = ROOT/'data'/'materials'
DEFAULT_SCOPE='以所提供学习资料涵盖的全部主题为考试范围'


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS course_lifecycle(course_id TEXT PRIMARY KEY,status TEXT NOT NULL DEFAULT 'learning');
    CREATE TABLE IF NOT EXISTS learning_materials(
      id TEXT PRIMARY KEY,course_id TEXT NOT NULL,title TEXT NOT NULL,kind TEXT NOT NULL,
      location TEXT NOT NULL,state TEXT NOT NULL,document_id TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS course_jobs(course_id TEXT PRIMARY KEY,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,error TEXT);
    CREATE TABLE IF NOT EXISTS material_generation_health(material_id TEXT PRIMARY KEY,course_id TEXT,state TEXT,attempts INTEGER,next_at REAL,last_attempt REAL,error TEXT);
    ''')


def status(course_id, owner):
    with connect() as db:
        initialize(db)
        library.allowed(db,course_id,owner)
        row=db.execute('SELECT status FROM course_lifecycle WHERE course_id=?',(course_id,)).fetchone()
    return row['status'] if row else 'learning'


def list_courses(owner):
    with connect() as db:
        initialize(db)
        library.initialize(db)
        rows=db.execute('''SELECT c.*,COALESCE(s.mode,'training') AS mode,s.note AS scope_note FROM courses c
            LEFT JOIN course_scopes s ON s.course_id=c.id WHERE c.owner=?''',(owner,)).fetchall()
    return [{**{k:r[k] for k in ('id','owner','title','mode','scope_note')},'exam_note':'' if r['scope_note']==DEFAULT_SCOPE else r['scope_note'] or ''} for r in rows]


def create(owner,title,mode,note=''):
    if not title.strip() or mode not in ('training','personal'):
        raise ValueError('请填写课程名称与学习范围')
    course_id=uuid.uuid4().hex
    with connect() as db:
        db.execute('INSERT INTO courses VALUES(?,?,?)',(course_id,owner,title.strip()))
    boundary=DEFAULT_SCOPE
    library.set_scope(course_id,owner,mode,[boundary],boundary)
    return {'id':course_id,'title':title.strip(),'material_count':0}


def materials(course_id,owner):
    with connect() as db:
        initialize(db)
        library.allowed(db,course_id,owner)
        return [dict(r) for r in db.execute('SELECT id,title,kind,state,created_at FROM learning_materials '
                                           'WHERE course_id=? ORDER BY created_at',(course_id,))]


def add(course_id,owner,title,kind,text='',content='',filename=''):
    if kind not in ('text','link','file') or not title.strip():
        raise ValueError('无效资料')
    status(course_id,owner)
    if kind=='link':
        from .material_versions import heads
        if any(h['origin']==text.strip() for h in heads(course_id,owner)):raise ValueError('该链接已经存在，请使用同步最新内容')
    material_id=uuid.uuid4().hex
    folder=MATERIALS/course_id/material_id
    folder.mkdir(parents=True)
    blocks=[]
    coverage='用户补充文字；完成学习时确认属于既定范围'
    if kind=='text':
        if not text.strip():raise ValueError('资料正文为空')
        target=folder/'original.md'
        target.write_text(text,encoding='utf-8')
        blocks=[(i,'text',part) for i,part in enumerate(text.split('\n\n'),1) if part.strip()]
    elif kind=='link':
        from urllib.parse import urlsplit
        url=urlsplit(text)
        if url.scheme!='https' or not url.hostname or url.username or url.password:
            raise ValueError('请使用完整HTTPS资料链接')
        target=folder/'source.url.txt'
        target.write_text(text,encoding='utf-8')
    else:
        raw=base64.b64decode(content,validate=True)
        if not raw or len(raw)>20_000_000:raise ValueError('文件不能为空且不超过20MB')
        suffix=Path(filename).suffix.lower()
        if suffix not in ('.md','.txt','.docx','.pdf','.pptx','.xlsx','.mp4','.mp3','.wav','.m4a','.webm','.mov','.png','.jpg','.jpeg'):
            raise ValueError('请使用Markdown、文本、DOCX、PDF、PPTX、XLSX、图片或音视频文件')
        target=folder/('original'+suffix)
        target.write_bytes(raw)
        if suffix in ('.md','.txt'):
            body=raw.decode('utf-8-sig')
            blocks=[(i,'text',part) for i,part in enumerate(body.split('\n\n'),1) if part.strip()]
        elif suffix=='.docx':
            blocks,details=extract_docx(target)
            coverage=details['coverage']
    document=None
    if blocks:
        result=library.ingest(course_id,owner,title,str(target),blocks,coverage)
        document=result['document_id']
    with connect() as db:
        initialize(db)
        db.execute('INSERT INTO learning_materials VALUES(?,?,?,?,?,?,?,?)',
            (material_id,course_id,title,kind,str(target),'scope_confirmation' if document else 'pending_extraction',
             document,datetime.now(timezone.utc).isoformat()))

    from .material_versions import track
    mid=track(course_id,material_id)
    return {'id':material_id,'material_id':mid,'state':'indexing' if document else 'queued'}


def complete(course_id,owner,confirm_scope=False):
    raise ValueError('课程已取消学完状态，请逐份材料确认已学完')


def rename(course_id,owner,title):
    title=title.strip()
    if not title or len(title)>200:raise ValueError('课程名称应为1至200字')
    with connect() as db:
        library.allowed(db,course_id,owner)
        db.execute('UPDATE courses SET title=? WHERE id=?',(title,course_id))
    return {'id':course_id,'title':title}


def material(course_id,material_id,owner):
    with connect() as db:
        initialize(db)
        library.allowed(db,course_id,owner)
        row=db.execute('SELECT * FROM learning_materials WHERE course_id=? AND id=?',(course_id,material_id)).fetchone()
    if not row:raise LookupError('资料不存在')
    row=dict(row)
    path=Path(row['location']).resolve()
    if not path.is_relative_to(MATERIALS.resolve()) or not path.is_file():raise LookupError('资料不存在')
    return row,path


def enqueue(course_id,owner):
    with connect() as db:
        initialize(db);library.allowed(db,course_id,owner)
        db.execute("INSERT INTO course_jobs VALUES(?,'queued',0,0,NULL) ON CONFLICT(course_id) DO UPDATE SET state='queued',attempts=0,next_at=0,error=NULL",(course_id,))


def progress(course_id,owner):
    from . import bank,material_policy
    rows=material_policy.materials(course_id,owner)
    with connect() as db:
        initialize(db)
        job=db.execute('SELECT * FROM course_jobs WHERE course_id=?',(course_id,)).fetchone()
    return {'accepted':len(bank.entries(owner,course_id)),'remaining':sum(r['remaining'] for r in rows),
        'materials':len(rows),'learned':sum(r['learned'] for r in rows),'state':job['state'] if job else 'idle',
        'error':job['error'] if job else None,'target':None}


def generation_health(mid):
    with connect() as db:
        initialize(db);row=db.execute('SELECT * FROM material_generation_health WHERE material_id=?',(mid,)).fetchone()
    return dict(row) if row else {'attempts':0,'next_at':0,'last_attempt':0,'state':'pending','error':None}

def record_generation(course_id,mid,accepted,error=None):
    import time
    old=generation_health(mid);attempts=0 if accepted else old['attempts']+1
    delay=0 if accepted else min(1800,60*2**min(attempts-1,5))
    with connect() as db:
        initialize(db);db.execute('INSERT OR REPLACE INTO material_generation_health VALUES(?,?,?,?,?,?,?)',(mid,course_id,'ready' if accepted else 'retry',attempts,time.time()+delay,time.time(),None if accepted else error or '新题审核未完成，后台将重试'))

def select_generation_material(materials,excluded=()):
    import time
    candidates=[(m,generation_health(m['id'])) for m in materials if m.get('choice_remaining',m['remaining'])>0 and m['id'] not in excluded]
    candidates=[(m,h) for m,h in candidates if h['next_at']<=time.time()]
    if not candidates:return None
    return min(candidates,key=lambda pair:(pair[0]['used']>0,pair[1]['last_attempt'],pair[0]['used']/max(1,pair[0]['cap']),pair[0]['id']))[0]


GENERATION_LOCK=__import__('threading').Lock()
def generate(course_id,owner):
    from . import bank,material_policy as policy
    from .pipeline import run_sample
    import time
    if not GENERATION_LOCK.acquire(blocking=False):return
    try:
        with connect() as db:
            initialize(db);library.allowed(db,course_id,owner)
            job=db.execute('SELECT * FROM course_jobs WHERE course_id=?',(course_id,)).fetchone()
            if not job or job['next_at']>time.time() or job['state']=='idle':return
            db.execute("UPDATE course_jobs SET state='generating',attempts=attempts+1 WHERE course_id=?",(course_id,))
        from .enrichment import run as enrich
        enrichment_pending=False
        try:enrich(course_id,owner)
        except Exception:enrichment_pending=True
        # Sparse personal notes define scope; reviewed web excerpts supply assessable facts.
        from .knowledge import index_material
        index_failed=set()
        for material in policy.materials(course_id,owner):
            if material['learned'] and material.get('budget_rule') in ('knowledge-v1','knowledge-v2'):
                try:
                    outcome=index_material(course_id,material['id'],owner,material['documents'],material['quality'])
                    if outcome and outcome['conflicts'] and not outcome['concepts']:
                        with connect() as db:db.execute("UPDATE material_heads SET state='conflict' WHERE id=?",(material['id'],))
                        index_failed.add(material['id'])
                except Exception:
                    index_failed.add(material['id']);record_generation(course_id,material['id'],0,'该材料知识点审核待重试')
        bank.sync(owner)
        # Seed choice questions for every newly learned material; short-answer budget stays reserved.
        pending=bool(index_failed) or enrichment_pending
        for material in policy.materials(course_id,owner):
            if not material['learned'] or material.get('processing_state') not in (None,'ready') or material['remaining']<=0:continue
            if material['id'] in index_failed:continue
            if generation_health(material['id'])['next_at']>time.time():pending=True;continue
            # Reserve-first: prepare all currently assigned short questions in bounded batches.
            if material.get('short_remaining',0)>0:
                history=bank.history(owner,course_id)
                try:
                    run=run_sample(course_id=course_id,material_id=material['id'],mixed_count=min(3,material['short_remaining']),short_only=True,
                        previous_questions=[{'prompt':q['prompt'],'options':q.get('options',[])} for q in history])
                    admitted=bank.register(run,owner);record_generation(course_id,material['id'],admitted)
                    if not admitted:pending=True
                except (Exception,SystemExit):record_generation(course_id,material['id'],0);pending=True
            # Keep the small initial choice seed; later choice generation remains daily/on demand.
            current=next(m for m in policy.materials(course_id,owner) if m['id']==material['id'])
            if current['used']==current.get('short_used',0) and current.get('choice_remaining',0)>0:
                history=bank.history(owner,course_id)
                try:
                    run=run_sample(course_id=course_id,material_id=material['id'],choice_count=min(2,current['choice_remaining']),
                        previous_questions=[{'prompt':q['prompt'],'options':q.get('options',[])} for q in history])
                    admitted=bank.register(run,owner);record_generation(course_id,material['id'],admitted)
                    if not admitted:pending=True
                except (Exception,SystemExit):record_generation(course_id,material['id'],0);pending=True
        if any(m['learned'] and m.get('short_remaining',0)>0 for m in policy.materials(course_id,owner)):pending=True
        if pending:
            with connect() as db:db.execute("UPDATE course_jobs SET state='queued',next_at=?,error=? WHERE course_id=?",(time.time()+60,'部分材料首批题仍在审核，按材料单独重试。',course_id))
            return
        with connect() as db:
            db.execute("UPDATE course_jobs SET state='idle',error=NULL WHERE course_id=?",(course_id,))
    except (Exception,SystemExit):
        with connect() as db:
            initialize(db)
            db.execute("UPDATE course_jobs SET state='queued',next_at=?,error=? WHERE course_id=?",(time.time()+1800,'资料整理或首批题审核未完成，稍后重试；不会超出材料上限。',course_id))
    finally:GENERATION_LOCK.release()

def prepare_review(course_id,owner,count=30):
    """Review uses only the current valid bank; never generates to fill a round."""
    from . import bank
    count=max(1,min(30,count))
    return {**bank.review_plan(course_id,owner,count),'message':'按当前有效题库安排，最多30题；不足按实际数量提供，不临时补题。'}


def worker(stop):
    import time
    from .material_policy import materials
    while not stop.is_set():
        for c in list_courses('local-owner'):
            if not any(r['learned'] for r in materials(c['id'],'local-owner')):continue
            with connect() as db:
                row=db.execute('SELECT * FROM course_jobs WHERE course_id=?',(c['id'],)).fetchone()
            if not row or (row['state'] in ('idle','ready') and any(m['learned'] and m.get('short_remaining',0)>0 for m in materials(c['id'],'local-owner'))):
                enqueue(c['id'],'local-owner');row=None
            if not row or (row['state'] not in ('idle','ready') and row['next_at']<=time.time()):generate(c['id'],'local-owner')
        stop.wait(30)


def set_exam_note(course_id,owner,note):
    from . import enrichment
    status(course_id,owner)
    with connect() as db:
        enrichment.initialize(db);library.initialize(db)
        scope=db.execute('SELECT * FROM course_scopes WHERE course_id=?',(course_id,)).fetchone()
        if not scope or scope['mode']!='personal':raise ValueError('仅个人学习支持考试范围备注')
        boundary=note.strip() or DEFAULT_SCOPE
        if boundary==scope['note']:return {'changed':False}
        docs=[dict(r) for r in db.execute('SELECT * FROM library_documents WHERE course_id=? AND scope_revision=? AND id NOT IN (SELECT document_id FROM enrichment_documents)',(course_id,scope['revision']))]
        saved={d['id']:[dict(r) for r in db.execute('SELECT * FROM library_evidence WHERE document_id=? ORDER BY ordinal',(d['id'],))] for d in docs}

    library.set_scope(course_id,owner,'personal',[boundary],boundary)
    for doc in docs:
        blocks=saved[doc['id']]
        result=library.ingest(course_id,owner,doc['title'],doc['origin'],[(r['ordinal'],'text',r['text']) for r in blocks],doc['coverage'])
        assignments={r['ordinal']:boundary for r in blocks if r['approved']}
        if assignments:library.approve(result['document_id'],owner,assignments)
        with connect() as db:
            db.execute('UPDATE learning_materials SET document_id=? WHERE document_id=?',(result['document_id'],doc['id']))
    from .material_policy import initialize as init_materials,key
    with connect() as db:
        init_materials(db)
        db.execute('UPDATE material_learning SET learned=0 WHERE course_id=?',(course_id,))
    return {'changed':True,'requires_material_confirmation':True}
