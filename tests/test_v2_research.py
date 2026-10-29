import copy,json,uuid
from pathlib import Path
import pytest
from gis_agent_v2.agent import Runner,canonical_plan
from gis_agent_v2.data import load_task
from gis_agent_v2.engine import QgisEngine,ToolError
from gis_agent_v2.evaluation import score
from gis_agent_v2.plan import validate_plan
from gis_agent_v2.web import app
from fastapi.testclient import TestClient

def runner(family='coverage',group='C'):
    return Runner(load_task('jianghan-'+family),group,'offline','test-v2-'+uuid.uuid4().hex[:12],purpose='test')

@pytest.mark.parametrize('family',['proximity','coverage','gaps'])
def test_real_data_end_to_end(family):
    r=runner(family);assert r.run()['status']=='completed';assert score(r.task,r.folder)['passed']
    from osgeo import ogr
    ds=ogr.Open(str(r.folder/'artifacts/results.gpkg'))
    assert ds.GetLayerByName('selected' if family=='proximity' else 'overlay') is not None

@pytest.mark.parametrize('mutation',['forward','unknown','duplicate','no_final','non_layer'])
def test_dependency_gate(mutation):
    task=load_task('jianghan-coverage');steps=canonical_plan(task)
    if mutation=='forward':steps[0]['arguments']['layer']='$clip'
    if mutation=='unknown':steps[0]['tool']='run_shell'
    if mutation=='duplicate':steps[1]['id']=steps[0]['id']
    if mutation=='no_final':steps[-1]['tool']='describe'
    if mutation=='non_layer':steps[0]['tool']='describe';steps[0]['arguments']={'layer':'parks'}
    with pytest.raises(ToolError):validate_plan(steps,task['inputs'])

def test_overlap_detection_and_local_repair():
    r=runner();correct=canonical_plan(r.task);bad=copy.deepcopy(correct)
    # 删除求并集步骤，把裁剪源改回公园输入：由真实交叠数据触发运行检查。
    bad=[s for s in bad if s['id']!='park_union']
    next(s for s in bad if s['id']=='clip')['arguments']['layer']='$project_parks'
    r.plan.install(bad);result=r.plan.execute()
    assert not result['ok'] and result['diagnostic']['code']=='OVERLAPPING_AREA'
    assert r.plan.states['areas']=='blocked'
    r.plan.install(correct)
    assert r.plan.states['project_zones']=='reused' and r.plan.states['project_parks']=='reused'
    assert r.plan.states['clip']=='pending' and r.plan.states['areas']=='pending'
    assert r.plan.execute()['ok'];assert score(r.task,r.folder)['passed']
    r.meta['status']='completed';r.persist()

def test_b_does_not_apply_c_overlap_gate():
    r=runner(group='B');clipped=r.engine.execute('intersection',{'layer':'parks','overlay':'zones'})['layer']
    result=r.engine.execute('calculate',{'layer':clipped,'field':'area','operation':'area_m2'})
    assert result['ok']

def test_independent_scorer_rejects_corrupted_export():
    r=runner('proximity');assert r.run()['status']=='completed'
    p=r.folder/'artifacts/selected.geojson';data=json.loads(p.read_text());data['features'].pop();p.write_text(json.dumps(data))
    assert not score(r.task,r.folder)['passed']

def test_web_contract_and_boundaries():
    c=TestClient(app);assert c.get('/api/health').status_code==200
    schema=c.get('/api/schema').json();assert len(schema['tools'])==16
    assert 'api_key' not in c.get('/api/health').text
    assert c.post('/api/run',json={'task_id':'jianghan-proximity'},headers={'origin':'https://unrelated.example'}).status_code==403
    assert c.post('/api/run',json={'task_id':'jianghan-proximity','distance_m':0}).status_code==422
    assert c.get('/api/regions/jianghan/.env.local').status_code==404
    assert c.get('/api/runs/absent/artifacts/worker.log').status_code==404
