from pathlib import Path
import hashlib, json, sqlite3, zipfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'learning.sqlite3'
W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def connect(path=DB):
    path.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path,factory=ClosingConnection); c.row_factory=sqlite3.Row
    c.executescript('''CREATE TABLE IF NOT EXISTS courses(id TEXT PRIMARY KEY,owner TEXT NOT NULL,title TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,course_id TEXT NOT NULL,title TEXT,location TEXT,status TEXT,hash TEXT,details TEXT);
    CREATE TABLE IF NOT EXISTS blocks(source_id TEXT,ordinal INTEGER,kind TEXT,text TEXT,PRIMARY KEY(source_id,ordinal));
    CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,owner TEXT,concept TEXT,date TEXT,q REAL,before REAL,after REAL,n INTEGER,rule TEXT,UNIQUE(owner,concept,date));''')
    return c

def extract_docx(path):
    with zipfile.ZipFile(path) as z:
        entry=z.getinfo('word/document.xml')
        if entry.file_size>20_000_000: raise ValueError('Document XML too large')
        root=ET.fromstring(z.read(entry)); body=root.find(W+'body'); blocks=[]
        for i,node in enumerate(body,1):
            if node.tag==W+'tbl':
                rows=[' | '.join(''.join(t.text or '' for t in cell.iter(W+'t')) for cell in row.findall(W+'tc')) for row in node.findall(W+'tr')]
                text='\n'.join(rows); kind='table'
            else:
                text=''.join(t.text or '' for t in node.iter(W+'t')); kind='paragraph'
            if text.strip(): blocks.append((i,kind,text))
        return blocks,{'images':sum(1 for n in z.namelist() if n.startswith('word/media/')),'coverage':'仅正文段落和表格文本；图片、文本框阅读顺序、页眉页脚及图示含义待核对；位置使用正文块序号，不伪造页码。'}

def import_sample(path=DB):
    doc=ROOT/'课程'/'sample.docx'
    blocks,details=extract_docx(doc); digest=hashlib.sha256(doc.read_bytes()).hexdigest()
    with connect(path) as c:
        c.execute('INSERT OR IGNORE INTO courses VALUES (?,?,?)',('cdn-edge','local-owner','示例课程'))
        sources=[('sample-link-1','示例链接1','https://example.invalid/material/1'),('sample-link-2','示例链接2','https://example.invalid/material/2'),('sample-link-3','示例链接3','https://example.invalid/material/3')]
        for sid,title,url in sources:
            c.execute('INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?,?)',(sid,'cdn-edge',title,url,'pending_access',None,json.dumps({'reason':'未取得正文，待授权读取或导出'},ensure_ascii=False)))
        c.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?)',('cdn-docx','cdn-edge',doc.name,str(doc),'partial',digest,json.dumps(details,ensure_ascii=False)))
        c.execute('DELETE FROM blocks WHERE source_id=?',('cdn-docx',))
        c.executemany('INSERT INTO blocks VALUES (?,?,?,?)',[('cdn-docx',*b) for b in blocks])
    return {'blocks':len(blocks),'characters':sum(len(b[2]) for b in blocks),**details}

if __name__=='__main__':print(json.dumps(import_sample(),ensure_ascii=False,indent=2))
