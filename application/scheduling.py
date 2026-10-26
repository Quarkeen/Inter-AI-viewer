import hashlib
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from database import DATABASE_PATH
from shortlisting import _checked_evaluation


JOIN_BUFFER = timedelta(minutes=10)
SLOT_CONFLICT = "An interview is already scheduled during this time or its reserved window. Please choose a different slot."


def _check_slot_conflicts(connection, starts_at, ends_at, exclude_id=None):
    busy_start, busy_end = reserved_window(starts_at, ends_at)
    for interview_id, start, end in connection.execute(
        "SELECT interview_id,starts_at,ends_at FROM interviews"
    ):
        if interview_id == exclude_id:
            continue
        other_start, other_end = reserved_window(start, end)
        if busy_start < other_end and busy_end > other_start:
            raise ValueError(SLOT_CONFLICT)


def access_window(starts_at, ends_at):
    """Allowed start window, separate from an interview session's deadline."""
    return datetime.fromisoformat(starts_at) - JOIN_BUFFER, datetime.fromisoformat(ends_at) + JOIN_BUFFER


def reserved_window(starts_at, ends_at):
    opens, closes = access_window(starts_at, ends_at)
    duration = datetime.fromisoformat(ends_at) - datetime.fromisoformat(starts_at)
    return opens, closes + duration


def utc_now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Choose an interview time with an explicit timezone.")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _verify_approval(connection, run_id, application_id, evaluation_id):
    row = connection.execute(
        """SELECT r.job_id FROM shortlist_items i
           JOIN shortlist_runs r ON r.run_id = i.run_id
           WHERE i.run_id = ? AND i.application_id = ? AND i.evaluation_id = ?
             AND i.approved = 1 AND r.status = 'completed'
             AND r.rowid = (SELECT max(rowid) FROM shortlist_runs WHERE job_id = r.job_id)""",
        (run_id, application_id, evaluation_id),
    ).fetchone()
    if row is None:
        raise ValueError("This application needs approval in the latest shortlist before scheduling or sending.")
    _checked_evaluation(connection, application_id, evaluation_id, row[0])


def get_approved_candidates(run_id, database_path=DATABASE_PATH): # getting approved candidates from database saved by shortlist review
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            """SELECT i.application_id, i.evaluation_id, c.full_name, c.email, e.score
               FROM shortlist_items i JOIN shortlist_runs r ON r.run_id = i.run_id
               JOIN applications a ON a.application_id = i.application_id
               JOIN candidates c ON c.candidate_id = a.candidate_id
               JOIN resume_evaluations e ON e.evaluation_id = i.evaluation_id
               WHERE i.run_id = ? AND i.approved = 1 AND r.status = 'completed'
               ORDER BY e.score DESC, i.application_id""", (run_id,)
        )]


def schedule_interview(run_id, application_id, starts_at, duration_minutes, database_path=DATABASE_PATH):
    """Reserve one candidate's slot; invitation creation remains a separate HR action."""
    starts_at = datetime.fromisoformat(_timestamp(starts_at))
    if starts_at <= utc_now() or type(duration_minutes) is not int or not 5 <= duration_minutes <= 60:
        raise ValueError("Choose a future start time and a duration between 5 and 60 minutes.")
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        item = connection.execute(
            "SELECT evaluation_id FROM shortlist_items WHERE run_id = ? AND application_id = ? AND approved = 1",
            (run_id, application_id),
        ).fetchone()
        if not item:
            raise ValueError("This application needs approval in the latest shortlist before scheduling or sending.")
        _verify_approval(connection, run_id, application_id, item[0])
        if connection.execute("SELECT 1 FROM interviews WHERE application_id = ?", (application_id,)).fetchone():
            raise ValueError("This candidate already has a slot. Use Reschedule to change it.")
        _check_slot_conflicts(connection, _timestamp(starts_at), _timestamp(ends_at))
        cursor = connection.execute(
            "INSERT INTO interviews (application_id,run_id,evaluation_id,starts_at,ends_at) VALUES (?, ?, ?, ?, ?)",
            (application_id, run_id, item[0], _timestamp(starts_at), _timestamp(ends_at)),
        )
        return cursor.lastrowid


def schedule_interviews(run_id, first_start, duration_minutes, database_path=DATABASE_PATH):
    first_start = datetime.fromisoformat(_timestamp(first_start))
    if first_start <= utc_now():
        raise ValueError("Choose a first interview time in the future.")
    if type(duration_minutes) is not int or not 5 <= duration_minutes <= 60:
        raise ValueError("Choose an interview duration between 5 and 60 minutes.")
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        candidates = connection.execute(
            """SELECT i.application_id, i.evaluation_id FROM shortlist_items i
               JOIN resume_evaluations e ON e.evaluation_id = i.evaluation_id
               WHERE i.run_id = ? AND i.approved = 1
               ORDER BY e.score DESC, i.application_id""", (run_id,)
        ).fetchall()
        if not candidates:
            raise ValueError("This review has no approved applications to schedule.")
        pending = []
        for application_id, evaluation_id in candidates:
            _verify_approval(connection, run_id, application_id, evaluation_id)
            existing = connection.execute(
                "SELECT status FROM interviews WHERE application_id = ?", (application_id,)
            ).fetchone()
            if existing is None or existing[0] == "scheduled":
                pending.append((application_id, evaluation_id))
        if not pending:
            raise ValueError("All approved applications already have invitations in progress or sent.")
        excluded = {item[0] for item in pending}
        occupied = [
            reserved_window(start, end)
            for app_id, start, end in connection.execute("SELECT application_id, starts_at, ends_at FROM interviews")
            if app_id not in excluded
        ]
        slot_start = first_start
        for application_id, evaluation_id in pending:
            slot_end = slot_start + timedelta(minutes=duration_minutes)
            busy_start, busy_end = reserved_window(_timestamp(slot_start), _timestamp(slot_end))
            if any(busy_start < end and busy_end > start for start, end in occupied):
                raise ValueError(SLOT_CONFLICT)
            connection.execute(
                """INSERT INTO interviews (application_id, run_id, evaluation_id, starts_at, ends_at)
                   VALUES (?, ?, ?, ?, ?) ON CONFLICT(application_id) DO UPDATE SET
                   run_id = excluded.run_id, evaluation_id = excluded.evaluation_id,
                   starts_at = excluded.starts_at, ends_at = excluded.ends_at""",
                (application_id, run_id, evaluation_id, _timestamp(slot_start), _timestamp(slot_end)),
            )
            occupied.append((busy_start, busy_end))
            slot_start = busy_end + JOIN_BUFFER
    return len(pending)


def get_interviews(job_id=None, database_path=DATABASE_PATH):
    query = """SELECT t.*, c.full_name, c.email, a.job_id,
                      EXISTS(SELECT 1 FROM rescheduled_invitations h WHERE h.interview_id=t.interview_id) AS is_rescheduled,
                      a.job_title_at_application AS job_title
               FROM interviews t JOIN applications a ON a.application_id = t.application_id
               JOIN candidates c ON c.candidate_id = a.candidate_id"""
    parameters = ()
    if job_id is not None:
        query += " WHERE a.job_id = ?"
        parameters = (job_id,)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query + " ORDER BY t.starts_at", parameters)]


def check_scheduled_approval(interview_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(
            "SELECT run_id, application_id, evaluation_id FROM interviews WHERE interview_id = ?", (interview_id,)
        ).fetchone()
        if row is None:
            raise ValueError("This interview no longer exists.")
        _verify_approval(connection, *row)


def validate_base_url(base_url):
    parsed = urlsplit(base_url.strip())
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Configure a valid APP_BASE_URL without credentials or query parameters.")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Use HTTPS for an interview address outside localhost.")
    return base_url.strip().rstrip("/")


def claim_invitation(interview_id, base_url, database_path=DATABASE_PATH):
    base_url = validate_base_url(base_url)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """SELECT t.*, c.full_name, c.email, a.job_title_at_application AS job_title
               FROM interviews t JOIN applications a ON a.application_id = t.application_id
               JOIN candidates c ON c.candidate_id = a.candidate_id WHERE t.interview_id = ?""", (interview_id,)
        ).fetchone()
        if row is None or row["status"] != "scheduled":
            raise ValueError("This invitation is already sent or a sending attempt is in progress.")
        _verify_approval(connection, row["run_id"], row["application_id"], row["evaluation_id"])
        if access_window(row["starts_at"], row["ends_at"])[1] <= utc_now():
            raise ValueError("This slot has expired. Schedule a future slot before sending.")
        _check_slot_conflicts(connection, row["starts_at"], row["ends_at"], row["interview_id"])
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        connection.execute(
            "UPDATE interviews SET status = 'sending', token_sha256 = ?, recipient_email = ? WHERE interview_id = ?",
            (token_hash, row["email"], interview_id),
        )
        invitation = dict(row)
        invitation["is_rescheduled"] = bool(connection.execute(
            "SELECT 1 FROM rescheduled_invitations WHERE interview_id = ? LIMIT 1", (interview_id,)
        ).fetchone())
        invitation["interview_url"] = f"{base_url}/interview?token={token}"
        return invitation


def finish_invitation(interview_id, status, message_id=None, database_path=DATABASE_PATH):
    if status not in ("sent", "scheduled", "delivery_unknown"):
        raise ValueError("Invalid invitation status.")
    with closing(sqlite3.connect(database_path)) as connection, connection:
        if status == "scheduled":
            connection.execute(
                """UPDATE interviews SET status = 'scheduled', token_sha256 = NULL, recipient_email = NULL
                   WHERE interview_id = ? AND status = 'sending'""", (interview_id,)
            )
        else:
            connection.execute(
                """UPDATE interviews SET status = ?, message_id = ?,
                   sent_at = CASE WHEN ? = 'sent' THEN CURRENT_TIMESTAMP ELSE NULL END
                   WHERE interview_id = ? AND status = 'sending'""", (status, message_id, status, interview_id)
            )


def get_invitation_access(token, database_path=DATABASE_PATH):
    if not isinstance(token, str) or not 20 <= len(token) <= 200:
        return {"state": "invalid"}
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM interviews WHERE token_sha256 = ? AND status IN ('sending','sent','delivery_unknown')", (token_hash,)
        ).fetchone()
        if row is None:
            if connection.execute(
                "SELECT 1 FROM rescheduled_invitations WHERE token_sha256 = ?", (token_hash,)
            ).fetchone():
                return {"state": "rescheduled"}
            return {"state": "invalid"}
        try:
            _verify_approval(connection, row["run_id"], row["application_id"], row["evaluation_id"])
        except ValueError:
            return {"state": "invalid"}
        session = connection.execute(
            "SELECT status,slot_end FROM interview_sessions WHERE invitation_sha256 = ?",
            (token_hash,),
        ).fetchone()
    now = utc_now()
    opens, closes = access_window(row["starts_at"], row["ends_at"])
    if session:
        state = "available" if session["status"] == "active" and now < datetime.fromisoformat(session["slot_end"]) else "expired"
    else:
        state = "early" if now < opens else "expired" if now >= closes else "available"
    duration = datetime.fromisoformat(row["ends_at"]) - datetime.fromisoformat(row["starts_at"])
    return {"state": state, "starts_at": row["starts_at"], "ends_at": row["ends_at"],
            "opens_at": _timestamp(opens), "closes_at": _timestamp(closes),
            "duration_minutes": int(duration.total_seconds() // 60)}


def reschedule_interview(interview_id, starts_at, duration_minutes, database_path=DATABASE_PATH):
    starts_at = datetime.fromisoformat(_timestamp(starts_at))
    if starts_at <= utc_now() or type(duration_minutes) is not int or not 5 <= duration_minutes <= 60:
        raise ValueError("Choose a future start time and a duration between 5 and 60 minutes.")
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT run_id,application_id,evaluation_id,status,token_sha256 FROM interviews WHERE interview_id = ?", (interview_id,)
        ).fetchone()
        if not row or row[3] == "sending":
            raise ValueError("An invitation is being sent or this interview no longer exists.")
        approval = connection.execute(
            """SELECT x.run_id,x.evaluation_id FROM shortlist_items x
               JOIN shortlist_runs r ON r.run_id=x.run_id
               WHERE x.application_id=? AND x.approved=1 AND r.status='completed'
                 AND r.rowid=(SELECT max(rowid) FROM shortlist_runs WHERE job_id=r.job_id)""",
            (row[1],),
        ).fetchone()
        if not approval:
            raise ValueError("This application needs approval in the latest shortlist before scheduling or sending.")
        _verify_approval(connection, approval[0], row[1], approval[1])
        if connection.execute(
            "SELECT 1 FROM interview_sessions WHERE interview_id = ? AND status = 'active' AND slot_end > ?",
            (interview_id, _timestamp(utc_now())),
        ).fetchone():
            raise ValueError("An active interview cannot be rescheduled.")
        _check_slot_conflicts(connection, _timestamp(starts_at), _timestamp(ends_at), interview_id)
        if row[4]:
            connection.execute(
                "INSERT INTO rescheduled_invitations (token_sha256,interview_id) VALUES (?, ?)", (row[4], interview_id),
            )
        connection.execute(
            """UPDATE interviews SET starts_at = ?, ends_at = ?, run_id = ?, evaluation_id = ?, status = 'scheduled', token_sha256 = NULL,
               recipient_email = NULL, message_id = NULL, sent_at = NULL WHERE interview_id = ?""",
            (_timestamp(starts_at), _timestamp(ends_at), approval[0], approval[1], interview_id),
        )
