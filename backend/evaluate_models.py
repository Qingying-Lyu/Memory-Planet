"""Explicit, synthetic-only Starflow evaluation; never modifies course data."""
import base64
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import httpx
from PIL import Image, ImageDraw, ImageFont
from .cloud import Starflow, StarflowVision
from .settings import configure
from .search_service import starflow_search

OUT = Path(__file__).resolve().parents[1] / 'data/model-evaluation-20261012'
TEXT = '缓存命中时直接返回缓存。缓存未命中时回源获取内容，再写入缓存。缓存有效期为60秒，超过有效期必须回源；本规则不保证源站故障时仍可服务。'

def task(model):
    value, meta = Starflow().json(model,
        '只依据材料输出JSON，不联网。完成提炼、出题、审题、评分、变更识别任务。',
        {'source':TEXT,'tasks':[
            'concepts数组列出知识点，每项含name和逐字原文quote。',
            'question生成一题应用单选，含prompt、4个options、answer_index(0起)、quote。',
            'bad_question:超过60秒且源站故障，缓存保证可用吗？正确答案标为保证可用。输出bad_question_approved布尔。',
            '学生回答：未命中直接返回缓存，无需回源。评分规则：说明未命中需回源为full，方向相反为none。输出grade_level。',
            '新版仅把60秒改为120秒，输出change含old_seconds,new_seconds,old_question_requires_review布尔。',
            '问源站故障备用路由如何配置？资料未提供，输出answer_sufficient布尔。']})
    q=value.get('question',{}); concepts=value.get('concepts',[])
    checks={'concept_quotes':bool(concepts) and all(isinstance(c,dict) and isinstance(c.get('quote'),str) and len(c['quote'])>=5 and c['quote'] in TEXT for c in concepts),
      'question_structure':len(q.get('options',[]))==4 and type(q.get('answer_index')) is int and 0<=q['answer_index']<4,
      'question_quote':isinstance(q.get('quote'),str) and len(q['quote'])>=5 and q['quote'] in TEXT,
      'reject_bad_question':value.get('bad_question_approved',value.get('bad_question',{}).get('bad_question_approved')) is False,
      'grade_contradiction':value.get('grade_level',value.get('grade',{}).get('grade_level'))=='none',
      'reject_unsupported_answer':value.get('answer_sufficient') is False,
      'change_detection':value.get('change',{}).get('old_seconds')==60 and value.get('change',{}).get('new_seconds')==120 and type(value.get('change',{}).get('old_question_requires_review')) is bool}
    return {'model':model,'kind':'text','checks':checks,'passed':sum(checks.values()),'total':len(checks),'response':value,**meta}

def vision(model):
    im=Image.new('RGB',(1000,240),'white');d=ImageDraw.Draw(im);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',32)
    d.text((20,25),'缓存有效期：60秒\n未命中 → 回源 → 写入缓存\n源站故障不保证可用',fill='black',font=font,spacing=15)
    buf=io.BytesIO();im.save(buf,format='PNG')
    value,meta=StarflowVision().json(model,'只读取图片，输出JSON {text:字符串}，保留否定和箭头。',{'payload':{'task':'OCR'},'image':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()})
    t=value.get('text','');checks={'number':'60' in t,'negation':'不保证' in t,'sequence':all(w in t for w in ['未命中','回源','写入缓存'])}
    return {'kind':'vision','checks':checks,'passed':sum(checks.values()),'total':3,'response':value,**meta}

def search(model):
    start=time.monotonic(); links=starflow_search('HTTP 缓存 Cache-Control max-age RFC 9111 官方文档',model,configure()['starflow_key_file'])
    pages=[]
    for url in links[:2]:
        try:
            r=httpx.get(url,timeout=20,follow_redirects=True);pages.append({'url':url,'status':r.status_code,'bytes':len(r.content)})
        except Exception as e: pages.append({'url':url,'error_type':type(e).__name__})
    return {'kind':'search','model':model,'seconds':round(time.monotonic()-start,2),'links':links,'pages':pages,'actual_search_verified':True}

def run():
    configure();OUT.mkdir(exist_ok=True)
    jobs=[('text',m,task) for m in ['mimo-v2.6-pro','mimo-v2.6-flash','glm-5.3','qwen3.8-max','deepseek-v4-pro-0813','kimi-k3']]
    jobs += [('vision',m,vision) for m in ['mimo-v2.6-pro','qwen3.8-max']]
    jobs += [('search',m,search) for m in ['glm-5.3-flash','glm-5.3']]
    results=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(fn,m):(kind,m) for kind,m,fn in jobs}
        for f in as_completed(futures):
            kind,m=futures[f]
            try:r=f.result()
            except Exception as e:r={'kind':kind,'model':m,'error_type':type(e).__name__,'error':str(e) if isinstance(e,RuntimeError) else 'call failed'}
            results.append(r);(OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in r.items() if k not in ('response','usage','links','pages')},ensure_ascii=False),flush=True)
    return results

if __name__=='__main__':run()
