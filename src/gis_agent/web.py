import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from typing import Literal
from .config import ROOT, Settings
from .data import list_tasks, load_task, save_json

app = FastAPI(title='矢量空间分析智能体', docs_url='/api/docs')
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
STATIC = ROOT / 'static'
app.mount('/static', StaticFiles(directory=str(STATIC)), name='static')
lock = threading.Lock()
active = {}


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def run_folder(run_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise HTTPException(404, '运行不存在')
    folder = ROOT / 'runs' / run_id
    if not folder.is_dir():
        raise HTTPException(404, '运行不存在')
    return folder


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'provider': Settings.load().public(),
            'runtime': 'QGIS', 'active_run': next(iter(active), None)}


@app.get('/api/tasks')
def tasks():
    return list_tasks()


@app.get('/api/runs')
def runs():
    results = []
    for path in sorted((ROOT / 'runs').glob('*/run.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        meta = read_json(path)
        if meta and not meta['run_id'].startswith('test-'):
            results.append({**meta, 'score': read_json(path.parent / 'score.json'),
                            'result': read_json(path.parent / 'result.json')})
    return results


@app.get('/api/runs/{run_id}')
def run_detail(run_id):
    folder = run_folder(run_id)
    events = []
    path = folder / 'events.jsonl'
    if path.exists():
        for line in path.read_text().splitlines()[-100:]:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return {'run': read_json(folder / 'run.json', {'status': 'starting'}),
            'result': read_json(folder / 'result.json'),
            'score': read_json(folder / 'score.json'), 'events': events}


@app.get('/api/runs/{run_id}/artifacts/{name}')
def artifact(run_id, name):
    if name not in {'map.png', 'zones.geojson', 'selected.geojson', 'overlay.geojson', 'results.csv', 'results.gpkg'}:
        raise HTTPException(404, '成果不存在')
    path = run_folder(run_id) / 'artifacts' / name
    if not path.is_file():
        raise HTTPException(404, '成果尚未生成')
    return FileResponse(path)


class RunInput(BaseModel):
    task_id: Literal['T1', 'T2', 'T3', 'T4', 'T5', 'T6'] = 'T1'
    group: Literal['A', 'B', 'C'] = 'C'
    mode: Literal['offline', 'live'] = 'offline'
    prompt: str | None = Field(default=None, max_length=4000)


@app.post('/api/run')
def launch(body: RunInput, request: Request):
    # 浏览器跨站请求不得触发本地付费运行。
    origin = request.headers.get('origin')
    if origin and urlparse(origin).netloc != request.headers.get('host'):
        raise HTTPException(403, '不允许跨站启动运行')
    if body.mode == 'live' and not Settings.load().api_key:
        raise HTTPException(400, '模型服务尚未配置')
    load_task(body.task_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, '已有任务正在运行，请等待结束')
    import uuid
    run_id = 'web-' + uuid.uuid4().hex[:12]
    folder = ROOT / 'runs' / run_id
    folder.mkdir(parents=True)
    command = [sys.executable, '-m', 'gis_agent.cli', 'run', '--task', body.task_id,
               '--group', body.group, '--mode', body.mode, '--run-id', run_id]
    if body.prompt:
        # 临时任务文本不暴露参考文件；只写用户文本。
        prompt_path = folder / 'input.txt'
        prompt_path.write_text(body.prompt)
        command += ['--prompt-file', str(prompt_path)]
    try:
        log = (folder / 'worker.log').open('w')
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log)
    except Exception:
        lock.release()
        raise HTTPException(500, '无法启动 GIS 进程')
    active[run_id] = process

    def wait():
        try:
            process.wait(timeout=620)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        finally:
            meta = read_json(folder / 'run.json')
            if meta is None:
                meta = {'run_id': run_id, 'task_id': body.task_id, 'group': body.group,
                        'mode': body.mode, 'status': 'failed', 'error': {'message': '运行初始化失败'}}
                save_json(folder / 'run.json', meta)
            elif meta['status'] == 'running':
                meta['status'] = 'failed'
                meta['error'] = {'message': '工作进程退出或超时，原记录已保留'}
                save_json(folder / 'run.json', meta)
            log.close()
            active.pop(run_id, None)
            lock.release()
    threading.Thread(target=wait, daemon=True).start()
    return {'run_id': run_id}
