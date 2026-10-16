"""Material learning flags and lifetime, source-scoped question budgets."""
import hashlib
import json
import re
from .store import connect
from . import library

def key(course_id,origin):
    from urllib.parse import urlsplit
    parsed=urlsplit(origin)
    if parsed.hostname in ('365.kdocs.cn','plus.wps.cn'):
        origin=parsed.scheme+'://'+parsed.hostname+parsed.path
    return hashlib.sha256((course_id+'\n'+origin).encode()).hexdigest()[:32]

def word_count(texts):
    unique={re.sub(r'\s+',' ',text).strip() for text in texts if text.strip()}
    return sum(len(re.findall(r'[\u3400-\u9fff]|[A-Za-z0-9]+',text)) for text in unique)

def initialize(db):
    library.initialize(db)
    from .knowledge import initialize as initialize_knowledge
    initialize_knowledge(db)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS material_migration(course_id TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS material_learning(id TEXT PRIMARY KEY,course_id TEXT,learned INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS bank_materials(owner TEXT,run_id TEXT,question_id TEXT,material_id TEXT,PRIMARY KEY(owner,run_id,question_id,material_id));
    ''')
    # One-way migration: only previously completed courses retain learned materials.
    legacy=db.execute("SELECT 1 FROM sqlite_master WHERE name='course_lifecycle'").fetchone()
    rows=db.execute('SELECT d.id,d.course_id,d.origin,d.scope_revision,(SELECT revision FROM course_scopes WHERE course_id=d.course_id) current_revision'+(",COALESCE(l.status,'learning') status FROM library_documents d LEFT JOIN course_lifecycle l ON l.course_id=d.course_id" if legacy else ",'learning' status FROM library_documents d")).fetchall()
    migrated={r[0] for r in db.execute('SELECT course_id FROM material_migration')}
    known={r[0] for r in db.execute('SELECT id FROM material_learning')}
    for r in rows:
        mid=key(r['course_id'],r['origin'])
        if mid not in known:
            db.execute('INSERT OR IGNORE INTO material_learning VALUES(?,?,?)',(mid,r['course_id'],int(r['status']=='completed' and r['course_id'] not in migrated and r['scope_revision']==r['current_revision'])))
            known.add(mid)
    for r in db.execute('SELECT id FROM courses').fetchall():
        if r[0] not in migrated:db.execute('INSERT OR IGNORE INTO material_migration VALUES(?)',(r[0],))

def snapshot(db,course_id,owner):
    library.allowed(db,course_id,owner)
    docs=[dict(r) for r in db.execute('SELECT d.* FROM library_documents d JOIN course_scopes s ON s.course_id=d.course_id AND s.revision=d.scope_revision WHERE d.course_id=?',(course_id,))]
    from .material_versions import active_document
    docs=[d for d in docs if active_document(db,d['id'])]
    managed={r['id']:dict(r) for r in db.execute('SELECT * FROM material_heads WHERE course_id=?',(course_id,))} if db.execute("SELECT 1 FROM sqlite_master WHERE name='material_heads'").fetchone() else {}
    has_web=db.execute("SELECT 1 FROM sqlite_master WHERE name='enrichment_documents'").fetchone()
    web={}
    if has_web:
        columns={r['name'] for r in db.execute('PRAGMA table_info(enrichment_documents)')}
        if 'parent_document_id' in columns:
            web={r['document_id']:r['parent_document_id'] for r in db.execute('SELECT document_id,parent_document_id FROM enrichment_documents WHERE course_id=?',(course_id,))}
        else:web={r['document_id']:None for r in db.execute('SELECT document_id FROM enrichment_documents WHERE course_id=?',(course_id,))}
    originals=[d for d in docs if d['id'] not in web]
    result={};mapping={}
    for d in originals:
        material_id=key(course_id,d['origin']);mapping[d['id']]=material_id
        flag=db.execute('SELECT learned FROM material_learning WHERE id=?',(material_id,)).fetchone()
        row=result.setdefault(material_id,{'id':material_id,'title':d['title'],'origin':d['origin'],'learned':bool(flag and flag['learned']),'parsed':True,'documents':[],'texts':[]})
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_materials'").fetchone():
            upload=db.execute('SELECT id FROM learning_materials WHERE document_id=?',(d['id'],)).fetchone()
            if upload:row['upload_id']=upload['id']
        source=next((r for r in db.execute('SELECT id,location FROM sources WHERE course_id=?',(course_id,)) if key(course_id,r['location'])==material_id),None)
        if source:row['source_id']=source['id']
        row['documents'].append(d['id'])
        parts=[dict(r) for r in db.execute('SELECT text,approved FROM library_evidence WHERE document_id=?',(d['id'],))]
        row['texts'] += [r['text'] for r in parts if r['approved'] or not any(p['approved'] for p in parts)]
    for d in docs:
        if d['id'] not in web:continue
        parent=web[d['id']]
        if not parent and len(originals)==1:parent=originals[0]['id']
        material_id=mapping.get(parent)
        if material_id:
            mapping[d['id']]=material_id;result[material_id]['documents'].append(d['id'])
            result[material_id]['texts'] += [r['text'] for r in db.execute('SELECT text FROM library_evidence WHERE document_id=? AND approved=1',(d['id'],))]
    known_origins={d['origin'] for d in originals}
    pending=[]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_materials'").fetchone():
        pending += [dict(r) for r in db.execute('SELECT id upload_id,title,location origin FROM learning_materials WHERE course_id=? AND document_id IS NULL',(course_id,)) if r['upload_id'] not in {h['upload_id'] for h in managed.values()}]
    pending += [dict(r) for r in db.execute('SELECT id source_id,title,location origin FROM sources WHERE course_id=?',(course_id,)) if r['origin'] not in known_origins]
    for item in pending:
        mid=key(course_id,item['origin'])
        if mid in result:continue
        db.execute('INSERT OR IGNORE INTO material_learning VALUES(?,?,0)',(mid,course_id))
        flag=db.execute('SELECT learned FROM material_learning WHERE id=?',(mid,)).fetchone()
        result[mid]={**item,'id':mid,'learned':bool(flag['learned']),'parsed':False,'documents':[],'texts':[]}
    from .question_quota import course_state
    shared_state=course_state(db,course_id,owner)
    for row in result.values():
        count=word_count(row.pop('texts'));row['words']=count;row['cap']=0
        row['used']=db.execute('SELECT count(*) FROM bank_materials WHERE owner=? AND material_id=?',(owner,row['id'])).fetchone()[0]
        row['remaining']=max(0,row['cap']-row['used']) if row['learned'] else 0
        if row['id'] not in managed and db.execute('SELECT 1 FROM knowledge_evidence WHERE material_id=?',(row['id'],)).fetchone():
            from .knowledge import budget
            row.update(budget(db,row['id'],owner,shared_state));row['budget_rule']='knowledge-v2'
            if not row['learned']:row['remaining']=row['choice_remaining']=row['short_remaining']=0
    for mid,h in managed.items():
        if mid not in result:result[mid]={'id':mid,'title':h['title'],'origin':h['origin'],'learned':bool(db.execute('SELECT learned FROM material_learning WHERE id=?',(mid,)).fetchone()[0]),'parsed':bool(h['document_id']),'documents':[],'words':0,'cap':0,'used':0,'remaining':0}
        row=result[mid];row.update(title=h['title'],upload_id=h['upload_id'],version=h['version'],quality=h['quality'],kind=h['kind'],processing_state=h['state'],error=h['error'])
        if h['state']=='processing':
            from pathlib import Path
            folder=Path(h['location']).parent/'extracted'
            try:
                if (folder/'alignment-progress.json').exists():row['progress']=json.loads((folder/'alignment-progress.json').read_text(encoding='utf-8'))
                elif list(folder.glob('page-*.png')):row['progress']={'completed':len(list(folder.glob('page-*.png.ocr.json'))),'total':len(list(folder.glob('page-*.png'))),'stage':'图文提取'}
                elif (folder/'scrape-progress.json').exists():row['progress']=json.loads((folder/'scrape-progress.json').read_text(encoding='utf-8'))
                elif (folder/'asr/transcription.json').exists():
                    p=json.loads((folder/'asr/transcription.json').read_text(encoding='utf-8'));row['progress']={'stage':'语音转写','last_end':p.get('last_end',0),'duration':p.get('duration',0)}
            except (OSError,ValueError):pass
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_progress'").fetchone():
                progress=db.execute('SELECT stage,completed,total FROM knowledge_progress WHERE material_id=?',(mid,)).fetchone()
                if progress:row['progress']=dict(progress)
        try:
            from pathlib import Path
            manifest=Path(h['location']).parent/'extracted'/'product-manifest.json'
            if manifest.exists():
                captured=json.loads(manifest.read_text(encoding='utf-8'));row['capture_summary']={k:captured.get(k) for k in ('total','completed','text_pages','source_empty','learning_pages','learning_chars','raw_chars','excluded','large_code_blocks_removed')}
        except (OSError,ValueError):pass
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='material_generation_health'").fetchone():
            health=db.execute('SELECT state,error,next_at FROM material_generation_health WHERE material_id=?',(mid,)).fetchone()
            if health:row['generation_health']=dict(health)
        row['choice_remaining']=row['short_remaining']=0
        from .knowledge import budget
        row.update(budget(db,mid,owner,shared_state));row['budget_rule']='knowledge-v2'
        if not row['learned'] or h['state']!='ready':row['remaining']=row['choice_remaining']=row['short_remaining']=0
    return result,mapping

def materials(course_id,owner):
    with connect() as db:
        initialize(db);rows,_=snapshot(db,course_id,owner)
        return list(rows.values())

def allowed_documents(course_id,owner,remaining=False):
    return {doc for row in materials(course_id,owner) if row['learned'] and (not remaining or row['remaining']>0) for doc in row['documents']}

def question_materials(package,question,mapping):
    result=set()
    for evidence in question['evidence']:
        source=package.get('evidence_map',{}).get(str(evidence['block']))
        if not source or source['document_id'] not in mapping:return set()
        result.add(mapping[source['document_id']])
    return result

def check_question(package,question,owner,context=None):
    with connect() as db:
        cache_key=(owner,package['course_id'])
        if context is not None and cache_key in context:rows,mapping=context[cache_key]
        else:
            initialize(db);rows,mapping=snapshot(db,package['course_id'],owner)
            if context is not None:context[cache_key]=(rows,mapping)
        from .material_versions import resolve_evidence
        from .question_quota import held
        cid=package.get('stable_concepts',{}).get(question['concept_id'])
        if cid and held(db,cid):raise ValueError('该考点存在待核对冲突，其他可靠考点仍可练习')
        if cid:
            for e in question['evidence']:
                source=package.get('evidence_map',{}).get(str(e['block']))
                if source and db.execute('SELECT 1 FROM knowledge_evidence_blocks WHERE concept_id=? AND document_id=? AND ordinal=?',(cid,source['document_id'],source['ordinal'])).fetchone():raise ValueError('该考点原结论已失效')
        resolved={}
        concepts=next((c for c in package.get('framework',{}).get('concepts',[]) if c['id']==question['concept_id']),{})
        for e in question['evidence']+concepts.get('evidence',[]):
            source=package.get('evidence_map',{}).get(str(e['block']))
            if source:
                resolved[str(e['block'])]=resolve_evidence(db,source,owner)
                if not resolved[str(e['block'])]:raise ValueError('题目依据已改动或撤回审核')
        checked={**package,'evidence_map':{**package.get('evidence_map',{}),**resolved}}
    ids=question_materials(checked,{**question,'evidence':question['evidence']+concepts.get('evidence',[])},mapping)
    if not ids or any(not rows[i]['learned'] or rows[i].get('processing_state') not in (None,'ready') for i in ids):raise ValueError('题目依据中有未学完或已失效的材料')

def set_learned(course_id,material_id,owner,learned=True):
    with connect() as db:
        initialize(db);rows,_=snapshot(db,course_id,owner)
        if material_id not in rows:raise LookupError('资料不存在')
        if learned and rows[material_id].get('processing_state') not in (None,'ready'):raise ValueError('请先完成解析、知识点审核及音视频核对，再确认已学完')
        docs=[doc for doc in rows[material_id]['documents'] if doc not in {r[0] for r in db.execute('SELECT document_id FROM enrichment_documents')} ] if db.execute("SELECT 1 FROM sqlite_master WHERE name='enrichment_documents'").fetchone() else rows[material_id]['documents']
        scope=db.execute('SELECT scope FROM course_scopes WHERE course_id=?',(course_id,)).fetchone()
        assignments={}
        for doc in docs:
            blocks=[dict(r) for r in db.execute('SELECT ordinal,approved FROM library_evidence WHERE document_id=?',(doc,))]
            prior=any(r['approved'] for r in blocks)
            assignments[doc]={r['ordinal']:json.loads(scope['scope'])[0] for r in blocks}
        db.execute('UPDATE material_learning SET learned=? WHERE id=?',(int(learned),material_id))
    # Explicit per-material confirmation approves the supplied body, not other materials.
    if learned:
        for doc,mapping in assignments.items():
            if mapping:library.approve(doc,owner,mapping)
    return {'learned':learned}
