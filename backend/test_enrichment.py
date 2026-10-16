import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from . import enrichment,learning,library,material_policy
from .store import connect
class EnrichmentTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.path=self.root/'db.sqlite3'
  self.patches=[patch.object(m,'connect',lambda:connect(self.path)) for m in (enrichment,learning,library,material_policy)]
  self.patches += [patch.object(library,'LIBRARY',self.root/'library'),patch.object(learning,'MATERIALS',self.root/'materials')]
  self.patches += [patch('backend.material_versions.track',return_value=None)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def course(self,note=''):
  course=learning.create('local-owner','缓存笔记','personal',note)['id']
  learning.add(course,'local-owner','简略笔记','text',text='缓存原理：减少回源；缓存更新策略。')
  for m in material_policy.materials(course,'local-owner'):material_policy.set_learned(course,m['id'],'local-owner')
  return course
 def test_optional_note_and_scope_change_preserve_original(self):
  course=self.course()
  self.assertEqual(learning.list_courses('local-owner')[0]['exam_note'],'')
  old=library.evidence(course,'local-owner')[0]['scope_revision']
  learning.set_exam_note(course,'local-owner','只考缓存更新策略')
  self.assertFalse(any(m['learned'] for m in material_policy.materials(course,'local-owner')))
  evidence=library.evidence(course,'local-owner')
  self.assertNotEqual(evidence[0]['scope_revision'],old)
  self.assertEqual(evidence[0]['text'],'缓存原理：减少回源；缓存更新策略。')
  self.assertEqual(learning.list_courses('local-owner')[0]['exam_note'],'只考缓存更新策略')
 def test_research_import_is_source_grounded_and_not_a_note(self):
  course=self.course('仅考缓存原理')
  topic={'title':'缓存原理','query':'cache documentation','note_block':1,'note_quote':'缓存原理'}
  quote='缓存可以减少回源请求。'*20
  with patch.object(enrichment,'plan',return_value=[topic]),patch.object(enrichment,'search',return_value=['https://example.org/cache']),patch.object(enrichment,'fetch',return_value=('https://example.org/cache',quote.encode())),patch.object(enrichment,'excerpts',return_value=[quote]):
   enrichment.run(course,'local-owner')
   self.assertEqual(enrichment.status(course,'local-owner')['state'],'ready')
   self.assertEqual(len(library.evidence(course,'local-owner')),2)
   self.assertEqual(len(enrichment.scope(course,'local-owner')[1]),1)
   enrichment.run(course,'local-owner')
   self.assertEqual(len(library.evidence(course,'local-owner')),2)
 def test_search_failure_does_not_fabricate_evidence(self):
  course=self.course()
  with patch.object(enrichment,'plan',return_value=[{'title':'缓存原理','query':'cache','note_block':1,'note_quote':'缓存原理'}]),patch.object(enrichment,'search',return_value=[]):
   with self.assertRaises(ValueError):enrichment.run(course,'local-owner')
  self.assertEqual(enrichment.status(course,'local-owner')['state'],'waiting')
  self.assertEqual(len(library.evidence(course,'local-owner')),1)
 def test_review_rejects_out_of_scope_excerpts(self):
  quote='缓存可以减少回源请求。'*20
  class Fake:
   def __init__(self,value):self.value=value
   def json(self,*args):return self.value,{}
  with patch.object(enrichment,'model_target',side_effect=[(Fake({'quotes':[quote]}),'extract'),(Fake({'approved':[False]}),'review')]):
   result=enrichment.excerpts('https://example.org',('<main>'+quote+'</main>').encode(),{'note_block':1},[{'text':'缓存原理'}],'仅缓存')
   self.assertEqual(result,[])
 def test_training_never_searches(self):
  course=learning.create('local-owner','公司培训','training','')['id']
  with patch.object(enrichment,'search') as search:
   enrichment.run(course,'local-owner');search.assert_not_called()
 def test_private_network_is_not_fetched(self):
  with patch.object(enrichment.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
   with self.assertRaises(ValueError):enrichment.fetch('https://example.org/private')

 def test_training_mindmap_enriches_only_recognized_learned_material(self):
  import json
  course=learning.create('local-owner','公司培训','training','')['id']
  learning.add(course,'local-owner','导图','text',text='缓存原理：减少回源；缓存更新策略。')
  learning.add(course,'local-owner','普通培训','text',text='内部产品定价规则。')
  for m in material_policy.materials(course,'local-owner'):material_policy.set_learned(course,m['id'],'local-owner')
  notes=library.evidence(course,'local-owner');doc=notes[0]['document_id']
  location=self.root/'capture'/'source.url.txt';(location.parent/'extracted').mkdir(parents=True)
  (location.parent/'extracted'/'extracted.json').write_text(json.dumps({'document_type':'mindmap'}),encoding='utf-8')
  with connect(self.path) as db:
   from . import material_versions
   material_versions.initialize(db)
   db.execute("INSERT INTO material_heads(id,course_id,origin,title,kind,location,document_id,version,quality,state,next_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(material_policy.key(course,notes[0]['origin']),course,notes[0]['origin'],'导图','link',str(location),doc,1,'document','ready',0))
  self.assertEqual({r['document_id'] for r in enrichment.scope(course,'local-owner')[1]},{doc})
  topic={'title':'缓存原理','query':'cache','note_block':1,'note_quote':'缓存原理'}
  with patch.object(enrichment,'plan',return_value=[topic]),patch.object(enrichment,'search',return_value=['https://example.org/cache']),patch.object(enrichment,'fetch',return_value=('https://example.org/cache',b'body')),patch.object(enrichment,'excerpts',return_value=['缓存可以减少回源请求。'*20]):
   enrichment.run(course,'local-owner')
  with connect(self.path) as db:
   row=db.execute('SELECT parent_document_id FROM enrichment_documents').fetchone()
   self.assertEqual(row[0],doc)
   path=db.execute('SELECT markdown FROM library_documents WHERE id IN (SELECT document_id FROM enrichment_documents)').fetchone()[0]
   self.assertTrue(Path(path).exists())
  (location.parent/'extracted'/'extracted.json').write_text(json.dumps({'document_type':'flowchart'}),encoding='utf-8')
  self.assertIsNone(enrichment.scope(course,'local-owner')[0])
