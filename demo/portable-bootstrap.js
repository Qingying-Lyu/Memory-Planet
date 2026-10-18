(async()=>{
const token=new URLSearchParams(location.hash.slice(1)).get('token')||sessionStorage.getItem('memory-planet-token')||'';
if(token)sessionStorage.setItem('memory-planet-token',token);
try{
 const r=await fetch('/setup/status',{headers:{Authorization:'Bearer '+token}});if(!r.ok)throw Error('访问链接失效，请重新双击“打开记忆星球.cmd”。');
 if(!(await r.json()).configured){location.replace('/setup');return}
 const stateResponse=await fetch('/client-state',{headers:{Authorization:'Bearer '+token}});if(!stateResponse.ok)throw Error('无法读取本机状态');
 window.planetInstallation=(await stateResponse.json()).reset_id;

 for(const src of ['app.js','backend-bridge.js','material-workflow.js'])await new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=src+'?v=0.0.2';s.onload=resolve;s.onerror=reject;document.body.append(s)});
 const addSettings=()=>{const bar=document.querySelector('.topbar');if(!bar||bar.querySelector('[data-portable-settings]'))return;const link=document.createElement('a');link.dataset.portableSettings='1';link.href='/setup';link.textContent='连接设置';link.style.cssText='color:#335b45;font-size:14px;text-decoration:none;white-space:nowrap;margin-left:16px';const badge=bar.querySelector('.demo-label');if(badge)badge.style.marginLeft='auto';bar.append(link)};
 const productLayout=layout;layout=function(){productLayout();addSettings()};addSettings();
 new MutationObserver(addSettings).observe(document.getElementById('app'),{childList:true,subtree:true});
}catch(e){document.getElementById('app').textContent=e.message}
})();
