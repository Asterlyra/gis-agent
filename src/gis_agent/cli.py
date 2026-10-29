import argparse
import json
import sys
import subprocess
from pathlib import Path
from .config import ROOT, Settings
from .data import make_samples, load_task, save_json


def main():
    parser = argparse.ArgumentParser(description='科报会 GIS 智能体')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='生成六个构造任务')
    commands.add_parser('doctor', help='检查 QGIS 与模型配置，不发起模型请求')
    run = commands.add_parser('run')
    run.add_argument('--task', choices=[f'T{i}' for i in range(1, 7)], default='T1')
    run.add_argument('--group', choices=['A', 'B', 'C'], default='C')
    run.add_argument('--mode', choices=['offline', 'live'], default='offline')
    run.add_argument('--run-id')
    run.add_argument('--prompt-file', type=Path)
    run.add_argument('--purpose', choices=['smoke', 'experiment', 'validation'])
    commands.add_parser('summarize')
    rescore = commands.add_parser('rescore', help='重新核验已有成果，不重新调用模型')
    rescore.add_argument('--run-id', required=True)
    batch = commands.add_parser('batch', help='固定矩阵；live 模式必须显式选择')
    batch.add_argument('--mode', choices=['offline', 'live'], default='offline')
    batch.add_argument('--tasks', nargs='+', default=[f'T{i}' for i in range(1, 7)])
    batch.add_argument('--groups', nargs='+', choices=['A', 'B', 'C'], default=['A', 'B', 'C'])
    web = commands.add_parser('serve')
    web.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.command == 'init':
        make_samples()
        print('已生成六个构造任务；未运行模型实验。')
    elif args.command == 'doctor':
        from .engine import QgisEngine
        engine = QgisEngine(load_task('T1'), ROOT / 'runs' / 'doctor')
        print(json.dumps({'qgis': engine.version, 'python': sys.version.split()[0],
                          'provider': Settings.load().public(), 'inputs': engine.tool_describe()}, ensure_ascii=False, indent=2))
    elif args.command == 'run':
        from .agent import Runner
        task = load_task(args.task)
        if args.prompt_file:
            task['prompt'] = args.prompt_file.read_text()[:4000]
            if task['family'] == 'proximity':
                import re
                distances = re.findall(r'(\d+(?:\.\d+)?)\s*(?:米|m\b)', task['prompt'])
                if len(distances) == 1:
                    task['distance_m'] = float(distances[0])
        runner = Runner(task, group=args.group, mode=args.mode, run_id=args.run_id, purpose=args.purpose)
        meta = runner.run()
        # 运行完成之后才加载参考流程，模型上下文和工具从未获得参考答案。
        from .evaluation import score
        verdict = score(task, runner.folder)
        print(json.dumps({'run': meta, 'score': verdict}, ensure_ascii=False, indent=2))
        return 0 if meta['status'] == 'completed' and verdict['passed'] else 1
    elif args.command == 'summarize':
        from .evaluation import summarize
        print(json.dumps(summarize(), ensure_ascii=False, indent=2))
    elif args.command == 'rescore':
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.run_id):
            parser.error('运行 ID 无效')
        folder = ROOT / 'runs' / args.run_id
        meta = json.loads((folder / 'run.json').read_text())
        task = json.loads((folder / 'task.json').read_text()) if (folder / 'task.json').exists() else load_task(meta['task_id'])
        from .engine import QgisEngine
        QgisEngine(task, ROOT / 'runs' / 'doctor')
        from .evaluation import score
        print(json.dumps(score(task, folder), ensure_ascii=False, indent=2))
    elif args.command == 'batch':
        failures = 0
        for task in args.tasks:
            load_task(task)
            for group in args.groups:
                status = subprocess.run([sys.executable, '-m', 'gis_agent.cli', 'run', '--task', task,
                                         '--group', group, '--mode', args.mode, '--purpose',
                                         'experiment' if args.mode=='live' else 'validation'], capture_output=True, text=True)
                failures += status.returncode != 0
                print(f'{task}/{group}/{args.mode}: ' + ('通过' if status.returncode == 0 else '失败（查看运行日志）'), flush=True)
        from .evaluation import summarize
        summarize()
        return 1 if failures else 0
    elif args.command == 'serve':
        import uvicorn
        uvicorn.run('gis_agent.web:app', host='127.0.0.1', port=args.port, log_level='warning')
    return 0


if __name__ == '__main__':
    sys.exit(main())
