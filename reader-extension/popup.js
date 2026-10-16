'use strict';
let capture=null;
const status=document.querySelector('#status');
document.querySelector('#read').onclick=async()=>{
  document.querySelector('#export').disabled=true;
  capture=null;
  try{
    const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
    if(!tab?.id||!/^https?:\/\//.test(tab.url||''))throw Error('请先打开 http 或 https 课程页面。');
    const [result]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>{
      const selection=window.getSelection()?.toString().trim();
      let text=selection||'';
      const headings=[];
      if(!text){
        const root=document.querySelector('article,[role="document"],main')||document.body;
        const nodes=root.querySelectorAll('h1,h2,h3,h4,p,li,td,th,blockquote,pre,[role="paragraph"]');
        const lines=[];
        for(const n of nodes){
          if(n.closest('nav,header,footer,aside,form,button,[hidden],[aria-hidden="true"],script,style'))continue;
          const style=getComputedStyle(n);if(style.display==='none'||style.visibility==='hidden'||!n.getClientRects().length)continue;
          const content=n.innerText?.trim();if(!content)continue;
          if(/^H[1-4]$/.test(n.tagName))headings.push(content);
          if(!lines.includes(content))lines.push(content);
        }
        text=lines.join('\n\n');
      }
      const limit=150000, truncated=text.length>limit;
      return {kind:'zhiwen-page-capture',version:1,title:document.title,url:location.href,text:text.slice(0,limit),headings:headings.slice(0,60),capturedAt:new Date().toISOString(),method:selection?'selection':'visible-dom',coverage:(selection?'用户选中的片段':'页面已加载的可见文本片段')+(truncated?'（已截断，请分段）':'')};
    }});
    capture=result?.result;
    if(!capture?.text?.trim())throw Error('未读到可用正文。尝试选中正文或字幕后重试；画布文档请导出原件。');
    document.querySelector('#preview').value=capture.text;
    status.textContent=`读到 ${capture.text.length} 字 · ${capture.coverage}。请核对是否为课程正文。`;
    document.querySelector('#export').disabled=false;
  }catch(e){status.textContent=e.message;document.querySelector('#preview').value=''}
};
document.querySelector('#export').onclick=()=>{
  if(!capture)return;
  const blob=new Blob([JSON.stringify(capture,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download='知温-页面读取.json';document.body.append(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),30000);
  status.textContent='已发起导出。回到课程 → 浏览器读取 → 接入读取结果。';
};
