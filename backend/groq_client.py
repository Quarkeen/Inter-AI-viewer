import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone

from database import DATABASE_PATH


def _log_usage(result, model_name, call_type, session_id, database_path):
    # Optional accounting must never wait on database locks or fail the workflow.
    try:
        if not isinstance(result, dict):
            return
        usage = result.get("usage")
        usage = usage if isinstance(usage, dict) else {}

        def count(mapping, key):
            value = mapping.get(key)
            return value if type(value) is int and 0 <= value <= 2**63 - 1 else None

        def text(value):
            return value[:256] if isinstance(value, str) else None

        prompt = count(usage, "prompt_tokens")
        completion = count(usage, "completion_tokens")
        total = count(usage, "total_tokens")
        details = usage.get("prompt_tokens_details")
        cached = count(details, "cached_tokens") if isinstance(details, dict) else None
        metadata = result.get("x_groq")
        request_id = text(metadata.get("id")) if isinstance(metadata, dict) else None
        choices = result.get("choices")
        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        with closing(sqlite3.connect(database_path, timeout=0)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """INSERT OR IGNORE INTO groq_token_usage
                   (response_id,request_id,session_id,model_name,call_type,prompt_tokens,
                    completion_tokens,total_tokens,cached_tokens,usage_reported,finish_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (text(result.get("id")), request_id, session_id,
                 text(result.get("model")) or model_name, call_type, prompt, completion, total,
                 cached, int(any(value is not None for value in (prompt, completion, total))),
                 text(first.get("finish_reason"))),
            )
    except Exception as error:
        # No credentials, prompts, candidate answers, or response bodies in logs.
        logging.getLogger(__name__).warning("Groq token accounting skipped (%s).", type(error).__name__)


def _reserve_request(interview_id, database_path, *, paced=False):
    while True:
        delay = 0
        with closing(sqlite3.connect(database_path)) as connection, connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'interview_sessions'").fetchone():
                return
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT session_id FROM interview_sessions WHERE status = 'active' AND slot_end > ?",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
            ).fetchone()
            if interview_id is None:
                if active:
                    raise RuntimeError("Other model requests are paused while an interview is active.")
            elif not active or active[0] != interview_id:
                raise ValueError("Your interview is no longer active.")
            if interview_id is not None or paced:
                previous = connection.execute("SELECT last_started_at FROM model_request_clock WHERE clock_id = 1").fetchone()
                delay = max(0, 60 - (time.time() - previous[0])) if previous else 0
            if delay == 0:
                connection.execute(
                    "INSERT INTO model_request_clock VALUES (1, ?) ON CONFLICT(clock_id) DO UPDATE SET last_started_at = excluded.last_started_at",
                    (time.time(),),
                )
                return
        time.sleep(min(delay, 1))


def generate(api_key, model_name, system_prompt, documents, schema, *,
             max_completion_tokens=3072, schema_name="resume_assessment",
             interview_id=None, database_path=DATABASE_PATH, purpose=None,
             usage_session_id=None, pace_requests=False):
    if not api_key.strip():
        raise ValueError("The interviewer is not configured. Please contact the hiring team." if interview_id else "Configure the Groq API key before evaluating resumes.")
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": documents},
        ],
        "temperature": 0,
        "reasoning_effort": "low",
        "max_completion_tokens": max_completion_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    }
    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "capstone-interviewer/1.0",
        },
    )
    _reserve_request(interview_id, database_path, paced=pace_requests)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        messages = {
            401: "Groq rejected the API key. Check the local secrets file.",
            403: "Groq denied access. Check the model permissions in your Groq account.",
            413: "The interview request exceeded the model's current limit. Your saved answer is safe." if interview_id else "These documents exceed the current Groq request limit. They were not truncated or scored.",
            429: "Groq's free-tier limit was reached. Please wait before trying again.",
        }
        raise RuntimeError(messages.get(error.code, f"Groq could not complete the evaluation (HTTP {error.code}).")) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Could not connect to Groq. Check the internet connection and try again.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("Groq returned an unreadable response. Please try again.") from None

    call_type = purpose or "resume_evaluation"
    if interview_id is not None:
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        call_type = "interview_plan" if isinstance(properties, dict) and "questions" in properties else "interview_turn"
    # Record provider-reported counts even if the completion is later rejected.
    _log_usage(result, model_name, call_type, interview_id or usage_session_id, database_path)
    try:
        choice = result["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("The interviewer returned no question. Please retry." if interview_id else "Groq returned no assessment. Please try again.") from None
    if choice.get("finish_reason") != "stop" or not isinstance(content, str) or not content.strip():
        raise RuntimeError("The interviewer did not finish its question. Your saved progress is safe; please retry." if interview_id else "Groq did not finish the assessment. No score was saved; please try again.")
    return content
