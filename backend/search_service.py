"""Search adapters, local result cache and per-provider circuit breakers."""
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urlsplit, urljoin, parse_qs
from bs4 import BeautifulSoup
import httpx
from .store import ROOT
from .settings import load
from .cloud import read_secret_file

CACHE=ROOT/'data/research-cache'
HEALTH=ROOT/'data/search-health.json'

def health():
    try:return json.loads(HEALTH.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def record(provider,state):
    data=health();data[provider]={'state':state,'at':time.time()}
    HEALTH.parent.mkdir(parents=True,exist_ok=True)
    temporary=HEALTH.with_suffix('.tmp');temporary.write_text(json.dumps(data),encoding='utf-8');temporary.replace(HEALTH)

def failure(exc):
    if isinstance(exc,httpx.TimeoutException):return 'timeout'
    if isinstance(exc,httpx.HTTPStatusError):return 'http_'+str(exc.response.status_code)
    if isinstance(exc,OSError) or 'getaddrinfo' in str(exc):return 'dns_failed'
    if isinstance(exc,httpx.ConnectError):return 'connection_failed'
    return 'unavailable_or_no_results'

def urls(values):
    return list(dict.fromkeys(v for v in values if isinstance(v,str) and urlsplit(v).scheme=='https' and urlsplit(v).hostname))[:5]

def api_search(provider,query,key_file):
    key=read_secret_file(key_file)
    # Separate search key, sent only to its documented API. No redirects.
    with httpx.Client(timeout=15,follow_redirects=False) as client:
        if provider=='brave':
            response=client.get('https://api.search.brave.com/res/v1/web/search',params={'q':query,'count':5},headers={'X-Subscription-Token':key,'Accept':'application/json'})
        elif provider=='tavily':
            response=client.post('https://api.tavily.com/search',headers={'Authorization':'Bearer '+key},json={'query':query,'max_results':5,'include_answer':False,'include_raw_content':False})
        else:raise ValueError('未知搜索服务')
        response.raise_for_status();data=response.json()
    rows=data.get('web',{}).get('results',[]) if provider=='brave' else data.get('results',[])
    return urls(r.get('url') for r in rows)

def starflow_search(query,model,key_file):
    """Search public topic keywords only; accept links only after billed search proof."""
    key=read_secret_file(key_file)
    body={'model':model,'messages':[
        {'role':'system','content':'你是公开资料检索助手。必须实际联网搜索，只寻找相关原始官方文档、大学教材或论文；不要扩展查询主题，不猜测URL。返回JSON {"sources":[{"title":"标题","url":"完整HTTPS来源链接"}]}，最多5条。无法搜索则返回空列表。'},
        {'role':'user','content':query}],
        'tools':[{'type':'web_search'}],'response_format':{'type':'json_object'},
        'max_tokens':8000,'stream':False}
    with httpx.Client(timeout=60,follow_redirects=False) as client:
        response=client.post('https://kspmas.ksyun.com/v1/chat/completions',headers={'Authorization':'Bearer '+key},json=body)
        response.raise_for_status();data=response.json()
    if data.get('usage',{}).get('web_search_queries',0)<1:
        raise ValueError('模型未执行联网搜索，拒绝将其回答当作搜索结果')
    choice=data['choices'][0]
    if choice.get('finish_reason')=='length':
        raise ValueError('联网检索输出被截断，稍后重试')
    content=choice['message'].get('content') or ''
    parsed=json.loads(content)
    return urls(r.get('url') for r in parsed.get('sources',[]) if isinstance(r,dict))


def search(query,fetch):
    settings=load();provider=settings.get('search_provider','public')
    key_file=settings.get('starflow_key_file','') if provider=='starflow' else settings.get('search_key_file','')
    models=settings.get('search_models',['glm-5.3-flash'])
    key=hashlib.sha256((provider+'\n'+key_file+'\n'+json.dumps(models)+'\n'+query).encode()).hexdigest()
    CACHE.mkdir(parents=True,exist_ok=True);cached=CACHE/(key+'.json')
    try:
        saved=json.loads(cached.read_text(encoding='utf-8'))
        if saved['expires']>time.time():return saved['urls']
    except (OSError,ValueError,KeyError):pass
    attempts=[]
    if provider=='starflow':
        if key_file:
            for model in models:
                attempts.append(('starflow:'+model,lambda model=model:starflow_search(query,model,key_file)))
        else:record('starflow','missing_key_file')
    if provider in ('brave','tavily'):
        if key_file:attempts.append((provider,lambda:api_search(provider,query,key_file)))
        else:record(provider,'missing_key_file')
    def bing(base):
        _,raw=fetch(base+'?'+urlencode({'q':query,'format':'rss'}))
        return urls(r.findtext('link') for r in ET.fromstring(raw).findall('.//item'))
    def duck():
        _,raw=fetch('https://html.duckduckgo.com/html/?'+urlencode({'q':query}))
        found=[]
        for a in BeautifulSoup(raw,'html.parser').select('a.result__a'):
            url=urljoin('https://html.duckduckgo.com',a.get('href',''))
            found.append(parse_qs(urlsplit(url).query).get('uddg',[url])[0])
        return urls(found)
    attempts += [('bing',lambda:bing('https://www.bing.com/search')),('bing-cn',lambda:bing('https://cn.bing.com/search')),('duckduckgo',duck)]
    result=[]
    for name,call in attempts:
        previous=health().get(name,{})
        if previous.get('state')!='ok' and time.time()-previous.get('at',0)<300:continue
        try:
            result=call()
            if result:record(name,'ok');break
            record(name,'no_results_or_challenge')
        except Exception as exc:record(name,failure(exc))
    saved={'urls':result,'expires':time.time()+(86400 if result else 900)}
    temp=cached.with_suffix('.tmp');temp.write_text(json.dumps(saved),encoding='utf-8');temp.replace(cached)
    return result
