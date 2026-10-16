"""Read-only real-material benchmark; outputs never enter the user's bank."""
import json,time,sqlite3,os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from .store import ROOT
from .cloud import ParsingStarflow
from .settings import configure
from .course_schema import Question

def run():
 configure();out=ROOT/'data/generation-evaluation-20261015-phase3';out.mkdir(exist_ok=True)
 with sqlite3.connect('file:'+str(ROOT/'data/learning.sqlite3').replace('\\','/')+'?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row;fixtures=[]
  for mid,label in [('115b1f7c7d63c715827a682f78628bfc','PPT'),('f9125ddd0570678d86cd8204915cf472','mindmap'),('bfbddec040f10ebfec331312ef3ffbb8','note')]:
   rows=db.execute('SELECT c.title,c.statement,e.quote FROM knowledge_concepts c JOIN knowledge_evidence e ON e.concept_id=c.id JOIN material_heads h ON h.id=e.material_id AND h.document_id=e.document_id WHERE h.id=? ORDER BY c.short_cap DESC,length(e.quote) DESC LIMIT 8',(mid,)).fetchall()
   fixtures.append((label,[{'id':'c'+str(i+1),'title':r['title'],'summary':r['statement'],'evidence':[{'block':i+1,'quote':r['quote']}]} for i,r in enumerate(rows)]))
 def test(label,concepts,model):
  started=time.monotonic();result={'material_type':label,'model':model};client=ParsingStarflow();client.output_tokens=6000;client.read_timeout=120;client.transport_attempts=1
  blocks={i+1:c['evidence'][0]['quote'] for i,c in enumerate(concepts)}
  try:
   raw,meta=client.json(model,'只依据原文出1道需要应用判断且条件充分的场景单选题，4个选项唯一答案，不能补充资料外事实。优先选择有步骤、机制或限制的知识点，题干给出两个明确且影响决策的约束，用原文比较方案或排障顺序。禁止把名词定义或公式抄写包装成场景题；若需要数学推理，场景需比较实际决策结果，不只代入或抄写公式。concept_id必须是给定的单个编号，不得拼接。输出实际JSON {questions:[题目]}，每题字段严格符合schema。选择题kind=single_choice，options恰好4个不同字符串，answer_index为0到3的整数，rubric必须为空数组[]。id为q1，evidence的quote逐字连续摘自对应block。reference_answer不超过80字，explanation不超过120字。',{'framework':{'concepts':concepts},'blocks':blocks,'schema':Question.model_json_schema()})
   q=Question.model_validate(raw['questions'][0]);assert q.kind=='single_choice';assert q.concept_id in {c['id'] for c in concepts}
   assert all(e.block in blocks and e.quote in blocks[e.block] for e in q.evidence)
   review,_=client.json('glm-5.3','独立作答并审题，不知道作者答案。仅根据原文判断是否有唯一答案、忠于原文且需应用推理。输出JSON {answer_index:0到3的整数,grounded:布尔,unambiguous:布尔,challenging:布尔,reason:字符串}。不放宽要求。',{'question':q.model_dump(exclude={'answer_index','reference_answer','explanation','evidence','rubric'}),'blocks':blocks})
   result.update(structure_pass=True,review_pass=all(review.get(k)is True for k in ('grounded','unambiguous','challenging')) and review.get('answer_index')==q.answer_index,generation_seconds=meta['seconds'],review=review)
   (out/(label+'-'+model+'.json')).write_text(json.dumps({'result':result,'question':q.model_dump()},ensure_ascii=False,indent=2),encoding='utf-8')
  except Exception as e:
   result.update(structure_pass=False,review_pass=False,error_type=type(e).__name__)
   if hasattr(e,'errors'):result['validation_errors']=[{k:x[k] for k in ('type','loc','msg')} for x in e.errors(include_input=False,include_url=False)]
   (out/(label+'-'+model+'-failure.json')).write_text(json.dumps({'result':result,'raw':locals().get('raw')},ensure_ascii=False,indent=2),encoding='utf-8')
  result['seconds']=round(time.monotonic()-started,2);return result
 results=[]
 with ThreadPoolExecutor(max_workers=3) as pool:
  jobs=[pool.submit(test,label,concepts,model) for label,concepts in fixtures if label in os.environ.get('EVAL_MATERIALS','PPT,mindmap,note').split(',') for model in ('mimo-v2.6-pro','glm-5.3-flash')]
  for job in as_completed(jobs):
   result=job.result();results.append(result);print(json.dumps({k:v for k,v in result.items() if k!='review'}),flush=True)
   (out/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':run()
