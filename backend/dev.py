"""Local-only launcher. Cloud credential paths are supplied via environment."""
import os
import secrets
from pathlib import Path
import uvicorn


if __name__ == '__main__':
    from .settings import configure
    configure()
    os.environ['ZHIWEN_SCHEDULER_ENABLED'] = '1'
    token = secrets.token_urlsafe(32)
    os.environ['ZHIWEN_LOCAL_TOKEN'] = token
    directory = Path(__file__).resolve().parents[1] / 'data'
    directory.mkdir(exist_ok=True)
    # Concurrent page reads must not wait behind background audit writes.
    import sqlite3
    with sqlite3.connect(directory/'learning.sqlite3') as db:
        mode=db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        if mode.lower()!='wal':raise RuntimeError('Could not enable concurrent database reads')
    (directory/'lab-url.txt').write_text('http://127.0.0.1:18766/demo/index.html#token='+token,encoding='utf-8')
    print('Memory Planet: http://127.0.0.1:18766/demo/index.html (access link saved under data)',flush=True)
    uvicorn.run('backend.main:app',host='127.0.0.1',port=18766,access_log=False)
