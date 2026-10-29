"""真实 QGIS 工具。只接受本会话图层句柄，不暴露文件/代码执行能力。"""
import csv
import json
import os
import sys
import itertools
import math
import time
from collections import Counter
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
            prefix = Path(os.environ.get('QGIS_PREFIX_PATH', str(Path(sys.prefix) / 'Library' if os.name == 'nt' else Path(sys.prefix))))
            QgsApplication.setPrefixPath(str(prefix), True)
            self.qgs = QgsApplication([], False)
            self.qgs.initQgis()
        prefix = Path(os.environ.get('QGIS_PREFIX_PATH', str(Path(sys.prefix) / 'Library' if os.name == 'nt' else Path(sys.prefix))))
        plugin = prefix / ('python/plugins' if os.name == 'nt' else 'share/qgis/python/plugins')
        if str(plugin) not in sys.path:
            sys.path.insert(0, str(plugin))
        from processing.core.Processing import Processing
        Processing.initialize()
        import processing
        self.processing = processing
        self.version = Qgis.QGIS_VERSION
        self.task, self.run_dir, self.checks = task, Path(run_dir), checks
        self.layers, self.counter, self.finished = {}, 0, False
        self.lineage = {}
        self.check_events = []
        self._profiles = {}
        validate_task(task)
        for role, relative in task['inputs'].items():
            layer = QgsVectorLayer(str(ROOT / relative), role, 'ogr')
            if not layer.isValid() or not layer.crs().isValid():
                raise ToolError('INVALID_INPUT', '输入图层或 CRS 无效')
            self.layers[role] = layer
            self.lineage[role] = {'tool': 'input', 'parents': [], 'source': relative}
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
        if layer in self._profiles:
            return self._profiles[layer]
        fields = [f.name() for f in l.fields()]
        summaries = {key: Counter() for key in fields}
        nulls = Counter()
        sample = []
        for f in l.getFeatures():
            row = self.row(f)
            if len(sample) < 3: sample.append(row)
            for key,value in row.items():
                if value is None or value == '': nulls[key] += 1
                else: summaries[key][str(value)] += 1
        result = {'count': l.featureCount(), 'crs': l.crs().authid(),
                'unit': QgsUnitTypes.toString(l.crs().mapUnits()),
                'geometry': QgsWkbTypes.displayString(l.wkbType()),
                'fields': fields, 'sample': sample,
                'field_profiles': {f.name(): {'type':f.typeName(),'missing':nulls[f.name()],
                    'distinct':len(summaries[f.name()]),
                    'top_values':summaries[f.name()].most_common(3)} for f in l.fields()},
                'extent':l.extent().toString(), 'sample_limit':3}
        self._profiles[layer] = result
        return result

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
        if self.checks and l.crs().authid() != self.task['analysis_crs']:
            raise ToolError('ANALYSIS_CRS', '测量投影不符合任务口径',
                            f'测量前转换到 {self.task["analysis_crs"]}；显示投影不等于测量投影')

    def compatible(self, left, right):
        if self.checks and self.layer(left).crs() != self.layer(right).crs():
            raise ToolError('CRS_MISMATCH', '参与运算的图层 CRS 不一致',
                            f'先核对并转换到 {self.task["analysis_crs"]}')

    def algorithm(self, name, params):
        try:
            output = self.processing.run(name, {**params, 'OUTPUT': 'memory:'})['OUTPUT']
            return self.remember(output)
        except Exception as exc:
            raise ToolError('GIS_EXECUTION_ERROR', f'QGIS 算法 {name} 执行失败',
                            str(exc)[:400] + '；核对当前图层几何、字段和参数')

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
            'difference': self.difference, 'merge': self.merge,
        }
        if name not in functions:
            raise ToolError('UNKNOWN_TOOL', '未知工具')
        if self.finished:
            raise ToolError('ALREADY_FINISHED', '本会话已结束')
        try:
            result = functions[name](**args)
            if result.get('layer'):
                handle = result['layer']
                parents = [v for k,v in args.items() if k in {'layer','overlay','mask','reference','zones','table','left','right'} and v in self.layers]
                self.lineage[handle] = {'tool':name,'parents':parents,'parameters':args}
                self.snapshot(handle)
                save_json(self.run_dir/'lineage.json', self.lineage)
            return result
        except TypeError:
            raise ToolError('INVALID_ARGUMENTS', '工具参数名称或类型不符合定义', '参考工具 schema')

    def snapshot(self, handle):
        """只简化显示几何，分析图层保持原精度。"""
        from qgis.core import QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsProject, QgsGeometry
        layer=self.layer(handle)
        if not layer.isSpatial(): return
        tx=QgsCoordinateTransform(layer.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject.instance())
        features=[]
        for f in layer.getFeatures():
            g=QgsGeometry(f.geometry())
            if not g.isEmpty():
                if layer.crs().authid()==self.task['analysis_crs']:g=g.simplify(3)
                g.transform(tx)
                features.append({'type':'Feature','properties':self.row(f),'geometry':json.loads(g.asJson(10))})
        path=self.run_dir/'layers'/f'{handle}.geojson'
        save_json(path,{'type':'FeatureCollection','features':features,'display_only':True,'simplified_m':3})

    def difference(self, layer, overlay):
        self.compatible(layer,overlay)
        return self.algorithm('native:difference',{'INPUT':self.layer(layer),'OVERLAY':self.layer(overlay)})

    def merge(self,left,right):
        self.compatible(left,right)
        return self.algorithm('native:mergevectorlayers',{'LAYERS':[self.layer(left),self.layer(right)],
                                                        'CRS':self.layer(left).crs()})

    def check(self,rule,passed,evidence,suggestion=''):
        record={'rule':rule,'passed':passed,'evidence':evidence}
        self.check_events.append(record)
        save_json(self.run_dir/'checks.json',self.check_events)
        if not passed:
            raise ToolError(rule,evidence,suggestion)

    def geometries(self,handle):
        from shapely.geometry import shape
        return [(shape(json.loads(f.geometry().asJson(15))),self.row(f))
                for f in self.layer(handle).getFeatures() if not f.geometry().isEmpty()]

    def check_overlap(self, handle):
        from shapely.ops import unary_union
        groups=defaultdict(list)
        key=self.task['schema']['zone_key']
        for g,p in self.geometries(handle): groups[str(p.get(key,'all'))].append(g)
        for group,geoms in groups.items():
            total=sum(g.area for g in geoms);union=unary_union(geoms).area
            error=total-union
            self.check('OVERLAPPING_AREA',error <= max(.1,total*1e-7),
                       f'图层 {handle} 分组 {group} 累计面积与并集面积差 {error:.3f} m²',
                       f'检查 {handle} 的来源步骤，在求面积前对重叠几何 dissolve，然后重算该步骤及其下游')

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
            if self.checks and self.task['family'] in {'coverage','gaps'}:
                self.check_overlap(layer)
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
            family=self.task['family']
            area_field='covered_m2' if family=='coverage' else 'uncovered_m2'
            denominator='zone_m2' if family=='coverage' else 'residential_m2'
            ratio='coverage_ratio' if family=='coverage' else 'uncovered_ratio'
            for f in output:
                if any(not math.isfinite(float(f[n])) for n in self.task['output_fields']) or \
                   float(f[area_field]) < 0 or float(f[denominator]) <= 0 or not 0 <= float(f[ratio]) <= 1:
                    raise ToolError('INVALID_AREA', '面积或覆盖比例范围异常')
                self.check('RATIO_CONSISTENCY',abs(float(f[ratio])-float(f[area_field])/float(f[denominator])) < 1e-8,
                           '输出比例须等于对应面积除以合同分母','重算比例字段')
        if self.task['family']=='proximity':
            total=sum(float(f['school_count']) for f in output)
            self.check('COUNT_CONSERVATION',total==self.layer(selected).featureCount(),
                       f'当前单区任务计数 {total:g}，选中学校 {self.layer(selected).featureCount()}',
                       '检查学校是否按当前完整区界归属，去重后重新统计')

    def validate_overlay(self, zones, overlay):
        if not self.checks or self.task['family']=='proximity':return
        from shapely.ops import unary_union
        if not overlay:raise ToolError('MISSING_OVERLAY','面积任务必须输出实际覆盖/未覆盖图层')
        self.metric(overlay)
        self.check_overlap(overlay)
        zone_geoms=self.geometries(zones)
        pieces=self.geometries(overlay)
        area_field='covered_m2' if self.task['family']=='coverage' else 'uncovered_m2'
        for z,p in zone_geoms:
            area=unary_union([g.intersection(z) for g,_ in pieces]).area
            claimed=float(p[area_field])
            self.check('AREA_LEDGER',abs(area-claimed)<=max(.1,area*1e-6),
                       f'分区 {p["zone_id"]} 图层并集面积 {area:.3f}，声明面积 {claimed:.3f}',
                       '由实际 overlay 计算面积并汇总，重算计数表及其下游')

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
        self.validate_overlay(zones,overlay)
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
        for name,handle in [('selected',selected),('overlay',overlay)]:
            if not handle:continue
            opts.layerName=name
            opts.actionOnExistingFile=QgsVectorFileWriter.CreateOrOverwriteLayer
            outcome=QgsVectorFileWriter.writeAsVectorFormatV3(
                self.layer(handle),str(folder/'results.gpkg'),QgsProject.instance().transformContext(),opts)
            if outcome[0]!=QgsVectorFileWriter.NoError:raise ToolError('EXPORT_FAILED',f'GeoPackage {name} 导出失败')
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
        for handle in [zones,selected,overlay]:
            if handle:self.snapshot(handle)
        if selected:result['artifacts'].append('selected.geojson')
        if overlay:result['artifacts'].append('overlay.geojson')
        result['map_layers']={'zones':zones,'selected':selected,'overlay':overlay}
        result['contract']={k:self.task[k] for k in ['family','region_name','analysis_crs','distance_m','hospital_distance_m','boundary_policy']}
        result['summary']=f'{self.task["region_name"]}：'+('；'.join(f'{k}={v:.6g}' for row in rows for k,v in row.items() if k in self.task['output_fields']))
        result['limitations']='基于 OSM 冻结快照与欧氏距离，不代表实际设施完整数量、步行时间或人口公平。'
        from .maps import export_map
        export_map(self.task, folder, result)
        save_json(self.run_dir / 'result.json', result)
        self.finished = True
        return {'ok': True, 'completed': True, 'rows': rows, 'backend': result['backend'],
                'artifacts': result['artifacts']}
