const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = __dirname;
const {resolveDirectory}=require('./bilibili.cjs');
const mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.ico':'image/x-icon'};
const allowed = new Set(['index.html','styles.css','app.js','content.js','engine.js']);
const server=http.createServer((req,res)=>{
  let file;
  try {file=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\//,'')||'index.html';}catch{res.writeHead(400);res.end();return;}
  if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405);res.end();return;}
  if(file==='api/bilibili'){
    if(req.headers.origin&&req.headers.origin!=='http://127.0.0.1:18765'){res.writeHead(403);res.end();return;}
    const raw=new URL(req.url,'http://localhost').searchParams.get('url')||'';
    resolveDirectory(raw).then(data=>{res.writeHead(200,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data))}).catch(err=>{res.writeHead(422,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify({error:err.message}))});return;
  }
  if(!allowed.has(file)){res.writeHead(404);res.end('Not found');return;}
  fs.readFile(path.join(root,file),(err,data)=>{if(err){res.writeHead(500);res.end('Unable to read asset');return;}res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(req.method==='HEAD'?undefined:data);});
});
server.on('error',err=>{console.error(err.code==='EADDRINUSE'?'Port 18765 is already in use. Open http://127.0.0.1:18765 if the Demo is running, or close the conflicting service.':err.message);process.exit(1);});
server.listen(18765,'127.0.0.1',()=>{
  console.log('Zhiwen demo is ready: http://127.0.0.1:18765\nClose this terminal to stop the server.');
  if(process.argv.includes('--open')&&process.platform==='win32')require('node:child_process').execFile('rundll32.exe',['url.dll,FileProtocolHandler','http://127.0.0.1:18765'],{windowsHide:true},()=>{});
});
