"""运行结束才载入。独立几何参考与文件核验，不属于 Agent 工具。"""
import csv
import hashlib
import json
import math
from pathlib import Path
from shapely.geometry import shape,mapping
from shapely.ops import unary_union,transform
from pyproj import Transformer
from .config import ROOT,V2
from .data import save_json


def read(path,target='EPSG:32650'):
    data=json.loads(Path(path).read_text());crs=data.get('crs',{}).get('properties',{}).get('name','EPSG:4326')
    tx=Transformer.from_crs(crs,target,always_xy=True).transform
    return [(shape(f['geometry']) if crs==target else transform(tx,shape(f['geometry'])),f['properties']) for f in data['features']]


def reference(task):
    digest=hashlib.sha256(json.dumps(task,sort_keys=True,ensure_ascii=False).encode()+
                          b''.join((ROOT/p).read_bytes() for p in task['inputs'].values())).hexdigest()
    cache=V2/'reports/references'/f'{digest}.json'
    if cache.exists():return json.loads(cache.read_text())
    layers={k:read(ROOT/p,task['analysis_crs']) for k,p in task['inputs'].items()}
    rows={};selected=[];outputs=[]
    if task['family']=='proximity':
        stations=unary_union([g for g,_ in layers['stations']])
        points={str(p['school_id']):g for g,p in layers['schools'] if g.distance(stations)<=task['distance_m']+1e-6}
        selected=sorted(points)
        for zone,p in layers['zones']:rows[str(p['zone_id'])]={'school_count':sum(zone.covers(g) for g in points.values())}
        output=None
    elif task['family']=='coverage':
        parks=unary_union([g for g,_ in layers['parks']])
        for zone,p in layers['zones']:
            g=zone.intersection(parks);outputs.append(g)
            rows[str(p['zone_id'])]={'covered_m2':g.area,'zone_m2':zone.area,'coverage_ratio':g.area/zone.area}
        output=mapping(unary_union(outputs))
    else:
        residential=unary_union([g for g,_ in layers['residential']])
        service=unary_union([g.buffer(task['distance_m'],quad_segs=24) for g,_ in layers['stations']]+
                            [g.buffer(task['hospital_distance_m'],quad_segs=24) for g,_ in layers['hospitals']])
        for zone,p in layers['zones']:
            base=zone.intersection(residential);g=base.difference(service);outputs.append(g)
            rows[str(p['zone_id'])]={'uncovered_m2':g.area,'residential_m2':base.area,'uncovered_ratio':g.area/base.area}
        output=mapping(unary_union(outputs))
    result={'rows':rows,'selected_ids':selected,'overlay_geometry':output,'source_digest':digest}
    save_json(cache,result);return result


def score(task,folder):
    folder=Path(folder);errors=[];differences=[];precision=recall=f1=None
    try:
        ref=reference(task);result=json.loads((folder/'result.json').read_text())
        actual={str(r['zone_id']):r for r in result['rows']}
        if len(actual)!=len(result['rows']) or set(actual)!=set(ref['rows']):errors.append('分区集合遗漏或重复')
        for key,fields in ref['rows'].items():
            for field,target in fields.items():
                value=float(actual[key][field]);diff=abs(value-target)
                tolerance=0 if field=='school_count' else 1e-7 if 'ratio' in field else .5+abs(target)*1e-6
                if not math.isfinite(value) or diff>tolerance:errors.append(f'{key}.{field} 超出容差')
                differences.append({'zone':key,'field':field,'actual':value,'expected':target,'absolute_error':diff,'tolerance':tolerance})
        zones=read(folder/'artifacts/zones.geojson',task['analysis_crs'])
        originals={str(p['zone_id']):g for g,p in read(ROOT/task['inputs']['zones'],task['analysis_crs'])}
        if len(zones)!=len(actual):errors.append('导出分区数量不一致')
        for g,p in zones:
            key=str(p['zone_id'])
            if key not in originals or g.symmetric_difference(originals[key]).area>.1:errors.append('导出区界几何错误')
            if p!=actual.get(key):errors.append('GeoJSON 与结果行不一致')
        if task['family']=='proximity':
            selected=read(folder/'artifacts/selected.geojson',task['analysis_crs'])
            ids=[str(p['school_id']) for g,p in selected];expected=set(ref['selected_ids']);observed=set(ids)
            common=len(expected&observed);precision=common/len(observed) if observed else float(not expected)
            recall=common/len(expected) if expected else float(not observed);f1=2*precision*recall/(precision+recall) if precision+recall else 0
            if observed!=expected or len(ids)!=len(observed) or sorted(ids)!=sorted(result['selected_ids']):errors.append('学校集合或身份重复错误')
            source={str(p['school_id']):g for g,p in read(ROOT/task['inputs']['schools'],task['analysis_crs'])}
            if any(k not in source or g.distance(source[k])>1e-5 for g,p in selected for k in [str(p['school_id'])]):errors.append('学校输出几何错误')
        else:
            overlay=unary_union([g for g,_ in read(folder/'artifacts/overlay.geojson',task['analysis_crs'])])
            target=shape(ref['overlay_geometry']);diff=overlay.symmetric_difference(target).area
            if diff>max(.5,target.area*1e-6):errors.append('覆盖/未覆盖输出几何错误')
        from osgeo import ogr
        ds=ogr.Open(str(folder/'artifacts/results.gpkg'))
        layer=ds.GetLayerByName('zones') if ds else None
        if layer is None or layer.GetFeatureCount()!=len(actual):errors.append('GeoPackage 无法重开或数量错误')
        else:
            if layer.GetSpatialRef().GetAuthorityCode(None)!=task['analysis_crs'].split(':')[1]:errors.append('GeoPackage CRS 错误')
            for row in layer:
                key=str(row['zone_id'])
                for field in task['output_fields']:
                    if float(row[field])!=float(actual[key][field]):errors.append('GeoPackage 属性不一致')
                g=shape(json.loads(row.GetGeometryRef().ExportToJson()))
                if g.symmetric_difference(originals[key]).area>.1:errors.append('GeoPackage 几何错误')
        with (folder/'artifacts/results.csv').open(encoding='utf-8-sig') as f:csv_rows=list(csv.DictReader(f))
        if len(csv_rows)!=len(actual):errors.append('CSV 行数错误')
        for row in csv_rows:
            for field in task['output_fields']:
                if float(row[field])!=float(actual[str(row['zone_id'])][field]):errors.append('CSV 属性不一致')
        if not (folder/'artifacts/map.png').is_file():errors.append('成果地图缺失')
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:errors.append('无法核验成果：'+type(exc).__name__)
    verdict={'passed':not errors,'errors':errors,'differences':differences,'set_precision':precision,
             'set_recall':recall,'set_f1':f1,'reference_engine':'Independent Shapely + GDAL artifact inspection'}
    save_json(folder/'score.json',verdict);return verdict
