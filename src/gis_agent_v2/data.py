import json
import re
from pathlib import Path
from .config import ROOT, V2
from gis_agent.data import save_json


def load_task(task_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,70}', task_id):
        raise ValueError('任务 ID 无效')
    return json.loads((V2 / 'data/tasks' / f'{task_id}.json').read_text(encoding='utf-8'))


def list_tasks(split=None):
    items = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((V2 / 'data/tasks').glob('*.json'))]
    return [t for t in items if split is None or t['split'] == split]


def validate_task(task):
    required = {'proximity': {'zones','schools','stations'}, 'coverage': {'zones','parks'},
                'gaps': {'zones','residential','stations','hospitals'}}
    if task.get('family') not in required or not required[task['family']].issubset(task['inputs']):
        raise ValueError('任务类型或输入图层不完整')
    for rel in task['inputs'].values():
        path = (ROOT / rel).resolve()
        if not path.is_relative_to((V2 / 'data').resolve()) or path.suffix != '.geojson' or not path.is_file():
            raise ValueError('仅接受 V2 数据集内的 GeoJSON 图层')


def request_task(task_id, prompt=None, distance_m=None, hospital_distance_m=None):
    task = load_task(task_id)
    if distance_m is not None:
        if not 0 < distance_m <= 5000:
            raise ValueError('公交距离应在 0—5000 米内')
        task['distance_m'] = distance_m
    if hospital_distance_m is not None:
        if not 0 < hospital_distance_m <= 5000:
            raise ValueError('医院距离应在 0—5000 米内')
        task['hospital_distance_m'] = hospital_distance_m
    if prompt:
        # 数值口径以显式参数为准；不静默把自由文本解析成另一个统计合同。
        if len(prompt) > 4000:
            raise ValueError('需求过长')
        task['prompt'] = prompt
    task['contract_note'] = '界面显式距离、类别和边界口径为执行合同；自由文本不能改写输入字段或图层角色。'
    return task
