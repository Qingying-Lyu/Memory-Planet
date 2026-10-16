import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import practice
from .store import connect
from .course_schema import Question, Grade, rubric_score, validate_evidence, Evidence


def sample_question():
    return {'id':'q1','concept_id':'c1','kind':'single_choice',
            'prompt':'客户要求降低源站带宽占用，应优先检查什么？',
            'options':['缓存命中率','显示器','鼠标','键盘'],'answer_index':0,
            'reference_answer':'缓存命中率','explanation':'缓存减少回源',
            'evidence':[{'block':1,'quote':'缓存可以减少回源请求'}],'rubric':[]}


class PracticeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.sqlite3'
        self.question=sample_question()
        self.data={'course_id':'test','questions':[self.question]}
        self.patches=[patch.object(practice,'connect',lambda:connect(self.path)),
                      patch.object(practice,'package',lambda run,owner:self.data)]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def test_first_score_idempotence_and_daily_limit(self):
        session=practice.start('run','q1','alice')['session_id']
        result=practice.submit(session,'0','alice')
        self.assertEqual(result['mastery'],85)
        self.assertTrue(result['counted'])
        self.assertEqual(practice.submit(session,'0','alice'),result)
        with self.assertRaises(ValueError):practice.submit(session,'1','alice')
        other=practice.start('run','q1','alice')['session_id']
        self.assertFalse(practice.submit(other,'0','alice')['counted'])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM mastery_events').fetchone()[0],1)

    def test_reveal_and_owner_isolation(self):
        session=practice.start('run','q1','alice')['session_id']
        with self.assertRaises(LookupError):practice.submit(session,'0','bob')
        practice.reveal(session,'alice')
        result=practice.submit(session,'0','alice')
        self.assertFalse(result['counted'])
        self.assertEqual(result['q'],100)
        fresh=practice.start('run','q1','alice')['session_id']
        self.assertFalse(practice.submit(fresh,'0','alice')['counted'])

    def test_parallel_open_cannot_bypass_reveal(self):
        first=practice.start('run','q1','alice')['session_id']
        second=practice.start('run','q1','alice')['session_id']
        practice.reveal(first,'alice')
        self.assertFalse(practice.submit(second,'0','alice')['counted'])

    def test_evidence_and_rubric_validation(self):
        with self.assertRaises(ValueError):validate_evidence([Evidence(block=1,quote='不存在的证据')],{1:'原始内容'})
        q=self.question.copy()
        q.update(kind='short_answer',options=[],answer_index=None,rubric=[
            {'id':'r1','point':'缓存','weight':100,'full':'说明减少回源','partial':'只提缓存','zero':'遗漏或语义相反'}])
        q=Question.model_validate(q)
        grade=Grade(items=[{'id':'r1','level':'partial','answer_quote':'缓存','reason':'缺少机制'}])
        self.assertEqual(rubric_score(q,grade,'使用缓存'),50)
        with self.assertRaises(ValueError):rubric_score(q,grade,'不知道')

    def test_short_answer_disagreement_does_not_score(self):
        self.question.update(kind='short_answer',options=[],answer_index=None,rubric=[
            {'id':'r1','point':'缓存','weight':100,'full':'说明减少回源','partial':'只提缓存','zero':'遗漏'}])
        class Fake:
            def json(self,model,system,data):
                return {'items':[{'id':'r1','level':'full' if model.startswith('qwen') else 'partial',
                                   'answer_quote':'缓存','reason':'测试差异'}]}, {'model':model}
        with patch.object(practice,'Starflow',Fake),patch.dict('os.environ',{'ZHIWEN_GRADE_MODEL':'qwen3.8-max','ZHIWEN_GRADE_REVIEW_MODEL':'glm-5.3'}):
            session=practice.start('run','q1','alice')['session_id']
            result=practice.submit(session,'使用缓存','alice')
        self.assertEqual(result['status'],'pending_review')
        self.assertIsNone(result['q'])
        self.assertFalse(result['counted'])


if __name__=='__main__':unittest.main()
