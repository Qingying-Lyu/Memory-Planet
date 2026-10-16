"""Persistent reviewed question bank and stable source-anchored mastery keys."""
import hashlib
import json
import re
from datetime import datetime, timezone

from . import practice
from .store import ROOT, connect


def fingerprint(question):
    # Options order and whitespace cannot make an old question new.
    text = re.sub(r'\W+', '', question['prompt'].lower())
    return hashlib.sha256(text.encode()).hexdigest()


def initialize(db):
    practice.initialize(db)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS question_bank(
      owner TEXT NOT NULL,course_id TEXT NOT NULL,run_id TEXT NOT NULL,question_id TEXT NOT NULL,
      kind TEXT NOT NULL,fingerprint TEXT NOT NULL,concept_key TEXT NOT NULL,created_at TEXT NOT NULL,
      PRIMARY KEY(owner,run_id,question_id));
    CREATE INDEX IF NOT EXISTS bank_course ON question_bank(owner,course_id);
    CREATE TABLE IF NOT EXISTS session_modes(
      session_id TEXT PRIMARY KEY,mode TEXT NOT NULL,created_at TEXT NOT NULL);
    ''')


def concept_key(package, question):
    concept = next((c for c in package['framework']['concepts'] if c['id'] == question['concept_id']), None)
    evidence = concept['evidence'] if concept else question['evidence']
    anchors = []
    for item in evidence:
        source = package.get('evidence_map', {}).get(str(item['block']))
        if source:
            anchors.append(f"{source['document_id']}:{source['scope_revision']}:{source['ordinal']}")
    anchor = min(anchors) if anchors else package['run_id'] + ':' + question['concept_id']
    return package['course_id'] + ':source:' + hashlib.sha256(anchor.encode()).hexdigest()[:24]


def register(run_id, owner):
    from . import material_policy as policy
    from difflib import SequenceMatcher
    package=practice.package(run_id,owner)
    if not package.get('evidence_map'):return 0
    admitted=0
    with connect() as db:
        initialize(db);policy.initialize(db);db.commit()
        db.execute('BEGIN IMMEDIATE')
        rows,mapping=policy.snapshot(db,package['course_id'],owner)
        # Backfill lifetime material accounting for historical accepted questions.
        existing=db.execute('SELECT * FROM question_bank WHERE owner=? AND course_id=?',(owner,package['course_id'])).fetchall()
        for old in existing:
            if old['run_id']==run_id:
                q=next((q for q in package['questions'] if q['id']==old['question_id']),None)
                if q:
                    for mid in policy.question_materials(package,q,mapping):db.execute('INSERT OR IGNORE INTO bank_materials VALUES(?,?,?,?)',(owner,run_id,q['id'],mid))
        rows,mapping=policy.snapshot(db,package['course_id'],owner)
        prompts=[]
        for prior_run in {r['run_id'] for r in existing}:
            try:prompts.extend(''.join(q['prompt'].lower().split()) for q in practice.package(prior_run,owner)['questions'])
            except LookupError:pass
        deleted=db.execute("SELECT 1 FROM sqlite_master WHERE name='deleted_questions'").fetchone()
        for q in package['questions']:
            if deleted and db.execute('SELECT 1 FROM deleted_questions WHERE owner=? AND run_id=? AND question_id=?',(owner,run_id,q['id'])).fetchone():continue
            if db.execute('SELECT 1 FROM question_bank WHERE owner=? AND run_id=? AND question_id=?',(owner,run_id,q['id'])).fetchone():continue
            ids=policy.question_materials(package,q,mapping)
            if not ids or any(not rows[mid]['learned'] or rows[mid]['remaining']<=0 for mid in ids):continue
            managed=any(rows[mid].get('budget_rule') in ('knowledge-v1','knowledge-v2') for mid in ids)
            stable=None
            if managed:
                from .knowledge import validate_admission,mastery_key
                stable=validate_admission(db,package,q,owner)
                if not stable:continue
            normalized=''.join(q['prompt'].lower().split())
            if any(SequenceMatcher(None,normalized,old).ratio()>=.78 for old in prompts):continue
            if db.execute('SELECT 1 FROM question_bank WHERE owner=? AND course_id=? AND fingerprint=?',(owner,package['course_id'],fingerprint(q))).fetchone():continue
            db.execute('INSERT INTO question_bank VALUES(?,?,?,?,?,?,?,?)',(owner,package['course_id'],run_id,q['id'],q['kind'],fingerprint(q),mastery_key(db,package['course_id'],stable) if stable else concept_key(package,q),datetime.now(timezone.utc).isoformat()))
            if stable:db.execute('INSERT INTO knowledge_questions VALUES(?,?,?,?,?)',(owner,run_id,q['id'],stable,q['kind']))
            for mid in ids:
                db.execute('INSERT INTO bank_materials VALUES(?,?,?,?)',(owner,run_id,q['id'],mid));rows[mid]['remaining']-=1
            prompts.append(normalized)
            admitted+=1
    return admitted


def sync(owner):
    # Historical accepted questions retain their lifetime charge even after a scope revision.
    from . import material_policy as policy
    with connect() as db:
        initialize(db);policy.initialize(db)
        mapping={r['id']:policy.key(r['course_id'],r['origin']) for r in db.execute('SELECT d.* FROM library_documents d JOIN courses c ON c.id=d.course_id WHERE c.owner=?',(owner,))}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='enrichment_documents'").fetchone():
            columns={r['name'] for r in db.execute('PRAGMA table_info(enrichment_documents)')}
            if 'parent_document_id' in columns:
                for r in db.execute('SELECT document_id,parent_document_id FROM enrichment_documents'):
                    if r['parent_document_id'] in mapping:mapping[r['document_id']]=mapping[r['parent_document_id']]
        historic=[dict(r) for r in db.execute('SELECT * FROM question_bank WHERE owner=? AND NOT EXISTS (SELECT 1 FROM bank_materials m WHERE m.owner=question_bank.owner AND m.run_id=question_bank.run_id AND m.question_id=question_bank.question_id)',(owner,))]
        cached={}
        for row in historic:
            if not re.fullmatch('[a-f0-9]{32}',row['run_id']):continue
            path=ROOT/'data'/'runs'/row['run_id']/'package.json'
            try:
                if row['run_id'] not in cached:cached[row['run_id']]=json.loads(path.read_text(encoding='utf-8'))
                package=cached[row['run_id']]
                q=next(q for q in package['questions'] if q['id']==row['question_id'])
                for mid in policy.question_materials(package,q,mapping):db.execute('INSERT OR IGNORE INTO bank_materials VALUES(?,?,?,?)',(owner,row['run_id'],row['question_id'],mid))
            except (OSError,ValueError,StopIteration):continue
    for file in (ROOT / 'data' / 'runs').glob('*/manifest.json'):
        row = json.loads(file.read_text(encoding='utf-8'))
        if row.get('owner') == owner and row.get('state') == 'review_preview':
            try:
                register(file.parent.name, owner)
            except LookupError:
                pass  # Old evidence versions are retained historically but unavailable.


def entries(owner, course_id=None):
    with connect() as db:
        initialize(db)
        rows = db.execute('SELECT * FROM question_bank WHERE owner=?' +
                          (' AND course_id=?' if course_id else '') + ' ORDER BY created_at,run_id,question_id',
                          (owner, course_id) if course_id else (owner,)).fetchall()
    result, packages = [], {}
    for row in rows:
        row = dict(row)
        if row['run_id'] not in packages:
            try:
                packages[row['run_id']] = practice.package(row['run_id'], owner)
            except LookupError:
                packages[row['run_id']] = None
        package = packages[row['run_id']]
        if package:
            q = next((q for q in package['questions'] if q['id'] == row['question_id']), None)
            if q:
                from .material_policy import check_question
                try:check_question(package,q,owner)
                except (ValueError,LookupError):continue
                result.append({**row, 'question': q})
    return result


def public_entries(owner, course_id=None):
    rows = entries(owner, course_id)
    with connect() as db:
        practice.initialize(db)
        done = {(r['run_id'], r['question_id']) for r in db.execute(
            'SELECT run_id,question_id FROM practice_sessions WHERE owner=? AND result IS NOT NULL', (owner,))}
    return [{k: row[k] for k in ('course_id', 'run_id', 'question_id', 'kind', 'created_at')} |
            {'prompt': row['question']['prompt'], 'done': (row['run_id'], row['question_id']) in done}
            for row in rows]

def history(owner,course_id):
    """Novelty review includes temporarily paused sources, not only recommendable questions."""
    with connect() as db:
        initialize(db)
        refs=[dict(r) for r in db.execute('SELECT run_id,question_id FROM question_bank WHERE owner=? AND course_id=?',(owner,course_id))]
    result=[];packages={}
    for row in refs:
        if row['run_id'] not in packages:
            try:packages[row['run_id']]=practice.package(row['run_id'],owner)
            except LookupError:packages[row['run_id']]=None
        package=packages[row['run_id']]
        if package:
            q=next((q for q in package['questions'] if q['id']==row['question_id']),None)
            if q:result.append(q)
    return result


def mastery(owner, course_id):
    keys=set()
    from .material_policy import materials
    mids={m['id'] for m in materials(course_id,owner) if m['learned'] and m.get('processing_state')=='ready'}
    with connect() as db:
        initialize(db)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_evidence'").fetchone():
            from .knowledge import mastery_key
            registered={mastery_key(db,course_id,r['concept_id']) for r in db.execute('SELECT DISTINCT concept_id,material_id FROM knowledge_evidence') if r['material_id'] in mids}
            if registered:keys=registered
        if not keys:keys={row['concept_key'] for row in entries(owner,course_id)}
        latest = {}
        for row in db.execute('SELECT * FROM mastery_events WHERE owner=? ORDER BY n', (owner,)):
            latest[row['concept']] = row['after']
    assessed = sum(key in latest for key in keys)
    return {'score': round(sum(latest[key] for key in keys if key in latest) / assessed, 1) if assessed else 0,
            'assessed_concepts': assessed, 'known_concepts': len(keys),
            'basis': '只计算已测试考点的最新掌握分；未测不计入分母，重复知识点共享掌握记录'}


def start_review(run_id, question_id, owner):
    with connect() as db:
        initialize(db)
        if not db.execute('SELECT 1 FROM question_bank WHERE owner=? AND run_id=? AND question_id=?',
                          (owner, run_id, question_id)).fetchone():
            raise LookupError('题目不在可访问题库中')
    return practice.start(run_id, question_id, owner, mode='review')


def review_plan(course_id, owner, count=5):
    from .library import allowed
    with connect() as db:
        allowed(db, course_id, owner)
        initialize(db)
        done = {(r['run_id'],r['question_id']) for r in db.execute(
            'SELECT run_id,question_id FROM practice_sessions WHERE owner=? AND result IS NOT NULL',(owner,))}
    rows = entries(owner, course_id)
    rows.sort(key=lambda r: ((r['run_id'],r['question_id']) in done, r['created_at'],r['question_id']))
    selected = rows[:count]
    short = next((r for r in rows if r['kind']=='short_answer'),None)
    if short and count>1 and selected and not any(r['kind']=='short_answer' for r in selected):
        selected[-1] = short
    return {'questions':[{k:r[k] for k in ('run_id','question_id','kind')} for r in selected],
            'available':len(rows)}
