"""Portable local-only entry point, using packaged dependencies."""
import os,secrets,socket,sqlite3
from pathlib import Path
import uvicorn
if __name__=='__main__':
    from .settings import configure
    from .relocation import relocate
    relocate()
    configure();os.environ['ZHIWEN_SCHEDULER_ENABLED']='1'
    root=Path(__file__).resolve().parents[1];data=root/'data';data.mkdir(exist_ok=True)
    port=None
    for candidate in range(18766,18867):
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',candidate));port=candidate;break
            except OSError:pass
    if port is None:raise RuntimeError('没有可用的本地服务端口')
    token=secrets.token_urlsafe(32);os.environ['ZHIWEN_LOCAL_TOKEN']=token
    with sqlite3.connect(data/'learning.sqlite3') as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower()=='wal'
    (data/'lab-url.txt').write_text(f'http://127.0.0.1:{port}/entry#token='+token,encoding='utf-8')
    uvicorn.run('backend.main:app',host='127.0.0.1',port=port,access_log=False)
