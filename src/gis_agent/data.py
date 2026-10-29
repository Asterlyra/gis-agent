"""公开任务包。这里没有实验参考答案。"""
import json
from pathlib import Path
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import transform
from pyproj import Transformer
from .config import ROOT


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def task_path(task_id):
    if task_id not in {f'T{i}' for i in range(1, 7)}:
        raise ValueError('未知任务')
    return ROOT / 'data' / task_id / 'task.json'


def load_task(task_id):
    return json.loads(task_path(task_id).read_text())


def list_tasks():
    return [load_task(f'T{i}') for i in range(1, 7) if task_path(f'T{i}').exists()]


def make_samples():
    """两套米制构造场景，各三种版本；生成过程可复核。"""
    x0, y0 = 500000, 3300000

    def geom(g):
        return transform(lambda x, y, z=None: (x + x0, y + y0), g)

    def feature(g, props):
        return {'type': 'Feature', 'geometry': mapping(geom(g)), 'properties': props}

    zones = [feature(box(i*1000, 0, (i+1)*1000, 1000),
                     {'zone_id': f'Z{i+1}', 'name': f'分区 {i+1}'}) for i in range(3)]
    schools = [feature(Point(x, y), {'school_id': f'S{i+1}', 'kind': 'school'})
               for i, (x, y) in enumerate([(400, 500), (800, 500), (1250, 500),
                                         (1550, 950), (2600, 500), (100, 900)])]
    stations = [feature(Point(x, 500), {'station_id': f'M{i+1}'})
                for i, x in enumerate([450, 850])]
    parks = [feature(box(100, 100, 600, 700), {'park_id': 'P1'}),
             feature(box(1200, 200, 1700, 800), {'park_id': 'P2'})]

    for i in range(1, 7):
        task_id, family = f'T{i}', 'proximity' if i < 4 else 'coverage'
        layers = json.loads(json.dumps({'zones': zones, 'schools': schools,
                                       'stations': stations, 'parks': parks}))
        if i == 3:
            layers['schools'] += [json.loads(json.dumps(layers['schools'][1]))]
        if i in (5, 6):
            layers['parks'].append(feature(box(400, 400, 1400, 900), {'park_id': 'P3'}))
        zone_key = 'district_code' if i == 6 else 'zone_id'
        if i == 6:
            for f in layers['zones']:
                f['properties']['district_code'] = f['properties'].pop('zone_id')
        paths = {}
        for role in (['zones', 'schools', 'stations'] if i < 4 else ['zones', 'parks']):
            crs = 'EPSG:32650'
            if i == 2 and role in ('schools', 'stations'):
                trans = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
                for f in layers[role]:
                    f['geometry'] = mapping(transform(trans.transform, shape(f['geometry'])))
                crs = 'EPSG:4326'
            path = ROOT / 'data' / task_id / f'{role}.geojson'
            save_json(path, {'type': 'FeatureCollection', 'name': role,
                            'crs': {'type': 'name', 'properties': {'name': crs}},
                            'features': layers[role]})
            paths[role] = str(path.relative_to(ROOT))
        title = ['学校邻近统计', '经纬度输入与米制距离', '学校去重与零计数分区',
                 '公园覆盖统计', '重叠公园覆盖并集', '字段变化与零覆盖分区'][i-1]
        prompt = (f'统计每个分区距离任一站点 500 米以内的学校数，包含距离边界；'
                  f'按 school_id 去重，保留所有零计数分区。输出选中学校和统计分区。'
                  if i < 4 else
                  f'统计各分区公园并集覆盖面积（平方米）与比例（0—1）；'
                  f'重叠只算一次，保留零覆盖分区。输出统计分区和交叠图层。')
        save_json(task_path(task_id), {
            'id': task_id, 'title': title, 'family': family, 'prompt': prompt,
            'source': '可手算构造数据，非真实城市设施',
            'analysis_crs': 'EPSG:32650', 'distance_m': 500,
            'inputs': paths, 'schema': {'zone_key': zone_key, 'school_key': 'school_id'},
            'output_fields': ['school_count'] if i < 4 else ['covered_m2', 'zone_m2', 'coverage_ratio'],
            'boundary_policy': '距离边界纳入；学校归属为 contains 或 covers；本样例无边界学校',
        })
    save_json(ROOT / 'data' / 'provenance.json', {
        'type': 'synthetic', 'crs': 'EPSG:32650', 'origin': [x0, y0],
        'description': '三个 1000m × 1000m 分区；学校和矩形公园由 data.py 确定性生成。',
        'independent_base_scenarios': 2, 'task_variants': 6,
        'source_code': 'src/gis_agent/data.py',
    })


def validate_task(task):
    """仅允许任务包声明的本地输入，不提供任意文件读取工具。"""
    required = {'zones', 'schools', 'stations'} if task['family'] == 'proximity' else {'zones', 'parks'}
    if not required.issubset(task['inputs']):
        raise ValueError('缺少必需图层')
    for rel in task['inputs'].values():
        p = (ROOT / rel).resolve()
        if not p.is_relative_to((ROOT / 'data').resolve()) or p.suffix.lower() not in {'.geojson', '.gpkg'}:
            raise ValueError('输入必须位于 data 目录，使用 GeoJSON 或 GeoPackage')
        if not p.is_file():
            raise ValueError('输入文件不存在')
