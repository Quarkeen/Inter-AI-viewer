import hashlib
import json
import math
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from database import DATABASE_PATH
from interview_templates import read_config, must_ask_questions, validate_required_plan
from warning_evidence import checked_snapshot, store_warning, valid_id, warning_summary
from scheduling import _timestamp, _verify_approval, access_window, utc_now


COMPLETION_MESSAGE = "Interview is done, your will be informed through mail for any further communication, Thank you"


def _invitation(connection, token, require_open=True):
    if not isinstance(token, str) or not 20 <= len(token) <= 200:
        raise ValueError("This invitation is unavailable.")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = connection.execute(
        """SELECT * FROM interviews WHERE token_sha256 = ?
           AND status IN ('sent','sending','delivery_unknown')""", (token_hash,)
    ).fetchone()
    if row is None:
        raise ValueError("This invitation is unavailable.")
    _verify_approval(connection, row["run_id"], row["application_id"], row["evaluation_id"])
    opens, closes = access_window(row["starts_at"], row["ends_at"])
    if require_open and not opens <= utc_now() < closes:
        raise ValueError("The interview is outside its assigned time window.")
    return row


def expire_sessions(connection):
    connection.execute(
        """UPDATE interview_sessions SET status = 'expired', ended_at = CURRENT_TIMESTAMP
           WHERE status = 'active' AND slot_end <= ?""", (_timestamp(utc_now()),)
    )


def start_session(token, database_path=DATABASE_PATH, *, preflight_receipt=None):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        expire_sessions(connection)
        invitation = _invitation(connection, token, require_open=False)
        previous = connection.execute(
            "SELECT session_id FROM interview_sessions WHERE invitation_sha256 = ?", (invitation["token_sha256"],)
        ).fetchone()
        if previous:
            return previous[0]
        now = utc_now()
        opens, closes = access_window(invitation["starts_at"], invitation["ends_at"])
        if not opens <= now < closes:
            raise ValueError("The interview is outside its allowed start window.")
        if connection.execute("SELECT 1 FROM interview_sessions WHERE status = 'active'").fetchone():
            raise ValueError("Another interview is in progress. Please try again shortly.")
        from preflight import consume_receipt
        consume_receipt(connection, preflight_receipt, invitation["token_sha256"])
        session_id = secrets.token_hex(16)
        duration = datetime.fromisoformat(invitation["ends_at"]) - datetime.fromisoformat(invitation["starts_at"])
        deadline = _timestamp(now + duration)
        connection.execute(
            """INSERT INTO interview_sessions
               (session_id,interview_id,evaluation_id,invitation_sha256,status,slot_end,started_at,template_json)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
            (session_id, invitation["interview_id"], invitation["evaluation_id"], invitation["token_sha256"], deadline, now.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(read_config(invitation["template_json"]))),
        )
        return session_id


def _settle_termination(connection, invitation_sha256):
    """End a paused session whose HR-armed interruption countdown has run out.

    Commits immediately: callers usually raise right after the flip (a terminated
    session cannot pause, resume, or advance), and that rollback must not undo it.
    """
    row = connection.execute(
        "SELECT status, paused_at, terminate_at FROM interview_sessions WHERE invitation_sha256 = ?",
        (invitation_sha256,),
    ).fetchone()
    if row and row[0] == "active" and row[1] and row[2] and datetime.fromisoformat(row[2]) <= utc_now():
        connection.execute(
            """UPDATE interview_sessions SET status = 'terminated', ended_at = CURRENT_TIMESTAMP
               WHERE invitation_sha256 = ?""", (invitation_sha256,))
        connection.commit()


def get_session(token, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        expire_sessions(connection)
        invitation = _invitation(connection, token, require_open=False)
        _settle_termination(connection, invitation["token_sha256"])
        row = connection.execute(
            """SELECT s.*, c.full_name, a.job_title_at_application AS job_title
               FROM interview_sessions s JOIN interviews i ON i.interview_id = s.interview_id
               JOIN applications a ON a.application_id = i.application_id
               JOIN candidates c ON c.candidate_id = a.candidate_id
               WHERE s.invitation_sha256 = ?""", (invitation["token_sha256"],)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['proctoring'] = warning_summary(connection, result['session_id'])
        return result


def get_session_data(session_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """SELECT s.*, a.resume_text, a.jd_at_application, a.job_title_at_application
               FROM interview_sessions s JOIN interviews i ON i.interview_id = s.interview_id
               JOIN applications a ON a.application_id = i.application_id WHERE s.session_id = ?""", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError("This interview session no longer exists.")
        return dict(row)


def get_turns(session_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            """SELECT turn_id, sequence, kind, main_number, question, source_ids_json,
                      answer_text, answered_at, decision_json, received_at
               FROM interview_turns WHERE session_id = ? ORDER BY sequence""", (session_id,)
        )]


def claim_operation(token, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        expire_sessions(connection)
        invitation = _invitation(connection, token, require_open=False)
        _settle_termination(connection, invitation["token_sha256"])
        row = connection.execute(
            "SELECT * FROM interview_sessions WHERE invitation_sha256 = ?", (invitation["token_sha256"],)
        ).fetchone()
        if row is None or row["status"] != "active":
            raise ValueError("This interview is not active.")
        if row["paused_at"]:
            raise ValueError("Your interview is paused. Restore the required conditions to resume.")
        if row["busy_until"] and datetime.fromisoformat(row["busy_until"]) > utc_now():
            raise ValueError("Your last action is still processing. Please wait before trying again.")
        operation = secrets.token_hex(16)
        connection.execute(
            "UPDATE interview_sessions SET processing_id = ?, busy_until = ? WHERE session_id = ?",
            (operation, _timestamp(utc_now() + timedelta(minutes=8)), row["session_id"]),
        )
        return row["session_id"], operation


PAUSE_REASONS = ("fullscreen-required", "fullscreen-exit", "camera-ended", "face-absent")
TERMINATING_REASONS = ("fullscreen-required", "fullscreen-exit", "face-absent")
TERMINATE_AFTER_SECONDS = 60
TERMINATE_GRACE_SECONDS = 5
MAX_PAUSE_CREDIT_SECONDS = 600


def pause_session(token, reason, database_path=DATABASE_PATH):
    """Freeze a live interview; the timer credit is settled on resume. Idempotent.

    When HR armed automatic termination for this interview, fullscreen and
    presence pauses also start a 60-second countdown to termination. A lost
    camera never arms it: hardware faults pause, they do not end interviews.
    """
    if reason not in PAUSE_REASONS:
        raise ValueError("Unknown pause reason.")
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        expire_sessions(connection)
        invitation = _invitation(connection, token, require_open=False)
        _settle_termination(connection, invitation["token_sha256"])
        row = connection.execute(
            "SELECT status, paused_at, pause_reason, terminate_at FROM interview_sessions WHERE invitation_sha256 = ?",
            (invitation["token_sha256"],),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise ValueError("This interview is not active.")
        if row["paused_at"]:
            return {"paused_at": row["paused_at"], "pause_reason": row["pause_reason"], "terminate_at": row["terminate_at"]}
        now = utc_now()
        armed = bool(invitation["terminate_on_interruption"]) and reason in TERMINATING_REASONS
        terminate_at = _timestamp(now + timedelta(seconds=TERMINATE_AFTER_SECONDS)) if armed else None
        connection.execute(
            "UPDATE interview_sessions SET paused_at = ?, pause_reason = ?, terminate_at = ? WHERE invitation_sha256 = ?",
            (_timestamp(now), reason, terminate_at, invitation["token_sha256"]),
        )
        return {"paused_at": _timestamp(now), "pause_reason": reason, "terminate_at": terminate_at}


def resume_session(token, database_path=DATABASE_PATH):
    """Resume a paused interview, extending the deadline by the paused gap. The total
    credited pause time is capped so pausing cannot bank unlimited thinking time."""
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        expire_sessions(connection)
        invitation = _invitation(connection, token, require_open=False)
        _settle_termination(connection, invitation["token_sha256"])
        row = connection.execute(
            "SELECT status, slot_end, paused_at, paused_seconds FROM interview_sessions WHERE invitation_sha256 = ?",
            (invitation["token_sha256"],),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise ValueError("This interview is not active.")
        if not row["paused_at"]:
            return {"slot_end": row["slot_end"], "credited_seconds": 0}
        gap = int(max(0.0, (utc_now() - datetime.fromisoformat(row["paused_at"])).total_seconds()))
        credit = min(gap, max(0, MAX_PAUSE_CREDIT_SECONDS - (row["paused_seconds"] or 0)))
        slot_end = _timestamp(datetime.fromisoformat(row["slot_end"]) + timedelta(seconds=credit))
        connection.execute(
            """UPDATE interview_sessions SET slot_end = ?, paused_at = NULL, pause_reason = NULL, terminate_at = NULL,
               paused_seconds = ? WHERE invitation_sha256 = ?""",
            (slot_end, (row["paused_seconds"] or 0) + credit, invitation["token_sha256"]),
        )
        return {"slot_end": slot_end, "credited_seconds": credit}


def terminate_session(token, database_path=DATABASE_PATH):
    """End a paused interview whose HR-armed countdown ran out. Idempotent.

    Returns whether the interview is now terminated. A small grace covers the
    lag between the candidate's countdown reaching zero and this call arriving.
    """
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        expire_sessions(connection)
        invitation = _invitation(connection, token, require_open=False)
        _settle_termination(connection, invitation["token_sha256"])
        row = connection.execute(
            "SELECT status, paused_at, terminate_at FROM interview_sessions WHERE invitation_sha256 = ?",
            (invitation["token_sha256"],),
        ).fetchone()
        if row is None or row["status"] not in ("active", "terminated"):
            raise ValueError("This interview is not active.")
        if row["status"] == "terminated":
            return {"terminated": True, "remaining_seconds": 0}
        if not row["paused_at"] or not row["terminate_at"]:
            return {"terminated": False, "remaining_seconds": 0}
        remaining = (datetime.fromisoformat(row["terminate_at"]) - utc_now()).total_seconds()
        if remaining > TERMINATE_GRACE_SECONDS:
            return {"terminated": False, "remaining_seconds": int(math.ceil(remaining))}
        connection.execute(
            """UPDATE interview_sessions SET status = 'terminated', ended_at = CURRENT_TIMESTAMP
               WHERE invitation_sha256 = ?""", (invitation["token_sha256"],))
        return {"terminated": True, "remaining_seconds": 0}


def release_operation(session_id, operation, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            """UPDATE interview_sessions SET processing_id = NULL, busy_until = NULL
               WHERE session_id = ? AND processing_id = ?""", (session_id, operation)
        )


def _active(connection, session_id):
    row = connection.execute(
        """SELECT s.status, s.slot_end, i.run_id, i.application_id, s.evaluation_id
           FROM interview_sessions s JOIN interviews i ON i.interview_id = s.interview_id
           WHERE s.session_id = ?""", (session_id,)
    ).fetchone()
    if not row or row[0] != "active" or datetime.fromisoformat(row[1]) <= utc_now():
        raise ValueError("Your interview slot has ended.")
    _verify_approval(connection, row[2], row[3], row[4])


def save_plan(session_id, plan, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        _active(connection, session_id)
        config = read_config(connection.execute('SELECT template_json FROM interview_sessions WHERE session_id=?', (session_id,)).fetchone()[0])
        validate_required_plan(config, plan)
        connection.execute("UPDATE interview_sessions SET plan_json = ? WHERE session_id = ? AND plan_json IS NULL", (json.dumps(plan), session_id))


def save_question(session_id, question, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _active(connection, session_id)
        previous = connection.execute(
            "SELECT turn_id,sequence,answer_text FROM interview_turns WHERE session_id = ? ORDER BY sequence DESC LIMIT 1", (session_id,)
        ).fetchone()
        if previous and previous[2] is None:
            return previous[0]
        counts = dict(connection.execute("SELECT kind,count(*) FROM interview_turns WHERE session_id = ? GROUP BY kind", (session_id,)))
        config = read_config(connection.execute("SELECT template_json FROM interview_sessions WHERE session_id=?", (session_id,)).fetchone()[0])
        if not 1 <= question["main_number"] <= config["main_questions"]:
            raise ValueError("This question is outside the interview plan.")
        if counts.get("main", 0) >= config["main_questions"] and question["kind"] == "main" or counts.get("follow_up", 0) >= config["max_followups"] and question["kind"] == "follow_up":
            raise ValueError("The interview question limit has been reached.")
        required = must_ask_questions(config)
        if required:
            plan = json.loads(connection.execute('SELECT plan_json FROM interview_sessions WHERE session_id=?', (session_id,)).fetchone()[0] or '[]')
            validate_required_plan(config, plan)
            if question['kind'] == 'main':
                index = counts.get('main', 0)
                if (question['main_number'] != index + 1 or question['question'] != plan[index]['question']
                        or question['source_ids'] != plan[index]['source_ids']):
                    raise ValueError('Ask the next approved main question in its saved order.')
            elif question['kind'] == 'follow_up':
                deadline = connection.execute('SELECT slot_end FROM interview_sessions WHERE session_id=?', (session_id,)).fetchone()[0]
                if (datetime.fromisoformat(deadline) - utc_now()).total_seconds() <= max(75, (config['main_questions'] - counts.get('main', 0)) * 180):
                    raise ValueError('Reserve time for the remaining main questions before follow-ups.')
        if question['kind'] == 'follow_up':
            local_count = connection.execute("SELECT count(*) FROM interview_turns WHERE session_id=? AND kind='follow_up' AND main_number=?", (session_id, question['main_number'])).fetchone()[0]
            if question['main_number'] != counts.get('main', 0) or local_count >= 2:
                raise ValueError('Allow at most two follow-ups for the current main question.')
        if connection.execute("SELECT 1 FROM interview_turns WHERE session_id = ? AND lower(question) = lower(?)", (session_id, question["question"])).fetchone():
            raise ValueError("The interviewer generated a repeated question. Please retry.")
        cursor = connection.execute(
            """INSERT INTO interview_turns (session_id,sequence,kind,main_number,question,source_ids_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, previous[1] + 1 if previous else 1, question["kind"], question["main_number"],
             question["question"], json.dumps(question["source_ids"])),
        )
        return cursor.lastrowid


def save_recording(session_id, operation, turn_id, audio, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _active(connection, session_id)
        row = connection.execute(
            "SELECT processing_id FROM interview_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row or row[0] != operation:
            raise ValueError("The recording request has expired. Please try again.")
        cursor = connection.execute(
            """UPDATE interview_turns SET audio_wav = ?, audio_sha256 = ?, received_at = ?, answer_text = NULL
               WHERE session_id = ? AND turn_id = ? AND answer_text IS NULL
               AND turn_id = (SELECT max(turn_id) FROM interview_turns WHERE session_id = ?)""",
            (audio, hashlib.sha256(audio).hexdigest(), _timestamp(utc_now()), session_id, turn_id, session_id),
        )
        if not cursor.rowcount:
            raise ValueError("This question has already been answered. Refresh to continue.")


def save_transcript(session_id, operation, turn_id, transcript, database_path=DATABASE_PATH):
    if not transcript.strip():
        raise ValueError("No speech was detected. Please record your answer again.")
    with closing(sqlite3.connect(database_path)) as connection, connection:
        row = connection.execute(
            """SELECT t.received_at,s.slot_end,s.processing_id FROM interview_turns t
               JOIN interview_sessions s ON s.session_id = t.session_id
               WHERE t.turn_id = ? AND t.session_id = ?""", (turn_id, session_id)
        ).fetchone()
        if not row or row[2] != operation or not row[0] or row[0] >= row[1]:
            raise ValueError("This answer was not received within the assigned slot.")
        # Preserve an answer received on time even if local transcription ends later.
        connection.execute(
            "UPDATE interview_turns SET answer_text = ?, answered_at = CURRENT_TIMESTAMP WHERE turn_id = ? AND answer_text IS NULL",
            (transcript.strip(), turn_id),
        )


def save_decision(session_id, turn_id, decision, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _active(connection, session_id)
        cursor = connection.execute(
            "UPDATE interview_turns SET decision_json = ? WHERE session_id = ? AND turn_id = ? AND decision_json IS NULL",
            (json.dumps(decision), session_id, turn_id),
        )
        if cursor.rowcount:
            connection.execute("UPDATE interview_sessions SET summary = ? WHERE session_id = ?", (decision["summary"], session_id))


MONITOR_EVENT_KINDS = frozenset({
    "browser.visibility", "browser.focus", "browser.fullscreen", "browser.clipboard",
    "face.count", "camera.state", "gaze.state", "warn.started", "warn.cleared", "pause.started", "pause.ended",
})
MAX_EVENTS_PER_REQUEST = 200
MAX_EVENT_DETAIL_BYTES = 2000


def _checked_events(events):
    if not isinstance(events, list) or not events or len(events) > MAX_EVENTS_PER_REQUEST:
        raise ValueError("Send between 1 and 200 monitoring events at a time.")
    rows = []
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in MONITOR_EVENT_KINDS:
            raise ValueError("Unknown monitoring event.")
        text = str(event.get("at"))
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError("Each monitoring event needs a valid timestamp.") from None
        if stamp.tzinfo is None:
            raise ValueError("Each monitoring event needs a valid timestamp.") from None
        detail = event.get("detail", {})
        if not isinstance(detail, dict):
            raise ValueError("Invalid monitoring event detail.")
        encoded = json.dumps(detail)
        if len(encoded) > MAX_EVENT_DETAIL_BYTES:
            raise ValueError("A monitoring event was too large.")
        identity = event.get('id')
        if identity is not None and not valid_id(identity):
            raise ValueError('Invalid monitoring event identifier.')
        if event['kind'] in ('warn.started', 'warn.cleared') and detail.get('warning_id'):
            if not valid_id(detail['warning_id']) or detail.get('signal') not in ('face', 'fullscreen', 'camera', 'gaze'):
                raise ValueError('Invalid warning episode.')
        image = checked_snapshot(event)
        rows.append((str(event.get("at")), event["kind"], encoded, identity, image, event))
    return rows


def save_events(token, events, database_path=DATABASE_PATH):
    """Store browser-reported observations. The server stamps its own receipt time;
    Client reports remain review signals, while explicit HR warning limits are
    enforced transactionally. Retried event IDs never create additional warnings."""
    rows = _checked_events(events)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        invitation = _invitation(connection, token, require_open=False)
        session = connection.execute(
            "SELECT session_id FROM interview_sessions WHERE invitation_sha256 = ?", (invitation["token_sha256"],)
        ).fetchone()
        if session is None:
            raise ValueError("This interview session is unavailable.")
        now = _timestamp(utc_now())
        for client_at, kind, detail, identity, image, event in rows:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO interview_events(session_id,recorded_at,client_at,kind,detail_json,client_event_id)
                   VALUES(?,?,?,?,?,?)""", (session['session_id'], now, client_at, kind, detail, identity))
            if cursor.rowcount:
                canonical = store_warning(connection, session, invitation, event, now, image)
                if canonical and canonical != event.get('detail', {}).get('warning_id'):
                    canonical_detail = json.loads(detail) | {'warning_id': canonical}
                    connection.execute('UPDATE interview_events SET detail_json=? WHERE event_id=?', (json.dumps(canonical_detail), cursor.lastrowid))
        return len(rows)


def get_events(session_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            """SELECT event_id, recorded_at, client_at, kind, detail_json
               FROM interview_events WHERE session_id = ? ORDER BY event_id""", (session_id,))]


def complete_session(session_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            """UPDATE interview_sessions SET status = CASE WHEN slot_end <= ? THEN 'expired' ELSE 'completed' END,
               ended_at = CURRENT_TIMESTAMP WHERE session_id = ? AND status = 'active'""", (_timestamp(utc_now()), session_id)
        )
