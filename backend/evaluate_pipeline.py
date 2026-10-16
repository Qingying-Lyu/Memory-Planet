"""Real model end-to-end generation in an isolated SQLite snapshot."""
import json,sqlite3,tempfile,time,shutil
from pathlib import Path
from unittest.mock import patch
from .store import ROOT,connect
from . import pipeline,knowledge,library,learning,material_policy as policy,bank,practice,material_versions

def run():
 out=ROOT/'data/generation-evaluation-20261015-pipeline';out.mkdir(exist_ok=True);results=[]
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);dbpath=root/'learning.sqlite3'
  from contextlib import closing
  with closing(sqlite3.connect('file:'+str(ROOT/'data/learning.sqlite3').replace('\\','/')+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(dbpath)) as dst:src.backup(dst)
  patches=[patch.object(m,'connect',lambda:connect(dbpath)) for m in (pipeline,knowledge,library,learning,policy,bank,practice,material_versions)]
  patches += [patch.object(m,'ROOT',root) for m in (pipeline,bank,practice)]
  for p in patches:p.start()
  try:
   for mid,label in [('115b1f7c7d63c715827a682f78628bfc','PPT'),('f9125ddd0570678d86cd8204915cf472','mindmap'),('bfbddec040f10ebfec331312ef3ffbb8','note')]:
    with connect(dbpath) as db:
     h=db.execute('SELECT * FROM material_heads WHERE id=?',(mid,)).fetchone();cid=h['course_id']
    policy.set_learned(cid,mid,'local-owner')
    for attempt in range(2):
     started=time.monotonic();row={'material_type':label,'attempt':attempt+1}
     try:
      runid=pipeline.run_sample(course_id=cid,material_id=mid,choice_count=2,previous_questions=[])
      package=json.loads((root/'data/runs'/runid/'package.json').read_text(encoding='utf-8'))
      admitted=bank.register(runid,'local-owner');row.update(accepted=len(package['questions']),admitted=admitted,held=len(package['held']))
      target=out/(label+'-'+str(attempt+1));shutil.copytree(root/'data/runs'/runid,target,dirs_exist_ok=True)
     except (Exception,SystemExit) as e:row.update(accepted=0,admitted=0,error_type=type(e).__name__)
     row['seconds']=round(time.monotonic()-started,2);results.append(row);print(json.dumps(row),flush=True)
     (out/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
     if row.get('admitted',0)>0:break
  finally:
   for p in reversed(patches):p.stop()
if __name__=='__main__':run()
