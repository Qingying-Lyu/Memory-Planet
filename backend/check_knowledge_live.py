"""Opt-in real cloud smoke test in an isolated temporary workspace."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from .store import connect
from . import material_versions as versions,knowledge,material_policy as policy,learning,library,bank,practice,pipeline

def run():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);db=root/'data/test.sqlite3'
        patches=[patch.object(m,'connect',lambda:connect(db)) for m in (versions,knowledge,policy,learning,library,bank,practice,pipeline)]
        patches += [patch.object(m,'ROOT',root) for m in (bank,practice,pipeline)]
        patches += [patch.object(learning,'MATERIALS',root/'data/materials'),patch.object(library,'LIBRARY',root/'课程库')]
        for p in patches:p.start()
        try:
            cid=learning.create('local-owner','合成缓存课程','training')['id']
            r=learning.add(cid,'local-owner','合成资料','text',text='缓存命中时直接返回缓存，不需要回源。\n\n缓存未命中时先回源获取内容，再写入缓存。\n\n缓存有效期为60秒，超过有效期必须回源；不保证源站故障时仍可服务。')
            versions.process(r['material_id'])
            m=policy.materials(cid,'local-owner')[0]
            result={'material_state':m.get('processing_state'),'concepts':m.get('concepts'),'cap':m['cap']}
            if m.get('processing_state')!='ready':raise RuntimeError('Synthetic knowledge indexing failed')
            policy.set_learned(cid,r['material_id'],'local-owner')
            run_id=pipeline.run_sample(course_id=cid,material_id=r['material_id'],mixed_count=2)
            result['accepted']=bank.register(run_id,'local-owner');result['bank_kinds']=[r['kind'] for r in bank.entries('local-owner',cid)]
            result['stable_keys']=all(':knowledge:' in r['concept_key'] for r in bank.entries('local-owner',cid))
            result['remaining']=policy.materials(cid,'local-owner')[0]['remaining']
            return result
        finally:
            for p in reversed(patches):p.stop()

if __name__=='__main__':
    result=run();out=Path(__file__).resolve().parents[1]/'data/model-evaluation-20261012/knowledge-live.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
