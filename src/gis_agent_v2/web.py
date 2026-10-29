"""本机地图工作台；GIS 在独立进程执行，凭据不进入前端。"""
import json,re,subprocess,sys,threading,uuid
from pathlib import Path
from urllib.parse import urlparse
from typing import Literal
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel,Field,ConfigDict
from pydantic import SecretStr
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from .config import ROOT,V2,Settings,BUDGET,GROUPS
from .credentials import CredentialError, store, save_key, clear_key
from .provider import check_connection
from .data import list_tasks,load_task,request_task,save_json
from .tools import TOOLS

app=FastAPI(title='城市空间分析 Agent · 研究工作台',docs_url='/api/docs')
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
app.mount('/static',StaticFiles(directory=str(V2/'static')),name='static')
lock=threading.Lock();active={}


@app.middleware('http')
async def protect_settings(request, call_next):
    if request.url.path.startswith('/api/settings'):
        if request.method != 'GET':
            origin = request.headers.get('origin')
            if (request.headers.get('x-gis-request') != 'settings' or
                request.headers.get('sec-fetch-site') == 'cross-site' or
                (origin and origin != 'http://' + request.headers.get('host', ''))):
                return JSONResponse({'detail': '不允许跨站修改模型设置。'}, status_code=403,
                                    headers={'Cache-Control': 'no-store'})
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def safe_validation(request, exc):
    if request.url.path.startswith('/api/settings'):
        return JSONResponse({'detail': '配置格式无效，请检查Key输入。'}, status_code=422,
                            headers={'Cache-Control': 'no-store'})
    return await request_validation_exception_handler(request, exc)


class KeyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: SecretStr | None = None


def checked_key(body, allow_saved=False):
    key = body.key.get_secret_value() if body.key else ''
    if not key and allow_saved:
        key = Settings.load().api_key
    if not 10 <= len(key) <= 512 or not key.isascii() or any(c.isspace() or not c.isprintable() for c in key):
        raise HTTPException(400, '请输入有效的DeepSeek Key，不要包含空格或换行。')
    return key


@app.get('/api/settings')
def model_settings():
    settings = Settings.load()
    return {**settings.public(), 'endpoint': settings.base_url, 'source': settings.source,
            'storage_available': store.available(), 'storage': '系统凭据存储'}


@app.put('/api/settings')
def update_settings(body: KeyInput):
    key = checked_key(body)
    try:
        save_key(key)
    except CredentialError as exc:
        raise HTTPException(503, str(exc)) from None
    except OSError:
        raise HTTPException(503, '无法保存配置状态，请检查本机用户目录权限。') from None
    return {'ok': True, 'configured': True, 'message': '已保存，下一次分析将使用新Key。'}


@app.delete('/api/settings')
def delete_settings():
    try:
        clear_key()
    except CredentialError as exc:
        raise HTTPException(503, str(exc)) from None
    except OSError:
        raise HTTPException(503, '无法保存配置状态，请检查本机用户目录权限。') from None
    return {'ok': True, 'configured': False, 'message': '已删除Key，仍可使用离线演示。'}


@app.post('/api/settings/test')
def test_settings(body: KeyInput):
    return check_connection(checked_key(body, allow_saved=True))

def read(path,default=None):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def folder(run_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,90}',run_id):raise HTTPException(404,'运行不存在')
    p=V2/'runs'/run_id
    if not p.is_dir() and run_id not in active:raise HTTPException(404,'运行不存在')
    return p

@app.get('/')
def index():return FileResponse(V2/'static/index.html')

@app.get('/api/health')
def health():return {'status':'ok','provider':Settings.load().public(),'active_run':next(iter(active),None),'budget':BUDGET,'groups':GROUPS}

@app.get('/api/tasks')
def tasks():return list_tasks()

@app.get('/api/schema')
def schema():return {'tools':TOOLS,'groups':GROUPS,'input':'任务合同 + 图层画像；仅句柄传入模型','output':['GeoJSON','CSV','GeoPackage','PNG','plan.json','events.jsonl','lineage.json','checks.json'],'budget':BUDGET}

@app.get('/api/regions/{region}/{name}')
def region_file(region,name):
    if region not in {t['region'] for t in list_tasks()} or name not in {'profile.json','display-zones.geojson','display-water.geojson','display-roads.geojson','display-schools.geojson','display-stations.geojson','display-hospitals.geojson','display-parks.geojson','display-residential.geojson'}:raise HTTPException(404,'图层不存在')
    return FileResponse(V2/'data/regions'/region/name)

@app.get('/api/runs')
def runs():
    entries=[]
    for path in sorted((V2/'runs').glob('*/run.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        meta=read(path)
        if meta and meta.get('purpose')!='test':entries.append({**meta,'score':read(path.parent/'score.json')})
    return entries[:160]

@app.get('/api/runs/{run_id}')
def detail(run_id):
    p=folder(run_id);events=[]
    if (p/'events.jsonl').exists():
        for line in (p/'events.jsonl').read_text(encoding='utf-8').splitlines():
            try:events.append(json.loads(line))
            except ValueError:pass
    return {'run':read(p/'run.json',{'run_id':run_id,'status':'starting'}),'task':read(p/'task.json'),
            'profile':read(p/'input_profile.json'),'plan':read(p/'plan.json'),'lineage':read(p/'lineage.json'),
            'checks':read(p/'checks.json',[]),'events':events,'result':read(p/'result.json'),'score':read(p/'score.json')}

@app.get('/api/runs/{run_id}/layers/{handle}')
def snapshot(run_id,handle):
    if not re.fullmatch(r'layer_\d+',handle):raise HTTPException(404,'图层不存在')
    p=folder(run_id)/'layers'/(handle+'.geojson')
    if not p.is_file():raise HTTPException(404,'该步骤产生的是统计表，无地图几何')
    return FileResponse(p)

@app.get('/api/runs/{run_id}/artifacts/{name}')
def artifact(run_id,name):
    if name not in {'map.png','zones.geojson','selected.geojson','overlay.geojson','results.csv','results.gpkg'}:raise HTTPException(404,'成果不存在')
    p=folder(run_id)/'artifacts'/name
    if not p.is_file():raise HTTPException(404,'成果尚未生成')
    return FileResponse(p,filename=name)

@app.get('/api/experiment')
def experiment():return read(V2/'reports/formal/summary.json',{'status':'pending','note':'离线演示、开发试跑与正式模型实验分别记录'})

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,70}$')
    group:Literal['A','B','C']='C'
    mode:Literal['offline','live']='offline'
    prompt:str|None=Field(default=None,max_length=4000)
    distance_m:float|None=Field(default=None,gt=0,le=5000)
    hospital_distance_m:float|None=Field(default=None,gt=0,le=5000)

@app.post('/api/run')
def launch(body:Input,request:Request):
    origin=request.headers.get('origin')
    if origin and urlparse(origin).netloc!=request.headers.get('host'):raise HTTPException(403,'不允许跨站启动运行')
    settings = Settings.load()
    if body.mode=='live' and not settings.api_key:raise HTTPException(400,'请先到模型设置填写DeepSeek Key。')
    try:task=request_task(body.task_id,body.prompt,body.distance_m,body.hospital_distance_m)
    except (OSError,ValueError):raise HTTPException(400,'任务或输入参数无效')
    if not lock.acquire(False):raise HTTPException(409,'已有运行，结束后再启动')
    run_id='webv2-'+uuid.uuid4().hex[:12]
    (V2/'requests').mkdir(exist_ok=True);(V2/'logs').mkdir(exist_ok=True)
    task_path=V2/'requests'/(run_id+'.json');save_json(task_path,task)
    log=(V2/'logs'/(run_id+'.log')).open('w', encoding='utf-8')
    command=[sys.executable,'-m','gis_agent_v2.cli','run','--task',body.task_id,'--task-file',str(task_path),
             '--mode',body.mode,'--group',body.group,'--run-id',run_id,'--purpose','demo']
    try:process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,env=settings.worker_environment())
    except Exception:
        log.close();lock.release();raise HTTPException(500,'启动失败')
    active[run_id]=process
    def wait():
        try:process.wait(timeout=620)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        finally:
            p=V2/'runs'/run_id;p.mkdir(exist_ok=True);meta=read(p/'run.json')
            if not meta:meta={'run_id':run_id,'task_id':task['id'],'title':task['title'],'group':body.group,'mode':body.mode,'purpose':'demo','status':'failed','error':{'message':'运行初始化失败'}}
            if meta.get('status') in {'starting','running'}:meta.update(status='failed',error={'message':'工作进程退出或超时，轨迹保留'})
            save_json(p/'run.json',meta);log.close();active.pop(run_id,None);lock.release()
    threading.Thread(target=wait,daemon=True).start()
    return {'run_id':run_id}
