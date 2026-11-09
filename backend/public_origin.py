"""Use the temporary public invitation origin only while its tunnel is alive."""
import json
import os
from pathlib import Path
import re

STATE_PATH = Path(__file__).resolve().parents[1] / '.run' / 'share' / 'state.json'


def process_identity(pid):
    try:
        os.kill(int(pid), 0)
        stat = Path('/proc') / str(int(pid)) / 'stat'
        if stat.exists():
            fields = stat.read_text().rsplit(')', 1)[1].split()
            return None if fields[0] == 'Z' else fields[19]
        return 'running'
    except (OSError, ValueError, TypeError, IndexError):
        return None


def active_public_origin(state_path=STATE_PATH, *, require_ready=False):
    state_path = Path(state_path)
    result = None
    try:
        state = json.loads(state_path.read_text())
        url = state.get('public_url', '')
        processes = [state.get(name, {}) for name in ('tunnel', 'gateway')]
        if (re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com', url)
                and all(p.get('identity') and process_identity(p.get('pid')) == p['identity'] for p in processes)):
            result = url
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if result is None and require_ready and state_path.exists():
        raise ValueError('Public interview sharing is not ready. Restart it before sending invitations.')
    return result
