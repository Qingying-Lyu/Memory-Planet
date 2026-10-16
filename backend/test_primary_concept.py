import unittest
from .pipeline import validate_primary_concept
from .course_schema import Question
from .test_practice import sample_question
class PrimaryConceptTests(unittest.TestCase):
 def test_compound_id_repaired_without_changing_question(self):
  q=Question.model_validate(sample_question());q.concept_id='c1;c2'
  fixed=validate_primary_concept(q,{'c1','c2'},lambda draft,allowed:{**draft,'concept_id':'c2'})
  self.assertEqual(fixed.concept_id,'c2');self.assertEqual(fixed.prompt,q.prompt)
 def test_invalid_id_or_content_change_rejected(self):
  q=Question.model_validate(sample_question());q.concept_id='c1;c2'
  for key,value in [('concept_id','c99'),('prompt','完全不同的题目内容，这个修改必须拒绝')]:
   with self.assertRaises(ValueError):validate_primary_concept(q,{'c1','c2'},lambda draft,allowed:{**draft,key:value})
 def test_valid_id_needs_no_repair(self):
  q=Question.model_validate(sample_question())
  self.assertIs(validate_primary_concept(q,{q.concept_id},lambda *_:self.fail('无需调用模型')),q)
if __name__=='__main__':unittest.main()
