import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from database import DATABASE_PATH
from scheduling import _timestamp, _verify_approval, utc_now


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
    if require_open and not datetime.fromisoformat(row["starts_at"]) <= utc_now() < datetime.fromisoformat(row["ends_at"]):
        raise ValueError("The interview is outside its assigned time window.")
    return row


def expire_sessions(connection):
    connection.execute(
        """UPDATE interview_sessions SET status = 'expired', ended_at = CURRENT_TIMESTAMP
           WHERE status = 'active' AND slot_end <= ?""", (_timestamp(utc_now()),)
    )


def start_session(token, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        expire_sessions(connection)
        invitation = _invitation(connection, token)
        previous = connection.execute(
            "SELECT session_id FROM interview_sessions WHERE invitation_sha256 = ?", (invitation["token_sha256"],)
        ).fetchone()
        if previous:
            return previous[0]
        if connection.execute("SELECT 1 FROM interview_sessions WHERE status = 'active'").fetchone():
            raise ValueError("Another interview is in progress. Please try again shortly.")
        session_id = secrets.token_hex(16)
        connection.execute(
            """INSERT INTO interview_sessions
               (session_id,interview_id,evaluation_id,invitation_sha256,status,slot_end)
               VALUES (?, ?, ?, ?, 'active', ?)""",
            (session_id, invitation["interview_id"], invitation["evaluation_id"], invitation["token_sha256"], invitation["ends_at"]),
        )
        return session_id


def get_session(token, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        expire_sessions(connection)
        invitation = _invitation(connection, token, require_open=False)
        row = connection.execute(
            """SELECT s.*, c.full_name, a.job_title_at_application AS job_title
               FROM interview_sessions s JOIN interviews i ON i.interview_id = s.interview_id
               JOIN applications a ON a.application_id = i.application_id
               JOIN candidates c ON c.candidate_id = a.candidate_id
               WHERE s.invitation_sha256 = ?""", (invitation["token_sha256"],)
        ).fetchone()
        return dict(row) if row else None


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
        invitation = _invitation(connection, token)
        row = connection.execute(
            "SELECT * FROM interview_sessions WHERE invitation_sha256 = ?", (invitation["token_sha256"],)
        ).fetchone()
        if row is None or row["status"] != "active":
            raise ValueError("This interview is not active.")
        if row["busy_until"] and datetime.fromisoformat(row["busy_until"]) > utc_now():
            raise ValueError("Your last action is still processing. Please wait before trying again.")
        operation = secrets.token_hex(16)
        connection.execute(
            "UPDATE interview_sessions SET processing_id = ?, busy_until = ? WHERE session_id = ?",
            (operation, _timestamp(utc_now() + timedelta(minutes=8)), row["session_id"]),
        )
        return row["session_id"], operation


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
        if counts.get("main", 0) >= 6 and question["kind"] == "main" or counts.get("follow_up", 0) >= 4 and question["kind"] == "follow_up":
            raise ValueError("The interview question limit has been reached.")
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


def complete_session(session_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            """UPDATE interview_sessions SET status = CASE WHEN slot_end <= ? THEN 'expired' ELSE 'completed' END,
               ended_at = CURRENT_TIMESTAMP WHERE session_id = ? AND status = 'active'""", (_timestamp(utc_now()), session_id)
        )
