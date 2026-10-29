"""真实 QGIS 工具。只接受本会话图层句柄，不暴露文件/代码执行能力。"""
import csv
import json
import os
import sys
from pathlib import Path
from collections import defaultdict
from .config import ROOT
from .data import save_json, validate_task

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class ToolError(Exception):
    def __init__(self, code, message, suggestion=''):
        super().__init__(message)
        self.code, self.suggestion = code, suggestion

    def public(self):
        return {'ok': False, 'code': self.code, 'message': str(self), 'suggestion': self.suggestion}


class QgisEngine:
    def __init__(self, task, run_dir, checks=False):
        from qgis.core import QgsApplication, QgsVectorLayer, Qgis
        self.qgs = QgsApplication.instance()
        if self.qgs is None:
            QgsApplication.setPrefixPath(str(Path(sys.prefix)), True)
            self.qgs = QgsApplication([], False)
            self.qgs.initQgis()
        plugin = Path(sys.prefix) / 'share/qgis/python/plugins'
        if str(plugin) not in sys.path:
            sys.path.insert(0, str(plugin))
        from processing.core.Processing import Processing
        Processing.initialize()
        import processing
        self.processing = processing
        self.version = Qgis.QGIS_VERSION
        self.task, self.run_dir, self.checks = task, Path(run_dir), checks
        self.layers, self.counter, self.finished = {}, 0, False
        validate_task(task)
        for role, relative in task['inputs'].items():
            layer = QgsVectorLayer(str(ROOT / relative), role, 'ogr')
            if not layer.isValid() or not layer.crs().isValid():
                raise ToolError('INVALID_INPUT', '输入图层或 CRS 无效')
            self.layers[role] = layer
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def layer(self, handle):
        if handle not in self.layers:
            raise ToolError('UNKNOWN_LAYER', f'未知图层句柄：{handle}', '先 describe 获取图层句柄')
        return self.layers[handle]

    def remember(self, layer):
        self.counter += 1
        handle = f'layer_{self.counter}'
        self.layers[handle] = layer
        return {'ok': True, 'layer': handle, **self.describe(handle)}

    def describe(self, layer):
        from qgis.core import QgsWkbTypes, QgsUnitTypes
        l = self.layer(layer)
        fields = [f.name() for f in l.fields()]
        return {'count': l.featureCount(), 'crs': l.crs().authid(),
                'unit': QgsUnitTypes.toString(l.crs().mapUnits()),
                'geometry': QgsWkbTypes.displayString(l.wkbType()),
                'fields': fields, 'sample': [self.row(f) for f in list(l.getFeatures())[:3]]}

    @staticmethod
    def row(feature):
        from qgis.core import NULL
        values = {}
        for field in feature.fields():
            v = feature[field.name()]
            values[field.name()] = None if v is None or v == NULL else v
        return values

    def fields(self, layer, names):
        known = self.layer(layer).fields().names()
        for name in names:
            if name not in known:
                raise ToolError('FIELD_NOT_FOUND', f'字段不存在：{name}', f'可用字段：{known}')

    def metric(self, layer):
        from qgis.core import QgsUnitTypes
        l = self.layer(layer)
        if self.checks and (l.crs().isGeographic() or l.crs().mapUnits() != QgsUnitTypes.DistanceMeters):
            raise ToolError('DISTANCE_UNIT_CONFLICT',
                            '当前工具按图层单位测量，输入不是米制投影 CRS',
                            f'使用 reproject 将图层转到 {self.task["analysis_crs"]}')

    def compatible(self, left, right):
        if self.checks and self.layer(left).crs() != self.layer(right).crs():
            raise ToolError('CRS_MISMATCH', '参与运算的图层 CRS 不一致',
                            f'先核对并转换到 {self.task["analysis_crs"]}')

    def algorithm(self, name, params):
        try:
            output = self.processing.run(name, {**params, 'OUTPUT': 'memory:'})['OUTPUT']
            return self.remember(output)
        except Exception:
            raise ToolError('GIS_EXECUTION_ERROR', f'QGIS 算法 {name} 执行失败',
                            '核对图层几何、字段和参数；可用 describe 查看元信息')

    def execute(self, name, args):
        functions = {
            'describe': self.tool_describe, 'reproject': self.reproject,
            'filter': self.filter, 'buffer': self.buffer,
            'select': self.select, 'within_distance': self.within_distance,
            'deduplicate': self.deduplicate,
            'dissolve': self.dissolve, 'intersection': self.intersection,
            'assign_zones': self.assign_zones, 'aggregate': self.aggregate,
            'left_join': self.left_join, 'calculate': self.calculate,
            'finalize': self.finalize,
        }
        if name not in functions:
            raise ToolError('UNKNOWN_TOOL', '未知工具')
        if self.finished:
            raise ToolError('ALREADY_FINISHED', '本会话已结束')
        try:
            return functions[name](**args)
        except TypeError:
            raise ToolError('INVALID_ARGUMENTS', '工具参数名称或类型不符合定义', '参考工具 schema')

    def tool_describe(self, layer=None):
        return {'ok': True, 'layers': {k: self.describe(k) for k in self.layers}} if layer is None else {
            'ok': True, 'layer': layer, **self.describe(layer)}

    def reproject(self, layer, crs):
        from qgis.core import QgsCoordinateReferenceSystem
        target = QgsCoordinateReferenceSystem(crs)
        if not target.isValid():
            raise ToolError('INVALID_CRS', '目标 CRS 无效')
        return self.algorithm('native:reprojectlayer', {'INPUT': self.layer(layer), 'TARGET_CRS': target})

    def filter(self, layer, field, value):
        self.fields(layer, [field])
        quoted_field = '"' + field.replace('"', '""') + '"'
        quoted_value = "'" + str(value).replace("'", "''") + "'"
        return self.algorithm('native:extractbyexpression', {'INPUT': self.layer(layer),
                              'EXPRESSION': f'{quoted_field} = {quoted_value}'})

    def buffer(self, layer, distance, unit='m'):
        if unit != 'm' or not isinstance(distance, (int, float)) or not 0 <= distance <= 100000:
            raise ToolError('INVALID_DISTANCE', '距离必须为 0—100000 米')
        self.metric(layer)
        return self.algorithm('native:buffer', {'INPUT': self.layer(layer), 'DISTANCE': distance,
                              'SEGMENTS': 24, 'END_CAP_STYLE': 0, 'JOIN_STYLE': 0,
                              'MITER_LIMIT': 2, 'DISSOLVE': False})

    def select(self, layer, mask):
        self.compatible(layer, mask)
        return self.algorithm('native:extractbylocation', {'INPUT': self.layer(layer),
                              'INTERSECT': self.layer(mask), 'PREDICATE': [0]})

    def within_distance(self, layer, reference, distance):
        if not isinstance(distance, (int, float)) or not 0 <= distance <= 100000:
            raise ToolError('INVALID_DISTANCE', '距离必须为 0—100000 米')
        self.metric(layer)
        self.metric(reference)
        self.compatible(layer, reference)
        return self.algorithm('native:extractwithindistance', {'INPUT': self.layer(layer),
                              'REFERENCE': self.layer(reference), 'DISTANCE': distance})

    def copy_rows(self, source, features, new_fields=None, updates=None):
        from qgis.core import QgsVectorLayer, QgsWkbTypes, QgsFeature
        source = self.layer(source)
        kind = QgsWkbTypes.displayString(source.wkbType())
        l = QgsVectorLayer(f'{kind}?crs={source.crs().authid()}', 'result', 'memory')
        provider = l.dataProvider()
        provider.addAttributes(list(source.fields()) + (new_fields or []))
        l.updateFields()
        result = []
        for f in features:
            n = QgsFeature(l.fields())
            n.setGeometry(f.geometry())
            values = f.attributes() + ((updates or {}).get(f.id(), []) if new_fields else [])
            n.setAttributes(values)
            result.append(n)
        provider.addFeatures(result)
        l.updateExtents()
        return self.remember(l)

    def deduplicate(self, layer, key):
        self.fields(layer, [key])
        seen, rows = set(), []
        for f in self.layer(layer).getFeatures():
            value = str(f[key])
            if value not in seen:
                seen.add(value)
                rows.append(f)
        return self.copy_rows(layer, rows)

    def dissolve(self, layer):
        return self.algorithm('native:dissolve', {'INPUT': self.layer(layer), 'FIELD': []})

    def intersection(self, layer, overlay):
        self.compatible(layer, overlay)
        return self.algorithm('native:intersection', {'INPUT': self.layer(layer),
                              'OVERLAY': self.layer(overlay), 'INPUT_FIELDS': [],
                              'OVERLAY_FIELDS': [], 'OVERLAY_FIELDS_PREFIX': ''})

    def assign_zones(self, layer, zones, zone_key):
        self.fields(zones, [zone_key])
        self.compatible(layer, zones)
        return self.algorithm('native:joinattributesbylocation', {'INPUT': self.layer(layer),
                              'JOIN': self.layer(zones), 'PREDICATE': [0], 'JOIN_FIELDS': [zone_key],
                              'METHOD': 0, 'DISCARD_NONMATCHING': True, 'PREFIX': ''})

    def aggregate(self, layer, group_field, output_field, operation, value_field=None):
        from qgis.core import QgsVectorLayer, QgsField, QgsFeature
        from qgis.PyQt.QtCore import QVariant
        self.fields(layer, [group_field] + ([value_field] if value_field else []))
        if operation not in {'count', 'sum'} or (operation == 'sum' and not value_field):
            raise ToolError('INVALID_AGGREGATION', '使用 count 或提供 value_field 的 sum')
        sums = defaultdict(float)
        for f in self.layer(layer).getFeatures():
            sums[str(f[group_field])] += 1 if operation == 'count' else float(f[value_field])
        table = QgsVectorLayer('None', 'statistics', 'memory')
        provider = table.dataProvider()
        provider.addAttributes([QgsField(group_field, QVariant.String),
                                QgsField(output_field, QVariant.Double)])
        table.updateFields()
        for key, value in sums.items():
            f = QgsFeature(table.fields())
            f.setAttributes([key, value])
            provider.addFeatures([f])
        return self.remember(table)

    def left_join(self, zones, table, zone_key, value_field, output_field=None):
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QVariant
        self.fields(zones, [zone_key])
        self.fields(table, [zone_key, value_field])
        name = output_field or value_field
        if name in self.layer(zones).fields().names():
            raise ToolError('FIELD_EXISTS', '输出字段已存在')
        lookup = {}
        for f in self.layer(table).getFeatures():
            key = str(f[zone_key])
            if key in lookup:
                raise ToolError('DUPLICATE_JOIN_KEY', '统计表连接键不唯一')
            lookup[key] = f[value_field]
        rows = list(self.layer(zones).getFeatures())
        return self.copy_rows(zones, rows, [QgsField(name, QVariant.Double)],
                              {f.id(): [lookup.get(str(f[zone_key]), 0)] for f in rows})

    def calculate(self, layer, field, operation, numerator=None, denominator=None):
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QVariant
        if field in self.layer(layer).fields().names():
            raise ToolError('FIELD_EXISTS', '输出字段已存在')
        rows = list(self.layer(layer).getFeatures())
        if operation == 'area_m2':
            self.metric(layer)
            updates = {f.id(): [f.geometry().area()] for f in rows}
        elif operation == 'ratio':
            self.fields(layer, [numerator, denominator])
            if any(float(f[denominator]) <= 0 for f in rows):
                raise ToolError('ZERO_DENOMINATOR', '分母面积必须大于零')
            updates = {f.id(): [float(f[numerator]) / float(f[denominator])] for f in rows}
        else:
            raise ToolError('INVALID_CALCULATION', 'operation 使用 area_m2 或 ratio')
        return self.copy_rows(layer, rows, [QgsField(field, QVariant.Double)], updates)

    def validate_final(self, zones, selected=None):
        import math
        key = self.task['schema']['zone_key']
        self.fields(zones, [key] + self.task['output_fields'])
        output = list(self.layer(zones).getFeatures())
        if not self.checks:
            return
        self.metric(zones)
        keys = [str(f[key]) for f in output]
        required = {str(f[key]) for f in self.layer('zones').getFeatures()}
        if set(keys) != required or len(keys) != len(set(keys)):
            raise ToolError('ZONE_COMPLETENESS', '分区遗漏或重复', '将统计表左连接回全部分区并填零')
        if self.task['family'] == 'proximity':
            if not selected:
                raise ToolError('MISSING_SELECTION', '需输出选中的学校图层')
            school_key = self.task['schema']['school_key']
            self.fields(selected, [school_key])
            ids = [str(f[school_key]) for f in self.layer(selected).getFeatures()]
            if len(ids) != len(set(ids)):
                raise ToolError('DUPLICATE_ENTITY', '学校业务 ID 重复', f'对 {school_key} 使用 deduplicate')
            if any(not math.isfinite(float(f['school_count'])) or float(f['school_count']) < 0 or
                   float(f['school_count']) % 1 for f in output):
                raise ToolError('INVALID_COUNT', '计数必须是非负整数')
        else:
            for f in output:
                if any(not math.isfinite(float(f[n])) for n in self.task['output_fields']) or \
                   float(f['covered_m2']) < 0 or float(f['zone_m2']) <= 0 or not 0 <= float(f['coverage_ratio']) <= 1:
                    raise ToolError('INVALID_AREA', '面积或覆盖比例范围异常')

    def export_geojson(self, layer, path):
        l = self.layer(layer)
        save_json(path, {'type': 'FeatureCollection',
                        'crs': {'type': 'name', 'properties': {'name': l.crs().authid()}},
                        'features': [{'type': 'Feature', 'properties': self.row(f),
                                      'geometry': json.loads(f.geometry().asJson(15))}
                                     for f in l.getFeatures()]})

    def finalize(self, zones, selected=None, overlay=None):
        from qgis.core import QgsVectorFileWriter, QgsProject
        self.validate_final(zones, selected)
        self.fields(zones, [self.task['schema']['zone_key']] + self.task['output_fields'])
        folder = self.run_dir / 'artifacts'
        folder.mkdir(exist_ok=True)
        self.export_geojson(zones, folder / 'zones.geojson')
        if selected:
            self.export_geojson(selected, folder / 'selected.geojson')
        if overlay:
            self.export_geojson(overlay, folder / 'overlay.geojson')
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName, opts.layerName = 'GPKG', 'zones'
        outcome = QgsVectorFileWriter.writeAsVectorFormatV3(
            self.layer(zones), str(folder / 'results.gpkg'), QgsProject.instance().transformContext(), opts)
        if outcome[0] != QgsVectorFileWriter.NoError:
            raise ToolError('EXPORT_FAILED', 'GeoPackage 导出失败')
        rows = [self.row(f) for f in self.layer(zones).getFeatures()]
        with (folder / 'results.csv').open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=self.layer(zones).fields().names())
            writer.writeheader()
            writer.writerows(rows)
        result = {'task': self.task['id'], 'family': self.task['family'],
                  'crs': self.layer(zones).crs().authid(), 'rows': rows,
                  'selected_ids': [str(f[self.task['schema']['school_key']])
                                   for f in self.layer(selected).getFeatures()] if selected else [],
                  'backend': 'QGIS ' + self.version,
                  'artifacts': ['zones.geojson', 'results.gpkg', 'results.csv', 'map.png']}
        from .maps import export_map
        export_map(self.task, folder, result)
        save_json(self.run_dir / 'result.json', result)
        self.finished = True
        return {'ok': True, 'completed': True, 'rows': rows, 'backend': result['backend'],
                'artifacts': result['artifacts']}
