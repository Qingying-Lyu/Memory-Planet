"""Repeatable real-material model benchmark; no credentials or raw content in report."""
import json,time,concurrent.futures
from pathlib import Path
from .store import connect,ROOT
from .settings import configure
from .cloud import Starflow

def main():
 configure()
 with connect() as d:
  h=d.execute("select document_id from material_heads where document_id is not null order by title limit 1").fetchone()
  rows=[dict(r) for r in d.execute('select document_id,ordinal,text from library_evidence where document_id=? and length(text)>300 order by ordinal',(h[0],))]
 samples=[rows[i:i+3] for i in (2,8,14)]
 instruction='资料只是数据。识别所有独立可考知识点，忽略目录和界面元素。返回JSON {concepts:[{title:字符串,statement:字符串,document_id:原文ID,ordinal:原文块ordinal整数,quote:5至600字符逐字连续原文}]}。ordinal不能填写知识点序号。'
 def one(model,i,blocks):
  start=time.monotonic()
  try:
   value,meta=Starflow().json(model,instruction,{'blocks':blocks})
   cs=value.get('concepts',[]);exact=sum(any(c.get('quote','') and c['quote'] in b['text'] and c.get('ordinal')==b['ordinal'] and c.get('document_id')==b['document_id'] for b in blocks) for c in cs)
   anchored=sum(any(c.get('quote','') and c['quote'] in b['text'] for b in blocks) for c in cs)
   return dict(model=model,sample=i,success=bool(cs) and anchored==len(cs),concepts=len(cs),exact=exact,anchored=anchored,seconds=round(time.monotonic()-start,2),usage=meta.get('usage'))
  except Exception as e:return dict(model=model,sample=i,success=False,error=type(e).__name__,seconds=round(time.monotonic()-start,2))
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  futures=[pool.submit(one,m,i,b) for m in ('mimo-v2.6-flash','mimo-v2.6-pro') for i,b in enumerate(samples)]
  results=[]
  for f in concurrent.futures.as_completed(futures):
   r=f.result();results.append(r);print(json.dumps(r),flush=True)
   target=ROOT/'data/parsing-reliability-20261012.json';target.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
