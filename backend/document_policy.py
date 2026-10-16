"""Reusable product-document learning scope, independent of crawler/model vendor.
Keep full captures separately; only filtered learning text is indexed for questions.
"""
import re
from urllib.parse import urlsplit,parse_qs

LEARNING_POLICY='principles-guides-v1'
def api_reference(entry):
    kind=parse_qs(urlsplit(entry['url']).query).get('type',[''])[0]
    return (urlsplit(entry['url']).hostname=='docs.ksyun.com' and kind in ('6','7')) or bool(re.search(r'/(?:api-reference|api_reference|api-docs|openapi)(?:/|$)',urlsplit(entry['url']).path,re.I)) or any(re.match(r'^API(?:目录|文档|参考|分组|概览)',p,re.I) for p in entry.get('path',[]))

def learning_text(text,url):
    removed=[0]
    def code(match):
        content=match.group(1)
        if len(content)>1200 or len(content.splitlines())>20:
            removed[0]+=1
            return '[完整代码或配置示例已省略，查看原文]('+url+')'
        return match.group(0)
    cleaned=re.sub(r'```[^\n]*\n(.*?)\n```',code,text,flags=re.S)
    # Exact repeated prose inside a page is redundant; short commands and table rows remain.
    seen=set();parts=[];duplicates=0
    for paragraph in cleaned.split('\n\n'):
        norm=''.join(paragraph.split())
        if len(norm)>80 and norm in seen:duplicates+=len(paragraph);continue
        seen.add(norm);parts.append(paragraph)
    return '\n\n'.join(parts),{'large_code_blocks_removed':removed[0],'duplicate_chars_removed':duplicates}

