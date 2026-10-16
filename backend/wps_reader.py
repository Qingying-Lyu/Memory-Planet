"""Authenticated local reader. Cookie never enters snapshots, Markdown or model inputs."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from .cloud import read_secret_file
from .store import ROOT

ALLOWED_HOSTS = {'365.kdocs.cn', 'plus.wps.cn'}

class DocumentReadError(ValueError):
    """Only fixed, credential-free reader messages may reach the product UI."""
    pass


def session_value(path):
    value = read_secret_file(path).strip().strip('\"\'')
    if value.startswith('wps_sid='):
        value = value.split('=', 1)[1].split(';', 1)[0]
    if not value or any(c.isspace() or c in ';\r\n' for c in value):
        raise DocumentReadError('WPS会话文件格式无效，仅保存wps_sid值')
    return value


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in ALLOWED_HOSTS or parsed.fragment:
        raise DocumentReadError('仅支持指定WPS材料域名的HTTPS链接')
    return parsed


def environment(html):
    match = re.search(r'window\.__WPSENV__\s*=\s*', html)
    if not match:
        return {}
    try:
        value, _ = json.JSONDecoder().raw_decode(html[match.end():])
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def probe(url, secret_file):
    validate_url(url)
    try:
        response = httpx.get(url, headers={'Cookie': 'wps_sid=' + session_value(secret_file)},
                             timeout=30, follow_redirects=False)
    except httpx.HTTPError:
        raise DocumentReadError('WPS访问失败，未记录响应或会话值') from None
    if response.status_code != 200:
        return {'status': 'access_pending', 'http_status': response.status_code, 'origin': url}
    html = response.content.decode('utf-8')
    env = environment(html)
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(strip=True) if soup.title else ''
    file = env.get('file_info', {}).get('file', {})
    acl = env.get('file_info', {}).get('user_acl', {})
    editor = soup.select_one('.ssr-editor')
    for tag in soup(['script', 'style']):
        tag.decompose()
    return {'origin': url, 'title': file.get('name') or title, 'file_id': file.get('id'),
            'status': 'readable_preview' if env and acl.get('read') else 'app_shell_only',
            'page_count': env.get('wpp_total_pages'), 'download_allowed': bool(acl.get('download')),
            'preview': editor.get_text('\n', strip=True) if editor else '',
            'coverage': '初始页面预览，不代表全文；图片和公式未验证'}


def collect_note(url, secret_file, folder):
    """Walk the actual virtualized document, preserving stable block identifiers."""
    from playwright.sync_api import sync_playwright
    validate_url(url)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    collected = {}
    headings = []
    visual_assets = {}
    capture_attempts = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        try:
            context = browser.new_context()
            context.add_cookies([{'name': 'wps_sid', 'value': session_value(secret_file),
                                  'domain': urlsplit(url).hostname, 'path': '/', 'secure': True}])
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
            page.wait_for_selector('.otl-scroll-container', timeout=45000)
            page.wait_for_timeout(6000)
            title = page.title()
            # Catalog describes document headings, not necessarily learned scope.
            catalog = page.locator('.catalog-content').evaluate_all('(xs)=>xs.map(x=>x.innerText).join("\\n")')
            scroller = page.locator('.otl-scroll-container').first
            bottom = False
            for step in range(150):
                # Virtualized nodes can disappear between calls. Snapshot IDs atomically,
                # then bound each capture instead of waiting 30 seconds on a stale nth locator.
                picture_ids=page.locator('.otl-scroll-container .PMNodeview.block[data-node-type="picture"]').evaluate_all('(xs)=>xs.filter(x=>{const r=x.getBoundingClientRect();return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight}).map(x=>x.id).filter(Boolean)')
                for block_id in picture_ids:
                    picture=page.locator('[id='+json.dumps(block_id)+']').first
                    if block_id and (block_id not in visual_assets or visual_assets[block_id]['status']=='capture_failed') and capture_attempts.get(block_id,0)<2:
                        capture_attempts[block_id]=capture_attempts.get(block_id,0)+1
                        name = 'image-' + re.sub(r'[^A-Za-z0-9_-]', '_', block_id) + '.png'
                        try:
                            picture.screenshot(path=str(folder/name),timeout=1500,animations='disabled')
                            visual_assets[block_id] = {'block_id':block_id,'image':name,'status':'pending_visual_review'}
                        except Exception:
                            # Reading text continues; failed image capture is explicitly preserved.
                            visual_assets[block_id] = {'block_id':block_id,'status':'capture_failed'}
                blocks = page.locator('.otl-scroll-container .block_tile[id], .otl-scroll-container .PMNodeview.block[id]').evaluate_all('''xs => xs.filter(x=>!xs.some(y=>y!==x&&y.contains(x))).map(x => ({
                    id:x.getAttribute('data-id') || x.getAttribute('data-block-id') || x.id,
                    text:x.innerText, html:x.outerHTML,
                    heading:!!x.querySelector('.otl-heading-content')}))''')
                for block in blocks:
                    if not block['text'].strip():
                        continue
                    key = block['id'] or block['text']
                    collected[key] = block
                    if block['heading'] and block['text'].strip() not in headings:
                        headings.append(block['text'].strip())
                metrics = scroller.evaluate('(e)=>({top:e.scrollTop,height:e.clientHeight,total:e.scrollHeight})')
                (folder/'scrape-progress.json').write_text(json.dumps({'stage':'云文档采集','step':step+1,'blocks':len(collected),'images':len(visual_assets),'scroll':metrics}),encoding='utf-8')
                if metrics['top'] + metrics['height'] >= metrics['total'] - 4:
                    bottom = True
                    break
                scroller.evaluate('(e)=>{e.scrollTop+=Math.max(100,e.clientHeight*0.7)}')
                page.wait_for_timeout(250)
            rows = []
            for index, (block_id, block) in enumerate(collected.items(), 1):
                rows.append({'ordinal': index, 'block_id': block_id, 'text': block['text'].strip(),
                             'kind': 'heading' if block['heading'] else 'paragraph'})
            result = {'origin': url, 'title': title, 'method': 'authenticated_browser_scroll',
                      'reached_bottom': bottom, 'catalog': catalog, 'headings': headings,
                      'visual_assets': list(visual_assets.values()),
                      'blocks': rows, 'status': 'text_extracted_pending_qa' if bottom else 'partial',
                      'coverage': '浏览器正文逐段采集；图片、公式和隐藏折叠内容尚未核对，不代表已学范围'}
            (folder / 'extracted.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            markdown = '# ' + title + '\n\n来源：' + url + '\n\n状态：' + result['status'] + '\n\n' + result['coverage'] + '\n'
            for row in rows:
                markdown += f'\n## 原文块 {row["ordinal"]}\n\n{row["text"]}\n'
            if visual_assets:
                markdown += '\n## 待核对图示\n'
                for asset in visual_assets.values():
                    if asset.get('image'):
                        markdown += f'\n![图示 {asset["block_id"]}]({asset["image"]})\n'
            (folder / 'source.md').write_text(markdown, encoding='utf-8')
            return {k: v for k, v in result.items() if k not in ('blocks', 'catalog')}
        finally:
            browser.close()


def collect_presentation(url, secret_file, folder):
    from playwright.sync_api import sync_playwright
    validate_url(url)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        try:
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.add_cookies([{'name': 'wps_sid', 'value': session_value(secret_file),
                                  'domain': urlsplit(url).hostname, 'path': '/', 'secure': True}])
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
            page.wait_for_selector('.thumbnail_slide', timeout=45000)
            page.wait_for_timeout(6000)
            title = page.title()
            slides = page.locator('.thumbnail_slide')
            total = slides.count()
            if not 1 <= total <= 300:
                raise DocumentReadError('幻灯片数量异常')
            rows = []
            for index in range(total):
                slide = slides.nth(index)
                slide.scroll_into_view_if_needed()
                slide.click()
                page.wait_for_function('(n)=>document.querySelector(".status_page_index")?.innerText.includes(" "+n+"/")', arg=index+1, timeout=15000)
                page.wait_for_timeout(650)
                text = page.locator('.wpp_editor_container').inner_text().split('无备注')[0].split('AI 演讲稿')[0].strip()
                screenshot = folder / f'page-{index+1:03}.png'
                page.locator('.wpp_editor_container').screenshot(path=str(screenshot))
                rows.append({'ordinal': index+1, 'kind': 'slide', 'text': text,
                             'image': screenshot.name, 'status': 'text_and_image_pending_qa'})
            result = {'title': title, 'origin': url, 'page_count': total, 'blocks': rows,
                      'status': 'text_extracted_pending_qa',
                      'coverage': '逐页正文和截图；图示含义、公式、图中文字及阅读顺序尚待视觉核对'}
            (folder / 'extracted.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            markdown = '# ' + title + '\n\n来源：' + url + '\n\n' + result['coverage'] + '\n'
            for row in rows:
                markdown += f'\n## 幻灯片 {row["ordinal"]}\n\n{row["text"]}\n\n![页面截图]({row["image"]})\n'
            (folder / 'source.md').write_text(markdown, encoding='utf-8')
            return {k: v for k, v in result.items() if k != 'blocks'}
        finally:
            browser.close()



def document_type(info, url):
    """Metadata wins over a stale link path or caller-provided officeType."""
    suffix = Path(info.get('title', '')).suffix.lower()
    kinds = {'.doc': 'word', '.docx': 'word', '.wps': 'word',
             '.ppt': 'presentation', '.pptx': 'presentation', '.dps': 'presentation',
             '.xls': 'spreadsheet', '.xlsx': 'spreadsheet', '.et': 'spreadsheet',
             '.md': 'markdown', '.markdown': 'markdown', '.txt': 'markdown',
             '.mp4': 'video', '.webm': 'video', '.mov': 'video',
             '.mp3': 'video', '.wav': 'video', '.m4a': 'video'}
    if suffix in kinds: return kinds[suffix]
    if info.get('page_count'): return 'presentation'
    if 'officeType=p' in url: return 'presentation'
    if 'plus.wps.cn/view/media' in url: return 'video'
    return 'note'



def diagram_blocks(definition):
    """Preserve hierarchy and connector direction; never flatten a diagram to labels."""
    clean=lambda text:BeautifulSoup(str(text or ''),'html.parser').get_text(' ',strip=True)
    rows=[];images=[]
    if isinstance(definition.get('elements'),dict):
        elements=definition['elements'];labels={}
        for key,e in elements.items():
            if not isinstance(e,dict) or e.get('name')=='linker':continue
            label=' '.join(clean(t.get('text')) for t in e.get('textBlock',[]) if isinstance(t,dict)).strip()
            labels[key]=label or '[无文字节点 '+key+']'
            if label:rows.append({'kind':'diagram_node','text':'节点 '+key+'：'+label,'node_id':key})
        for key,e in elements.items():
            if not isinstance(e,dict):continue
            if e.get('parent') in labels and key in labels:
                rows.append({'kind':'diagram_group','text':'分组：'+labels[e['parent']]+' 包含 '+labels[key]})
            if e.get('name')=='linker':
                a=e.get('from',{}).get('id');b=e.get('to',{}).get('id')
                if a not in labels or b not in labels:raise DocumentReadError('流程图存在无法对应的连线，未接纳不完整流程')
                label=clean(e.get('text')) or ' '.join(clean(t.get('text')) for t in e.get('textBlock',[]))
                rows.append({'kind':'diagram_edge','text':labels[a]+' --'+(label or '流程连接')+'--> '+labels[b]})
            fill=e.get('fillStyle',{})
            if fill.get('fileId') or fill.get('image'):raise DocumentReadError('流程图包含尚未读取的图片内容')
        kind='flowchart'
    elif isinstance(definition.get('children'),list):
        seen=set()
        def walk(node,parents):
            if not isinstance(node,dict):raise DocumentReadError('思维导图节点结构无效')
            key=node.get('id')
            if key in seen:raise DocumentReadError('思维导图节点重复')
            seen.add(key);title=clean(node.get('title'));path=parents+([title] if title else [])
            if title:rows.append({'kind':'mindmap_node','node_id':key,'text':' > '.join(path)})
            note=clean(node.get('note') or node.get('remark'))
            if note:rows.append({'kind':'mindmap_note','text':' > '.join(path)+'：'+note})
            if node.get('image',{}).get('url'):images.append({'node_id':key,'context':' > '.join(path),'url':node['image']['url']})
            for child in node.get('children',[])+node.get('freeChildren',[]):walk(child,path)
        walk(definition,[]);kind='mindmap'
    else:raise DocumentReadError('图表正文结构尚未识别')
    if not rows:raise DocumentReadError('图表没有可读取的正文')
    return [{**r,'ordinal':i} for i,r in enumerate(rows,1)],images,kind


def detect_browser_type(url,secret_file):
    from playwright.sync_api import sync_playwright
    validate_url(url)
    with sync_playwright() as p:
        b=p.chromium.launch(channel='msedge',headless=True)
        try:
            c=b.new_context();c.add_cookies([{'name':'wps_sid','value':session_value(secret_file),'domain':host,'path':'/','secure':True} for host in ('.kdocs.cn','.wps.cn')])
            page=c.new_page();page.goto(url,wait_until='domcontentloaded',timeout=45000)
            for _ in range(90):
                if any(urlsplit(f.url).hostname=='wps.processon.com' for f in page.frames):return 'diagram'
                if page.locator('#workspace svg.text-page').count():return 'word'
                if page.locator('.thumbnail_slide').count():return 'presentation'
                if page.locator('.otl-scroll-container').count():return 'note'
                if page.locator('video').count():return 'video'
                page.wait_for_timeout(500)
            raise DocumentReadError('尚未识别云文档正文类型或授权尚未生效')
        finally:b.close()


def collect_diagram(url,secret_file,folder):
    """Capture only authorized content definitions, excluding API account/session fields."""
    from playwright.sync_api import sync_playwright
    validate_url(url);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        b=p.chromium.launch(channel='msedge',headless=True)
        try:
            c=b.new_context();c.add_cookies([{'name':'wps_sid','value':session_value(secret_file),'domain':'.kdocs.cn','path':'/','secure':True}])
            page=c.new_page();contents=[];permissions={};canvases=[]
            def receive(response):
                parsed=urlsplit(response.url)
                if parsed.hostname!='wps.processon.com':return
                if parsed.path not in ('/wpsapi/diagrams/edit/api','/wpsapi/diagrams/view/api','/wpsapi/diagraming/get/chart/def','/wpsapi/canvas/get/chartdefids'):return
                try:
                    data=response.json()
                    if isinstance(data.get('user_acl'),dict):permissions.update(data['user_acl'])
                    # Read-only share pages use a different authorized bootstrap response.
                    if parsed.path=='/wpsapi/diagrams/view/api' and response.status==200 and data.get('role') in ('owner','editor','viewer','reader','view') and data.get('definition') and type(data.get('readonly')) is bool:
                        permissions['read']=True
                    if isinstance(data.get('chartCanvas'),list):canvases[:]=data['chartCanvas']
                    if isinstance(data.get('data'),dict) and isinstance(data['data'].get('chartCanvas'),list):canvases[:]=data['data']['chartCanvas']
                    raw=data.get('data',{}).get('def') if isinstance(data.get('data'),dict) else None
                    raw=raw or data.get('def') or data.get('definition')
                    if isinstance(raw,str):raw=json.loads(raw)
                    if isinstance(raw,dict):contents.append(raw)
                except (ValueError,TypeError):pass
            page.on('response',receive);page.goto(url,wait_until='domcontentloaded',timeout=45000)
            for _ in range(120):
                if contents and permissions.get('read'):break
                page.wait_for_timeout(500)
            if not contents or not permissions.get('read'):raise DocumentReadError('未取得授权图表正文')
            page.wait_for_timeout(2500)
            definitions=[contents[-1]]
            if len(canvases)>1:
                frame=next((f for f in page.frames if urlsplit(f.url).hostname=='wps.processon.com'),None)
                if not frame:raise DocumentReadError('图表正文框架未完成加载')
                for canvas in canvases[1:]:
                    count=len(contents)
                    frame.get_by_text(canvas['title'],exact=True).click(timeout=10000)
                    for _ in range(40):
                        page.wait_for_timeout(250)
                        if len(contents)>count:break
                    if len(contents)==count:raise DocumentReadError('部分图表画布未完成读取')
                    definitions.append(contents[-1])
            rows=[];images=[];kind=None
            for index,definition in enumerate(definitions,1):
                current,pictures,kind=diagram_blocks(definition)
                rows.extend({**r,'text':('画布 '+str(index)+'：' if len(definitions)>1 else '')+r['text']} for r in current)
                images.extend(pictures)
            rows=[{**r,'ordinal':i} for i,r in enumerate(rows,1)];assets=[]
            for i,image in enumerate(images,1):
                parsed=urlsplit(image['url'])
                if parsed.scheme!='https' or parsed.hostname not in ('tc-cdn.processon.com','cdn.processon.com'):raise DocumentReadError('导图图片来源未验证')
                # No WPS SID is added to a third-party request.
                r=httpx.get(image['url'],timeout=30,follow_redirects=False)
                if r.status_code!=200 or len(r.content)>20_000_000:raise DocumentReadError('导图图片读取未完成')
                if not r.content.startswith((b'\x89PNG',b'\xff\xd8',b'GIF8',b'RIFF')):raise DocumentReadError('导图附件不是可验证图片')
                extension='.png' if r.content.startswith(b'\x89PNG') else '.jpg' if r.content.startswith(b'\xff\xd8') else '.gif' if r.content.startswith(b'GIF8') else '.webp'
                name=f'diagram-image-{i:03}'+extension;(folder/name).write_bytes(r.content)
                assets.append({'image':name,'context':image['context'],'node_id':image['node_id'],'status':'pending_visual_review'})
            result={'origin':url,'title':page.title(),'method':'authenticated_processon_definition','document_type':kind,
                'reached_bottom':True,'blocks':rows,'visual_assets':assets,'status':'text_extracted_pending_qa',
                'coverage':'授权正文结构；保留所有节点、层级和连线，含折叠节点；图片另做视觉核对'}
            (folder/'extracted.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            (folder/'source.md').write_text('# '+result['title']+'\n\n'+'\n\n'.join(r['text'] for r in rows),encoding='utf-8')
            return result
        finally:b.close()

def collect_word(url, secret_file, folder):
    """Read the authorized Word SVG pages, never the document outline."""
    from playwright.sync_api import sync_playwright
    validate_url(url)
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        try:
            context = browser.new_context(viewport={'width':1600,'height':1450})
            context.add_cookies([{'name':'wps_sid','value':session_value(secret_file),
                'domain':urlsplit(url).hostname,'path':'/','secure':True}])
            page = context.new_page(); page.goto(url,wait_until='domcontentloaded',timeout=45000)
            page.wait_for_selector('#workspace svg.text-page',timeout=60000)
            scroller=page.locator('#workspace'); captured={}; bottom_rounds=0; expected=0
            for step in range(600):
                # Pagination changes during font loading. Require stable bottom and exact coverage.
                page.wait_for_timeout(400)
                status=' '.join(page.locator('.page-info').all_text_contents())
                match=re.search(r'(\d+)\s*/\s*(\d+)',status)
                if match: expected=int(match.group(2))
                pages=page.locator('#workspace svg.text-page')
                for i in range(pages.count()):
                    node=pages.nth(i)
                    row=node.evaluate("""svg=>{
                      const main=svg.querySelector('#main'); if(!main)return null;
                      const runs=[...main.querySelectorAll('text')].map(t=>{
                        const m=t.getCTM(),p=svg.createSVGPoint();p.x=t.x.baseVal.numberOfItems?t.x.baseVal.getItem(0).value:0;p.y=t.y.baseVal.numberOfItems?t.y.baseVal.getItem(0).value:0;
                        const q=p.matrixTransform(m);return {x:q.x,y:q.y,text:t.textContent};
                      }).filter(t=>t.text.trim()).sort((a,b)=>a.y-b.y||a.x-b.x);
                      const lines=[];for(const t of runs){let l=lines.find(l=>Math.abs(l.y-t.y)<3);if(!l){l={y:t.y,runs:[]};lines.push(l)}l.runs.push(t)}
                      return {top:parseFloat(svg.parentElement.style.top),
                        text:lines.sort((a,b)=>a.y-b.y).map(l=>l.runs.sort((a,b)=>a.x-b.x).map(t=>t.text).join('')).join('\\n'),
                        visual:!!main.querySelector('image,foreignObject,path,polygon,polyline')};
                    }""")
                    if not row or not row['text'].strip(): continue
                    key=round(row['top'],1)
                    if key not in captured:
                        row['image']='word-'+str(len(captured)+1).zfill(3)+'.png'
                        # SVG captures preserve the whole page, even outside the viewport.
                        node.screenshot(path=str(folder/row['image']),timeout=5000,animations='disabled')
                        captured[key]=row
                metrics=scroller.evaluate('(e)=>({top:e.scrollTop,height:e.clientHeight,total:e.scrollHeight})')
                (folder/'scrape-progress.json').write_text(json.dumps({'stage':'Word正文分页采集','completed':len(captured),'total':expected}),encoding='utf-8')
                at_bottom=metrics['top']+metrics['height']>=metrics['total']-4
                bottom_rounds=bottom_rounds+1 if at_bottom else 0
                if bottom_rounds>=5 and expected and len(captured)==expected: break
                if expected>300: raise DocumentReadError('Word文档超过300页，请拆分材料')
                if at_bottom and bottom_rounds>=20: raise DocumentReadError('Word分页数量与采集正文不一致，未接纳不完整内容')
                scroller.evaluate('(e)=>{e.scrollTop+=Math.max(200,e.clientHeight*0.7)}')
            else: raise DocumentReadError('Word全文采集未完成，已保存进度')
            rows=[{**r,'ordinal':i,'kind':'page'} for i,(_,r) in enumerate(sorted(captured.items()),1)]
            result={'origin':url,'title':page.title(),'method':'authenticated_word_svg',
                'page_count':expected,'reached_bottom':True,'blocks':rows,'status':'text_extracted_pending_qa',
                'coverage':'正文SVG逐页提取，页数与分页栏一致；含图页面另做视觉核对'}
            (folder/'extracted.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            (folder/'source.md').write_text('# '+result['title']+'\n\n'+'\n\n'.join('## 第 '+str(r['ordinal'])+' 页\n\n'+r['text'] for r in rows),encoding='utf-8')
            return result
        finally: browser.close()

def collect_video(url, secret_file, folder, extract_audio=False):
    """Read authorized player metadata; extract audio only when download ACL allows it."""
    from playwright.sync_api import sync_playwright
    import subprocess
    import hashlib
    from urllib.parse import urljoin
    from concurrent.futures import ThreadPoolExecutor
    validate_url(url)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        try:
            context = browser.new_context()
            value = session_value(secret_file)
            context.add_cookies([{'name': 'wps_sid', 'value': value, 'domain': host, 'path': '/', 'secure': True}
                                 for host in ('.wps.cn', '.kdocs.cn')])
            page = context.new_page()
            permissions = {}
            playlist = {}
            def receive(response):
                parsed = urlsplit(response.url)
                if parsed.hostname == 'drive.wps.cn' and '/api/v5/links/' in parsed.path:
                    try:
                        result = response.json()
                        permissions.update(result.get('user_acl', {}))
                    except Exception:
                        pass
                if parsed.hostname == 'plus.wps.cn' and 'mpegurl' in response.headers.get('content-type','').lower():
                    try:
                        text = response.text()
                        if text.startswith('#EXTM3U'):
                            playlist.update(url=response.url, text=text)
                    except Exception:
                        pass
            page.on('response', receive)
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
            page.wait_for_selector('video', timeout=45000)
            page.wait_for_function('()=>document.querySelector("video")?.readyState>=1', timeout=45000)
            properties = page.locator('video').evaluate('(v)=>({duration:v.duration,track_count:v.textTracks.length,source:v.currentSrc})')
            title = page.locator('body').inner_text().split('\n')[0]
            result = {'origin': url, 'title': title, 'duration_seconds': properties['duration'],
                      'subtitle_tracks': properties['track_count'], 'download_allowed': bool(permissions.get('download')),
                      'status': 'pending_transcription', 'coverage': '仅视频元数据；无转写正文，不可出题'}
            (folder / 'metadata.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            if extract_audio:
                if not permissions.get('download'):
                    raise DocumentReadError('当前视频账号没有下载授权，不提取音频')
                import imageio_ffmpeg
                target = folder / 'audio.wav'
                temporary = folder / 'audio.partial.wav'
                if not playlist or '#EXT-X-KEY' in playlist['text'] or '#EXT-X-ENDLIST' not in playlist['text']:
                    raise DocumentReadError('播放器媒体来源未验证，不发送会话值')
                segments = [urljoin(playlist['url'], line.strip()) for line in playlist['text'].splitlines()
                            if line.strip() and not line.startswith('#')]
                segment_folder = folder/'segments'
                segment_folder.mkdir(exist_ok=True)
                def fetch_segment(pair):
                    index, url = pair
                    destination = segment_folder/f'{index:05}.ts'
                    if destination.exists() and destination.stat().st_size > 0:
                        return destination
                    for _ in range(4):
                        parsed = urlsplit(url)
                        if parsed.scheme != 'https' or parsed.hostname not in ('plus.wps.cn','plg-preview.ks3-cn-beijing.ksyuncs.com'):
                            raise DocumentReadError('媒体分片域名未验证')
                        headers = {'Cookie':'wps_sid='+value} if parsed.hostname == 'plus.wps.cn' else {}
                        response = httpx.get(url,headers=headers,timeout=60,follow_redirects=False)
                        if response.status_code in (301,302,303,307,308):
                            url = urljoin(url,response.headers['location'])
                            continue
                        if response.status_code != 200:
                            raise DocumentReadError('授权媒体分片读取失败')
                        temporary_segment = destination.with_suffix('.tmp')
                        temporary_segment.write_bytes(response.content)
                        temporary_segment.replace(destination)
                        return destination
                    raise DocumentReadError('媒体分片重定向过多')
                with ThreadPoolExecutor(max_workers=6) as pool:
                    for done, _ in enumerate(pool.map(fetch_segment, enumerate(segments)), 1):
                        if done % 50 == 0:
                            print(f'authorized playback segments: {done}/{len(segments)}',flush=True)
                raw = folder/'media.ts'
                with raw.open('wb') as output:
                    for index in range(len(segments)):
                        with (segment_folder/f'{index:05}.ts').open('rb') as part:
                            import shutil
                            shutil.copyfileobj(part,output)
                completed = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-y',
                    '-i', str(raw), '-vn', '-ac', '1', '-ar', '16000', str(temporary)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
                if completed.returncode != 0:
                    raise DocumentReadError('音频提取失败，原文件保留待核对')
                temporary.replace(target)
                # Retain authorized media locally for timestamped visual alignment.
                result['video_file'] = str(raw)
                result['audio_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
                result['audio_file'] = str(target)
                result['audio_method'] = 'authorized_playback_stream; not original-file export'
            (folder / 'metadata.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            return result
        finally:
            browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--session-file', required=True)
    parser.add_argument('--note', action='store_true')
    parser.add_argument('--presentation', action='store_true')
    parser.add_argument('--video', action='store_true')
    parser.add_argument('--extract-audio', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'wps-note')
    args = parser.parse_args()
    try:
        result = (collect_video(args.url, args.session_file, args.output, args.extract_audio) if args.video else
                  collect_presentation(args.url, args.session_file, args.output) if args.presentation else
                  collect_note(args.url, args.session_file, args.output) if args.note else probe(args.url, args.session_file))
    except Exception as error:
        print(json.dumps({'status':'failed', 'error_type':type(error).__name__,
                          'message':'读取未完成，未记录会话值或含签名的媒体地址'}, ensure_ascii=False))
        raise SystemExit(1) from None
    result.pop('preview', None)
    print(json.dumps(result, ensure_ascii=False))
