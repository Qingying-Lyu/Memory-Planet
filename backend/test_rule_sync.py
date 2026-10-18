"""Regression contracts for confirmed rules; all data and model calls isolated."""
import json
import math
from unittest import TestCase
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from . import test_knowledge_workflow as fixtures
FakeAI=fixtures.FakeAI
from . import knowledge,question_quota as quota,bank,practice,learning,material_policy as policy,material_versions as versions,pipeline
from .store import connect
from .scoring import update


class RuleSyncTests(TestCase):
    def setUp(self):
        self.f=fixtures.KnowledgeWorkflowTests('test_pending_analysis_cannot_be_marked_learned')
        self.f.setUp()
    def tearDown(self):self.f.tearDown()
    def cid(self,mid):
        with connect(self.f.db) as db:return db.execute('SELECT concept_id FROM knowledge_evidence WHERE material_id=?',(mid,)).fetchone()[0]
    def state(self):
        with connect(self.f.db) as db:return quota.course_state(db,self.f.cid,'alice')
    def fill(self,n=5):
        mids=[self.f.add(f'独立主题{i}需要依据各自材料核对。') for i in range(n)]
        for mid in mids:self.f.learned(mid)
        return mids
    def angles(self,mid,n):
        cid=self.cid(mid)
        with connect(self.f.db) as db:
            e=db.execute('SELECT * FROM knowledge_evidence WHERE material_id=?',(mid,)).fetchone()
            quota.sync_angles(db,cid,mid,[{'target':f'目标{i}检验不同的操作决策','document_id':e['document_id'],'ordinal':e['ordinal'],'quote':e['quote']} for i in range(n-1)],True)
        return cid
    def package(self,mid,kind='single_choice',ids=None):
        run=self.f.package(mid,[kind])
        p=self.f.root/'data/runs'/run/'package.json'
        data=json.loads(p.read_text(encoding='utf-8'))
        if ids is not None:data['questions'][0]['angle_ids']=ids
        p.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        return run
    def test_confirmed_scoring_boundaries_and_full_precision(self):
        self.assertEqual(update(None,100,1),85)
        self.assertAlmostEqual(update(84.99,100,2),84.99+.5/math.sqrt(2)*(100-84.99))
        self.assertAlmostEqual(update(85,100,2),85+.25/math.sqrt(2)*15)
        self.assertAlmostEqual(update(85,0,2),85+.25/math.sqrt(2)*(0-85))
    def test_less_than_five_shared_angles_never_grants_short(self):
        self.fill(4);s=self.state()
        self.assertEqual((s['cap'],s['short_limit'],s['short_remaining']),(4,0,0))
    def test_short_reserve_twenty_percent_not_added_to_total(self):
        self.fill(5);s=self.state()
        self.assertEqual((s['cap'],s['short_limit'],s['short_remaining']),(5,1,1))
        self.assertEqual(sum(c['choice_remaining'] for c in s['concepts'].values()),4)
    def test_one_concept_can_have_more_than_three_reviewed_angles(self):
        mid=self.f.add();self.f.learned(mid);cid=self.angles(mid,6)
        s=self.state();self.assertEqual(s['concepts'][cid]['cap'],6)
        self.assertEqual(s['short_limit'],1)
    def test_choice_cannot_cover_reserved_last_angle(self):
        mids=self.fill();s=self.state();cid=next(c for c,x in s['concepts'].items() if x['reserved'])
        mid=next(m for m in mids if self.cid(m)==cid)
        self.assertEqual(bank.register(self.package(mid),'alice'),0)
        self.assertEqual(bank.register(self.package(mid,'short_answer'),'alice'),1)
        self.assertEqual(self.state()['short_used'],1)
        self.assertEqual(bank.register(self.package(mid,'short_answer'),'alice'),0)
    def test_comprehensive_short_covers_targets_but_charges_one(self):
        mid=self.f.add();self.f.learned(mid);cid=self.angles(mid,5)
        ids=[a['id'] for a in self.state()['concepts'][cid]['angles'][:2]]
        run=self.package(mid,'short_answer',ids)
        self.assertEqual(bank.register(run,'alice'),1)
        self.assertEqual(bank.register(run,'alice'),0)
        c=self.state()['concepts'][cid]
        self.assertEqual((c['cap'],c['used'],c['remaining']),(5,1,4))
        self.assertEqual(len(c['angles']),3)
        self.assertEqual(bank.register(self.package(mid,ids=[ids[0]]),'alice'),0)
    def test_concurrent_short_admission_stays_under_course_limit(self):
        mids=self.fill();s=self.state();cid=next(c for c,x in s['concepts'].items() if x['reserved'])
        mid=next(m for m in mids if self.cid(m)==cid)
        runs=[self.package(mid,'short_answer') for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:counts=list(pool.map(lambda r:bank.register(r,'alice'),runs))
        self.assertEqual(sum(counts),1)
        self.assertEqual(self.state()['short_used'],1)
    def test_release_unspent_short_does_not_refund_spent_quota(self):
        self.fill();s=self.state();cid=next(c for c,x in s['concepts'].items() if x['reserved'])
        with connect(self.f.db) as db:
            with self.assertRaises(LookupError):quota.release_short(db,self.f.cid,cid,'bob')
            result=quota.release_short(db,self.f.cid,cid,'alice')
        self.assertFalse(result['refunded']);self.assertEqual(self.state()['used'],0)
        self.assertFalse(self.state()['concepts'][cid]['reserved'])
    def test_depth_review_never_calls_models_or_fills_shortfall(self):
        mid=self.f.add();self.f.learned(mid);run=self.package(mid);bank.register(run,'alice')
        with patch.object(pipeline,'run_sample',side_effect=AssertionError('review must not generate')):
            plan=learning.prepare_review(self.f.cid,'alice')
        self.assertEqual(len(plan['questions']),1)
    def test_partial_conflict_keeps_other_points_and_other_material_learned(self):
        a=self.f.add('缓存有效期为60秒，超时必须回源。\n\n缓存未命中应回源获取内容。');self.f.learned(a)
        good=self.f.package(a,['single_choice'],ordinal=2);bank.register(good,'alice')
        disputed=self.package(a);bank.register(disputed,'alice')
        b=self.f.add('缓存有效期为120秒，超时必须回源。\n\n材料授权必须按用户身份核对。')
        self.assertEqual(self.f.m(b)['processing_state'],'ready');self.f.learned(b)
        self.assertEqual([r['run_id'] for r in bank.entries('alice')],[good])
        conflict=knowledge.overview(self.f.cid,'alice')['conflicts'][0]
        result=knowledge.resolve_conflict(self.f.cid,conflict['id'],'alice')
        self.assertEqual(result['withdrawn_materials'],0);self.assertTrue(self.f.m(a)['learned'])
        self.assertIn(good,[r['run_id'] for r in bank.entries('alice')])
    def test_migration_preserves_all_old_rows_even_when_over_new_cap(self):
        mid=self.f.add();cid=self.cid(mid)
        with connect(self.f.db) as db:
            db.execute('INSERT INTO knowledge_questions VALUES(?,?,?,?,?)',('alice','old1','q1',cid,'single_choice'))
            db.execute('INSERT INTO knowledge_questions VALUES(?,?,?,?,?)',('alice','old2','q2',cid,'short_answer'))
            old=[tuple(r) for r in db.execute('SELECT * FROM knowledge_questions')]
            db.execute('DELETE FROM knowledge_quota_migrations');db.execute('DELETE FROM knowledge_angles');db.execute('DELETE FROM knowledge_angle_sources')
            quota.initialize(db);s=quota.course_state(db,self.f.cid,'alice')
            self.assertEqual(old,[tuple(r) for r in db.execute('SELECT * FROM knowledge_questions')])
            self.assertEqual((s['cap'],s['used'],s['short_used']),(1,2,1))
            self.assertEqual(s['concepts'][cid]['remaining'],0)
    def test_unreviewed_angles_cannot_inflate_quota(self):
        class RejectAngles(FakeAI):
            def json(self,model,instruction,data):
                if '独立逐项审核每个考查角度' in instruction:return {'approved':[[False]*len(i['angles']) for i in data['items']],'simple_fact':[False]*len(data['items'])},{}
                return super().json(model,instruction,data)
        with patch.object(knowledge,'Starflow',RejectAngles):mid=self.f.add()
        self.assertEqual(self.f.m(mid)['cap'],0)
        self.assertGreater(self.f.m(mid)['pending_concepts'],0)

    def test_failed_extraction_batch_does_not_discard_other_reviewed_blocks(self):
        from .cloud import CloudError
        class PartialFailure(FakeAI):
            def json(self,model,instruction,data):
                if '识别所有独立' in instruction and any('NETWORK-FAIL' in b['text'] for b in data['blocks']):raise CloudError('Timeout')
                return super().json(model,instruction,data)
        with patch.object(knowledge,'Starflow',PartialFailure):
            mid=self.f.add('NETWORK-FAIL'+'字'*3988+'\n\n缓存未命中时应回源获取内容。')
        self.assertEqual(self.f.m(mid)['processing_state'],'ready')
        self.assertGreater(self.f.m(mid)['pending_concepts'],0)
        self.assertGreater(self.f.m(mid)['cap'],0)
