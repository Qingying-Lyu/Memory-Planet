"""Reviewed assessment angles, shared lifetime charges and course short reserves.

Existing knowledge_questions is the authoritative immutable lifetime ledger.
Schema additions never rewrite historical questions, scores or charges.
"""
import hashlib
import json


def normalize(text):
    return ''.join(text.lower().split())


def initialize(db):
    # execute(), not executescript(): admission holds BEGIN IMMEDIATE throughout.
    statements = (
        'CREATE TABLE IF NOT EXISTS knowledge_angles(id TEXT PRIMARY KEY,concept_id TEXT NOT NULL,target TEXT NOT NULL,status TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS knowledge_angle_sources(angle_id TEXT,material_id TEXT,document_id TEXT,ordinal INTEGER,quote TEXT,PRIMARY KEY(angle_id,material_id,document_id,ordinal))',
        'CREATE TABLE IF NOT EXISTS knowledge_angle_reviews(concept_id TEXT PRIMARY KEY,short_eligible INTEGER NOT NULL,audited INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS knowledge_question_angles(owner TEXT,run_id TEXT,question_id TEXT,angle_id TEXT,PRIMARY KEY(owner,run_id,question_id,angle_id))',
        'CREATE TABLE IF NOT EXISTS knowledge_short_reservations(owner TEXT,concept_id TEXT,state TEXT,PRIMARY KEY(owner,concept_id))',
        'CREATE TABLE IF NOT EXISTS knowledge_evidence_blocks(concept_id TEXT,material_id TEXT,document_id TEXT,ordinal INTEGER,PRIMARY KEY(concept_id,material_id,document_id,ordinal))',
        'CREATE TABLE IF NOT EXISTS knowledge_quota_migrations(version TEXT PRIMARY KEY)',
    )
    for statement in statements: db.execute(statement)
    if db.execute("SELECT 1 FROM knowledge_quota_migrations WHERE version='angles-v1'").fetchone():return
    # Grandfather only one factual angle supported by reviewed original evidence.
    # Do not convert the old extra short_cap into a new shared allowance.
    for c in db.execute('SELECT id,statement,short_cap FROM knowledge_concepts').fetchall():
        evidence=db.execute('SELECT * FROM knowledge_evidence WHERE concept_id=?',(c['id'],)).fetchall()
        if not evidence:continue
        aid=angle_id(c['id'],c['statement'])
        db.execute("INSERT OR IGNORE INTO knowledge_angles VALUES(?,?,?,'active')",(aid,c['id'],c['statement']))
        for e in evidence:
            db.execute('INSERT OR IGNORE INTO knowledge_angle_sources VALUES(?,?,?,?,?)',(aid,e['material_id'],e['document_id'],e['ordinal'],e['quote']))
        db.execute('INSERT OR IGNORE INTO knowledge_angle_reviews(concept_id,short_eligible) VALUES(?,?)',(c['id'],int(c['short_cap']>0)))
        # All old questions remain charged, even when there are more than angles.
        for q in db.execute('SELECT * FROM knowledge_questions WHERE concept_id=?',(c['id'],)).fetchall():
            db.execute('INSERT OR IGNORE INTO knowledge_question_angles VALUES(?,?,?,?)',(q['owner'],q['run_id'],q['question_id'],aid))
    db.execute("INSERT INTO knowledge_quota_migrations VALUES('angles-v1')")


def angle_id(cid,target):
    return hashlib.sha256((cid+'\n'+normalize(target)).encode()).hexdigest()[:32]


def held(db,cid):
    return bool(db.execute("SELECT 1 FROM knowledge_conflicts WHERE concept_id=? AND status='pending'",(cid,)).fetchone())


def evidence_valid(db,cid,mid,doc,ordinal):
    from .material_versions import active_document
    return active_document(db,doc) and not held(db,cid) and not db.execute('SELECT 1 FROM knowledge_evidence_blocks WHERE concept_id=? AND material_id=? AND document_id=? AND ordinal=?',(cid,mid,doc,ordinal)).fetchone()


def sync_angles(db,cid,mid,angles,short_eligible):
    """Only independently reviewed angles reach here; old charges are untouched."""
    initialize(db)
    db.execute('INSERT INTO knowledge_angle_reviews VALUES(?,?,1) ON CONFLICT(concept_id) DO UPDATE SET short_eligible=excluded.short_eligible,audited=1',(cid,int(short_eligible)))
    for angle in angles:
        aid=angle.get('existing_id') or angle_id(cid,angle['target'])
        prior=db.execute('SELECT concept_id FROM knowledge_angles WHERE id=?',(aid,)).fetchone()
        if prior and prior[0]!=cid:raise ValueError('考查角度不属于主考点')
        db.execute("INSERT INTO knowledge_angles VALUES(?,?,?,'active') ON CONFLICT(id) DO UPDATE SET target=excluded.target",(aid,cid,angle['target']))
        db.execute('INSERT OR REPLACE INTO knowledge_angle_sources VALUES(?,?,?,?,?)',(aid,mid,angle['document_id'],angle['ordinal'],angle['quote']))


def available_angles(db,cid,owner,documents=None):
    if held(db,cid):return []
    used={r[0] for r in db.execute('SELECT angle_id FROM knowledge_question_angles WHERE owner=?',(owner,))}
    rows=[]
    for a in db.execute("SELECT * FROM knowledge_angles WHERE concept_id=? AND status='active' ORDER BY id",(cid,)):
        if a['id'] in used:continue
        sources=[dict(r) for r in db.execute('SELECT * FROM knowledge_angle_sources WHERE angle_id=?',(a['id'],))
                 if (documents is None or r['document_id'] in documents) and evidence_valid(db,cid,r['material_id'],r['document_id'],r['ordinal'])]
        if sources:rows.append({**dict(a),'sources':sources})
    return rows


def course_state(db,course_id,owner):
    initialize(db)
    concepts=[dict(r) for r in db.execute('SELECT c.id,COALESCE(r.short_eligible,0) short_eligible FROM knowledge_concepts c LEFT JOIN knowledge_angle_reviews r ON r.concept_id=c.id WHERE c.course_id=? ORDER BY c.id',(course_id,))]
    from .material_versions import active_document
    angles=[dict(r) for r in db.execute("SELECT a.* FROM knowledge_angles a JOIN knowledge_concepts c ON c.id=a.concept_id WHERE c.course_id=? AND a.status='active' ORDER BY a.id",(course_id,))]
    sources={}
    for r in db.execute('SELECT s.* FROM knowledge_angle_sources s JOIN knowledge_angles a ON a.id=s.angle_id JOIN knowledge_concepts c ON c.id=a.concept_id WHERE c.course_id=?',(course_id,)):
        sources.setdefault(r['angle_id'],[]).append(dict(r))
    held_ids={r[0] for r in db.execute("SELECT concept_id FROM knowledge_conflicts WHERE course_id=? AND status='pending'",(course_id,))}
    blocked={tuple(r) for r in db.execute('SELECT b.concept_id,b.material_id,b.document_id,b.ordinal FROM knowledge_evidence_blocks b JOIN knowledge_concepts c ON c.id=b.concept_id WHERE c.course_id=?',(course_id,))}
    counts={}
    for r in db.execute('SELECT q.concept_id,q.kind,count(*) n FROM knowledge_questions q JOIN knowledge_concepts c ON c.id=q.concept_id WHERE c.course_id=? AND q.owner=? GROUP BY q.concept_id,q.kind',(course_id,owner)):
        counts.setdefault(r['concept_id'],{})[r['kind']]=r['n']
    covered={r[0] for r in db.execute('SELECT q.angle_id FROM knowledge_question_angles q JOIN knowledge_angles a ON a.id=q.angle_id JOIN knowledge_concepts c ON c.id=a.concept_id WHERE c.course_id=? AND q.owner=?',(course_id,owner))}
    learned=set()
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='material_learning'").fetchone():
        learned={r[0] for r in db.execute('SELECT DISTINCT e.concept_id FROM knowledge_evidence e JOIN material_learning m ON m.id=e.material_id JOIN knowledge_concepts c ON c.id=e.concept_id WHERE c.course_id=? AND m.learned=1',(course_id,))}
    active_docs={doc:active_document(db,doc) for doc in {r['document_id'] for rows in sources.values() for r in rows}}
    byconcept={}
    for angle in angles:
        cid=angle['concept_id']
        valid=[r for r in sources.get(angle['id'],[]) if active_docs[r['document_id']] and (cid,r['material_id'],r['document_id'],r['ordinal']) not in blocked]
        if valid and cid not in held_ids:byconcept.setdefault(cid,[]).append({**angle,'sources':valid})
    states={}
    for c in concepts:
        cid=c['id'];active=byconcept.get(cid,[]);cap=len(active)
        usage=counts.get(cid,{})
        remaining=max(0,cap-sum(usage.values()))
        opened=[a for a in active if a['id'] not in covered]
        states[cid]={**c,'cap':cap,'used':sum(usage.values()),'short_used':usage.get('short_answer',0),'remaining':remaining,'uncovered_remaining':min(remaining,len(opened)), 'held':cid in held_ids,'angles':opened,'learned':cid in learned,'reserved':False}
    total=sum(c['cap'] for c in states.values())
    eligible=sum(c['short_eligible'] and c['cap']>0 for c in states.values())
    limit=min(eligible,total//5)
    short_used=sum(c['short_used'] for c in states.values())
    slots=max(0,limit-short_used)
    reservations={r['concept_id']:r['state'] for r in db.execute('SELECT * FROM knowledge_short_reservations WHERE owner=?',(owner,))}
    candidates=[c for c in states.values() if c['learned'] and c['short_eligible'] and c['uncovered_remaining'] and not c['short_used'] and not c['held'] and reservations.get(c['id'])!='released']
    candidates.sort(key=lambda c:(reservations.get(c['id'])!='reserved',not c['learned'],-c['cap'],c['id']))
    selected={c['id'] for c in candidates[:slots]}
    for cid,c in states.items():
        c['reserved']=cid in selected
        c['short_remaining']=int(c['reserved'])
        c['choice_remaining']=max(0,c['uncovered_remaining']-int(c['reserved'])) if not c['held'] else 0
        if c['reserved'] and reservations.get(cid)!='reserved':db.execute("INSERT INTO knowledge_short_reservations VALUES(?,?,'reserved') ON CONFLICT(owner,concept_id) DO UPDATE SET state='reserved'",(owner,cid))
        elif reservations.get(cid)=='reserved':db.execute("UPDATE knowledge_short_reservations SET state='inactive' WHERE owner=? AND concept_id=?",(owner,cid))
    return {'concepts':states,'cap':total,'used':sum(c['used'] for c in states.values()),'short_limit':limit,'short_used':short_used,'short_remaining':min(slots,len(selected))}


def coverage(db,package,q,owner):
    cid=package.get('stable_concepts',{}).get(q['concept_id'])
    if not cid:return None
    state=course_state(db,package['course_id'],owner)['concepts'].get(cid)
    if not state or state['held']:return None
    if q['kind']=='short_answer':
        if not state['short_remaining']:return None
    elif not state['choice_remaining']:return None
    ids=q.get('angle_ids') or []
    if not ids:
        # Backward-compatible unambiguous single-angle packages only.
        if len(state['angles'])!=1:return None
        ids=[state['angles'][0]['id']]
    if len(ids)!=len(set(ids)) or not ids:return None
    byid={a['id']:a for a in state['angles']}
    if any(aid not in byid for aid in ids):return None
    if q['kind']=='single_choice' and state['reserved'] and len(byid)-len(ids)<1:return None
    for aid in ids:
        matched=False
        for source in byid[aid]['sources']:
            for e in q['evidence']:
                anchor=package.get('evidence_map',{}).get(str(e['block']))
                if anchor and anchor['document_id']==source['document_id'] and anchor['ordinal']==source['ordinal']:
                    matched=True
        if not matched:return None
    return cid,ids


def charge(db,owner,run,qid,cid,kind,angles):
    db.execute('INSERT INTO knowledge_questions VALUES(?,?,?,?,?)',(owner,run,qid,cid,kind))
    for aid in angles:db.execute('INSERT INTO knowledge_question_angles VALUES(?,?,?,?)',(owner,run,qid,aid))
    if kind=='short_answer':db.execute("UPDATE knowledge_short_reservations SET state='spent' WHERE owner=? AND concept_id=?",(owner,cid))


def release_short(db,course_id,cid,owner):
    if not db.execute('SELECT 1 FROM courses WHERE id=? AND owner=?',(course_id,owner)).fetchone():raise LookupError('课程不存在')
    if not db.execute('SELECT 1 FROM knowledge_concepts WHERE id=? AND course_id=?',(cid,course_id)).fetchone():raise LookupError('考点不存在')
    initialize(db)
    db.execute("INSERT INTO knowledge_short_reservations VALUES(?,?,'released') ON CONFLICT(owner,concept_id) DO UPDATE SET state='released'",(owner,cid))
    return {'released':True,'refunded':False}


def source_blocked(db,doc,ordinal):
    """Conservative retrieval filter; practice additionally checks its primary concept."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_conflicts'").fetchone():return False
    for r in db.execute("SELECT details FROM knowledge_conflicts WHERE status='pending'"):
        c=json.loads(r[0]).get('concept',{})
        if c.get('document_id')==doc and c.get('ordinal')==ordinal:return True
    if db.execute('SELECT 1 FROM knowledge_evidence e JOIN knowledge_conflicts c ON c.concept_id=e.concept_id WHERE e.document_id=? AND e.ordinal=? AND c.status=\'pending\'',(doc,ordinal)).fetchone():return True
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_evidence_blocks'").fetchone():
        return bool(db.execute('SELECT 1 FROM knowledge_evidence_blocks WHERE document_id=? AND ordinal=?',(doc,ordinal)).fetchone())
    return False
