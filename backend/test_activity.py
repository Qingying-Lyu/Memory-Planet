import json,tempfile,unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from . import activity,practice,challenges
from .store import connect

class ActivityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite3'
  self.p=patch.object(activity,'connect',lambda:connect(self.path));self.p.start()
  with connect(self.path) as db:practice.initialize(db);challenges.initialize(db)
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def daily(self,state='ready',complete=True):
  with connect(self.path) as db:
   db.execute('INSERT INTO challenge_batches VALUES(?,?,?,?,?,?,?)',('b','alice','2026-10-12',15,state,'{}',None))
   for i in range(2):
    result=json.dumps({'submitted_at':'2026-10-12T17:00:00+00:00'}) if complete or i==0 else None
    db.execute('INSERT INTO practice_sessions VALUES(?,?,?,?,?,?,?)',(str(i),'alice','run',str(i),0,'A',result))
    db.execute('INSERT INTO session_modes VALUES(?,?,?)',(str(i),'daily','2026-10-12T10:30:00+08:00'))
    db.execute('INSERT INTO challenge_items VALUES(?,?,?,?,?,?)',('b',i,'run',str(i),str(i),str(i)))
 def test_daily_uses_actual_beijing_completion_day_and_partial_not_lit(self):
  self.daily(state='partial')
  self.assertEqual(activity.history('alice',date(2026,10,13))['completed_days'],0)
  with connect(self.path) as db:db.execute("UPDATE challenge_batches SET state='ready'")
  days=activity.history('alice',date(2026,10,13))['days']
  self.assertEqual(days[-1]['level'],1);self.assertEqual(days[-2]['level'],0)
 def test_unfinished_daily_stays_gray(self):
  self.daily(complete=False)
  self.assertEqual(activity.history('alice',date(2026,10,13))['completed_days'],0)
 def test_completed_review_and_daily_bright_and_owner_isolated(self):
  self.daily();rid=activity.register('alice',[{'run_id':'r','question_id':'q'}])
  with connect(self.path) as db:
   db.execute('INSERT INTO practice_sessions VALUES(?,?,?,?,?,?,?)',('s','alice','r','q',0,'A','{}'))
   db.execute('INSERT INTO session_modes VALUES(?,?,?)',('s','review','2026-10-13T01:00:00+08:00'))
  with self.assertRaises(LookupError):activity.complete('bob',rid,['s'])
  activity.complete('alice',rid,['s']);activity.complete('alice',rid,['s'])
  with connect(self.path) as db:db.execute('UPDATE review_activity_rounds SET completed_at=?',('2026-10-13T01:00:00+08:00',))
  result=activity.history('alice',date(2026,10,13));self.assertEqual(result['days'][-1]['level'],2);self.assertEqual(result['days'][-1]['review'],1)
  self.assertEqual(activity.history('bob',date(2026,10,13))['completed_days'],0)
 def test_review_only_gray_and_incomplete_round_rejected(self):
  rid=activity.register('alice',[{'run_id':'r','question_id':'q'}])
  with self.assertRaises(ValueError):activity.complete('alice',rid,[])
  with connect(self.path) as db:db.execute('UPDATE review_activity_rounds SET completed_at=?',('2026-10-13T01:00:00+08:00',))
  day=activity.history('alice',date(2026,10,13))['days'][-1]
  self.assertEqual(day['level'],0);self.assertEqual(day['review'],1)
if __name__=='__main__':unittest.main()
