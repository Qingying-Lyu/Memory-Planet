import json,os,sqlite3,tempfile,time,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from . import maintenance as m
class MaintenanceTests(unittest.TestCase):
 def test_automatic_dedup_rotation_and_manual_protection(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'data/materials').mkdir(parents=True)
   with sqlite3.connect(root/'data/learning.sqlite3') as db:db.execute('create table test(value)')
   db.close()
   note=root/'data/materials/note.md';note.write_text('initial')
   with patch.object(m,'ROOT',root),patch.object(m,'BACKUPS',root/'backups'):
    manual=m.backup()
    self.assertTrue(m.backup(automatic=True)['skipped'])
    self.assertEqual(len(m.backups()),1)
    automatic=[]
    for index in range(4):
     note.write_text(str(index));automatic.append(m.backup(automatic=True)['name'])
    names={item['name'] for item in m.backups()}
    self.assertIn(manual['name'],names)
    self.assertNotIn(automatic[0],names)
    self.assertTrue(set(automatic[1:]).issubset(names))
    self.assertTrue(m.backup(automatic=True)['skipped'])
    note.write_text('changed')
    m.backup()
    self.assertTrue(set(automatic[1:]).issubset({item['name'] for item in m.backups()}))
    self.assertFalse(list((root/'backups').glob('*.tmp')))
    self.assertFalse(list((root/'backups').glob('*.sqlite3')))
 def test_failed_verification_does_not_rotate_backups(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'data').mkdir()
   with sqlite3.connect(root/'data/learning.sqlite3') as db:db.execute('create table test(value)')
   db.close()
   with patch.object(m,'ROOT',root),patch.object(m,'BACKUPS',root/'backups'):
    original=m.backup()
    with patch.object(m,'verify',side_effect=ValueError('invalid')):
     with self.assertRaises(ValueError):m.backup(automatic=True)
    self.assertEqual([item['name'] for item in m.backups()],[original['name']])
    self.assertFalse(list((root/'backups').glob('*.tmp')))
 def test_backup_restore_and_cleanup_protection(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'data/qa').mkdir(parents=True);(root/'data/materials').mkdir();(root/'data/runs/accepted').mkdir(parents=True)
   with sqlite3.connect(root/'data/learning.sqlite3') as db:db.execute('create table test(value)');db.execute("insert into test values('original')")
   db.close()
   (root/'data/materials/note.md').write_text('source',encoding='utf-8')
   qa=root/'data/qa/old.png';qa.write_bytes(b'old');os.utime(qa,(time.time()-40*86400,)*2)
   final=root/'data/runs/accepted/package.json';final.write_text('{}')
   (root/'data/lab-url.txt').write_text('SECRET-TOKEN')
   with patch.object(m,'ROOT',root),patch.object(m,'BACKUPS',root/'backups'):
    backup=m.backup();inventory=m.verify(root/'backups'/backup['name'])
    self.assertNotIn('data/lab-url.txt',inventory)
    plan=m.cleanup_preview();self.assertEqual([f['path'] for f in plan['files']],['data/qa/old.png'])
    m.cleanup();self.assertFalse(qa.exists());self.assertTrue(final.exists())
    (root/'data/materials/note.md').write_text('changed');(root/'data/runs/extra').mkdir()
    with patch('socket.socket') as sock:
     sock.return_value.__enter__.return_value.connect_ex.return_value=1
     result=m.restore(backup['name'])
    self.assertEqual((root/'data/materials/note.md').read_text(),'source')
    self.assertFalse((root/'data/runs/extra').exists())
    self.assertTrue((Path(result['recovery'])/'data/runs/extra').exists())
 def test_corrupt_archive_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'bad.zip'
   with zipfile.ZipFile(p,'w') as z:
    z.writestr('inventory.json',json.dumps({'sha256':{'../escape':'bad'}}));z.writestr('../escape','x')
   with self.assertRaises(ValueError):m.verify(p)
