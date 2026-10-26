"""Keep report generation independent of Streamlit page navigation."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import streamlit as st

from database import DATABASE_PATH
from groq_client import generate
from interview_reports import generate_report


@st.cache_resource
def _registry():
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="interview-report"), {}, Lock()


def _key(session_id, database_path):
    return str(Path(database_path).resolve()), session_id


def report_job_status(session_id, database_path=DATABASE_PATH):
    _, jobs, lock = _registry()
    with lock:
        job = jobs.get(_key(session_id, database_path))
    if job is None:
        return {"state": "idle"}
    if not job.done():
        return {"state": "running" if job.running() else "queued"}
    error = job.exception()
    if error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else "Report generation was interrupted. Your saved assessments are safe; please resume."
        return {"state": "failed", "message": message}
    return {"state": "done"}


def start_report_job(session_id, api_key, model_name, database_path=DATABASE_PATH):
    if not api_key.strip():
        raise ValueError("Configure the Groq API key before generating reports.")
    executor, jobs, lock = _registry()
    key = _key(session_id, database_path)

    def work():
        # No Streamlit calls from the worker: navigation must not interrupt it.
        def model(system, documents, schema):
            return generate(
                api_key, model_name, system, documents, schema,
                max_completion_tokens=1536, schema_name="interview_report",
                purpose="interview_scoring", usage_session_id=session_id,
                pace_requests=True, database_path=database_path,
            )
        return generate_report(session_id, model, model_name, database_path)

    with lock:
        previous = jobs.get(key)
        if previous is not None and not previous.done():
            return
        jobs[key] = executor.submit(work)
