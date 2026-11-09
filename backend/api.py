"""HTTP API for the interviewer and candidate setup checks."""

import os
import re
import sqlite3
from datetime import datetime
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Literal

from interview_templates import list_templates, save_template, read_config, public_config
from preflight import issue_receipt
from admissions_preset import ensure_admissions_template
from warning_evidence import gaze_monitoring_summary, list_warnings, warning_snapshot

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from public_origin import active_public_origin
from automatic_scheduling import (
    AutomaticScheduleRequest, AutomaticScheduleCommit,
    preview_automatic_schedule, commit_automatic_schedule,
)
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pydantic import BaseModel, ConfigDict, Field

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass


def _warn_unless_launcher_env():
    """Detect a launch that bypassed run.py, where GPU transcription fails.

    faster-whisper needs the nvidia-* pip libraries on the loader path. That
    is exactly what backend/run.py sets up, so its absence means the server
    was started wrong (e.g. plain `uvicorn api:app`) and speech recognition
    will fail with 'libcublas.so.12 is not found'.
    """
    import logging
    from importlib.util import find_spec

    try:
        spec = find_spec("nvidia.cublas")
        lib_dir = str(Path(next(iter(spec.submodule_search_locations))) / "lib") if spec else None
    except Exception:
        lib_dir = None
    if lib_dir and lib_dir not in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
        logging.getLogger("interviewer").warning(
            "NVIDIA libraries are not on LD_LIBRARY_PATH. This server was not "
            "started via backend/run.py, so GPU speech transcription WILL fail "
            "with 'libcublas.so.12 is not found'. Stop it and start it with: "
            "python backend/run.py (from the project root)"
        )

from database import (
    get_applications, get_evaluation_input, get_jobs,
    get_latest_resume_evaluation, get_resume_pdf, init_database,
    save_application, save_job, save_resume_evaluation,
)
from groq_client import generate
from interview_agent import PLAN_SCHEMA, continue_interview
from interview_reports import (
    REPORT_VERSION, answer_recording, finished_interviews,
    get_report, latest_decision, report_source, save_report_decision,
    shortlisted_candidates, source_hash,
)
from interview_store import (
    COMPLETION_MESSAGE, get_session, get_turns, pause_session, resume_session, save_events, start_session,
    terminate_session,
)
from invitations import send_invitation
from jobs import report_job_status, start_report_job
from report_pdf import export_report_pdf
from resume import extract_resume
from scheduling import (
    access_window, check_scheduled_approval, get_approved_candidates,
    get_interviews, get_invitation_access, reschedule_interview,
    schedule_interview, utc_now,
)
from screening import RUBRIC_VERSION, WEIGHTS, assess_resume, document_hashes
from shortlisting import advance_workflow, get_run, get_run_items, start_run


def _env(name, default=""):
    return os.environ.get(name, default)


def _groq():
    return _env("GROQ_API_KEY", ""), _env("GROQ_MODEL", "openai/gpt-oss-120b")


def _smtp_settings():
    return {
        "SMTP_USERNAME": _env("SMTP_USERNAME", ""),
        "SMTP_PASSWORD": _env("SMTP_PASSWORD", ""),
        "APP_BASE_URL": active_public_origin(require_ready=True) or _env("APP_BASE_URL", "http://localhost:5173"),
    }


def _call(func, *args, generic="The request could not be completed. Please try again.", **kwargs):
    try:
        return func(*args, **kwargs)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="The backend is not configured.") from None
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail=generic) from None


app = FastAPI(title="AI Interviewer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_env("FRONTEND_ORIGIN", "http://localhost:5173")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    _warn_unless_launcher_env()
    init_database()
    ensure_admissions_template()


@app.get("/api/health")
def health():
    return {"status": "ok"}


class JobCreate(BaseModel):
    title: str = ""
    description: str = ""


@app.get("/api/jobs")
def list_jobs():
    return _call(get_jobs, generic="Jobs could not be loaded. Please refresh and try again.")


@app.post("/api/jobs")
def create_job(body: JobCreate):
    job_id = _call(save_job, body.title, body.description, generic="The job could not be saved. Please try again.")
    return {"job_id": job_id}


@app.post("/api/jobs/extract-description")
async def extract_description(file: UploadFile = File(...)):
    content = await file.read()
    try:
        if len(content) > 5 * 1024 * 1024:
            raise ValueError("Upload a file smaller than 5 MB.")
        if (file.filename or "").lower().endswith(".pdf"):
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise ValueError("Upload an unencrypted PDF.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            text = content.decode("utf-8-sig")
        if not text.strip():
            raise ValueError("No text was found. Paste the description instead.")
        return {"text": text.strip()}
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Use a UTF-8 text file or paste the description.") from None
    except PdfReadError:
        raise HTTPException(status_code=400, detail="Could not read this PDF. Try another file or paste the description.") from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


def _application(application_id):
    for item in _call(get_applications, generic="Applications could not be loaded. Please refresh and try again."):
        if item["application_id"] == application_id:
            return item
    raise HTTPException(status_code=404, detail="This application no longer exists.")


def _current_evaluation(application):
    evaluation = _call(
        get_latest_resume_evaluation, application["application_id"],
        generic="The evaluation could not be loaded or saved. Please try again.",
    )
    if not evaluation:
        return None
    resume_hash, jd_hash = document_hashes(
        application["resume_text"], application["job_title_at_application"], application["jd_at_application"],
    )
    if (evaluation["resume_sha256"] != resume_hash or evaluation["jd_sha256"] != jd_hash
            or evaluation["rubric_version"] != RUBRIC_VERSION):
        return None
    return evaluation


@app.get("/api/applications")
def list_applications(job_id: int | None = Query(default=None)):
    return _call(get_applications, job_id, generic="Applications could not be loaded. Please refresh and try again.")


@app.post("/api/applications")
async def submit_application(
    job_id: int = Form(...),
    full_name: str = Form(default=""),
    email: str = Form(default=""),
    resume: UploadFile = File(...),
):
    content = await resume.read()
    try:
        text = extract_resume(content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    application_id = _call(
        save_application, job_id, full_name, email, resume.filename or "resume.pdf",
        content, text, text, generic="Your application could not be saved. Please try again.",
    )
    jobs = {job["job_id"]: job for job in _call(get_jobs)}
    title = jobs[job_id]["title"] if job_id in jobs else "this role"
    return {
        "application_id": application_id,
        "receipt": f"Application submitted successfully for {title}. Your application ID is #{application_id}.",
    }


@app.get("/api/applications/{application_id}")
def application_detail(application_id: int):
    application = _application(application_id)
    return {"application": application, "evaluation": _current_evaluation(application)}


@app.post("/api/applications/{application_id}/evaluate")
def evaluate_application(application_id: int):
    application = _application(application_id)
    api_key, model_name = _groq()
    source = _call(get_evaluation_input, application_id, generic="The evaluation could not be loaded or saved. Please try again.")
    result = _call(assess_resume, source["resume_text"], source["job_title_at_application"],
                   source["jd_at_application"], partial(generate, api_key, model_name))
    _call(save_resume_evaluation, application_id, source["resume_text"], source["job_title_at_application"],
          source["jd_at_application"], result["assessment"], model_name,
          generic="The evaluation could not be loaded or saved. Please try again.")
    return {"application": application, "evaluation": _current_evaluation(application)}


@app.get("/api/applications/{application_id}/resume")
def download_resume(application_id: int):
    application = _application(application_id)
    content = _call(get_resume_pdf, application_id)
    filename = application["resume_filename"].replace('"', "")
    return Response(
        content, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/screening/weights")
def screening_weights():
    return {"weights": WEIGHTS}


def _shortlist_model():
    api_key, model_name = _groq()
    return partial(generate, api_key, model_name), model_name


class ShortlistCreate(BaseModel):
    job_id: int
    threshold: float = 70.0


class Decision(BaseModel):
    application_id: int
    approved: bool


class DecisionsBody(BaseModel):
    decisions: list[Decision]


@app.post("/api/shortlists")
def generate_shortlist(body: ShortlistCreate):
    run_id = _call(start_run, body.job_id, body.threshold,
                   generic="The shortlist could not be loaded or saved. Please try again.")
    generate_model, model_name = _shortlist_model()
    _call(advance_workflow, run_id, generate_model, model_name,
          generic="The shortlist could not be loaded or saved. Please try again.")
    return {"run_id": run_id}


@app.get("/api/shortlists")
def latest_shortlist(job_id: int = Query(...)):
    run = _call(get_run, job_id=job_id, generic="The shortlist could not be loaded or saved. Please try again.")
    if not run:
        return {"run": None, "items": []}
    items = _call(get_run_items, run["run_id"], generic="The shortlist could not be loaded or saved. Please try again.")
    return {"run": run, "items": items}


@app.get("/api/shortlists/{run_id}")
def shortlist_detail(run_id: str):
    run = _call(get_run, run_id, generic="The shortlist could not be loaded or saved. Please try again.")
    if not run:
        raise HTTPException(status_code=404, detail="This shortlist no longer exists.")
    items = _call(get_run_items, run_id, generic="The shortlist could not be loaded or saved. Please try again.")
    return {"run": run, "items": items}


@app.post("/api/shortlists/{run_id}/continue")
def continue_shortlist(run_id: str):
    generate_model, model_name = _shortlist_model()
    _call(advance_workflow, run_id, generate_model, model_name,
          generic="The shortlist could not be loaded or saved. Please try again.")
    return {"run_id": run_id}


@app.post("/api/shortlists/{run_id}/decisions")
def save_shortlist_decisions(run_id: str, body: DecisionsBody):
    generate_model, model_name = _shortlist_model()
    decisions = [item.model_dump() for item in body.decisions]
    _call(advance_workflow, run_id, generate_model, model_name, decisions=decisions,
          generic="The shortlist could not be loaded or saved. Please try again.")
    return {"run_id": run_id}


class TemplateQuestionBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    kind: Literal['must_ask', 'sample']
    question: str = Field(min_length=1, max_length=500)
    topic: str = Field(default='', max_length=80)
    expected_points: str = Field(default='', max_length=1200)
    example_answer: str = Field(default='', max_length=2000)
    followup_guidance: str = Field(default='', max_length=500)


class InterviewTemplateBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=80)
    main_questions: int = Field(default=6, strict=True, ge=1, le=20)
    max_followups: int = Field(default=4, strict=True, ge=0, le=20)
    expected_version: int | None = Field(default=None, strict=True, ge=1)
    mode: Literal['ai', 'custom', 'hybrid'] = 'ai'
    topics: list[str] = Field(default_factory=list, max_length=20)
    questions: list[TemplateQuestionBody] = Field(default_factory=list, max_length=20)

    def content(self):
        return dict(mode=self.mode, topics=self.topics, questions=[item.model_dump() for item in self.questions])


@app.get("/api/interview-templates")
def interview_templates():
    return _call(list_templates)


@app.get("/api/interview-templates/{template_id}/versions")
def template_versions(template_id: str):
    return _call(list_templates, template_id=template_id)


@app.post("/api/interview-templates")
def create_interview_template(body: InterviewTemplateBody):
    return _call(save_template, body.name, body.main_questions, body.max_followups, **body.content())


@app.post("/api/interview-templates/{template_id}/versions")
def revise_interview_template(template_id: str, body: InterviewTemplateBody):
    return _call(save_template, body.name, body.main_questions, body.max_followups,
                 template_id=template_id, expected_version=body.expected_version, **body.content())


class InterviewSchedule(BaseModel):
    run_id: str
    application_id: int
    starts_at: str = Field(description="ISO timestamp with an explicit timezone.")
    duration_minutes: int = 30
    terminate_on_interruption: bool = False
    warning_limit: int = Field(default=3, strict=True, ge=0, le=20)
    template_id: str | None = None
    template_version: int | None = Field(default=None, strict=True, ge=1)


class InterviewReschedule(BaseModel):
    starts_at: str = Field(description="ISO timestamp with an explicit timezone.")
    duration_minutes: int = 30


def _slot_timestamp(value):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Choose an interview time with an explicit timezone.") from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise HTTPException(status_code=400, detail="Choose an interview time with an explicit timezone.") from None
    return moment


@app.get("/api/scheduling/approved")
def approved_candidates(run_id: str = Query(...)):
    return _call(get_approved_candidates, run_id, generic="The schedule could not be loaded or saved. Please try again.")


@app.post("/api/scheduling/automatic/preview")
def automatic_schedule_preview(body: AutomaticScheduleRequest):
    return _call(preview_automatic_schedule, body.model_dump(), generic="The automatic schedule could not be prepared.")


@app.post("/api/scheduling/automatic/commit")
def automatic_schedule_commit(body: AutomaticScheduleCommit):
    return _call(commit_automatic_schedule, body.model_dump(), generic="The automatic schedule could not be saved.")


@app.get("/api/interviews")
def list_interviews(job_id: int | None = Query(default=None)):
    return _call(get_interviews, job_id, generic="The schedule could not be loaded or saved. Please try again.")


@app.post("/api/interviews")
def schedule_candidate(body: InterviewSchedule):
    interview_id = _call(schedule_interview, body.run_id, body.application_id,
                         _slot_timestamp(body.starts_at), body.duration_minutes,
                         terminate_on_interruption=body.terminate_on_interruption, template_id=body.template_id, template_version=body.template_version,
                         warning_limit=body.warning_limit,
                         generic="The schedule could not be loaded or saved. Please try again.")
    return {"interview_id": interview_id}


@app.get("/api/interviews/{interview_id}/approval")
def interview_approval(interview_id: int):
    try:
        check_scheduled_approval(interview_id)
    except ValueError as error:
        return {"ok": False, "reason": str(error)}
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="The schedule could not be loaded or saved. Please try again.") from None
    return {"ok": True, "reason": None}


@app.post("/api/interviews/{interview_id}/reschedule")
def reschedule_candidate(interview_id: int, body: InterviewReschedule):
    _call(reschedule_interview, interview_id, _slot_timestamp(body.starts_at), body.duration_minutes,
          generic="The schedule could not be loaded or saved. Please try again.")
    return {"interview_id": interview_id}


@app.post("/api/interviews/{interview_id}/send-invitation")
def send_candidate_invitation(interview_id: int):
    _call(send_invitation, interview_id, _smtp_settings(),
          generic="The schedule could not be loaded or saved. Please try again.")
    return {"interview_id": interview_id}


@app.get("/api/scheduling/access-window")
def scheduling_window(starts_at: str = Query(...), ends_at: str = Query(...)):
    try:
        opens, closes = access_window(starts_at, ends_at)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="This interview slot is invalid.") from None
    return {"opens_at": opens.isoformat(), "closes_at": closes.isoformat(), "expired": closes <= utc_now()}


def _interview_model(token):
    api_key, model_name = _groq()

    def ask(system, documents, schema):
        session = get_session(token)
        return generate(
            api_key, model_name, system, documents, schema,
            max_completion_tokens=max(1536, read_config(session.get("template_json"))["main_questions"] * 180) if schema == PLAN_SCHEMA else 1024,
            schema_name="interview_question", interview_id=session["session_id"],
        )

    return ask


def _interview_state(token):
    access = _call(get_invitation_access, token, generic="Your interview could not be loaded. Please refresh and try again.")
    session = None if access["state"] in ("invalid", "rescheduled") else _call(get_session, token, generic="Your interview could not be loaded. Please refresh and try again.")
    turns = []
    if session:
        turns = _call(get_turns, session["session_id"], generic="Your interview could not be loaded. Please refresh and try again.")
    config = read_config(session.get("template_json")) if session else read_config()
    proctoring = session.get('proctoring') if session else None
    if session is None and access["state"] not in ("invalid", "rescheduled"):
        from contextlib import closing
        from database import DATABASE_PATH
        from interview_store import _invitation
        with closing(sqlite3.connect(DATABASE_PATH)) as c:
            c.row_factory = sqlite3.Row
            invitation = _invitation(c, token, require_open=False)
            config = read_config(invitation["template_json"])
            proctoring = dict(terminate_on_interruption=bool(invitation['terminate_on_interruption']),warning_limit=invitation['warning_limit'],warning_count=0,active_warnings={},terminated=False)
    # Internal sessions contain the frozen answer guide, plan and model summary.
    # Candidate responses deliberately expose only the fields used by the room.
    session_keys = ('session_id', 'status', 'slot_end', 'paused_at', 'pause_reason', 'terminate_at', 'job_title', 'full_name')
    turn_keys = ('turn_id', 'sequence', 'kind', 'main_number', 'question', 'answer_text', 'answered_at')
    return {"access": access, "session": {key: session.get(key) for key in session_keys} if session else None,
            "turns": [{key: turn.get(key) for key in turn_keys} for turn in turns],
            "completion_message": COMPLETION_MESSAGE, "config": public_config(config), 'proctoring': proctoring}


@app.get("/api/invitations/access")
def invitation_access(token: str = Query(...)):
    return _call(get_invitation_access, token, generic="Your interview could not be loaded. Please refresh and try again.")


@app.post("/api/interview/preflight")
async def verify_preflight(token: str = Form(...), checks: str = Form(...), camera: UploadFile = File(...), audio: UploadFile = File(...)):
    import json
    try:
        data = json.loads(checks)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid setup check.") from None
    if not isinstance(data, dict) or len(checks) > 2000:
        raise HTTPException(status_code=400, detail="Invalid setup check.")
    frame = await camera.read(250_001)
    recording = await audio.read(400_001)
    return _call(issue_receipt, token, data, frame, recording)


@app.post("/api/interview/sessions")
def open_interview_session(token: str = Form(...), preflight_receipt: str = Form(...)):
    session_id = _call(start_session, token, preflight_receipt=preflight_receipt, generic="Your interview could not be loaded. Please refresh and try again.")
    return {"session_id": session_id}


@app.get("/api/interview/state")
def interview_state(token: str = Query(...)):
    return _interview_state(token)


@app.post("/api/interview/events")
def report_interview_events(token: str = Form(...), events: str = Form(...)):
    import json
    if len(events) > 1_000_000:
        raise HTTPException(status_code=400, detail="Invalid monitoring events.")
    try:
        items = json.loads(events)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid monitoring events.") from None
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Invalid monitoring events.")
    saved = _call(save_events, token, items, generic="The monitoring events could not be recorded. Please try again.")
    result = {'saved': saved}
    if any(item.get('kind', '').startswith('warn.') and item.get('detail', {}).get('warning_id') for item in items):
        session = _call(get_session, token)
        result['proctoring'] = session.get('proctoring') if session else None
    return result


@app.post("/api/interview/pause")
def pause_interview(token: str = Form(...), reason: str = Form(...)):
    return _call(pause_session, token, reason, generic="The interview could not be paused. Please try again.")


@app.post("/api/interview/resume")
def resume_interview(token: str = Form(...)):
    return _call(resume_session, token, generic="The interview could not be resumed. Please try again.")


@app.post("/api/interview/terminate")
def terminate_interview(token: str = Form(...)):
    return _call(terminate_session, token, generic="The interview could not be ended. Please try again.")


@app.post("/api/interview/advance")
async def advance_interview(
    token: str = Form(...),
    turn_id: int | None = Form(default=None),
    audio: UploadFile | None = File(default=None),
):
    recording = await audio.read() if audio is not None else None
    try:
        await run_in_threadpool(continue_interview, token, _interview_model(token), recording=recording, turn_id=turn_id)
    except (ValueError, RuntimeError, FileNotFoundError, sqlite3.Error) as error:
        if isinstance(error, ValueError):
            raise HTTPException(status_code=400, detail=str(error)) from None
        if isinstance(error, RuntimeError):
            raise HTTPException(status_code=502, detail=str(error)) from None
        raise HTTPException(status_code=500, detail="Your interview could not be loaded. Please refresh and try again.") from None
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="The interviewer could not continue. Your saved progress is safe; please retry.",
        ) from None
    return _interview_state(token)


@app.get("/api/reports/finished")
def finished_reports():
    return _call(finished_interviews, generic="The report could not be loaded or saved. Please try again.")


@app.get("/api/reports/state")
def report_state(session_id: str = Query(...)):
    source = _call(report_source, session_id, generic="The report could not be loaded or saved. Please try again.")
    report = _call(get_report, session_id=session_id, generic="The report could not be loaded or saved. Please try again.")
    current = bool(report and report["status"] == "ready" and report["rubric_version"] == REPORT_VERSION
                   and report["source_sha256"] == source_hash(source))
    job = _call(report_job_status, session_id, generic="The report could not be loaded or saved. Please try again.")
    lease_active = bool(report and report["generation_owner"] and report["lease_until"]
                        and datetime.fromisoformat(report["lease_until"]) > utc_now())
    generating = job["state"] in ("queued", "running") or lease_active
    return {"source": source, "report": report, "current": current, "job": job, "generating": generating,
            'warnings': _call(list_warnings, session_id), 'gaze_monitoring': _call(gaze_monitoring_summary, session_id)}


@app.get('/api/reports/warning-snapshot')
def report_warning_snapshot(session_id: str = Query(...), warning_id: str = Query(...)):
    _call(report_source, session_id)  # Evidence is available for finished interviews only.
    content = _call(warning_snapshot, session_id, warning_id)
    return Response(content, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@app.post("/api/reports/generate")
def generate_candidate_report(session_id: str = Form(...)):
    api_key, model_name = _groq()
    _call(start_report_job, session_id, api_key, model_name,
          generic="The report could not be loaded or saved. Please try again.")
    return {"session_id": session_id}


@app.get("/api/reports/{report_id}")
def report_detail(report_id: int):
    report = _call(get_report, report_id, generic="The report could not be loaded or saved. Please try again.")
    if not report:
        raise HTTPException(status_code=404, detail="This interview report is unavailable.")
    decision = _call(latest_decision, report_id, generic="The report could not be loaded or saved. Please try again.")
    return {"report": report, "decision": decision}


@app.get("/api/reports/{report_id}/pdf")
def report_pdf(report_id: int):
    report = _call(get_report, report_id, generic="The report could not be loaded or saved. Please try again.")
    if not report or report["status"] != "ready":
        raise HTTPException(status_code=400, detail="This report is not ready to download.")
    content = _call(export_report_pdf, report_id, generic="The report could not be loaded or saved. Please try again.")
    filename = re.sub(r"[^a-zA-Z0-9_-]", "_", report["source"]["full_name"])
    filename += f"_application_{report['source']['application_id']}_report.pdf"
    return Response(
        content, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ReportDecisionBody(BaseModel):
    outcome: str = ""
    notes: str = ""


@app.post("/api/reports/{report_id}/decision")
def save_candidate_decision(report_id: int, body: ReportDecisionBody):
    _call(save_report_decision, report_id, body.outcome, body.notes,
          generic="The report could not be loaded or saved. Please try again.")
    decision = _call(latest_decision, report_id, generic="The report could not be loaded or saved. Please try again.")
    return {"decision": decision}


@app.get("/api/reports/audio")
def report_audio(session_id: str = Query(...), turn_id: int = Query(...)):
    recording = _call(answer_recording, session_id, turn_id,
                      generic="The report could not be loaded or saved. Please try again.")
    if not recording:
        raise HTTPException(status_code=404, detail="No recording is available for this answer.")
    return Response(recording, media_type="audio/wav")


@app.get("/api/shortlisted")
def shortlisted():
    return _call(shortlisted_candidates, generic="The candidate list could not be loaded. Please refresh and try again.")
