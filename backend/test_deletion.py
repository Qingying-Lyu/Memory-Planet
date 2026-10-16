import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from . import deletion,bank,learning,library,practice,material_policy,enrichment,challenges
from .store import connect
from .test_practice import sample_question

class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'data/db.sqlite3'
        self.patches=[patch.object(m,'connect',lambda:connect(self.db)) for m in (deletion,bank,learning,library,practice,material_policy,enrichment,challenges)]
        self.patches.extend([patch.object(m,'ROOT',self.root) for m in (deletion,bank,practice)])
        self.patches.extend([patch.object(learning,'MATERIALS',self.root/'data/materials'),patch.object(library,'LIBRARY',self.root/'课程库')])
        self.patches.append(patch('backend.material_versions.track',return_value=None))
        for p in self.patches:p.start()
        self.course=learning.create('alice','待删除课程','training')['id']
        self.other=learning.create('bob','其他用户课程','training')['id']
        for title in ('资料一','资料二'):learning.add(self.course,'alice',title,'text',text='缓存可以减少回源请求。'+title+'字'*2000)
        self.materials=material_policy.materials(self.course,'alice')
        for m in self.materials:material_policy.set_learned(self.course,m['id'],'alice')
        self.run='1'*32;rows=library.evidence(self.course,'alice');questions=[];concepts=[]
        for i,row in enumerate(rows,1):
            q=sample_question();q.update(id=f'q{i}',concept_id=f'c{i}',prompt=hashlib.sha256(str(i).encode()).hexdigest()+'场景判断',evidence=[{'block':i,'quote':'缓存可以减少回源请求'}])
            questions.append(q);concepts.append({'id':f'c{i}','title':'缓存'+str(i),'summary':'缓存原理','evidence':q['evidence']})
        self.data={'run_id':self.run,'course_id':self.course,'questions':questions,'framework':{'concepts':concepts},'evidence_map':{str(i):{k:row[k] for k in ('document_id','ordinal','digest','scope_revision')} for i,row in enumerate(rows,1)}}
        self.folder=self.root/'data/runs'/self.run;self.folder.mkdir(parents=True)
        self.manifest={'owner':'alice','course_id':self.course,'state':'review_preview'}
        self.save();bank.register(self.run,'alice')
        self.session=bank.start_review(self.run,'q1','alice');practice.submit(self.session['session_id'],'0','alice')
    def save(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        (self.folder/'package.json').write_text(json.dumps(self.data),encoding='utf-8');(self.folder/'manifest.json').write_text(json.dumps(self.manifest),encoding='utf-8')
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_partial_material_removes_related_question_but_preserves_other(self):
        doc=self.data['evidence_map']['1']['document_id']
        mid=next(m['id'] for m in self.materials if doc in m['documents'])
        plan=deletion.preview(self.course,'alice','materials',[mid]);self.assertEqual(plan['questions'],1)
        deletion.delete(self.course,'alice','materials',[mid])
        self.assertEqual(len(material_policy.materials(self.course,'alice')),1)
        self.assertEqual([r['question_id'] for r in bank.entries('alice',self.course)],['q2'])
        bank.start_review(self.run,'q2','alice')
        with self.assertRaises(LookupError):practice.submit(self.session['session_id'],'0','alice')
        self.assertFalse((self.root/'课程库'/doc).exists())
        bank.sync('alice');self.assertEqual(len(bank.entries('alice',self.course)),1)
    def test_bank_only_preserves_materials_used_budget_and_blocks_reimport(self):
        before=[m['used'] for m in material_policy.materials(self.course,'alice')]
        deletion.delete(self.course,'alice','bank')
        self.assertEqual(bank.entries('alice',self.course),[])
        self.assertEqual([m['used'] for m in material_policy.materials(self.course,'alice')],before)
        self.assertEqual(len(material_policy.materials(self.course,'alice')),2)
        self.save();bank.sync('alice');self.assertEqual(bank.entries('alice',self.course),[])
    def test_whole_course_deletion_and_other_owner_protection(self):
        with self.assertRaises(LookupError):deletion.delete(self.course,'bob','course')
        with self.assertRaises(ValueError):deletion.preview(self.course,'alice','materials',['not-a-material'])
        deletion.delete(self.course,'alice','course')
        self.assertEqual(learning.list_courses('alice'),[])
        self.assertEqual(len(learning.list_courses('bob')),1)
        self.assertFalse(self.folder.exists())
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM practice_sessions WHERE owner=?',('alice',)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM library_documents WHERE course_id=?',(self.course,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM mastery_events WHERE owner=?',('alice',)).fetchone()[0],0)
    def test_deletion_refuses_external_paths(self):
        outside=self.root.parent/'external-private-material.txt'
        with self.assertRaises(ValueError):deletion.safe_path(outside)
        with self.assertRaises(ValueError):deletion.safe_path(self.root/'backend')
    def test_busy_generation_refuses_mutation(self):
        learning.GENERATION_LOCK.acquire()
        try:
            with self.assertRaises(ValueError):deletion.delete(self.course,'alice','course')
        finally:learning.GENERATION_LOCK.release()
        self.assertEqual(len(bank.entries('alice',self.course)),2)
