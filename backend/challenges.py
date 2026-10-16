"""10:30 current-day mixed recommendations and additional bank-only rounds."""
import json,math,threading,uuid,time
from datetime import datetime,timedelta,timezone
from difflib import SequenceMatcher
from .store import connect
from . import bank,practice,library,material_policy as policy
TZ=timezone(timedelta(hours=8))
LOCK=threading.Lock()
def slot(now=None):
    now=(now or datetime.now(TZ)).astimezone(TZ)
    at=now.replace(hour=10,minute=30,second=0,microsecond=0)
    return at if now>=at else at-timedelta(days=1)

def initialize(db):
    bank.initialize(db)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS challenge_batches(
      id TEXT PRIMARY KEY,owner TEXT NOT NULL,slot TEXT NOT NULL,target INTEGER NOT NULL,
      state TEXT NOT NULL,allocation TEXT NOT NULL,error TEXT,UNIQUE(owner,slot));
    CREATE TABLE IF NOT EXISTS challenge_items(
      batch_id TEXT NOT NULL,position INTEGER NOT NULL,run_id TEXT NOT NULL,
      question_id TEXT NOT NULL,session_id TEXT NOT NULL UNIQUE,fingerprint TEXT NOT NULL,
      PRIMARY KEY(batch_id,position));
    CREATE TABLE IF NOT EXISTS challenge_runs(
      batch_id TEXT NOT NULL,run_id TEXT NOT NULL,PRIMARY KEY(batch_id,run_id));
    CREATE TABLE IF NOT EXISTS challenge_retries(batch_id TEXT PRIMARY KEY,attempts INTEGER NOT NULL,next_at REAL NOT NULL);
    ''')



def weight(score):return .1+.9*(1-max(0,min(100,score))/100)**2

def allocate(scores,total=15,credits=None):
    if not scores:return {}
    credits={} if credits is None else credits
    weights={cid:weight(score) for cid,score in scores.items()}
    amount=sum(weights.values());result={cid:0 for cid in scores}
    for cid in scores:credits[cid]=credits.get(cid,0)+total*weights[cid]/amount
    for _ in range(total):
        cid=max(scores,key=lambda cid:(credits[cid],cid))
        result[cid]+=1;credits[cid]-=1
    return result

def novel(q,history):
    prompt=''.join(q['prompt'].lower().split())
    return all(bank.fingerprint(q)!=bank.fingerprint(old) and SequenceMatcher(None,prompt,''.join(old['prompt'].lower().split())).ratio()<.78 for old in history)

def courses(owner):
    with connect() as db:rows=[dict(r) for r in db.execute('SELECT id,title FROM courses WHERE owner=?',(owner,))]
    return [r for r in rows if any(m['learned'] and m['parsed'] and m.get('processing_state') in (None,'ready') for m in policy.materials(r['id'],owner))]


def ranked_questions(owner,course_id=None,exclude=(),now=None,due_only=False):
    """All answer modes share history; unanswered is not an incorrect answer."""
    excluded=set(exclude)
    rows=[r for r in bank.entries(owner,course_id) if r['kind']=='single_choice' and (r['run_id'],r['question_id']) not in excluded]
    with connect() as db:
        history={}
        for r in db.execute('SELECT p.run_id,p.question_id,p.result,m.created_at FROM practice_sessions p LEFT JOIN session_modes m ON m.session_id=p.id WHERE p.owner=? AND p.result IS NOT NULL ORDER BY m.created_at,p.rowid',(owner,)):
            result=json.loads(r['result'])
            ref=(r['run_id'],r['question_id'])
            at=result.get('submitted_at') or r['created_at'] or ''
            if ref not in history or at>=history[ref][1]:history[ref]=(result,at)
        mastery={r['concept']:r['after'] for r in db.execute('SELECT * FROM mastery_events WHERE owner=? ORDER BY n',(owner,))}
        source_materials={}
        tested_materials=set()
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='bank_materials'").fetchone():
            for r in db.execute('SELECT run_id,question_id,material_id FROM bank_materials WHERE owner=?',(owner,)):source_materials.setdefault((r[0],r[1]),set()).add(r[2])
            tested_materials={r[0] for r in db.execute('SELECT DISTINCT b.material_id FROM bank_materials b JOIN practice_sessions p ON p.owner=b.owner AND p.run_id=b.run_id AND p.question_id=b.question_id WHERE b.owner=? AND p.result IS NOT NULL',(owner,))}
    current=(now or datetime.now(TZ)).astimezone(TZ)
    def age(prior):
        try:return (current.date()-datetime.fromisoformat(prior[1]).astimezone(TZ).date()).days
        except (ValueError,TypeError):return 999
    def due(row):
        prior=history.get((row['run_id'],row['question_id']))
        if not prior:return True
        q=prior[0].get('q')
        interval=1 if q is None or q<60 else 14 if mastery.get(row['concept_key'],0)>=90 else 7 if mastery.get(row['concept_key'],0)>=80 else 3
        return age(prior)>=interval
    def priority(row):
        prior=history.get((row['run_id'],row['question_id']))
        fresh=any(mid not in tested_materials for mid in source_materials.get((row['run_id'],row['question_id']),set()))
        untested=row['concept_key'] not in mastery
        group=0 if untested and not prior else 1 if not prior else 2 if due(row) and prior[0].get('q') is not None and prior[0]['q']<60 else 3 if due(row) else 4
        return (group,0 if fresh else 1,mastery.get(row['concept_key'],0),prior[1] if prior else '',row['created_at'],row['question_id'])
    return sorted((r for r in rows if not due_only or due(r)),key=priority)



def generate(owner='local-owner',now=None):
    from .learning import GENERATION_LOCK
    now=(now or datetime.now(TZ)).astimezone(TZ)
    # A missed date never creates a historical job, including before today's deadline.
    if now<now.replace(hour=10,minute=30,second=0,microsecond=0):return
    if not LOCK.acquire(blocking=False):return
    if not GENERATION_LOCK.acquire(blocking=False):LOCK.release();return
    try:
        bank.sync(owner);current=slot(now).isoformat()
        with connect() as db:
            initialize(db)
            db.execute('CREATE TABLE IF NOT EXISTS allocation_credits(owner TEXT,course_id TEXT,credit REAL,PRIMARY KEY(owner,course_id))')
            old=db.execute('SELECT * FROM challenge_batches WHERE owner=? AND slot=?',(owner,current)).fetchone()
            if old and old['state']=='ready':return
            if old and old['state']=='exhausted':
                count=db.execute('SELECT count(*) FROM challenge_items WHERE batch_id=?',(old['id'],)).fetchone()[0]
                if count:return
                # No-material state must not consume today's slot before first course is learned.
                if not courses(owner):return
                db.execute('DELETE FROM challenge_retries WHERE batch_id=?',(old['id'],))
            retry=db.execute('SELECT * FROM challenge_retries WHERE batch_id=?',(old['id'],)).fetchone() if old else None
            if retry and retry['next_at']>time.time():return
        available=courses(owner)
        if not available and not old:return
        if old:batch_id=old['id'];allocation=json.loads(old['allocation']) or allocate({c['id']:bank.mastery(owner,c['id'])['score'] for c in available})
        else:
            scores={c['id']:bank.mastery(owner,c['id'])['score'] for c in available}
            with connect() as db:
                credits={r['course_id']:r['credit'] for r in db.execute('SELECT * FROM allocation_credits WHERE owner=?',(owner,))}
                allocation=allocate(scores,15,credits)
                for cid in scores:db.execute('INSERT OR REPLACE INTO allocation_credits VALUES(?,?,?)',(owner,cid,credits[cid]))
                batch_id=uuid.uuid4().hex
                db.execute("INSERT INTO challenge_batches VALUES(?,?,?,?,?,?,NULL)",(batch_id,owner,current,15,'generating',json.dumps(allocation)))
        with connect() as db:
            db.execute("UPDATE challenge_batches SET state='generating' WHERE id=?",(batch_id,))
            db.execute('INSERT INTO challenge_retries VALUES(?,1,?) ON CONFLICT(batch_id) DO UPDATE SET attempts=attempts+1,next_at=excluded.next_at',(batch_id,time.time()+1800))
            published=[dict(r) for r in db.execute('SELECT i.*,b.course_id,b.concept_key FROM challenge_items i JOIN question_bank b ON b.owner=? AND b.run_id=i.run_id AND b.question_id=i.question_id WHERE i.batch_id=?',(owner,batch_id))]
        chosen={(r['run_id'],r['question_id']) for r in published}
        fingerprints={r['fingerprint'] for r in published}
        concepts={r['concept_key'] for r in published}
        def publish(row):
            q=row['question'];ref=(row['run_id'],row['question_id']);fp=bank.fingerprint(q)
            if ref in chosen or fp in fingerprints or row['concept_key'] in concepts or len(published)>=15:return False
            session=practice.start(*ref,owner,mode='daily')
            with connect() as db:db.execute('INSERT INTO challenge_items VALUES(?,?,?,?,?,?)',(batch_id,len(published),*ref,session['session_id'],fp))
            chosen.add(ref);fingerprints.add(fp);concepts.add(row['concept_key']);published.append({'course_id':row['course_id']});return True
        def reuse(cid,count,unanswered_only=False):
            n=0
            for row in ranked_questions(owner,cid,chosen,now=now,due_only=True):
                if unanswered_only:
                    with connect() as db:
                        if db.execute("SELECT 1 FROM practice_sessions WHERE owner=? AND run_id=? AND question_id=? AND result IS NOT NULL LIMIT 1",(owner,row['run_id'],row['question_id'])).fetchone():continue
                if n>=count:break
                if publish(row):n+=1
            return n
        from .pipeline import run_sample
        from .learning import select_generation_material,record_generation
        attempted_materials=set();generation_batches=0
        def generate_new(cid,missing):
            nonlocal generation_batches
            for attempt in range(2):
                if missing<=0 or len(published)>=15 or generation_batches>=8:break
                materials=[m for m in policy.materials(cid,owner) if m.get('choice_remaining',m['remaining'])>0]
                if not materials:break
                material=select_generation_material(materials,attempted_materials)
                if not material:break
                attempted_materials.add(material['id']);generation_batches+=1
                history=bank.history(owner,cid)
                try:
                    run=run_sample(course_id=cid,material_id=material['id'],choice_count=min(3,missing,material.get('choice_remaining',material['remaining'])),previous_questions=[{'prompt':q['prompt'],'options':q.get('options',[])} for q in history])
                    admitted=bank.register(run,owner);record_generation(cid,material['id'],admitted)
                    if admitted:attempted_materials.discard(material['id'])
                    with connect() as db:db.execute('INSERT OR IGNORE INTO challenge_runs VALUES(?,?)',(batch_id,run))
                    for row in bank.entries(owner,cid):
                        if missing<=0:break
                        if row['run_id']==run and row['kind']=='single_choice' and publish(row):missing-=1
                except (Exception,SystemExit):record_generation(cid,material['id'],0);continue
            return missing
        for cid,quota in allocation.items():
            missing=quota-sum(r['course_id']==cid for r in published)
            # Fill unseen questions first; reserve up to one third for due review.
            exploration=max(0,math.ceil(quota*2/3)-sum(r['course_id']==cid for r in published))
            used=reuse(cid,min(missing,exploration),unanswered_only=True)
            missing-=used
            pending=min(missing,max(0,exploration-used))
            missing-=pending-generate_new(cid,pending)
            missing-=reuse(cid,max(0,missing))
            missing=generate_new(cid,missing)
            reuse(cid,max(0,missing))
        # Pool shortfalls across courses, reusing the bank before spending more new-question budget.
        for cid in sorted(allocation,key=lambda c:weight(bank.mastery(owner,c)['score']),reverse=True):reuse(cid,15-len(published))
        for cid in allocation:
            if len(published)<15:generate_new(cid,15-len(published))
        # One final bounded pool pass lets a healthy course cover failed-course shortfalls.
        for cid in allocation:
            if len(published)<15:generate_new(cid,15-len(published))
        amount=len(published)
        has_capacity=any(m.get('choice_remaining',m['remaining'])>0 for c in available for m in policy.materials(c['id'],owner))
        state='ready' if amount==15 else 'partial' if has_capacity else 'exhausted'
        error=None if amount==15 else f'今日可推荐{amount}道选择题；未到复习时间的答对题不会重复凑数。'+('新题审核未完成，稍后重试。' if has_capacity else '可补充并学完新材料，或选择深度复习。')
        actual={cid:sum(p['course_id']==cid for p in published) for cid in allocation}
        shortfalls={cid:quota-actual.get(cid,0) for cid,quota in allocation.items() if actual.get(cid,0)<quota}
        if shortfalls:
            labels={c['id']:c['title'] for c in available}
            warning='；'.join(labels.get(cid,cid)+'计划'+str(allocation[cid])+'题、实际'+str(actual.get(cid,0))+'题' for cid in shortfalls)
            error=(error+' ' if error else '')+warning+'。该课程可用题或新题审核不足，暂由其他课程补足；并非未纳入推荐。'
        with connect() as db:db.execute('UPDATE challenge_batches SET state=?,error=? WHERE id=?',(state,error,batch_id))
    except Exception:
        if 'batch_id' in locals():
            with connect() as db:db.execute("UPDATE challenge_batches SET state='partial',error=? WHERE id=?",('推荐未完成，保留已审题目，稍后重试。',batch_id))
    finally:GENERATION_LOCK.release();LOCK.release()


def again(owner,request_id):
    """Idempotent mixed-bank round: no model calls or new budget charges."""
    with connect() as db:
        initialize(db)
        db.execute('CREATE TABLE IF NOT EXISTS additional_rounds(owner TEXT,request_id TEXT,payload TEXT,created_at TEXT,PRIMARY KEY(owner,request_id))')
        old=db.execute('SELECT payload FROM additional_rounds WHERE owner=? AND request_id=?',(owner,request_id)).fetchone()
        if old:return json.loads(old['payload'])
        previous=db.execute('SELECT payload FROM additional_rounds WHERE owner=? ORDER BY created_at DESC LIMIT 1',(owner,)).fetchone()
        excluded={(q['run_id'],q['question_id']) for q in json.loads(previous['payload'])['questions']} if previous else set()
        daily=db.execute('SELECT id FROM challenge_batches WHERE owner=? AND slot=?',(owner,slot().isoformat())).fetchone()
        if daily:
            items=db.execute('SELECT i.*,p.result FROM challenge_items i JOIN practice_sessions p ON p.id=i.session_id WHERE batch_id=?',(daily['id'],)).fetchall()
            if items and all(i['result'] for i in items):excluded.update((i['run_id'],i['question_id']) for i in items)
    scores={c['id']:bank.mastery(owner,c['id'])['score'] for c in courses(owner)}
    quotas=allocate(scores)
    selected=[];seen=set()
    def add(row):
        fp=bank.fingerprint(row['question'])
        if fp in seen or len(selected)>=15:return
        seen.add(fp);selected.append({k:row[k] for k in ('course_id','run_id','question_id','kind')})
    for cid,quota in quotas.items():
        for row in ranked_questions(owner,cid,excluded)[:quota]:add(row)
    for row in ranked_questions(owner,exclude=excluded):add(row)
    result={'questions':selected,'target':15,'available':len(selected),'message':None if len(selected)==15 else f'避开刚才一轮后，只有{len(selected)}道可用选择题，本轮不重复凑数。'}
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO additional_rounds VALUES(?,?,?,?)',(owner,request_id,json.dumps(result),datetime.now(TZ).isoformat()))
        return json.loads(db.execute('SELECT payload FROM additional_rounds WHERE owner=? AND request_id=?',(owner,request_id)).fetchone()['payload'])


def board(owner,now=None):
    current=slot(now)
    with connect() as db:
        initialize(db)
        batch=db.execute('SELECT * FROM challenge_batches WHERE owner=? AND slot=?',(owner,current.isoformat())).fetchone()
        items=[] if not batch else [dict(r) for r in db.execute('SELECT i.*,p.result FROM challenge_items i JOIN practice_sessions p ON p.id=i.session_id WHERE batch_id=? ORDER BY position',(batch['id'],))]
        archived=db.execute('SELECT count(*) FROM challenge_batches WHERE owner=? AND slot<?',(owner,current.isoformat())).fetchone()[0]
    visible=[];validation_context={}
    for item in items:
        try:
            q,package=practice.question_for(item['run_id'],item['question_id'],owner,validation_context=validation_context)
            if not package.get('evidence_map'):policy.check_question(package,q.model_dump(),owner,context=validation_context)
        except (LookupError,ValueError):continue
        visible.append({'session_id':item['session_id'],'course_id':package['course_id'],'kind':q.kind,'prompt':q.prompt,'options':q.options,'result':json.loads(item['result']) if item['result'] else None})
    return {'state':batch['state'] if batch else 'waiting','slot':current.isoformat(),'next_at':(current+timedelta(days=1)).isoformat(),'target':15,'questions':visible,'allocation':{cid:sum(q['course_id']==cid for q in visible) for cid in json.loads(batch['allocation'])} if batch else {},'error':batch['error'] if batch else None,'archived_batches':archived}

def worker(stop):
    while not stop.is_set():
        now=datetime.now(TZ)
        if now>=now.replace(hour=10,minute=30,second=0,microsecond=0):generate()
        stop.wait(30)
