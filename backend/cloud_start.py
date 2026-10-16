"""Private cloud staging, accessed through SSH forwarding until production auth exists."""
import os
import secrets
from pathlib import Path
import uvicorn


if __name__ == '__main__':
    root=Path(__file__).resolve().parents[1]
    token_file=root/'data'/'staging-token'
    token_file.parent.mkdir(exist_ok=True)
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(32),encoding='utf-8')
        token_file.chmod(0o600)
    os.environ['ZHIWEN_LOCAL_TOKEN']=token_file.read_text(encoding='utf-8').strip()
    uvicorn.run('backend.main:app',host='127.0.0.1',port=18766,access_log=False)
