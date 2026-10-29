"""计划校验、来源引用、依赖失效和局部重算，不接触评估器。"""
import json
import re
from .data import save_json
from .engine import ToolError
from .tools import TOOLS


def dependencies(arguments):
    return {v[1:] for v in arguments.values() if isinstance(v,str) and v.startswith('$')}


def validate_plan(steps,inputs):
    allowed={t['function']['name'] for t in TOOLS}
    if not isinstance(steps,list) or not 1<=len(steps)<=35:
        raise ToolError('INVALID_PLAN','计划需为 1—35 个步骤')
    seen=set(); producers=set()
    for step in steps:
        if not isinstance(step,dict) or set(step)!={'id','purpose','tool','arguments'}:
            raise ToolError('INVALID_PLAN','每步需要 id、purpose、tool、arguments')
        key=step['id']
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}',key) or key in seen or key in inputs:
            raise ToolError('INVALID_PLAN','步骤 ID 重复、与输入冲突或格式不合法')
        if step['tool'] not in allowed or not isinstance(step['arguments'],dict) or not isinstance(step['purpose'],str):
            raise ToolError('INVALID_PLAN','工具名或参数格式错误')
        if not dependencies(step['arguments']).issubset(seen):
            raise ToolError('INVALID_PLAN','引用必须指向之前步骤，格式为 $步骤id；不允许环或未知依赖')
        if not dependencies(step['arguments']).issubset(producers):
            raise ToolError('INVALID_PLAN','只能引用产生图层或统计表的步骤，describe 不产生图层')
        seen.add(key)
        if step['tool'] not in {'describe','finalize'}:producers.add(key)
    if steps[-1]['tool']!='finalize' or sum(s['tool']=='finalize' for s in steps)!=1:
        raise ToolError('INVALID_PLAN','计划必须且只能以一个 finalize 结束')
    return steps


class PlanExecutor:
    def __init__(self,runner):
        self.runner=runner;self.steps=[];self.results={};self.states={};self.revision=0

    def persist(self):
        save_json(self.runner.folder/'plan.json',{'revision':self.revision,'steps':self.steps,
                                                'states':self.states,'outputs':self.results})

    def install(self,steps):
        validate_plan(steps,self.runner.task['inputs'])
        old={s['id']:s for s in self.steps};keep={};invalid=[]
        for s in steps:
            key=s['id'];prev=old.get(key)
            same=prev and prev['tool']==s['tool'] and prev['arguments']==s['arguments']
            if same and key in self.results and dependencies(s['arguments']).issubset(keep):
                keep[key]=self.results[key]
            else:invalid.append(key)
        self.steps=steps;self.results=keep;self.revision+=1
        self.states={s['id']:('reused' if s['id'] in keep else 'pending') for s in steps}
        self.persist()
        self.runner.event('plan',revision=self.revision,steps=steps,reused=list(keep),invalidated=invalid)

    def execute(self):
        for step in self.steps:
            key=step['id']
            if key in self.results:continue
            arguments={k:self.results[v[1:]]['layer'] if isinstance(v,str) and v.startswith('$') else v
                       for k,v in step['arguments'].items()}
            self.states[key]='running';self.persist()
            result=self.runner.call_tool(step['tool'],arguments,step_id=key,purpose=step['purpose'])
            if not result['ok']:
                self.states[key]='blocked';self.persist()
                return {'ok':False,'blocked_step':step,'diagnostic':result,
                        'completed_steps':list(self.results),'instruction':'修订完整计划，保持正确步骤的 ID 与参数；依赖受影响的下游会重算。'}
            self.results[key]=result;self.states[key]='completed';self.persist()
        return {'ok':True,'completed':self.runner.engine.finished,
                'step_count':len(self.steps),'revision':self.revision}
