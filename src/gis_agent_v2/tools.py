from gis_agent.tools import TOOLS as BASE_TOOLS, tool

TOOLS=BASE_TOOLS+[
    tool('difference','从 layer 减去 overlay 的覆盖几何，保留 layer 属性；用于未覆盖区域。要求相同 CRS。',
         {'layer':'被减图层句柄','overlay':'覆盖范围句柄'},['layer','overlay']),
    tool('merge','合并两个几何类型兼容图层，保留记录；重叠仍存在，求并集需另用 dissolve。',
         {'left':'左图层句柄','right':'右图层句柄'},['left','right'])]
STEP={'type':'object','properties':{'id':{'type':'string'},'purpose':{'type':'string'},
      'tool':{'type':'string','enum':[t['function']['name'] for t in TOOLS]},
      'arguments':{'type':'object','additionalProperties':True}},'required':['id','purpose','tool','arguments'],
      'additionalProperties':False}


def plan_tool(name,description):
    return {'type':'function','function':{'name':name,'description':description,
            'parameters':{'type':'object','properties':{'steps':{'type':'array','items':STEP,'minItems':1,'maxItems':35}},
                          'required':['steps'],'additionalProperties':False}}}


PLAN_TOOLS=[plan_tool('submit_plan','提交按依赖排序的完整分析计划并执行。arguments 中图层引用输入角色名或 $步骤id，例如 $project。必须以 finalize 结束；它是分析计划，不包含统计答案。返回实际图层摘要或阻塞步骤。'),
            plan_tool('repair_plan','失败后提交修订后的完整计划。语义未变化且依赖未失效的步骤会复用；受影响步骤与下游重算。不能读取参考答案。最多两次修订。')]
