"""仅由运行结束后的评估进程使用，Agent 工具无法读取。"""
import csv
import json
import math
from pathlib import Path
from shapely.geometry import shape
from shapely.ops import transform, unary_union
from pyproj import Transformer
from .config import ROOT
from .data import load_task, save_json


def read_features(path, target='EPSG:32650'):
    d = json.loads(Path(path).read_text())
    crs = d.get('crs', {}).get('properties', {}).get('name', 'EPSG:4326')
    trans = Transformer.from_crs(crs, target, always_xy=True)
    return [(transform(trans.transform, shape(f['geometry'])), f['properties'])
            for f in d['features']]


def reference(task):
    """独立 Shapely 距离与几何并集，不调用 Agent 的 QGIS 工具。"""
    inputs = {k: read_features(ROOT / v, task['analysis_crs']) for k, v in task['inputs'].items()}
    zone_key = task['schema']['zone_key']
    rows = {}
    if task['family'] == 'proximity':
        stations = [g for g, _ in inputs['stations']]
        selected = {}
        for g, p in inputs['schools']:
            if any(g.distance(s) <= task['distance_m'] + 1e-6 for s in stations):
                selected.setdefault(str(p[task['schema']['school_key']]), g)
        for zone, props in inputs['zones']:
            rows[str(props[zone_key])] = {'school_count': sum(zone.covers(g) for g in selected.values())}
        return {'rows': rows, 'selected_ids': sorted(selected)}
    parks = unary_union([g for g, _ in inputs['parks']])
    for zone, props in inputs['zones']:
        area = zone.intersection(parks).area
        rows[str(props[zone_key])] = {'covered_m2': area, 'zone_m2': zone.area,
                                    'coverage_ratio': area / zone.area}
    return {'rows': rows}


def score(task, run_dir):
    run_dir = Path(run_dir)
    errors = []
    try:
        expected = reference(task)
        result = json.loads((run_dir / 'result.json').read_text())
        actual = {}
        for row in result['rows']:
            key = str(row[task['schema']['zone_key']])
            if key in actual:
                errors.append(f'分区重复：{key}')
            actual[key] = row
        if set(actual) != set(expected['rows']):
            errors.append('分区集合不一致')
        config = json.loads((ROOT / 'configs' / 'experiment.json').read_text())
        differences = []
        for key, fields in expected['rows'].items():
            for field, target in fields.items():
                try:
                    value = float(actual[key][field])
                    if not math.isfinite(value):
                        raise ValueError()
                    diff = abs(value - target)
                    tolerance = (0 if field == 'school_count' else
                                 1e-9 if field == 'coverage_ratio' else
                                 config['area_absolute_tolerance_m2'] +
                                 config['area_relative_tolerance'] * abs(target))
                    if diff > tolerance:
                        errors.append(f'{key}.{field} 数值不一致')
                    differences.append({'zone': key, 'field': field,
                                        'actual': value, 'expected': target, 'absolute_error': diff})
                except (KeyError, ValueError, TypeError):
                    errors.append(f'{key}.{field} 缺失或非有限数值')
        if task['family'] == 'proximity':
            ids = result.get('selected_ids', [])
            if sorted(set(ids)) != expected['selected_ids']:
                errors.append('学校集合不一致')
            if len(ids) != len(set(ids)):
                errors.append('选中学校存在重复业务 ID')
        # 文件存在不够：实际打开图层并独立核对字段、记录、坐标与几何。
        output = run_dir / 'artifacts' / 'zones.geojson'
        geo = json.loads(output.read_text())
        if geo.get('crs', {}).get('properties', {}).get('name') != task['analysis_crs']:
            errors.append('成果 CRS 不符合合同')
        exported = read_features(output, task['analysis_crs'])
        source = {str(p[task['schema']['zone_key']]): g for g, p in
                  read_features(ROOT / task['inputs']['zones'], task['analysis_crs'])}
        if len(exported) != len(expected['rows']):
            errors.append('导出图层分区数不正确')
        for geometry, props in exported:
            key = str(props[task['schema']['zone_key']])
            if key not in source or geometry.symmetric_difference(source[key]).area > .1:
                errors.append('导出分区几何不正确')
            if props != actual.get(key):
                errors.append('导出图层与声明的统计行不一致')
        if task['family'] == 'proximity':
            exported_selected = read_features(run_dir / 'artifacts' / 'selected.geojson', task['analysis_crs'])
            if sorted(str(p[task['schema']['school_key']]) for _, p in exported_selected) != sorted(result['selected_ids']):
                errors.append('导出学校与声明集合不一致')
        for name in ['results.csv', 'results.gpkg', 'map.png']:
            if not (run_dir / 'artifacts' / name).is_file():
                errors.append(f'缺少成果：{name}')
        from qgis.core import QgsVectorLayer
        gpkg = QgsVectorLayer(str(run_dir / 'artifacts' / 'results.gpkg') + '|layername=zones', 'check', 'ogr')
        if not gpkg.isValid() or gpkg.featureCount() != len(expected['rows']) or gpkg.crs().authid() != task['analysis_crs']:
            errors.append('GeoPackage 无法重开或结构不正确')
        else:
            for f in gpkg.getFeatures():
                # GeoPackage 自动添加 fid；它不是业务字段。
                row = {name: f[name] for name in gpkg.fields().names() if name in next(iter(actual.values()), {})}
                if row != actual.get(str(f[task['schema']['zone_key']])):
                    errors.append('GeoPackage 与统计声明不一致')
        with (run_dir / 'artifacts' / 'results.csv').open(encoding='utf-8-sig') as f:
            csv_rows = list(csv.DictReader(f))
        if len(csv_rows) != len(expected['rows']):
            errors.append('CSV 行数不正确')
        for row in csv_rows:
            key = str(row[task['schema']['zone_key']])
            for field in task['output_fields']:
                if float(row[field]) != float(actual[key][field]):
                    errors.append('CSV 与统计声明不一致')
        verdict = {'passed': not errors, 'errors': errors, 'differences': differences,
                   'reference_engine': 'Shapely independent reference',
                   'map_exported': (run_dir / 'artifacts' / 'map.png').is_file()}
    except (OSError, KeyError, ValueError, TypeError) as exc:
        verdict = {'passed': False, 'errors': ['无法读取完整成果：' + type(exc).__name__],
                   'differences': [], 'map_exported': False}
    save_json(run_dir / 'score.json', verdict)
    return verdict


def summarize():
    rows = []
    for path in sorted((ROOT / 'runs').glob('*/run.json')):
        meta = json.loads(path.read_text())
        if meta.get('purpose') == 'test' or meta['run_id'].startswith('test-'):
            continue
        verdict_path = path.parent / 'score.json'
        verdict = json.loads(verdict_path.read_text()) if verdict_path.exists() else {}
        rows.append({'run_id': meta['run_id'], 'task': meta['task_id'], 'group': meta['group'],
                     'mode': meta['mode'], 'purpose': meta.get('purpose', 'smoke' if meta['mode']=='live' else 'validation'),
                     'status': meta['status'], 'passed': verdict.get('passed', False),
                     'model_requests': meta.get('requests', 0), 'gis_calls': meta.get('tools', 0),
                     'tokens': meta.get('usage', {}).get('total_tokens', 0),
                     'seconds': round(meta.get('elapsed_seconds', 0), 3),
                     'corrections': meta.get('corrections', 0),
                     'errors': '；'.join(verdict.get('errors', []))})
    folder = ROOT / 'reports' / 'generated'
    save_json(folder / 'summary.json', rows)
    if rows:
        with (folder / 'summary.csv').open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return rows
