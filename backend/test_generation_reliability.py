import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from .store import connect
from . import pipeline,knowledge,library,learning,material_policy as policy,bank,practice,challenges,enrichment
from .test_knowledge_workflow import FakeAI
from .test_practice import sample_question

class GenerationReliabilityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'db.sqlite3';self.patches=[]
  for m in (pipeline,knowledge,library,learning,policy,bank,practice,challenges,enrichment):self.patches.append(patch.object(m,'connect',lambda:connect(self.db)))
  self.patches += [patch.object(m,'ROOT',self.root) for m in (pipeline,bank,practice)]
  self.patches += [patch.object(learning,'MATERIALS',self.root/'materials'),patch.object(library,'LIBRARY',self.root/'library'),patch.object(knowledge,'Starflow',FakeAI),patch('backend.cloud.Starflow',FakeAI),patch.object(knowledge,'embed',side_effect=lambda xs:[[1.,0.] for _ in xs])]
  for p in self.patches:p.start()
  self.course=learning.create('local-owner','新课程','training')['id']
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def material(self,title,text):
  from . import material_versions
  with patch.object(material_versions,'connect',lambda:connect(self.db)):
   result=learning.add(self.course,'local-owner',title,'text',text=text);mid=result['material_id'];material_versions.process(mid)
  return mid
 def client(self,invalid=False):
  class Client:
   def json(self,model,instruction,data):
    if '生成' in instruction and 'questions' not in data and 'framework' in data:
     qs=[]
     for i,c in enumerate(data['framework']['concepts'][:2]):
      q=sample_question();q.update(id='q'+str(i+1),concept_id=c['id'],evidence=c['evidence'],prompt='在业务缓存未命中且源站可访问的情况下，应采取哪项处理措施？'+str(i),reference_answer='回源获取内容',explanation='依据材料，未命中需要回源。')
      qs.append(q)
     if invalid:qs.append({**qs[0],'id':'q99','options':['不合格']})
     return {'questions':qs},{}
    if '独立作答并审题' in instruction:return {'reviews':[{'id':q['id'],'answer_index':0,'grounded':True,'unambiguous':True,'challenging':True,'novel':True,'in_scope':True,'reason':'符合原文'} for q in data['questions']]},{}
    raise AssertionError('Unexpected test stage')
  return Client()
 def test_newly_learned_material_generates_and_enters_daily_without_manual_bank(self):
  mid=self.material('新资料','缓存未命中时，应回源获取内容。\n\n缓存命中时，直接返回缓存内容。')
  self.assertFalse(challenges.courses('local-owner'))
  policy.set_learned(self.course,mid,'local-owner');self.assertEqual(challenges.courses('local-owner')[0]['id'],self.course)
  with patch.object(pipeline,'model_target',return_value=(self.client(), 'test-model')):
   run=pipeline.run_sample(course_id=self.course,material_id=mid,choice_count=2);self.assertGreater(bank.register(run,'local-owner'),0)
  from datetime import datetime
  with patch('backend.pipeline.run_sample',side_effect=RuntimeError('No extra model calls')):
   challenges.generate(now=datetime(2026,10,15,11,tzinfo=challenges.TZ))
  board=challenges.board('local-owner',datetime(2026,10,15,11,tzinfo=challenges.TZ))
  self.assertTrue(board['questions']);self.assertTrue(all(q['course_id']==self.course for q in board['questions']))
  policy.set_learned(self.course,mid,'local-owner',False)
  self.assertFalse(challenges.board('local-owner',datetime(2026,10,15,11,tzinfo=challenges.TZ))['questions'])
 def test_one_invalid_question_does_not_discard_valid_sibling(self):
  mid=self.material('资料','缓存未命中时，应回源获取内容。')
  policy.set_learned(self.course,mid,'local-owner')
  with patch.object(pipeline,'model_target',return_value=(self.client(invalid=True),'test-model')):
   run=pipeline.run_sample(course_id=self.course,material_id=mid,choice_count=2)
  package=json.loads((self.root/'data/runs'/run/'package.json').read_text(encoding='utf-8'))
  self.assertEqual(len(package['questions']),1);self.assertTrue(package['held']);self.assertEqual(bank.register(run,'local-owner'),1)
 def test_failed_material_cools_down_and_does_not_starve_another(self):
  a={'id':'a','cap':50,'used':0,'remaining':50,'choice_remaining':50};b={**a,'id':'b','cap':2,'remaining':2,'choice_remaining':2}
  learning.record_generation(self.course,'a',0)
  self.assertEqual(learning.select_generation_material([a,b])['id'],'b')
  self.assertEqual(learning.generation_health('a')['state'],'retry')
  learning.record_generation(self.course,'b',1)
  self.assertEqual(learning.generation_health('b')['state'],'ready')
 def test_unanswered_new_material_is_not_buried_under_old_unanswered_bank(self):
  with connect(self.db) as db:
   policy.initialize(db);bank.initialize(db)
   for q,mid in [('old','m-old'),('seen','m-old'),('new','m-new')]:db.execute('INSERT INTO bank_materials VALUES(?,?,?,?)',('local-owner','r',q,mid))
   db.execute('INSERT INTO practice_sessions VALUES(?,?,?,?,?,?,?)',('s','local-owner','r','seen',0,'A','{"q":100}'))
  rows=[{'run_id':'r','question_id':q,'kind':'single_choice','concept_key':'k','created_at':at} for q,at in [('old','2026-10-01'),('new','2026-10-15')]]
  with patch.object(bank,'entries',return_value=rows):ranked=challenges.ranked_questions('local-owner')
  self.assertEqual(ranked[0]['question_id'],'new')
 def test_transient_generation_failure_recovers_without_early_quota_charge(self):
  from .cloud import CloudError
  mid=self.material('恢复测试','缓存未命中时，应回源获取内容。');policy.set_learned(self.course,mid,'local-owner')
  inner=self.client();models=[]
  class Flaky:
   endpoint='https://kspmas.ksyun.com/v1'
   def json(self,model,instruction,data):
    if 'framework' in data and 'questions' not in data:
     models.append(model)
     if len(models)==1:raise CloudError('模拟临时服务失败')
    return inner.json(model,instruction,data)
  with patch.object(pipeline,'model_target',return_value=(Flaky(),'test-model')):
   run=pipeline.run_sample(course_id=self.course,material_id=mid,choice_count=1)
  self.assertEqual(len(models),2);self.assertEqual(models[-1],'glm-5.3-flash');self.assertNotEqual(models[0],models[-1])
  self.assertEqual(policy.materials(self.course,'local-owner')[0]['used'],0)
  self.assertEqual(bank.register(run,'local-owner'),1)
 def test_first_failed_material_does_not_block_next_material_seed(self):
  a=self.material('资料A','缓存未命中时，应回源获取内容。');b=self.material('资料B','缓存命中时，直接返回缓存内容。')
  for mid in (a,b):policy.set_learned(self.course,mid,'local-owner')
  learning.enqueue(self.course,'local-owner');called=[]
  def generate(**kwargs):
   called.append(kwargs['material_id'])
   if kwargs['material_id']==a:raise RuntimeError('模拟单份资料模型调用失败')
   return 'recovered'
  with patch('backend.pipeline.run_sample',side_effect=generate),patch.object(bank,'register',return_value=1),patch.object(bank,'sync'),patch.object(enrichment,'run'):
   learning.generate(self.course,'local-owner')
  self.assertIn(a,called);self.assertIn(b,called)
  self.assertEqual(learning.generation_health(a)['state'],'retry');self.assertEqual(learning.generation_health(b)['state'],'ready')
if __name__=='__main__':unittest.main()
