import base64
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from . import knowledge,material_versions as versions,material_policy as policy,library,learning,bank,practice,deletion,media_alignment as media,challenges,enrichment
from .store import connect
from .test_practice import sample_question

class FakeAI:
    def json(self,model,instruction,data):
        if '为每个独立知识点识别' in instruction:
            return {'items':[{'index':i['index'],'angles':[{'target':i['statement'],'document_id':i['document_id'],'ordinal':i['ordinal'],'quote':i['text'],'existing_id':next((c['id'] for c in i['candidates'] if c['target']==i['statement']),None)}]} for i in data['items']]},{}
        if '独立逐项审核每个考查角度' in instruction:
            return {'approved':[[True]*len(i['angles']) for i in data['items']],'simple_fact':[False]*len(data['items'])},{}
        if '识别所有独立' in instruction:
            return {'concepts':[{'title':b['text'][:8],'statement':b['text'],'document_id':b['document_id'],'ordinal':b['ordinal'],'quote':b['text']} for b in data['blocks']]},{}
        if '独立检查每点' in instruction:return {'approved':[True]*len(data['concepts']),'short_answer':[True]*len(data['concepts'])},{}
        if 'decisions' in instruction and 'items' in data:
            rows=[]
            for item in data['items']:
                text=item['incoming']['statement'];same=next((c for c in item['candidates'] if c['statement']==text),None)
                correction=next((c for c in item['candidates'] if '有效期' in text and '有效期' in c['statement']),None)
                rows.append({'index':item['index'],'relation':'same' if same else 'correction' if correction else 'new','id':(same or correction or {}).get('id')})
            return {'decisions':rows},{}
        if 'checks' in data:return {'approved':[True]*len(data['checks'])},{}
        if 'items' in data and 'decisions' in data:return {'approved':[True]*len(data['items'])},{}
        if '判断新陈述' in instruction:
            text=data['incoming']['statement'];same=next((c for c in data['candidates'] if c['statement']==text),None)
            correction=next((c for c in data['candidates'] if '有效期' in text and '有效期' in c['statement']),None)
            return {'relation':'same' if same else 'correction' if correction else 'new','id':(same or correction or {}).get('id')},{}
        if '仅比较给定diff' in instruction:return {'summary':'材料有更新','added':[],'removed':[],'corrected':[],'wording_only':False},{}
        if '只审查时间对齐' in instruction:return {'text':data['speech'],'uncertain':[],'consistent':True},{}
        return {'approved':True},{}

class KnowledgeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'data/db.sqlite3'
        modules=(knowledge,versions,policy,library,learning,bank,practice,deletion,media,challenges,enrichment)
        self.patches=[patch.object(m,'connect',lambda:connect(self.db)) for m in modules]
        self.patches += [patch.object(m,'ROOT',self.root) for m in (bank,practice,deletion)]
        self.patches += [patch.object(learning,'MATERIALS',self.root/'data/materials'),patch.object(library,'LIBRARY',self.root/'课程库'),patch.object(knowledge,'Starflow',FakeAI),patch('backend.cloud.Starflow',FakeAI),patch.object(media,'Starflow',FakeAI),patch.object(knowledge,'embed',side_effect=lambda texts:[[1.,float(len(t)%7)] for t in texts])]
        for p in self.patches:p.start()
        self.cid=learning.create('alice','缓存课程','training')['id'];self.n=0
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def add(self,text='缓存未命中时应回源获取内容。',title='材料'):
        result=learning.add(self.cid,'alice',title,'text',text=text);mid=result['material_id'];versions.process(mid,'alice');return mid
    def learned(self,mid):return policy.set_learned(self.cid,mid,'alice')
    def m(self,mid):return next(m for m in policy.materials(self.cid,'alice') if m['id']==mid)
    def package(self,mid,kinds,ordinal=1):
        self.n+=1;run=f'{self.n:032x}';m=self.m(mid)
        with connect(self.db) as db:
            evidence=db.execute('SELECT d.id document_id,d.digest,d.scope_revision,e.ordinal,e.text FROM library_documents d JOIN library_evidence e ON e.document_id=d.id WHERE d.id=? AND ordinal=?',(m['documents'][0],ordinal)).fetchone()
            concept=db.execute('SELECT concept_id FROM knowledge_evidence WHERE material_id=? AND ordinal=?',(mid,ordinal)).fetchone()[0]
        qs=[]
        for i,kind in enumerate(kinds):
            q=sample_question();q.update(id=f'q{i}',concept_id='c1',kind=kind,prompt=hashlib.sha256(f'{run}:{i}'.encode()).hexdigest()+'场景判断',evidence=[{'block':1,'quote':evidence['text']}])
            if kind=='short_answer':q.update(options=[],answer_index=None,rubric=[{'id':'r1','point':'回源','weight':100,'full':'说明回源','partial':'不完整','zero':'未说明'}])
            qs.append(q)
        data={'run_id':run,'course_id':self.cid,'framework':{'concepts':[{'id':'c1','evidence':qs[0]['evidence']}]},'questions':qs,'evidence_map':{'1':{k:evidence[k] for k in ('document_id','ordinal','digest','scope_revision')}},'stable_concepts':{'c1':concept}}
        folder=self.root/'data/runs'/run;folder.mkdir(parents=True)
        (folder/'package.json').write_text(json.dumps(data),encoding='utf-8');(folder/'manifest.json').write_text(json.dumps({'course_id':self.cid,'owner':'alice','state':'review_preview'}),encoding='utf-8')
        return run
    def test_failed_point_is_pending_and_never_gains_question_quota(self):
        from .cloud import CloudError
        class OneFailure(FakeAI):
            def json(self,model,instruction,data):
                if 'items' in data and any('incoming' in x and '待复核' in x['incoming']['statement'] for x in data['items']):raise CloudError('timeout')
                return super().json(model,instruction,data)
        with patch.object(knowledge,'Starflow',OneFailure):
            mid=self.add('缓存未命中时应回源获取内容。\n\n待复核资料说明客户需要授权。\n\nI帧可以独立解码。')
        m=self.m(mid);self.assertEqual(m['processing_state'],'ready');self.assertEqual(m['pending_concepts'],1);self.assertEqual(m['concepts'],2);self.assertEqual(m['cap'],2)
        self.learned(mid);self.assertEqual(self.m(mid)['remaining'],2)

    def test_malformed_review_retries_without_accepting_single_boolean(self):
        calls=[]
        class TransientShape(FakeAI):
            def json(self,model,instruction,data):
                if '独立检查每点' in instruction:
                    calls.append(1)
                    if len(calls)==1:return {'approved':True},{}
                return super().json(model,instruction,data)
        with patch.object(knowledge,'Starflow',TransientShape):mid=self.add()
        self.assertEqual(self.m(mid)['processing_state'],'ready');self.assertEqual(len(calls),2)

    def test_one_unverifiable_quote_does_not_abort_other_points(self):
        class BadQuote(FakeAI):
            def json(self,model,instruction,data):
                if '修复知识点引用' in instruction:return {'concepts':data['concepts'],'rejected':[]},{}
                value,meta=super().json(model,instruction,data)
                if '识别所有独立' in instruction:value['concepts'][-1]['quote']='这句话完全不存在于原始材料中。'
                return value,meta
        with patch.object(knowledge,'Starflow',BadQuote):mid=self.add('缓存未命中时应回源获取内容。\n\nI帧可以独立解码。')
        self.assertEqual(self.m(mid)['processing_state'],'ready')
        self.assertEqual(self.m(mid)['concepts'],1);self.assertEqual(self.m(mid)['pending_concepts'],1)
        self.assertEqual(self.m(mid)['cap'],1)

    def test_layout_whitespace_is_restored_to_literal_source_quote(self):
        blocks=[{'document_id':'d','ordinal':2,'text':'控制信令\n负责协商，媒体流承载音视频。'}]
        c={'title':'媒体流','statement':'控制信令负责协商','document_id':'d','ordinal':2,'quote':'控制信令负责协商'}
        fixed=knowledge.anchor_candidates([c],blocks)[0]
        self.assertEqual(fixed['quote'],'控制信令\n负责协商')
        self.assertIn(fixed['quote'],blocks[0]['text'])

    def test_exact_quote_repairs_wrong_page_without_fuzzy_matching(self):
        blocks=[{'document_id':'d','ordinal':4,'text':'缓存未命中时应回源获取内容。'}]
        c={'title':'回源','statement':'缓存未命中时回源','document_id':'d','ordinal':1,'quote':'缓存未命中时应回源获取内容。'}
        self.assertEqual(knowledge.anchor_candidates([c],blocks)[0]['ordinal'],4)
        with self.assertRaises(ValueError):knowledge.anchor_candidates([{**c,'quote':'缓存未命中时应向用户索取内容。'}],blocks)
        with self.assertRaises(ValueError):knowledge.anchor_candidates([c],blocks+[{**blocks[0],'ordinal':5}])

    def test_short_precise_document_has_concept_budget_not_word_budget(self):
        mid=self.add();m=self.m(mid);self.assertEqual(m['processing_state'],'ready');self.assertLess(m['words'],200);self.assertEqual(m['cap'],1);self.assertEqual(m['remaining'],0)
        self.learned(mid);self.assertEqual((self.m(mid)['choice_remaining'],self.m(mid)['short_remaining']),(1,0))
    def test_simple_fact_does_not_automatically_get_short_answer_budget(self):
        class FactAI(FakeAI):
            def json(self,model,instruction,data):
                if '独立检查每点' in instruction:
                    return {'approved':[True]*len(data['concepts']),'short_answer':[False]*len(data['concepts'])},{}
                return super().json(model,instruction,data)
        with patch.object(knowledge,'Starflow',FactAI):mid=self.add('缓存有效期为60秒。')
        self.assertEqual(self.m(mid)['cap'],1)
        self.learned(mid)
        self.assertEqual((self.m(mid)['choice_remaining'],self.m(mid)['short_remaining']),(1,0))

    def test_pending_analysis_cannot_be_marked_learned(self):
        mid=learning.add(self.cid,'alice','未分析','text',text='缓存未命中时应回源获取内容。')['material_id']
        with self.assertRaises(ValueError):self.learned(mid)
    def test_identical_material_shares_concept_and_lifetime_budget(self):
        a=self.add();b=self.add(title='重复材料');self.learned(a);self.learned(b)
        overview=knowledge.overview(self.cid,'alice');self.assertEqual(len(overview['concepts']),1);self.assertEqual(overview['concepts'][0]['materials'],2)
        run=self.package(a,['single_choice']*3);self.assertEqual(bank.register(run,'alice'),1)
        self.assertEqual(self.m(b)['choice_remaining'],0);self.assertEqual(self.m(b)['short_remaining'],0)
        run=self.package(b,['short_answer']);self.assertEqual(bank.register(run,'alice'),0)
        self.assertEqual(self.m(a)['remaining'],0)
    def test_media_obeys_shared_course_twenty_percent_limit(self):
        mid=self.add()
        with connect(self.db) as db:
            db.execute('DELETE FROM knowledge_evidence');db.execute('DELETE FROM knowledge_concepts');db.execute('DELETE FROM knowledge_jobs');db.execute("UPDATE material_heads SET quality='media',state='indexing' WHERE id=?",(mid,))
        versions.process(mid,'alice');self.learned(mid);self.assertEqual((self.m(mid)['cap'],self.m(mid)['choice_remaining'],self.m(mid)['short_remaining']),(1,1,0))
    def test_update_requires_withdrawal_and_keeps_material_id(self):
        mid=self.add();self.learned(mid)
        with self.assertRaises(ValueError):versions.update(self.cid,mid,'alice','更新','text',text='缓存未命中时应回源获取内容。')
        policy.set_learned(self.cid,mid,'alice',False)
        versions.update(self.cid,mid,'alice','更新','text',text='缓存未命中时应回源获取内容。\n\n缓存有效期为60秒，超时必须回源。');versions.process(mid,'alice')
        self.assertEqual(len(policy.materials(self.cid,'alice')),1);self.assertFalse(self.m(mid)['learned']);self.assertEqual(self.m(mid)['cap'],2);self.assertEqual(len(versions.history(self.cid,mid,'alice')),2)
    def test_update_preserves_unchanged_question_but_stops_changed_block(self):
        mid=self.add('缓存未命中时应回源获取内容。\n\n缓存有效期为60秒，超时必须回源。');self.learned(mid)
        good=self.package(mid,['single_choice']);changed=self.package(mid,['single_choice'],2);bank.register(good,'alice');bank.register(changed,'alice')
        policy.set_learned(self.cid,mid,'alice',False);self.assertEqual(bank.entries('alice'),[])
        versions.update(self.cid,mid,'alice','更新','text',text='缓存未命中时应回源获取内容。\n\n缓存有效期为120秒，超时必须回源。');versions.process(mid,'alice');self.learned(mid)
        self.assertEqual([r['run_id'] for r in bank.entries('alice')],[good]);self.assertEqual(self.m(mid)['used'],2);self.assertEqual(self.m(mid)['cap'],2)
        with self.assertRaises(ValueError):practice.start(changed,'q0','alice')
    def test_reupload_same_content_does_not_increase_cap(self):
        mid=self.add();self.learned(mid);run=self.package(mid,['single_choice']);bank.register(run,'alice');policy.set_learned(self.cid,mid,'alice',False)
        versions.update(self.cid,mid,'alice','同内容','text',text='缓存未命中时应回源获取内容。');versions.process(mid,'alice');self.learned(mid)
        self.assertEqual((self.m(mid)['cap'],self.m(mid)['used']),(1,1))
    def test_ownership_and_bank_clear_do_not_refund_budget(self):
        mid=self.add();self.learned(mid);run=self.package(mid,['single_choice']);bank.register(run,'alice')
        with self.assertRaises(LookupError):versions.history(self.cid,mid,'bob')
        deletion.delete(self.cid,'alice','bank');self.assertEqual(self.m(mid)['used'],1)
    def test_full_delete_removes_heads_vectors_and_history(self):
        mid=self.add();deletion.delete(self.cid,'alice','course')
        with connect(self.db) as db:
            for table in ('material_heads','material_versions','knowledge_concepts','knowledge_evidence','knowledge_jobs'):self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
    def test_alignment_only_approved_corrected_segments_enter_library(self):
        upload=learning.add(self.cid,'alice','音频','file',filename='a.wav',content=base64.b64encode(b'not-real-audio').decode());mid=upload['material_id'];h=versions.heads(self.cid,'alice')[0];folder=Path(h['location']).parent/'extracted';folder.mkdir()
        data={'state':'pending_review','audio':'aligned-audio.wav','has_video':False,'windows':[{'id':'1','start':0,'end':20,'text':'转写疑点'},{'id':'2','start':20,'end':30,'text':'废话'}]}
        (folder/'alignment.json').write_text(json.dumps(data),encoding='utf-8')
        with connect(self.db) as db:db.execute("UPDATE material_heads SET state='review' WHERE id=?",(mid,))
        with self.assertRaises(ValueError):self.learned(mid)
        media.approve(self.cid,mid,'alice',[{'id':'1','approved':True,'text':'缓存未命中时应回源获取内容。'},{'id':'2','approved':False,'text':'废话'}]);versions.process(mid,'alice');self.learned(mid)
        evidence=library.evidence(self.cid,'alice');self.assertEqual(len(evidence),1);self.assertIn('0.0—20.0秒',evidence[0]['text']);self.assertNotIn('废话',evidence[0]['text'])
    def test_cross_material_correction_is_blocked_as_conflict(self):
        a=self.add('缓存有效期为60秒，超时必须回源。');self.learned(a);b=self.add('缓存有效期为120秒，超时必须回源。',title='矛盾材料')
        self.assertEqual(self.m(b)['processing_state'],'conflict')
        with self.assertRaises(ValueError):self.learned(b)
    def test_concurrent_admission_cannot_spend_short_reserve(self):
        from concurrent.futures import ThreadPoolExecutor
        mid=self.add();self.learned(mid);runs=[self.package(mid,['single_choice']*2) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:counts=list(pool.map(lambda run:bank.register(run,'alice'),runs))
        self.assertEqual(sum(counts),1);self.assertEqual(self.m(mid)['short_remaining'],0)

    def test_explicit_conflict_resolution_preserves_other_material_learning_without_new_quota(self):
        a=self.add('缓存有效期为60秒，超时必须回源。');self.learned(a);b=self.add('缓存有效期为120秒，超时必须回源。',title='新结论')
        conflict=knowledge.overview(self.cid,'alice')['conflicts'][0]
        with self.assertRaises(LookupError):knowledge.resolve_conflict(self.cid,conflict['id'],'bob')
        result=knowledge.resolve_conflict(self.cid,conflict['id'],'alice')
        self.assertFalse(result['quota_increased']);self.assertTrue(self.m(a)['learned']);self.assertFalse(self.m(b)['learned']);self.assertEqual(self.m(b)['cap'],1)
        self.learned(b)
        self.learned(a)  # Only the rejected concept is suspended; material remains usable.

    def test_material_rename_preserves_learning_quota_versions_and_evidence(self):
        mid=self.add();self.learned(mid);before=self.m(mid)
        with connect(self.db) as db:
            evidence=[tuple(r) for r in db.execute('SELECT * FROM library_evidence')]
            versions_before=[tuple(r) for r in db.execute('SELECT * FROM material_versions')]
        versions.rename(self.cid,mid,'alice',' 新名称 ')
        after=self.m(mid);self.assertEqual(after['title'],'新名称')
        for key in ('learned','cap','used','remaining','documents','version','processing_state'):
            self.assertEqual(before[key],after[key],key)
        with connect(self.db) as db:
            self.assertEqual(evidence,[tuple(r) for r in db.execute('SELECT * FROM library_evidence')])
            self.assertEqual(versions_before,[tuple(r) for r in db.execute('SELECT * FROM material_versions')])
        with self.assertRaises(LookupError):versions.rename(self.cid,mid,'bob','越权')
        with self.assertRaises(ValueError):versions.rename(self.cid,mid,'alice',' ')

    def test_busy_update_reports_actionable_error_without_creating_version(self):
        mid=self.add()
        with patch.object(learning,'GENERATION_LOCK') as lock:
            lock.acquire.return_value=False
            with self.assertRaisesRegex(versions.MaterialUpdateError,'本次未更新材料'):
                versions.update(self.cid,mid,'alice','材料','text',text='更新正文')
        self.assertEqual(self.m(mid)['version'],1)

    def test_link_sync_keeps_origin_and_uses_fresh_version_directory(self):
        link='https://365.kdocs.cn/l/test-same-link'
        result=learning.add(self.cid,'alice','链接材料','link',text=link)
        mid=result['material_id']
        result=versions.update(self.cid,mid,'alice','链接材料','link',text=link)
        with connect(self.db) as db:h=dict(db.execute('SELECT * FROM material_heads WHERE id=?',(mid,)).fetchone())
        self.assertEqual(result['version'],2)
        self.assertEqual(h['origin'],link)
        self.assertEqual(Path(h['location']).read_text(encoding='utf-8'),link)
        self.assertIn('versions',Path(h['location']).parts)
        self.assertFalse((Path(h['location']).parent/'extracted'/'extracted.json').exists())
        self.assertFalse(self.m(mid)['learned'])

    def test_audio_video_window_alignment_preserves_timestamps_and_flags(self):
        folder=self.root/'alignment';source=self.root/'source.mp4';source.write_bytes(b'synthetic')
        segments=[{'start':0,'end':5,'text':'缓存未命中。','avg_logprob':-2},{'start':8,'end':10,'text':'需要回源。'},{'start':25,'end':30,'text':'缓存有效期60秒。'}]
        def transcribe(audio,target):
            target.mkdir(parents=True,exist_ok=True);(target/'segments.jsonl').write_text('\n'.join(json.dumps(s) for s in segments),encoding='utf-8')
        def ffmpeg(args,**kwargs):Path(args[-1]).write_bytes(b'synthetic')
        with patch('backend.transcribe.transcribe',side_effect=transcribe),patch.object(media.subprocess,'run',side_effect=ffmpeg) as ff,patch.object(media,'image_text',return_value='缓存画面文字'):
            blocks,result=media.prepare(source,folder,video=source)
        self.assertIsNone(blocks);self.assertEqual(len(result['windows']),2);self.assertEqual(result['windows'][0]['end'],10)
        self.assertIn('需要回源',result['windows'][0]['speech']);self.assertTrue(result['windows'][0]['uncertain']);self.assertFalse(result['windows'][0]['approved'])
        frames=[c.args[0] for c in ff.call_args_list if '-ss' in c.args[0]];self.assertEqual([a[a.index('-ss')+1] for a in frames],['5.0','27.5'])

    def test_withdrawal_during_cloud_grading_cannot_update_mastery(self):
        mid=self.add();
        for i in range(4):self.add(f'独立事实{i}说明材料需要授权。')
        self.learned(mid);run=self.package(mid,['short_answer']);bank.register(run,'alice');sid=bank.start_review(run,'q0','alice')['session_id']
        parent=self
        class Grader:
            def json(self,model,instruction,data):
                policy.set_learned(parent.cid,mid,'alice',False)
                return {'items':[{'id':'r1','level':'full','answer_quote':'回源','reason':'已说明'}]},{}
        with patch.object(practice,'Starflow',Grader),self.assertRaises(ValueError):practice.submit(sid,'需要回源','alice')
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM mastery_events').fetchone()[0],0)

if __name__=='__main__':unittest.main()
