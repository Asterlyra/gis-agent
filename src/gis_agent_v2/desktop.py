"""Local browser launcher and target-machine diagnostics. No API call during startup."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import uuid
import webbrowser
from .runtime import configure


def doctor(full=False):
    configure()
    from .config import ROOT, V2
    from .credentials import store
    from .data import list_tasks, load_task
    from .engine import QgisEngine
    from qgis.core import Qgis
    from osgeo import gdal
    import fastapi, httpx
    checks = {'python': sys.version.split()[0], 'qgis': Qgis.QGIS_VERSION,
              'gdal': gdal.VersionInfo(), 'tasks': len(list_tasks()), 'data_ready': (V2/'data/regions').is_dir(),
              'credential_store_available': store.available(), 'api_call_performed': False}
    task = load_task('jianghan-proximity')
    import tempfile
    with tempfile.TemporaryDirectory(prefix='gis-check-') as folder:
        engine = QgisEngine(task, folder, checks=True)
        near = engine.execute('within_distance', {'layer':'schools', 'reference':'stations', 'distance':500})
        checks['distance_selection'] = near['count']
    if full:
        if os.name == 'nt':
            target = 'GISAgent.SelfTest.' + uuid.uuid4().hex
            backend = store.backend()
            try:
                backend.set_password(target, 'probe', target)
                checks['credential_roundtrip'] = backend.get_password(target, 'probe') == target
            finally:
                backend.delete_password(target, 'probe')
        from .agent import Runner
        from .evaluation import score
        checks['offline_tasks'] = []
        for family in ('proximity', 'coverage', 'gaps'):
            task = load_task('jianghan-' + family)
            runner = Runner(task, 'C', 'offline', 'check-' + uuid.uuid4().hex[:12], purpose='test')
            meta = runner.run()
            verdict = score(task, runner.folder)
            checks['offline_tasks'].append({'task':task['id'], 'status':meta['status'], 'passed':verdict['passed']})
    checks['passed'] = (checks['tasks'] == 21 and checks['data_ready'] and checks['distance_selection'] == 29
                        and checks['credential_store_available'] and checks.get('credential_roundtrip', True)
                        and all(t['passed'] for t in checks.get('offline_tasks', [])))
    output = V2/'logs/windows-check.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks['passed'] else 1


def serve():
    configure()
    from .config import ROOT, V2
    # Check writable output location before opening the browser.
    (V2/'logs').mkdir(parents=True, exist_ok=True)
    from .web import app
    import uvicorn
    listener = socket.socket()
    if os.name == 'nt':
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    for port in range(8766, 8787):
        try:
            listener.bind(('127.0.0.1', port))
            break
        except OSError:
            continue
    else:
        raise RuntimeError('8766—8786端口均被占用，请关闭已有程序。')
    listener.listen(128)
    url = f'http://127.0.0.1:{port}/'
    state = V2/'logs/desktop.json'
    info = {'pid': os.getpid(), 'url': url, 'root':str(ROOT), 'started':time.time()}
    state.write_text(json.dumps(info), encoding='utf-8')
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    def open_when_ready():
        while not server.started and not server.should_exit:
            time.sleep(.1)
        if server.started:
            webbrowser.open(url)
    threading.Thread(target=open_when_ready, daemon=True).start()
    print('GIS Agent: ' + url, flush=True)
    print('Use Stop.cmd to stop the service and its analysis workers.', flush=True)
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
        try:
            if json.loads(state.read_text(encoding='utf-8'))['pid'] == os.getpid():
                state.unlink()
        except (OSError, ValueError, KeyError):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    return doctor(args.full) if args.check else serve()


if __name__ == '__main__':
    raise SystemExit(main())
