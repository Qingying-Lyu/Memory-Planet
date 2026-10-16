import hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from . import learning,library,bank,enrichment,practice,material_policy as policy,challenges
from .store import connect
from .test_practice import sample_question
class LearningTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.path=self.root/'db.sqlite3'
  self.patches=[patch.object(m,'connect',lambda:connect(self.path)) for m in (learning,library,bank,enrichment,practice,policy,challenges)]
  self.patches += [patch.object(learning,'MATERIALS',self.root/'materials'),patch.object(library,'LIBRARY',self.root/'library'),patch.object(bank,'ROOT',self.root)]
  # Historical fixtures exercise grandfathered word budgets before adoption.
  self.patches += [patch('backend.material_versions.track',return_value=None)]
  self.patches += [patch('backend.material_versions.heads',return_value=[])]
  for p in self.patches:p.start()
  self.packages={};self.mock=patch.object(practice,'package',side_effect=lambda run,owner:self.packages[run]);self.mock.start()
 def tearDown(self):
  self.mock.stop()
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def create(self):
  course=learning.create('local-owner','缓存课程','training')['id']
  learning.add(course,'local-owner','资料一','text',text='缓存原理。'+'字'*996)
  material=policy.materials(course,'local-owner')[0]
  policy.set_learned(course,material['id'],'local-owner')
  return course,material['id']
 def package(self,course,run,count):
  rows=library.evidence(course,'local-owner');q=sample_question()
  self.packages[run]={'run_id':run,'course_id':course,'questions':[dict(q,id=f'q{i}',prompt=hashlib.sha256(f'{run}-{i}'.encode()).hexdigest()+'缓存场景判断') for i in range(count)],'framework':{'concepts':[{'id':'c1','evidence':q['evidence']}]},'evidence_map':{'1':{k:rows[0][k] for k in ('document_id','ordinal','scope_revision','digest')}}}
 def test_cap_counts_accepted_only_and_deep_review_survives_exhaustion(self):
  course,mid=self.create();self.package(course,'run1',10)
  self.assertEqual(bank.register('run1','local-owner'),5)
  self.assertEqual(bank.register('run1','local-owner'),0)
  m=policy.materials(course,'local-owner')[0];self.assertEqual((m['cap'],m['used'],m['remaining']),(5,5,0))
  self.assertEqual(bank.review_plan(course,'local-owner',10)['available'],5)
  self.package(course,'run2',3);self.assertEqual(bank.register('run2','local-owner'),0)
  with patch('backend.pipeline.run_sample') as generate:
   challenges.generate();generate.assert_not_called()
  self.assertEqual(challenges.board('local-owner')['state'],'exhausted')
 def test_new_material_does_not_disable_other_materials(self):
  course,mid=self.create();self.package(course,'run1',2);bank.register('run1','local-owner')
  learning.add(course,'local-owner','资料二','text',text='新材料'*200)
  rows=policy.materials(course,'local-owner');self.assertEqual(sum(m['learned'] for m in rows),1)
  self.assertEqual(bank.review_plan(course,'local-owner')['available'],2)
  self.assertNotIn('status',learning.list_courses('local-owner')[0])
  policy.set_learned(course,mid,'local-owner',False)
  self.assertEqual(bank.review_plan(course,'local-owner')['available'],0)
 def test_material_authentication_and_pending_source(self):
  course,mid=self.create()
  with self.assertRaises(LookupError):policy.set_learned(course,mid,'other')
  learning.add(course,'local-owner','链接','link',text='https://example.org/course')
  pending=next(m for m in policy.materials(course,'local-owner') if not m['parsed'])
  policy.set_learned(course,pending['id'],'local-owner')
  pending=next(m for m in policy.materials(course,'local-owner') if m['id']==pending['id'])
  self.assertTrue(pending['learned']);self.assertEqual(pending['remaining'],0)
 def test_word_count_deduplicates_and_caps_are_monotonic(self):
  self.assertEqual(policy.word_count(['缓存 cache 123','缓存 cache 123']),4)
  self.assertEqual(policy.word_count(['字'*199])//200,0)
  self.assertEqual(policy.word_count(['字'*200])//200,1)
  self.assertEqual(policy.word_count(['字'*10000])//200,50)
 def test_weighted_allocation_keeps_low_frequency_for_mastered_courses(self):
  credits={};counts={'weak':0,'strong':0}
  for day in range(30):
   daily=challenges.allocate({'weak':0,'strong':100},15,credits)
   for key,value in daily.items():counts[key]+=value
  self.assertAlmostEqual(counts['weak']/counts['strong'],10,delta=.5)
  self.assertEqual(sum(counts.values()),450)
 def test_small_frequency_courses_are_not_starved(self):
  credits={};served=0
  scores={str(i):0 for i in range(20)};scores['mastered']=100
  for day in range(60):served+=challenges.allocate(scores,15,credits)['mastered']
  self.assertGreater(served,0)

 def test_parallel_admission_cannot_exceed_cap(self):
  from concurrent.futures import ThreadPoolExecutor
  course,mid=self.create();self.package(course,'run1',4);self.package(course,'run2',4)
  with ThreadPoolExecutor(max_workers=2) as pool:counts=list(pool.map(lambda run:bank.register(run,'local-owner'),('run1','run2')))
  self.assertEqual(sum(counts),5)
  self.assertEqual(policy.materials(course,'local-owner')[0]['used'],5)
 def test_revoking_and_reconfirming_does_not_reset_budget(self):
  course,mid=self.create();self.package(course,'run1',3);bank.register('run1','local-owner')
  policy.set_learned(course,mid,'local-owner',False);policy.set_learned(course,mid,'local-owner')
  self.assertEqual(policy.materials(course,'local-owner')[0]['remaining'],2)
 def test_cross_material_question_requires_all_materials_learned(self):
  course,mid=self.create();self.package(course,'run1',1)
  learning.add(course,'local-owner','未学材料','text',text='新内容'*100)
  with connect(self.path) as db:
   doc=db.execute('SELECT * FROM library_documents WHERE title=?',('未学材料',)).fetchone()
  q=self.packages['run1']['questions'][0]
  q['evidence'].append({'block':2,'quote':'新内容新内容'})
  self.packages['run1']['evidence_map']['2']={'document_id':doc['id'],'ordinal':1,'scope_revision':doc['scope_revision'],'digest':doc['digest']}
  self.assertEqual(bank.register('run1','local-owner'),0)
