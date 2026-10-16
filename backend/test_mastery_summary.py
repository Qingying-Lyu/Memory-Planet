import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from . import bank,practice
from .store import connect
class MasterySummaryTests(unittest.TestCase):
 def test_untested_points_do_not_dilute_assessed_score(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'db.sqlite3'
   with connect(path) as db:
    practice.initialize(db)
    db.execute('INSERT INTO mastery_events VALUES(?,?,?,?,?,?,?,?,?)',('e','alice','k1','2026-10-14',100,None,80,1,'mastery-v2'))
   with patch.object(bank,'connect',lambda:connect(path)),patch.object(bank,'entries',return_value=[{'concept_key':'k1'},{'concept_key':'k2'},{'concept_key':'k3'}]),patch('backend.material_policy.materials',return_value=[]):
    result=bank.mastery('alice','course')
    self.assertEqual(result['score'],80);self.assertEqual(result['assessed_concepts'],1);self.assertEqual(result['known_concepts'],3)
    self.assertEqual(bank.mastery('bob','course')['score'],0)
 def test_existing_material_initialization_does_not_write_on_read(self):
  from . import material_policy,library
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'db.sqlite3'
   with connect(path) as db:
    db.execute('INSERT INTO courses VALUES(?,?,?)',('c','alice','课程'))
    material_policy.initialize(db)
    before=db.total_changes
    material_policy.initialize(db)
    self.assertEqual(db.total_changes,before)
if __name__=='__main__':unittest.main()
