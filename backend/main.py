import os,secrets,json
from fastapi import FastAPI,Depends,HTTPException,BackgroundTasks
from fastapi.security import HTTPAuthorizationCredentials,HTTPBearer
from .store import connect
from .store import ROOT
from . import practice
from .cloud import CloudError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from . import library, daily, wps, learning
from contextlib import asynccontextmanager
import threading

@asynccontextmanager
async def lifespan(app):
    stop = threading.Event()
    from .settings import ready
    def start_when_ready():
        while not stop.is_set():
            if ready():
                start_workers()
                return
            stop.wait(1)
    def start_workers():
        from .material_versions import bootstrap
        bootstrap()
        from .challenges import worker
        threading.Thread(target=worker,args=(stop,),daemon=True,name='daily-generator').start()
        threading.Thread(target=learning.worker,args=(stop,),daemon=True,name='course-bank-generator').start()
        from .material_versions import worker as material_worker
        threading.Thread(target=material_worker,args=(stop,),daemon=True,name='material-parser').start()
        from . import maintenance
        threading.Thread(target=maintenance.worker,args=(stop,),daemon=True,name='local-maintenance').start()
    if os.environ.get('ZHIWEN_SCHEDULER_ENABLED') == '1':
        threading.Thread(target=start_when_ready,daemon=True,name='configuration-gate').start()
    yield
    stop.set()

app=FastAPI(title='记忆星球 API',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
app.mount('/demo',StaticFiles(directory=ROOT/'demo'),name='existing-demo')
bearer=HTTPBearer(auto_error=False)
def owner(auth:HTTPAuthorizationCredentials=Depends(bearer)):
    token=os.environ.get('ZHIWEN_LOCAL_TOKEN','')
    if not token or auth is None or not secrets.compare_digest(auth.credentials,token):raise HTTPException(401,'需要本地访问令牌')
    return 'local-owner'
@app.get('/health')
def health():
    from .settings import ready
    return {'status':'ok','mode':'portable','version':'0.0.2-portable','ai_configured':ready()}

@app.middleware('http')
async def setup_gate(request,call_next):
    from .settings import ready
    path=request.url.path
    if path.startswith(('/courses','/daily','/practice','/runs','/overview','/backups')) and not ready():
        expected=os.environ.get('ZHIWEN_LOCAL_TOKEN','')
        if not expected or not secrets.compare_digest(request.headers.get('authorization',''),'Bearer '+expected):
            return JSONResponse(status_code=401,content={'detail':'需要本地访问令牌'})
        return JSONResponse(status_code=409,content={'detail':'请先完成连接设置'})
    response=await call_next(request)
    if path.startswith('/setup') or path=='/client-state':
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
    return response

@app.get('/entry')
def entry():
    from .settings import ready
    return RedirectResponse('/demo/index.html' if ready() else '/setup',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
@app.get('/setup')
def setup_page():return FileResponse(ROOT/'demo/setup.html')
@app.get('/setup/status')
def setup_status(user=Depends(owner)):
    from .settings import status
    return status()
class SetupInput(BaseModel):
    api_key:str=Field(default='',max_length=4096)
    wps_sid:str=Field(default='',max_length=8192)
    clear_wps:bool=False
@app.post('/setup/save')
def setup_save(body:SetupInput,user=Depends(owner)):
    from .settings import save
    from .challenges import LOCK
    from .material_versions import LOCK as material_lock
    if not learning.GENERATION_LOCK.acquire(blocking=False):raise HTTPException(409,'材料或题目处理中，请稍后再修改连接设置')
    try:
        if not material_lock.acquire(blocking=False):raise HTTPException(409,'材料读取中，请稍后再修改连接设置')
        try:
            if not LOCK.acquire(blocking=False):raise HTTPException(409,'每日题处理中，请稍后再修改连接设置')
            try:return save(body.api_key,body.wps_sid,body.clear_wps)
            finally:LOCK.release()
        finally:material_lock.release()
    finally:learning.GENERATION_LOCK.release()
@app.post('/setup/check')
def setup_check(user=Depends(owner)):
    from .cloud import starflow_key
    import httpx
    try:
        r=httpx.get('https://kspmas.ksyun.com/v1/models',headers={'Authorization':'Bearer '+starflow_key()},timeout=20)
        if r.status_code==200:return {'ok':True,'message':'星流连接及密钥认证通过；具体模型权限与余额以实际调用为准。'}
        return {'ok':False,'message':'星流认证失败，请检查KEY。' if r.status_code in (401,403) else '暂未完成连接检查，可稍后重试。'}
    except Exception:return {'ok':False,'message':'无法连接星流，请检查网络或稍后重试。'}

@app.exception_handler(CloudError)
async def cloud_error(request, exc):
    return JSONResponse(status_code=502,content={'detail':'模型或存储调用失败，请稍后重试；本次未按零分处理'})

@app.exception_handler(LookupError)
async def missing(request, exc):
    return JSONResponse(status_code=404,content={'detail':'未找到可访问的记录'})

from .material_versions import MaterialUpdateError

@app.exception_handler(MaterialUpdateError)
async def material_update_error(request, exc):
    return JSONResponse(status_code=409,content={'detail':str(exc)})

@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse(status_code=422,content={'detail':'提交或模型返回的数据未通过校验，本次未计分'})

@app.get('/lab')
def lab():
    return RedirectResponse('/demo/index.html',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

@app.get('/runs')
def runs(user=Depends(owner)):
    result=[]
    for file in (ROOT/'data'/'runs').glob('*/manifest.json'):
        row=json.loads(file.read_text(encoding='utf-8'))
        if row.get('owner')==user:
            result.append({k:row.get(k) for k in ('id','state','started_at','accepted','held','coverage')})
    return sorted(result,key=lambda r:r['started_at'],reverse=True)

@app.get('/runs/{run_id}')
def run_preview(run_id:str,user=Depends(owner)):
    data=practice.package(run_id,user)
    return {'run_id':run_id,'course_id':data['course_id'],'coverage':data['coverage'],
            'framework':data['framework'],'held_count':len(data['held']),
            'questions':[{k:q[k] for k in ('id','kind','prompt','concept_id')} for q in data['questions']]}

@app.post('/runs/{run_id}/questions/{question_id}/start')
def start_practice(run_id:str,question_id:str,user=Depends(owner)):
    from .bank import start_review
    return start_review(run_id,question_id,user)

class Answer(BaseModel):
    answer:str=Field(min_length=1,max_length=6000)

@app.post('/practice/{session_id}/submit')
def submit_practice(session_id:str,body:Answer,user=Depends(owner)):
    return practice.submit(session_id,body.answer,user)

@app.post('/practice/{session_id}/reveal')
def reveal_practice(session_id:str,user=Depends(owner)):
    return practice.reveal(session_id,user)
@app.get('/courses')
def courses(user=Depends(owner)):
    return learning.list_courses(user)
@app.get('/courses/{course_id}/sources')
def sources(course_id:str,user=Depends(owner)):
    with connect() as c:
        if not c.execute('SELECT 1 FROM courses WHERE id=? AND owner=?',(course_id,user)).fetchone():raise HTTPException(404)
        return [dict(r) for r in c.execute('SELECT id,title,location,status,details FROM sources WHERE course_id=?',(course_id,))]

class ScopeBody(BaseModel):
    mode: str
    topics: list[str] = Field(min_length=1, max_length=100)
    note: str = Field(default='', max_length=20000)

@app.put('/courses/{course_id}/scope')
def course_scope(course_id:str,body:ScopeBody,user=Depends(owner)):
    return library.set_scope(course_id,user,body.mode,body.topics,body.note)

@app.get('/courses/{course_id}/evidence')
def course_evidence(course_id:str,user=Depends(owner)):
    return library.evidence(course_id,user)

@app.post('/courses/{course_id}/ask')
def course_ask(course_id:str,body:Answer,user=Depends(owner)):
    return library.explain(course_id,user,body.answer)

@app.post('/runs/{run_id}/daily')
def daily_challenge(run_id:str,user=Depends(owner)):
    raise HTTPException(410,'每日挑战已改为每天10:30推荐选择题，请使用 /daily')

@app.post('/courses/{course_id}/daily')
def course_daily_challenge(course_id:str,user=Depends(owner)):
    from .challenges import board
    result = board(user)
    result['questions'] = [q for q in result['questions'] if q['course_id'] == course_id]
    return result

@app.get('/daily')
def daily_board(user=Depends(owner)):
    from .challenges import board
    return board(user)

class AdditionalRound(BaseModel):
    request_id:str=Field(min_length=16,max_length=80,pattern=r'^[A-Za-z0-9-]+$')

@app.post('/daily/again')
def additional_challenge(body:AdditionalRound,user=Depends(owner)):
    from .challenges import again
    return again(user,body.request_id)

@app.get('/bank')
def question_bank(user=Depends(owner)):
    from . import bank
    bank.sync(user)
    return bank.public_entries(user)

@app.post('/bank/{run_id}/{question_id}/start')
def bank_review(run_id:str,question_id:str,user=Depends(owner)):
    from .bank import start_review
    return start_review(run_id,question_id,user)

@app.get('/overview')
def overview(user=Depends(owner)):
    from . import bank
    with connect() as db:
        courses = [dict(r) for r in db.execute('SELECT id,title FROM courses WHERE owner=?',(user,))]
    return [{'id':c['id'],'title':c['title'],'mastery':bank.mastery(user,c['id'])} for c in courses]

class ReviewPlan(BaseModel):
    course_id:str
    count:int=Field(default=30,ge=1,le=30)

@app.post('/review/plan')
def plan_review(body:ReviewPlan,user=Depends(owner)):
    from .activity import register
    plan=learning.prepare_review(body.course_id,user,body.count)
    if plan['questions']:plan['activity_id']=register(user,plan['questions'])
    return plan

@app.get('/integrations/wps/callback')
def wps_verify():
    return {'result':'ok'}

@app.post('/integrations/wps/callback')
def wps_callback(body:dict):
    try:
        return wps.enqueue(body)
    except PermissionError:
        raise HTTPException(403,'回调身份、课程范围或时效未通过验证') from None
    except (KeyError, OSError):
        raise HTTPException(503,'WPS接入尚未配置') from None
@app.get('/sources/{source_id}/blocks')
def blocks(source_id:str,offset:int=0,limit:int=50,user=Depends(owner)):
    if offset<0 or not 1<=limit<=100:raise HTTPException(422)
    with connect() as c:
        allowed=c.execute('SELECT 1 FROM sources s JOIN courses c ON c.id=s.course_id WHERE s.id=? AND c.owner=?',(source_id,user)).fetchone()
        if not allowed:raise HTTPException(404)
        return [dict(r) for r in c.execute('SELECT ordinal,kind,text FROM blocks WHERE source_id=? ORDER BY ordinal LIMIT ? OFFSET ?',(source_id,limit,offset))]

class CourseCreate(BaseModel):
    title:str=Field(min_length=1,max_length=200)
    mode:str
    note:str=Field(default='',max_length=20000)
class MaterialCreate(BaseModel):
    title:str=Field(min_length=1,max_length=200)
    kind:str
    text:str=Field(default='',max_length=1000000)
    content:str=Field(default='',max_length=28000000)
    filename:str=Field(default='',max_length=255)
class CompleteCourse(BaseModel):
    confirm_scope:bool=False
@app.post('/courses')
def create_course(body:CourseCreate,user=Depends(owner)):
    return learning.create(user,body.title,body.mode,body.note)
@app.get('/courses/{course_id}/materials')
def course_materials(course_id:str,user=Depends(owner)):
    return learning.materials(course_id,user)
@app.post('/courses/{course_id}/materials')
def add_material(course_id:str,body:MaterialCreate,tasks:BackgroundTasks,user=Depends(owner)):
    result=learning.add(course_id,user,**body.model_dump())
    from .material_versions import process
    tasks.add_task(process,result['material_id'],user)
    return result
@app.post('/courses/{course_id}/complete')
def complete_course(course_id:str,body:CompleteCourse,tasks:BackgroundTasks,user=Depends(owner)):
    with connect() as db:library.allowed(db,course_id,user)
    raise HTTPException(410,'课程已取消学完状态，请逐份确认材料已学完')

class RenameCourse(BaseModel):
    title:str=Field(min_length=1,max_length=200)
@app.post('/courses/{course_id}/rename')
def rename_course(course_id:str,body:RenameCourse,user=Depends(owner)):
    return learning.rename(course_id,user,body.title)

class CourseDeletion(BaseModel):
    mode:str=Field(pattern=r'^(course|materials|bank)$')
    material_ids:list[str]=Field(default_factory=list,max_length=200)
    confirm:bool=False

@app.post('/courses/{course_id}/delete-preview')
def preview_course_deletion(course_id:str,body:CourseDeletion,user=Depends(owner)):
    from .deletion import preview
    return preview(course_id,user,body.mode,body.material_ids)

@app.post('/courses/{course_id}/delete')
def delete_course_content(course_id:str,body:CourseDeletion,user=Depends(owner)):
    if not body.confirm:raise HTTPException(400,'请先确认删除范围')
    from .deletion import delete
    return delete(course_id,user,body.mode,body.material_ids)

@app.get('/client-state')
def client_reset_state(user=Depends(owner)):
    path=ROOT/'data/client-state.json'
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'reset_id':secrets.token_hex(16)}),encoding='utf-8')
    return json.loads(path.read_text(encoding='utf-8'))

@app.get('/courses/{course_id}/generation')
def course_generation(course_id:str,user=Depends(owner)):
    return learning.progress(course_id,user)
@app.get('/courses/{course_id}/materials/{material_id}')
def view_material(course_id:str,material_id:str,user=Depends(owner)):
    row,path=learning.material(course_id,material_id,user)
    if row['kind']=='link':return {'kind':'link','url':path.read_text(encoding='utf-8')}
    if path.suffix.lower() in ('.md','.txt'):
        return {'kind':'text','title':row['title'],'text':path.read_text(encoding='utf-8-sig')}
    with connect() as db:
        library.initialize(db)
        rows=db.execute('SELECT ordinal,text FROM library_evidence WHERE document_id=? ORDER BY ordinal',(row['document_id'],)).fetchall()
    return {'kind':'file','title':row['title'],'text':'\n\n'.join(r['text'] for r in rows)}
@app.get('/courses/{course_id}/materials/{material_id}/download')
def download_material(course_id:str,material_id:str,user=Depends(owner)):
    row,path=learning.material(course_id,material_id,user)
    return FileResponse(path,filename=row['title']+path.suffix,media_type='application/octet-stream')

@app.get('/sources/{source_id}/download')
def download_source(source_id:str,user=Depends(owner)):
    from pathlib import Path
    with connect() as db:
        row=db.execute('SELECT s.* FROM sources s JOIN courses c ON c.id=s.course_id WHERE s.id=? AND c.owner=?',(source_id,user)).fetchone()
    if not row:raise LookupError('资料不存在')
    path=Path(row['location']).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():raise LookupError('资料原件未保存到本机')
    return FileResponse(path,filename=path.name,media_type='application/octet-stream')

@app.get('/maintenance')
def maintenance_status(user=Depends(owner)):
    from .maintenance import overview
    return overview()
@app.post('/maintenance/backup')
def maintenance_backup(user=Depends(owner)):
    from .maintenance import backup
    return backup()
class CleanupBody(BaseModel):
    reviewed_audio:bool=False
@app.post('/maintenance/cleanup-preview')
def maintenance_preview(body:CleanupBody,user=Depends(owner)):
    from .maintenance import cleanup_preview
    return cleanup_preview(body.reviewed_audio)
@app.post('/maintenance/cleanup')
def maintenance_cleanup(body:CleanupBody,user=Depends(owner)):
    from .maintenance import cleanup
    return cleanup(body.reviewed_audio)

class ExamNote(BaseModel):
    note:str=Field(default='',max_length=20000)
@app.post('/courses/{course_id}/exam-note')
def save_exam_note(course_id:str,body:ExamNote,user=Depends(owner)):
    raise HTTPException(410,'考试范围备注已取消，请逐份确认材料已学完')
@app.get('/courses/{course_id}/research')
def course_research(course_id:str,user=Depends(owner)):
    from .enrichment import status
    return status(course_id,user)
@app.get('/courses/{course_id}/research-documents')
def course_research_documents(course_id:str,user=Depends(owner)):
    from . import enrichment
    with connect() as db:
        enrichment.initialize(db);library.allowed(db,course_id,user)
        return [dict(r) for r in db.execute('SELECT d.id,d.title,d.origin,x.topic FROM enrichment_documents x JOIN library_documents d ON d.id=x.document_id JOIN course_scopes s ON s.course_id=d.course_id AND s.revision=d.scope_revision WHERE d.course_id=? AND EXISTS(SELECT 1 FROM library_evidence e WHERE e.document_id=d.id AND e.approved=1)',(course_id,))]

@app.get('/courses/{course_id}/research-documents/{document_id}')
def view_research_document(course_id:str,document_id:str,user=Depends(owner)):
    from . import enrichment
    with connect() as db:
        enrichment.initialize(db);library.allowed(db,course_id,user)
        doc=db.execute('SELECT d.* FROM enrichment_documents x JOIN library_documents d ON d.id=x.document_id JOIN course_scopes s ON s.course_id=d.course_id AND s.revision=d.scope_revision WHERE d.course_id=? AND d.id=?',(course_id,document_id)).fetchone()
        if not doc:raise LookupError('资料不可访问')
        rows=db.execute('SELECT text FROM library_evidence WHERE document_id=? AND approved=1 ORDER BY ordinal',(document_id,)).fetchall()
        if not rows:raise LookupError('资料尚未审核')
    return {'title':doc['title'],'origin':doc['origin'],'text':'\n\n'.join(r['text'] for r in rows)}

@app.get('/maintenance/search-health')
def search_health(user=Depends(owner)):
    from .search_service import health
    from .settings import load
    settings=load()
    return {'provider':settings.get('search_provider','public'),'key_configured':bool(settings.get('starflow_key_file') if settings.get('search_provider')=='starflow' else settings.get('search_key_file')),'services':health()}

class MaterialLearned(BaseModel):
    learned:bool=True
    confirm_scope:bool=False
@app.get('/courses/{course_id}/study-materials')
def study_materials(course_id:str,user=Depends(owner)):
    from .material_policy import materials
    return materials(course_id,user)
@app.post('/courses/{course_id}/study-materials/{material_id}/learned')
def mark_material(course_id:str,material_id:str,body:MaterialLearned,tasks:BackgroundTasks,user=Depends(owner)):
    from .material_policy import set_learned
    if body.learned and not body.confirm_scope:raise HTTPException(422,'请确认此材料已学完且属于考试范围')
    if not learning.GENERATION_LOCK.acquire(blocking=False):raise HTTPException(409,'材料或题目处理中，请稍后再修改状态')
    try:result=set_learned(course_id,material_id,user,body.learned)
    finally:learning.GENERATION_LOCK.release()
    learning.enqueue(course_id,user)
    if body.learned:tasks.add_task(learning.generate,course_id,user)
    return result

@app.get('/courses/{course_id}/study-materials/{material_id}/body')
def read_study_material(course_id:str,material_id:str,original:bool=False,user=Depends(owner)):
    from . import material_policy as policy
    with connect() as db:
        policy.initialize(db);rows,_=policy.snapshot(db,course_id,user)
        if material_id not in rows:raise LookupError('材料不存在')
        text=[]
        docs=rows[material_id]['documents']
        if original and db.execute("SELECT 1 FROM sqlite_master WHERE name='material_heads'").fetchone():
            head=db.execute('SELECT document_id FROM material_heads WHERE id=?',(material_id,)).fetchone()
            if head and head[0]:docs=[head[0]]
        for doc in docs:
            text += [r['text'] for r in db.execute('SELECT text FROM library_evidence WHERE document_id=? ORDER BY ordinal',(doc,))]
    return {'title':rows[material_id]['title'],'text':'\n\n'.join(text)}

@app.post('/courses/{course_id}/study-materials/{material_id}/update')
def update_study_material(course_id:str,material_id:str,body:MaterialCreate,tasks:BackgroundTasks,user=Depends(owner)):
    from . import material_versions
    result=material_versions.update(course_id,material_id,user,**body.model_dump())
    tasks.add_task(material_versions.process,material_id,user)
    return result

@app.get('/courses/{course_id}/study-materials/{material_id}/versions')
def material_history(course_id:str,material_id:str,user=Depends(owner)):
    from .material_versions import history
    return history(course_id,material_id,user)

@app.post('/courses/{course_id}/study-materials/{material_id}/retry')
def retry_material(course_id:str,material_id:str,tasks:BackgroundTasks,user=Depends(owner)):
    from .material_versions import retry,process
    result=retry(course_id,material_id,user);tasks.add_task(process,material_id,user);return result

@app.get('/courses/{course_id}/knowledge')
def course_knowledge(course_id:str,user=Depends(owner)):
    from .knowledge import overview
    return overview(course_id,user)

class ConflictResolution(BaseModel):
    confirm:bool=False
@app.post('/courses/{course_id}/knowledge/conflicts/{conflict_id}/resolve')
def resolve_knowledge_conflict(course_id:str,conflict_id:str,body:ConflictResolution,user=Depends(owner)):
    if not body.confirm:raise HTTPException(422,'请确认采用该结论并暂停该争议考点的旧依据')
    from .knowledge import resolve_conflict
    return resolve_conflict(course_id,conflict_id,user)

@app.post('/courses/{course_id}/knowledge/{concept_id}/release-short')
def release_short_reserve(course_id:str,concept_id:str,body:ConflictResolution,user=Depends(owner)):
    if not body.confirm:raise HTTPException(422,'请确认放弃该考点尚未使用的简答预留，已用额度不返还')
    from .material_versions import mutation
    from .question_quota import release_short
    with mutation():
        with connect() as db:return release_short(db,course_id,concept_id,user)

@app.get('/courses/{course_id}/study-materials/{material_id}/alignment')
def media_alignment(course_id:str,material_id:str,user=Depends(owner)):
    from .media_alignment import get
    return get(course_id,material_id,user)[2]

class AlignmentWindow(BaseModel):
    id:str=Field(max_length=30)
    approved:bool
    text:str=Field(max_length=50000)
class AlignmentReview(BaseModel):
    windows:list[AlignmentWindow]=Field(max_length=10000)

@app.post('/courses/{course_id}/study-materials/{material_id}/alignment')
def review_alignment(course_id:str,material_id:str,body:AlignmentReview,tasks:BackgroundTasks,user=Depends(owner)):
    from .media_alignment import approve
    from .material_versions import process
    result=approve(course_id,material_id,user,[w.model_dump() for w in body.windows]);tasks.add_task(process,material_id,user);return result

@app.get('/courses/{course_id}/study-materials/{material_id}/alignment/asset/{name}')
def alignment_asset(course_id:str,material_id:str,name:str,user=Depends(owner)):
    from .media_alignment import get
    _,folder,data=get(course_id,material_id,user)
    names={w.get('frame') for w in data['windows']}|{data['audio']}
    if name not in names or '/' in name or '\\' in name:raise LookupError('资源不可访问')
    path=(folder/name).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():raise LookupError('资源不存在')
    return FileResponse(path,media_type='audio/wav' if name==data['audio'] else 'image/jpeg')

@app.get('/activity')
def activity_history(user=Depends(owner)):
    from .activity import history
    return history(user)

class ReviewActivityCompletion(BaseModel):
    session_ids:list[str]=Field(min_length=1,max_length=30)

@app.post('/activity/review/{round_id}/complete')
def complete_review_activity(round_id:str,body:ReviewActivityCompletion,user=Depends(owner)):
    from .activity import complete
    return complete(user,round_id,body.session_ids)

@app.post('/courses/{course_id}/study-materials/{material_id}/rename')
def rename_study_material(course_id:str,material_id:str,body:RenameCourse,user=Depends(owner)):
    from .material_versions import rename
    return rename(course_id,material_id,user,body.title)
