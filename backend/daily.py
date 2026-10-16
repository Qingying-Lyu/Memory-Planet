"""Daily choice-only preview, persistent selection and review timing."""
from datetime import datetime, timedelta, timezone, date
import json

from . import practice
from .store import connect

TZ = timezone(timedelta(hours=8))


def initialize(db):
    practice.initialize(db)
    db.executescript('''CREATE TABLE IF NOT EXISTS daily_challenges (
        owner TEXT NOT NULL, course_id TEXT NOT NULL, day TEXT NOT NULL,
        session_id TEXT NOT NULL UNIQUE, reason TEXT NOT NULL,
        PRIMARY KEY(owner,course_id,day));''')


def select(run_id, owner):
    return select_packages([(run_id, practice.package(run_id, owner))], owner)


def select_course(course_id, owner):
    from .library import allowed
    from .store import ROOT
    with connect() as db:
        allowed(db, course_id, owner)
    packages = []
    for file in (ROOT / 'data' / 'runs').glob('*/manifest.json'):
        row = json.loads(file.read_text(encoding='utf-8'))
        if row.get('course_id') != course_id or row.get('owner') != owner or row.get('state') != 'review_preview':
            continue
        try:
            package = practice.package(file.parent.name, owner)
            if package.get('evidence_map'):
                packages.append((file.parent.name, package))
        except LookupError:
            continue  # Superseded scopes cannot supply questions.
    if not packages:
        return {'status': 'no_due_question', 'reason': '当前课程没有可用的已审题包', 'preview': True}
    return select_packages(packages, owner)


def select_packages(packages, owner):
    course_id = packages[0][1]['course_id']
    if any(data['course_id'] != course_id for _, data in packages):
        raise ValueError('每日挑战不能混合课程')
    day = datetime.now(TZ).date()
    with connect() as db:
        initialize(db)
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT d.*,p.run_id,p.question_id FROM daily_challenges d '
                              'JOIN practice_sessions p ON p.id=d.session_id '
                              'WHERE d.owner=? AND d.course_id=? AND d.day=?',
                              (owner, course_id, day.isoformat())).fetchone()
        if existing:
            q, _ = practice.question_for(existing['run_id'], existing['question_id'], owner)
            return public(existing['session_id'], q.model_dump(), existing['reason'], day)
        candidates = []
        for run_id, data, q in ((run_id, data, q) for run_id, data in packages for q in data['questions']):
            if q['kind'] != 'single_choice':
                continue
            if db.execute('SELECT 1 FROM practice_sessions WHERE owner=? AND run_id=? AND question_id=? '
                          'AND (revealed=1 OR result IS NOT NULL)', (owner, run_id, q['id'])).fetchone():
                continue
            concept = data['course_id'] + ':' + run_id + ':' + q['concept_id']
            last = db.execute('SELECT * FROM mastery_events WHERE owner=? AND concept=? ORDER BY n DESC LIMIT 1',
                              (owner, concept)).fetchone()
            if last:
                interval = 1 if last['after'] < 60 else 3 if last['after'] < 80 else 7
                due = date.fromisoformat(last['day']) + timedelta(days=interval)
                if due > day:
                    continue
                priority = (0, due.isoformat(), last['after'], q['id'])
                reason = f'该知识点已到复习时间（间隔{interval}天），优先使用未见过的题'
            else:
                priority = (1, '', 0, q['id'])
                reason = '该知识点尚未评估，使用未见过的选择题'
            candidates.append((priority + (run_id,), run_id, q, reason))
        if not candidates:
            return {'status': 'no_due_question', 'reason': '没有到期且未见过的已审选择题；需要补充题库', 'preview': True}
        _, run_id, q, reason = min(candidates, key=lambda item: item[0])
        import uuid
        session_id = uuid.uuid4().hex
        db.execute('INSERT INTO practice_sessions(id,owner,run_id,question_id) VALUES(?,?,?,?)',
                   (session_id, owner, run_id, q['id']))
        db.execute('INSERT INTO daily_challenges VALUES(?,?,?,?,?)',
                   (owner, course_id, day.isoformat(), session_id, reason))
        return public(session_id, q, reason, day)


def public(session_id, q, reason, day):
    return {'status': 'ready', 'session_id': session_id, 'day': day.isoformat(),
            'kind': 'single_choice', 'prompt': q['prompt'], 'options': q['options'],
            'reason': reason, 'preview': True}
