"""Read-only deployed-service checks; prints status/counts, never credentials."""
import hashlib
import json
import os
from pathlib import Path
import httpx
from .cloud import KS3, starflow_key
from .store import ROOT


def main():
    token=(ROOT/'data'/'staging-token').read_text(encoding='utf-8').strip()
    with httpx.Client(timeout=30,follow_redirects=False) as client:
        unauth=client.get('http://127.0.0.1:18766/courses')
        courses=client.get('http://127.0.0.1:18766/courses',headers={'Authorization':'Bearer '+token})
        models=client.get('https://kspmas.ksyun.com/v1/models',headers={'Authorization':'Bearer '+starflow_key()})
    assert unauth.status_code==401 and courses.status_code==200 and models.status_code==200
    manifest=next((ROOT/'data'/'runs').glob('*/manifest.json'))
    archive=json.loads(manifest.read_text(encoding='utf-8'))['archive']
    content=KS3()._request('GET',archive['key'])
    assert hashlib.sha256(content).hexdigest()==archive['sha256']
    print(json.dumps({'unauthenticated':401,'course_count':len(courses.json()),
                      'model_count':len(models.json()['data']),'ks3_hash_verified':True}))


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit('Cloud check failed: '+type(error).__name__) from None
