import os,tempfile,unittest,zipfile
from unittest.mock import patch
from backend import store
from pathlib import Path
from fastapi.testclient import TestClient
from backend.store import connect,import_sample
from backend.scoring import update,display
from backend.main import app
class Tests(unittest.TestCase):
 def test_scoring(self):
  m=update(None,100,1);self.assertEqual(m,85)
  for n in range(2,101):
   new=update(m,100,n);self.assertTrue(m<new<100);m=new
  self.assertLess(display(m),100)
  self.assertLess(update(95,0,16),95)
 def test_invalid(self):
  for q in [-1,101,float('nan')]:
   with self.assertRaises(ValueError):update(None,q,1)
 def test_import_idempotent(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'db';fixture=Path(d)/'课程'/'直播CDN核心知识体系.docx';fixture.parent.mkdir()
   with zipfile.ZipFile(fixture,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>测试正文</w:t></w:r></w:p></w:body></w:document>')
   with patch.object(store,'ROOT',Path(d)):a=import_sample(p);import_sample(p)
   with connect(p) as c:
    self.assertEqual(c.execute('SELECT count(*) FROM sources').fetchone()[0],4)
    self.assertEqual(c.execute('SELECT count(*) FROM blocks').fetchone()[0],a['blocks'])
    self.assertGreater(a['blocks'],0)
 def test_private_api(self):
  os.environ['ZHIWEN_LOCAL_TOKEN']='test-only-token'
  with TestClient(app) as client:
   self.assertEqual(client.get('/courses').status_code,401)
   self.assertEqual(client.get('/courses',headers={'Authorization':'Bearer wrong'}).status_code,401)
   h={'Authorization':'Bearer test-only-token'}
   self.assertEqual(client.get('/sources/missing/blocks',headers=h).status_code,404)
   self.assertEqual(client.get('/sources/cdn-docx/blocks?limit=500',headers=h).status_code,422)
if __name__=='__main__':unittest.main()
