"""Browser QA with isolated API fixtures: no real course writes or model calls."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from .store import ROOT

def run():
    out=ROOT/'data/model-evaluation-20261012';writes=[];errors=[]
    materials=[{'id':'m1','title':'已学文档','kind':'text','learned':True,'parsed':True,'processing_state':'ready','version':1,'quality':'document','concepts':1,'cap':3,'used':0,'choice_remaining':2,'short_remaining':1,'origin':'local'},
      {'id':'m2','title':'待学文档','kind':'text','learned':False,'parsed':True,'processing_state':'ready','version':2,'quality':'document','concepts':1,'cap':3,'used':0,'choice_remaining':0,'short_remaining':0,'origin':'local'},
      {'id':'m3','title':'音视频核对','kind':'file','learned':False,'parsed':False,'processing_state':'review','version':1,'quality':'media','concepts':0,'cap':0,'used':0,'choice_remaining':0,'short_remaining':0,'origin':'local'}]
    alignment={'audio':'aligned-audio.wav','sampling':'按20秒窗口核对','windows':[{'id':'1','start':0,'end':20,'text':'缓存未命中应回源。','speech':'缓存未命中应回源。','visual':'','uncertain':['核对术语'],'frame':None,'approved':False}]}
    def route(r):
        p=urlsplit(r.request.url).path
        if p.startswith('/demo/'):r.continue_();return
        if r.request.method=='POST':writes.append({'path':p,'body':r.request.post_data_json});r.fulfill(json={'state':'queued'});return
        data=[]
        if p=='/client-state':data={'reset_id':'isolated-ui-test'}
        elif p=='/courses':data=[{'id':'qa','title':'测试课程','mode':'training'}]
        elif p=='/overview':data=[{'id':'qa','title':'测试课程','mastery':{'score':0,'assessed_concepts':0,'known_concepts':1}}]
        elif p=='/daily':data={'questions':[],'state':'waiting','slot':'qa'}
        elif p.endswith('/study-materials'):data=materials
        elif p.endswith('/body'):data={'title':'待学文档','text':'原文正文，未包含联网扩充内容。\n第1节：正文\n'+'1.1 缓存机制\n实际正文，缓存未命中应回源。\n'*100}
        elif p.endswith('/versions'):data=[{'version':2,'changes':{'analysis':{'summary':'有效期更新'},'diff':'-60秒\n+120秒','knowledge':{'concepts':1,'conflicts':0}}}]
        elif p.endswith('/alignment'):data=alignment
        elif p.endswith('/knowledge'):data={'concepts':[{'id':'k1','title':'缓存回源','statement':'未命中应回源','materials':2,'choice_cap':2,'short_cap':1}],'conflicts':[]}
        elif p.endswith('/generation'):data={'learned':1,'materials':3,'accepted':0,'remaining':3}
        r.fulfill(json=data)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1280,'height':900});page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',route)
        page.goto('http://127.0.0.1:18766/demo/index.html#token=fixture-only')
        page.locator('[data-action="planet-folder"]').click();page.get_by_text('已学文档',exact=True).wait_for()
        assert page.locator('#content [data-action="choose"]').count()==0
        page.locator('[data-action="mw-more"][data-id="m1"]').click()
        assert page.locator('[data-action="mw-update"][data-id="m1"]').is_disabled()
        page.evaluate('close()')
        assert page.locator('[data-action="planet-material-learned"][data-id="m3"]').is_disabled()
        page.locator('[data-action="planet-read-study-material"][data-id="m2"]').click()
        assert '实际正文' in page.locator('#planet-body-text').inner_text()
        page.locator('#planet-body-jump').select_option(index=80)
        assert page.locator('.modal-head [data-action="close"]').is_visible()
        page.locator('.modal-head [data-action="close"]').click()
        page.locator('[data-action="mw-more"][data-id="m2"]').click()
        page.locator('[data-action="mw-update"][data-id="m2"]').click();page.locator('#mw-text').wait_for();assert '原文正文' in page.locator('#mw-text').input_value()
        page.locator('#mw-text').fill('更新后的资料正文。');page.locator('[data-action="mw-save-update"]').click();page.get_by_text('音视频核对',exact=True).wait_for()
        page.locator('[data-action="mw-more"][data-id="m2"]').click()
        page.locator('[data-action="mw-history"][data-id="m2"]').click();page.get_by_text('有效期更新',exact=True).wait_for();page.evaluate('close()')
        page.locator('[data-action="mw-alignment"]').click();page.locator('.mw-approved').check();page.locator('.mw-reviewed-text').fill('核对后：缓存未命中应回源。');page.locator('[data-action="mw-approve-alignment"]').click();page.get_by_text('已学文档',exact=True).wait_for()
        assert not errors,errors
        assert any(w['path'].endswith('/m2/update') and w['body']['text']=='更新后的资料正文。' for w in writes)
        assert any(w['path'].endswith('/m3/alignment') and w['body']['windows'][0]['approved'] for w in writes)
        browser.close()
    result={'passed':True,'page_errors':errors,'desktop_workflow':True,'fixture_writes':len(writes)}
    (out/'ui-result.json').write_text(json.dumps(result),encoding='utf-8');print(json.dumps(result))

if __name__=='__main__':run()
