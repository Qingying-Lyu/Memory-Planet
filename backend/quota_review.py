"""Conservative, evidence-reviewed migration of legacy per-concept quotas."""
import json,hashlib,time,uuid
from concurrent.futures import ThreadPoolExecutor
from .store import connect
from . import knowledge,ai_rules
from .cloud import ParsingStarflow
from .settings import configure

INSTRUCTION=knowledge.GUARD+'判断每个知识点是否具备独立简答题价值。必须给定依据明确支持因果机制、至少两步操作、实质比较或有依据的应用分析。单个参数、名词、清单、孤立结论，以及仅复述选择题答案，不给简答额度。不能补充外部知识。只输出JSON {"short_answer":[false,true]}，布尔数组与points逐项等长。'

def migrate():
    configure()
    with connect() as db:
        knowledge.initialize(db)
        rows=[dict(r) for r in db.execute('SELECT c.* FROM knowledge_concepts c WHERE EXISTS(SELECT 1 FROM knowledge_evidence e WHERE e.concept_id=c.id)')]
        for r in rows:r['evidence']=[x[0] for x in db.execute('SELECT DISTINCT quote FROM knowledge_evidence WHERE concept_id=?',(r['id'],))]
    def review(group):
        points=[{k:r[k] for k in ('id','statement','evidence')} for r in group]
        key=hashlib.sha256(json.dumps(points,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        with connect() as db:cached=db.execute("SELECT result FROM knowledge_audits WHERE stage='quota_policy_v2' AND input_hash=?",(key,)).fetchone()
        value=json.loads(cached[0]) if cached else None
        for attempt in range(2):
            if value and isinstance(value.get('short_answer'),list) and len(value['short_answer'])==len(group) and all(type(x)is bool for x in value['short_answer']):break
            client,rule=ai_rules.client_for('evidence_review',ParsingStarflow)
            value,meta=client.json(rule.model,INSTRUCTION,{'points':points})
        flags=value.get('short_answer')
        if not isinstance(flags,list) or len(flags)!=len(group) or any(type(x)is not bool for x in flags):raise ValueError('额度审核格式无效，未改动额度')
        if not cached:
            with connect() as db:db.execute('INSERT INTO knowledge_audits VALUES(?,?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,group[0]['course_id'],'quota-policy-v2',key,'quota_policy_v2','glm-5.3',json.dumps(value),json.dumps({'rule_version':ai_rules.VERSION}),time.time(),key))
        return [(r['id'],int(flag)) for r,flag in zip(group,flags)]
    groups=[rows[i:i+6] for i in range(0,len(rows),6)]
    with ThreadPoolExecutor(max_workers=3) as pool:decisions=[p for group in pool.map(review,groups) for p in group]
    with connect() as db:
        for cid,short in decisions:
            counts=dict(db.execute('SELECT kind,count(*) FROM knowledge_questions WHERE concept_id=? GROUP BY kind',(cid,)))
            db.execute('UPDATE knowledge_concepts SET choice_cap=?,short_cap=? WHERE id=?',(max(1,counts.get('single_choice',0)),max(short,counts.get('short_answer',0)),cid))
        return {'concepts':len(decisions),'short_answer_eligible':sum(s for _,s in decisions),'quota':sum(db.execute('SELECT choice_cap+short_cap FROM knowledge_concepts WHERE id=?',(cid,)).fetchone()[0] for cid,_ in decisions)}

if __name__=='__main__':print(json.dumps(migrate(),ensure_ascii=False))
