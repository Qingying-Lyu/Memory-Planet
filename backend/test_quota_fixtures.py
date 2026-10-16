"""Explicit reviewed-angle fixtures for scheduling/admission tests (no word quotas)."""
import hashlib
from . import knowledge,question_quota as quota


def reviewed(db,course_id,mid,count=1,separate=False):
    knowledge.initialize(db)
    source=db.execute('SELECT d.id,e.ordinal,e.text FROM library_documents d JOIN library_evidence e ON e.document_id=d.id WHERE d.course_id=? ORDER BY d.id',(course_id,)).fetchall()
    from .material_policy import key
    doc=next(d for d in db.execute('SELECT * FROM library_documents WHERE course_id=?',(course_id,)) if key(course_id,d['origin'])==mid)
    e=next(e for e in source if e['id']==doc['id']);quote=e['text'][:min(100,len(e['text']))]
    result=[]
    for i in range(count if separate else 1):
        cid=hashlib.sha256(f'{course_id}:{mid}:fixture:{i}'.encode()).hexdigest()[:32]
        db.execute('INSERT OR IGNORE INTO knowledge_concepts(id,course_id,title,statement,vector,choice_cap,short_cap) VALUES(?,?,?,?,?,1,0)',(cid,course_id,f'已审核测试考点{i}',f'已审核测试事实{i}', '[1,0]'))
        db.execute('INSERT OR IGNORE INTO knowledge_evidence VALUES(?,?,?,?,?)',(cid,mid,doc['id'],e['ordinal'],quote))
        quota.sync_angles(db,cid,mid,[{'target':f'已审核测试角度{i}-{j}','document_id':doc['id'],'ordinal':e['ordinal'],'quote':quote} for j in range(1 if separate else count)],False)
        result.append(cid)
    return result
