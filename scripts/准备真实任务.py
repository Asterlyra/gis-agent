"""从完整真实区界抽取数据。分析前清洗口径和来源全部记录。"""
import hashlib
import json
from collections import Counter
from pathlib import Path
from osgeo import ogr
from pyproj import Transformer
from shapely.geometry import shape, mapping
from shapely.ops import transform, unary_union

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'v2/data'
ogr.UseExceptions()
TX = Transformer.from_crs(4326,32650,always_xy=True).transform
BACK = Transformer.from_crs(32650,4326,always_xy=True).transform
REGIONS = [('jianghan','江汉区','dev'),('qiaokou','硚口区','dev'),('wuchang','武昌区','dev'),
           ('jiangan','江岸区','test'),('hanyang','汉阳区','test'),('qingshan','青山区','test'),('hongshan','洪山区','test')]


def save(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')


def feature(g,p):
    return {'type':'Feature','geometry':mapping(g),'properties':p}


def fc(features):
    return {'type':'FeatureCollection','features':features}


def polygon_parts(g):
    if g.geom_type in {'Polygon','MultiPolygon'}:return [g] if g.area>0 else []
    return [p for part in getattr(g,'geoms',[]) for p in polygon_parts(part)]


def main():
    source=ROOT/'research_upgrade/raw/hubei-gpkg/hubei.gpkg'
    ds=ogr.Open(str(source),0)
    boundary=ds.GetLayerByName('gis_osm_adminareas_a_free')
    zones={}
    for f in boundary:
        if f['fclass']=='admin_level6' and f.GetGeometryRef():
            zones[f['name']]=(shape(json.loads(f.GetGeometryRef().ExportToJson())),str(f['osm_id']))
    manifest={'source':'Geofabrik hubei-261011-free.gpkg.zip','license':'ODbL 1.0',
              'attribution':'© OpenStreetMap contributors; Geofabrik',
              'source_gpkg_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'analysis_crs':'EPSG:32650','regions':[],
              'policy':{'school_geometry':'面取内部代表点；点保留；非空同名点被同名校园面覆盖时合并，保留映射日志',
                        'park_geometry':'landuse park 与 pois_a park 均输入，保留跨图层重复表达，由分析求并集',
                        'distance':'学校内部代表点到 bus_stop 点的欧氏距离，边界纳入',
                        'gaps':'居住用地并集减去公交缓冲与医院代表点缓冲并集，缓冲24段/象限',
                        'scope':'OSM 快照内记录，不代表现实设施完整清单'}}
    names={r[1] for r in REGIONS}
    all_geoms={name:transform(TX,zones[name][0]) for name in names}
    # 保留完整来源与确定性的点面合并日志，避免把原始记录数当作现实实体数量。
    for rid,name,split in REGIONS:
        zone_wgs,zone_id=zones[name];zone=all_geoms[name]
        if not zone.is_valid:raise ValueError(name+' boundary invalid')
        margin=zone.buffer(5000)
        window=transform(BACK,margin).bounds
        def read(kind,categories=None):
            layer=ds.GetLayerByName('gis_osm_'+kind+'_free');layer.SetSpatialFilterRect(*window)
            result=[]
            for f in layer:
                p=f.items();geo=f.GetGeometryRef()
                if not geo or (categories and p['fclass'] not in categories):continue
                g=transform(TX,shape(json.loads(geo.ExportToJson())))
                if not g.is_valid or g.is_empty:raise ValueError(f'{name}:{kind} invalid geometry')
                if not g.intersects(margin):continue
                p={'source_layer':kind,'osm_id':str(p['osm_id']),'name':p.get('name') or '',
                   'fclass':p.get('fclass'),'record_id':kind+':'+str(p['osm_id'])+':'+p.get('fclass','')}
                result.append((g,p))
            layer.SetSpatialFilter(None)
            return result
        school_areas=read('pois_a',{'school'})
        school_points=read('pois',{'school'})
        merged=[];schools=[]
        for g,p in school_areas:
            q=g.representative_point()
            if zone.covers(q):schools.append(feature(q,{**p,'school_id':p['record_id'],'origin_geometry':g.geom_type}))
        for g,p in school_points:
            matches=[a for poly,a in school_areas if p['name'] and p['name']==a['name'] and poly.covers(g)]
            if matches:
                merged.append({'point':p['record_id'],'polygon':matches[0]['record_id'],'rule':'same nonempty name and polygon covers point'})
            elif zone.covers(g):schools.append(feature(g,{**p,'school_id':p['record_id'],'origin_geometry':'Point'}))
        # 来源相同的重复记录若出现则按全几何身份核对，而非静默丢弃冲突。
        unique={}
        for f in schools:
            key=f['properties']['school_id']
            if key in unique and unique[key]['geometry']!=f['geometry']:raise ValueError('school identity conflict')
            unique[key]=f
        schools=list(unique.values())
        stations=[feature(g,{**p,'station_id':p['record_id']}) for g,p in read('transport',{'bus_stop'})]
        hospitals=[feature(g.representative_point(),{**p,'hospital_id':p['record_id']})
                   for g,p in read('pois_a',{'hospital'})]+[feature(g,{**p,'hospital_id':p['record_id']}) for g,p in read('pois',{'hospital'})]
        parks=[feature(g,p) for g,p in read('landuse_a',{'park'})+read('pois_a',{'park'}) if g.intersects(zone)]
        residential=[];boundary_only=[];mixed_clips=[]
        for g,p in read('landuse_a',{'residential'}):
            if not g.intersects(zone):continue
            clipped=g.intersection(zone);parts=polygon_parts(clipped)
            if not parts:boundary_only.append(p['record_id']);continue
            if clipped.geom_type not in {'Polygon','MultiPolygon'}:mixed_clips.append(p['record_id'])
            residential.append(feature(unary_union(parts),p))
        district=OUT/'regions'/rid
        layers={'zones':[feature(zone,{'zone_id':zone_id,'name':name})],'schools':schools,'stations':stations,
                'hospitals':hospitals,'parks':parks,'residential':residential}
        for role,features in layers.items():
            data=fc(features);data['crs']={'type':'name','properties':{'name':'EPSG:32650'}}
            save(district/(role+'.geojson'),data)
        # 在线地图只用简化显示数据；计算层不简化。
        display={'water':[feature(transform(BACK,g.simplify(12)),p) for g,p in read('water_a')],
                 'roads':[feature(transform(BACK,g.simplify(12)),p) for g,p in read('roads',{'primary','secondary','tertiary','trunk','motorway'})],
                 'zones':[feature(zone_wgs,{'zone_id':zone_id,'name':name})]}
        for role in ['schools','stations','hospitals','parks','residential']:
            display[role]=[feature(transform(BACK,shape(f['geometry']).simplify(5)),f['properties']) for f in layers[role]]
        for role,features in display.items():save(district/('display-'+role+'.geojson'),fc(features))
        info={'id':rid,'name':name,'split':split,'counts':{k:len(v) for k,v in layers.items()},
              'area_km2':zone.area/1e6,'school_point_polygon_merges':merged,
              'resident_union_m2':unary_union([shape(f['geometry']) for f in residential]).area,
              'facility_margin_m':5000,'boundary_id':zone_id}
        info['residential_clip_cleanup']={'boundary_only_records_excluded':boundary_only,
                                         'mixed_geometry_polygon_parts_kept':mixed_clips,
                                         'rule':'面积分析只保留裁剪后非零面积面；边界接触产生线或点不作为用地面'}
        save(district/'profile.json',info);manifest['regions'].append(info)
        for family,title in [('proximity','学校与公交邻近'),('coverage','公园并集覆盖'),('gaps','多条件未覆盖用地')]:
            roles={'proximity':['zones','schools','stations'],'coverage':['zones','parks'],
                   'gaps':['zones','residential','stations','hospitals']}[family]
            task={'id':rid+'-'+family,'title':name+' · '+title,'region':rid,'region_name':name,'split':split,
                  'family':family,'analysis_crs':'EPSG:32650','distance_m':500,'hospital_distance_m':1000,
                  'schema':{'zone_key':'zone_id','school_key':'school_id'},
                  'inputs':{r:str((district/(r+'.geojson')).relative_to(ROOT)) for r in roles},
                  'output_fields':{'proximity':['school_count'],'coverage':['covered_m2','zone_m2','coverage_ratio'],
                                   'gaps':['uncovered_m2','residential_m2','uncovered_ratio']}[family],
                  'source':manifest['source'],'attribution':manifest['attribution'],
                  'boundary_policy':'距离边界纳入；学校以内部代表点归属当前完整区界；居住用地裁剪到区界',
                  'geometry_policy':manifest['policy'],'prompt':{
                      'proximity':f'统计{name}距离公交站500米以内的学校数量，输出选中学校和分区统计。',
                      'coverage':f'计算{name}公园并集覆盖面积和区界面积占比，重叠只算一次。',
                      'gaps':f'识别{name}居住用地中同时不在公交站500米范围和医院1000米范围内的部分，统计未覆盖面积和占居住用地并集比例。'}[family]}
            save(OUT/'tasks'/(task['id']+'.json'),task)
        print(name,info['counts'],flush=True)
    pairs=[]
    from itertools import combinations
    for a,b in combinations(names,2):
        overlap=all_geoms[a].intersection(all_geoms[b]).area
        if overlap>.1:pairs.append({'left':a,'right':b,'overlap_m2':overlap})
    manifest['boundary_overlap_pairs']=pairs
    save(OUT/'provenance.json',manifest)


if __name__=='__main__':main()
