import argparse
import json
from .runtime import configure
configure()
from .config import V2
from .data import load_task,list_tasks


def main():
    parser=argparse.ArgumentParser(description='真实城市 GIS Agent V2');sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('tasks')
    p=sub.add_parser('run');p.add_argument('--task',required=True);p.add_argument('--mode',choices=['offline','live'],default='offline')
    p.add_argument('--group',choices=['A','B','C'],default='C');p.add_argument('--run-id');p.add_argument('--purpose',default='development');p.add_argument('--task-file')
    p=sub.add_parser('serve');p.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    if args.command=='tasks':print(json.dumps(list_tasks(),ensure_ascii=False,indent=2));return 0
    if args.command=='serve':
        import uvicorn
        uvicorn.run('gis_agent_v2.web:app',host='127.0.0.1',port=args.port,log_level='warning');return 0
    from .agent import Runner
    from pathlib import Path
    task=json.loads(Path(args.task_file).read_text(encoding='utf-8')) if args.task_file else load_task(args.task)
    runner=Runner(task,args.group,args.mode,args.run_id,args.purpose);meta=runner.run()
    from .evaluation import score
    verdict=score(task,runner.folder)
    print(json.dumps({'run':meta,'score':verdict},ensure_ascii=False))
    return 0 if meta['status']=='completed' and verdict['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
