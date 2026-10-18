"""Portable settings contain relative paths; encrypted secrets live separately."""
import json,os,threading,uuid
from pathlib import Path
from .store import ROOT
LOCK=threading.RLock()
PATH_KEYS=('starflow_key_file','wps_session_file','asr_model_path')
def load():
    path=ROOT/'data/local-settings.json'
    if not path.exists():return {}
    result=json.loads(path.read_text(encoding='utf-8'))
    for key in PATH_KEYS:
        if result.get(key):result[key]=str((ROOT/result[key]).resolve())
    result['asr_dll_dirs']=[str((ROOT/p).resolve()) for p in result.get('asr_dll_dirs',[])]
    return result
def ready():
    from .cloud import read_secret_file
    try:
        s=load();return bool(s.get('configured') and read_secret_file(s['starflow_key_file']))
    except (OSError,ValueError,KeyError):return False
def configure():
    s=load()
    for key,env in {'starflow_key_file':'ZHIWEN_STARFLOW_KEY_FILE','generate_model':'ZHIWEN_GENERATE_MODEL',
      'extract_model':'ZHIWEN_EXTRACT_MODEL','review_model':'ZHIWEN_REVIEW_MODEL','grade_model':'ZHIWEN_GRADE_MODEL',
      'daily_generate_model':'ZHIWEN_DAILY_GENERATE_MODEL','grade_review_model':'ZHIWEN_GRADE_REVIEW_MODEL'}.items():
        if s.get(key):os.environ[env]=s[key]
    os.environ.pop('ZHIWEN_STARFLOW_KEY',None)
    os.environ['ZHIWEN_MODEL_PROVIDER']='starflow'
    os.environ['ZHIWEN_ENABLE_CLOUD_ARCHIVE']='0'
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'browsers')
    return s
def save(api_key='',wps_sid='',clear_wps=False):
    from .credentials import seal
    from .cloud import read_secret_file
    api_key=api_key.strip();wps_sid=wps_sid.strip().strip('"\'')
    if wps_sid.startswith('wps_sid='):wps_sid=wps_sid.split('=',1)[1].split(';',1)[0]
    if api_key and ('*' in api_key or any(c.isspace() for c in api_key) or len(api_key)>4096):raise ValueError('API KEY格式不正确')
    if wps_sid and (any(c.isspace() or c==';' for c in wps_sid) or len(wps_sid)>8192):raise ValueError('请仅填写wps_sid值')
    with LOCK:
        previous=load()
        if not api_key:
            try:read_secret_file(previous['starflow_key_file'])
            except (OSError,ValueError,KeyError):raise ValueError('首次使用请填写星流API KEY') from None
        folder=ROOT/'data/private';folder.mkdir(parents=True,exist_ok=True)
        pending=[];settings={k:v for k,v in previous.items() if k not in (*PATH_KEYS,'asr_dll_dirs')}
        for field,value in (('starflow_key_file',api_key),('wps_session_file',wps_sid)):
            if value:
                p=folder/(uuid.uuid4().hex+'.secret');p.write_bytes(seal(value));pending.append(p)
                settings[field]=p.relative_to(ROOT).as_posix()
            elif previous.get(field):settings[field]=Path(previous[field]).relative_to(ROOT).as_posix()
        if clear_wps:settings.pop('wps_session_file',None)
        settings.update(configured=True,storage='local',model_provider='starflow',generate_model='mimo-v2.6-pro',extract_model='mimo-v2.6-pro',
            review_model='glm-5.3',grade_model='mimo-v2.6-pro',daily_generate_model='mimo-v2.6-pro',grade_review_model='glm-5.3',
            asr_model_path='models/faster-whisper-base',asr_dll_dirs=['runtime/native'],
            asr_model_sha256='d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9')
        target=ROOT/'data/local-settings.json';temp=target.with_suffix('.tmp')
        try:
            temp.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(target)
        except Exception:
            for p in pending:p.unlink(missing_ok=True)
            raise
        configure()
        active={settings.get('starflow_key_file'),settings.get('wps_session_file')}
        for p in folder.glob('*.secret'):
            if p.relative_to(ROOT).as_posix() not in active:p.unlink(missing_ok=True)
    return status()
def status():
    from .cloud import read_secret_file
    s=load();wps=False
    try:wps=bool(read_secret_file(s['wps_session_file']))
    except (OSError,ValueError,KeyError):pass
    return {'configured':ready(),'wps_configured':wps,'version':'0.0.2-portable'}
