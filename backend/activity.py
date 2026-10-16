"""Beijing-day completion history, independent of mastery scores."""
import json,uuid
from datetime import datetime,timedelta,timezone
from .store import connect
TZ=timezone(timedelta(hours=8))
def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS review_activity_rounds(id TEXT PRIMARY KEY,owner TEXT,questions TEXT,completed_at TEXT)")
def register(owner,questions):
    with connect() as db:
        initialize(db);rid=uuid.uuid4().hex
        db.execute('INSERT INTO review_activity_rounds VALUES(?,?,?,NULL)',(rid,owner,json.dumps(questions)))
    return rid

def complete(owner,rid,sessions):
    with connect() as db:
        initialize(db)
        row=db.execute('SELECT * FROM review_activity_rounds WHERE id=? AND owner=?',(rid,owner)).fetchone()
        if not row:raise LookupError('复习轮次不存在')
        expected={(q['run_id'],q['question_id']) for q in json.loads(row['questions'])}
        actual=set()
        for sid in set(sessions):
            s=db.execute("SELECT p.* FROM practice_sessions p JOIN session_modes m ON m.session_id=p.id WHERE p.id=? AND p.owner=? AND m.mode='review'",(sid,owner)).fetchone()
            if not s or not s['result']:raise ValueError('本轮复习尚未完成')
            actual.add((s['run_id'],s['question_id']))
        if not expected or actual!=expected:raise ValueError('本轮复习尚未完成')
        at=row['completed_at'] or datetime.now(TZ).isoformat()
        db.execute('UPDATE review_activity_rounds SET completed_at=? WHERE id=?',(at,rid))
    return {'completed':True}

def history(owner,today=None):
    today=today or datetime.now(TZ).date();start=today-timedelta(days=364)
    days={}
    def event(at,kind):
        try:day=datetime.fromisoformat(at).astimezone(TZ).date()
        except (ValueError,TypeError):return
        if start<=day<=today:days.setdefault(day.isoformat(),{'daily':0,'review':0})[kind]+=1
    with connect() as db:
        initialize(db)
        for r in db.execute('SELECT completed_at FROM review_activity_rounds WHERE owner=? AND completed_at IS NOT NULL',(owner,)):event(r[0],'review')
        exists=lambda table:bool(db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone())
        if exists('challenge_batches') and exists('challenge_items') and exists('practice_sessions') and exists('session_modes'):
            for batch in db.execute("SELECT id FROM challenge_batches WHERE owner=? AND state IN ('ready','exhausted')",(owner,)):
                rows=db.execute('SELECT p.result,m.created_at FROM challenge_items i JOIN practice_sessions p ON p.id=i.session_id LEFT JOIN session_modes m ON m.session_id=p.id WHERE i.batch_id=? AND p.owner=?',(batch[0],owner)).fetchall()
                if not rows or any(not r['result'] for r in rows):continue
                times=[json.loads(r['result']).get('submitted_at') or r['created_at'] for r in rows]
                if all(times):event(max(times,key=lambda t:datetime.fromisoformat(t)),'daily')
    output=[]
    for i in range(365):
        day=(start+timedelta(days=i)).isoformat();v=days.get(day,{'daily':0,'review':0})
        output.append({'date':day,**v,'level':(2 if v['review'] else 1) if v['daily'] else 0})
    return {'timezone':'Asia/Shanghai','days':output,'completed_days':sum(x['daily']>0 for x in output),'bright_days':sum(x['level']==2 for x in output)}
