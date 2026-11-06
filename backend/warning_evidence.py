"""Warning evidence is separate from interview scoring and its report cache."""
import base64
import binascii
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image, UnidentifiedImageError
from database import DATABASE_PATH

SIGNALS = {'face': 'Face not visible', 'fullscreen': 'Fullscreen exited', 'camera': 'Camera unavailable', 'gaze': 'Sustained look-away estimate (review only)'}
COUNTED_SIGNALS = ('face', 'fullscreen')


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value) is not None


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('A warning needs a valid timestamp.')
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('A warning needs a valid timestamp.') from None
    if moment.tzinfo is None:
        raise ValueError('A warning needs a timezone-aware timestamp.')
    return moment.astimezone(timezone.utc).isoformat(timespec='milliseconds')


def checked_snapshot(event):
    data = event.get('snapshot')
    if data is None:
        return None, None, 'Camera frame unavailable at warning time.'
    if event.get('kind') != 'warn.started' or not isinstance(data, str) or not data.startswith('data:image/jpeg;base64,') or len(data) > 170_000:
        raise ValueError('Invalid warning snapshot.')
    try:
        raw = base64.b64decode(data.split(',', 1)[1], validate=True)
        with Image.open(BytesIO(raw)) as image:
            if image.format != 'JPEG' or not 16 <= image.width <= 1280 or not 16 <= image.height <= 960:
                raise ValueError('Invalid warning snapshot dimensions.')
            image.load()
            image = image.convert('RGB')
            image.thumbnail((480, 360))
            clean = BytesIO()
            image.save(clean, format='JPEG', quality=78)
        captured_at = timestamp(event.get('snapshot_at'))
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('Invalid warning snapshot.') from None
    return clean.getvalue(), captured_at, ''


def warning_summary(connection, session_id):
    row = connection.execute('''SELECT i.terminate_on_interruption,i.warning_limit,s.status
        FROM interview_sessions s JOIN interviews i ON i.interview_id=s.interview_id WHERE s.session_id=?''', (session_id,)).fetchone()
    if row is None:
        raise ValueError('This interview session is unavailable.')
    count = connection.execute("SELECT count(*) FROM interview_warnings WHERE session_id=? AND signal IN ('face','fullscreen')", (session_id,)).fetchone()[0]
    active = {signal: identity for signal, identity in connection.execute('SELECT signal,warning_id FROM interview_warnings WHERE session_id=? AND cleared_at IS NULL ORDER BY recorded_at, rowid', (session_id,))}
    return dict(warning_count=count, warning_limit=row[1], terminate_on_interruption=bool(row[0]), active_warnings=active, terminated=row[2] == 'terminated')


def store_warning(connection, session, invitation, event, now, image):
    """One warning per episode; duplicate retries or a page reload cannot add a strike."""
    detail = event.get('detail', {})
    identity, signal = detail.get('warning_id'), detail.get('signal')
    if event['kind'] not in ('warn.started', 'warn.cleared') or not identity:
        return
    if not valid_id(identity) or signal not in SIGNALS:
        raise ValueError('Invalid warning episode.')
    sid = session['session_id']
    if event['kind'] == 'warn.cleared':
        connection.execute('UPDATE interview_warnings SET cleared_at=coalesce(cleared_at,?) WHERE session_id=? AND warning_id=? AND signal=?', (now, sid, identity, signal))
        return identity
    existing = connection.execute('SELECT signal FROM interview_warnings WHERE session_id=? AND warning_id=?', (sid, identity)).fetchone()
    if existing:
        if existing[0] != signal:
            raise ValueError('A warning identifier cannot change its signal.')
        return identity
    # Simultaneous tabs or a refreshed room still describe the same open episode.
    opened = connection.execute('SELECT warning_id FROM interview_warnings WHERE session_id=? AND signal=? AND cleared_at IS NULL', (sid, signal)).fetchone()
    if opened:
        return opened[0]
    jpeg, captured_at, note = image
    connection.execute('''INSERT INTO interview_warnings(session_id,warning_id,signal,recorded_at,client_at,snapshot_jpeg,snapshot_at,snapshot_note)
        VALUES(?,?,?,?,?,?,?,?)''', (sid, identity, signal, now, event['at'], jpeg, captured_at, note))
    count = warning_summary(connection, sid)['warning_count']
    if invitation['terminate_on_interruption'] and signal in COUNTED_SIGNALS and count > invitation['warning_limit']:
        connection.execute("""UPDATE interview_sessions SET status='terminated',pause_reason='warning-limit',
            ended_at=substr(?,1,19),terminate_at=NULL WHERE session_id=? AND status='active'""", (now, sid))
    return identity


def list_warnings(session_id, database_path=DATABASE_PATH, *, include_images=False):
    with closing(sqlite3.connect(database_path)) as c:
        c.row_factory = sqlite3.Row
        columns = 'rowid AS sequence,warning_id,signal,recorded_at,client_at,cleared_at,snapshot_at,snapshot_note,' + ('snapshot_jpeg' if include_images else 'snapshot_jpeg IS NOT NULL AS has_snapshot')
        rows = [dict(row) for row in c.execute(f'SELECT {columns} FROM interview_warnings WHERE session_id=? ORDER BY recorded_at,rowid', (session_id,))]
        ids = {row['warning_id'] for row in rows}
        # Older events never captured images. Retain every historical warning,
        # with an honest placeholder, without retroactively adding policy strikes.
        for row in c.execute("SELECT event_id,recorded_at,client_at,detail_json FROM interview_events WHERE session_id=? AND kind='warn.started' ORDER BY event_id", (session_id,)):
            detail = json.loads(row['detail_json'])
            if detail.get('warning_id') in ids:
                continue
            if detail.get('warning_id'):
                # A client may have upgraded before the backend. Keep its warning
                # even if only the old event logger received it, without counting
                # repeated deliveries of that episode more than once.
                ids.add(detail['warning_id'])
            rows.append(dict(sequence=row['event_id'],warning_id=f"legacy-{row['event_id']}",signal=detail.get('signal','unknown'),recorded_at=row['recorded_at'],client_at=row['client_at'],cleared_at=None,snapshot_at=None,snapshot_note='Snapshot capture was not available for this historical warning.',has_snapshot=False,**({'snapshot_jpeg': None} if include_images else {})))
    for row in rows:
        row['label'] = SIGNALS.get(row['signal'], 'Monitoring warning')
        if include_images:
            row['has_snapshot'] = row['snapshot_jpeg'] is not None
    return sorted(rows, key=lambda row: (row['recorded_at'], row['sequence']))


def gaze_monitoring_summary(session_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as c:
        states = [json.loads(row[0]).get('state') for row in c.execute(
            "SELECT detail_json FROM interview_events WHERE session_id=? AND kind='gaze.state' ORDER BY event_id", (session_id,))]
    if not states:
        return 'No gaze tracking record is available for this interview.'
    if 'enabled' not in states:
        return 'Gaze tracking was not calibrated or could not start. Standard monitoring observations are reported separately.'
    gaps = sum(state in ('uncertain', 'unavailable', 'suspended', 'calibrating') for state in states)
    return f'Calibrated gaze tracking was used during this interview; {gaps} tracking interruption(s) noted. Webcam estimates support human review only and do not establish cheating.'


def warning_snapshot(session_id, warning_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as c:
        row = c.execute('SELECT snapshot_jpeg FROM interview_warnings WHERE session_id=? AND warning_id=?', (session_id, warning_id)).fetchone()
    if not row or row[0] is None:
        raise ValueError('This warning snapshot is unavailable.')
    return row[0]
