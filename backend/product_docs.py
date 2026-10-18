from .browser_runtime import launch as launch_browser
"""Bounded Ksyun product-directory capture; no course data mutations."""
import hashlib,json,re,threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin,urlsplit,parse_qs
from bs4 import BeautifulSoup
from .enrichment import _fetch
from .document_policy import LEARNING_POLICY,api_reference,learning_text

class EmptyVideoSource(ValueError):
    pass

def supports(url):
    p=urlsplit(url)
    return p.scheme=='https' and p.netloc=='docs.ksyun.com' and bool(re.fullmatch(r'/products/\d+/?',p.path))

def directory(raw,root,allow_empty=False):
    soup=BeautifulSoup(raw,'html.parser');nav=soup.select_one('.ks-doc-left-public-nav .list-sec')
    if not nav:raise ValueError('未找到产品文档目录，未将首页当作完整材料')
    entries={}
    for a in nav.select('a[href]'):
        url=urljoin(root,a['href']);p=urlsplit(url)
        if p.scheme=='https' and p.netloc=='docs.ksyun.com' and re.fullmatch(r'/documents/\d+/?',p.path):
            trail=[]
            for parent in a.parents:
                if parent is nav:break
                if parent.name=='li':
                    header=parent.find(['h2','h3','h4'],recursive=False)
                    if header:trail.append(header.get_text(' ',strip=True))
            entries.setdefault(p.path.rstrip('/'),{'title':a.get_text(' ',strip=True),'url':url,'path':list(reversed(trail))})
    if (not entries and not allow_empty) or len(entries)>500:raise ValueError('产品目录为空或超过500篇，未截断采集')
    return list(entries.values())

def expand_directory(raw,root,fetcher):
    """Expand unloaded category branches, while inline nested leaves need no extra hop."""
    entries={urlsplit(e['url']).path:e for e in directory(raw,root,allow_empty=True)}
    queue=[raw];seen=set()
    while queue:
        page=queue.pop(0);soup=BeautifulSoup(page,'html.parser');nav=soup.select_one('.ks-doc-left-public-nav .list-sec')
        if not nav:raise ValueError('子目录缺少产品范围信息')
        for a in nav.select('a[href]'):
            link=urljoin(root,a['href']);parsed=urlsplit(link)
            if parsed.netloc!='docs.ksyun.com' or not re.fullmatch(r'/directories/\d+/?',parsed.path):continue
            parent=a.find_parent('li')
            if parent and any(re.fullmatch(r'/documents/\d+/?',urlsplit(urljoin(root,c['href'])).path) for c in parent.select('a[href]')):continue
            if parsed.path in seen:continue
            seen.add(parsed.path)
            if len(seen)>500:raise ValueError('子目录数量超过限制，未截断采集')
            final,child=fetcher(link)
            if urlsplit(final).netloc!='docs.ksyun.com':raise ValueError('子目录跳转到站外')
            child_soup=BeautifulSoup(child,'html.parser');product_links=child_soup.select('.ks-doc-left-public-nav a[href]')
            products={urlsplit(urljoin(root,x['href'])).path.rstrip('/') for x in product_links if re.fullmatch(r'/products/\d+/?',urlsplit(urljoin(root,x['href'])).path)}
            if products and urlsplit(root).path.rstrip('/') not in products:raise ValueError('子目录属于其他产品')
            for e in directory(child,root,allow_empty=True):entries.setdefault(urlsplit(e['url']).path,e)
            if len(entries)>500:raise ValueError('产品目录超过500篇，未截断采集')
            queue.append(child)
    if not entries:raise ValueError('产品子目录中没有可读取正文')
    return list(entries.values())

def article(raw,url):
    soup=BeautifulSoup(raw,'html.parser');body=soup.select_one('.ks-docs-editor__content')
    if body and not body.get_text(strip=True) and not body.select('img,table,pre'):body=None
    body=body or soup.select_one('.ks-doc-main')
    if body and body.select_one('.video-doc-container') and not body.select('[data-url]') and len(body.get_text(strip=True))<5:
        raise EmptyVideoSource('官网仅有空视频容器，没有正文和可用视频地址')
    if not body:raise ValueError('未找到文档正文')
    for x in body.select('script,style,nav,footer,button,input,select,.copy-btn,.copy-button,.hljs-copy-button,.ks-relation-page,.ks-doc-commend,.ks-doc-public-crumb,.ks-doc-update'):x.decompose()
    for table in body.select('table'):
        rows=[]
        for tr in table.select('tr'):
            cells=[' '.join(c.get_text(' ',strip=True).split()).replace('|',r'\|') for c in tr.find_all(['td','th'],recursive=False)]
            if cells:rows.append(cells)
        if rows:
            width=max(map(len,rows));rows=[r+['']*(width-len(r)) for r in rows]
            lines=['| '+' | '.join(r)+' |' for r in rows];lines.insert(1,'| '+' | '.join(['---']*width)+' |')
            table.replace_with('\n\n'+'\n'.join(lines)+'\n\n')
    for pre in body.select('pre'):pre.replace_with('\n\n```\n'+pre.get_text()+'\n```\n\n')
    for image in body.select('img'):
        src=image.get('src') or image.get('data-src')
        image.replace_with('\n!['+image.get('alt','原文图示')+']('+urljoin(url,src)+')\n' if src else '')
    for a in body.select('a[href]'):a.replace_with('['+a.get_text(' ',strip=True)+']('+urljoin(url,a['href'])+')')
    for h in body.select('h1,h2,h3,h4,h5,h6'):h.insert_before('\n'+'#'*int(h.name[1])+' ');h.insert_after('\n')
    text=re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+','\n\n',body.get_text('\n',strip=True)).strip()
    if len(text)<5:raise ValueError('文档正文为空，未接受空页面')
    if sum(x in text[:1500] for x in ('全部文档','当前文档','热搜词推荐'))>=2:raise ValueError('读取到站点导航，未接受为正文')
    return text

def rendered_article(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=launch_browser(p)
        try:
            page=browser.new_page()
            page.goto(url,wait_until='domcontentloaded',timeout=45000)
            if urlsplit(page.url).netloc!='docs.ksyun.com':raise ValueError('页面跳出文档范围')
            page.wait_for_function("document.querySelector('.ks-doc-main')?.innerText.trim().length>80",timeout=30000)
            page.wait_for_timeout(600)
            return article(page.content(),url)
        finally:browser.close()


def collect(root,folder,fetcher=None):
    if not supports(root):raise ValueError('不是金山云产品目录地址')
    fetcher=fetcher or _fetch;folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    final,raw=fetcher(root)
    if not supports(final) or urlsplit(final).path.rstrip('/')!=urlsplit(root).path.rstrip('/'):raise ValueError('产品首页跳转到其他范围')
    entries=expand_directory(raw,root,fetcher);lock=threading.Lock();done=0;failed=[]
    def read(entry):
        nonlocal done
        key=hashlib.sha256(entry['url'].encode()).hexdigest();cache=folder/(key+'.json')
        try:
            cached=json.loads(cache.read_text(encoding='utf-8')) if cache.exists() else {}
            if cached.get('url')==entry['url'] and (cached.get('text') or cached.get('source_empty')):text=cached.get('text','');source_empty=bool(cached.get('source_empty'))
            else:
                target,page=fetcher(entry['url'])
                if urlsplit(target).netloc!='docs.ksyun.com' or urlsplit(target).path!=urlsplit(entry['url']).path:raise ValueError('正文跳转超出目录范围')
                source_empty=False
                try:text=article(page,target)
                except EmptyVideoSource:
                    text='';source_empty=True;(folder/(key+'.source.html')).write_bytes(page)
                except ValueError:
                    if fetcher is not _fetch:raise
                    text=rendered_article(target)
                cache.write_text(json.dumps({**entry,'text':text,'source_empty':source_empty},ensure_ascii=False),encoding='utf-8')
            result={**entry,'text':text,'source_empty':source_empty}
        except Exception:
            result=None
            with lock:failed.append(entry)
        with lock:
            done+=1;(folder/'scrape-progress.json').write_text(json.dumps({'stage':'产品文档采集','completed':done,'total':len(entries),'failed':len(failed)}),encoding='utf-8')
        return result
    with ThreadPoolExecutor(max_workers=4) as pool:pages=list(pool.map(read,entries))
    excluded=[];learning=[]
    for page in pages:
        if not page or not page['text']:continue
        if api_reference(page):excluded.append({'title':page['title'],'url':page['url'],'reason':'API参考不属于当前学习范围'});continue
        text,stats=learning_text(page['text'],page['url'])
        learning.append({**page,'text':text,**stats})
    manifest={'origin':root,'total':len(entries),'completed':len(entries)-len(failed),'failed':failed,'source_empty':[{k:p[k] for k in ('title','url')} for p in pages if p and p.get('source_empty')],'text_pages':sum(bool(p and p['text']) for p in pages),'pages':[{k:p[k] for k in ('title','url')} for p in pages if p],
        'learning_policy':LEARNING_POLICY,'learning_pages':len(learning),'excluded':excluded,'raw_chars':sum(len(p['text']) for p in pages if p),'learning_chars':sum(len(p['text']) for p in learning),'large_code_blocks_removed':sum(p['large_code_blocks_removed'] for p in learning)}
    (folder/'product-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if failed:raise ValueError('产品文档有'+str(len(failed))+'篇读取失败，重试会继续未完成页面')
    blocks=[]
    for p in pages:
        # Full original capture is retained for audit and future range changes.
        (folder/(hashlib.sha256(p['url'].encode()).hexdigest()+'.md')).write_text('# '+p['title']+'\n\n来源：'+p['url']+'\n\n'+(p['text'] or '官网此页只有空视频容器，没有正文或可用视频地址；未纳入出题。'),encoding='utf-8')
    for p in learning:
        (folder/('learning-'+hashlib.sha256(p['url'].encode()).hexdigest()+'.md')).write_text('# '+p['title']+'\n\n来源：'+p['url']+'\n\n'+p['text'],encoding='utf-8')
        for part in p['text'].split('\n\n'):
            if part.strip():blocks.append((len(blocks)+1,'text','文档：'+p['title']+'\n来源：'+p['url']+'\n\n'+part))
    return blocks
