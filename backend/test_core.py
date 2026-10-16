import tempfile
import unittest
from pathlib import Path
import json
import time
from unittest.mock import patch

from . import library, daily, practice, wps
from .store import connect
from .test_practice import sample_question


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'test.sqlite3'
        self.patches = [patch.object(module, 'connect', lambda: connect(self.db))
                        for module in (library, daily, practice, wps)]
        self.patches.append(patch.object(library, 'LIBRARY', self.root / 'library'))
        for item in self.patches:
            item.start()
        with connect(self.db) as db:
            db.execute('INSERT INTO courses VALUES(?,?,?)', ('test', 'alice', '测试'))

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_scope_approval_and_revision(self):
        with self.assertRaises(ValueError):
            library.set_scope('test', 'alice', 'personal', ['缓存'])
        library.set_scope('test', 'alice', 'personal', ['缓存'], '笔记只涉及缓存命中')
        result = library.ingest('test', 'alice', '资料', 'https://example.com',
                                [(1, 'text', '缓存可以减少回源请求'), (2, 'text', '路由协议扩展知识')], '文字')
        self.assertTrue(Path(result['markdown']).exists())
        self.assertEqual(library.evidence('test', 'alice'), [])
        with self.assertRaises(ValueError):
            library.approve(result['document_id'], 'alice', {2: '路由'})
        library.approve(result['document_id'], 'alice', {1: '缓存'})
        self.assertEqual(len(library.evidence('test', 'alice')), 1)
        with self.assertRaises(LookupError):
            library.evidence('test', 'bob')
        self.assertEqual(library.answer('test', 'alice', '缓存作用')['status'], 'evidence_found')
        self.assertEqual(library.answer('test', 'alice', '路由协议')['status'], 'insufficient_evidence')
        library.set_scope('test', 'alice', 'personal', ['回源'], '笔记范围变更')
        self.assertEqual(library.evidence('test', 'alice'), [])
        with self.assertRaises(ValueError):
            library.approve(result['document_id'], 'alice', {1: '回源'})

    def test_answer_rejects_changed_scope_and_fabricated_citations(self):
        from unittest.mock import Mock
        library.set_scope('test', 'alice', 'personal', ['缓存'], '已学缓存')
        doc = library.ingest('test', 'alice', '笔记', 'local',
                             [(1, 'text', '缓存可以减少回源请求')], '文字')
        library.approve(doc['document_id'], 'alice', {1: '缓存'})
        generator, reviewer = Mock(), Mock()
        answer = {'sufficient': True, 'answer': '缓存减少回源。', 'steps': [],
                  'citations': [{'document_id': doc['document_id'], 'ordinal': 1,
                                 'quote': '缓存可以减少回源请求'}]}
        generator.json.return_value = (answer, {})
        def change_scope(*args):
            library.set_scope('test', 'alice', 'personal', ['路由'], '现在只学路由')
            return {'approved': True}, {}
        reviewer.json.side_effect = change_scope
        with patch('backend.settings.configure'), patch('backend.cloud.model_target',
                side_effect=[(generator, 'generate'), (reviewer, 'review')]):
            with self.assertRaises(LookupError):
                library.explain('test', 'alice', '缓存作用')
        library.set_scope('test', 'alice', 'personal', ['缓存'], '已学缓存')
        library.approve(doc['document_id'], 'alice', {1: '缓存'})
        generator.json.return_value = ({**answer, 'citations': [None]}, {})
        with patch('backend.settings.configure'), patch('backend.cloud.model_target', return_value=(generator, 'generate')):
            with self.assertRaises(ValueError):
                library.explain('test', 'alice', '缓存作用')

    def test_daily_idempotence_and_answer_privacy(self):
        q = sample_question()
        data = {'course_id': 'test', 'questions': [q, dict(q, id='q2', kind='short_answer')]}
        with patch.object(practice, 'package', return_value=data):
            first = daily.select('run', 'alice')
            second = daily.select('run', 'alice')
            self.assertEqual(first, second)
            self.assertNotIn('answer_index', first)
            self.assertEqual(first['kind'], 'single_choice')
            practice.submit(first['session_id'], '0', 'alice')
            with connect(self.db) as db:
                db.execute('DELETE FROM daily_challenges')
            self.assertEqual(daily.select('run', 'alice')['status'], 'no_due_question')

    def test_daily_uses_unseen_question_from_other_batch(self):
        q = sample_question()
        first = {'course_id': 'test', 'questions': [q]}
        second = {'course_id': 'test', 'questions': [dict(q, id='q2', prompt='第二批课程中的未见过选择题场景') ]}
        with connect(self.db) as db:
            practice.initialize(db)
            db.execute('INSERT INTO practice_sessions(id,owner,run_id,question_id,revealed) VALUES(?,?,?,?,1)',
                       ('seen', 'alice', 'first', q['id']))
        result = daily.select_packages([('first', first), ('second', second)], 'alice')
        self.assertEqual(result['prompt'], second['questions'][0]['prompt'])
        self.assertNotIn('answer_index', result)
        with connect(self.db) as db:
            row = db.execute('SELECT run_id,question_id FROM practice_sessions WHERE id=?',
                             (result['session_id'],)).fetchone()
        self.assertEqual(tuple(row), ('second', 'q2'))

    def test_wps_binding_privacy_and_replay(self):
        bindings = {'42': {'owner': 'alice', 'course_id': 'test', 'audience': 'shared_training'}}
        event = {'robot_key': 'test-key', 'chatid': 7, 'creator': 42,
                 'ctime': int(time.time()), 'content': '@机器人 缓存的作用'}
        with patch.object(wps, 'webhook', return_value='https://xz.wps.cn/api/v1/webhook/send?key=test-key'), \
                patch.dict('os.environ', {'ZHIWEN_WPS_CHAT_ID': '7', 'ZHIWEN_WPS_BINDINGS_FILE': 'test'}), \
                patch.object(wps, 'read_secret_file', side_effect=lambda _: json.dumps(bindings)):
            self.assertEqual(wps.enqueue(event), {'result': 'ok'})
            wps.enqueue(event)
            with connect(self.db) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM wps_inbox').fetchone()[0], 1)
            with self.assertRaises(PermissionError):
                wps.enqueue(dict(event, creator=99))
            with self.assertRaises(PermissionError):
                wps.enqueue(dict(event, robot_key='forged'))
            with self.assertRaises(PermissionError):
                wps.enqueue(dict(event, ctime=1))
            bindings['42']['audience'] = 'personal'
            with self.assertRaises(PermissionError):
                wps.enqueue(event)


if __name__ == '__main__':
    unittest.main()
