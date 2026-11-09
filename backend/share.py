"""Start/stop a temporary candidate-only Cloudflare Quick Tunnel."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from public_origin import STATE_PATH, active_public_origin, process_identity

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = STATE_PATH.parent


def read_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def write_state(state):
    temporary = STATE_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2))
    temporary.chmod(0o600)
    temporary.replace(STATE_PATH)


def spawn(command, name):
    log_path = RUNTIME / (name + '.log')
    with log_path.open('wb') as log:
        log_path.chmod(0o600)
        process = subprocess.Popen(command, cwd=ROOT / 'backend', stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
    identity = process_identity(process.pid)
    if identity is None:
        raise RuntimeError(f'{name} did not start. See {log_path}')
    return {'pid': process.pid, 'identity': identity, 'log': str(log_path)}


def stop_process(info):
    if not info or process_identity(info.get('pid')) != info.get('identity'):
        return
    os.kill(info['pid'], signal.SIGTERM)
    for _ in range(50):
        if process_identity(info['pid']) != info['identity']:
            return
        time.sleep(.1)
    raise RuntimeError('A sharing process did not stop. See its runtime log before restarting.')


def stop(state):
    stop_process(state.get('tunnel'))
    stop_process(state.get('gateway'))
    STATE_PATH.unlink(missing_ok=True)


def get(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=8) as response:
        return response.read()


def start():
    old = read_state()
    if active_public_origin():
        print(json.dumps({'public_url': active_public_origin(), 'status': 'already running'}))
        return
    if old:
        stop(old)
    try:
        if json.loads(get('http://127.0.0.1:8000/api/health')) != {'status': 'ok'}:
            raise ValueError('Unexpected health response')
    except Exception:
        raise RuntimeError('Start the private API first: .venv/bin/python backend/run.py') from None
    binary = shutil.which('cloudflared') or str(ROOT / '.run' / 'bin' / 'cloudflared')
    if not Path(binary).is_file():
        raise RuntimeError('Install cloudflared first; see REMOTE_DEMO.md.')
    environment = {**os.environ, 'VITE_API_BASE_URL': ''}
    subprocess.run(['npm', 'run', 'build'], cwd=ROOT / 'frontend', env=environment, check=True)
    state = {}
    try:
        state['gateway'] = spawn([
            sys.executable, '-m', 'uvicorn', 'public_gateway:app', '--host', '127.0.0.1',
            '--port', '8080', '--no-access-log',
        ], 'gateway')
        write_state(state)
        for _ in range(60):
            try:
                if json.loads(get('http://127.0.0.1:8080/api/health')) == {'status': 'ok'}:
                    break
            except Exception:
                pass
            if process_identity(state['gateway']['pid']) != state['gateway']['identity']:
                raise RuntimeError('Candidate gateway exited; check .run/share/gateway.log.')
            time.sleep(.25)
        else:
            raise RuntimeError('Candidate gateway did not become ready.')
        state['tunnel'] = spawn([
            binary, 'tunnel', '--no-autoupdate', '--protocol', 'http2', '--url', 'http://127.0.0.1:8080',
        ], 'cloudflared')
        write_state(state)
        for _ in range(180):
            log = Path(state['tunnel']['log']).read_text(errors='replace')
            url = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', log)
            if url and 'Registered tunnel connection' in log:
                state['public_url'] = url[0]
                break
            if process_identity(state['tunnel']['pid']) != state['tunnel']['identity']:
                raise RuntimeError('Cloudflare Tunnel exited; check .run/share/cloudflared.log.')
            time.sleep(.5)
        else:
            raise RuntimeError('The tunnel did not connect; check .run/share/cloudflared.log.')
        last_error = 'No valid health response received.'
        deadline = time.monotonic() + 120
        print('Tunnel connected; waiting for public HTTPS routing to become ready.', flush=True)
        while time.monotonic() < deadline:
            try:
                if json.loads(get(state['public_url'] + '/api/health')) == {'status': 'ok'}:
                    break
            except Exception as error:
                last_error = str(error)
            time.sleep(1)
        else:
            raise RuntimeError('The public HTTPS address did not pass its health check: ' + last_error)
        write_state(state)
        print(json.dumps({
            'public_url': state['public_url'], 'status': 'ready',
            'local_hr': 'http://localhost:5173', 'candidate_path': '/interview?token=PERSONAL_INVITATION_TOKEN',
            'stop_command': '.venv/bin/python backend/share.py stop',
        }), flush=True)
    except BaseException:
        stop(state)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'stop'))
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUNTIME / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == 'start':
            start()
        elif args.action == 'stop':
            stop(read_state())
            print('Candidate sharing stopped. The private app and interview data are retained.')
        else:
            url = active_public_origin()
            print(json.dumps({'status': 'running' if url else 'stopped', 'public_url': url}))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
