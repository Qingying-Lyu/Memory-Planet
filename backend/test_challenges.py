import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from . import bank, challenges, library, practice, learning, enrichment, material_policy
from .store import connect
from .test_practice import sample_question


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'db.sqlite3'
        (self.root/'data'/'runs').mkdir(parents=True)
        self.patches = [patch.object(m, 'connect', lambda: connect(self.path))
                        for m in (bank, challenges, library, practice, learning, enrichment, material_policy)]
        self.patches += [patch.object(bank, 'ROOT', self.root),
                         patch.object(library, 'LIBRARY', self.root/'library'),
                         patch.object(bank,'concept_key',lambda package,q:package['course_id']+':test:'+package['run_id']+':'+q['id'])]
        for p in self.patches:
            p.start()
        self.packages = {}
        self.p = patch.object(practice, 'package', side_effect=lambda r, o: self.packages[r])
        self.p.start()
        for course in ('a', 'b'):
            with connect(self.path) as db:
                db.execute('INSERT INTO courses VALUES(?,?,?)', (course, 'local-owner', course))
            library.set_scope(course, 'local-owner', 'training', ['缓存'])
            doc = library.ingest(course, 'local-owner', '资料', 'local',
                                 [(1, 'text', '缓存可以减少回源请求。'+''.join(chr(0x4e00+i) for i in range(20000)))], '文字')
            library.approve(doc['document_id'], 'local-owner', {1: '缓存'})
        for course in ('a','b'):
            for m in material_policy.materials(course,'local-owner'):
                from .test_quota_fixtures import reviewed
                with connect(self.path) as db:reviewed(db,course,m['id'],100,separate=True)
                material_policy.set_learned(course,m['id'],'local-owner')
        self.counter = 0

    def tearDown(self):
        self.p.stop()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def fake_pipeline(self, **kwargs):
        self.counter += 1
        run = f'{self.counter:032x}'
        rows = library.evidence(kwargs['course_id'], 'local-owner')
        q = sample_question()
        questions = []
        from .question_quota import course_state
        with connect(self.path) as db:
            state=course_state(db,kwargs['course_id'],'local-owner')
            candidates=[cid for cid,c in state['concepts'].items() if c['choice_remaining']>0]
        for i in range(min(kwargs['choice_count'],len(candidates))):
            prompt = hashlib.sha256(f'{run}:{i}'.encode()).hexdigest()+'场景判断'
            questions.append(dict(q, id=f'q{i+1}',concept_id=f'c{i+1}',prompt=prompt))
        self.packages[run] = {'run_id':run,'course_id':kwargs['course_id'],
            'questions':questions,'stable_concepts':{f'c{i+1}':cid for i,cid in enumerate(candidates[:len(questions)])},'framework':{'concepts':[{'id':q['concept_id'],'evidence':q['evidence']} for q in questions]},
            'evidence_map':{'1':{k:rows[0][k] for k in ('document_id','scope_revision','ordinal','digest')}}}
        return run

    def test_nine_oclock_boundary_and_allocation(self):
        before = datetime(2026,10,9,10,29,tzinfo=challenges.TZ)
        self.assertEqual(challenges.slot(before).day, 8)
        self.assertEqual(challenges.slot(before+timedelta(minutes=1)).day, 9)
        equal = challenges.allocate({'a':0,'b':0})
        high = challenges.allocate({'a':90,'b':0})
        self.assertEqual(sum(high.values()),15)
        self.assertLess(high['a'],equal['a'])
        self.assertGreater(high['b'],high['a'])

    def test_unanswered_questions_can_return_in_next_day_recommendations(self):
        now = datetime.now(challenges.TZ)
        with patch('backend.pipeline.run_sample',side_effect=self.fake_pipeline):
            challenges.generate(now=now)
            first = challenges.board('local-owner',now)
            self.assertEqual(first['state'],'ready')
            self.assertEqual(len(first['questions']),15)
            self.assertTrue(all(q['kind']=='single_choice' and 'answer_index' not in q for q in first['questions']))
            count = self.counter
            challenges.generate(now=now)
            self.assertEqual(self.counter,count)
            tomorrow = now+timedelta(days=1)
            self.assertEqual(challenges.board('local-owner',tomorrow)['questions'],[])
            old_entries = len(bank.entries('local-owner'))
            self.assertGreaterEqual(old_entries,15)
            challenges.generate(now=tomorrow)
            second = challenges.board('local-owner',tomorrow)
            self.assertEqual(len(second['questions']),15)
            self.assertTrue({q['prompt'] for q in first['questions']}&{q['prompt'] for q in second['questions']})
            self.assertEqual(len({q['prompt'] for q in second['questions']}),15)
            self.assertGreater(len(bank.entries('local-owner')),old_entries)
            with patch.object(challenges,'slot',return_value=challenges.slot(tomorrow)):
                with self.assertRaises(ValueError):
                    practice.submit(first['questions'][0]['session_id'],'0','local-owner')

    def test_daily_and_review_share_source_mastery(self):
        now = datetime.now(challenges.TZ)
        with patch('backend.pipeline.run_sample',side_effect=self.fake_pipeline):
            challenges.generate(now=now)
        first = challenges.board('local-owner',now)['questions'][0]
        result = practice.submit(first['session_id'],'0','local-owner')
        self.assertTrue(result['counted'])
        self.assertGreater(bank.mastery('local-owner',first['course_id'])['score'],0)
        row = next(r for r in bank.entries('local-owner') if r['course_id']==first['course_id'])
        review = bank.start_review(row['run_id'],row['question_id'],'local-owner')
        other = practice.submit(review['session_id'],'0','local-owner')
        self.assertFalse(other['counted'])  # Same source anchor, same day.
        with connect(self.path) as db:
            db.execute("UPDATE mastery_events SET day='2000-01-01'")
        review = bank.start_review(row['run_id'],row['question_id'],'local-owner')
        retention = practice.submit(review['session_id'],'1','local-owner')
        self.assertTrue(retention['counted'])
        self.assertLess(retention['mastery'],result['mastery'])

    def test_no_filler_when_generation_repeats_history(self):
        q = sample_question()
        self.assertFalse(challenges.novel(dict(q,options=list(reversed(q['options']))),[q]))
        self.assertFalse(challenges.novel(dict(q,prompt=q['prompt']+' '),[q]))

    def test_failed_generation_reuses_bank_and_respects_retry_cooldown(self):
        old = {}
        for course in ('a','b'):
            old[course] = self.fake_pipeline(course_id=course,choice_count=3)
            bank.register(old[course],'local-owner')
        def repeat(**kwargs):
            return old[kwargs['course_id']]
        with patch('backend.pipeline.run_sample',side_effect=repeat) as model:
            challenges.generate()
            result = challenges.board('local-owner')
            self.assertEqual(result['state'],'partial')
            self.assertEqual(len(result['questions']),6)
            self.assertEqual(len(bank.entries('local-owner')),6)
            attempts = model.call_count
            challenges.generate()
            self.assertEqual(model.call_count,attempts)

    def test_deep_review_plan_mixes_types_without_exposing_answers(self):
        run = self.fake_pipeline(course_id='a',choice_count=3)
        q = self.packages[run]['questions'][-1]
        with connect(self.path) as db:
            cid=self.packages[run]['stable_concepts'][q['concept_id']]
            db.execute('UPDATE knowledge_angle_reviews SET short_eligible=1 WHERE concept_id=?',(cid,))
        q.update(kind='short_answer',options=[],answer_index=None,rubric=[
            {'id':'r1','point':'缓存机制','weight':100,'full':'说明减少回源',
             'partial':'只提缓存','zero':'遗漏或相反'}])
        bank.register(run,'local-owner')
        plan = bank.review_plan('a','local-owner',2)
        self.assertEqual({q['kind'] for q in plan['questions']},{'single_choice','short_answer'})
        self.assertTrue(all(set(q)=={'run_id','question_id','kind'} for q in plan['questions']))
        with self.assertRaises(LookupError):
            bank.review_plan('a','other',2)

    def test_quality_shortfall_reallocates_only_to_fresh_questions(self):
        def unavailable_course(**kwargs):
            if kwargs['course_id']=='a':
                raise SystemExit(1)
            return self.fake_pipeline(**kwargs)
        with patch('backend.pipeline.run_sample',side_effect=unavailable_course):
            challenges.generate()
        result = challenges.board('local-owner')
        self.assertEqual(result['state'],'ready')
        self.assertEqual(len(result['questions']),15)
        self.assertEqual(result['allocation'],{'a':0,'b':15})
        self.assertEqual(len({q['prompt'] for q in result['questions']}),15)

    def test_only_completed_courses_are_eligible(self):
        with connect(self.path) as db:
            db.execute("UPDATE material_learning SET learned=0 WHERE course_id='a'")
        self.assertEqual([r['id'] for r in challenges.courses('local-owner')],['b'])

    def test_reopened_course_rejects_existing_daily_session(self):
        with patch('backend.pipeline.run_sample',side_effect=self.fake_pipeline):challenges.generate()
        board=challenges.board('local-owner')
        q=next(q for q in board['questions'] if q['course_id']=='a')
        with connect(self.path) as db:db.execute("UPDATE material_learning SET learned=0 WHERE course_id='a'")
        with self.assertRaises(ValueError):practice.submit(q['session_id'],'0','local-owner')
        with self.assertRaises(ValueError):practice.reveal(q['session_id'],'local-owner')
        self.assertTrue(all(q['course_id']!='a' for q in challenges.board('local-owner')['questions']))

    def test_no_historical_backfill_and_before_deadline_waits(self):
        with patch('backend.pipeline.run_sample',side_effect=self.fake_pipeline) as model:
            morning=datetime(2026,10,20,8,tzinfo=challenges.TZ)
            challenges.generate(now=morning)
            model.assert_not_called()
            with connect(self.path) as db:
                challenges.initialize(db)
                self.assertEqual(db.execute('SELECT count(*) FROM challenge_batches').fetchone()[0],0)
            challenges.generate(now=morning.replace(hour=12))
            challenges.generate(now=morning.replace(day=25,hour=12))
            challenges.generate(now=morning.replace(day=25,hour=13))
        with connect(self.path) as db:
            dates=[r['slot'][:10] for r in db.execute('SELECT slot FROM challenge_batches ORDER BY slot')]
        self.assertEqual(dates,['2026-10-20','2026-10-25'])

    def test_exhausted_material_still_recommends_old_questions(self):
        for cid in ('a','b'):
            run=self.fake_pipeline(course_id=cid,choice_count=10);bank.register(run,'local-owner')
        with patch.object(challenges.policy,'materials',side_effect=lambda cid,owner:[{'learned':True,'parsed':True,'remaining':0}]),patch('backend.pipeline.run_sample') as model:
            challenges.generate()
            board=challenges.board('local-owner')
        model.assert_not_called()
        self.assertEqual(len(board['questions']),15)
        self.assertEqual(board['state'],'ready')

    def test_additional_round_is_bank_only_idempotent_and_excludes_last_round(self):
        for cid in ('a','b'):
            for i in range(2):
                run=self.fake_pipeline(course_id=cid,choice_count=10);bank.register(run,'local-owner')
        before=len(bank.entries('local-owner'))
        with patch('backend.pipeline.run_sample') as model:
            first=challenges.again('local-owner','request-first')
            repeat=challenges.again('local-owner','request-first')
            second=challenges.again('local-owner','request-second')
        model.assert_not_called();self.assertEqual(first,repeat)
        refs=lambda plan:{(q['run_id'],q['question_id']) for q in plan['questions']}
        self.assertEqual(len(first['questions']),15)
        self.assertFalse(refs(first)&refs(second))
        self.assertEqual(len(bank.entries('local-owner')),before)
        self.assertTrue(all(q['kind']=='single_choice' for q in first['questions']))
        self.assertEqual(challenges.again('other','request-other')['questions'],[])

    def test_additional_shortfall_never_repeats_within_round(self):
        run=self.fake_pipeline(course_id='a',choice_count=3);bank.register(run,'local-owner')
        first=challenges.again('local-owner','first')
        self.assertEqual(len(first['questions']),3)
        self.assertIn('只有3道',first['message'])
        self.assertEqual(challenges.again('local-owner','second')['questions'],[])

    def test_recommendation_prioritizes_unanswered_then_wrong_then_old_correct(self):
        run=self.fake_pipeline(course_id='a',choice_count=3);bank.register(run,'local-owner')
        first=bank.start_review(run,'q1','local-owner');practice.submit(first['session_id'],'0','local-owner')
        second=bank.start_review(run,'q2','local-owner');practice.submit(second['session_id'],'1','local-owner')
        with connect(self.path) as db:db.execute("UPDATE session_modes SET created_at='2000-01-01T00:00:00+00:00'")
        ranked=challenges.ranked_questions('local-owner','a',now=datetime.now(challenges.TZ)+timedelta(days=20))
        self.assertEqual([q['question_id'] for q in ranked],['q3','q2','q1'])

    def test_additional_api_requires_auth_and_valid_idempotency_key(self):
        import os
        from fastapi.testclient import TestClient
        from .main import app
        with patch.dict(os.environ,{'ZHIWEN_LOCAL_TOKEN':'local-test-token','ZHIWEN_SCHEDULER_ENABLED':'0'}),TestClient(app) as client,patch.object(challenges,'again',return_value={'questions':[]}) as again:
            self.assertEqual(client.post('/daily/again',json={'request_id':'request-1234567890'}).status_code,401)
            auth={'Authorization':'Bearer local-test-token'}
            self.assertEqual(client.post('/daily/again',json={'request_id':'bad'},headers=auth).status_code,422)
            self.assertEqual(client.post('/daily/again',json={'request_id':'request-1234567890'},headers=auth).status_code,200)
            again.assert_called_once_with('local-owner','request-1234567890')


class SpacingTests(unittest.TestCase):
    def test_correct_question_cooldown_uses_submission_not_session_creation(self):
        now=datetime(2026,10,15,12,tzinfo=challenges.TZ)
        rows=[dict(kind='single_choice',run_id='r',question_id=q,concept_key=q,created_at='') for q in ('correct','wrong','new')]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db.sqlite3'
            with connect(path) as db:
                practice.initialize(db)
                db.execute("CREATE TABLE session_modes(session_id TEXT PRIMARY KEY,mode TEXT,created_at TEXT)")
                for q,score in [('correct',100),('wrong',0)]:
                    db.execute('INSERT INTO practice_sessions(id,owner,run_id,question_id,result) VALUES(?,?,?,?,?)',(q,'o','r',q,__import__('json').dumps(dict(q=score,submitted_at='2026-10-14T12:00:00+08:00'))))
                    db.execute('INSERT INTO session_modes VALUES(?,?,?)',(q,'daily','2000-01-01T00:00:00+00:00'))
            with patch.object(challenges,'connect',lambda:connect(path)),patch.object(bank,'entries',return_value=rows):
                self.assertEqual([r['question_id'] for r in challenges.ranked_questions('o',now=now,due_only=True)],['new','wrong'])
                self.assertIn('correct',[r['question_id'] for r in challenges.ranked_questions('o',now=now+timedelta(days=3),due_only=True)])
