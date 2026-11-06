import re
import json
import sqlite3
from contextlib import closing
from pathlib import Path


DATABASE_PATH = Path(__file__).parent / "data" / "recruitment.db"


SESSION_COLUMNS = ("session_id", "interview_id", "evaluation_id", "invitation_sha256", "status", "slot_end",
                   "plan_json", "summary", "processing_id", "busy_until", "paused_at", "pause_reason",
                   "paused_seconds", "terminate_at", "started_at", "ended_at")


def _migrate_session_status(database_path):
    """Rebuild interview_sessions once so 'terminated' joins the status CHECK.

    Runs in its own connection because PRAGMA foreign_keys cannot toggle mid-transaction.
    Older tables may also lack the pause columns; those backfill to their defaults.
    """
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'interview_sessions'").fetchone()
        if row is None or row[0] is None or "'terminated'" in row[0]:
            return
        legacy = {column[1] for column in connection.execute("PRAGMA table_info(interview_sessions)")}
        defaults = {"paused_seconds": "0", "summary": "''"}
        select = ", ".join(name if name in legacy else defaults.get(name, "NULL") for name in SESSION_COLUMNS)
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        try:
            with connection:
                connection.execute("ALTER TABLE interview_sessions RENAME TO interview_sessions_legacy")
                connection.execute(f"""
                    CREATE TABLE interview_sessions (
                        session_id TEXT PRIMARY KEY,
                        interview_id INTEGER NOT NULL REFERENCES interviews(interview_id),
                        evaluation_id INTEGER NOT NULL REFERENCES resume_evaluations(evaluation_id),
                        invitation_sha256 TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL CHECK (status IN ('active','completed','expired','terminated')),
                        slot_end TEXT NOT NULL,
                        plan_json TEXT,
                        summary TEXT NOT NULL DEFAULT '',
                        processing_id TEXT,
                        busy_until TEXT,
                        paused_at TEXT,
                        pause_reason TEXT,
                        paused_seconds INTEGER NOT NULL DEFAULT 0,
                        terminate_at TEXT,
                        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        ended_at TEXT
                    )
                """)
                connection.execute(f"INSERT INTO interview_sessions ({', '.join(SESSION_COLUMNS)}) SELECT {select} FROM interview_sessions_legacy")
                connection.execute("DROP TABLE interview_sessions_legacy")
        finally:
            connection.execute("PRAGMA foreign_keys = ON")


def _repair_session_references(database_path):
    """Repair dependent FKs left by older session-status migrations, without deleting data."""
    with closing(sqlite3.connect(database_path)) as c:
        tables = c.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND sql LIKE '%REFERENCES%interview_sessions_legacy%'").fetchall()
        if not tables:
            return
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("PRAGMA legacy_alter_table=ON")
        with c:
            c.execute("BEGIN IMMEDIATE")
            for name, sql in tables:
                if not re.fullmatch(r'[a-z_]+',name):
                    raise sqlite3.IntegrityError('Unexpected migration table name.')
                replacement = name + '_fk_repair'
                indexes = c.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (name,)).fetchall()
                seq = c.execute('SELECT seq FROM sqlite_sequence WHERE name=?',(name,)).fetchone()
                columns = ','.join('"'+r[1]+'"' for r in c.execute(f'PRAGMA table_info("{name}")'))
                fixed = sql.replace('interview_sessions_legacy','interview_sessions')
                fixed = re.sub(r'(?i)(CREATE TABLE\s+(?:IF NOT EXISTS\s+)?)["`]?'+name+r'["`]?', lambda m:m[1]+'"'+replacement+'"', fixed, count=1)
                c.execute(fixed)
                c.execute(f'INSERT INTO "{replacement}" ({columns}) SELECT {columns} FROM "{name}"')
                c.execute(f'DROP TABLE "{name}"')
                c.execute(f'ALTER TABLE "{replacement}" RENAME TO "{name}"')
                for (definition,) in indexes:
                    c.execute(definition)
                if seq:
                    c.execute('UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name=?',(seq[0],name))
            if c.execute('PRAGMA foreign_key_check').fetchall():
                raise sqlite3.IntegrityError('Session reference repair failed validation.')


def _migrate_warning_signals(database_path):
    """Widen the CHECK atomically, preserving every warning, image and row order."""
    with closing(sqlite3.connect(database_path)) as connection:
        sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='interview_warnings'").fetchone()
        if not sql or "'gaze'" in sql[0]:
            return
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("""CREATE TABLE interview_warnings_gaze (
                session_id TEXT NOT NULL REFERENCES interview_sessions(session_id),
                warning_id TEXT NOT NULL, signal TEXT NOT NULL CHECK(signal IN ('face','fullscreen','camera','gaze')),
                recorded_at TEXT NOT NULL, client_at TEXT NOT NULL, cleared_at TEXT,
                snapshot_jpeg BLOB, snapshot_at TEXT, snapshot_note TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(session_id,warning_id))""")
            columns = 'rowid,session_id,warning_id,signal,recorded_at,client_at,cleared_at,snapshot_jpeg,snapshot_at,snapshot_note'
            connection.execute(f'INSERT INTO interview_warnings_gaze({columns}) SELECT {columns} FROM interview_warnings')
            connection.execute('DROP TABLE interview_warnings')
            connection.execute('ALTER TABLE interview_warnings_gaze RENAME TO interview_warnings')
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise sqlite3.IntegrityError('Warning migration failed foreign-key validation.')


def init_database(database_path=DATABASE_PATH):
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    _migrate_session_status(database_path)
    _repair_session_references(database_path)
    _migrate_warning_signals(database_path)

    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL CHECK (length(trim(title)) > 0),
                description TEXT NOT NULL CHECK (length(trim(description)) > 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL CHECK (length(trim(full_name)) > 0),
                email TEXT NOT NULL COLLATE NOCASE UNIQUE
            )
        """)

        # Older databases stored candidate details directly in applications.
        legacy = "full_name" in {
            column[1] for column in connection.execute("PRAGMA table_info(applications)")
        }
        if legacy:
            sequence = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'applications'"
            ).fetchone()
            connection.execute("ALTER TABLE applications RENAME TO applications_legacy")

        connection.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                application_id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id),
                job_id INTEGER NOT NULL REFERENCES jobs(job_id),
                resume_filename TEXT NOT NULL,
                resume_pdf BLOB NOT NULL CHECK (length(resume_pdf) > 0),
                resume_extracted_text TEXT NOT NULL CHECK (length(trim(resume_extracted_text)) > 0),
                resume_text TEXT NOT NULL CHECK (length(trim(resume_text)) > 0),
                job_title_at_application TEXT NOT NULL,
                jd_at_application TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (candidate_id, job_id)
            )
        """)
        if legacy:
            connection.execute("""
                INSERT OR IGNORE INTO candidates (full_name, email)
                SELECT full_name, lower(trim(email)) FROM applications_legacy
                WHERE application_id IN (
                    SELECT min(application_id) FROM applications_legacy
                    GROUP BY lower(trim(email))
                ) ORDER BY application_id
            """)
            connection.execute("""
                INSERT INTO applications
                    (application_id, candidate_id, job_id, resume_filename, resume_pdf,
                     resume_extracted_text, resume_text, job_title_at_application,
                     jd_at_application, applied_at)
                SELECT a.application_id, c.candidate_id, a.job_id, a.resume_filename,
                       a.resume_pdf, a.resume_extracted_text, a.resume_text,
                       a.job_title_at_application, a.jd_at_application, a.applied_at
                FROM applications_legacy a
                JOIN candidates c ON c.email = lower(trim(a.email))
            """)
            old_count = connection.execute("SELECT count(*) FROM applications_legacy").fetchone()[0]
            new_count = connection.execute("SELECT count(*) FROM applications").fetchone()[0]
            if old_count != new_count or connection.execute("PRAGMA foreign_key_check").fetchall():
                raise sqlite3.IntegrityError("Application migration failed its data checks.")
            connection.execute("DROP TABLE applications_legacy")
            if sequence:
                connection.execute(
                    """INSERT INTO sqlite_sequence (name, seq)
                       SELECT 'applications', ? WHERE NOT EXISTS
                       (SELECT 1 FROM sqlite_sequence WHERE name = 'applications')""",
                    (sequence[0],),
                )
                connection.execute(
                    "UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = 'applications'",
                    (sequence[0],),
                )

        connection.execute("""
            CREATE TABLE IF NOT EXISTS resume_evaluations (
                evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES applications(application_id),
                resume_sha256 TEXT NOT NULL,
                jd_sha256 TEXT NOT NULL,
                rubric_version TEXT NOT NULL,
                model_name TEXT NOT NULL,
                score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                assessment_json TEXT NOT NULL,
                evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS shortlist_runs (
                run_id TEXT PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(job_id),
                threshold REAL NOT NULL CHECK (threshold >= 0 AND threshold <= 100),
                application_ids_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN
                    ('evaluating', 'awaiting_approval', 'completed', 'superseded')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS shortlist_items (
                run_id TEXT NOT NULL REFERENCES shortlist_runs(run_id),
                application_id INTEGER NOT NULL REFERENCES applications(application_id),
                evaluation_id INTEGER NOT NULL REFERENCES resume_evaluations(evaluation_id),
                proposed INTEGER NOT NULL CHECK (proposed IN (0, 1)),
                approved INTEGER CHECK (approved IN (0, 1)),
                decided_at TEXT,
                PRIMARY KEY (run_id, application_id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS interviews (
                interview_id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL UNIQUE REFERENCES applications(application_id),
                run_id TEXT NOT NULL,
                evaluation_id INTEGER NOT NULL REFERENCES resume_evaluations(evaluation_id),
                starts_at TEXT NOT NULL,
                ends_at TEXT NOT NULL CHECK (ends_at > starts_at),
                status TEXT NOT NULL DEFAULT 'scheduled'
                    CHECK (status IN ('scheduled', 'sending', 'sent', 'delivery_unknown')),
                token_sha256 TEXT UNIQUE,
                recipient_email TEXT,
                message_id TEXT,
                sent_at TEXT,
                terminate_on_interruption INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id, application_id)
                    REFERENCES shortlist_items(run_id, application_id)
            )
        """)
        interview_columns = {column[1] for column in connection.execute("PRAGMA table_info(interviews)")}
        if "terminate_on_interruption" not in interview_columns:
            connection.execute("ALTER TABLE interviews ADD COLUMN terminate_on_interruption INTEGER NOT NULL DEFAULT 0")
        if "warning_limit" not in interview_columns:
            connection.execute("ALTER TABLE interviews ADD COLUMN warning_limit INTEGER NOT NULL DEFAULT 3 CHECK(warning_limit BETWEEN 0 AND 20)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS rescheduled_invitations (
                token_sha256 TEXT PRIMARY KEY,
                interview_id INTEGER NOT NULL REFERENCES interviews(interview_id),
                rescheduled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS interview_preflight (
                receipt_sha256 TEXT PRIMARY KEY,
                invitation_sha256 TEXT NOT NULL,
                interview_id INTEGER NOT NULL REFERENCES interviews(interview_id),
                policy_version TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS interview_sessions (
                session_id TEXT PRIMARY KEY,
                interview_id INTEGER NOT NULL REFERENCES interviews(interview_id),
                evaluation_id INTEGER NOT NULL REFERENCES resume_evaluations(evaluation_id),
                invitation_sha256 TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK (status IN ('active','completed','expired','terminated')),
                slot_end TEXT NOT NULL,
                plan_json TEXT,
                summary TEXT NOT NULL DEFAULT '',
                processing_id TEXT,
                busy_until TEXT,
                paused_at TEXT,
                pause_reason TEXT,
                paused_seconds INTEGER NOT NULL DEFAULT 0,
                terminate_at TEXT,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_at TEXT
            )
        """)
        # Older databases predate interview pausing; add the columns in place.
        session_columns = {column[1] for column in connection.execute("PRAGMA table_info(interview_sessions)")}
        if "paused_at" not in session_columns:
            connection.execute("ALTER TABLE interview_sessions ADD COLUMN paused_at TEXT")
        if "pause_reason" not in session_columns:
            connection.execute("ALTER TABLE interview_sessions ADD COLUMN pause_reason TEXT")
        if "paused_seconds" not in session_columns:
            connection.execute("ALTER TABLE interview_sessions ADD COLUMN paused_seconds INTEGER NOT NULL DEFAULT 0")
        if "terminate_at" not in session_columns:
            connection.execute("ALTER TABLE interview_sessions ADD COLUMN terminate_at TEXT")
        connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS single_active_interview
                              ON interview_sessions(status) WHERE status = 'active'""")
        turns_sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='interview_turns'").fetchone()
        if turns_sql and 'main_number BETWEEN 1 AND 6' in turns_sql[0]:
            indexes = connection.execute("SELECT sql FROM sqlite_master WHERE tbl_name='interview_turns' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
            seq = connection.execute("SELECT seq FROM sqlite_sequence WHERE name='interview_turns'").fetchone()
            expanded = turns_sql[0].replace('interview_turns','interview_turns_budget',1).replace('main_number BETWEEN 1 AND 6','main_number BETWEEN 1 AND 20')
            connection.execute(expanded)
            connection.execute('INSERT INTO interview_turns_budget SELECT * FROM interview_turns')
            connection.execute('DROP TABLE interview_turns')
            connection.execute('ALTER TABLE interview_turns_budget RENAME TO interview_turns')
            for (definition,) in indexes:
                connection.execute(definition)
            if seq:
                connection.execute("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='interview_turns'",(seq[0],))
        connection.execute("""
            CREATE TABLE IF NOT EXISTS interview_turns (
                turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES interview_sessions(session_id),
                sequence INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('main','follow_up')),
                main_number INTEGER NOT NULL CHECK (main_number BETWEEN 1 AND 20),
                question TEXT NOT NULL,
                source_ids_json TEXT NOT NULL DEFAULT '[]',
                audio_wav BLOB,
                audio_sha256 TEXT,
                received_at TEXT,
                answer_text TEXT,
                answered_at TEXT,
                decision_json TEXT,
                UNIQUE(session_id, sequence)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS interview_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES interview_sessions(session_id),
                recorded_at TEXT NOT NULL,
                client_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS interview_events_by_session ON interview_events(session_id)")
        if 'client_event_id' not in {row[1] for row in connection.execute('PRAGMA table_info(interview_events)')}:
            connection.execute('ALTER TABLE interview_events ADD COLUMN client_event_id TEXT')
        connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS interview_events_idempotent ON interview_events(session_id,client_event_id) WHERE client_event_id IS NOT NULL')
        connection.execute("""CREATE TABLE IF NOT EXISTS interview_warnings (
            session_id TEXT NOT NULL REFERENCES interview_sessions(session_id),
            warning_id TEXT NOT NULL, signal TEXT NOT NULL CHECK(signal IN ('face','fullscreen','camera','gaze')),
            recorded_at TEXT NOT NULL, client_at TEXT NOT NULL, cleared_at TEXT,
            snapshot_jpeg BLOB, snapshot_at TEXT, snapshot_note TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(session_id,warning_id))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS model_request_clock (
            clock_id INTEGER PRIMARY KEY CHECK (clock_id = 1), last_started_at REAL NOT NULL
        )""")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS groq_token_usage (
                usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                response_id TEXT UNIQUE,
                request_id TEXT,
                session_id TEXT REFERENCES interview_sessions(session_id) ON DELETE SET NULL,
                model_name TEXT NOT NULL,
                call_type TEXT NOT NULL,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                cached_tokens INTEGER,
                usage_reported INTEGER NOT NULL CHECK (usage_reported IN (0,1)),
                finish_reason TEXT,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS groq_usage_by_session ON groq_token_usage(session_id)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS interview_reports (
                report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES interview_sessions(session_id),
                source_sha256 TEXT NOT NULL,
                rubric_version TEXT NOT NULL,
                model_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'generating' CHECK (status IN ('generating','ready')),
                source_json TEXT NOT NULL,
                parts_json TEXT NOT NULL DEFAULT '{}',
                assessment_json TEXT,
                overall_score REAL,
                generation_owner TEXT,
                lease_until TEXT,
                generated_at TEXT,
                UNIQUE(session_id,source_sha256,rubric_version)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS report_decisions (
                decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL REFERENCES interview_reports(report_id),
                outcome TEXT NOT NULL CHECK (outcome IN
                    ('Advance to next round','Hold for review','Do not advance')),
                notes TEXT NOT NULL DEFAULT '',
                decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        existing = connection.execute("SELECT sql FROM sqlite_master WHERE name='interview_templates'").fetchone()
        if existing and 'main_questions+max_followups<=10' in existing[0]:
            expanded = existing[0].replace('interview_templates','interview_templates_budget',1).replace('BETWEEN 1 AND 10','BETWEEN 1 AND 20').replace('BETWEEN 0 AND 8','BETWEEN 0 AND 20').replace('main_questions+max_followups<=10','main_questions+max_followups<=20')
            connection.execute(expanded)
            connection.execute('INSERT INTO interview_templates_budget SELECT * FROM interview_templates')
            connection.execute('DROP TABLE interview_templates')
            connection.execute('ALTER TABLE interview_templates_budget RENAME TO interview_templates')
        connection.execute("""CREATE TABLE IF NOT EXISTS interview_templates (
            template_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
            name TEXT NOT NULL, main_questions INTEGER NOT NULL CHECK(main_questions BETWEEN 1 AND 20),
            max_followups INTEGER NOT NULL CHECK(max_followups BETWEEN 0 AND 20),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(template_id,version), CHECK(main_questions+max_followups<=20),
            CHECK(max_followups<=2*main_questions))""")
        if 'config_json' not in {row[1] for row in connection.execute('PRAGMA table_info(interview_templates)')}:
            connection.execute('ALTER TABLE interview_templates ADD COLUMN config_json TEXT')
        for table in ('interviews', 'interview_sessions'):
            if 'template_json' not in {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}:
                connection.execute(f'ALTER TABLE {table} ADD COLUMN template_json TEXT')


def save_job(title, description, database_path=DATABASE_PATH): # used by 0_Create_Job.py to create a job
    title = title.strip()
    description = description.strip()
    if not title or not description:
        raise ValueError("Enter both a job title and a job description.")

    with closing(sqlite3.connect(database_path)) as connection:
        cursor = connection.execute(
            "INSERT INTO jobs (title, description) VALUES (?, ?)",
            (title, description),
        )
        connection.commit()
        return cursor.lastrowid


def get_jobs(database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            "SELECT job_id, title, description, created_at FROM jobs ORDER BY job_id DESC"
        )]


# def update_job(job_id, title, description, database_path=DATABASE_PATH): # Used to update JD, deprecated because of complexities
#     title = title.strip()
#     description = description.strip()
#     if not title or not description:
#         raise ValueError("Enter both a job title and a job description.")

#     with closing(sqlite3.connect(database_path)) as connection:
#         cursor = connection.execute(
#             """UPDATE jobs SET title = ?, description = ?
#                WHERE job_id = ? AND (title != ? OR description != ?)""",
#             (title, description, job_id, title, description),
#         )
#         changed = cursor.rowcount > 0
#         if not changed and connection.execute(
#             "SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)
#         ).fetchone() is None:
#             raise ValueError("This job no longer exists. Refresh the page.")
#         connection.commit()
#         return changed


def save_application(job_id, full_name, email, resume_filename, resume_pdf,
                     resume_extracted_text, resume_text, database_path=DATABASE_PATH): # Used by 2_Apply.py to save applications of candidates
    full_name = full_name.strip()
    email = email.strip().lower()
    if not full_name:
        raise ValueError("Enter your full name.")
    if not re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", email):
        raise ValueError("Enter a valid email address, such as name@example.com.")
    if not resume_pdf or len(resume_pdf) > 5 * 1024 * 1024 or not resume_pdf.lstrip().startswith(b"%PDF-"):
        raise ValueError("Upload a valid resume PDF, no larger than 5 MB.")
    resume_filename = resume_filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not resume_filename.lower().endswith(".pdf"):
        raise ValueError("Upload your resume as a PDF.")
    if not resume_extracted_text.strip() or not resume_text.strip():
        raise ValueError("Your resume must contain readable text. Review the extracted text before applying.")

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        job = connection.execute(
            "SELECT title, description FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if job is None:
            raise ValueError("This job is no longer available. Select another job.")
        candidate = connection.execute(
            "SELECT candidate_id, full_name FROM candidates WHERE email = ?", (email,)
        ).fetchone()
        if candidate and " ".join(candidate[1].split()).casefold() != " ".join(full_name.split()).casefold():
            raise ValueError(
                "This email address is already associated with a different name. "
                "Use the name from your original application or correct your email address."
            )
        existing = connection.execute(
            """SELECT a.application_id FROM applications a
               JOIN candidates c ON a.candidate_id = c.candidate_id
               WHERE a.job_id = ? AND c.email = ?""",
            (job_id, email),
        ).fetchone()
        if existing:
            raise ValueError(f"You have already applied for this job. Your application ID is #{existing[0]}.")
        if candidate:
            candidate_id = candidate[0]
        else:
            candidate_id = connection.execute(
                "INSERT INTO candidates (full_name, email) VALUES (?, ?)", (full_name, email)
            ).lastrowid
        cursor = connection.execute(
            """INSERT INTO applications
               (job_id, candidate_id, resume_filename, resume_pdf,
                resume_extracted_text, resume_text, job_title_at_application, jd_at_application)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, candidate_id, resume_filename, resume_pdf,
             resume_extracted_text.strip(), resume_text.strip(), job[0], job[1]),
        )
        connection.commit()
        return cursor.lastrowid


def get_applications(job_id=None, database_path=DATABASE_PATH):
    query = """SELECT a.application_id, a.candidate_id, a.job_id, c.full_name, c.email,
                      a.resume_filename, a.resume_text, a.job_title_at_application,
                      a.jd_at_application, a.applied_at, j.title AS current_job_title
               FROM applications a
               JOIN candidates c ON a.candidate_id = c.candidate_id
               JOIN jobs j ON a.job_id = j.job_id"""
    parameters = ()
    if job_id is not None:
        query += " WHERE a.job_id = ?"
        parameters = (job_id,)
    query += " ORDER BY a.application_id DESC"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, parameters)]


def get_resume_pdf(application_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(
            "SELECT resume_pdf FROM applications WHERE application_id = ?", (application_id,)
        ).fetchone()
    if row is None:
        raise ValueError("This application no longer exists.")
    return row[0]


def get_evaluation_input(application_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """SELECT application_id, resume_text, job_title_at_application, jd_at_application
               FROM applications WHERE application_id = ?""", (application_id,)
        ).fetchone()
    if row is None:
        raise ValueError("This application no longer exists.")
    return dict(row)


def save_resume_evaluation(application_id, resume_text, job_title, jd_text,
                           assessment, model_name, database_path=DATABASE_PATH):
    from screening import RUBRIC_VERSION, document_hashes, validate_assessment

    score = validate_assessment(assessment, resume_text, jd_text)
    if not model_name.strip():
        raise ValueError("The evaluation must record which model was used.")
    resume_hash, jd_hash = document_hashes(resume_text, job_title, jd_text)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        source = connection.execute(
            """SELECT resume_text, job_title_at_application, jd_at_application
               FROM applications WHERE application_id = ?""", (application_id,)
        ).fetchone()
        if source != (resume_text, job_title, jd_text):
            raise ValueError("The application documents changed. Evaluate the current documents before saving.")
        cursor = connection.execute(
            """INSERT INTO resume_evaluations
               (application_id, resume_sha256, jd_sha256, rubric_version,
                model_name, score, assessment_json) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (application_id, resume_hash, jd_hash, RUBRIC_VERSION, model_name.strip(),
             score, json.dumps(assessment, ensure_ascii=False, allow_nan=False)),
        )
        return cursor.lastrowid


def get_latest_resume_evaluation(application_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """SELECT * FROM resume_evaluations WHERE application_id = ?
               ORDER BY evaluation_id DESC LIMIT 1""", (application_id,)
        ).fetchone()
    if row is None:
        return None
    evaluation = dict(row)
    evaluation["assessment"] = json.loads(evaluation.pop("assessment_json"))
    return evaluation
